# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

UndoManager — Command-pattern undo/redo for the node graph
============================================================

Macro-based cascade tracking
-----------------------------
Every user action (widget edit, disconnect, delete) may trigger a
cascade of secondary effects: port additions/removals, downstream
evaluations, auto-disable widget value changes.  All commands pushed
during one cascade are collected into a **macro** and wrapped as a
single ``CompoundCommand``, so one Ctrl+Z reverses the entire cascade.

Auto-macros
~~~~~~~~~~~
When ``value_changed`` fires on any widget, the undo manager auto-opens
a macro before pushing the ``WidgetValueCommand``.  All subsequent
commands from the same cascade (downstream widget changes from evaluate,
etc.) go into the same macro.  A debounce mechanism keeps the macro
open until the cascade settles — checked via ``QTimer.singleShot(0)``
with a stability test (did the macro grow since the last check?).

Explicit macros
~~~~~~~~~~~~~~~
Canvas-level bulk operations (shake/backspace disconnect) call
``begin_macro()`` / ``end_macro()`` explicitly.  ``end_macro()``
schedules the same debounce close, covering deferred evaluations.

Port lifecycle
~~~~~~~~~~~~~~
``port_added`` / ``port_removed`` signals do **not** push commands.
Port changes are always side-effects of a primary action whose
command handles undo/redo by re-triggering the node's own handlers.
Port slots only manage widget baselines.

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
    """Command-stack undo/redo for the node canvas with macro support."""

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
        self._restore_activity: int = 0       # bumped by deferred cascades
        self._restore_last_activity: int = 0  # snapshot for stability check
        self._restore_stable_ticks: int = 0   # must be stable for 2 ticks

        # Merge window
        self._merge_window_ms = merge_window_ms
        self._merge_timer = QTimer()
        self._merge_timer.setSingleShot(True)
        self._merge_open: bool = False
        self._merge_timer.timeout.connect(self._close_merge_window)

        # Widget-core wiring
        self._connected_cores: Dict[int, Tuple[Any, Any]] = {}
        self._port_slots: Dict[int, Tuple[Any, Any]] = {}
        self._widget_baselines: Dict[Tuple[str, str], Any] = {}

        # ── Macro state ──────────────────────────────────────────────
        self._in_macro: bool = False
        self._macro_label: str = ""
        self._macro_stack: List[UndoCommand] = []
        self._macro_close_scheduled: bool = False
        self._macro_last_size: int = 0
        self._macro_stable_ticks: int = 0  # must be stable for 2 ticks

        if hasattr(canvas, 'node_added'):
            canvas.node_added.connect(self._on_node_added)
        if hasattr(canvas, 'node_removed'):
            canvas.node_removed.connect(self._on_node_removed)

    # ==================================================================
    # Public API
    # ==================================================================

    def push(self, cmd: UndoCommand) -> None:
        """Record a command.  Goes into the macro if one is open.

        If no macro is open, one is auto-opened so that any deferred
        side-effects (downstream evaluations from ``set_dirty``) are
        bundled into the same undo step.  This covers ALL code paths
        — widget changes, disconnects, deletions — without requiring
        each caller to manually open a macro.
        """
        if self._restoring:
            _dbg(f"push BLOCKED (_restoring): {cmd.description}")
            return

        _dbg(f"push: {cmd.description}  macro={self._in_macro}")

        # Auto-open a macro for ANY command if none is open
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
        self._schedule_macro_close()

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
        self._macro_close_scheduled = False
        self._macro_last_size = 0
        self._macro_stable_ticks = 0
        _dbg(f"begin_macro: '{label}'")

    def end_macro(self) -> None:
        """Signal that the explicit macro body is done.
        Schedules a debounce close to capture deferred evaluations.
        """
        if not self._in_macro:
            return
        _dbg(f"end_macro: scheduling close for '{self._macro_label}'")
        self._schedule_macro_close()

    def _begin_auto_macro(self, label: str) -> None:
        """Open a macro automatically from push()."""
        if self._in_macro:
            return
        self._close_merge_window()
        self._in_macro = True
        self._macro_label = label
        self._macro_stack = []
        self._macro_close_scheduled = False
        self._macro_last_size = 0
        self._macro_stable_ticks = 0
        _dbg(f"auto_macro OPEN: '{label}'")

    def _schedule_macro_close(self) -> None:
        """Record size, reset stability counter, schedule check."""
        self._macro_last_size = len(self._macro_stack)
        self._macro_stable_ticks = 0  # new command arrived → reset
        if not self._macro_close_scheduled:
            self._macro_close_scheduled = True
            QTimer.singleShot(0, self._try_close_macro)

    def _try_close_macro(self) -> None:
        """Close the macro only after 2 consecutive stable ticks.

        Deferred evaluations from ``set_dirty`` also use
        ``QTimer.singleShot(0)``.  A single stable check would race
        with them.  Two ticks guarantees that any evaluate scheduled
        in the same tick as the command has already fired.
        """
        self._macro_close_scheduled = False
        if not self._in_macro:
            return

        current_size = len(self._macro_stack)
        _dbg(f"_try_close_macro: size={current_size}, "
             f"last={self._macro_last_size}, "
             f"stable_ticks={self._macro_stable_ticks}")

        if current_size > self._macro_last_size:
            _dbg("  → grew, resetting stability")
            self._macro_stable_ticks = 0
            self._schedule_macro_close()
            return

        self._macro_stable_ticks += 1
        if self._macro_stable_ticks < 2:
            _dbg("  → stable tick 1/2, rechecking")
            self._macro_last_size = current_size
            self._macro_close_scheduled = True
            QTimer.singleShot(0, self._try_close_macro)
            return

        _dbg("  → stable tick 2/2, finalizing")
        self._finalize_macro()

    def _finalize_macro(self) -> None:
        """Wrap collected commands into CompoundCommand and push."""
        self._in_macro = False
        self._macro_close_scheduled = False
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
        self._restore_activity = 0
        self._restore_stable_ticks = 0
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
        self._restore_activity = 0
        self._restore_stable_ticks = 0
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
        """Schedule a stability check for the restore guard."""
        self._restore_last_activity = self._restore_activity
        QTimer.singleShot(0, self._try_finish_restore)

    def _try_finish_restore(self) -> None:
        """Clear _restoring only after 2 consecutive stable ticks.

        Same pattern as macro close.  Activity is bumped by
        ``_on_value_changed`` and port slots whenever they fire during
        a restore, telling us the cascade is still running.
        """
        if not self._restoring:
            return

        current = self._restore_activity
        _dbg(f"_try_finish_restore: activity={current}, "
             f"last={self._restore_last_activity}, "
             f"stable={self._restore_stable_ticks}")

        if current > self._restore_last_activity:
            _dbg("  → activity detected, resetting stability")
            self._restore_stable_ticks = 0
            self._restore_last_activity = current
            QTimer.singleShot(0, self._try_finish_restore)
            return

        self._restore_stable_ticks += 1
        if self._restore_stable_ticks < 2:
            _dbg("  → stable tick 1/2, rechecking")
            self._restore_last_activity = current
            QTimer.singleShot(0, self._try_finish_restore)
            return

        _dbg("  → stable tick 2/2, finalizing")
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
        self._macro_close_scheduled = False
        self._macro_stable_ticks = 0
        self._restoring = False
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
                self._port_slots[id(node)] = (pa_slot, None)

            if hasattr(node, 'port_removed'):
                pr_slot = self._make_port_removed_slot(node_uuid)
                node.port_removed.connect(pr_slot)
                existing = self._port_slots.get(id(node), (None, None))
                self._port_slots[id(node)] = (existing[0], pr_slot)

            _dbg(f"_wire_node: {node_uuid[:8]} "
                 f"bindings={list(wc.bindings().keys())}")
        except (RuntimeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Slot factories
    # ------------------------------------------------------------------

    def _make_widget_slot(self, node_uuid: str, wc):
        """Return a slot that pushes WidgetValueCommands and auto-opens
        macros to capture cascading side-effects.
        """
        def _on_value_changed(port_name: str = ""):
            if not port_name:
                return
            if self._restoring:
                # Cascade still active — signal to _try_finish_restore
                self._restore_activity += 1
                _dbg(f"value_changed BLOCKED (_restoring): "
                     f"{node_uuid[:8]}:{port_name}  "
                     f"activity={self._restore_activity}")
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
                     f"— no baseline, stored {new_value!r}")
                return

            if old_value == new_value:
                return

            _dbg(f"value_changed: {node_uuid[:8]}:{port_name} "
                 f"{old_value!r} → {new_value!r}")

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
            if self._restoring:
                self._restore_activity += 1
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
            if self._restoring:
                self._restore_activity += 1
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
            pa_slot, pr_slot = port_entry
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
