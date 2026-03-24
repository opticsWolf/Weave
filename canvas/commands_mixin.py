# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

CanvasCommandsMixin
-------------------
Defines all canvas-level commands as public ``cmd_*`` methods.

Both :class:`~weave.canvas_menu.ContextMenuProvider` and
:class:`~weave.canvas_states.DefaultInteractionState` use this mixin so that every
command is implemented exactly once.

ContextMenuProvider inherits from it directly.
DefaultInteractionState accesses it via ``canvas._context_menu_provider``.
"""

import os
from functools import partial
from typing import Optional, List, Dict, Any

from PySide6.QtWidgets import QGraphicsItem, QFileDialog, QMainWindow
from PySide6.QtCore import QSettings, Qt

from weave.canvas.canvas_grid import GridType
from weave.stylemanager import StyleManager, StyleCategory

from weave.logger import get_logger
log = get_logger("CanvasCommands")

# Graceful import handling for node components
#try:
from weave.node import NodeTrace, DragTrace
HAS_NODE_COMPONENTS = True
#except ImportError:
#    NodeTrace = None
#    DragTrace = None
#    HAS_NODE_COMPONENTS = False

# Serializer import
#try:
from weave.serializer import GraphSerializer
HAS_SERIALIZER = True

from weave.canvas.undo_commands import get_node_uid
#except ImportError:
#    GraphSerializer = None
#    HAS_SERIALIZER = False
#    log.warning("Serializer not found. File operations will be disabled.")


class CanvasCommandsMixin:
    """
    Mixin providing all canvas commands as public ``cmd_*`` methods.

    Subclasses (or users of the mixin) must set ``self._canvas`` before
    calling any command.  :class:`ContextMenuProvider` does this in its
    ``__init__``; :class:`DefaultInteractionState` accesses the mixin through
    ``canvas._context_menu_provider``.

    File-management state (current path, serializer cache, history) also
    lives here so it is shared by whichever entry point triggers the
    operation.
    """

    # File dialog filter for node graph files
    FILE_FILTER = "Node Graph (*.json);;All Files (*)"

    def _init_commands(self, canvas) -> None:
        """
        Initialise command state.  Call from ``__init__`` of the host class.
        """
        self._canvas = canvas
        self._current_filepath: Optional[str] = None
        self._serializer: Optional[Any] = None

        # File history
        self._file_history: List[str] = []
        self._max_history_items: int = 10
        self._settings = QSettings("opticsWolf", "Weave")
        self._load_file_history()

        # Panel management
        # Dynamic docks (inspectors that follow selection).  Multiple allowed.
        self._dynamic_docks: List[Any] = []
        # Static docks keyed by node UUID string — one per node.
        self._static_docks: Dict[str, Any] = {}

        # Undo / Redo
        from weave.canvas.undo_manager import UndoManager
        self._undo_manager = UndoManager(
            canvas, self._get_registry_map,
        )

    # =========================================================================
    # PUBLIC COMMAND API
    # =========================================================================

    def cmd_new(self) -> None:
        """Create a new blank canvas with default settings."""
        if hasattr(self._canvas, '_node_manager'):
            self._canvas._node_manager.clear_all()

        self._canvas.clearSelection()

        style_manager = StyleManager.instance()
        current_theme = style_manager.current_theme
        if current_theme:
            style_manager.apply_theme_and_prefs(current_theme)
            
        self._canvas.update()
        self._undo_manager.clear()
        log.info("New canvas created with default settings")

    def cmd_save(self) -> None:
        """Save the current graph. Opens Save As dialog if no path is set."""
        if self._current_filepath:
            self._do_save(self._current_filepath)
        else:
            self.cmd_save_as()

    def cmd_save_as(self) -> None:
        """Open a Save As dialog and save to the chosen path."""
        view = self._get_view()
        parent_widget = view if view else None

        start_dir = os.path.dirname(self._current_filepath) if self._current_filepath else ""

        filepath, _ = QFileDialog.getSaveFileName(
            parent_widget,
            "Save Node Graph",
            start_dir,
            self.FILE_FILTER,
        )

        if filepath:
            if not os.path.splitext(filepath)[1]:
                filepath += ".json"
            self._do_save(filepath)

    def cmd_open(self) -> None:
        """Open a file dialog and load the selected graph."""
        view = self._get_view()
        parent_widget = view if view else None

        start_dir = os.path.dirname(self._current_filepath) if self._current_filepath else ""

        filepath, _ = QFileDialog.getOpenFileName(
            parent_widget,
            "Open Node Graph",
            start_dir,
            self.FILE_FILTER,
        )

        if filepath:
            self._do_load(filepath)

    def cmd_open_recent(self, filepath: str) -> None:
        """Load a file from recent history by path."""
        if os.path.exists(filepath):
            self._do_load(filepath)

    def cmd_open_recent_by_index(self, index: int) -> None:
        """Load a file from recent history by zero-based index."""
        if 0 <= index < len(self._file_history):
            self.cmd_open_recent(self._file_history[index])

    def cmd_clear_canvas(self) -> None:
        """Remove all managed nodes and close all panels."""
        self.cmd_close_all_panels()
        if hasattr(self._canvas, '_node_manager'):
            self._canvas._node_manager.clear_all()
        self._canvas.clearSelection()
        self._undo_manager.clear()

    def cmd_select_all(self) -> None:
        """Select all movable nodes in the scene."""
        for item in self._canvas.items():
            is_movable = item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            is_trace = HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace))
            if is_movable and not is_trace:
                item.setSelected(True)

    def cmd_duplicate(self, target_item: QGraphicsItem) -> None:
        """
        Duplicate the target node (or all selected nodes if target is part
        of a multi-selection).
        """
        from weave.canvas.undo_commands import (
            AddNodeCommand, CompoundCommand, capture_node_snapshot,
        )
        root_node = self._resolve_root(target_item)
        if root_node is None:
            return
        if not (root_node.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
            return
        if HAS_NODE_COMPONENTS and isinstance(root_node, (NodeTrace, DragTrace)):
            return

        selected_nodes = self._get_movable_selected()
        if root_node in selected_nodes and len(selected_nodes) > 1:
            cloned = self._canvas.clone_nodes(selected_nodes)
        else:
            cloned = self._canvas.clone_nodes([root_node])

        if cloned:
            reg = self._get_registry_map()
            cmds = []
            for node in cloned:
                uid, cls_name, state, pos = capture_node_snapshot(node)
                cmds.append(AddNodeCommand(cls_name, state, uid, pos, reg))
            if len(cmds) == 1:
                self._undo_manager.push(cmds[0])
            else:
                self._undo_manager.push(CompoundCommand(cmds, "Duplicate nodes"))

    def cmd_duplicate_selected(self) -> None:
        """Duplicate all currently selected movable nodes."""
        from weave.canvas.undo_commands import (
            AddNodeCommand, CompoundCommand, capture_node_snapshot,
        )
        nodes = self._get_movable_selected()
        if not nodes:
            return
        cloned = self._canvas.clone_nodes(nodes)
        if cloned:
            reg = self._get_registry_map()
            cmds = []
            for node in cloned:
                uid, cls_name, state, pos = capture_node_snapshot(node)
                cmds.append(AddNodeCommand(cls_name, state, uid, pos, reg))
            if len(cmds) == 1:
                self._undo_manager.push(cmds[0])
            else:
                self._undo_manager.push(CompoundCommand(cmds, "Duplicate nodes"))

    def cmd_delete(self, target_item: QGraphicsItem) -> None:
        """
        Delete the target node (or all selected nodes if target is part
        of a multi-selection).
        """
        root_node = self._resolve_root(target_item)
        if root_node is None or root_node.scene() != self._canvas:
            return

        selected_nodes = self._get_movable_selected()
        if root_node in selected_nodes and len(selected_nodes) > 1:
            nodes_to_delete = selected_nodes
        else:
            nodes_to_delete = [root_node]

        self._remove_nodes(nodes_to_delete)

    def cmd_delete_selected(self) -> int:
        """Delete all currently selected movable nodes. Returns deletion count."""
        nodes = self._get_movable_selected()
        if not nodes:
            return 0
        return self._remove_nodes(nodes)

    def cmd_disconnect_selected(self) -> int:
        """Disconnect all traces from selected nodes without deleting them.

        Returns the number of traces removed.
        """
        from weave.portutils import ConnectionFactory
        from weave.canvas.undo_commands import RemoveConnectionsCommand
        from weave.node.node_trace import NodeTrace

        selected = self._canvas.selectedItems()
        if not selected:
            return 0

        nodes = [
            item for item in selected
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not (HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace)))
        ]
        if not nodes:
            return 0

        # Capture connection tuples BEFORE removing
        from weave.canvas.undo_commands import _get_port_lists
        seen: set = set()
        conn_tuples = []
        traces_to_remove: set = set()
        for node in nodes:
            for port_attr in ('inputs', 'outputs'):
                for port in getattr(node, port_attr, []):
                    for trace in list(getattr(port, 'connected_traces', [])):
                        if id(trace) in seen:
                            continue
                        seen.add(id(trace))
                        traces_to_remove.add(trace)

                        src = getattr(trace, 'source', None)
                        dst = getattr(trace, 'target', None)
                        if src and dst:
                            src_node = getattr(src, 'node', None)
                            dst_node = getattr(dst, 'node', None)
                            if src_node and dst_node:
                                _, out = _get_port_lists(src_node)
                                in_list, _ = _get_port_lists(dst_node)
                                try:
                                    conn_tuples.append((
                                        get_node_uid(src_node),
                                        out.index(src),
                                        get_node_uid(dst_node),
                                        in_list.index(dst),
                                    ))
                                except ValueError:
                                    pass

        removed = 0
        for trace in traces_to_remove:
            try:
                ConnectionFactory.remove(trace, trigger_compute=True)
                removed += 1
            except RuntimeError:
                pass

        if removed and conn_tuples:
            self._undo_manager.push(RemoveConnectionsCommand(conn_tuples))
        return removed

    # ── Undo / Redo ─────────────────────────────────────────────────────

    def cmd_undo(self) -> bool:
        """Undo the last graph-state change."""
        return self._undo_manager.undo()

    def cmd_redo(self) -> bool:
        """Redo the previously undone change."""
        return self._undo_manager.redo()

    def cmd_push(self, cmd) -> None:
        """Push a command onto the undo stack.

        Called by ``DefaultInteractionState`` and ``Canvas`` when they
        construct granular undo commands (e.g. ``MoveNodesCommand``).
        """
        self._undo_manager.push(cmd)

    @property
    def can_undo(self) -> bool:
        """True when there is at least one command to undo."""
        return self._undo_manager.can_undo

    @property
    def can_redo(self) -> bool:
        """True when there is at least one command to redo."""
        return self._undo_manager.can_redo

    def cmd_bring_to_front(self, target_item: QGraphicsItem) -> None:
        """Bring the target item to the front of the z-order."""
        root_node = self._resolve_root(target_item)
        if root_node and hasattr(self._canvas, '_orchestrator'):
            self._canvas._orchestrator.bring_to_front(root_node)

    def cmd_change_header_color(self, nodes: List[QGraphicsItem], color) -> None:
        """Apply ``color`` to the header of all ``nodes``."""
        from weave.canvas.undo_commands import NodePropertyCommand
        changes = []
        for node in nodes:
            uid = get_node_uid(node)
            old_color = getattr(node, '_header_bg', None)
            changes.append((uid, old_color, color))
            if hasattr(node, 'set_config'):
                node.set_config(header_bg=color)
            else:
                node.update()
        if changes:
            self._undo_manager.push(NodePropertyCommand(
                changes, 'set_config', 'Change header color',
            ))

    def cmd_set_grid_type(self, grid_type: GridType) -> None:
        """
        Switch the canvas grid to *grid_type* and repaint immediately.

        The new value is pushed through StyleManager so the canvas cache,
        any serializers, and any other style subscribers all stay in sync.
        The choice is persisted as a workspace preference.
        """
        sm = StyleManager.instance()
        sm.update(StyleCategory.CANVAS, grid_type=grid_type.value)
        sm.persist_workspace_prefs()

        # Force an immediate repaint on every attached viewport.
        try:
            for view in self._canvas.views():
                view.viewport().update()
        except Exception:
            pass

        log.debug(f"Grid type changed to: {grid_type.name}")

    def cmd_set_trace_style(self, connection_type: str) -> None:
        """
        Switch the trace connection style to *connection_type* and repaint.

        Accepted values match the keys understood by NodeTrace / DragTrace:
        ``"bezier"``, ``"straight"``, ``"angular"``.

        The new value is pushed through StyleManager so all registered
        NodeTrace and DragTrace instances receive an ``on_style_changed``
        notification and rebuild their paths automatically.
        The choice is persisted as a workspace preference.
        """
        sm = StyleManager.instance()
        sm.update(StyleCategory.TRACE, connection_type=connection_type)
        sm.persist_workspace_prefs()

        # Force an immediate repaint on every attached viewport.
        try:
            for view in self._canvas.views():
                view.viewport().update()
        except Exception:
            pass

        log.debug(f"Trace style changed to: {connection_type!r}")

    def cmd_toggle_snapping(self) -> None:
        """Toggle grid snapping and persist the preference."""
        sm = StyleManager.instance()
        current = sm.get(StyleCategory.CANVAS, "snapping_enabled", True)
        sm.update(StyleCategory.CANVAS, snapping_enabled=not current)
        sm.persist_workspace_prefs()
        log.debug(f"Snapping toggled to: {not current}")

    # =========================================================================
    # PANEL MANAGEMENT
    # =========================================================================

    def _get_main_window(self) -> Optional[QMainWindow]:
        """Return the QMainWindow that owns the first view, or None."""
        view = self._get_view()
        if view is None:
            return None
        widget = view.window()
        return widget if isinstance(widget, QMainWindow) else None

    def _get_dock_adapter_class(self):
        """Deferred import to avoid circular dependencies."""
        from weave.dockadapter import NodeDockAdapter
        return NodeDockAdapter

    # ── Dynamic (Inspector) panels ───────────────────────────────────────

    def cmd_add_dynamic_panel(self) -> None:
        """Create and show a new dynamic inspector panel.

        Each call creates a fresh inspector.  The title is numbered
        automatically ("Inspector", "Inspector (2)", …) using the
        lowest available number.
        """
        DockAdapter = self._get_dock_adapter_class()
        main_win = self._get_main_window()
        if main_win is None:
            log.warning("Cannot add dynamic panel: no QMainWindow found.")
            return

        title = self._next_dock_title("Inspector")
        dock = DockAdapter.create_dynamic(
            title, self._canvas, parent=main_win,
        )
        main_win.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, dock,
        )
        # When the user closes via the title-bar X, remove from the list.
        dock.destroyed.connect(lambda: self._dynamic_docks_discard(dock))
        self._dynamic_docks.append(dock)

    # Keep the old name as an alias so existing menu wiring works.
    cmd_show_dynamic_panel = cmd_add_dynamic_panel

    def cmd_hide_all_dynamic_panels(self) -> None:
        """Hide all dynamic inspector panels (does not destroy them)."""
        for dock in self._dynamic_docks:
            dock.hide()

    def cmd_show_all_dynamic_panels(self) -> None:
        """Show all existing dynamic inspector panels."""
        for dock in self._dynamic_docks:
            dock.show()
            dock.raise_()

    def cmd_remove_all_dynamic_panels(self) -> None:
        """Destroy all dynamic inspector panels."""
        for dock in list(self._dynamic_docks):
            try:
                dock.close()
                dock.deleteLater()
            except RuntimeError:
                pass
        self._dynamic_docks.clear()

    # Backward-compatible alias used by cmd_close_all_panels.
    cmd_remove_dynamic_panel = cmd_remove_all_dynamic_panels

    def _dynamic_docks_discard(self, dock) -> None:
        """Remove *dock* from the list if present (no-op otherwise)."""
        try:
            self._dynamic_docks.remove(dock)
        except ValueError:
            pass

    @property
    def dynamic_docks(self) -> List[Any]:
        """All live dynamic inspector docks."""
        return list(self._dynamic_docks)

    # ── Static (mirrored) panels ─────────────────────────────────────────

    def cmd_mirror_node(self, node: QGraphicsItem) -> None:
        """Create a static panel for *node*.

        One static panel per node — if a panel already exists for this
        node it is shown and raised instead of creating a duplicate.
        The dock title is numbered if another dock with the same base
        title already exists (e.g. "Float", "Float (2)").
        """
        DockAdapter = self._get_dock_adapter_class()
        main_win = self._get_main_window()
        if main_win is None:
            log.warning("Cannot mirror node: no QMainWindow found.")
            return

        # Resolve node UUID
        node_id = self._node_uuid_str(node)
        if node_id is None:
            log.warning("Cannot mirror node: node has no UUID.")
            return

        # Already mirrored? — just show it.
        existing = self._static_docks.get(node_id)
        if existing is not None:
            existing.show()
            existing.raise_()
            return

        # Determine a numbered title
        base_title = self._node_display_title(node)
        title = self._next_dock_title(base_title)

        dock = DockAdapter.create_static(title, node, parent=main_win)
        main_win.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, dock,
        )

        # Clean up when the node is deleted.
        dock.panel.linked_node_lost.connect(
            lambda _id=node_id: self._unregister_static_dock(_id)
        )
        # Clean up when the user clicks the dock's close (X) button.
        dock.dock_closed.connect(
            lambda _id=node_id: self._unregister_static_dock(_id, destroy=False)
        )
        self._static_docks[node_id] = dock

    def cmd_remove_static_panel(self, node: QGraphicsItem) -> None:
        """Remove the static panel for *node*, if any."""
        node_id = self._node_uuid_str(node)
        if node_id is None:
            return
        self._unregister_static_dock(node_id, destroy=True)

    def _unregister_static_dock(
        self, node_id: str, *, destroy: bool = True
    ) -> None:
        """Remove a static dock from the registry.

        Args:
            node_id:  The node UUID string key.
            destroy:  If True, call close() + deleteLater() on the dock.
                      Set to False when called from dock_closed (the dock
                      is already closing itself — calling close() again
                      would re-enter closeEvent).
        """
        dock = self._static_docks.pop(node_id, None)
        if dock is not None and destroy:
            try:
                dock.close()
                dock.deleteLater()
            except RuntimeError:
                pass

    def has_static_panel(self, node: QGraphicsItem) -> bool:
        """Return True if a static panel already exists for *node*."""
        node_id = self._node_uuid_str(node)
        return node_id is not None and node_id in self._static_docks

    # ── Bulk show / hide / close ─────────────────────────────────────────

    def cmd_show_all_panels(self) -> None:
        """Show all panels (dynamic + static)."""
        for dock in self._dynamic_docks:
            dock.show()
        for dock in self._static_docks.values():
            dock.show()

    def cmd_hide_all_panels(self) -> None:
        """Hide all panels (dynamic + static)."""
        for dock in self._dynamic_docks:
            dock.hide()
        for dock in self._static_docks.values():
            dock.hide()

    def cmd_close_all_panels(self) -> None:
        """Close and destroy all panels (dynamic + static)."""
        self.cmd_remove_all_dynamic_panels()
        # Iterate over a snapshot — closing modifies _static_docks.
        for node_id in list(self._static_docks):
            self._unregister_static_dock(node_id, destroy=True)

    # ── Panel state serialization ────────────────────────────────────────

    def get_panel_state(self) -> Dict[str, Any]:
        """Capture the current dock panel configuration.

        Returns a JSON-safe dict describing every live dynamic and
        static panel so that :meth:`restore_panel_state` can recreate
        them after a file load.

        Node references use the serializer's ``unique_id`` attribute
        (not the internal ``_node_uuid``) so they align with the
        ``uuid_map`` that the serializer builds during deserialization.
        """
        dynamic: List[Dict[str, Any]] = []
        for dock in self._dynamic_docks:
            entry: Dict[str, Any] = {"title": dock.windowTitle()}
            panel = dock.panel
            entry["pinned"] = panel.is_pinned
            # If pinned to a node, record the serializer-assigned ID.
            if panel.is_pinned and panel.node is not None:
                ser_id = get_node_uid(panel.node)
                if ser_id:
                    entry["pinned_node_uuid"] = ser_id
            dynamic.append(entry)

        static: List[Dict[str, Any]] = []
        for _internal_id, dock in self._static_docks.items():
            node = dock.node
            if node is None:
                continue
            ser_id = get_node_uid(node)
            if not ser_id:
                continue
            static.append({
                "node_uuid": ser_id,
                "title": dock.windowTitle(),
            })

        return {
            "dynamic": dynamic,
            "static": static,
        }

    def restore_panel_state(
        self, data: Dict[str, Any], uuid_map: Dict[str, Any]
    ) -> None:
        """Recreate dock panels from a previously saved state.

        Args:
            data:     The dict produced by :meth:`get_panel_state`.
            uuid_map: Mapping of serialized node-UUID strings to live
                      node instances (provided by the serializer after
                      all nodes have been restored).
        """
        # ── Dynamic inspectors ───────────────────────────────────────
        for entry in data.get("dynamic", []):
            self.cmd_add_dynamic_panel()
            dock = self._dynamic_docks[-1]  # the one we just created

            # Override the auto-generated title with the saved one.
            saved_title = entry.get("title")
            if saved_title:
                dock.setWindowTitle(saved_title)

            # Re-pin if the saved panel was pinned to a specific node.
            if entry.get("pinned") and "pinned_node_uuid" in entry:
                node = uuid_map.get(entry["pinned_node_uuid"])
                if node is not None:
                    # Bind to the node, then toggle the pin on.
                    dock.panel.bind_node(node, static=False)
                    dock.panel._header.set_pin_checked(True)
                    dock.panel._on_pin_toggled(True)

        # ── Static panels ────────────────────────────────────────────
        for entry in data.get("static", []):
            ser_node_id = entry.get("node_uuid")
            if ser_node_id is None:
                continue
            node = uuid_map.get(ser_node_id)
            if node is None:
                log.debug(
                    f"restore_panel_state: node {ser_node_id[:12]}… not found "
                    f"— skipping static panel '{entry.get('title')}'."
                )
                continue
            self.cmd_mirror_node(node)
            # cmd_mirror_node keys by _node_uuid_str (internal UUID),
            # not the serializer's unique_id — look up with the right key.
            internal_id = self._node_uuid_str(node)
            dock = self._static_docks.get(internal_id) if internal_id else None
            if dock is not None:
                saved_title = entry.get("title")
                if saved_title:
                    dock.setWindowTitle(saved_title)

    # ── Private helpers ──────────────────────────────────────────────────

    @staticmethod
    def _node_uuid_str(node: QGraphicsItem) -> Optional[str]:
        """Extract a string UUID from a node, or None."""
        if hasattr(node, 'get_uuid_string'):
            return node.get_uuid_string()
        if hasattr(node, '_node_uuid'):
            return str(node._node_uuid)
        return None

    @staticmethod
    def _node_display_title(node: QGraphicsItem) -> str:
        """Best-effort readable title for a node."""
        try:
            tip = node.header._title.toolTip()
            if tip:
                return tip
            return node.header._title.toPlainText()
        except Exception:
            pass
        name = getattr(node, 'name', None)
        if name:
            return str(name) if not callable(name) else name()
        return type(node).__name__

    def _next_dock_title(self, base_title: str) -> str:
        """Return a unique dock title based on *base_title*.

        Collects the titles of all existing static docks and the
        dynamic dock.  If *base_title* is not in use, returns it
        unchanged.  Otherwise appends the lowest available number
        in parentheses, e.g. ``"Float (2)"``.
        """
        import re

        existing_titles: set[str] = set()
        for dock in self._dynamic_docks:
            existing_titles.add(dock.windowTitle())
        for dock in self._static_docks.values():
            existing_titles.add(dock.windowTitle())

        if base_title not in existing_titles:
            return base_title

        # Collect numbers already in use for this base title.
        # Matches  "Base Title"  and  "Base Title (N)".
        used_numbers: set[int] = {1}  # The bare title counts as 1.
        pattern = re.compile(
            rf"^{re.escape(base_title)}\s*\((\d+)\)$"
        )
        for title in existing_titles:
            m = pattern.match(title)
            if m:
                used_numbers.add(int(m.group(1)))

        # Find the lowest unused number ≥ 2.
        n = 2
        while n in used_numbers:
            n += 1

        return f"{base_title} ({n})"

    # =========================================================================
    # BACKWARD-COMPATIBLE ALIASES (used by existing action connections)
    # =========================================================================

    def _on_new(self) -> None:
        self.cmd_new()

    def _on_save(self) -> None:
        self.cmd_save()

    def _on_save_as(self) -> None:
        self.cmd_save_as()

    def _on_load(self) -> None:
        self.cmd_open()

    def _on_load_recent_file(self, filepath: str) -> None:
        self.cmd_open_recent(filepath)

    def _on_clear_canvas_triggered(self) -> None:
        self.cmd_clear_canvas()

    def _on_select_all(self) -> None:
        self.cmd_select_all()

    def _on_duplicate_triggered(self, target_item: QGraphicsItem) -> None:
        self.cmd_duplicate(target_item)

    def _on_delete_triggered(self, target_item: QGraphicsItem) -> None:
        self.cmd_delete(target_item)

    def _on_bring_to_front(self, target_item: QGraphicsItem) -> None:
        self.cmd_bring_to_front(target_item)

    def _on_change_header_color(self, nodes, color) -> None:
        self.cmd_change_header_color(nodes, color)

    # =========================================================================
    # FILE OPERATIONS — INTERNALS
    # =========================================================================

    @property
    def current_filepath(self) -> Optional[str]:
        """The file path of the currently loaded/saved graph, or None."""
        return self._current_filepath

    @current_filepath.setter
    def current_filepath(self, path: Optional[str]) -> None:
        self._current_filepath = path

    def save(self) -> None:
        """Public API: save the current graph (programmatic access)."""
        self.cmd_save()

    def save_as(self) -> None:
        """Public API: save to a new path."""
        self.cmd_save_as()

    def load(self) -> None:
        """Public API: load via file dialog."""
        self.cmd_open()

    def load_file(self, filepath: str) -> None:
        """Public API: load from a specific path."""
        self._do_load(filepath)

    def save_file(self, filepath: str) -> None:
        """Public API: save to a specific path."""
        self._do_save(filepath)

    def _do_save(self, filepath: str) -> None:
        serializer = self._get_serializer()
        if serializer is None:
            log.warning("Cannot save: serializer unavailable.")
            return

        success = serializer.save_to_file(
            filepath,
            self._canvas,
            view=self._get_view(),
        )

        if success:
            self._current_filepath = filepath
            self._add_to_file_history(filepath)
            log.info(f"Graph saved to: {filepath}")
        else:
            log.error(f"Save failed: {filepath}")

    def _do_load(self, filepath: str) -> None:
        serializer = self._get_serializer()
        if serializer is None:
            log.warning("Cannot load: serializer unavailable.")
            return

        success = serializer.load_from_file(
            filepath,
            self._canvas,
            view=self._get_view(),
        )

        if success:
            self._current_filepath = filepath
            self._add_to_file_history(filepath)
            self._undo_manager.clear()
            self._undo_manager.wire_existing_nodes()
            self._undo_manager.snapshot_widget_baselines()
            log.info(f"Graph loaded from: {filepath}")
        else:
            log.error(f"Load failed: {filepath}")

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    def _get_movable_selected(self) -> List[QGraphicsItem]:
        """Return selected movable non-trace nodes."""
        return [
            item for item in self._canvas.selectedItems()
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not (HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace)))
        ]

    @staticmethod
    def _resolve_root(item: Optional[QGraphicsItem]) -> Optional[QGraphicsItem]:
        """Walk up the parent hierarchy to the root item."""
        node = item
        while node and node.parentItem():
            node = node.parentItem()
        return node

    def _remove_nodes(self, nodes: List[QGraphicsItem]) -> int:
        """Remove nodes via Canvas.remove_node (emits ``node_removed``). Returns count."""
        from weave.canvas.undo_commands import (
            RemoveNodesCommand, capture_node_snapshot, capture_node_connections,
        )
        # Capture state BEFORE removal for undo
        snapshots = []
        all_conns = []
        seen_conns: set = set()
        for node in nodes:
            if node.scene() != self._canvas:
                continue
            snapshots.append(capture_node_snapshot(node))
            for conn in capture_node_connections(self._canvas, node):
                if conn not in seen_conns:
                    all_conns.append(conn)
                    seen_conns.add(conn)

        # Perform deletion
        count = 0
        for node in nodes:
            if node.scene() != self._canvas:
                continue
            if hasattr(node, 'remove_all_connections'):
                node.remove_all_connections()
            try:
                self._canvas.remove_node(node)
                count += 1
            except RuntimeError as e:
                log.debug(f"Node already removed: {e}")

        if count and snapshots:
            self._undo_manager.push(
                RemoveNodesCommand(snapshots, all_conns, self._get_registry_map())
            )
        return count

    def _get_view(self):
        """Return the first QGraphicsView attached to the canvas, or None."""
        views = self._canvas.views()
        return views[0] if views else None

    def _get_serializer(self) -> Optional[Any]:
        """Lazily create and cache a GraphSerializer."""
        if self._serializer is not None:
            return self._serializer

        if not HAS_SERIALIZER or GraphSerializer is None:
            return None

        try:
            from weave.noderegistry import NODE_REGISTRY
            registry_map = {cls.__name__: cls for cls in NODE_REGISTRY.get_all_nodes()}
        except ImportError:
            registry_map = {}

        self._serializer = GraphSerializer(registry_map)
        return self._serializer

    def _get_registry_map(self) -> Dict[str, type]:
        """Return ``{class_name: cls}`` for node instantiation by undo commands."""
        try:
            from weave.noderegistry import NODE_REGISTRY
            return {cls.__name__: cls for cls in NODE_REGISTRY.get_all_nodes()}
        except ImportError:
            return {}

    # ── File history ─────────────────────────────────────────────────────────

    def _load_file_history(self) -> None:
        history = self._settings.value("file_history", [])
        if isinstance(history, list):
            self._file_history = [f for f in history if os.path.exists(f)]
        else:
            self._file_history = []

    def _save_file_history(self) -> None:
        self._settings.setValue("file_history", self._file_history)

    def _add_to_file_history(self, filepath: str) -> None:
        if filepath in self._file_history:
            self._file_history.remove(filepath)
        self._file_history.insert(0, filepath)
        if len(self._file_history) > self._max_history_items:
            self._file_history = self._file_history[:self._max_history_items]
        self._save_file_history()