# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas v12 - Performance Optimized
Integrates optimized connection dragging, port interaction, and context menu
with streamlined event handling and cached style access.

Performance Optimizations (v12):
1. Cached style properties - no StyleManager.get() calls in properties/hot paths
2. Observer pattern for style updates - cache refreshed only when styles change  
3. Optimized drawBackground - uses cached grid settings instead of get_all()
4. Compatible with optimized qt_canvasstates (v12)

Refactoring Changes from v10:
1. Removed _apply_grid_snapping() - now invoked by IdleState
2. Removed _handle_clone_trigger() - IdleState calls NodeManager directly
3. Removed _resolve_node_for_item() - consolidated into ItemResolver utility
4. Canvas is now a pure passive container for scene management

Responsibilities:
- Scene setup and configuration
- Node management (add/spawn via NodeManager)
- Rendering (grid background)
- State machine hosting
- Connection helper methods (used by ConnectionDragState)
"""

from typing import Optional, Tuple, Any, Dict, List, Type
from enum import IntEnum

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtWidgets import (
    QGraphicsScene, QGraphicsItem, QGraphicsProxyWidget,
    QGraphicsSceneContextMenuEvent, QGraphicsSceneMouseEvent
)
from PySide6.QtGui import QPainter, QTransform, QColor, QPen, QKeyEvent

from weave.canvas.canvas_grid import GridRenderer, GridType
from weave.canvas.canvas_orchestrator import CanvasOrchestrator
from weave.canvas.canvas_nodemanager import NodeManager
from weave.canvas.canvas_menu import ContextMenuProvider

from weave.canvas.canvas_states import (
    CanvasInteractionState, IdleState, ConnectionDragState, delete_selected_nodes
)

from weave.portutils import PortUtils, ConnectionFactory
from weave.node.node_port import NodePort
from weave.node.node_trace import NodeTrace, DragTrace
from weave.basenode import BaseControlNode

from weave.stylemanager import StyleManager, StyleCategory

from weave.logger import get_logger
log = get_logger("Canvas")


class Canvas(QGraphicsScene):
    """
    High-performance node graph canvas with state-machine-based interaction.
    
    Performance Optimizations (v12):
    - Cached style parameters updated via observer pattern
    - Properties return cached values (O(1)) instead of StyleManager.get() (O(N))
    - drawBackground uses cached grid settings
    - Reduced overhead in high-frequency rendering
    
    This class is a pure passive container. All interaction logic is
    delegated to the state machine (IdleState, ConnectionDragState).
    
    Architectural Role:
    - Hosts the state machine
    - Provides access to scene infrastructure (_orchestrator, _node_manager)
    - Exposes connection helpers for ConnectionDragState
    - Handles rendering and configuration
    
    Integration with StyleManager:
    - Subscribes to CANVAS category style changes 
    - Updates background color and layout properties dynamically
    - Caches grid pen and style parameters for performance
    """

    # Signals
    node_added = Signal(QGraphicsItem)
    node_removed = Signal(QGraphicsItem)
    connection_created = Signal(object, object)  # (source_port, target_port)
    selection_changed_custom = Signal(list)

    def __init__(self, parent=None, config=None):
        super().__init__(parent)

        # Initialize Style Manager singleton access
        self._style_manager = StyleManager.instance()
        
        # ===== CACHED STYLE PARAMETERS =====
        # Set up default cache properties before initializing subsystems
        self._cached_bg_color = QColor(30, 33, 40)
        self._cached_grid_color = QColor(50, 55, 62)
        self._cached_grid_type = GridType.DOTS
        self._cached_grid_spacing = 20
        self._cached_grid_line_width = 2.0
        self._cached_margin = 500
        self._cached_min_width = 3000
        self._cached_min_height = 2000
        self._cached_snapping_enabled = True
        self._cached_connection_snap_radius = 25.0
        self._cached_shake_to_disconnect = False
        self._cached_max_visible_grid_lines = 5000
        
        # Grid pen caching (updated when grid settings change)
        self._grid_pen = None
        
        # Sync cache with current StyleManager values FIRST
        self._sync_style_cache()
        
        # Initialize grid pen with cached values  
        self._update_grid_pen()

        # Initialize core subsystems BEFORE registering for style updates
        # Layout & Z-Order - use cached values
        self._orchestrator = CanvasOrchestrator(
            self,
            margin=self._cached_margin,
            min_width=self._cached_min_width,
            min_height=self._cached_min_height,
            debounce_ms=50,
            snap_radius=self._cached_connection_snap_radius
        )

        # Node Management
        self._node_manager = NodeManager(
            self,
            z_order_manager=self._orchestrator,
            grid_spacing=self._cached_grid_spacing
        )

        # Context Menu & Grid
        self._context_menu_provider = ContextMenuProvider(self)
        self._grid_renderer = GridRenderer()

        # State Machine
        self._current_state: CanvasInteractionState = IdleState(self)

        # Setup initial background
        self.setBackgroundBrush(self._cached_bg_color)
        
        # Scene setup
        self.changed.connect(self._orchestrator.schedule_resize)
        self.selectionChanged.connect(self._on_selection_changed)
        self._orchestrator.recalculate_bounds()

        # NOW that all subsystems are initialized, register for style updates
        self._style_manager.register(self, StyleCategory.CANVAS)
        
        # Listen for full theme switches so we can force-refresh every node,
        # port and trace via the NodeManager (safety net for items that are
        # not individually registered as StyleManager subscribers).
        self._style_manager.theme_changed.connect(self._on_theme_changed)
        
        # Apply provided config (will trigger on_style_changed safely)
        if config:
            self._style_manager.update(StyleCategory.CANVAS, **config)

    def _sync_style_cache(self):
        """
        Update all cached style parameters from StyleManager.
        
        Called:
        1. During __init__ to populate initial values
        2. By on_style_changed when CANVAS styles change
        
        Performance: O(1) bulk access using get_all() instead of 
        multiple individual get() calls
        """
        canvas_styles = self._style_manager.get_all(StyleCategory.CANVAS)
        
        # Update all cached values
        self._cached_bg_color = canvas_styles.get('bg_color', QColor(30, 33, 40))
        self._cached_grid_color = canvas_styles.get('grid_color', QColor(50, 55, 62))
        self._cached_grid_spacing = canvas_styles.get('grid_spacing', 20)
        self._cached_grid_line_width = canvas_styles.get('grid_line_width', 2.0)
        self._cached_margin = canvas_styles.get('margin', 500)
        self._cached_min_width = canvas_styles.get('min_width', 3000)
        self._cached_min_height = canvas_styles.get('min_height', 2000)
        self._cached_snapping_enabled = canvas_styles.get('snapping_enabled', True)
        self._cached_connection_snap_radius = canvas_styles.get('connection_snap_radius', 25.0)
        self._cached_shake_to_disconnect = canvas_styles.get('shake_to_disconnect', True)
        self._cached_max_visible_grid_lines = canvas_styles.get('max_visible_grid_lines', 5000)
        
        # Handle grid_type conversion
        grid_type_value = canvas_styles.get('grid_type', GridType.DOTS)
        if isinstance(grid_type_value, int):
            try:
                self._cached_grid_type = GridType(grid_type_value)
            except ValueError:
                self._cached_grid_type = GridType.DOTS
        else:
            self._cached_grid_type = grid_type_value

    def _update_grid_pen(self):
        """Update the cached grid pen when grid settings change."""
        self._grid_pen = QPen(self._cached_grid_color)
        self._grid_pen.setWidthF(self._cached_grid_line_width)
        self._grid_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

    def on_style_changed(self, category, changes):
        """
        Callback required by StyleManager subscription.
        Updates scene properties and cached values based on style modifications.
        
        Performance optimized: Instead of handling each change individually,
        we refresh the entire cache if ANY CANVAS style changed. This is more
        efficient than selective updates since cache refresh is O(1) bulk operation.
        
        Args:
            category: The StyleCategory that changed
            changes: Dict of changed key-value pairs
        """
        if category != StyleCategory.CANVAS:
            return

        # Refresh entire cache - more efficient than selective updates
        self._sync_style_cache()

        # Update background color if changed (visual update)
        if 'bg_color' in changes:
            self.setBackgroundBrush(self._cached_bg_color)
            
        # Handle grid settings that affect pen caching
        if any(key in changes for key in ('grid_color', 'grid_line_width')):
            self._update_grid_pen()
            
        # Update orchestrator bounds if layout constants change
        if any(key in changes for key in ('margin', 'min_width', 'min_height')):
            self._orchestrator.recalculate_bounds()
        
        # Update orchestrator snap radius if changed
        if 'connection_snap_radius' in changes:
            self._orchestrator.snap_radius = self._cached_connection_snap_radius
            
        # Trigger a redraw of the grid and scene
        self.update()

    def apply_new_theme(self, theme_name: str):
        """Helper to switch themes globally from the canvas."""
        self._style_manager.apply_theme(theme_name)

    def _on_theme_changed(self, theme_name: str) -> None:
        """
        Slot connected to StyleManager.theme_changed.

        Forces every managed node, port and trace to re-read the active
        style — regardless of whether they registered as subscribers.
        The StyleManager's own subscriber notifications handle items that
        *are* registered; this is the safety net for everything else.
        """
        self._node_manager.refresh_all_styles(self._style_manager)

        # Schedule a full scene repaint so the new visuals are
        # composited immediately.
        self.update()

    def set_state(self, state: CanvasInteractionState) -> None:
        """Transition to a new interaction state."""
        if hasattr(self._current_state, 'on_exit'):
            self._current_state.on_exit()
        
        self._current_state = state
        
        if hasattr(self._current_state, 'on_enter'):
            self._current_state.on_enter()

    # ==========================================================================
    # CONFIGURATION API - now using cached properties
    # ==========================================================================

    @property
    def style_manager(self):
        """Public access to the StyleManager instance (used by interaction states)."""
        return self._style_manager

    @property
    def shake_to_disconnect(self) -> bool:
        """
        Whether the shake-to-disconnect gesture is enabled.
        
        Performance: O(1) cached access instead of StyleManager.get()
        """
        return self._cached_shake_to_disconnect

    @property
    def grid_spacing(self) -> int:
        """
        Grid spacing in pixels.
        
        Performance: O(1) cached access instead of StyleManager.get()
        """
        return self._cached_grid_spacing
    
    @property
    def snapping_enabled(self) -> bool:
        """
        Whether grid snapping is enabled by default.
        
        Performance: O(1) cached access instead of StyleManager.get()
        """
        return self._cached_snapping_enabled
    
    @property
    def connection_snap_radius(self) -> float:
        """
        Snap radius for port connections in pixels.
        
        Performance: O(1) cached access instead of StyleManager.get()
        """
        return self._cached_connection_snap_radius

    def set_config(self, **kwargs) -> None:
        """
        Update configuration through StyleManager.
        
        Calling update() triggers on_style_changed via the subscriber
        mechanism, which refreshes the cache, updates the background,
        grid pen, orchestrator bounds, and schedules a repaint.
        No manual post-processing is needed here.
        """
        self._style_manager.update(StyleCategory.CANVAS, **kwargs)

    # ==========================================================================
    # NODE MANAGEMENT
    # ==========================================================================

    def add_node(self, node: QGraphicsItem, pos: Tuple[float, float] = (0, 0)) -> None:
        """Add a node to the canvas."""
        self._node_manager.add_node(node, pos)
        self._orchestrator.schedule_resize()
        self.node_added.emit(node)

    def spawn_node(self, node_cls: Type[QGraphicsItem], pos: QPointF) -> None:
        """Instantiate and add a node at the given position."""
        try:
            node = node_cls()
            self.add_node(node, (pos.x(), pos.y()))
        except Exception as e:
            log.error(f"Failed to spawn {node_cls.__name__}: {e}")

    def clone_nodes(self, nodes: List[QGraphicsItem]) -> List[QGraphicsItem]:
        """
        Clone nodes using NodeManager.
        
        Note: This public API remains for external callers. Internal state
        machine cloning now calls _node_manager.clone_nodes() directly.
        """
        return self._node_manager.clone_nodes(nodes)

    # ==========================================================================
    # RENDERING - Optimized with cached values
    # ==========================================================================

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """
        Draw grid background with cached settings.
        
        Performance optimized: Uses cached grid parameters instead of
        calling get_all() on every frame (potentially 60+ Hz).
        """
        super().drawBackground(painter, rect)
        
        # Use cached values - no StyleManager access needed
        grid_type = self._cached_grid_type
        spacing = self._cached_grid_spacing
        max_lines = self._cached_max_visible_grid_lines
        
        if self._grid_renderer.should_render(rect, spacing, max_lines, grid_type):
            self._grid_renderer.draw_grid(
                painter, rect, spacing, self._grid_pen, grid_type
            )

    # ==========================================================================
    # CONTEXT MENU
    # ==========================================================================

    def contextMenuEvent(self, event: QGraphicsSceneContextMenuEvent) -> None:
        """Handle right-click context menu (ignored if Ctrl is pressed)."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return

        item = self.itemAt(event.scenePos(), QTransform())
        if isinstance(item, (NodeTrace, DragTrace)):
            item = None

        try:
            menu = self._context_menu_provider.create_menu(event.scenePos(), item)
            if menu:
                menu.exec(event.screenPos())
            event.accept()
        except Exception as e:
            log.error(f"Context menu failed: {e}")
            super().contextMenuEvent(event)

    # ==========================================================================
    # MOUSE EVENT HANDLERS
    # ==========================================================================

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """
        Delegate to state machine.

        After Qt's default ``mousePressEvent`` runs, re-assert proxy focus
        if the user clicked into an embedded widget.  The default handler
        may shift scene focus to the **parent node** (it is the topmost
        selectable item), which steals focus from the proxy and makes the
        embedded widget's cursor disappear.
        """
        if self._current_state.on_mouse_press(event):
            event.accept()
            return

        super().mousePressEvent(event)

        # Re-assert proxy focus after super()'s default handling.
        if self._is_widget_editing():
            self._ensure_proxy_focus()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """Handle double-click - delegate to state machine for all logic."""
        if self._current_state.on_mouse_double_click(event):
            event.accept()
            return
        
        super().mouseDoubleClickEvent(event)

        if self._is_widget_editing():
            self._ensure_proxy_focus()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """
        Delegate to state machine.

        After release, force a proxy repaint so that a completed text
        selection highlight is shown immediately.
        """
        if self._current_state.on_mouse_release(event):
            event.accept()
            return
        super().mouseReleaseEvent(event)

        if self._is_widget_editing():
            self._update_editing_proxy()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        """
        Delegate to state machine, then apply grid snapping.
        
        Grid snapping must be applied AFTER Qt's default move behavior
        so that items have been repositioned first.
        
        Widget-editing:
            When the user is interacting with an embedded widget (e.g.
            click-dragging to select text), the move is routed through
            the proxy via ``super()`` and the proxy is repainted so that
            the text selection highlight updates in real time.

        Popup fix:
            When a widget interaction has suppressed node dragging
            (_suppressed_movable_node is set), grid snapping is skipped
            entirely.  Repositioning a node while a combo-box popup is
            open would detach the popup from the widget visually.
        """
        # Let state handle the move first (ConnectionDragState consumes this)
        if self._current_state.on_mouse_move(event):
            event.accept()
            return
        
        # Default behavior - Qt handles item dragging / proxy delivery
        super().mouseMoveEvent(event)
        
        # Skip snapping while a widget has suppressed node movement
        if getattr(self._current_state, '_suppressed_movable_node', None) is not None:
            # Force proxy repaint so text-selection highlight updates
            # in real time during click-drag inside a text field.
            self._update_editing_proxy()
            return
        
        # Apply grid snapping AFTER default behavior moved the items
        if hasattr(self._current_state, 'apply_grid_snapping'):
            self._current_state.apply_grid_snapping(event)

    # ==========================================================================
    # KEYBOARD EVENT HANDLING
    # ==========================================================================

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handle keyboard shortcuts for canvas operations.

        Widget-editing guard
        --------------------
        When the user is interacting with an embedded widget inside a
        node (``QLineEdit``, ``QTextEdit``, editable ``QComboBox``
        line-edit, ``QSpinBox``, etc.), **all** canvas shortcuts and
        ``QGraphicsScene`` default key behaviour (arrow-key item
        movement, Tab focus cycling, etc.) are suppressed.

        Keys are routed to the focused ``QGraphicsProxyWidget`` via
        ``super().keyPressEvent(event)`` and then **unconditionally
        accepted** so the scene does not act on them a second time.
        The proxy is then repainted so that cursor position and
        selection changes are shown immediately.

        Pressing **Escape** exits widget-editing mode: the proxy loses
        focus, the node's ``ItemIsMovable`` flag is restored (if it was
        suppressed), and canvas shortcuts become active again.

        Shortcuts (handled by IdleState, only when NOT editing a widget):
        - Delete / Backspace: Remove selected nodes
        - Ctrl+D: Duplicate selected nodes (with internal traces)
        - Ctrl+A: Select all nodes
        - Ctrl+N: New canvas
        - Ctrl+O: Open file
        - Ctrl+S: Save file
        - Ctrl+Shift+S: Save As
        - Ctrl+Shift+C: Clear canvas
        - Alt+[1-9]: Open recent file by index

        Other keys are passed to the parent implementation.
        """
        # ── Widget-editing guard ──────────────────────────────────────
        if self._is_widget_editing():
            if event.key() == Qt.Key.Key_Escape:
                self._exit_widget_editing()
                event.accept()
                return

            # Route to the focused proxy via standard QGraphicsScene
            # delivery so the embedded widget receives the key.
            # Accept unconditionally afterward so the scene's own
            # default key handling (arrow-key moves, Tab focus cycling,
            # etc.) is fully suppressed.
            super().keyPressEvent(event)
            event.accept()

            # Force the proxy to repaint so the cursor position /
            # selection / blink state is shown immediately.
            self._update_editing_proxy()
            return
        # ── End widget-editing guard ──────────────────────────────────

        # Delegate keyboard handling to current interaction state first
        if self._current_state.keyPressEvent(event):
            event.accept()
            return

        # Allow default handling for other keys
        super().keyPressEvent(event)

    # ==========================================================================
    # WIDGET-EDITING HELPERS
    # ==========================================================================

    def _is_widget_editing(self) -> bool:
        """
        Return ``True`` when the user is interacting with an embedded
        widget inside a node.

        Detection uses two independent signals (either is sufficient):

        1. The current state's ``_suppressed_movable_node`` is set,
           meaning ``_yield_to_proxy()`` has temporarily disabled node
           dragging so the widget can receive mouse events.
        2. A ``QGraphicsProxyWidget`` is the scene's current focus item,
           meaning the user has clicked into a widget and is typing.
        """
        # Signal 1: state machine flagged a widget interaction
        if getattr(self._current_state, '_suppressed_movable_node', None) is not None:
            return True
        # Signal 2: a proxy widget currently holds keyboard focus
        if isinstance(self.focusItem(), QGraphicsProxyWidget):
            return True
        return False

    def _get_editing_proxy(self) -> Optional[QGraphicsProxyWidget]:
        """
        Return the ``QGraphicsProxyWidget`` the user is currently
        interacting with, or ``None``.

        Checks the suppressed-node path first (the proxy is obtained
        via the node's ``_widget_core``), then falls back to the scene's
        current focus item.
        """
        # Path 1: via the node whose movability was suppressed
        node = getattr(self._current_state, '_suppressed_movable_node', None)
        if node is not None:
            core = getattr(node, '_widget_core', None)
            if core is not None:
                proxy = core.get_proxy()
                if proxy is not None:
                    return proxy

        # Path 2: the scene's current focus item
        focus_item = self.focusItem()
        if isinstance(focus_item, QGraphicsProxyWidget):
            return focus_item

        return None

    def _ensure_proxy_focus(self) -> None:
        """
        Make sure the ``QGraphicsProxyWidget`` has scene focus.

        ``QGraphicsScene.mousePressEvent()`` may give focus to the
        *parent node* (which is the topmost selectable item at the
        click position), stealing it from the proxy.  When the proxy
        loses scene focus, the embedded widget's ``hasFocus()`` becomes
        ``False``, the cursor blink timer stops, and the text cursor
        disappears.

        This method re-asserts focus on the proxy after the scene's
        default handling has run.
        """
        proxy = self._get_editing_proxy()
        if proxy is not None and not proxy.hasFocus():
            proxy.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_editing_proxy(self) -> None:
        """
        Force the editing proxy to repaint.

        Called after key events, mouse moves, and mouse releases so that
        cursor position changes, selection highlights, and blink-state
        toggles are reflected on screen immediately.  Without this,
        ``QGraphicsProxyWidget`` may defer repainting until the next
        idle cycle, making the cursor appear to "jump" or stay invisible
        while the user is actively editing.
        """
        proxy = self._get_editing_proxy()
        if proxy is not None:
            proxy.update()

    def _exit_widget_editing(self) -> None:
        """
        Leave widget-editing mode.

        * Restores the ``ItemIsMovable`` flag on the node whose movability
          was suppressed by ``_yield_to_proxy()``.
        * Clears keyboard focus from the ``QGraphicsProxyWidget`` so that
          subsequent key events reach the canvas shortcut handler again.
        """
        # Restore movable flag
        state = self._current_state
        node = getattr(state, '_suppressed_movable_node', None)
        if node is not None:
            try:
                node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            except RuntimeError:
                pass  # node already deleted
            state._suppressed_movable_node = None

        # Clear proxy focus
        focus_item = self.focusItem()
        if isinstance(focus_item, QGraphicsProxyWidget):
            focus_item.clearFocus()
    
    def _duplicate_selected_nodes(self) -> None:
        """
        Duplicate all selected movable nodes with their internal traces.
        """
        selected_nodes = [
            item for item in self.selectedItems()
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not isinstance(item, (NodeTrace, DragTrace))
        ]
        
        if selected_nodes:
            self._node_manager.clone_nodes(selected_nodes)
    
    def _select_all_nodes(self) -> None:
        """
        Select all movable nodes in the scene (excludes traces and fixed items).
        """
        for item in self.items():
            # Only select movable items that are not traces
            if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                and not isinstance(item, (NodeTrace, DragTrace))):
                item.setSelected(True)

    # ==========================================================================
    # CONNECTION HELPERS (used by ConnectionDragState)
    # ==========================================================================

    def _create_connection(self, start: NodePort, end: NodePort) -> Optional[NodeTrace]:
        """Create a connection and emit signal."""
        result = ConnectionFactory.create(
            self, start, end,
            validate=True,
            trigger_compute=True
        )
        if result:
            self.connection_created.emit(start, end)
        return result

    def _set_global_port_dimming(self, active: bool, source_port: Optional[NodePort]) -> None:
        """Dim incompatible ports during connection drag."""
        for item in self.items():
            if not isinstance(item, NodePort) or item == source_port:
                continue
            
            if active:
                if not PortUtils.are_compatible(source_port, item):
                    if hasattr(item, 'set_connection_state'):
                        item.set_connection_state(False)
            else:
                if hasattr(item, 'reset_connection_state'):
                    item.reset_connection_state()

    # ==========================================================================
    # SELECTION
    # ==========================================================================

    def _on_selection_changed(self) -> None:
        """Handle selection changes - bring to front and notify."""
        selected = self.selectedItems()
        
        for item in selected:
            self._orchestrator.bring_to_front(item)
        
        if hasattr(self._current_state, 'on_selection_changed'):
            self._current_state.on_selection_changed(selected)
            
        self.selection_changed_custom.emit(selected)

    # ==========================================================================
    # GETTERS FOR CONFIGURATION PROPERTIES (Cached for compatibility)
    # These now return cached values instead of calling StyleManager.get()
    # ==========================================================================

    @property 
    def bg_color(self) -> QColor:
        """Background color - cached value."""
        return self._cached_bg_color

    @property
    def grid_color(self) -> QColor:
        """Grid color - cached value."""
        return self._cached_grid_color

    @property
    def margin(self) -> int:
        """Canvas margin - cached value."""
        return self._cached_margin
    
    @property
    def min_width(self) -> int:
        """Minimum canvas width - cached value."""
        return self._cached_min_width
        
    @property 
    def min_height(self) -> int:
        """Minimum canvas height - cached value."""
        return self._cached_min_height

    @property
    def grid_type(self) -> GridType:
        """Grid type - cached value."""
        return self._cached_grid_type

    @property
    def grid_line_width(self) -> float:
        """Grid line width - cached value."""
        return self._cached_grid_line_width