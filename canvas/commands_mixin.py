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
:class:`~weave.canvas_states.IdleState` use this mixin so that every
command is implemented exactly once.

ContextMenuProvider inherits from it directly.
IdleState accesses it via ``canvas._context_menu_provider``.
"""

import os
from functools import partial
from typing import Optional, List, Any

from PySide6.QtWidgets import QGraphicsItem, QFileDialog
from PySide6.QtCore import QSettings

from weave.stylemanager import StyleManager

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
#except ImportError:
#    GraphSerializer = None
#    HAS_SERIALIZER = False
#    log.warning("Serializer not found. File operations will be disabled.")


class CanvasCommandsMixin:
    """
    Mixin providing all canvas commands as public ``cmd_*`` methods.

    Subclasses (or users of the mixin) must set ``self._canvas`` before
    calling any command.  :class:`ContextMenuProvider` does this in its
    ``__init__``; :class:`IdleState` accesses the mixin through
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
            style_manager.apply_theme(current_theme)

        self._canvas.update()
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
        """Remove all managed nodes from the canvas."""
        if hasattr(self._canvas, '_node_manager'):
            self._canvas._node_manager.clear_all()
        self._canvas.clearSelection()

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
        root_node = self._resolve_root(target_item)
        if root_node is None:
            return
        if not (root_node.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
            return
        if HAS_NODE_COMPONENTS and isinstance(root_node, (NodeTrace, DragTrace)):
            return

        selected_nodes = self._get_movable_selected()
        if root_node in selected_nodes and len(selected_nodes) > 1:
            self._canvas.clone_nodes(selected_nodes)
        else:
            self._canvas.clone_nodes([root_node])

    def cmd_duplicate_selected(self) -> None:
        """Duplicate all currently selected movable nodes."""
        nodes = self._get_movable_selected()
        if nodes:
            self._canvas.clone_nodes(nodes)

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

    def cmd_bring_to_front(self, target_item: QGraphicsItem) -> None:
        """Bring the target item to the front of the z-order."""
        root_node = self._resolve_root(target_item)
        if root_node and hasattr(self._canvas, '_orchestrator'):
            self._canvas._orchestrator.bring_to_front(root_node)

    def cmd_change_header_color(self, nodes: List[QGraphicsItem], color) -> None:
        """Apply ``color`` to the header of all ``nodes``."""
        for node in nodes:
            if hasattr(node, 'set_config'):
                node.set_config(header_bg=color)
            else:
                node.update()

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
            minimap=self._get_minimap(),
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
            minimap=self._get_minimap(),
        )

        if success:
            self._current_filepath = filepath
            self._add_to_file_history(filepath)
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
        """Remove nodes via NodeManager (or directly from scene). Returns count."""
        node_manager = getattr(self._canvas, '_node_manager', None)
        count = 0
        for node in nodes:
            if node.scene() != self._canvas:
                continue
            if hasattr(node, 'remove_all_connections'):
                node.remove_all_connections()
            if node_manager:
                node_manager.remove_node(node)
            else:
                self._canvas.removeItem(node)
            count += 1
        return count

    def _get_view(self):
        """Return the first QGraphicsView attached to the canvas, or None."""
        views = self._canvas.views()
        return views[0] if views else None

    def _get_minimap(self):
        """Find the minimap widget, if present."""
        view = self._get_view()
        if view is None:
            return None

        for obj in (self._canvas, view):
            minimap = getattr(obj, '_minimap', None) or getattr(obj, 'minimap', None)
            if minimap is not None:
                return minimap

        try:
            from qt_minimap import QtNodeMinimap
            for child in view.children():
                if isinstance(child, QtNodeMinimap):
                    return child
        except ImportError:
            pass

        return None

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