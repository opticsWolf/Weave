# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

import math
from enum import Enum
from typing import Optional, Callable, TYPE_CHECKING
from PySide6.QtWidgets import (
    QGraphicsItem, QWidget,
    QGraphicsTextItem, QStyleOptionGraphicsItem, QGraphicsObject,
    QGraphicsSceneHoverEvent, QGraphicsSceneMouseEvent
)
from PySide6.QtCore import Qt, QRectF, QPointF, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QFont, QTextCursor, 
    QPainterPathStroker, QBrush
)

# Forward declaration (as in original)
if TYPE_CHECKING:
    from weave.node.node_core import Node

# ==============================================================================
# 1. HELPER FUNCTIONS
# ==============================================================================

def highlight_colors(color: QColor, b_offset: int, s_offset: int = 0) -> QColor:
    """
    Adjusts the brightness and saturation of a QColor.
    All values cast to int for PySide6 C++ signature safety.
    """
    h, s, l, a = color.getHsl()
    l = int(max(0, min(255, l + b_offset)))
    s = int(max(0, min(255, s + s_offset)))
    return QColor.fromHsl(int(h), s, l, int(a))

# ==============================================================================
# 2. COMPONENTS 
# ==============================================================================

class NodeState(Enum):
    NORMAL = 0
    PASSTHROUGH = 1
    DISABLED = 2
    COMPUTING = 3

# ==============================================================================
# StateSlider - Interactive State Toggle (Visual Component)
# ==============================================================================

class StateSlider(QGraphicsObject):
    """
    A sliding toggle switch that visually represents NodeState.
    
    This is a VISUAL-ONLY component. It does NOT handle mouse clicks directly.
    Click handling is done by the canvas state machine (IdleState) which detects
    clicks on the slider's bounding rect and calls the node's cycle_state() method.
    The node then calls header.sync_state_slider() to update this visual.
    
    States: Normal (Green/Left) -> Passthrough (Orange/Center) -> Disabled (Red/Right)
    """

    def __init__(self, parent: 'NodeHeader'):
        super().__init__(parent)
        self._node_header = parent
        # Access the node's configuration for slider properties
        self._node = parent._node
        
        # Internal state
        self._anim_value = 0.0
        self._current_state = NodeState.NORMAL
        self._hovered = False
        
        # Shared animation instance
        self._animation = QPropertyAnimation(self, b"anim_value", self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuad)

        # Initialize config values
        self.update_config()

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def update_config(self) -> None:
        """
        Refreshes slider properties from the node's configuration.
        Must be called whenever the config changes to update visuals.
        """
        cfg = self._node._config
        # Guard: only notify scene when actually in one, to prevent
        # BSP tree corruption / heap corruption via null scene pointer.
        if self.scene():
            self.prepareGeometryChange()
        
        # 1. Geometry properties
        base_h = cfg['state_slider_height']
        self._padding = cfg['state_slider_padding'] 
        self._height = base_h + (2 * self._padding)
        self._width = base_h * cfg['state_slider_width_ratio']
        
        # 2. Animation properties
        self._animation_duration = cfg['state_slider_animation_duration']
        self._animation.setDuration(self._animation_duration)
        
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._width, self._height)
    
    def get_scene_rect(self) -> QRectF:
        """Returns the bounding rect in scene coordinates for hit testing."""
        return self.mapRectToScene(self.boundingRect())

    # --- Animation Property ---
    def _get_anim_value(self) -> float:
        return self._anim_value

    def _set_anim_value(self, val: float):
        self._anim_value = val
        self.update()

    anim_value = Property(float, _get_anim_value, _set_anim_value)

    # --- State Synchronization (called by NodeHeader/Node) ---
    def sync_to_state(self, state: NodeState, animate: bool = True):
        """
        Synchronizes the slider position to match the given NodeState.
        Called by NodeHeader when the node's state changes via canvas state machine.
        """
        if state == self._current_state:
            return
            
        self._current_state = state
        
        end_val = 0.0
        if state == NodeState.PASSTHROUGH:
            end_val = 1.0
        elif state == NodeState.DISABLED:
            end_val = 2.0
            
        if animate and self._animation_duration > 0:
            self._animation.stop()
            self._animation.setStartValue(self._anim_value)
            self._animation.setEndValue(end_val)
            self._animation.start()
        else:
            self._anim_value = end_val
            self.update()

    # ------------------------------------------------------------------
    # Color Interpolation
    # ------------------------------------------------------------------

    def _interpolate_color(self) -> QColor:
        """
        Interpolates between icon_colors based on anim_value.

        Reads state_visuals through StyleManager.get() which performs
        read-time conversion of list colors → QColor.  Uses STRING keys
        ('NORMAL', 'PASSTHROUGH', 'DISABLED') matching core_theme.BASE_DEFAULTS.
        """
        
        v = self._anim_value
        cfg = self._node_header._node._config
        visuals = cfg['state_visuals']

        # Resolve the three relevant icon_colors (already QColor from read-time conversion)
        normal_ic    = visuals.get('NORMAL',      {}).get('icon_color', QColor(100, 220, 100))
        passthru_ic  = visuals.get('PASSTHROUGH',  {}).get('icon_color', QColor(255, 180, 50))
        disabled_ic  = visuals.get('DISABLED',     {}).get('icon_color', QColor(220, 60, 60))

        if v <= 1.0:
            ratio = v
            c1, c2 = normal_ic, passthru_ic
        else:
            ratio = v - 1.0
            c1, c2 = passthru_ic, disabled_ic

        r = c1.red()   + (c2.red()   - c1.red())   * ratio
        g = c1.green() + (c2.green() - c1.green()) * ratio
        b = c1.blue()  + (c2.blue()  - c1.blue())  * ratio
        return QColor(int(r), int(g), int(b))

    # --- Hover Feedback (visual only) ---
    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent):
        self._hovered = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent):
        self._hovered = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    # --- Rendering ---
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cfg = self._node_header._node._config
        
        h = self._height
        w = self._width
        knob_d = h - (self._padding * 2)
        
        # 1. Draw Track (Pill-shaped)
        track_rect = QRectF(0, 0, w, h)
        radius = h / 2.0
        
        # Use header outline color and body background color
        outline_color = self._node_header._outline_color 
        body_bg = cfg['body_bg']
        
        # Apply hover highlight to background and knob only (not the edge)
        is_hover_hl = self._hovered and cfg['state_slider_highlight_on_hover']
        if is_hover_hl:
            shift = cfg['state_slider_highlight_color_shift']
            body_bg = highlight_colors(body_bg, shift/2)
        
        painter.setPen(QPen(outline_color, 1.5))
        painter.setBrush(QBrush(body_bg)) # Now uses body background
        painter.drawRoundedRect(track_rect, radius, radius)
        
        # 2. Calculate Knob Position
        min_x = self._padding
        max_x = w - self._padding - knob_d
        norm_pos = self._anim_value / 2.0 
        current_x = min_x + (max_x - min_x) * norm_pos
        
        knob_rect = QRectF(current_x, self._padding, knob_d, knob_d)
        
        # 3. Draw Knob (highlight knob color on hover)
        knob_color = self._interpolate_color()
        if is_hover_hl:
            knob_color = highlight_colors(knob_color, shift*2)
        painter.setBrush(knob_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(knob_rect)
 

class MinimizeButton(QGraphicsObject):
    """
    A visual minimize button for nodes with hover states and different appearances 
    based on node's minimized state.
    
    The button changes appearance when:
    - Hovered (different color)
    - When the node is minimized vs expanded
    """
    
    def __init__(self, parent: 'NodeHeader'):
        super().__init__(parent)
        self._header = parent
        self._node = parent._node
        
        # State tracking
        self._hovered = False
        self._is_minimized = False
        
        # Geometry setup - will be calculated by parent during layout
        self._rect = QRectF()
        
        # Configure initial state
        self.update_config()
        self.setAcceptHoverEvents(True)
        # CRITICAL: Don't accept mouse buttons - let clicks pass through to canvas state machine
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(5)  # Should appear above other elements but below text

    def update_config(self) -> None:
        """
        Refreshes button properties from the node's configuration.
        Must be called whenever config changes to update visuals.
        """
        cfg = self._node._config
        if self.scene():
            self.prepareGeometryChange()
        
        # Load settings for button appearance
        btn_size = cfg['minimize_btn_size']
        self._btn_radius = btn_size / 2.0
        
        # Colors based on configuration - no fallback needed now that schema provides all defaults
        self._normal_color = cfg['minimize_btn_normal_color']
        self._hover_color = cfg['minimize_btn_hover_color']
        self._minimized_color = cfg['minimize_btn_minimized_color']
        
        # Additional configuration options
        self._border_width = cfg['minimize_btn_border_width']
        self._border_color = cfg['minimize_btn_border_color']
        
        # Update rect based on current size
        if not self._rect.isEmpty():
            center = self._rect.center()
            self._rect = QRectF(
                center.x() - self._btn_radius,
                center.y() - self._btn_radius,
                self._btn_radius * 2,
                self._btn_radius * 2
            )
        
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect

    def shape(self) -> QPainterPath:
        """Return a circle path for accurate hit-testing."""
        path = QPainterPath()
        center = self._rect.center()
        path.addEllipse(center.x() - self._btn_radius, 
                       center.y() - self._btn_radius,
                       self._btn_radius * 2, 
                       self._btn_radius * 2)
        return path

    def set_rect(self, rect: QRectF) -> None:
        """Set the button's geometry rectangle."""
        if self._rect != rect:
            if self.scene():
                self.prepareGeometryChange()
            self._rect = rect
            # Keep the center of circle at same position (if we want to maintain aspect ratio)
            self.update()

    def sync_to_minimized_state(self, is_minimized: bool) -> None:
        """
        Syncs button appearance with node's minimized state.
        
        Args:
            is_minimized: True if node is currently minimized
        """
        if self._is_minimized != is_minimized:
            self._is_minimized = is_minimized
            self.update()

    def hoverEnterEvent(self, event) -> None:
        """Handle mouse enter event for hover feedback."""
        self._hovered = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        """Handle mouse leave event to reset hover state."""
        self._hovered = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        """
        Paint the minimize button with appropriate appearance based on current state.
        
        Draws a circle that changes color based on:
        - Hover state (mouse over)
        - Minimized state (node's state)
        """
        if self._rect.isEmpty():
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Determine button color
        color = self._normal_color
        
        if self._hovered:
            color = self._hover_color
        elif self._is_minimized:
            color = self._minimized_color

        # Draw the circle with appropriate color and border
        painter.setBrush(color)
        
        # Handle border drawing
        if self._border_width > 0 and not self._border_color.alpha() == 0:
            painter.setPen(QPen(self._border_color, self._border_width))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            
        center = self._rect.center()
        painter.drawEllipse(
            center.x() - self._btn_radius,
            center.y() - self._btn_radius,
            self._btn_radius * 2,
            self._btn_radius * 2
        )


class EditableTitle(QGraphicsTextItem):
    """
    Title item that handles editing and syncing back to the node.
    """
    __slots__ = ('_header',)
    
    def __init__(self, text: str, parent: 'NodeHeader'):
        super().__init__(text, parent)
        self._header = parent 
        self._apply_font(is_selected=False)
        self._lock_interaction()
        
    def set_color(self, color: QColor) -> None:
        self.setDefaultTextColor(color)
        
    def update_selection_style(self, is_selected: bool) -> None:
        self._apply_font(is_selected)
        
    def _apply_font(self, is_selected: bool) -> None:
        cfg = self._header._node._config
        font = QFont(cfg['font_family'], cfg['font_size'])
        if is_selected:
            font.setWeight(cfg['sel_font_weight'])
            font.setItalic(cfg['sel_font_italic'])
        else:
            font.setWeight(cfg['font_weight'])
            font.setItalic(cfg['font_italic'])
        self.setFont(font)

    def _lock_interaction(self) -> None:
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

    def unlock_interaction(self) -> None:
        # Restore full text before editing
        full_text = ""
        # 1. Try Node Name
        if hasattr(self._header._node, 'name'):
            val = self._header._node.name
            full_text = val() if callable(val) else str(val)
        
        # 2. Fallback to ToolTip
        if not full_text:
            full_text = self.toolTip()
            
        # 3. Apply
        if full_text and full_text != self.toPlainText():
            self.setPlainText(full_text)

        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)

    def focusOutEvent(self, event) -> None:
        """
        Called when user clicks away. 
        Saves the text to the Node and re-locks.
        """
        self._lock_interaction()
        
        # 1. Clean the text (remove newlines from Enter key)
        new_name = self.toPlainText().strip()
        
        # 2. CRITICAL: Update ToolTip IMMEDIATELY.
        # This ensures that if the Node update fails (or hasn't happened yet),
        # _recalculate_layout will read this NEW value instead of the old one.
        self.setToolTip(new_name)
        
        # 3. Commit to Source of Truth (The Node)
        # Handle 'set_name' method (standard) or 'name' property
        node = self._header._node
        if hasattr(node, 'set_name') and callable(node.set_name):
            node.set_name(new_name)
        elif hasattr(node, 'name'):
            # If it's a property/attribute, set it directly
            node.name = new_name
            
        super().focusOutEvent(event)
        
        # 4. Trigger Layout Update
        if hasattr(node, 'enforce_min_dimensions'):
            node.enforce_min_dimensions()
        
        self._header._recalculate_layout()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clearFocus()  # This triggers focusOutEvent
            return
        super().keyPressEvent(event)
        
class ResizeHandle(QGraphicsItem):
    """
    Handle for resizing the node - unchanged.
    """
    __slots__ = (
        '_node', '_callback', '_hovered', '_resizing',
        '_drag_start_screen_pos', '_drag_start_size', '_path', '_rect'
    )

    def __init__(self, parent: 'Node', callback: Callable[[float, float, bool], None]):
        super().__init__(parent)
        self._node = parent
        self._callback = callback
        self._hovered = False
        self._resizing = False
        self._drag_start_screen_pos = QPointF()
        self._drag_start_size = (0.0, 0.0)
        
        self._path = QPainterPath()
        self._rect = QRectF()
        self._recalculate_geometry()
        
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

    def _recalculate_geometry(self) -> None:
        cfg = self._node._config
        shift = cfg['resize_handle_offset'] / math.sqrt(2)
        r = cfg['resize_handle_radius']
        
        self._path = QPainterPath()
        vis_rect = QRectF(shift - r, shift - r, 2*r, 2*r)
        self._path.arcMoveTo(vis_rect, 0)
        self._path.arcTo(vis_rect, 0, -90)
        
        margin = cfg['resize_handle_hover_width']
        total_r = r + margin
        self._rect = QRectF(shift - total_r, shift - total_r, 2*total_r, 2*total_r)

    def boundingRect(self) -> QRectF:
        return self._rect

    def shape(self) -> QPainterPath:
        """Return a path based on the arc for more accurate hit-testing."""
        stroker = QPainterPathStroker()
        cfg = self._node._config
        stroker.setWidth(cfg['resize_handle_hover_width'] * 2)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        return stroker.createStroke(self._path)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        cfg = self._node._config
        
        if self._hovered or self._resizing:
            color = cfg['resize_handle_hover_color']
            width = cfg['resize_handle_hover_width']
        else:
            color = cfg['resize_handle_color']
            width = cfg['resize_handle_width']
            
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)

    def hoverEnterEvent(self, event: 'QGraphicsSceneHoverEvent') -> None:
        self._hovered = True
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: 'QGraphicsSceneHoverEvent') -> None:
        self._hovered = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._resizing = True
            self._drag_start_screen_pos = event.screenPos()
            if self._node:
                self._drag_start_size = (self._node._width, self._node._total_height)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resizing:
            delta = event.screenPos() - self._drag_start_screen_pos
            base_w, base_h = self._drag_start_size
            target_w = base_w + delta.x()
            target_h = base_h + delta.y()
            is_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._callback(target_w, target_h, is_ctrl)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._resizing = False
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)
