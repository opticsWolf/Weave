# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

UndoManager — Command-pattern undo/redo with compute-fence tracking
====================================================================

Compute fence
-------------
``BaseControlNode.set_dirty`` increments ``scene._eval_fence`` when
scheduling a deferred ``evaluate``, and ``_fenced_evaluate`` decrements
it when the evaluate callback completes.  This gives the undo manager
an exact count of in-flight evaluations — no tick-counting, no timers.

Macro cascade tracking
----------------------
Every ``push()`` auto-opens a macro.  The macro stays open while
``scene._eval_fence > 0`` (evaluations pending) OR while new commands
keep arriving.  Once the fence drains and the macro is stable for 1
additional tick (to catch widget updates that fire from within the
last evaluate), the macro is finalized into a ``CompoundCommand``.

Restore guard
-------------
During ``undo()`` / ``redo()``, ``_restoring`` stays True until the
fence drains and no more deferred activity has occurred.

Debug logging
~~~~~~~~~~~~~
Set env var ``WEAVE_UNDO_DEBUG=1`` for verbose tracing.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGraphicsItem

from weave.canvas.undo_commands import (
    UndoCommand, WidgetValueCommand, CompoundCommand, get_node_uid,
)
from weave.logger import get_logger
log = get_logger("UndoManager")

_DEBUG = os.environ.get("WEAVE_UNDO_DEBUG", "0") == "1"

def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[UndoManager] {msg}", flush=True)
    log.debug(msg)


class UndoManager:
    """Command-stack undo/redo with compute-fence-based macro support."""

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
        self._redo_stack: List[UndoCommand] = []
        self._restoring: bool = False

        # Merge window
        self._merge_window_ms = merge_window_ms
        self._merge_timer = QTimer()
        self._merge_timer.setSingleShot(True)
        self._merge_open: bool = False
        self._merge_timer.timeout.connect(self._close_merge_window)

        # Widget-core wiring
        self._connected_cores: Dict[int, Tuple[Any, Any]] = {}
        self._port_slots: Dict[int, Tuple[Any, Any, Any]] = {}
        self._widget_baselines: Dict[Tuple[str, str], Any] = {}

        # ── Macro state ──────────────────────────────────────────────
        self._in_macro: bool = False
        self._macro_label: str = ""
        self._macro_stack: List[UndoCommand] = []
        self._macro_check_scheduled: bool = False

        # ── Restore state ────────────────────────────────────────────
        self._restore_check_scheduled: bool = False

        if hasattr(canvas, 'node_added'):
            canvas.node_added.connect(self._on_node_added)
        if hasattr(canvas, 'node_removed'):
            canvas.node_removed.connect(self._on_node_removed)

    # ==================================================================
    # Fence accessor
    # ==================================================================

    def _eval_fence(self) -> int:
        """Read the scene-level pending-evaluation counter."""
        return getattr(self._canvas, '_eval_fence', 0)

    # ==================================================================
    # Public API
    # ==================================================================

    def push(self, cmd: UndoCommand) -> None:
        """Record a command.  Auto-opens a macro to capture cascades."""
        if self._restoring:
            _dbg(f"push BLOCKED (_restoring): {cmd.description}")
            return

        _dbg(f"push: {cmd.description}  macro={self._in_macro}  "
             f"fence={self._eval_fence()}")

        # Auto-open a macro for ANY command
        if not self._in_macro:
            # Try merge first (rapid widget edits)
            if self._merge_open and self._undo_stack:
                top = self._undo_stack[-1]
                if top.try_merge(cmd):
                    _dbg(f"  → merged into: {top.description}")
                    self._restart_merge_window()
                    return
            self._begin_auto_macro(f"Auto: {cmd.description}")

        self._macro_stack.append(cmd)
        _dbg(f"  → macro stack (depth={len(self._macro_stack)})")
        self._schedule_macro_check()

    # ==================================================================
    # Macro API
    # ==================================================================

    def begin_macro(self, label: str = "") -> None:
        """Open an explicit macro.  Ignored if one is already open."""
        if self._in_macro:
            _dbg(f"begin_macro IGNORED (already open): '{label}'")
            return
        self._close_merge_window()
        self._in_macro = True
        self._macro_label = label
        self._macro_stack = []
        self._macro_check_scheduled = False
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
        _dbg(f"auto_macro OPEN: '{label}'")

    def _schedule_macro_check(self) -> None:
        """Schedule a fence+stability check on the next tick."""
        if not self._macro_check_scheduled:
            self._macro_check_scheduled = True
            QTimer.singleShot(0, self._try_close_macro)

    def _try_close_macro(self) -> None:
        """Close the macro when fence==0 and stable for 1 extra tick."""
        self._macro_check_scheduled = False
        if not self._in_macro:
            return

        fence = self._eval_fence()
        size = len(self._macro_stack)

        _dbg(f"_try_close_macro: fence={fence}, size={size}")

        if fence > 0:
            _dbg("  → fence > 0, rescheduling")
            self._macro_check_scheduled = True
            QTimer.singleShot(0, self._try_close_macro)
            return

        # Fence is 0 — do one more tick to catch widget updates
        # that fire from within the last evaluate.
        _dbg("  → fence=0, one more stability check")
        self._macro_check_scheduled = True
        QTimer.singleShot(0, lambda: self._macro_final_check(size))

    def _macro_final_check(self, prev_size: int) -> None:
        """Second check: if macro didn't grow and fence still 0, close."""
        self._macro_check_scheduled = False
        if not self._in_macro:
            return

        fence = self._eval_fence()
        size = len(self._macro_stack)

        _dbg(f"_macro_final_check: fence={fence}, size={size}, "
             f"prev={prev_size}")

        if fence > 0 or size > prev_size:
            _dbg("  → grew or fence>0, restarting")
            self._schedule_macro_check()
            return

        _dbg("  → stable, finalizing")
        self._finalize_macro()

    def _finalize_macro(self) -> None:
        """Wrap collected commands into CompoundCommand and push."""
        self._in_macro = False
        self._macro_check_scheduled = False
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

        # Merge with top (rapid widget edits across macros)
        if self._merge_open and self._undo_stack:
            top = self._undo_stack[-1]
            if top.try_merge(cmd):
                _dbg(f"  → merged into: {top.description}")
                self._restart_merge_window()
                return

        self._redo_stack.clear()
        self._undo_stack.append(cmd)
        _dbg(f"  → undo stack (depth={len(self._undo_stack)})")

        if isinstance(cmd, WidgetValueCommand):
            self._open_merge_window()

    def _force_close_macro(self) -> None:
        """Immediately finalize any open macro."""
        if self._in_macro:
            _dbg("_force_close_macro")
            self._finalize_macro()

    # ==================================================================
    # Undo / Redo
    # ==================================================================

    def undo(self) -> bool:
        self._close_merge_window()
        self._force_close_macro()

        if not self._undo_stack:
            _dbg("undo: nothing")
            return False

        cmd = self._undo_stack.pop()
        self._restoring = True
        self._restore_check_scheduled = False
        _dbg(f"undo START: {cmd.description}")
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
        _dbg(f"redo START: {cmd.description}")
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
            QTimer.singleShot(0, self._try_finish_restore)

    def _try_finish_restore(self) -> None:
        """Clear _restoring when fence==0 + 1 stable tick."""
        self._restore_check_scheduled = False
        if not self._restoring:
            return

        fence = self._eval_fence()
        _dbg(f"_try_finish_restore: fence={fence}")

        if fence > 0:
            _dbg("  → fence > 0, rescheduling")
            self._restore_check_scheduled = True
            QTimer.singleShot(0, self._try_finish_restore)
            return

        # Fence is 0 — one more tick for deferred widget updates
        _dbg("  → fence=0, one more check")
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

        self._restoring = False
        self.snapshot_widget_baselines()
        _dbg("_finish_restore: _restoring=False, baselines refreshed")

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
        self._restoring = False
        self._restore_check_scheduled = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._disconnect_all_cores()
        self._widget_baselines.clear()

    @property
    def registry_map(self) -> Dict[str, type]:
        return self._get_registry_map()

    @staticmethod
    def of(canvas) -> Optional["UndoManager"]:
        provider = getattr(canvas, "_context_menu_provider", None)
        return getattr(provider, "_undo_manager", None) if provider else None

    # ==================================================================
    # Merge window
    # ==================================================================

    def _open_merge_window(self) -> None:
        self._merge_open = True
        self._merge_timer.start(self._merge_window_ms)

    def _restart_merge_window(self) -> None:
        if self._merge_open:
            self._merge_timer.start(self._merge_window_ms)

    def _close_merge_window(self) -> None:
        self._merge_timer.stop()
        self._merge_open = False

    # ==================================================================
    # Widget-core wiring
    # ==================================================================

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

    # ------------------------------------------------------------------
    # Slot factories
    # ------------------------------------------------------------------

    def _make_widget_slot(self, node_uuid: str, wc):
        """Push WidgetValueCommands into the current macro."""
        def _on_value_changed(port_name: str = ""):
            if not port_name:
                return
            if self._restoring:
                _dbg(f"value_changed BLOCKED (_restoring): "
                     f"{node_uuid[:8]}:{port_name}")
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

            _dbg(f"value_changed: {node_uuid[:8]}:{port_name} "
                 f"{repr(old_value)[:40]} → {repr(new_value)[:40]}")

            cmd = WidgetValueCommand(node_uuid, port_name,
                                     old_value, new_value)
            self.push(cmd)
            self._widget_baselines[key] = new_value

        return _on_value_changed

    def _make_port_added_slot(self, node_uuid: str, wc):
        """Capture widget baselines for new dynamic ports. No command."""
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
        """Clean up baselines for removed ports. No command."""
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

    # ------------------------------------------------------------------
    # Core/node lifecycle
    # ------------------------------------------------------------------

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

    # ==================================================================
    # Baseline management
    # ==================================================================

    def snapshot_widget_baselines(self) -> None:
        self._widget_baselines.clear()
        for wc, _slot in self._connected_cores.values():
            node_ref = getattr(wc, '_node_ref', None)
            if node_ref is None:
                continue
            uid = get_node_uid(node_ref)
            for port_name in wc.bindings():
                try:
                    self._widget_baselines[(uid, port_name)] = \
                        wc.get_port_value(port_name)
                except Exception:
                    pass
