# -*- coding: utf-8 -*-
"""
UndoManager — Command-pattern undo/redo with compute-fence tracking
FIXED VERSION: 
1. Added missing _merge_open initialization in __init__
2. Added quiescence period after restore before accepting new commands
3. Widget slot checks for pending restore state more aggressively
4. Deferred baseline snapshot to ensure all signals are processed
5. CIRCUIT BREAKER: Detects stuck fence and forces completion
6. Added descriptions for UI and fixed encapsulation leak
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

        self._restore_check_scheduled: bool = False
        
        self._quiescence_pending: bool = False
        self._pending_widget_signals: int = 0

        # CIRCUIT BREAKER: Track retry counts for stuck fence detection
        self._restore_retry_count: int = 0
        self._max_fence_retries: int = 100  # Max retries before forcing completion

        if hasattr(canvas, 'node_added'):
            canvas.node_added.connect(self._on_node_added)
        if hasattr(canvas, 'node_removed'):
            canvas.node_removed.connect(self._on_node_removed)

    def _eval_fence(self) -> int:
        """Read the scene-level pending-evaluation counter."""
        return getattr(self._canvas, '_eval_fence', 0)

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
            _dbg("  → fence > 0, rescheduling")
            self._schedule_macro_check()
            return

        self._macro_stability_counter += 1
        
        if self._macro_stability_counter < 2:
            _dbg(f"  → fence=0, stability={self._macro_stability_counter}, need 2")
            self._schedule_macro_check()
            return

        if size > getattr(self, '_last_macro_size', 0):
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
        """Schedule a fence check for restore completion."""
        if not self._restore_check_scheduled:
            self._restore_check_scheduled = True
            self._restore_retry_count = 0  # Reset counter on new schedule
            QTimer.singleShot(0, self._try_finish_restore)

    def _try_finish_restore(self) -> None:
        """Clear _restoring when fence==0 + 1 stable tick."""
        self._restore_check_scheduled = False
        if not self._restoring:
            return

        fence = self._eval_fence()
        
        # CIRCUIT BREAKER: Increment retry counter
        self._restore_retry_count += 1
        
        _dbg(f"_try_finish_restore: fence={fence}, retry={self._restore_retry_count}")

        if fence > 0:
            # CIRCUIT BREAKER: Check if we've exceeded max retries
            if self._restore_retry_count > self._max_fence_retries:
                log.error(
                    f"UndoManager: FENCE STUCK at {fence} for {self._restore_retry_count} ticks. "
                    f"Forcing completion. This indicates a fence leak in node add/remove. "
                    f"Check that _decrement_eval_fence is called even when scene is None."
                )
                self._restore_retry_count = 0
                self._enter_quiescence()
                return
            
            _dbg(f"  → fence > 0, rescheduling (retry {self._restore_retry_count})")
            self._restore_check_scheduled = True
            QTimer.singleShot(0, self._try_finish_restore)
            return

        # Fence is 0 — one more tick for deferred widget updates
        _dbg("  → fence=0, one more check")
        self._restore_retry_count = 0
        self._restore_check_scheduled = True
        QTimer.singleShot(0, self._restore_final_check)

    def _restore_final_check(self) -> None:
        """Second check: if fence still 0, clear restore."""
        self._restore_check_scheduled = False
        if not self._restoring:
            return

        fence = self._eval_fence()
        _dbg(f"_restore_final_check: fence={fence}")

        if fence > 0:
            _dbg("  → fence > 0 again, restarting")
            self._schedule_restore_check()
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
        self._restoring = False
        self._restore_check_scheduled = False
        self._quiescence_pending = False
        self._restore_retry_count = 0  # Reset circuit breaker
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