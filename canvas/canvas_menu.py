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
from PySide6.QtCore import QPointF, Qt, QTimer, QEvent, QSettings
from PySide6.QtGui import QAction, QKeySequence, QColor, QPixmap, QIcon

from weave.stylemanager import StyleManager, StyleCategory
from weave.canvas.canvas_grid import GridType
from weave.canvas.commands_mixin import CanvasCommandsMixin, HAS_NODE_COMPONENTS, HAS_SERIALIZER
from weave.node.node_trace import NodeTrace, DragTrace

from weave.logger import get_logger
log = get_logger("ContextMenu")

# ============================================================================
# THEME DISCOVERY
# ============================================================================

# Node registry import
try:
    from weave.noderegistry import NODE_REGISTRY, SearchResult
    HAS_REGISTRY = True
except ImportError:
    NODE_REGISTRY = None
    SearchResult = None
    HAS_REGISTRY = False
    log.warning("Node registry not found. Registry menu will be disabled.")

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


class ContextMenuProvider(CanvasCommandsMixin):
    """
    Provides context menu functionality for the canvas.
    
    Two modes:
    1. Node Actions: When clicking on a node (duplicate, delete, etc.)
    2. Node Registry: When clicking on background (spawn new nodes)
    
    All canvas commands are provided by :class:`CanvasCommandsMixin`.
    File operations (Save, Save As, Load) are backed by
    :class:`GraphSerializer` via the mixin.
    """
    
    def __init__(self, canvas: 'Canvas'):
        """
        Initialize the context menu provider.
        
        Args:
            canvas: The Canvas instance this provider serves.
        """
        self._init_commands(canvas)
    
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
        duplicate_action.triggered.connect(lambda: self.cmd_duplicate(target_item))
        menu.addAction(duplicate_action)
        
        # Action: Delete Node(s)
        del_text = f"Delete {node_count} Nodes" if is_multi_selection else "Delete Node"
        delete_action = QAction(del_text, menu)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(lambda: self.cmd_delete(target_item))
        menu.addAction(delete_action)
        
        menu.addSeparator()

        # Action: Undo
        undo_action = QAction("Undo", menu)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.setEnabled(self.can_undo)
        undo_action.triggered.connect(self.cmd_undo)
        menu.addAction(undo_action)

        # Action: Redo
        redo_action = QAction("Redo", menu)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.setEnabled(self.can_redo)
        redo_action.triggered.connect(self.cmd_redo)
        menu.addAction(redo_action)

        menu.addSeparator()

        # Action: Add Inspector (creates a new dynamic dock)
        if not is_multi_selection:
            add_insp = QAction("Add Inspector", menu)
            add_insp.triggered.connect(self.cmd_add_dynamic_panel)
            menu.addAction(add_insp)

        # Action: Mirror to Panel (static dock)
        if not is_multi_selection:
            if self.has_static_panel(root_node):
                remove_panel = QAction("Remove Panel", menu)
                remove_panel.triggered.connect(
                    lambda: self.cmd_remove_static_panel(root_node)
                )
                menu.addAction(remove_panel)
            else:
                mirror_action = QAction("Mirror to Panel", menu)
                mirror_action.triggered.connect(
                    lambda: self.cmd_mirror_node(root_node)
                )
                menu.addAction(mirror_action)

        menu.addSeparator()

        # Action: Clear Canvas (only for single node selection or no selection)
        clear_canvas_action = QAction("Clear Canvas", menu)
        clear_canvas_action.setShortcut("Ctrl+Shift+C")  # Using Ctrl+Shift+C to avoid conflicts
        clear_canvas_action.triggered.connect(self.cmd_clear_canvas)
        menu.addAction(clear_canvas_action)
        
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
                
                # Use partial to pass the target nodes and palette INDEX.
                # This ensures _header_color_index is stored on each node,
                # which enables correct theme switching (colors map to the
                # equivalent palette slot in the new theme) and correct
                # serialization (the index is saved/restored, not a raw colour).
                action.triggered.connect(
                    partial(self._on_change_header_color_by_index, targets, i)
                )
                color_menu.addAction(action)
            
            menu.addSeparator()
        
        # Action: Select All
        select_all_action = QAction("Select All", menu)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self.cmd_select_all)
        menu.addAction(select_all_action)
        
        # Action: Bring to Front
        front_action = QAction("Bring to Front", menu)
        front_action.triggered.connect(lambda: self.cmd_bring_to_front(target_item))
        menu.addAction(front_action)

        menu.addSeparator()

        # --- Styles submenu (Themes, Grid Style, Trace Style) ---
        self._build_styles_submenu(menu)
    
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
            
            # New command
            new_action = QAction("New", file_menu)
            new_action.setShortcut("Ctrl+N")
            new_action.triggered.connect(self._on_new)
            file_menu.addAction(new_action)
            
            save_action = QAction("Save", file_menu)
            save_action.setShortcut("Ctrl+S")
            save_action.triggered.connect(self._on_save)
            file_menu.addAction(save_action)
            
            save_as_action = QAction("Save As...", file_menu)
            save_as_action.setShortcut("Ctrl+Shift+S")
            save_as_action.triggered.connect(self._on_save_as)
            file_menu.addAction(save_as_action)
            
            # Add recent files submenu
            if self._file_history:
                recent_files_menu = file_menu.addMenu("Recent Files")
                for i, filepath in enumerate(self._file_history):
                    action = QAction(filepath, recent_files_menu)
                    action.triggered.connect(partial(self._on_load_recent_file, filepath))
                    # Add keyboard shortcut (Alt+1 through Alt+0)
                    if i < 9:
                        action.setShortcut(f"Alt+{i + 1}")
                    recent_files_menu.addAction(action)
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
        
        # Action: Clear Canvas
        clear_canvas_action = QAction("Clear Canvas", menu)
        clear_canvas_action.setShortcut("Ctrl+Shift+C")
        clear_canvas_action.triggered.connect(self._on_clear_canvas_triggered)
        menu.addAction(clear_canvas_action)
        
        menu.addSeparator()

        # Action: Undo
        undo_action = QAction("Undo", menu)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.setEnabled(self.can_undo)
        undo_action.triggered.connect(self.cmd_undo)
        menu.addAction(undo_action)

        # Action: Redo
        redo_action = QAction("Redo", menu)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.setEnabled(self.can_redo)
        redo_action.triggered.connect(self.cmd_redo)
        menu.addAction(redo_action)

        menu.addSeparator()
        
        # --- Panels submenu ---
        self._build_panels_submenu(menu)

        menu.addSeparator()

        if not HAS_REGISTRY or NODE_REGISTRY is None:
            no_reg = QAction("Node Registry Unavailable", menu)
            no_reg.setEnabled(False)
            menu.addAction(no_reg)
            return
        
        # --- Styles submenu (Themes, Grid Style, Trace Style) ---
        self._build_styles_submenu(menu)

        menu.addSeparator()

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
    # GRID TYPE SUBMENU
    # ==========================================================================

    # Human-readable labels for every GridType variant.
    _GRID_TYPE_LABELS = {
        GridType.LINES:        "Lines",
        GridType.DOTS:         "Dots",
        GridType.LINES_ACCENT: "Lines (Accented)",
        GridType.DOTS_ACCENT:  "Dots (Accented)",
        GridType.NONE:         "None",
    }

    def _build_grid_type_submenu(self, parent_menu: QMenu) -> None:
        """
        Append a "Grid Style" submenu to *parent_menu*.

        The currently active GridType is shown with a checkmark.
        Selecting an entry delegates to :meth:`cmd_set_grid_type` so the
        change flows through StyleManager and updates the canvas cache.
        """
        # Read the current grid type directly from the cached canvas property
        # so the checkmark is always accurate even after theme switches.
        canvas_styles = StyleManager.instance().get_all(StyleCategory.CANVAS)
        raw = canvas_styles.get('grid_type', GridType.DOTS)
        try:
            current_type = GridType(raw) if isinstance(raw, int) else raw
        except ValueError:
            current_type = GridType.DOTS

        grid_menu = parent_menu.addMenu("Grid Style")

        for grid_type, label in self._GRID_TYPE_LABELS.items():
            action = QAction(label, grid_menu)
            action.setCheckable(True)
            action.setChecked(grid_type == current_type)
            action.triggered.connect(partial(self.cmd_set_grid_type, grid_type))
            grid_menu.addAction(action)

    # ==========================================================================
    # THEME SUBMENU
    # ==========================================================================

    def _build_themes_submenu(self, parent_menu: QMenu) -> None:
        """
        Append a "Themes" submenu to *parent_menu*.

        Theme discovery and initial restore are handled by StyleManager at
        first instantiation, so the list is always fully populated here.
        The currently active theme is shown with a checkmark (✓).
        """
        manager = StyleManager.instance()
        current = manager.current_theme
        available = manager.available_themes

        theme_menu = parent_menu.addMenu("Themes")

        if not available:
            no_themes = QAction("No themes available", theme_menu)
            no_themes.setEnabled(False)
            theme_menu.addAction(no_themes)
            return

        for name in available:
            # Pretty-print: "midnight" → "Midnight"
            label = name.replace("_", " ").title()
            action = QAction(label, theme_menu)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.triggered.connect(partial(self._on_apply_theme, name))
            theme_menu.addAction(action)

        theme_menu.addSeparator()

        load_action = QAction("Load Theme from File…", theme_menu)
        load_action.triggered.connect(self._on_load_theme_file)
        theme_menu.addAction(load_action)

    def _on_apply_theme(self, theme_name: str) -> None:
        """
        Apply *theme_name* via the StyleManager.

        StyleManager.apply_theme() handles:
          1. Resetting schemas to defaults and applying overrides.
          2. Notifying all registered subscribers (canvas, minimap, etc.).
          3. Emitting theme_changed, which the Canvas uses to force-refresh
             every managed node, port and trace via NodeManager.

        All we need here is a viewport repaint so new visuals are flushed.
        """
        manager = StyleManager.instance()
        if not manager.apply_theme(theme_name, persist_workspace=True):
            log.warning(f"apply_theme('{theme_name}') returned False – theme may be unknown.")
            return

        # Flush the viewport so new colours appear on screen immediately.
        try:
            for view in self._canvas.views():
                view.viewport().update()
        except Exception:
            pass

    def _on_load_theme_file(self) -> None:
        """Open a file dialog to load a JSON theme, register, and apply it."""
        view = self._canvas.views()[0] if self._canvas.views() else None
        filepath, _ = QFileDialog.getOpenFileName(
            view,
            "Load Theme File",
            "",
            "Theme Files (*.json);;All Files (*)",
        )
        if not filepath:
            return
        manager = StyleManager.instance()
        if manager.load_from_file(filepath, apply=True):
            log.debug(f"Theme loaded and applied from '{filepath}'")
        else:
            log.warning(f"Failed to load theme from '{filepath}'")
    
    # ==========================================================================
    # TRACE STYLE SUBMENU
    # ==========================================================================

    # Human-readable labels for every supported trace connection type.
    _TRACE_STYLE_LABELS = {
        "bezier":   "Bézier",
        "straight": "Straight",
        "angular":  "Angular",
    }

    def _build_trace_style_submenu(self, parent_menu: QMenu) -> None:
        """
        Append a "Trace Style" submenu to *parent_menu*.

        The currently active connection_type is shown with a checkmark.
        Selecting an entry calls :meth:`cmd_set_trace_style` so the change
        propagates through StyleManager and every live NodeTrace / DragTrace
        rebuilds its path automatically.
        """
        trace_styles = StyleManager.instance().get_all(StyleCategory.TRACE)
        current_type = trace_styles.get("connection_type", "bezier")

        trace_menu = parent_menu.addMenu("Trace Style")

        for key, label in self._TRACE_STYLE_LABELS.items():
            action = QAction(label, trace_menu)
            action.setCheckable(True)
            action.setChecked(key == current_type)
            action.triggered.connect(partial(self.cmd_set_trace_style, key))
            trace_menu.addAction(action)

    # ==========================================================================
    # STYLES SUBMENU (groups Themes, Grid Style, Trace Style)
    # ==========================================================================

    def _build_styles_submenu(self, parent_menu: QMenu) -> None:
        """
        Append a "Styles" submenu to *parent_menu* that contains the
        Themes, Grid Style, and Trace Style submenus.
        """
        styles_menu = parent_menu.addMenu("Styles")
        self._build_themes_submenu(styles_menu)
        self._build_grid_type_submenu(styles_menu)
        self._build_trace_style_submenu(styles_menu)

    # ==========================================================================
    # PANELS SUBMENU
    # ==========================================================================

    def _build_panels_submenu(self, parent_menu: QMenu) -> None:
        """
        Append a "Panels" submenu to *parent_menu*.

        Contains:
        - Add Inspector        (always — creates a new dynamic dock)
        - Show All Inspectors  (if any exist)
        - Hide All Inspectors  (if any exist)
        - separator
        - Show All Panels
        - Hide All Panels
        - separator
        - Close All Panels
        """
        panels_menu = parent_menu.addMenu("Panels")

        # ── Inspectors ───────────────────────────────────────────────
        add_insp = QAction("Add Inspector", panels_menu)
        add_insp.triggered.connect(self.cmd_add_dynamic_panel)
        panels_menu.addAction(add_insp)

        has_inspectors = bool(self._dynamic_docks)

        show_insp = QAction("Show All Inspectors", panels_menu)
        show_insp.setEnabled(has_inspectors)
        show_insp.triggered.connect(self.cmd_show_all_dynamic_panels)
        panels_menu.addAction(show_insp)

        hide_insp = QAction("Hide All Inspectors", panels_menu)
        hide_insp.setEnabled(has_inspectors)
        hide_insp.triggered.connect(self.cmd_hide_all_dynamic_panels)
        panels_menu.addAction(hide_insp)

        panels_menu.addSeparator()

        # ── Bulk show / hide / close ─────────────────────────────────
        has_any = has_inspectors or bool(self._static_docks)

        show_all = QAction("Show All Panels", panels_menu)
        show_all.setEnabled(has_any)
        show_all.triggered.connect(self.cmd_show_all_panels)
        panels_menu.addAction(show_all)

        hide_all = QAction("Hide All Panels", panels_menu)
        hide_all.setEnabled(has_any)
        hide_all.triggered.connect(self.cmd_hide_all_panels)
        panels_menu.addAction(hide_all)

        panels_menu.addSeparator()

        close_all = QAction("Close All Panels", panels_menu)
        close_all.setEnabled(has_any)
        close_all.triggered.connect(self.cmd_close_all_panels)
        panels_menu.addAction(close_all)

    # ==========================================================================
    # HEADER COLOR HANDLER
    # ==========================================================================

    def _on_change_header_color_by_index(self, targets, palette_index: int) -> None:
        """
        Set the header colour of *targets* via a palette index.

        Using the index-based API (``set_header_color_by_index``) rather than
        an absolute QColor ensures that:
        1. The index is persisted in the node's serialized state, so the
           colour survives save/load correctly.
        2. When the active theme changes, ``_reapply_header_color_from_index``
           maps the index to the equivalent colour in the new palette.
        3. Duplicating a node copies the index, so the clone gets the
           correct palette colour — not the raw QColor from the old theme.
        """
        from weave.canvas.undo_commands import NodePropertyCommand, get_node_uid
        changes = []
        for node in targets:
            uid = get_node_uid(node)
            old_idx = getattr(node, '_header_color_index', None)
            changes.append((uid, old_idx, palette_index))
            if hasattr(node, 'set_header_color_by_index'):
                node.set_header_color_by_index(palette_index)
        if changes:
            self._undo_manager.push(NodePropertyCommand(
                changes, 'set_header_color_by_index', 'Change header color',
            ))

    # ==========================================================================
    # ACTION HANDLERS
    # All command logic lives in CanvasCommandsMixin.  The _on_* aliases
    # defined there delegate to cmd_* so existing signal connections continue
    # to work without modification.
    # ==========================================================================