# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

import os
from functools import partial
from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QMenu, QGraphicsItem, QWidgetAction, QLineEdit,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QFileDialog
)
from PySide6.QtCore import QPointF, Qt, QTimer, QEvent
from PySide6.QtGui import QAction, QKeySequence, QColor, QPixmap, QIcon

from weave.stylemanager import StyleManager, StyleCategory

from weave.logger import get_logger
log = get_logger("ContextMenu")

# Graceful import handling for node components
try:
    from weave.node import NodeTrace, DragTrace
    HAS_NODE_COMPONENTS = True
except ImportError:
    NodeTrace = None
    DragTrace = None
    HAS_NODE_COMPONENTS = False

# Node registry import
try:
    from weave.noderegistry import NODE_REGISTRY, SearchResult
    HAS_REGISTRY = True
except ImportError:
    NODE_REGISTRY = None
    SearchResult = None
    HAS_REGISTRY = False
    log.warning("Node registry not found. Registry menu will be disabled.")

# Serializer import
try:
    from weave.serializer import GraphSerializer
    HAS_SERIALIZER = True
except ImportError:
    GraphSerializer = None
    HAS_SERIALIZER = False
    log.warning("Serializer not found. File operations will be disabled.")


class SearchMenuController:
    """
    Controls search functionality by injecting a search QLineEdit into a QMenu
    via QWidgetAction, then dynamically adding/removing plain QActions as
    search results directly into the menu. No QListWidget is used.
    """

    # Sentinel used as the data payload for the search separator and status actions
    _SEARCH_SENTINEL = "__search_result__"

    def __init__(
        self,
        canvas: 'Canvas',
        scene_pos: QPointF,
        menu: QMenu,
        insert_before: Optional[QAction] = None,
        ):
                
        self._canvas = canvas
        self._scene_pos = scene_pos
        self._menu = menu
        self._insert_before = insert_before  # Actions are inserted before this action

        # Keep track of dynamically added result actions so we can remove them
        self._result_actions: List[QAction] = []
        self._status_action: Optional[QAction] = None
        self._separator_action: Optional[QAction] = None
        self._current_index: int = -1  # Track keyboard-navigable highlight

        self._search_timer: Optional[QTimer] = None

        self._setup(insert_before)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup(self, insert_before: Optional[QAction]) -> None:
        """Create the search input widget-action and wire signals."""

        # --- search input wrapped in a small widget for padding ---
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 8, 8, 8)
        layout.setSpacing(0)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search nodes...")
        self._search_input.setMinimumWidth(250)
        self._search_input.setClearButtonEnabled(True)
        layout.addWidget(self._search_input)

        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        widget_action = QWidgetAction(self._menu)
        widget_action.setDefaultWidget(container)

        if insert_before:
            self._menu.insertAction(insert_before, widget_action)
        else:
            self._menu.addAction(widget_action)

        # Store so we know where to insert result actions (right after the search bar)
        self._search_action = widget_action

        # --- debounce timer ---
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._perform_search)

        # --- signals ---
        self._search_input.textChanged.connect(self._on_text_changed)
        self._search_input.returnPressed.connect(self._on_return_pressed)
        self._search_input.installEventFilter(_SearchEventFilter(self, self._search_input))

        # Give focus after the menu is shown
        QTimer.singleShot(0, self._search_input.setFocus)

    # ------------------------------------------------------------------
    # Internal: dynamic action management
    # ------------------------------------------------------------------

    def _clear_results(self) -> None:
        """Remove all previously injected search-result actions."""
        for action in self._result_actions:
            self._menu.removeAction(action)
        self._result_actions.clear()

        if self._status_action:
            self._menu.removeAction(self._status_action)
            self._status_action = None

        if self._separator_action:
            self._menu.removeAction(self._separator_action)
            self._separator_action = None

        self._current_index = -1

    def _insertion_point(self) -> Optional[QAction]:
        """Return the QAction before which new results should be inserted."""
        # We insert results right after the search widget action.
        actions = self._menu.actions()
        try:
            idx = actions.index(self._search_action)
            if idx + 1 < len(actions):
                return actions[idx + 1]
        except ValueError:
            pass
        return None  # append at end

    def _insert_action(self, action: QAction) -> None:
        """Insert an action right after the last result (or after the search bar)."""
        # We always append result actions at the "insertion point" which is the
        # first action that existed *after* the search bar before we started adding.
        # Since we keep _insert_before from __init__, we can use that.
        if self._insert_before:
            self._menu.insertAction(self._insert_before, action)
        else:
            self._menu.addAction(action)

    # ------------------------------------------------------------------
    # Search logic
    # ------------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        if self._search_timer:
            self._search_timer.stop()
        query = text.strip()
        if not query:
            self._clear_results()
            return
        self._search_timer.start(150)

    def _perform_search(self) -> None:
        query = self._search_input.text().strip()
        self._clear_results()

        if not query:
            return

        if not HAS_REGISTRY or NODE_REGISTRY is None:
            self._add_status("Registry unavailable")
            return

        # Primary search
        results: List = NODE_REGISTRY.search(query, limit=50)
        fuzzy = False

        if not results:
            results = NODE_REGISTRY.fuzzy_search(query, threshold=0.5, limit=20)
            fuzzy = True

        if not results:
            self._add_status("No results found")
            return

        label = f"{len(results)} results" + (" (fuzzy)" if fuzzy else "")
        self._add_status(label)

        # Add a separator between status and result items
        sep = QAction(self._menu)
        sep.setSeparator(True)
        self._insert_action(sep)
        self._separator_action = sep

        # Populate result actions
        for result in results:
            display_name = self._get_node_display_name(result.node_cls)
            category_path = result.category
            if result.subcategory:
                category_path += f" > {result.subcategory}"

            action = QAction(display_name, self._menu)
            
            # Set node icon if available
            icon = NODE_REGISTRY.get_node_icon(result.node_cls) if HAS_REGISTRY else None
            if icon:
                action.setIcon(icon)
            
            action.setToolTip(
                f"Category: {category_path}\n"
                f"Score: {result.score:.1f}\n"
                f"Matched: {', '.join(result.matched_fields)}"
            )
            # Store node class on the action
            action.setData(result.node_cls)
            action.triggered.connect(partial(self._spawn_node, result.node_cls))
            self._insert_action(action)
            self._result_actions.append(action)

        # Highlight the first result
        if self._result_actions:
            self._current_index = 0
            self._highlight_current()

    def _add_status(self, text: str) -> None:
        """Add a disabled status action (e.g. '5 results')."""
        action = QAction(text, self._menu)
        action.setEnabled(False)
        self._insert_action(action)
        self._status_action = action

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _highlight_current(self) -> None:
        """Visually highlight the current result action in the menu."""
        if not self._result_actions:
            return
        # QMenu.setActiveAction sets the hover/highlight
        if 0 <= self._current_index < len(self._result_actions):
            self._menu.setActiveAction(self._result_actions[self._current_index])

    def navigate_down(self) -> None:
        if not self._result_actions:
            return
        self._current_index = (self._current_index + 1) % len(self._result_actions)
        self._highlight_current()

    def navigate_up(self) -> None:
        if not self._result_actions:
            return
        self._current_index = (self._current_index - 1) % len(self._result_actions)
        self._highlight_current()

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------

    def _on_return_pressed(self) -> None:
        """Spawn the currently highlighted node on Enter."""
        if self._result_actions and 0 <= self._current_index < len(self._result_actions):
            node_cls = self._result_actions[self._current_index].data()
            if node_cls:
                self._spawn_node(node_cls)

    def _spawn_node(self, node_cls) -> None:
        if node_cls and self._canvas:
            self._canvas.spawn_node(node_cls, self._scene_pos)
            self._menu.close()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _get_node_display_name(node_cls) -> str:
        if HAS_REGISTRY and NODE_REGISTRY is not None:
            return NODE_REGISTRY.get_node_display_name(node_cls)
        return getattr(node_cls, 'node_name', None) or getattr(node_cls, '__name__', 'Unknown Node')


class _SearchEventFilter(QWidget):
    """
    Tiny event filter installed on the QLineEdit to intercept
    arrow keys (for navigating results) and Escape (to close menu).
    """

    def __init__(self, controller: SearchMenuController, parent: QWidget):
        super().__init__(parent)
        self._ctrl = controller

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._ctrl.navigate_down()
                return True
            if key == Qt.Key.Key_Up:
                self._ctrl.navigate_up()
                return True
            if key == Qt.Key.Key_Escape:
                self._ctrl._menu.close()
                return True
        return super().eventFilter(obj, event)


class ContextMenuProvider:
    """
    Provides context menu functionality for the canvas.
    
    Two modes:
    1. Node Actions: When clicking on a node (duplicate, delete, etc.)
    2. Node Registry: When clicking on background (spawn new nodes)
    
    Also provides file operations (Save, Save As, Load) backed by
    :class:`GraphSerializer`.
    """
    
    # File dialog filter for node graph files
    FILE_FILTER = "Node Graph (*.json);;All Files (*)"
    
    def __init__(self, canvas: 'Canvas'):
        """
        Initialize the context menu provider.
        
        Args:
            canvas: The Canvas instance this provider serves.
        """
        self._canvas = canvas
        self._current_filepath: Optional[str] = None
        self._serializer: Optional[Any] = None
    
    def create_menu(self, scene_pos: QPointF, target_item: Optional[QGraphicsItem] = None) -> Optional[QMenu]:
        """
        Creates and returns the appropriate context menu.
        
        Args:
            scene_pos: The position in scene coordinates where the menu was requested.
            target_item: The item that was clicked, or None if background was clicked.
            
        Returns:
            QMenu instance ready to be shown, or None if no menu should be shown.
        """
        # Get the view for proper menu parenting
        view = self._canvas.views()[0] if self._canvas.views() else None
        
        # Create menu with proper parent
        menu = QMenu(parent=view)
        
        # Determine what kind of menu to build
        if target_item:
            self._build_node_actions(menu, target_item)
        else:
            self._build_registry_actions(menu, scene_pos)
        
        return menu if menu.actions() else None
    
    def _build_node_actions(self, menu: QMenu, target_item: QGraphicsItem) -> None:
        """
        Builds node-specific action menu with multi-selection awareness.
        
        Args:
            menu: The QMenu to populate.
            target_item: The node/item that was clicked.
        """
        # Find root node for target
        root_node = target_item
        while root_node and root_node.parentItem():
            root_node = root_node.parentItem()
        
        # Check if this is part of a multi-selection
        selected_items = self._canvas.selectedItems()
        selected_nodes = [
            item for item in selected_items
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not (HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace)))
        ]
        
        is_multi_selection = root_node in selected_nodes and len(selected_nodes) > 1
        node_count = len(selected_nodes) if is_multi_selection else 1
        
        # Action: Duplicate Node(s)
        dup_text = f"Duplicate {node_count} Nodes" if is_multi_selection else "Duplicate Node"
        duplicate_action = QAction(dup_text, menu)
        duplicate_action.setShortcut("Ctrl+D")
        duplicate_action.triggered.connect(lambda: self._on_duplicate_triggered(target_item))
        menu.addAction(duplicate_action)
        
        # Action: Delete Node(s)
        del_text = f"Delete {node_count} Nodes" if is_multi_selection else "Delete Node"
        delete_action = QAction(del_text, menu)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(lambda: self._on_delete_triggered(target_item))
        menu.addAction(delete_action)
        
        menu.addSeparator()
        
        # --- New Section: Header Color Sub-menu ---
        # Retrieve the palette list from StyleManager
        node_styles = StyleManager.instance().get_all(StyleCategory.NODE)
        palette = node_styles.get('header_color_palette', [])
        
        if palette:
            # Determine which nodes are targets for color change
            targets = selected_nodes if is_multi_selection else [root_node]
            
            color_menu = menu.addMenu("Set Header Color")
            for i, color in enumerate(palette):
                # Generate action with a color swatch icon
                action = QAction(f"Color {i + 1}", color_menu)
                
                pix = QPixmap(16, 16)
                pix.fill(color)
                action.setIcon(QIcon(pix))
                
                # Use partial to pass the target nodes and specific color
                action.triggered.connect(
                    partial(self._on_change_header_color, targets, color)
                )
                color_menu.addAction(action)
            
            menu.addSeparator()
        
        # Action: Select All
        select_all_action = QAction("Select All", menu)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self._on_select_all)
        menu.addAction(select_all_action)
        
        # Action: Bring to Front
        front_action = QAction("Bring to Front", menu)
        front_action.triggered.connect(lambda: self._on_bring_to_front(target_item))
        menu.addAction(front_action)
    
    def _build_registry_actions(self, menu: QMenu, scene_pos: QPointF) -> None:
        """
        Builds the node creation menu from the registry.
        
        Args:
            menu: The QMenu to populate.
            scene_pos: Where the new node should be spawned.
        """
        # --- File Operations ---
        if HAS_SERIALIZER:
            file_menu = menu.addMenu("File")
            
            save_action = QAction("Save", file_menu)
            save_action.setShortcut("Ctrl+S")
            save_action.triggered.connect(self._on_save)
            file_menu.addAction(save_action)
            
            save_as_action = QAction("Save As...", file_menu)
            save_as_action.setShortcut("Ctrl+Shift+S")
            save_as_action.triggered.connect(self._on_save_as)
            file_menu.addAction(save_as_action)
            
            file_menu.addSeparator()
            
            load_action = QAction("Open...", file_menu)
            load_action.setShortcut("Ctrl+O")
            load_action.triggered.connect(self._on_load)
            file_menu.addAction(load_action)
            
            menu.addSeparator()
        
        # Action: Select All (available even when clicking background)
        select_all_action = QAction("Select All", menu)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self._on_select_all)
        menu.addAction(select_all_action)
        
        menu.addSeparator()
        
        if not HAS_REGISTRY or NODE_REGISTRY is None:
            no_reg = QAction("Node Registry Unavailable", menu)
            no_reg.setEnabled(False)
            menu.addAction(no_reg)
            return
        
        # --- Browse Nodes submenu (always present) ---
        tree = NODE_REGISTRY.get_tree()
        if tree and isinstance(tree, dict):
            browse_menu = menu.addMenu("Browse Nodes")
            self._populate_browse_menu(browse_menu, tree, scene_pos)
        
        menu.addSeparator()

        # --- Search: injected as plain actions into the menu ---
        # The SearchMenuController adds a QWidgetAction (the search bar) and
        # will dynamically insert/remove regular QActions for results below it.
        # We pass insert_before=None so results appear at the bottom of the menu.
        self._search_controller = SearchMenuController(
            canvas=self._canvas,
            scene_pos=scene_pos,
            menu=menu,
            insert_before=None,
        )
    
    def _populate_browse_menu(self, browse_menu: QMenu, tree: dict, scene_pos: QPointF) -> None:
        """Build the hierarchical Browse Nodes submenu."""
        for category, sub_dict in sorted(tree.items()):
            if not isinstance(sub_dict, dict) or not sub_dict:
                continue
            
            valid_sub_cats = {}
            for sub_cat, node_classes in sub_dict.items():
                if not node_classes:
                    continue
                valid_nodes = [c for c in node_classes if c is not None]
                if valid_nodes:
                    valid_sub_cats[sub_cat] = valid_nodes
            
            if not valid_sub_cats:
                continue
            
            cat_menu = browse_menu.addMenu(str(category))
            
            # Nodes with None subcategory go directly in the category menu
            direct_nodes = valid_sub_cats.get(None, [])
            if direct_nodes:
                for node_cls in sorted(direct_nodes, key=lambda c: self._get_node_display_name(c)):
                    action_name = self._get_node_display_name(node_cls)
                    action = cat_menu.addAction(action_name)
                    icon = NODE_REGISTRY.get_node_icon(node_cls)
                    if icon:
                        action.setIcon(icon)
                    action.triggered.connect(
                        partial(self._canvas.spawn_node, node_cls, scene_pos)
                    )
                if any(k is not None for k in valid_sub_cats.keys()):
                    cat_menu.addSeparator()
            
            for sub_cat, valid_nodes in sorted(
                ((k, v) for k, v in valid_sub_cats.items() if k is not None),
                key=lambda x: str(x[0])
            ):
                sub_menu = cat_menu.addMenu(str(sub_cat))
                for node_cls in sorted(valid_nodes, key=lambda c: self._get_node_display_name(c)):
                    action_name = self._get_node_display_name(node_cls)
                    action = sub_menu.addAction(action_name)
                    icon = NODE_REGISTRY.get_node_icon(node_cls)
                    if icon:
                        action.setIcon(icon)
                    action.triggered.connect(
                        partial(self._canvas.spawn_node, node_cls, scene_pos)
                    )
    
    def _get_node_display_name(self, node_cls) -> str:
        """
        Extracts a display name for a node class.
        Delegates to the registry for centralized resolution.
        """
        if HAS_REGISTRY and NODE_REGISTRY is not None:
            return NODE_REGISTRY.get_node_display_name(node_cls)
        return getattr(node_cls, 'node_name', None) or getattr(node_cls, '__name__', 'Unknown Node')
    
    # ==========================================================================
    # ACTION HANDLERS
    # ==========================================================================
    
    def _on_duplicate_triggered(self, target_item: QGraphicsItem) -> None:
        """
        Handles node duplication with multi-selection support.
        """
        root_node = target_item
        while root_node and root_node.parentItem():
            root_node = root_node.parentItem()
        
        if not root_node or not (root_node.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
            return
            
        if HAS_NODE_COMPONENTS and isinstance(root_node, (NodeTrace, DragTrace)):
            return
        
        selected_items = self._canvas.selectedItems()
        selected_nodes = [
            item for item in selected_items
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not (HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace)))
        ]
        
        if root_node in selected_nodes and len(selected_nodes) > 1:
            self._canvas.clone_nodes(selected_nodes)
        else:
            self._canvas.clone_nodes([root_node])
    
    def _on_delete_triggered(self, target_item: QGraphicsItem) -> None:
        """
        Handles node deletion with multi-selection support.
        """
        root_node = target_item
        while root_node and root_node.parentItem():
            root_node = root_node.parentItem()
        
        if not root_node or root_node.scene() != self._canvas:
            return
        
        selected_items = self._canvas.selectedItems()
        selected_nodes = [
            item for item in selected_items
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not (HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace)))
        ]
        
        if root_node in selected_nodes and len(selected_nodes) > 1:
            nodes_to_delete = selected_nodes
        else:
            nodes_to_delete = [root_node]
        
        for node in nodes_to_delete:
            if node.scene() == self._canvas:
                if hasattr(node, 'remove_all_connections'):
                    node.remove_all_connections()
                
                node_manager = getattr(self._canvas, '_node_manager', None)
                if node_manager:
                    node_manager.remove_node(node)
                else:
                    self._canvas.removeItem(node)
    
    def _on_change_header_color(self, nodes: List[QGraphicsItem], color: QColor) -> None:
            """
            Applies the selected color to the header_bg of all target nodes.
            """
            for node in nodes:
                # Use the high-performance set_config method
                if hasattr(node, 'set_config'):
                    node.set_config(header_bg=color)
                else:
                    # Fallback for nodes that might not implement the full config protocol
                    node.update()
    
    def _on_bring_to_front(self, target_item: QGraphicsItem) -> None:
        """
        Brings the target item to the front of the z-order.
        """
        root_node = target_item
        while root_node and root_node.parentItem():
            root_node = root_node.parentItem()
        
        if root_node:
            if hasattr(self._canvas, '_orchestrator'):
                self._canvas._orchestrator.bring_to_front(root_node)
    
    def _on_select_all(self) -> None:
        """
        Select all movable nodes in the scene.
        """
        for item in self._canvas.items():
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                and not (HAS_NODE_COMPONENTS and isinstance(item, (NodeTrace, DragTrace)))):
                item.setSelected(True)

    # ==========================================================================
    # FILE OPERATIONS
    # ==========================================================================

    def _get_serializer(self) -> Optional[Any]:
        """
        Lazily create the :class:`GraphSerializer` from the node registry.
        
        The serializer is cached for the lifetime of this provider.
        
        Returns:
            A ``GraphSerializer`` instance, or ``None`` if the serializer
            or registry is unavailable.
        """
        if self._serializer is not None:
            return self._serializer

        if not HAS_SERIALIZER or GraphSerializer is None:
            return None

        # Build registry map from NODE_REGISTRY
        if HAS_REGISTRY and NODE_REGISTRY is not None:
            registry_map = {
                cls.__name__: cls for cls in NODE_REGISTRY.get_all_nodes()
            }
        else:
            registry_map = {}

        self._serializer = GraphSerializer(registry_map)
        return self._serializer

    def _get_view(self):
        """Return the first QGraphicsView attached to the canvas, or None."""
        views = self._canvas.views()
        return views[0] if views else None

    def _get_minimap(self):
        """
        Attempt to find a minimap widget parented to the current view.
        
        The minimap is typically a child widget of the view with a
        ``corner`` attribute (from QtNodeMinimap).  Returns ``None``
        if not found.
        """
        view = self._get_view()
        if view is None:
            return None

        # Try common attribute names where the canvas or view stores the minimap
        for obj in (self._canvas, view):
            minimap = getattr(obj, '_minimap', None) or getattr(obj, 'minimap', None)
            if minimap is not None:
                return minimap

        # Fallback: scan view children for a QtNodeMinimap instance
        try:
            from qt_minimap import QtNodeMinimap
            for child in view.children():
                if isinstance(child, QtNodeMinimap):
                    return child
        except ImportError:
            pass

        return None

    @property
    def current_filepath(self) -> Optional[str]:
        """The file path of the currently loaded/saved graph, or None."""
        return self._current_filepath

    @current_filepath.setter
    def current_filepath(self, path: Optional[str]) -> None:
        self._current_filepath = path

    # ── Save ──────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """
        Save the current graph.
        
        If a file path is already set (from a previous Save As or Load),
        the graph is saved directly.  Otherwise, opens a Save As dialog.
        """
        if self._current_filepath:
            self._do_save(self._current_filepath)
        else:
            self._on_save_as()

    def _on_save_as(self) -> None:
        """
        Open a Save As dialog and save the graph to the chosen path.
        """
        view = self._get_view()
        parent_widget = view if view else None

        # Start in the directory of the current file, or the user's home
        start_dir = ""
        if self._current_filepath:
            start_dir = os.path.dirname(self._current_filepath)

        filepath, _ = QFileDialog.getSaveFileName(
            parent_widget,
            "Save Node Graph",
            start_dir,
            self.FILE_FILTER,
        )

        if filepath:
            # Ensure .json extension
            if not os.path.splitext(filepath)[1]:
                filepath += ".json"
            self._do_save(filepath)

    def _do_save(self, filepath: str) -> None:
        """
        Perform the actual save operation.
        
        Args:
            filepath: Target file path.
        """
        serializer = self._get_serializer()
        if serializer is None:
            log.warning("Cannot save: serializer unavailable.")
            return

        view = self._get_view()
        minimap = self._get_minimap()

        success = serializer.save_to_file(
            filepath,
            self._canvas,
            view=view,
            minimap=minimap,
            include_style=True,
        )

        if success:
            self._current_filepath = filepath
            log.info(f"Graph saved to: {filepath}")
        else:
            log.error(f"Save failed: {filepath}")

    # ── Load ──────────────────────────────────────────────────────

    def _on_load(self) -> None:
        """
        Open a file dialog and load the selected graph file.
        """
        view = self._get_view()
        parent_widget = view if view else None

        start_dir = ""
        if self._current_filepath:
            start_dir = os.path.dirname(self._current_filepath)

        filepath, _ = QFileDialog.getOpenFileName(
            parent_widget,
            "Open Node Graph",
            start_dir,
            self.FILE_FILTER,
        )

        if filepath:
            self._do_load(filepath)

    def _do_load(self, filepath: str) -> None:
        """
        Perform the actual load operation.
        
        Args:
            filepath: Source file path.
        """
        serializer = self._get_serializer()
        if serializer is None:
            log.warning("Cannot load: serializer unavailable.")
            return

        view = self._get_view()
        minimap = self._get_minimap()

        success = serializer.load_from_file(
            filepath,
            self._canvas,
            view=view,
            minimap=minimap,
            restore_style=True,
        )

        if success:
            self._current_filepath = filepath
            log.info(f"Graph loaded from: {filepath}")
        else:
            log.error(f"Load failed: {filepath}")

    def save(self) -> None:
        """Public API: save the current graph (programmatic access)."""
        self._on_save()

    def save_as(self) -> None:
        """Public API: save the current graph to a new path."""
        self._on_save_as()

    def load(self) -> None:
        """Public API: load a graph from a file dialog."""
        self._on_load()

    def load_file(self, filepath: str) -> None:
        """Public API: load a graph from a specific file path."""
        self._do_load(filepath)

    def save_file(self, filepath: str) -> None:
        """Public API: save the graph to a specific file path."""
        self._do_save(filepath)