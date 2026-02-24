# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas Minimap - Refactored with StyleManager integration.

Optimized Minimap with 'Screen Space' rendering, Interactive UI controls,
and Auto-Minimize functionality with QVariantAnimation.

StyleManager Integration:
- Removed DEFAULT_MINIMAP_CONFIG dict (now managed by StyleManager)
- Added on_style_changed callback for live style updates
- Registers with StyleManager for MINIMAP category
"""
import sys
import math
from enum import Enum
from typing import Optional, Tuple, List, Dict, Any

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsItem, QWidget, QApplication, QStyleOptionGraphicsItem, QToolTip
)
from PySide6.QtCore import (
    Qt, QEvent, QObject, QRectF, QPointF, QPoint, QTimer, 
    QVariantAnimation, QEasingCurve, QAbstractAnimation
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QMouseEvent, QFont, QPainterPath, QEnterEvent, QCursor
)

# ------------------------------------------------------------------------------
# NEW IMPORTS: Icon Painters & Enum
# ------------------------------------------------------------------------------
from weave.canvas.canvas_minimap_icons import (
    MinimapButton, 
    IconReset, 
    IconFit, 
    IconPin, 
    IconSnap
)

# Import StyleManager for centralized styling
from weave.stylemanager import StyleManager, StyleCategory

from weave.logger import get_logger
log = get_logger("Minimap")


# ==============================================================================
# ENUMS & CONSTANTS
# ==============================================================================

class MinimapCorner(Enum):
    TOP_LEFT = 0
    TOP_RIGHT = 1
    BOTTOM_LEFT = 2
    BOTTOM_RIGHT = 3


# ==============================================================================
# MINIMAP CLASS
# ==============================================================================

class CanvasMinimap(QGraphicsView):
    """
    Optimized Minimap with 'Screen Space' rendering, Interactive UI controls,
    and Auto-Minimize functionality with QVariantAnimation.
    
    Integrated with StyleManager for centralized styling.
    """

    def __init__(
        self, 
        target_view: QGraphicsView, 
        parent: Optional[QWidget] = None, 
        config: Optional[Dict[str, Any]] = None
    ):
        super().__init__(parent)
        
        self._target_view = target_view
        self._target_scene = target_view.scene()
        self.setScene(self._target_scene)

        # Get StyleManager instance
        self._style_manager = StyleManager.instance()

        # 1. Initialize Config from StyleManager
        self._config = self._get_minimap_config()
        if config:
            self.set_config(**config)

        # 2. Initialize Icon Painters
        self._icon_painters = {
            MinimapButton.RESET: IconReset(),
            MinimapButton.FIT: IconFit(),
            MinimapButton.PIN: IconPin(),
            MinimapButton.SNAP: IconSnap()
        }

        # Interaction State
        self._current_corner = MinimapCorner.TOP_RIGHT
        self._is_panning_view = False
        self._is_dragging_widget = False
        self._drag_start_pos = QPointF()
        
        # Auto-Hide State
        self._auto_hide_enabled = False  # False = Pinned (Always visible)
        self._is_minimized = False       # Visual state (Icon mode vs Map mode)
        
        # UI State
        self._hovered_button: MinimapButton = MinimapButton.NONE

        # Timers for smooth hiding/unhiding triggers
        self._timer_show = QTimer(self)
        self._timer_show.setSingleShot(True)
        self._timer_show.timeout.connect(self._perform_expand)

        self._timer_hide = QTimer(self)
        self._timer_hide.setSingleShot(True)
        self._timer_hide.timeout.connect(self._perform_minimize)

        # Animation Setup
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim_value_changed)
        self._anim.finished.connect(self._on_anim_finished)
        # Store animation direction to handle state changes at end
        self._anim_target_is_minimized = False 

        self._setup_properties()
        self._setup_connections()
        
        # ==========================================================
        # AUTO-RESIZE LOGIC
        # ==========================================================
        if self.parentWidget():
            self.parentWidget().installEventFilter(self)
        
        # Initial position update
        self.update_position()
        
        # Register for style change notifications
        self._style_manager.register(self, StyleCategory.MINIMAP)

    def _get_minimap_config(self) -> Dict[str, Any]:
        """
        Get the current minimap configuration from StyleManager.
        
        Returns:
            A dictionary of minimap styling parameters.
        """
        return self._style_manager.get_all(StyleCategory.MINIMAP)

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        """
        Callback method called when StyleManager notifies about style changes.
        
        Args:
            category: The style category that changed (should be StyleCategory.MINIMAP)
            changes: Dictionary of changed keys and their new values
        """
        if category == StyleCategory.MINIMAP:
            # Update internal config with new values
            self._config.update(changes)
            
            # Check if geometry-related keys changed
            geom_keys = {'width', 'height', 'margin', 'minimized_size'}
            if geom_keys.intersection(changes.keys()):
                self.update_position()
            
            # Trigger repaint
            self.viewport().update()

    def set_config(self, **kwargs):
        """Update configuration with provided values."""
        should_update_geom = False
        
        for key, value in kwargs.items():
            if key in self._config:
                self._config[key] = value
                if key in ['width', 'height', 'margin', 'minimized_size']:
                    should_update_geom = True
            else:
                log.warning(f"Warning: Unknown config key '{key}' ignored.")

        if should_update_geom:
            self.update_position()
        
        self.viewport().update()

    def _setup_properties(self) -> None:
        """Configure view properties."""
        self.setInteractive(False)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | 
            QPainter.RenderHint.TextAntialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent; border: none;")
        self.setMouseTracking(True)

    def _setup_connections(self) -> None:
        """Setup signal connections."""
        self._target_view.horizontalScrollBar().valueChanged.connect(self._request_update)
        self._target_view.verticalScrollBar().valueChanged.connect(self._request_update)
        self._target_scene.sceneRectChanged.connect(self._on_scene_rect_changed)

    def _request_update(self, _=None):
        """Request viewport update."""
        self.viewport().update()

    def _on_scene_rect_changed(self, rect: QRectF):
        """Handle scene rect changes."""
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.viewport().update()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Filter events from parent widget."""
        if obj == self.parentWidget() and event.type() == QEvent.Type.Resize:
            # When parent resizes, if we aren't animating, snap to corner
            if self._anim.state() != QAbstractAnimation.State.Running:
                self.update_position()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        """Handle resize events."""
        super().resizeEvent(event)
        # We perform fitInView if we are NOT minimized.
        # During the animation to minimize, _is_minimized remains False until the end,
        # so we get a smooth scaling down effect.
        if not self._is_minimized:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # =========================================================================
    # GEOMETRY & ANIMATION
    # =========================================================================

    def _calculate_geometry_rect(self, minimized: bool) -> QRectF:
        """Helper to calculate target geometry without applying it."""
        if not self.parentWidget(): 
            return QRectF(self.geometry())
        
        m = self._config['margin']
        if minimized:
            w = self._config['minimized_size']
            h = self._config['minimized_size']
        else:
            w = self._config['width']
            h = self._config['height']
        
        p_rect = self.parentWidget().rect()
        x, y = self._get_coords_for_corner(self._current_corner, p_rect, w, h, m)
        return QRectF(x, y, w, h)

    def update_position(self):
        """Instant geometry update (no animation)."""
        rect = self._calculate_geometry_rect(self._is_minimized)
        self.setGeometry(rect.toRect())
        
        if not self._is_minimized:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _get_coords_for_corner(self, corner: MinimapCorner, p_rect: QRectF, w, h, m) -> Tuple[int, int]:
        """Get coordinates for a specific corner."""
        if corner == MinimapCorner.TOP_LEFT: 
            return (m, m)
        elif corner == MinimapCorner.TOP_RIGHT: 
            return (int(p_rect.width() - w - m), m)
        elif corner == MinimapCorner.BOTTOM_LEFT: 
            return (m, int(p_rect.height() - h - m))
        elif corner == MinimapCorner.BOTTOM_RIGHT: 
            return (int(p_rect.width() - w - m), int(p_rect.height() - h - m))
        return (0, 0)

    # =========================================================================
    # AUTO HIDE LOGIC (TIMERS + ANIMATION TRIGGERS)
    # =========================================================================

    def enterEvent(self, event: QEnterEvent) -> None:
        """Handle mouse enter."""
        super().enterEvent(event)
        self._timer_hide.stop()
        if self._is_minimized or self._anim_target_is_minimized:
            delay = self._config.get('hover_enter_delay', 150)
            self._timer_show.start(delay)

    def leaveEvent(self, event: QEvent) -> None:
        """Handle mouse leave."""
        super().leaveEvent(event)
        self._timer_show.stop()
        if self._is_dragging_widget:
            return
        if self._auto_hide_enabled:
            # Only minimize if we are currently expanded or expanding
            if not self._is_minimized and not self._anim_target_is_minimized:
                delay = self._config.get('hover_leave_delay', 400)
                self._timer_hide.start(delay)

    def _perform_expand(self):
        """Called by timer to start expansion animation."""
        # Stop any conflicting animation or timers
        self._timer_hide.stop()
        self._anim.stop()

        # 1. VISUAL STATE: We must be 'Expanded' logically to draw map contents while growing
        self._is_minimized = False 
        self._anim_target_is_minimized = False

        # 2. Setup Animation
        start_rect = QRectF(self.geometry())
        end_rect = self._calculate_geometry_rect(minimized=False)

        self._anim.setDuration(self._config.get('anim_duration', 100))
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(end_rect)
        self._anim.start()

    def _perform_minimize(self):
        """Called by timer to start minimize animation."""
        if not self._auto_hide_enabled or self.underMouse() or self._is_dragging_widget:
            return

        self._timer_show.stop()
        self._anim.stop()
        
        # 1. We keep _is_minimized = False so it scales down visibly. 
        # We only flip to True (Icon mode) when animation finishes.
        self._anim_target_is_minimized = True

        # 2. Setup Animation
        start_rect = QRectF(self.geometry())
        end_rect = self._calculate_geometry_rect(minimized=True)

        self._anim.setDuration(self._config.get('anim_duration', 100))
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(end_rect)
        self._anim.start()

    def _on_anim_value_changed(self, value):
        """Update geometry on every animation frame."""
        rect = value.toRect()
        self.setGeometry(rect)
        # Note: resizeEvent automatically calls fitInView because _is_minimized is False

    def _on_anim_finished(self):
        """Finalize state after animation."""
        if self._anim_target_is_minimized:
            self._is_minimized = True
            # Force update to redraw as Icon
            self.viewport().update()
        else:
            self._is_minimized = False
            # Ensure precise final geometry
            self.update_position()

    # =========================================================================
    # UI GEOMETRY
    # =========================================================================

    def _get_ui_button_rects(self) -> Dict[MinimapButton, QRectF]:
        """Calculate button rectangles."""
        if self._is_minimized:
            return {}

        vp_rect = self.viewport().rect()
        icon_size = self._config['icon_size']
        padding = self._config['icon_padding']
        spacing = self._config['icon_spacing']
        
        base_x = vp_rect.width() - padding
        base_y = padding
        
        rects = {}
        # Order requested: Pin -> Snap -> Reset -> Fit (Left to Right)
        # We calculate from Right to Left for easier anchor to edge.
        
        # 4. Fit (Rightmost)
        x_fit = base_x - icon_size
        rects[MinimapButton.FIT] = QRectF(x_fit, base_y, icon_size, icon_size)
        
        # 3. Reset (Left of Fit)
        x_reset = x_fit - icon_size - spacing
        rects[MinimapButton.RESET] = QRectF(x_reset, base_y, icon_size, icon_size)

        # 2. Snap (Left of Reset)
        x_snap = x_reset - icon_size - spacing
        rects[MinimapButton.SNAP] = QRectF(x_snap, base_y, icon_size, icon_size)

        # 1. Pin (Left of Snap)
        x_pin = x_snap - icon_size - spacing
        rects[MinimapButton.PIN] = QRectF(x_pin, base_y, icon_size, icon_size)
        
        return rects

    def _get_tooltip_text(self, btn_type: MinimapButton) -> str:
        """Get tooltip text for a button."""
        if btn_type == MinimapButton.FIT:
            return "Fit to Content"
        elif btn_type == MinimapButton.RESET:
            return "Reset Zoom (100%)"
        elif btn_type == MinimapButton.PIN:
            return "Unpin Minimap" if not self._auto_hide_enabled else "Pin Minimap"
        elif btn_type == MinimapButton.SNAP:
            is_snapping = False
            if hasattr(self._target_scene, 'snapping_enabled'):
                is_snapping = self._target_scene.snapping_enabled
            return "Disable Grid Snapping" if is_snapping else "Enable Grid Snapping"
        return ""

    # =========================================================================
    # RENDER PIPELINE
    # =========================================================================

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """Draw minimap background."""
        painter.save()
        painter.resetTransform()
        
        vp_rect = self.viewport().rect()
        bw = self._config['border_width']
        radius = self._config['corner_radius']
        adj = bw / 2
        
        bg_rect = QRectF(vp_rect).adjusted(adj, adj, -adj, -adj)
        path = QPainterPath()
        path.addRoundedRect(bg_rect, radius, radius)
        
        painter.setBrush(self._config['bg_color'])
        pen = QPen(self._config['border_color'], bw)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        
        painter.drawPath(path)
        painter.restore()

    def drawItems(self, painter: QPainter, numItems: int, items: List[QGraphicsItem], options: QStyleOptionGraphicsItem) -> None:
        """Draw items on minimap."""
        # OPTIMIZATION: Do not draw items if minimized (Icon Mode)
        if self._is_minimized:
            return

        current_scale = self.transform().m11()
        safe_scale = current_scale if current_scale > 0.001 else 1.0
        visual_radius = self._config['node_radius'] / safe_scale

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)

        default_color = self._config['node_color']

        for item in items:
            color = default_color
            if hasattr(item, '_custom_header_bg'): 
                color = item._custom_header_bg
            elif hasattr(item, 'color'): 
                color = item.color
            elif hasattr(item, 'brush'): 
                color = item.brush().color()
                
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(item.sceneBoundingRect(), visual_radius, visual_radius)

        painter.restore()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """Draw minimap foreground (lens, UI)."""
        painter.save()

        if self._is_minimized:
            # DRAW MINIMIZED ICON
            painter.resetTransform()
            self._draw_minimized_icon(painter)
        else:
            # DRAW LENS
            target_viewport = self._target_view.viewport().rect()
            
            # Map the viewport to the scene polygon
            view_polygon = self._target_view.mapToScene(target_viewport)
            
            # Convert polygon to bounding rect
            lens_rect = view_polygon.boundingRect()

            # --- DYNAMIC RADIUS CALCULATION ---
            minimap_scale = self.transform().m11()
            desired_px_radius = self._config.get('lens_corner_radius', 0.0)
            
            if minimap_scale > 0.0001:
                lens_radius_scene = desired_px_radius / minimap_scale
            else:
                lens_radius_scene = 0.0
            
            pen = QPen(self._config['lens_border_color'])
            pen.setWidthF(self._config['lens_border_width'])
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCosmetic(True)
            
            painter.setPen(pen)
            painter.setBrush(self._config['lens_fill_color'])
            
            painter.drawRoundedRect(lens_rect, lens_radius_scene, lens_radius_scene)

            # DRAW UI
            painter.resetTransform()
            self._draw_zoom_label(painter)
            self._draw_control_buttons(painter)

        painter.restore()

    def _draw_minimized_icon(self, painter: QPainter):
        """Draws a simple 'Map' icon when minimized."""
        vp_rect = self.viewport().rect()
        c = vp_rect.center()
        
        pen = QPen(self._config['text_color'], 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Draw a small grid/map symbol
        w = vp_rect.width() * 0.5
        h = vp_rect.height() * 0.5
        r = QRectF(c.x() - w/2, c.y() - h/2, w, h)
        
        painter.drawRoundedRect(r, 2, 2)
        
        # Dots inside
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._config['text_color'])
        dot_size = 3
        painter.drawEllipse(r.center(), dot_size/2, dot_size/2)
        painter.drawEllipse(QPointF(r.left() + 4, r.top() + 4), dot_size/2, dot_size/2)
        painter.drawEllipse(QPointF(r.right() - 4, r.bottom() - 4), dot_size/2, dot_size/2)

    def _draw_zoom_label(self, painter: QPainter):
        """Draw zoom percentage label."""
        zoom_val = int(self._target_view.transform().m11() * 100)
        text = f"{zoom_val}%"
        
        font = QFont(self._config['font_family'], self._config['font_size'])
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self._config['text_color'])
        
        vp_rect = self.viewport().rect()
        text_rect = painter.fontMetrics().boundingRect(text)
        
        # Bottom-Right
        painter.drawText(
            vp_rect.width() - text_rect.width() - 10, 
            vp_rect.height() - 10, 
            text
        )

    def _draw_control_buttons(self, painter: QPainter):
        """Draw control buttons."""
        rects = self._get_ui_button_rects()
        
        # Prepare standard UI pens
        pen_outline = QPen(self._config['border_color'], 1.0)
        
        # Prepare "default" symbol pen (painters may override this, but good to have set)
        pen_symbol = QPen(self._config['text_color'], self._config['icon_symbol_width'])
        pen_symbol.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen_symbol.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        
        # Check global states once
        is_snapping = False
        if hasattr(self._target_scene, 'snapping_enabled'):
            is_snapping = self._target_scene.snapping_enabled

        for btn_type, rect in rects.items():
            # 1. Draw Button Background (Hover Effect)
            painter.setPen(pen_outline)
            if self._hovered_button == btn_type:
                painter.setBrush(self._config['icon_hover_color'])
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            
            painter.drawRoundedRect(rect, 4, 4)

            # 2. Prepare for Icon Paint
            painter.save()
            
            # Set default drawing context for the icon
            painter.setPen(pen_symbol)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            # Determine specific state for the icon painter
            state = False
            if btn_type == MinimapButton.PIN:
                # IconPin expects: True = Pinned (Active Color), False = Unpinned (Slanted)
                # Logic: auto_hide_enabled=False -> Pinned
                state = not self._auto_hide_enabled
            elif btn_type == MinimapButton.SNAP:
                state = is_snapping

            # 3. Delegate to Icon Painter
            if btn_type in self._icon_painters:
                self._icon_painters[btn_type].paint(painter, rect, self._config, state)

            painter.restore()

    # =========================================================================
    # INTERACTION
    # =========================================================================

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move."""
        if self._is_minimized:
            return 

        pos = event.position()
        rects = self._get_ui_button_rects()
        
        prev_hover = self._hovered_button
        self._hovered_button = MinimapButton.NONE
        
        tooltip_shown = False

        for btn_type, rect in rects.items():
            if rect.contains(pos):
                self._hovered_button = btn_type
                # Show Tooltip
                QToolTip.showText(QCursor.pos(), self._get_tooltip_text(btn_type), self)
                tooltip_shown = True
                break
        
        if not tooltip_shown:
            QToolTip.hideText()

        if prev_hover != self._hovered_button:
            self.viewport().update()

        if self._is_dragging_widget:
            new_pos = event.globalPosition().toPoint() - self._drag_start_pos
            if self.parentWidget():
                parent_rect = self.parentWidget().rect()
                w, h = self._config['width'], self._config['height']
                x = max(0, min(new_pos.x(), parent_rect.width() - w))
                y = max(0, min(new_pos.y(), parent_rect.height() - h))
                self.move(x, y)
            event.accept()
        elif self._is_panning_view:
            self._update_main_view_from_minimap(pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press."""
        if self._is_minimized:
            # Click while minimized = force open
            self._timer_show.stop()
            self._perform_expand()
            return

        pos = event.position()
        rects = self._get_ui_button_rects()
        
        clicked_btn = MinimapButton.NONE
        for btn_type, rect in rects.items():
            if rect.contains(pos):
                clicked_btn = btn_type
                break
        
        if clicked_btn != MinimapButton.NONE:
            if clicked_btn == MinimapButton.RESET:
                self._action_reset_view()
            elif clicked_btn == MinimapButton.FIT:
                self._action_fit_view()
            elif clicked_btn == MinimapButton.PIN:
                self._action_toggle_autohide()
            elif clicked_btn == MinimapButton.SNAP:
                self._action_toggle_snapping()
            
            # Immediately update tooltip text after action (e.g., Snap Enable -> Disable)
            if clicked_btn in [MinimapButton.PIN, MinimapButton.SNAP]:
                 QToolTip.showText(QCursor.pos(), self._get_tooltip_text(clicked_btn), self)

            event.accept()
            return

        is_ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        is_left = event.button() == Qt.MouseButton.LeftButton

        if is_ctrl and is_left:
            self._is_dragging_widget = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.pos()
            self._create_snap_overlay()
            event.accept()
        elif is_left:
            self._is_panning_view = True
            self._update_main_view_from_minimap(pos)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_dragging_widget:
                self._is_dragging_widget = False
                self._remove_snap_overlay()
                self._snap_to_nearest_corner()
                
            self._is_panning_view = False
        super().mouseReleaseEvent(event)

    # =========================================================================
    # ACTIONS
    # =========================================================================

    def _action_toggle_autohide(self):
        """Toggles the auto-hide feature."""
        self._auto_hide_enabled = not self._auto_hide_enabled
        self.viewport().update()
        
        if self._auto_hide_enabled and not self.underMouse():
            self._timer_hide.start(self._config.get('hover_leave_delay', 400))

    def _action_toggle_snapping(self):
        """Toggles grid snapping on the target scene if supported."""
        if hasattr(self._target_scene, 'set_config') and hasattr(self._target_scene, 'snapping_enabled'):
            new_state = not self._target_scene.snapping_enabled
            self._target_scene.set_config(snapping_enabled=new_state)
            self.viewport().update()
        else:
            log.info("Target scene does not support snapping configuration.")

    def _action_reset_view(self):
        """Reset view to default zoom."""
        if hasattr(self._target_view, "reset_zoom"):
            self._target_view.reset_zoom()
        else:
            self._target_view.resetTransform()
            if self._target_scene.itemsBoundingRect().isEmpty():
                self._target_view.centerOn(0, 0)
            else:
                self._target_view.centerOn(self._target_scene.itemsBoundingRect().center())
        self.viewport().update()

    def _action_fit_view(self):
        """Fit view to content."""
        if hasattr(self._target_view, "fit_content"):
            self._target_view.fit_content()
        else:
            scene_rect = self._target_scene.itemsBoundingRect()
            if scene_rect.width() < 1 or scene_rect.height() < 1:
                 self._action_reset_view()
                 return
            target_rect = scene_rect.adjusted(-50, -50, 50, 50)
            self._target_view.fitInView(target_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.viewport().update()

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _snap_to_nearest_corner(self):
        """Snap to nearest corner after dragging."""
        if not self.parentWidget(): 
            return
        parent_rect = self.parentWidget().rect()
        current_pos = self.pos()
        
        # Always calculate snap based on MAXIMIZED size
        w = self._config['width']
        h = self._config['height']
        m = self._config['margin']

        corners = [
            (MinimapCorner.TOP_LEFT, self._get_coords_for_corner(MinimapCorner.TOP_LEFT, parent_rect, w, h, m)),
            (MinimapCorner.TOP_RIGHT, self._get_coords_for_corner(MinimapCorner.TOP_RIGHT, parent_rect, w, h, m)),
            (MinimapCorner.BOTTOM_LEFT, self._get_coords_for_corner(MinimapCorner.BOTTOM_LEFT, parent_rect, w, h, m)),
            (MinimapCorner.BOTTOM_RIGHT, self._get_coords_for_corner(MinimapCorner.BOTTOM_RIGHT, parent_rect, w, h, m)),
        ]
        
        best_corner = min(corners, key=lambda x: (current_pos.x()-x[1][0])**2 + (current_pos.y()-x[1][1])**2)[0]
        self._current_corner = best_corner
        self.update_position()

    def _update_main_view_from_minimap(self, pos: QPointF) -> None:
        """Update main view center from minimap click."""
        scene_pos = self.mapToScene(pos.toPoint())
        self._target_view.centerOn(scene_pos)

    def _create_snap_overlay(self):
        """Create snap overlay for drag feedback."""
        if not self.parentWidget(): 
            return
        self._overlay = SnapOverlay(self.parentWidget(), self._style_manager)
        self._overlay.resize(self.parentWidget().size())
        self._overlay.show()
        self.raise_() 

    def _remove_snap_overlay(self):
        """Remove snap overlay."""
        if hasattr(self, '_overlay') and self._overlay:
            self._overlay.close()
            self._overlay = None

    def cleanup(self):
        """Cleanup method to unregister from StyleManager."""
        self._style_manager.unregister(self, StyleCategory.MINIMAP)


# =============================================================================
# HELPER WIDGET FOR VISUAL FEEDBACK
# =============================================================================

class SnapOverlay(QWidget):
    """Visual overlay showing snap positions during minimap drag."""
    
    def __init__(self, parent, style_manager: StyleManager):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._style_manager = style_manager
        self._config = self._style_manager.get_all(StyleCategory.MINIMAP)

    def paintEvent(self, event):
        """Paint snap overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        c = self._config.get('snap_color', QColor(128, 128, 128, 128))
        width_pen = self._config.get('snap_width', 1.5)

        pen = QPen(c)
        pen.setWidthF(width_pen)
        pen.setStyle(Qt.PenStyle.DashLine) 
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = self.rect()
        w = self._config.get('width', 200)
        h = self._config.get('height', 150)
        m = self._config.get('margin', 20)
        corner_radius = self._config.get('corner_radius', 10)

        # Draw rects at 4 corners
        # Top-Left
        painter.drawRoundedRect(m, m, w, h, corner_radius, corner_radius)
        # Top-Right
        painter.drawRoundedRect(rect.width() - w - m, m, w, h, corner_radius, corner_radius)
        # Bottom-Left
        painter.drawRoundedRect(m, rect.height() - h - m, w, h, corner_radius, corner_radius)
        # Bottom-Right
        painter.drawRoundedRect(rect.width() - w - m, rect.height() - h - m, w, h, corner_radius, corner_radius)


# =============================================================================
# BACKWARD COMPATIBILITY - Deprecated global config dict
# =============================================================================
# For code that still references DEFAULT_MINIMAP_CONFIG, provide a proxy
# that redirects to StyleManager. This can be removed once all code is migrated.

class _MinimapConfigProxy:
    """
    Backward compatibility proxy for DEFAULT_MINIMAP_CONFIG.
    Redirects all access to StyleManager.
    """
    def __getitem__(self, key: str) -> Any:
        return StyleManager.instance().get(StyleCategory.MINIMAP, key)
    
    def __setitem__(self, key: str, value: Any) -> None:
        StyleManager.instance().update(StyleCategory.MINIMAP, **{key: value})
    
    def get(self, key: str, default: Any = None) -> Any:
        return StyleManager.instance().get(StyleCategory.MINIMAP, key, default)
    
    def update(self, new_config: Dict[str, Any]) -> None:
        StyleManager.instance().update(StyleCategory.MINIMAP, **new_config)
    
    def copy(self) -> Dict[str, Any]:
        return StyleManager.instance().get_all(StyleCategory.MINIMAP)
    
    def __contains__(self, key: str) -> bool:
        return key in StyleManager.instance().get_all(StyleCategory.MINIMAP)
    
    def __repr__(self) -> str:
        return f"_MinimapConfigProxy -> StyleManager.MINIMAP"


# Backward compatibility: DEFAULT_MINIMAP_CONFIG now proxies to StyleManager
DEFAULT_MINIMAP_CONFIG = _MinimapConfigProxy()