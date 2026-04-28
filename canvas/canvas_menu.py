# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

import os
from functools import partial
from typing import Optional, List, Dict, Any, Tuple
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
from weave.canvas.canvas_icon_provider import get_menu_icon_provider
from weave.portregistry import PortRegistry

from weave.logger import get_logger
log = get_logger("ContextMenu")

# ============================================================================
# ICON PROVIDER — initialised lazily on first use via _icon()
# ============================================================================

def _icons():
    """Return the process-wide MenuIconProvider, or None if not configured."""
    from pathlib import Path
    default_path = Path(__file__).parent.parent / "resources" / "menu_icons"
    icon_dir = os.environ.get("WEAVE_ICON_DIR", default_path)
    icon_path = Path(icon_dir).resolve()
    return get_menu_icon_provider(directory=icon_path, size=16)


def _icon(name: str) -> Optional[QIcon]:
    """
    Fetch a theme-tinted menu icon by name.

    Returns None — rather than raising — when the provider is not
    configured or the named icon does not exist, so every call-site
    can treat icons as optional:

        if ic := _icon("save"): action.setIcon(ic)
    """
    provider = _icons()
    if provider is None:
        return None
    return provider.get_or_none(name)


# ============================================================================
# NODE ICON PROVIDER — theme-aware icons for node registry entries
# ============================================================================

def _get_node_icon_provider():
    """
    Return the process-wide ``NodeIconProvider`` singleton, or ``None``.

    The provider is created on first use with default parameters.
    Returns ``None`` — rather than raising — if the import fails so
    every call-site can treat node icons as optional.
    """
    #try:
    from weave.node.node_icon_provider import get_node_icon_provider
    return get_node_icon_provider()
    #except Exception:
    #    return None


def _node_icon(node_cls) -> Optional[QIcon]:
    """
    Return a theme-tinted menu ``QIcon`` for *node_cls* via
    ``NodeIconProvider.for_menu()``, or ``None`` if unavailable.
    """
    provider = _get_node_icon_provider()
    if provider is None:
        return None
    return provider.for_menu(node_cls)

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
        outer = QVBoxLayout(container)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)
 
        # Inner row: icon (optional) + line edit side by side
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
 
        # Search icon — uses the same provider as the rest of the menu
        _ic = _icon("input-search")
        if _ic is not None:
            icon_label = QLabel()
            icon_label.setPixmap(_ic.pixmap(_ic.availableSizes()[0] if _ic.availableSizes() else 16))
            icon_label.setFixedSize(16, 16)
            icon_label.setScaledContents(True)
            row.addWidget(icon_label)
 
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search nodes...")
        self._search_input.setMinimumWidth(250)
        self._search_input.setClearButtonEnabled(True)
        row.addWidget(self._search_input)
 
        outer.addLayout(row)
 
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
            
            # Set node icon via NodeIconProvider (theme-aware, tinted)
            if ic := _node_icon(result.node_cls):
                action.setIcon(ic)
            
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


# ============================================================================
# PORT SIGNATURE CACHE
# ============================================================================

def _node_display_name(node_cls) -> str:
    """Module-level helper for resolving node display names."""
    if HAS_REGISTRY and NODE_REGISTRY is not None:
        return NODE_REGISTRY.get_node_display_name(node_cls)
    return getattr(node_cls, 'node_name', None) or getattr(node_cls, '__name__', 'Unknown')


class _PortSignatureCache:
    """Lazy cache mapping each registered node class to its port signatures.

    FIXED: Uses the "Template Node" pattern. Instead of rapidly creating 
    and destroying dummy nodes (which corrupts PySide6's C++ style cache), 
    it keeps one template instance of each node alive in a hidden background scene.
    """

    def __init__(self):
        self._signatures: Dict[type, List[Tuple[str, str, bool]]] = {}
        self._registry_size: int = 0
        
        # Hidden scene to keep dummy nodes alive and safe from C++ deletion
        self._dummy_scene = None
        self._template_nodes = []

    def get_all(self) -> Dict[type, List[Tuple[str, str, bool]]]:
        """Return cached signatures, rebuilding if the registry has grown."""
        if not HAS_REGISTRY or NODE_REGISTRY is None:
            return {}
        current_size = len(NODE_REGISTRY._flat_map)
        if current_size != self._registry_size:
            self._rebuild()
        return self._signatures

    def _rebuild(self):
        """Instantiate each registered class once and keep it alive to read ports."""
        self._signatures.clear()
        if not HAS_REGISTRY or NODE_REGISTRY is None:
            return

        # Initialize the hidden scene once
        if self._dummy_scene is None:
            from PySide6.QtWidgets import QGraphicsScene
            self._dummy_scene = QGraphicsScene()

        all_classes = NODE_REGISTRY.get_all_nodes()
        self._registry_size = len(all_classes)

        for node_cls in all_classes:
            try:
                # 1. Instantiate the node and anchor it to the hidden scene
                node = node_cls()
                self._dummy_scene.addItem(node)
                
                # Prevent Python garbage collection from killing it
                self._template_nodes.append(node) 

                # 2. Extract signatures
                ports: List[Tuple[str, str, bool]] = []
                for p in getattr(node, 'inputs', []):
                    ports.append((
                        getattr(p, 'name', ''),
                        getattr(p, 'datatype', 'generic'),
                        False,
                    ))
                for p in getattr(node, 'outputs', []):
                    ports.append((
                        getattr(p, 'name', ''),
                        getattr(p, 'datatype', 'generic'),
                        True,
                    ))
                self._signatures[node_cls] = ports
                
                # NOTE: We intentionally DO NOT teardown or destroy the node here.
                # It lives peacefully in the background scene forever.

            except Exception as e:
                log.debug(f"Port signature cache: skip {node_cls.__name__}: {e}")

_port_sig_cache = _PortSignatureCache()


# ============================================================================
# COMPATIBLE NODE FILTER
# ============================================================================

def _find_compatible_classes(
    source_port,
    signatures: Dict[type, List[Tuple[str, str, bool]]],
) -> List[Tuple[type, str]]:
    """Return node classes that have at least one port compatible with
    *source_port*, together with the best matching port name.

    Returns:
        List of ``(node_class, best_port_name)`` sorted by match
        quality (exact type first, then cast-compatible, then wildcard)
        and then alphabetically by display name.
    """
    source_is_output = getattr(source_port, 'is_output', True)
    source_datatype = getattr(source_port, 'datatype', 'generic')
    source_ptype = PortRegistry.get(source_datatype)

    WILDCARD = frozenset({'generic', 'any', 'object'})
    source_is_wild = (
        source_ptype.name.lower() in WILDCARD if source_ptype else False
    )

    results: List[Tuple[type, str, int]] = []

    for node_cls, ports in signatures.items():
        best_port: Optional[str] = None
        best_score = 0  # 0=none  1=wildcard  2=cast  3=exact

        for port_name, port_datatype, port_is_output in ports:
            # Must be opposite direction
            if port_is_output == source_is_output:
                continue
            # Skip dummy / summary ports
            if port_datatype == 'dummy':
                continue

            port_ptype = PortRegistry.get(port_datatype)
            port_is_wild = (
                port_ptype.name.lower() in WILDCARD if port_ptype else False
            )

            if source_datatype == port_datatype:
                score = 3
            elif source_is_wild or port_is_wild:
                score = 1
            else:
                # Direction-aware converter check
                if source_is_output:
                    is_valid, _ = PortRegistry.get_converter(
                        source_ptype, port_ptype,
                    )
                else:
                    is_valid, _ = PortRegistry.get_converter(
                        port_ptype, source_ptype,
                    )
                score = 2 if is_valid else 0

            if score > best_score:
                best_score = score
                best_port = port_name

        if best_port is not None:
            results.append((node_cls, best_port, best_score))

    results.sort(key=lambda r: (
        -r[2], _node_display_name(r[0]).lower()
    ))
    return [(cls, pn) for cls, pn, _ in results]


# ============================================================================
# COMPATIBLE NODE MENU CONTROLLER
# ============================================================================

class CompatibleNodeMenuController:
    """Menu controller showing only nodes whose ports are compatible
    with the drag source.  Includes a search bar that filters within
    the pre-computed compatible subset.

    Spawning an entry auto-connects the best matching port so the
    user gets a connected node in one gesture.
    """

    def __init__(
        self,
        canvas,
        source_port,
        scene_pos: QPointF,
        menu: QMenu,
        compatible: List[Tuple[type, str]],
    ):
        self._canvas = canvas
        self._source_port = source_port
        self._source_is_output = getattr(source_port, 'is_output', True)
        self._scene_pos = scene_pos
        self._menu = menu
        self._all_compatible = compatible

        self._result_actions: List[QAction] = []
        self._current_index: int = -1
        self._search_timer: Optional[QTimer] = None
        self._status_action: Optional[QAction] = None

        self._setup()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup(self):
        # ── Search input ──────────────────────────────────────────────
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        _ic = _icon("input-search")
        if _ic is not None:
            icon_label = QLabel()
            icon_label.setPixmap(
                _ic.pixmap(
                    _ic.availableSizes()[0]
                    if _ic.availableSizes() else 16
                )
            )
            icon_label.setFixedSize(16, 16)
            icon_label.setScaledContents(True)
            row.addWidget(icon_label)

        self._search_input = QLineEdit()
        dtype = getattr(self._source_port, 'datatype', '?')
        direction = "input" if self._source_is_output else "output"
        self._search_input.setPlaceholderText(
            f"Search compatible nodes ({dtype} → {direction})…"
        )
        self._search_input.setMinimumWidth(280)
        self._search_input.setClearButtonEnabled(True)
        row.addWidget(self._search_input)

        outer.addLayout(row)
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed,
        )

        widget_action = QWidgetAction(self._menu)
        widget_action.setDefaultWidget(container)
        self._menu.addAction(widget_action)
        self._search_action = widget_action

        # ── Status line ───────────────────────────────────────────────
        self._status_action = QAction("", self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._update_status(len(self._all_compatible))

        self._menu.addSeparator()

        # ── Debounce timer ────────────────────────────────────────────
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        # ── Signals ───────────────────────────────────────────────────
        self._search_input.textChanged.connect(self._on_text_changed)
        self._search_input.returnPressed.connect(self._on_return_pressed)
        self._search_input.installEventFilter(
            _SearchEventFilter(self, self._search_input)
        )

        # ── Initial population ────────────────────────────────────────
        self._populate(self._all_compatible)
        QTimer.singleShot(0, self._search_input.setFocus)

    # ------------------------------------------------------------------
    # Populate / clear
    # ------------------------------------------------------------------

    def _populate(self, items: List[Tuple[type, str]]):
        self._clear_results()
        for node_cls, port_name in items:
            display = _node_display_name(node_cls)
            action = QAction(display, self._menu)

            cat = getattr(node_cls, 'node_class', '')
            sub = getattr(node_cls, 'node_subclass', '')
            path = f"{cat} > {sub}" if sub else cat
            action.setToolTip(f"{path}\nConnects to: {port_name}")
            action.setData((node_cls, port_name))

            if ic := _node_icon(node_cls):
                action.setIcon(ic)

            action.triggered.connect(
                partial(self._spawn_and_connect, node_cls, port_name)
            )
            self._menu.addAction(action)
            self._result_actions.append(action)

        if self._result_actions:
            self._current_index = 0
            self._highlight_current()

    def _clear_results(self):
        for action in self._result_actions:
            self._menu.removeAction(action)
        self._result_actions.clear()
        self._current_index = -1

    def _update_status(self, count: int):
        if self._status_action:
            self._status_action.setText(
                f"{count} compatible node{'s' if count != 1 else ''}"
            )

    # ------------------------------------------------------------------
    # Search filtering
    # ------------------------------------------------------------------

    def _on_text_changed(self, text: str):
        if self._search_timer:
            self._search_timer.stop()
        if not text.strip():
            self._populate(self._all_compatible)
            self._update_status(len(self._all_compatible))
            return
        self._search_timer.start(100)

    def _apply_filter(self):
        query = self._search_input.text().strip().lower()
        if not query:
            filtered = self._all_compatible
        else:
            terms = query.split()
            filtered = []
            for node_cls, port_name in self._all_compatible:
                name = _node_display_name(node_cls).lower()
                tags = ' '.join(
                    getattr(node_cls, 'node_tags', None) or []
                ).lower()
                cat = getattr(node_cls, 'node_class', '').lower()
                searchable = f"{name} {tags} {cat}"
                if all(t in searchable for t in terms):
                    filtered.append((node_cls, port_name))
        self._populate(filtered)
        self._update_status(len(filtered))

    # ------------------------------------------------------------------
    # Keyboard navigation  (interface expected by _SearchEventFilter)
    # ------------------------------------------------------------------

    def navigate_down(self):
        if not self._result_actions:
            return
        self._current_index = (
            (self._current_index + 1) % len(self._result_actions)
        )
        self._highlight_current()

    def navigate_up(self):
        if not self._result_actions:
            return
        self._current_index = (
            (self._current_index - 1) % len(self._result_actions)
        )
        self._highlight_current()

    def _highlight_current(self):
        if (self._result_actions
                and 0 <= self._current_index < len(self._result_actions)):
            self._menu.setActiveAction(
                self._result_actions[self._current_index]
            )

    def _on_return_pressed(self):
        if (self._result_actions
                and 0 <= self._current_index < len(self._result_actions)):
            data = self._result_actions[self._current_index].data()
            if data:
                node_cls, port_name = data
                self._spawn_and_connect(node_cls, port_name)

    # ------------------------------------------------------------------
    # Spawn + auto-connect
    # ------------------------------------------------------------------

    def _spawn_and_connect(self, node_cls, target_port_name: str):
        """Spawn a node at the drop position and auto-connect.

        The ``AddNodeCommand`` and ``AddConnectionCommand`` are bundled
        inside a single undo macro so Ctrl+Z reverts both atomically.
        """
        if not node_cls or not self._canvas:
            return

        provider = getattr(self._canvas, '_context_menu_provider', None)
        mgr = (
            getattr(provider, '_undo_manager', None) if provider else None
        )

        if mgr:
            mgr.begin_macro("Spawn and Connect Node")

        try:
            # 1. Spawn the node
            node = node_cls()
            self._canvas.add_node(
                node, (self._scene_pos.x(), self._scene_pos.y()),
            )

            # 2. Push AddNodeCommand
            if provider and mgr:
                from weave.canvas.undo_commands import (
                    AddNodeCommand, capture_node_snapshot,
                )
                uid, cls_name, state, npos = capture_node_snapshot(node)
                mgr.push(AddNodeCommand(
                    cls_name, state, uid, npos,
                    provider._get_registry_map(),
                ))

            # 3. Find the matching port on the new node and connect
            search_list = (
                getattr(node, 'inputs', [])
                if self._source_is_output
                else getattr(node, 'outputs', [])
            )
            target_port = next(
                (p for p in search_list if p.name == target_port_name),
                None,
            )
            if target_port and self._source_port:
                self._canvas._create_connection(
                    self._source_port, target_port,
                )
        except Exception as e:
            log.error(f"Spawn-and-connect failed: {e}")
        finally:
            if mgr:
                mgr.end_macro()

        self._menu.close()


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

    def show_compatible_node_menu(
        self, source_port, scene_pos: QPointF, screen_pos,
    ) -> None:
        """Show a menu of node types compatible with *source_port*.

        Called when a connection drag is released on empty canvas.
        The menu is pre-filtered to nodes whose ports are type-compatible
        with the drag source, and includes a search bar for narrowing.
        Selecting an entry spawns the node and auto-connects.

        Args:
            source_port: The port the drag originated from.
            scene_pos:   Drop position in scene coordinates (spawn target).
            screen_pos:  Drop position in screen coordinates (menu anchor).
        """
        # 1. Build / refresh the port signature cache
        signatures = _port_sig_cache.get_all()
        if not signatures:
            return

        # 2. Filter to compatible classes
        compatible = _find_compatible_classes(source_port, signatures)
        if not compatible:
            return

        # 3. Build menu
        view = self._canvas.views()[0] if self._canvas.views() else None
        menu = QMenu(parent=view)

        self._compat_controller = CompatibleNodeMenuController(
            canvas=self._canvas,
            source_port=source_port,
            scene_pos=scene_pos,
            menu=menu,
            compatible=compatible,
        )

        # 4. Show (blocks until dismissed)
        menu.exec(screen_pos)
    
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
        if ic := _icon("duplicate"): duplicate_action.setIcon(ic)
        menu.addAction(duplicate_action)
        
        # Action: Delete Node(s)
        del_text = f"Delete {node_count} Nodes" if is_multi_selection else "Delete Node"
        delete_action = QAction(del_text, menu)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(lambda: self.cmd_delete(target_item))
        if ic := _icon("square-minus"): delete_action.setIcon(ic)
        menu.addAction(delete_action)
        
        menu.addSeparator()

        # Action: Undo
        undo_action = QAction("Undo", menu)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.setEnabled(self.can_undo)
        undo_action.triggered.connect(self.cmd_undo)
        if ic := _icon("undo"): undo_action.setIcon(ic)
        menu.addAction(undo_action)

        # Action: Redo
        redo_action = QAction("Redo", menu)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.setEnabled(self.can_redo)
        redo_action.triggered.connect(self.cmd_redo)
        if ic := _icon("redo"): redo_action.setIcon(ic)
        menu.addAction(redo_action)

        menu.addSeparator()

        # Action: Add Inspector (creates a new dynamic dock)
        if not is_multi_selection:
            add_insp = QAction("Add Inspector", menu)
            add_insp.triggered.connect(self.cmd_add_dynamic_panel)
            if ic := _icon("eye-plus"): add_insp.setIcon(ic)
            menu.addAction(add_insp)

        # Action: Mirror to Panel (static dock)
        if not is_multi_selection:
            if self.has_static_panel(root_node):
                remove_panel = QAction("Remove Panel", menu)
                remove_panel.triggered.connect(
                    lambda: self.cmd_remove_static_panel(root_node)
                )
                if ic := _icon("panel_remove"): remove_panel.setIcon(ic)
                menu.addAction(remove_panel)
            else:
                mirror_action = QAction("Mirror to Panel", menu)
                mirror_action.triggered.connect(
                    lambda: self.cmd_mirror_node(root_node)
                )
                if ic := _icon("add-mirror"): mirror_action.setIcon(ic)
                menu.addAction(mirror_action)

        menu.addSeparator()

        # Action: Clear Canvas (only for single node selection or no selection)
        clear_canvas_action = QAction("Clear Canvas", menu)
        clear_canvas_action.setShortcut("Ctrl+Shift+C")  # Using Ctrl+Shift+C to avoid conflicts
        clear_canvas_action.triggered.connect(self.cmd_clear_canvas)
        if ic := _icon("eraser"): clear_canvas_action.setIcon(ic)
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
            if ic := _icon("palette"): color_menu.setIcon(ic)
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
        if ic := _icon("click"): select_all_action.setIcon(ic)
        menu.addAction(select_all_action)
        
        # Action: Bring to Front
        front_action = QAction("Bring to Front", menu)
        front_action.triggered.connect(lambda: self.cmd_bring_to_front(target_item))
        if ic := _icon("arrow-move-up"): front_action.setIcon(ic)
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
            if ic := _icon("file"): file_menu.setIcon(ic)

            # New command
            new_action = QAction("New", file_menu)
            new_action.setShortcut("Ctrl+N")
            new_action.triggered.connect(self._on_new)
            if ic := _icon("file-new"): new_action.setIcon(ic)
            file_menu.addAction(new_action)
            
            save_action = QAction("Save", file_menu)
            save_action.setShortcut("Ctrl+S")
            save_action.triggered.connect(self._on_save)
            if ic := _icon("file-save"): save_action.setIcon(ic)
            file_menu.addAction(save_action)
            
            save_as_action = QAction("Save As...", file_menu)
            save_as_action.setShortcut("Ctrl+Shift+S")
            save_as_action.triggered.connect(self._on_save_as)
            if ic := _icon("file-save_as"): save_as_action.setIcon(ic)
            file_menu.addAction(save_as_action)
            
            # Add recent files submenu
            if self._file_history:
                recent_files_menu = file_menu.addMenu("Recent Files")
                if ic := _icon("history"): recent_files_menu.setIcon(ic)
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
            if ic := _icon("open"): load_action.setIcon(ic)
            file_menu.addAction(load_action)
            
            menu.addSeparator()
        
        # Action: Select All (available even when clicking background)
        select_all_action = QAction("Select All", menu)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self._on_select_all)
        if ic := _icon("click"): select_all_action.setIcon(ic)
        menu.addAction(select_all_action)
        
        # Action: Clear Canvas
        clear_canvas_action = QAction("Clear Canvas", menu)
        clear_canvas_action.setShortcut("Ctrl+Shift+C")
        clear_canvas_action.triggered.connect(self._on_clear_canvas_triggered)
        if ic := _icon("eraser"): clear_canvas_action.setIcon(ic)
        menu.addAction(clear_canvas_action)
        
        menu.addSeparator()

        # Action: Undo
        undo_action = QAction("Undo", menu)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.setEnabled(self.can_undo)
        undo_action.triggered.connect(self.cmd_undo)
        if ic := _icon("undo"): undo_action.setIcon(ic)
        menu.addAction(undo_action)

        # Action: Redo
        redo_action = QAction("Redo", menu)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.setEnabled(self.can_redo)
        redo_action.triggered.connect(self.cmd_redo)
        if ic := _icon("redo"): redo_action.setIcon(ic)
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
            if ic := _icon("list-tree"): browse_menu.setIcon(ic)
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
        node_icons = _get_node_icon_provider()

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

            # Category icon — scan every node in the category looking for one
            # that provides a node_class_icon.  Only fall back to a plain
            # node_icon when *no* node in the entire category defines one,
            # so a single node returning None never silently hides a proper
            # class icon set on another node in the same group.
            if node_icons:
                all_cat_nodes = [n for nodes in valid_sub_cats.values() for n in nodes]
                cat_ic = next(
                    (ic for n in all_cat_nodes if (ic := node_icons.for_menu_class(n))),
                    None,
                ) or next(
                    (ic for n in all_cat_nodes if (ic := node_icons.for_menu(n))),
                    None,
                )
                if cat_ic:
                    cat_menu.setIcon(cat_ic)

            # Nodes with None subcategory go directly in the category menu
            direct_nodes = valid_sub_cats.get(None, [])
            if direct_nodes:
                for node_cls in sorted(direct_nodes, key=lambda c: self._get_node_display_name(c)):
                    action_name = self._get_node_display_name(node_cls)
                    action = cat_menu.addAction(action_name)
                    if node_icons:
                        if ic := node_icons.for_menu(node_cls):
                            action.setIcon(ic)
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

                # Subcategory icon — scan every node in the subcategory for a
                # node_subclass_icon first, then node_class_icon, and only fall
                # back to a plain node_icon when none of the nodes in the group
                # define a dedicated class or subclass icon.
                if node_icons and valid_nodes:
                    sub_ic = (
                        next(
                            (ic for n in valid_nodes if (ic := node_icons.for_menu_subclass(n))),
                            None,
                        )
                        or next(
                            (ic for n in valid_nodes if (ic := node_icons.for_menu_class(n))),
                            None,
                        )
                        or next(
                            (ic for n in valid_nodes if (ic := node_icons.for_menu(n))),
                            None,
                        )
                    )
                    if sub_ic:
                        sub_menu.setIcon(sub_ic)

                for node_cls in sorted(valid_nodes, key=lambda c: self._get_node_display_name(c)):
                    action_name = self._get_node_display_name(node_cls)
                    action = sub_menu.addAction(action_name)
                    if node_icons:
                        if ic := node_icons.for_menu(node_cls):
                            action.setIcon(ic)
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
        if ic := _icon("grid"): grid_menu.setIcon(ic)

        for grid_type, label in self._GRID_TYPE_LABELS.items():
            action = QAction(label, grid_menu)
            action.setCheckable(True)
            action.setChecked(grid_type == current_type)
            # Try a grid-type-specific icon (e.g. "grid_dots", "grid_lines"),
            # fall back to the generic "grid" icon.
            #icon_name = f"grid_{label.lower().replace(' ', '_').replace('(', '').replace(')', '')}"
            #if ic := (_icon(icon_name) or _icon("grid")):
            #    action.setIcon(ic)
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
        if ic := _icon("brush"): theme_menu.setIcon(ic)

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
            # Use a theme-specific icon if one exists (e.g. "theme_warm"),
            # otherwise fall back to the generic "theme" icon.
            #if ic := (_icon(f"theme_{name}") or _icon("theme")):
            #    action.setIcon(ic)
            action.triggered.connect(partial(self._on_apply_theme, name))
            theme_menu.addAction(action)

        theme_menu.addSeparator()

        load_action = QAction("Load Theme from File…", theme_menu)
        if ic := _icon("open"): load_action.setIcon(ic)
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
        if ic := _icon("trace"): trace_menu.setIcon(ic)

        for key, label in self._TRACE_STYLE_LABELS.items():
            action = QAction(label, trace_menu)
            action.setCheckable(True)
            action.setChecked(key == current_type)
            # Try "trace_bezier", "trace_straight", "trace_angular",
            # fall back to generic "trace".
            #if ic := (_icon(f"trace_{key}") or _icon("trace")):
            #    action.setIcon(ic)
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
        if ic := _icon("themes"): styles_menu.setIcon(ic)
        
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
        if ic := _icon("layout-2"): panels_menu.setIcon(ic)

        # ── Inspectors ───────────────────────────────────────────────
        add_insp = QAction("Add Inspector", panels_menu)
        add_insp.triggered.connect(self.cmd_add_dynamic_panel)
        if ic := _icon("eye-plus"): add_insp.setIcon(ic)
        panels_menu.addAction(add_insp)

        has_inspectors = bool(self._dynamic_docks)

        show_insp = QAction("Show All Inspectors", panels_menu)
        show_insp.setEnabled(has_inspectors)
        show_insp.triggered.connect(self.cmd_show_all_dynamic_panels)
        if ic := _icon("eye-check"): show_insp.setIcon(ic)
        panels_menu.addAction(show_insp)

        hide_insp = QAction("Hide All Inspectors", panels_menu)
        hide_insp.setEnabled(has_inspectors)
        hide_insp.triggered.connect(self.cmd_hide_all_dynamic_panels)
        if ic := _icon("eye-off"): hide_insp.setIcon(ic)
        panels_menu.addAction(hide_insp)

        panels_menu.addSeparator()

        # ── Bulk show / hide / close ─────────────────────────────────
        has_any = has_inspectors or bool(self._static_docks)

        show_all = QAction("Show All Panels", panels_menu)
        show_all.setEnabled(has_any)
        show_all.triggered.connect(self.cmd_show_all_panels)
        if ic := _icon("layout"): show_all.setIcon(ic)
        panels_menu.addAction(show_all)

        hide_all = QAction("Hide All Panels", panels_menu)
        hide_all.setEnabled(has_any)
        hide_all.triggered.connect(self.cmd_hide_all_panels)
        if ic := _icon("layout-off"): hide_all.setIcon(ic)
        panels_menu.addAction(hide_all)

        panels_menu.addSeparator()

        close_all = QAction("Close All Panels", panels_menu)
        close_all.setEnabled(has_any)
        close_all.triggered.connect(self.cmd_close_all_panels)
        if ic := _icon("square-x"): close_all.setIcon(ic)
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