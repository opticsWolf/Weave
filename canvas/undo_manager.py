# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

UndoManager — Command-pattern undo/redo with compute-fence tracking
FIXED VERSION: 
1. Added missing _merge_open initialization in __init__
2. Added quiescence period after restore before accepting new commands
3. Widget slot checks for pending restore state more aggressively
4. Deferred baseline snapshot to ensure all signals are processed
5. CIRCUIT BREAKER: Detects stuck fence and forces completion
6. Added descriptions for UI and fixed encapsulation leak
7. Initialized _last_macro_size in __init__ and clear() (no more lazy getattr)
8. Circuit breaker now resets the leaked fence on trip, so a single leak
   doesn't cascade into every subsequent undo/redo operation
9. Circuit breaker now also covers fence oscillation (0 → >0 → 0 → ...)
   via a shared _trip_fence_circuit_breaker helper used by both
   _try_finish_restore and _restore_final_check; retry counter now
   accumulates across all reschedules within a single restore
10. Event-driven fence wakeup (Option A: canvas.eval_fence_idle signal,
    Option B: per-node compute_finished signal) replaces 0ms busy-poll
    while waiting for long-running computes. Polling is retained as a
    250ms safety-net fallback. _max_fence_retries bumped to 480 so the
    circuit breaker is now a meaningful 120s wall-clock timeout.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple, Set

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGraphicsItem

from weave.canvas.undo_commands import (
    UndoCommand, WidgetValueCommand, CompoundCommand, 
    AddNodeCommand, RemoveNodesCommand, get_node_uid,
)
from weave.logger import get_logger

log = get_logger("UndoManager")

_DEBUG = os.environ.get("WEAVE_UNDO_DEBUG", "0") == "1"

# Slow-poll fallback interval used when waiting for the canvas eval_fence to
# drop. Event-driven wakeups (Option A: canvas.eval_fence_idle, Option B:
# node.compute_finished) are the primary signal — this poll only exists as
# a safety net for cases where neither signal fires (e.g. fence leak,
# external fence manipulation, canvas without the signal defined).
_FENCE_WAIT_INTERVAL_MS = 250

def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[UndoManager] {msg}", flush=True)
    log.debug(msg)


class UndoManager:
    """Command-stack undo/redo with compute-fence-based macro support.
    
    FIXED: Added circuit breaker for stuck fence detection.
    """

    def __init__(
        self,
        canvas,
        get_registry_map: Callable[[], Dict[str, type]],
        max_steps: int = 100,
        merge_window_ms: int = 600,
    ) -> None:
        self._canvas = canvas
        self._get_registry_map = get_registry_map

        self._undo_stack: deque[UndoCommand] = deque(maxlen=max_steps)
        self._redo_stack: deque[UndoCommand] = deque(maxlen=max_steps)
        self._restoring: bool = False

        # FIXED: Initialize _merge_open (was missing!)
        self._merge_open: bool = False

        self._merge_window_ms = merge_window_ms
        self._merge_timer = QTimer()
        self._merge_timer.setSingleShot(True)
        self._merge_timer.timeout.connect(self._close_merge_window)

        self._connected_cores: Dict[int, Tuple[Any, Any]] = {}
        self._port_slots: Dict[int, Tuple[Any, Any, Any]] = {}
        
        self._active_node_uuids: Set[str] = set()
        self._widget_baselines: Dict[Tuple[str, str], Any] = {}

        self._in_macro: bool = False
        self._macro_label: str = ""
        self._macro_stack: List[UndoCommand] = []
        self._macro_check_scheduled: bool = False
        self._macro_stability_counter: int = 0
        self._last_macro_size: int = 0

        self._restore_check_scheduled: bool = False
        
        self._quiescence_pending: bool = False
        self._pending_widget_signals: int = 0

        # CIRCUIT BREAKER: Track retry counts for stuck fence detection.
        # With _FENCE_WAIT_INTERVAL_MS=250 and event-driven wakeups as the
        # primary mechanism, the breaker is now a wall-clock timeout: 480
        # retries × 250ms = 120s budget. This must outlast the worst-case
        # cancellation latency of any in-flight worker during restore (NOT
        # the worst-case compute time — workers are cancelled by undo via
        # set_dirty before the restore-finish wait begins).
        self._restore_retry_count: int = 0
        self._max_fence_retries: int = 480

        # Event-driven fence wakeup (Options A + B).
        # _fence_wakeup_pending dedupes the case where many nodes finish
        # compute simultaneously — we only schedule one wakeup per pending
        # check, no matter how many signals fire.
        self._fence_wakeup_pending: bool = False
        self._compute_finished_slots: Dict[int, Tuple[Any, Callable]] = {}

        if hasattr(canvas, 'node_added'):
            canvas.node_added.connect(self._on_node_added)
        if hasattr(canvas, 'node_removed'):
            canvas.node_removed.connect(self._on_node_removed)

        # Option A: subscribe to canvas-level fence-idle signal if defined.
        # The signal is emitted by BaseControlNode._decrement_eval_fence
        # whenever the canvas eval_fence transitions to 0. Graceful no-op
        # if the canvas class doesn't declare the signal.
        idle_sig = getattr(canvas, 'eval_fence_idle', None)
        if idle_sig is not None:
            try:
                idle_sig.connect(self._on_fence_idle)
                _dbg("connected to canvas.eval_fence_idle (Option A)")
            except (RuntimeError, TypeError):
                pass

    def _eval_fence(self) -> int:
        """Read the scene-level pending-evaluation counter."""
        return getattr(self._canvas, '_eval_fence', 0)

    # ── Event-driven fence wakeup (Options A + B) ─────────────────────
    #
    # Polling at _FENCE_WAIT_INTERVAL_MS is only a fallback. The common
    # case is woken by these slots: canvas eval_fence_idle (A) or per-node
    # compute_finished (B). Both route through _request_fence_wakeup,
    # which is intentionally minimal (no synchronous fence work) so it
    # adds zero latency to the emitting path.

    def _on_fence_idle(self) -> None:
        """Slot for canvas.eval_fence_idle — fence just hit 0."""
        _dbg("event: canvas.eval_fence_idle fired")
        self._request_fence_wakeup()

    def _on_compute_finished(self) -> None:
        """Slot for node.compute_finished — a worker just released its fence."""
        _dbg("event: node.compute_finished fired")
        self._request_fence_wakeup()

    def _request_fence_wakeup(self) -> None:
        """Queue an immediate fence re-check if one is currently pending.

        Lightweight by design: no fence read, no work — just sets a flag
        and posts a 0ms timer. Multiple simultaneous emissions (e.g. 100
        nodes finishing at once) dedupe to a single scheduled wakeup via
        _fence_wakeup_pending.
        """
        if self._fence_wakeup_pending:
            return
        if not (self._macro_check_scheduled or self._restore_check_scheduled):
            # Nothing waiting on the fence — nothing to wake up.
            return
        self._fence_wakeup_pending = True
        QTimer.singleShot(0, self._do_fence_wakeup)

    def _do_fence_wakeup(self) -> None:
        """Run any pending fence checks now that an event indicated progress.

        The pending 250ms slow-poll timers will still fire later, but the
        _in_macro / _restoring guards at the top of each check method
        make those redundant invocations safe no-ops. The check methods
        also reset their own *_check_scheduled flags at entry, so we do
        not need to clear them here.
        """
        self._fence_wakeup_pending = False
        if self._in_macro and self._macro_check_scheduled:
            self._try_close_macro()
        if self._restoring and self._restore_check_scheduled:
            self._try_finish_restore()

    @property
    def next_undo_description(self) -> str:
        """User-friendly description of the next action to be undone."""
        return self._undo_stack[-1].description if self._undo_stack else ""

    @property
    def next_redo_description(self) -> str:
        """User-friendly description of the next action to be redone."""
        return self._redo_stack[-1].description if self._redo_stack else ""

    def push(self, cmd: UndoCommand) -> None:
        """Record a command. Auto-opens a macro to capture cascades."""
        if self._restoring:
            _dbg(f"push BLOCKED (_restoring): {cmd.description}")
            return
        
        if self._quiescence_pending:
            _dbg(f"push BLOCKED (quiescence): {cmd.description}")
            return

        prev_undo_len = len(self._undo_stack)
        
        _dbg(f"push: {cmd.description}  macro={self._in_macro}  "
             f"fence={self._eval_fence()}")

        if self._merge_open and self._undo_stack:
            top = self._undo_stack[-1]
            if top.try_merge(cmd):
                _dbg(f"  → merged into: {top.description}")
                self._restart_merge_window()
                if isinstance(cmd, WidgetValueCommand):
                    key = (cmd.node_uuid, cmd.port_name)
                    self._widget_baselines[key] = cmd.new_value
                return

        if not self._in_macro:
            self._begin_auto_macro(f"Auto: {cmd.description}")

        self._macro_stack.append(cmd)
        _dbg(f"  → macro stack (depth={len(self._macro_stack)})")
        
        self._macro_stability_counter = 0
        self._schedule_macro_check()

        if (len(self._undo_stack) == self._undo_stack.maxlen and 
            prev_undo_len == self._undo_stack.maxlen and
            len(self._widget_baselines) > len(self._connected_cores) * 2):
            _dbg("  → undo stack full, triggering baseline GC")
            self._gc_widget_baselines()

    def begin_macro(self, label: str = "") -> None:
        """Open an explicit macro. Ignored if one is already open."""
        if self._in_macro:
            _dbg(f"begin_macro IGNORED (already open): '{label}'")
            return
        self._close_merge_window()
        self._in_macro = True
        self._macro_label = label
        self._macro_stack = []
        self._macro_check_scheduled = False
        self._macro_stability_counter = 0
        _dbg(f"begin_macro: '{label}'")

    def end_macro(self) -> None:
        """Signal that the explicit macro body is done."""
        if not self._in_macro:
            return
        _dbg(f"end_macro: scheduling check for '{self._macro_label}'")
        self._schedule_macro_check()

    def _begin_auto_macro(self, label: str) -> None:
        """Open a macro automatically from push()."""
        if self._in_macro:
            return
        self._close_merge_window()
        self._in_macro = True
        self._macro_label = label
        self._macro_stack = []
        self._macro_check_scheduled = False
        self._macro_stability_counter = 0
        _dbg(f"auto_macro OPEN: '{label}'")

    def _schedule_macro_check(self) -> None:
        """Schedule a fence+stability check on the next tick."""
        if not self._macro_check_scheduled:
            self._macro_check_scheduled = True
            QTimer.singleShot(0, self._try_close_macro)

    def _try_close_macro(self) -> None:
        """Close the macro when fence==0 and stable for 2 consecutive ticks."""
        self._macro_check_scheduled = False
        if not self._in_macro:
            return

        fence = self._eval_fence()
        size = len(self._macro_stack)

        _dbg(f"_try_close_macro: fence={fence}, size={size}, "
             f"stability={self._macro_stability_counter}")

        if fence > 0:
            self._macro_stability_counter = 0
            _dbg(f"  → fence > 0, rescheduling in {_FENCE_WAIT_INTERVAL_MS}ms")
            # Slow-poll fallback. Event-driven wakeup via compute_finished /
            # eval_fence_idle will fire the check sooner if it can.
            self._macro_check_scheduled = True
            QTimer.singleShot(_FENCE_WAIT_INTERVAL_MS, self._try_close_macro)
            return

        self._macro_stability_counter += 1
        
        if self._macro_stability_counter < 2:
            _dbg(f"  → fence=0, stability={self._macro_stability_counter}, need 2")
            self._schedule_macro_check()
            return

        if size > self._last_macro_size:
            self._last_macro_size = size
            self._macro_stability_counter = 0
            _dbg("  → macro grew, resetting stability")
            self._schedule_macro_check()
            return

        _dbg("  → stable for 2 ticks, finalizing")
        self._finalize_macro()

    def _finalize_macro(self) -> None:
        """Wrap collected commands into CompoundCommand and push."""
        self._in_macro = False
        self._macro_check_scheduled = False
        self._macro_stability_counter = 0
        self._last_macro_size = 0
        
        children = self._macro_stack
        self._macro_stack = []
        label = self._macro_label
        self._macro_label = ""

        if not children:
            _dbg("_finalize_macro: empty")
            return

        if len(children) == 1:
            cmd = children[0]
            _dbg(f"_finalize_macro: unwrapped → {cmd.description}")
        else:
            cmd = CompoundCommand(children, label)
            _dbg(f"_finalize_macro: Compound({len(children)}) '{label}' "
                 f"→ {[c.description for c in children]}")

        if self._merge_open and self._undo_stack:
            top = self._undo_stack[-1]
            if top.try_merge(cmd):
                _dbg(f"  → merged into: {top.description}")
                self._restart_merge_window()
                return

        self._redo_stack.clear()
        self._undo_stack.append(cmd)
        _dbg(f"  → undo stack (depth={len(self._undo_stack)}/{self._undo_stack.maxlen})")

        if isinstance(cmd, WidgetValueCommand):
            self._open_merge_window()

    def _force_close_macro(self) -> None:
        """Immediately finalize any open macro."""
        if self._in_macro:
            _dbg("_force_close_macro")
            self._finalize_macro()

    def undo(self) -> bool:
        self._close_merge_window()
        self._force_close_macro()

        if not self._undo_stack:
            _dbg("undo: nothing")
            return False

        cmd = self._undo_stack.pop()
        self._restoring = True
        self._restore_check_scheduled = False
        self._quiescence_pending = False
        self._restore_retry_count = 0  # Reset retry counter
        _dbg(f"undo START: {cmd.description}")
        
        self._refresh_baselines_for_command(cmd)
        
        try:
            cmd.undo(self._canvas)
            self._redo_stack.append(cmd)
            _dbg(f"undo OK: {cmd.description}")
            return True
        except Exception as e:
            log.error(f"Undo failed: {e}")
            _dbg(f"undo FAILED: {e}")
            return False
        finally:
            self._schedule_restore_check()

    def redo(self) -> bool:
        self._close_merge_window()
        self._force_close_macro()

        if not self._redo_stack:
            _dbg("redo: nothing")
            return False

        cmd = self._redo_stack.pop()
        self._restoring = True
        self._restore_check_scheduled = False
        self._quiescence_pending = False
        self._restore_retry_count = 0  # Reset retry counter
        _dbg(f"redo START: {cmd.description}")
        
        self._refresh_baselines_for_command(cmd)
        
        try:
            cmd.redo(self._canvas)
            self._undo_stack.append(cmd)
            _dbg(f"redo OK: {cmd.description}")
            return True
        except Exception as e:
            log.error(f"Redo failed: {e}")
            _dbg(f"redo FAILED: {e}")
            return False
        finally:
            self._schedule_restore_check()

    def _schedule_restore_check(self) -> None:
        """Schedule a fence check for restore completion.

        Note: the retry counter is reset by undo()/redo() at restore start
        (once per restore operation). It must NOT be reset here — it must
        accumulate across reschedules so the circuit breaker can detect
        fence oscillation (0 → >0 → 0 → ...), not just a stuck-high fence.
        """
        if not self._restore_check_scheduled:
            self._restore_check_scheduled = True
            QTimer.singleShot(0, self._try_finish_restore)

    def _trip_fence_circuit_breaker(self, fence: int, where: str) -> bool:
        """Increment retry counter; if exceeded, force completion.

        Returns True if the breaker tripped (caller MUST return immediately).
        Returns False if the caller should continue with its normal path.

        When the breaker trips, the leaked fence is reset to 0 so subsequent
        undo/redo operations don't immediately re-trip on the same leak. The
        underlying caller bug is NOT masked: the loud log.error above remains
        the diagnostic, and points at the actual root cause.
        """
        self._restore_retry_count += 1
        if self._restore_retry_count > self._max_fence_retries:
            log.error(
                f"UndoManager: FENCE STUCK at {fence} for "
                f"{self._restore_retry_count} ticks in {where}. "
                f"Forcing completion and resetting fence to break the leak. "
                f"This indicates a fence leak in node add/remove. "
                f"Check that _decrement_eval_fence is called even when "
                f"scene is None."
            )
            try:
                setattr(self._canvas, '_eval_fence', 0)
            except (AttributeError, RuntimeError):
                pass
            self._restore_retry_count = 0
            self._enter_quiescence()
            return True
        return False

    def _try_finish_restore(self) -> None:
        """Clear _restoring when fence==0 + 1 stable tick."""
        self._restore_check_scheduled = False
        if not self._restoring:
            return

        fence = self._eval_fence()
        _dbg(f"_try_finish_restore: fence={fence}, "
             f"retry={self._restore_retry_count}")

        if fence > 0:
            if self._trip_fence_circuit_breaker(fence, "_try_finish_restore"):
                return
            _dbg(f"  → fence > 0, rescheduling in {_FENCE_WAIT_INTERVAL_MS}ms "
                 f"(retry {self._restore_retry_count})")
            # Slow-poll fallback. compute_finished / eval_fence_idle will
            # wake us sooner if the fence drops naturally.
            self._restore_check_scheduled = True
            QTimer.singleShot(_FENCE_WAIT_INTERVAL_MS, self._try_finish_restore)
            return

        # Fence is 0 — one more tick for deferred widget updates.
        # NOTE: do NOT reset _restore_retry_count here — the breaker must
        # accumulate ticks across the full restore so fence oscillation
        # (0 → >0 → 0 → ...) can also be detected.
        _dbg("  → fence=0, one more check")
        self._restore_check_scheduled = True
        QTimer.singleShot(0, self._restore_final_check)

    def _restore_final_check(self) -> None:
        """Second check: if fence still 0, clear restore."""
        self._restore_check_scheduled = False
        if not self._restoring:
            return

        fence = self._eval_fence()
        _dbg(f"_restore_final_check: fence={fence}, "
             f"retry={self._restore_retry_count}")

        if fence > 0:
            if self._trip_fence_circuit_breaker(fence, "_restore_final_check"):
                return
            _dbg(f"  → fence > 0 again, rescheduling in {_FENCE_WAIT_INTERVAL_MS}ms")
            # Oscillation case: fence dropped, we scheduled this final
            # check, but fence came back up. Slow-poll until it settles.
            # Event-driven wakeup will pre-empt this if compute completes.
            self._restore_check_scheduled = True
            QTimer.singleShot(_FENCE_WAIT_INTERVAL_MS, self._try_finish_restore)
            return

        self._enter_quiescence()

    def _enter_quiescence(self) -> None:
        """Enter a quiescence period after restore to let signals settle."""
        self._quiescence_pending = True
        _dbg("_enter_quiescence: entering quiescence period")
        QTimer.singleShot(0, self._exit_quiescence)

    def _exit_quiescence(self) -> None:
        """Exit quiescence and finalize the restore operation.

        FIXED: Re-wire any nodes that were restored while ``_restoring``
        was True.  ``_on_node_added`` skips wiring during restore to
        avoid capturing transient signal emissions as undo commands.
        Once the restore is complete we must reconnect the WidgetCore
        ``value_changed`` slots so future edits are tracked again.
        """
        self._quiescence_pending = False
        self._restoring = False
        self._restore_retry_count = 0  # Reset for next time

        # Re-wire nodes that appeared during the restore phase.
        # _on_node_added() bailed out for these because _restoring was
        # True, so their WidgetCore.value_changed is not connected yet.
        self.wire_existing_nodes()

        self.snapshot_widget_baselines()
        _dbg("_exit_quiescence: _restoring=False, nodes re-wired, "
             "baselines refreshed, quiescence ended")

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def clear(self) -> None:
        self._close_merge_window()
        self._in_macro = False
        self._macro_stack.clear()
        self._macro_check_scheduled = False
        self._macro_stability_counter = 0
        self._last_macro_size = 0
        self._restoring = False
        self._restore_check_scheduled = False
        self._quiescence_pending = False
        self._restore_retry_count = 0  # Reset circuit breaker
        self._fence_wakeup_pending = False  # Reset event-driven wakeup state
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._disconnect_all_cores()
        self._widget_baselines.clear()
        self._active_node_uuids.clear()

    @property
    def registry_map(self) -> Dict[str, type]:
        return self._get_registry_map()

    @staticmethod
    def of(canvas) -> Optional["UndoManager"]:
        provider = getattr(canvas, "_context_menu_provider", None)
        return getattr(provider, "_undo_manager", None) if provider else None

    def _gc_widget_baselines(self) -> None:
        """Remove baselines for nodes no longer connected."""
        self._active_node_uuids.clear()
        for wc, _slot in self._connected_cores.values():
            node_ref = getattr(wc, '_node_ref', None)
            if node_ref is not None:
                try:
                    uid = get_node_uid(node_ref)
                    if uid:
                        self._active_node_uuids.add(uid)
                except (RuntimeError, AttributeError):
                    pass
        
        dead_keys = [
            key for key in self._widget_baselines 
            if key[0] not in self._active_node_uuids
        ]
        
        for key in dead_keys:
            del self._widget_baselines[key]
            _dbg(f"GC baseline: {key[0][:8]}:{key[1]}")
        
        if dead_keys:
            _dbg(f"GC complete: removed {len(dead_keys)} orphaned baselines, "
                 f"remaining {len(self._widget_baselines)}")

    def _refresh_baselines_for_command(self, cmd: UndoCommand) -> None:
        """Pre-emptively update baselines affected by a command."""
        if isinstance(cmd, WidgetValueCommand):
            key = (cmd.node_uuid, cmd.port_name)
            self._widget_baselines[key] = cmd.old_value
            
        elif isinstance(cmd, CompoundCommand):
            for child in cmd.children:
                self._refresh_baselines_for_command(child)
                
        elif isinstance(cmd, (AddNodeCommand, RemoveNodesCommand)):
            self._gc_widget_baselines()

    def _open_merge_window(self) -> None:
        self._merge_open = True
        self._merge_timer.start(self._merge_window_ms)

    def _restart_merge_window(self) -> None:
        if self._merge_open:
            self._merge_timer.start(self._merge_window_ms)

    def _close_merge_window(self) -> None:
        self._merge_timer.stop()
        self._merge_open = False

    def wire_existing_nodes(self) -> None:
        for item in self._canvas.items():
            if not (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
                continue
            if id(item) in self._connected_cores:
                continue
            self._wire_node(item)

    def wire_node(self, node) -> None:
        if id(node) in self._connected_cores:
            return
        self._wire_node(node)

    def _wire_node(self, node) -> None:
        wc = getattr(node, '_widget_core', None) or getattr(node, '_weave_core', None)
        if wc is None or not hasattr(wc, 'value_changed'):
            return
        try:
            node_uuid = get_node_uid(node)
            if not node_uuid:
                return
            
            self._active_node_uuids.add(node_uuid)
            
            for port_name in wc.bindings():
                try:
                    val = wc.get_port_value(port_name)
                    self._widget_baselines[(node_uuid, port_name)] = val
                except Exception:
                    pass

            slot = self._make_widget_slot(node_uuid, wc)
            wc.value_changed.connect(slot)
            self._connected_cores[id(node)] = (wc, slot)

            # Option B: subscribe to compute_finished if the node defines it.
            # ThreadedNode emits this in _cleanup_after_worker, AFTER the
            # fence has been released, making it a reliable "fence may have
            # just dropped" hint. The slot is intentionally minimal — it
            # only flags a wakeup, never does fence work synchronously, so
            # it cannot add latency to compute completion.
            if hasattr(node, 'compute_finished'):
                try:
                    node.compute_finished.connect(self._on_compute_finished)
                    self._compute_finished_slots[id(node)] = (
                        node, self._on_compute_finished
                    )
                    _dbg(f"  → connected compute_finished (Option B)")
                except (RuntimeError, TypeError):
                    pass

            if hasattr(node, 'port_added'):
                pa_slot = self._make_port_added_slot(node_uuid, wc)
                node.port_added.connect(pa_slot)
                self._port_slots[id(node)] = (node, pa_slot, None)

            if hasattr(node, 'port_removed'):
                pr_slot = self._make_port_removed_slot(node_uuid)
                node.port_removed.connect(pr_slot)
                existing = self._port_slots.get(id(node), (node, None, None))
                self._port_slots[id(node)] = (node, existing[1], pr_slot)

            _dbg(f"_wire_node: {node_uuid[:8]} "
                 f"bindings={list(wc.bindings().keys())}")
        except (RuntimeError, TypeError):
            pass

    def _make_widget_slot(self, node_uuid: str, wc):
        """Push WidgetValueCommands into the current macro."""
        def _on_value_changed(port_name: str = ""):
            if not port_name:
                return
            
            if self._restoring or self._quiescence_pending:
                _dbg(f"value_changed during restore/quiescence: {node_uuid[:8]}:{port_name} "
                     f"— baseline updated, no command")
                try:
                    new_value = wc.get_port_value(port_name)
                    key = (node_uuid, port_name)
                    self._widget_baselines[key] = new_value
                except Exception:
                    pass
                return

            key = (node_uuid, port_name)
            try:
                new_value = wc.get_port_value(port_name)
            except Exception:
                return

            old_value = self._widget_baselines.get(key)
            
            if old_value is None:
                self._widget_baselines[key] = new_value
                _dbg(f"value_changed: {node_uuid[:8]}:{port_name} "
                     f"— no baseline, stored")
                return

            if old_value == new_value:
                return

            self._widget_baselines[key] = new_value

            _dbg(f"value_changed: {node_uuid[:8]}:{port_name} "
                 f"{repr(old_value)[:40]} → {repr(new_value)[:40]}")

            cmd = WidgetValueCommand(node_uuid, port_name,
                                     old_value, new_value)
            self.push(cmd)

        return _on_value_changed

    def _make_port_added_slot(self, node_uuid: str, wc):
        def _on_port_added(port):
            port_name = getattr(port, 'name', '')
            if not port_name or wc is None:
                return
            try:
                if hasattr(wc, 'has_binding') and wc.has_binding(port_name):
                    val = wc.get_port_value(port_name)
                    self._widget_baselines[(node_uuid, port_name)] = val
                    _dbg(f"port_added baseline: "
                         f"{node_uuid[:8]}:{port_name} = {val!r}")
            except Exception:
                pass

        return _on_port_added

    def _make_port_removed_slot(self, node_uuid: str):
        def _on_port_removed(port):
            port_name = getattr(port, 'name', '')
            if not port_name:
                return
            removed = self._widget_baselines.pop(
                (node_uuid, port_name), None)
            if removed is not None:
                _dbg(f"port_removed baseline: "
                     f"{node_uuid[:8]}:{port_name} cleared")

        return _on_port_removed

    def _disconnect_all_cores(self) -> None:
        for wc, slot in self._connected_cores.values():
            try:
                wc.value_changed.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connected_cores.clear()

        # Option B: disconnect compute_finished slots
        for node_ref, cf_slot in self._compute_finished_slots.values():
            if node_ref is None:
                continue
            try:
                node_ref.compute_finished.disconnect(cf_slot)
            except (RuntimeError, TypeError):
                pass
        self._compute_finished_slots.clear()

        for node_ref, pa_slot, pr_slot in self._port_slots.values():
            if node_ref is None:
                continue
            if pa_slot is not None and hasattr(node_ref, 'port_added'):
                try:
                    node_ref.port_added.disconnect(pa_slot)
                except (RuntimeError, TypeError):
                    pass
            if pr_slot is not None and hasattr(node_ref, 'port_removed'):
                try:
                    node_ref.port_removed.disconnect(pr_slot)
                except (RuntimeError, TypeError):
                    pass
        self._port_slots.clear()

    def _on_node_added(self, node) -> None:
        if self._restoring:
            return
        self._wire_node(node)

    def _on_node_removed(self, node) -> None:
        entry = self._connected_cores.pop(id(node), None)
        if entry is not None:
            wc, slot = entry
            try:
                wc.value_changed.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
            
            try:
                node_uuid = get_node_uid(node)
                dead_keys = [k for k in self._widget_baselines if k[0] == node_uuid]
                for key in dead_keys:
                    del self._widget_baselines[key]
                    _dbg(f"node_removed: cleared baseline {key[0][:8]}:{key[1]}")
                self._active_node_uuids.discard(node_uuid)
            except (RuntimeError, AttributeError):
                pass

        # Option B: disconnect compute_finished
        cf_entry = self._compute_finished_slots.pop(id(node), None)
        if cf_entry is not None:
            _node_ref, cf_slot = cf_entry
            try:
                node.compute_finished.disconnect(cf_slot)
            except (RuntimeError, TypeError):
                pass
                
        port_entry = self._port_slots.pop(id(node), None)
        if port_entry is not None:
            _node_ref, pa_slot, pr_slot = port_entry
            if pa_slot is not None and hasattr(node, 'port_added'):
                try:
                    node.port_added.disconnect(pa_slot)
                except (RuntimeError, TypeError):
                    pass
            if pr_slot is not None and hasattr(node, 'port_removed'):
                try:
                    node.port_removed.disconnect(pr_slot)
                except (RuntimeError, TypeError):
                    pass

    def snapshot_widget_baselines(self) -> None:
        """Full refresh of all widget baselines."""
        self._widget_baselines.clear()
        self._active_node_uuids.clear()
        
        for wc, _slot in self._connected_cores.values():
            node_ref = getattr(wc, '_node_ref', None)
            if node_ref is None:
                continue
            try:
                uid = get_node_uid(node_ref)
                if not uid:
                    continue
                self._active_node_uuids.add(uid)
                for port_name in wc.bindings():
                    try:
                        val = wc.get_port_value(port_name)
                        self._widget_baselines[(uid, port_name)] = val
                    except Exception:
                        pass
            except (RuntimeError, AttributeError):
                pass
                
        _dbg(f"snapshot_baselines: {len(self._widget_baselines)} entries "
             f"for {len(self._active_node_uuids)} nodes")