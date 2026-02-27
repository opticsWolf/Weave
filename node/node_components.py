# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Refactored NodeBody and NodeHeader - Removed redundant mouse interaction 
and context menu handling now managed at the canvas level.

Includes StateSlider: An interactive toggle switch for node state.
"""
import math
from typing import Optional, Callable, Tuple, TYPE_CHECKING
#from enum import Enum
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QGraphicsTextItem, QStyleOptionGraphicsItem, QGraphicsObject,
    QGraphicsSceneHoverEvent, QGraphicsSceneMouseEvent
)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QFont, QTextCursor, QFontMetrics,
    QLinearGradient, QPainterPathStroker, QBrush
)

from weave.node.node_subcomponents import NodeState, StateSlider, MinimizeButton, EditableTitle, ResizeHandle, highlight_colors

# Forward declaration (as in original)
if TYPE_CHECKING:
    from weave.node.node_core import Node

# ==============================================================================
# 1. HELPER FUNCTIONS
# ==============================================================================

# highlight_colors is imported from qt_nodesubcomponents to avoid circular imports

def shift_color(color: QColor, shift: int) -> QColor:
    """
    Shifts Saturation, Lightness, and Hue by a single value.
    All values cast to int to prevent PySide6 C++ signature mismatch
    with QColor.fromHsl when shift/4 produces a float.
    
    Args:
        color (QColor): The base color to shift
        shift (int): Amount to shift HSL channels
        
    Returns:
        QColor: A new color with shifted values
    """
    h, s, l, a = color.getHsl()
    s = int(max(0, min(255, s + shift)))
    l = int(max(0, min(255, l + shift)))
    if h != -1:
        h = int((h + shift / 4) % 360)
    return QColor.fromHsl(h, s, l, a)

def create_angled_gradient(rect: QRectF, angle_deg: float, color_start: QColor, color_end: QColor) -> QLinearGradient:
    """
    Creates a QLinearGradient angled across a rectangle.
    
    Args:
        rect (QRectF): The bounding rectangle for the gradient
        angle_deg (float): Angle in degrees
        color_start (QColor): Start color of the gradient  
        color_end (QColor): End color of the gradient
        
    Returns:
        QLinearGradient: Configured linear gradient object
    """
    angle_rad = math.radians(angle_deg % 360)
    cx, cy = rect.center().x(), rect.center().y()
    r = math.sqrt(rect.width()**2 + rect.height()**2) / 2
    dx = r * math.cos(angle_rad)
    dy = r * math.sin(angle_rad)
    
    start_point = QPointF(cx - dx, cy - dy)
    end_point = QPointF(cx + dx, cy + dy)
    
    gradient = QLinearGradient(start_point, end_point)
    gradient.setColorAt(0.0, color_start)
    gradient.setColorAt(1.0, color_end)
    return gradient

# ==============================================================================
# 2. COMPONENTS 
# ==============================================================================

class NodeHeader(QGraphicsItem):
    """
    Refactored header component with removed context menu handling.
    
    Includes StateSlider for interactive state toggling and MinimizeButton
    for visual minimize/maximize feedback.
    
    Removed: 
    - contextMenuEvent (now handled at Canvas level)
    - mousePressEvent (selection logic now via standard flags)
    """
    
    __slots__ = (
        '_node', '_width', '_title', '_bg_color', '_outline_color',
        '_height', '_minimize_btn', '_state_slider',
        '_shape_path', '_outline_path', '_bottom_line_path'
    )

    def __init__(self, parent: 'Node', title_text: str):
        """
        Initialize NodeHeader with a parent node.
        
        Args:
            parent (Node): The owning node
            title_text (str): Initial title text
        """
        super().__init__(parent)
        self._node = parent
        self._width = parent._config['width']
        
        self._shape_path = QPainterPath()
        self._outline_path = QPainterPath()
        self._bottom_line_path = QPainterPath()
        
        self.setAcceptHoverEvents(True)
        self._title = EditableTitle(title_text, self)
        
        # Use the node's already-configured colors
        self._bg_color = parent._config['header_bg']
        self._outline_color = parent._config['outline_color']
        
        self._height = parent._config['header_height']
        
        # Create the MinimizeButton - visual component with hover states
        self._minimize_btn = MinimizeButton(self)
        
        # Create the StateSlider - No longer passing height directly to constructor
        self._state_slider = StateSlider(self)
        self._recalculate_layout()

    def get_height(self) -> float:
        """Get the header's current height."""
        return self._height

    def get_title_width(self) -> float:
        """Get the width of the title text."""
        return self._title.boundingRect().width()

    def set_colors(self, bg: QColor, outline: QColor, text: QColor) -> None:
        """
        Set all header colors at once.
        
        Args:
            bg (QColor): Background color
            outline (QColor): Outline color  
            text (QColor): Text color
        """
        self._bg_color = bg
        self._outline_color = outline
        self._title.set_color(text)
        # Update slider since it uses our outline color
        if hasattr(self, '_state_slider'):
            self._state_slider.update()
        # Update minimize button
        if hasattr(self, '_minimize_btn'):
            self._minimize_btn.update()
        self.update()

    def sync_state_slider(self, state: NodeState, animate: bool = True) -> None:
        """
        Synchronizes the StateSlider to reflect the current node state.
        Called by Node when state changes.
        
        Args:
            state (NodeState): Current node state
            animate (bool): Whether to animate the change
        """
        if hasattr(self, '_state_slider'):
            self._state_slider.sync_to_state(state, animate)

    def get_state_slider_rect(self) -> QRectF:
        """
        Returns the StateSlider's bounding rect in NODE-LOCAL coordinates.
        Used by canvas state machine for hit testing.
        
        Returns:
            QRectF: Slider rectangle
        """
        if hasattr(self, '_state_slider'):
            # Get slider rect in header coords, then map to node coords
            slider_rect = self._state_slider.boundingRect()
            slider_pos = self._state_slider.pos()
            return QRectF(slider_pos.x(), slider_pos.y(), 
                         slider_rect.width(), slider_rect.height())
        return QRectF()

    def sync_minimize_button(self, is_minimized: bool) -> None:
        """
        Synchronizes the MinimizeButton appearance with the node's minimized state.
        Called by Node when minimize state changes.
        
        Args:
            is_minimized (bool): Whether node is currently minimized
        """
        if hasattr(self, '_minimize_btn'):
            self._minimize_btn.sync_to_minimized_state(is_minimized)

    def get_minimize_btn_rect(self) -> QRectF:
        """
        Returns the MinimizeButton's bounding rect in NODE-LOCAL coordinates.
        Used by canvas state machine for hit testing.
        
        Returns:
            QRectF: Button rectangle
        """
        if hasattr(self, '_minimize_btn'):
            btn_rect = self._minimize_btn.boundingRect()
            btn_pos = self._minimize_btn.pos()
            return QRectF(btn_pos.x(), btn_pos.y(),
                         btn_rect.width(), btn_rect.height())
        return QRectF()

    # Removed hoverMoveEvent/hoverLeaveEvent - now handled at canvas level 
    # with global mouse move tracking for better performance

    def update_selection_style(self, is_selected: bool):
        """Update title style based on selection state."""
        self._title.update_selection_style(is_selected)
        self._recalculate_layout()

    def _recalculate_layout(self):
        """
        Recalculates layout using a unidirectional dependency chain:
        Button (Fixed) <- Slider (Dependent) <- Title (Dependent & Elided).
        
        Strictly prevents text wrapping (multiline) by eliding text 
        that exceeds the available horizontal space.
        """
        # prepareGeometryChange() MUST be called BEFORE any attribute that
        # affects boundingRect (_height, _width) is modified. Only call when
        # in a scene to avoid PySide6 null-deref on scene-less items.
        if self.scene():
            self.prepareGeometryChange()

        cfg = self._node._config
        
        # Update sub-component configs before layout positioning
        if hasattr(self, '_state_slider'):
            self._state_slider.update_config()
        if hasattr(self, '_minimize_btn'):
            self._minimize_btn.update_config()
    
        # --- 1. Load Configuration ---
        left_padding = cfg.get('header_left_padding', 'header_h_padding')
        right_padding = cfg.get('header_right_padding', 'header_h_padding')
        title_slider_spacing = cfg.get('header_title_slider_spacing', 'header_item_spacing')
        slider_minimize_spacing = cfg.get('header_slider_minimize_spacing', 'header_item_spacing')
    
        # --- 2. Calculate Vertical Geometry ---
        font = self._title.font()
        fm = QFontMetrics(font)
        req_height = fm.height() + (cfg['header_v_padding'] * 2)
        self._height = max(cfg['header_height'], req_height)
        
        # Vertical centering
        slider_bounds = self._state_slider.boundingRect()
        slider_y = (self._height - slider_bounds.height()) / 2
        
        btn_size = cfg['minimize_btn_size']
        btn_y = (self._height - btn_size) / 2
        
        title_y = (self._height - self._title.boundingRect().height()) / 2
    
        # --- 3. Position Button (Primary Anchor) ---
        btn_x = self._width - right_padding - btn_size
        btn_rect = QRectF(0, 0, btn_size, btn_size)
        
        if hasattr(self, '_minimize_btn'):
            self._minimize_btn.set_rect(btn_rect)
            self._minimize_btn.setPos(btn_x, btn_y)
    
        # --- 4. Position Slider (Button-Anchored) ---
        slider_x = btn_x - slider_minimize_spacing - slider_bounds.width()
        slider_x = max(0.0, slider_x)
        self._state_slider.setPos(slider_x, slider_y)
    
        # --- 5. Position Title ---
        title_x = left_padding
        max_title_width = slider_x - title_slider_spacing - left_padding
        max_title_width = max(0.0, max_title_width)
    
        full_text = ""
        if hasattr(self._node, 'name'):
            val = self._node.name
            full_text = val() if callable(val) else str(val)
        if not full_text:
            full_text = self._title.toolTip()
        if not full_text:
            full_text = self._title.toPlainText()
    
        elided_text = fm.elidedText(full_text, Qt.ElideRight, int(max_title_width))
        if self._title.toPlainText() != elided_text:
            self._title.setPlainText(elided_text)
        if self._title.toolTip() != full_text:
            self._title.setToolTip(full_text)
        self._title.setPos(title_x, title_y)
    
        # --- 6. Finalize ---
        self._build_shape_path()

    def _build_shape_path(self):
        cfg = self._node._config
        r = cfg['radius']
        w = self._width
        h = self._height
        is_minimized = self._node.is_minimized
        border_w = cfg['border_width']
        
        stroke_inset = border_w / 2.0
        effective_r = min(r, h / 2) if is_minimized else r
        
        fill_path = QPainterPath()
        fill_path.moveTo(0, h if not is_minimized else h - effective_r)
        fill_path.lineTo(0, effective_r)
        fill_path.arcTo(0, 0, 2*effective_r, 2*effective_r, 180, -90)
        fill_path.lineTo(w - effective_r, 0)
        fill_path.arcTo(w - 2*effective_r, 0, 2*effective_r, 2*effective_r, 90, -90)

        if is_minimized:
            fill_path.lineTo(w, h - effective_r)
            fill_path.arcTo(w - 2*effective_r, h - 2*effective_r, 2*effective_r, 2*effective_r, 0, -90)
            fill_path.lineTo(effective_r, h)
            fill_path.arcTo(0, h - 2*effective_r, 2*effective_r, 2*effective_r, 270, -90)
            fill_path.closeSubpath()
            self._outline_path = fill_path
            self._bottom_line_path = QPainterPath() 
        else:
            fill_path.lineTo(w, h)
            fill_path.lineTo(0, h)
            fill_path.closeSubpath()
            
            bottom_line_active = cfg.get('header_bottom_line_enabled', False)
            outline_path = QPainterPath()
            outline_path.moveTo(0, h) 
            outline_path.lineTo(0, effective_r)
            outline_path.arcTo(0, 0, 2*effective_r, 2*effective_r, 180, -90)
            outline_path.lineTo(w - effective_r, 0)
            outline_path.arcTo(w - 2*effective_r, 0, 2*effective_r, 2*effective_r, 90, -90)
            outline_path.lineTo(w, h)
            
            if not bottom_line_active:
                outline_path.lineTo(0, h)
                outline_path.closeSubpath()
                self._bottom_line_path = QPainterPath()
            else:
                line_path = QPainterPath()
                line_path.moveTo(stroke_inset, h)
                line_path.lineTo(w - stroke_inset, h)
                self._bottom_line_path = line_path

            self._outline_path = outline_path

        self._shape_path = fill_path

    def set_width(self, width: float) -> None:
        """
        Set the header's width and update layout.
        
        Args:
            width (float): New width
        """
        if self._width != width:
            if self.scene():
                self.prepareGeometryChange()
            self._width = width
            self._recalculate_layout()
            self.update()

    def boundingRect(self) -> QRectF:
        """Get the header's bounding rectangle."""
        return QRectF(0, 0, self._width, self._height)

    def shape(self) -> QPainterPath:
        """Return the shape path for accurate hit-testing with rounded corners."""
        return self._shape_path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        """
        Paint the header component.
        
        Args:
            painter (QPainter): The painter
            option (QStyleOptionGraphicsItem): Style options  
            widget (QWidget, optional): Widget being painted on
        """
        cfg = self._node._config

        if cfg.get('header_gradient_enabled', False):
            shift = cfg['header_gradient_shift']
            angle = cfg['header_gradient_angle']
            col_start = self._bg_color
            col_end = shift_color(col_start, shift)
            grad = create_angled_gradient(self.boundingRect(), angle, col_start, col_end)
            painter.setBrush(grad)
        else:
            painter.setBrush(self._bg_color)
            
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(self._shape_path)

        pen = QPen(self._outline_color, cfg['border_width'])
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if cfg.get('header_bottom_line_enabled', False) and not self._node.is_minimized:
             pen.setCapStyle(Qt.PenCapStyle.FlatCap) 

        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._outline_path)
        
        if not self._bottom_line_path.isEmpty() and cfg.get('header_bottom_line_enabled', False):
            line_shift = cfg['header_bottom_line_shift']
            line_color = self._bg_color
            line_color = highlight_colors(line_color, line_shift, line_shift)
            
            line_pen = QPen(line_color, cfg['header_bottom_line_width'])
            line_pen.setCapStyle(Qt.PenCapStyle.FlatCap) 
            painter.setPen(line_pen)
            painter.drawPath(self._bottom_line_path)

        # Note: MinimizeButton and StateSlider are QGraphicsObject children and paint themselves


class NodeBody(QGraphicsItem):
    """
    Refactored body component with removed context menu handling.
    
    Removed: 
    - contextMenuEvent (now handled at Canvas level)
    - mousePressEvent / mouseMoveEvent (selection/hover now via standard flags)
    """
    
    __slots__ = (
        '_node', '_width', '_height', '_bg_color', '_outline_color',
        '_proxy', '_widget', '_shape_path', '_outline_path',
        '_input_area_rect', '_output_area_rect'
    )

    def __init__(self, parent: 'Node'):
        """
        Initialize NodeBody with a parent node.
        
        Args:
            parent (Node): The owning node
        """
        super().__init__(parent)
        self._node = parent
        self._width = parent._config['width']
        self._height = 100
        self._bg_color = parent._config['body_bg']
        self._outline_color = parent._config['outline_color']
        
        self._shape_path = QPainterPath()
        self._outline_path = QPainterPath()
        self._input_area_rect = QRectF()
        self._output_area_rect = QRectF()
        
        self._proxy = QGraphicsProxyWidget(self)
        self._widget = QWidget()
        self._widget.setStyleSheet("background-color: transparent; color: white;")
        self._widget.setLayout(QVBoxLayout())
        self._widget.layout().setContentsMargins(0, 0, 0, 0)
        self._proxy.setWidget(self._widget)
        self._proxy.setPos(10, 10)
        
        # ──── NEW: Enable focus routing for interactive widgets ────
        self._proxy.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True
        )
        self._proxy.setAcceptHoverEvents(True)
        self._proxy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # ──── END NEW ─────────────────────────────────────────────
        
        self._recalculate_path()

    def update_layout(self, width: float, height: float, 
                     input_rect: QRectF, output_rect: QRectF, 
                     widget_y: float, widget_h: float):
        """
        Updates the geometry of the body and its content proxy.
        This replaces the old set_size() to allow for port area reservation.
        
        Args:
            width (float): New width
            height (float): New height  
            input_rect (QRectF): Input area rectangle
            output_rect (QRectF): Output area rectangle
            widget_y (float): Widget Y position
            widget_h (float): Widget height
        """
        if self.scene():
            self.prepareGeometryChange()
        self._width = width
        self._height = height
        self._input_area_rect = input_rect
        self._output_area_rect = output_rect
        
        self._proxy.setPos(10, widget_y)
        self._proxy.resize(max(0, width - 20), max(0, widget_h))
        
        self._recalculate_path()
        self.update()

    def set_size(self, width: float, height: float) -> None:
        """
        Compatibility wrapper for update_layout.
        Preserves old behavior where widget consumes full available space (minus padding).
        
        Args:
            width (float): New width
            height (float): New height
        """
        # Assume 10px padding on top, 10px on bottom
        widget_y = 10.0
        widget_h = max(0, height - 20)
        self.update_layout(width, height, QRectF(), QRectF(), widget_y, widget_h)

    def get_content_min_size(self) -> Tuple[float, float]:
        """
        Get the minimum size required for content.
        
        Returns:
            tuple: (width, height) minimum content sizes
        """
        size_hint = self._widget.minimumSizeHint()
        return size_hint.width() + 20, size_hint.height()

    def set_colors(self, bg: QColor, outline: QColor) -> None:
        """
        Set body colors.
        
        Args:
            bg (QColor): Background color
            outline (QColor): Outline color
        """
        self._bg_color = bg
        self._outline_color = outline
        self.update()

    def set_content(self, widget: QWidget) -> None:
        """
        Set the content widget.
        
        Args:
            widget (QWidget): Widget to display in body
        """
        layout = self._widget.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        layout.addWidget(widget)

    def _recalculate_path(self):
        cfg = self._node._config
        r = cfg['radius']
        w = self._width
        h = self._height
        
        fill_path = QPainterPath()
        fill_path.moveTo(0, 0)
        fill_path.lineTo(w, 0)
        fill_path.lineTo(w, h - r)
        fill_path.arcTo(w - 2*r, h - 2*r, 2*r, 2*r, 0, -90)
        fill_path.lineTo(r, h)
        fill_path.arcTo(0, h - 2*r, 2*r, 2*r, 270, -90)
        fill_path.lineTo(0, 0)
        fill_path.closeSubpath()
        self._shape_path = fill_path
        
        outline_path = QPainterPath()
        outline_path.moveTo(0, 0)
        outline_path.lineTo(0, h - r)
        outline_path.arcTo(0, h - 2*r, 2*r, 2*r, 180, 90)
        outline_path.lineTo(w - r, h)
        outline_path.arcTo(w - 2*r, h - 2*r, 2*r, 2*r, 270, 90)
        outline_path.lineTo(w, 0)
        self._outline_path = outline_path

    def boundingRect(self) -> QRectF:
        """Get the body's bounding rectangle."""
        return QRectF(0, 0, self._width, self._height)

    def shape(self) -> QPainterPath:
        """Return the shape path for accurate hit-testing with rounded corners."""
        return self._shape_path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        """
        Paint the body component.
        
        Args:
            painter (QPainter): The painter
            option (QStyleOptionGraphicsItem): Style options  
            widget (QWidget, optional): Widget being painted on
        """
        cfg = self._node._config
        
        if cfg['body_gradient_enabled']:
            shift = cfg['body_gradient_shift']
            angle = cfg['body_gradient_angle']
            col_start = self._bg_color
            col_end = shift_color(col_start, shift)
            grad = create_angled_gradient(self.boundingRect(), angle, col_start, col_end)
            painter.setBrush(grad)
        else:
            painter.setBrush(self._bg_color)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(self._shape_path)

        # Draw Port Areas if enabled (New Functionality)
        if cfg.get('enable_port_area', False):
            area_bg = cfg.get('port_area_bg')
            if area_bg is not None:
                painter.setBrush(area_bg)
                if not self._input_area_rect.isEmpty():
                    painter.drawRect(self._input_area_rect)
                if not self._output_area_rect.isEmpty():
                    painter.drawRect(self._output_area_rect)

        pen = QPen(self._outline_color, cfg['border_width'])
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._outline_path)
