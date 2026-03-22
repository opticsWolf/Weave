# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

UndoManager — Command-pattern undo/redo for the node graph
============================================================

Replaces the snapshot-based approach with granular commands.  Each
user action pushes a lightweight ``UndoCommand`` that stores only
the delta.  Undo/redo applies commands forward or backward without
tearing down the entire canvas.

Widget-value coalescing
-----------------------
Rapid widget edits (typing, scrolling a spinbox) are coalesced via
the ``try_merge()`` protocol on ``WidgetValueCommand``.  A debounce
timer controls the merge window: edits within the window merge into
the previous command; edits after the window starts a new command.

The manager auto-wires ``WidgetCore.value_changed`` on every node
(via the ``node_added`` / ``node_removed`` signals) with per-node
closures that capture the node UUID and widget core reference.
This means callers only need to push commands for discrete actions
(move, add, delete, connect).  Widget edits are handled
automatically.

Usage
-----
::

    # Construction (inside CanvasCommandsMixin._init_commands):
    from weave.canvas.undo_manager import UndoManager
    self._undo_manager = UndoManager(canvas, self._get_registry_map)

    # After moving nodes:
    from weave.canvas.undo_commands import MoveNodesCommand
    cmd = MoveNodesCommand(moves)      # {uuid: (old_pos, new_pos)}
    self._undo_manager.push(cmd)

    # Keyboard shortcuts:
    self._undo_manager.undo()
    self._undo_manager.redo()
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGraphicsItem

from weave.canvas.undo_commands import (
    UndoCommand, WidgetValueCommand, AddPortCommand, RemovePortCommand,
    CompoundCommand, get_node_uid,
)
from weave.logger import get_logger
log = get_logger("UndoManager")


class UndoManager:
    """
    Command-stack undo/redo for the node canvas.

    Parameters
    ----------
    canvas
        The ``Canvas`` (QGraphicsScene subclass) that owns the graph.
    get_registry_map : callable
        Zero-arg callable returning ``{class_name: cls}`` dict used by
        add/remove commands to instantiate nodes.  Lazy to avoid import
        ordering issues.
    max_steps : int
        Maximum undo commands retained.
    merge_window_ms : int
        Time window during which consecutive widget-value edits to the
        same port are merged into a single command.
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
        self._redo_stack: List[UndoCommand] = []
        self._restoring: bool = False

        # Merge window for widget edits
        self._merge_window_ms = merge_window_ms
        self._merge_timer = QTimer()
        self._merge_timer.setSingleShot(True)
        self._merge_open: bool = False
        self._merge_timer.timeout.connect(self._close_merge_window)

        # Widget-core auto-wiring.
        # Stores {id(node): (widget_core, slot_closure)} so we can
        # disconnect cleanly on node removal.
        self._connected_cores: Dict[int, Tuple[Any, Any]] = {}

        # Port lifecycle slots.
        # Stores {id(node): (port_added_slot, port_removed_slot)}.
        self._port_slots: Dict[int, Tuple[Any, Any]] = {}

        # Baseline widget values captured at the start of each edit
        # session.  Keyed by (node_uuid, port_name).  Used to produce
        # the old_value for WidgetValueCommand.
        self._widget_baselines: Dict[Tuple[str, str], Any] = {}

        if hasattr(canvas, 'node_added'):
            canvas.node_added.connect(self._on_node_added)
        if hasattr(canvas, 'node_removed'):
            canvas.node_removed.connect(self._on_node_removed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, cmd: UndoCommand) -> None:
        """Record a command that has already been executed.

        If the merge window is open and the new command can merge
        into the previous one (e.g. consecutive widget edits), the
        previous command absorbs it and no new entry is added.

        Otherwise the redo stack is cleared (standard linear undo)
        and the command is appended.

        ``cmd.redo()`` is **not** called — the caller has already
        performed the action.

        Side-effect port command discarding
        ------------------------------------
        When a widget change drives port creation/removal (e.g. a count
        spinbox), the signal sequence is:

            port_added / port_removed  →  AddPortCommand pushed first
            value_changed              →  WidgetValueCommand pushed second

        The port commands are *side effects* of the widget value change,
        not independent operations.  The ``WidgetValueCommand`` alone is
        sufficient: its undo/redo restores the widget value via
        ``apply_port_value``, which lets the native widget signal fire.
        The node's own handler (e.g. ``_on_count_changed`` →
        ``_set_count``) then manages both ports and widgets correctly.

        When a ``WidgetValueCommand`` arrives, any ``AddPortCommand`` /
        ``RemovePortCommand`` for the same node sitting at the top of
        the stack are therefore **discarded**.
        """
        if self._restoring:
            return

        # Attempt merge with top of undo stack
        if self._merge_open and self._undo_stack:
            top = self._undo_stack[-1]
            if top.try_merge(cmd):
                log.debug(f"Merged into: {top.description}")
                self._restart_merge_window()
                return

        # -- Discard side-effect port commands --
        # When a WidgetValueCommand arrives after AddPort/RemovePort
        # commands for the same node, the port commands are side-effects
        # of the widget change.  Discard them — the WidgetValueCommand's
        # undo/redo will re-trigger the node's own port management via
        # apply_port_value (which lets native widget signals fire).
        if isinstance(cmd, WidgetValueCommand) and self._undo_stack:
            discarded = 0
            while self._undo_stack:
                top = self._undo_stack[-1]
                if (
                    isinstance(top, (AddPortCommand, RemovePortCommand))
                    and top._node_uuid == cmd.node_uuid
                ):
                    self._undo_stack.pop()
                    discarded += 1
                else:
                    break
            if discarded:
                log.debug(
                    f"Discarded {discarded} port cmd(s) — side-effect of "
                    f"widget change on '{cmd.port_name}'"
                )

        # Standard push — discard forward history
        self._redo_stack.clear()
        self._undo_stack.append(cmd)
        log.debug(f"Pushed: {cmd.description}  (depth={len(self._undo_stack)})")

        # Open a merge window for mergeable commands
        if isinstance(cmd, WidgetValueCommand):
            self._open_merge_window()

    def undo(self) -> bool:
        """Reverse the most recent command.  Returns True on success."""
        self._close_merge_window()

        if not self._undo_stack:
            log.debug("Nothing to undo.")
            return False

        cmd = self._undo_stack.pop()
        self._restoring = True
        try:
            cmd.undo(self._canvas)
            self._redo_stack.append(cmd)
            log.debug(f"Undo: {cmd.description}")
            # Refresh baselines so the next widget edit has a correct
            # old_value reflecting the post-undo state.
            if isinstance(cmd, WidgetValueCommand):
                # Surgical update: only the one port changed.
                key = (cmd.node_uuid, cmd.port_name)
                self._widget_baselines[key] = cmd.old_value
            else:
                # Non-widget commands (move, add, delete, connect) may
                # indirectly change widget values (e.g. node recreated by
                # undo-of-delete gets fresh widget state).  Snapshot all
                # connected cores so the next user edit has the correct
                # old_value and doesn't produce a stale-baseline command.
                self.snapshot_widget_baselines()
            return True
        except Exception as e:
            log.error(f"Undo failed: {e}")
            return False
        finally:
            self._restoring = False

    def redo(self) -> bool:
        """Re-apply the most recently undone command.  Returns True on success."""
        self._close_merge_window()

        if not self._redo_stack:
            log.debug("Nothing to redo.")
            return False

        cmd = self._redo_stack.pop()
        self._restoring = True
        try:
            cmd.redo(self._canvas)
            self._undo_stack.append(cmd)
            log.debug(f"Redo: {cmd.description}")
            # Refresh baselines so the next widget edit has a correct
            # old_value reflecting the post-redo state.
            if isinstance(cmd, WidgetValueCommand):
                # Surgical update: only the one port changed.
                key = (cmd.node_uuid, cmd.port_name)
                self._widget_baselines[key] = cmd.new_value
            else:
                # Non-widget commands: resnapshot all baselines in case
                # redo recreated nodes or otherwise altered widget state.
                self.snapshot_widget_baselines()
            return True
        except Exception as e:
            log.error(f"Redo failed: {e}")
            return False
        finally:
            self._restoring = False

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def clear(self) -> None:
        """Discard all history (e.g. after loading a new file)."""
        self._close_merge_window()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._disconnect_all_cores()
        self._widget_baselines.clear()

    @property
    def registry_map(self) -> Dict[str, type]:
        """Convenience accessor for commands that need the node registry."""
        return self._get_registry_map()

    # ------------------------------------------------------------------
    # Merge window
    # ------------------------------------------------------------------

    def _open_merge_window(self) -> None:
        self._merge_open = True
        self._merge_timer.start(self._merge_window_ms)

    def _restart_merge_window(self) -> None:
        if self._merge_open:
            self._merge_timer.start(self._merge_window_ms)

    def _close_merge_window(self) -> None:
        self._merge_timer.stop()
        self._merge_open = False

    # ------------------------------------------------------------------
    # Widget-core auto-wiring (per-node closures)
    # ------------------------------------------------------------------

    def wire_existing_nodes(self) -> None:
        """Connect ``value_changed`` on every node already in the scene.

        Call after loading a file or bulk-adding nodes that bypass the
        ``node_added`` signal.
        """
        for item in self._canvas.items():
            if not (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
                continue
            if id(item) in self._connected_cores:
                continue
            self._wire_node(item)

    def wire_node(self, node) -> None:
        """Public entry point: wire a single node's WidgetCore and port
        lifecycle signals for undo tracking.

        Call this for nodes added via paths that don't emit the
        ``node_added`` signal (e.g. ``NodeManager.clone_nodes``).
        Idempotent — safe to call on an already-wired node.
        """
        if id(node) in self._connected_cores:
            return
        self._wire_node(node)

    def _wire_node(self, node) -> None:
        """Connect a node's WidgetCore.value_changed and port lifecycle
        signals with closures that capture the node UUID.
        
        Also snapshots all current widget values as baselines so the
        very first edit has a correct old_value to revert to.
        """
        wc = getattr(node, '_widget_core', None) or getattr(node, '_weave_core', None)
        if wc is None or not hasattr(wc, 'value_changed'):
            return
        try:
            node_uuid = get_node_uid(node)
            # Snapshot current values BEFORE connecting, so the first
            # edit knows what the pre-edit value was.
            for port_name in wc.bindings():
                try:
                    val = wc.get_port_value(port_name)
                    self._widget_baselines[(node_uuid, port_name)] = val
                except Exception:
                    pass

            slot = self._make_widget_slot(node_uuid, wc)
            wc.value_changed.connect(slot)
            self._connected_cores[id(node)] = (wc, slot)

            # Wire port lifecycle signals for undo tracking
            if hasattr(node, 'port_added'):
                pa_slot = self._make_port_added_slot(node_uuid)
                node.port_added.connect(pa_slot)
                # Store slot reference for cleanup
                self._port_slots[id(node)] = (pa_slot, None)

            if hasattr(node, 'port_removed'):
                pr_slot = self._make_port_removed_slot(node_uuid)
                node.port_removed.connect(pr_slot)
                # Merge with existing entry
                existing = self._port_slots.get(id(node), (None, None))
                self._port_slots[id(node)] = (existing[0] or pa_slot, pr_slot)

        except (RuntimeError, TypeError):
            pass

    def _make_widget_slot(self, node_uuid: str, wc):
        """Return a slot closure that pushes ``WidgetValueCommand`` s.

        The closure captures:
        - ``node_uuid``: stable identifier for the node
        - ``wc``: the WidgetCore, used to read the current value

        On each ``value_changed(port_name)`` emission:
        1. Read the current (new) value via ``wc.get_port_value``.
        2. Look up the baseline (old) value from ``_widget_baselines``.
        3. If they differ, push a ``WidgetValueCommand``.
        4. The ``push()`` method handles merge-window coalescing.
        """

        def _on_value_changed(port_name: str = ""):
            if self._restoring or not port_name:
                return

            key = (node_uuid, port_name)
            try:
                new_value = wc.get_port_value(port_name)
            except Exception:
                return

            old_value = self._widget_baselines.get(key)

            # If somehow no baseline exists (shouldn't happen after
            # _wire_node, but defensive), store current and return.
            if old_value is None:
                self._widget_baselines[key] = new_value
                return

            if old_value == new_value:
                return

            cmd = WidgetValueCommand(node_uuid, port_name, old_value, new_value)
            self.push(cmd)

            # Update baseline so the next change after the merge window
            # closes has a correct old_value.
            self._widget_baselines[key] = new_value

        return _on_value_changed

    def _make_port_added_slot(self, node_uuid: str):
        """Return a slot closure for ``port_added`` that pushes an
        ``AddPortCommand`` so the dynamic port addition can be undone.
        """

        def _on_port_added(port):
            if self._restoring:
                return
            cmd = AddPortCommand(
                node_uuid=node_uuid,
                port_name=getattr(port, 'name', ''),
                datatype=getattr(port, 'datatype', 'flow'),
                is_output=getattr(port, 'is_output', True),
                description=getattr(port, 'port_description', ''),
            )
            self.push(cmd)

        return _on_port_added

    def _make_port_removed_slot(self, node_uuid: str):
        """Return a slot closure for ``port_removed`` that pushes a
        ``RemovePortCommand`` so the dynamic port removal can be undone.

        NOTE: By the time ``port_removed`` fires, the port's traces have
        already been disconnected by ``_detach_port``.  Connection data
        is therefore empty here.  If callers need connection-preserving
        undo they must capture connections *before* calling
        ``remove_port()`` and push a ``CompoundCommand`` manually.
        """

        def _on_port_removed(port):
            if self._restoring:
                return
            cmd = RemovePortCommand(
                node_uuid=node_uuid,
                port_name=getattr(port, 'name', ''),
                datatype=getattr(port, 'datatype', 'flow'),
                is_output=getattr(port, 'is_output', True),
                description=getattr(port, 'port_description', ''),
                connections=[],
            )
            self.push(cmd)

        return _on_port_removed

    def _disconnect_all_cores(self) -> None:
        """Disconnect every tracked widget core and port signal."""
        for wc, slot in self._connected_cores.values():
            try:
                wc.value_changed.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connected_cores.clear()

        # Port lifecycle slots — need the actual node to disconnect,
        # but during clear() nodes may already be gone.  Best-effort.
        self._port_slots.clear()

    def _on_node_added(self, node) -> None:
        """Wire a newly added node's widget core."""
        if self._restoring:
            return
        self._wire_node(node)

    def _on_node_removed(self, node) -> None:
        """Disconnect a removed node's widget core and port signals."""
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

    # ------------------------------------------------------------------
    # Baseline management
    # ------------------------------------------------------------------

    def snapshot_widget_baselines(self) -> None:
        """Capture current widget values as baselines.

        Call after any non-widget mutation (node add, load, undo/redo)
        so that subsequent widget edits have correct old_values.
        """
        self._widget_baselines.clear()
        for wc, _slot in self._connected_cores.values():
            node_ref = getattr(wc, '_node_ref', None)
            if node_ref is None:
                continue
            uid = get_node_uid(node_ref)
            for port_name in wc.bindings():
                try:
                    self._widget_baselines[(uid, port_name)] = wc.get_port_value(port_name)
                except Exception:
                    pass