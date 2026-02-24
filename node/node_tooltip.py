# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from typing import Dict, Any, Optional
import sys

from PySide6.QtWidgets import (
    QWidget, QGraphicsDropShadowEffect, QGraphicsView, QToolTip, QApplication
)
from PySide6.QtCore import (
    Qt, QObject, QRectF, QPoint, QSize, QEvent, QTimer, 
    QPropertyAnimation, QEasingCurve, Property
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QPainterPath, QCursor
)

# ==============================================================================
# CONFIGURATION
# ==============================================================================


DEFAULT_TOOLTIP_CONFIG: Dict[str, Any] = {
    # Container Styling
    'bg_color': QColor(40, 43, 48, 240),
    'border_color': QColor(80, 80, 80, 255),
    'border_width': 1.0,
    'corner_radius': 6.0,
    
    # Padding / Spacing
    'padding_x': 10,
    'padding_y': 6,
    'shadow_blur': 12,
    'shadow_color': QColor(0, 0, 0, 90),

    # Font Styling
    'text_color': QColor(220, 220, 220),
    'font_family': "Segoe UI",
    'font_size': 9,
    'font_weight': QFont.Weight.Medium,
    'font_italic': False,

    # NEW: Animation & Behavior Config
    'hide_delay_ms': 200,          # Time before hiding after mouse leave
    'anim_duration_ms': 150,       # Entrance animation speed
    'anim_easing': QEasingCurve.Type.OutQuad, # Easing for the pop-up
}

# ==============================================================================
# CUSTOM TOOLTIP WIDGET
# ==============================================================================

class NodeTooltip(QWidget):
    """
    A high-performance, custom-painted tooltip widget with entrance animation.
    """

    def __init__(self, parent: Optional[QWidget] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        
        # 1. Window Flags
        self.setWindowFlags(
            Qt.WindowType.ToolTip | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.NoDropShadowWindowHint
        )
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # 2. Configuration & State
        self._config = DEFAULT_TOOLTIP_CONFIG.copy()
        if config:
            self.update_config(config)
            
        self._text: str = ""
        self._font: QFont = QFont()
        self._update_font_object()

        # 3. Animation State
        self._scale_factor: float = 1.0
        
        # We need a QPropertyAnimation to drive the value smoothly
        self._anim = QPropertyAnimation(self, b"scale_factor", self)
        self._anim.setDuration(self._config['anim_duration_ms'])
        self._anim.setEasingCurve(self._config['anim_easing'])

        # 4. Shadow Effect
        self._shadow = QGraphicsDropShadowEffect(self)
        self._update_shadow()
        self.setGraphicsEffect(self._shadow)

    # -- Qt Property for Animation --
    def get_scale_factor(self) -> float:
        return self._scale_factor

    def set_scale_factor(self, factor: float):
        self._scale_factor = factor
        self.update() # Trigger repaint on every frame of animation

    # Register the property so QPropertyAnimation can find it
    scale_factor = Property(float, get_scale_factor, set_scale_factor)

    # -- Configuration Methods --
    def update_config(self, new_config: Dict[str, Any]) -> None:
        self._config.update(new_config)
        self._update_font_object()
        self._update_shadow()
        if hasattr(self, '_anim'):
            self._anim.setDuration(self._config['anim_duration_ms'])
            self._anim.setEasingCurve(self._config['anim_easing'])
        self.update()

    def _update_font_object(self):
        f = QFont(self._config['font_family'], self._config['font_size'])
        f.setWeight(self._config['font_weight'])
        f.setItalic(self._config['font_italic'])
        f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        self._font = f

    def _update_shadow(self):
        self._shadow.setBlurRadius(self._config['shadow_blur'])
        self._shadow.setColor(self._config['shadow_color'])
        self._shadow.setOffset(0, 2)

    def setText(self, text: str) -> None:
        if self._text == text:
            return
            
        self._text = text
        
        # Calculate Size
        fm = QFontMetrics(self._font)
        w_text = fm.horizontalAdvance(text)
        h_text = fm.height()
        
        px = self._config['padding_x']
        py = self._config['padding_y']
        
        total_w = w_text + (px * 2)
        total_h = h_text + (py * 2)
        
        self.resize(QSize(int(total_w), int(total_h)))

    def show_at(self, global_pos: QPoint, text: str = None) -> None:
        if text is not None:
            self.setText(text)
            
        offset_pos = global_pos + QPoint(10, 10)
        self.move(offset_pos)
        
        # If hidden, or if we want to restart animation on every move:
        # (Usually only animate if popping up from hidden state)
        if self.isHidden():
            self.set_scale_factor(0.0) # Reset start state
            self.show()
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        else:
            # If already visible, just ensure we are fully scaled
            if self._scale_factor != 1.0:
                self._scale_factor = 1.0
                self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = QRectF(self.rect())
        
        # --- ANIMATION TRANSFORM ---
        # "Enlarging from the top": Scale vertically, anchored at the top-center.
        if self._scale_factor < 1.0:
            # 1. Translate painter to the top-center pivot point
            pivot_x = rect.center().x()
            pivot_y = rect.top()
            painter.translate(pivot_x, pivot_y)
            
            # 2. Scale (X=1.0 keeps width, Y=factor grows height)
            painter.scale(1.0, self._scale_factor)
            
            # 3. Translate back
            painter.translate(-pivot_x, -pivot_y)
        # ---------------------------

        bw = self._config['border_width']
        draw_rect = rect.adjusted(bw/2, bw/2, -bw/2, -bw/2)
        
        # 1. Draw Background & Border
        path = QPainterPath()
        radius = self._config['corner_radius']
        path.addRoundedRect(draw_rect, radius, radius)
        
        painter.setBrush(QBrush(self._config['bg_color']))
        
        pen = QPen(self._config['border_color'])
        pen.setWidthF(bw)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin) 
        painter.setPen(pen)
        
        painter.drawPath(path)

        # 2. Draw Text
        painter.setFont(self._font)
        painter.setPen(self._config['text_color'])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        
        painter.end()


# ==============================================================================
# TOOLTIP PROXY (CONTROLLER)
# ==============================================================================

class GlobalTooltipProxy(QObject):
    def __init__(self, tooltip_widget: NodeTooltip):
        super().__init__()
        self._tooltip = tooltip_widget
        self._current_watched_item: Optional[Any] = None 
        self._current_view: Optional[QGraphicsView] = None
        
        # --- MISSING PART WAS HERE ---
        # Initialize the timer immediately
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._perform_hide)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        
        # 1. SHOW LOGIC
        if event.type() == QEvent.ToolTip:
            # If we are about to show something, cancel any pending hide
            if self._hide_timer.isActive():
                self._hide_timer.stop()

            text = ""
            sender_item = None
            
            parent = obj.parent() if isinstance(obj, QWidget) else None
            
            if isinstance(parent, QGraphicsView):
                self._current_view = parent
                local_pos = obj.mapFromGlobal(QCursor.pos())
                sender_item = parent.itemAt(local_pos)
                if sender_item:
                    text = sender_item.toolTip()
            
            else:
                self._current_view = None
                text = obj.toolTip()
                sender_item = obj

            if text:
                self._current_watched_item = sender_item
                
                global_pos = QCursor.pos()
                if isinstance(event, (QEvent, QObject)) and hasattr(event, "globalPos"):
                     global_pos = event.globalPos()

                self._tooltip.show_at(global_pos, text)
                return True 

        # 2. HIDE LOGIC A: Leaving Widget
        elif event.type() == QEvent.Leave:
            self._schedule_hide()

        # 3. HIDE LOGIC B: GraphicsItem Tracking
        elif event.type() == QEvent.MouseMove:
            if self._tooltip.isVisible() and self._current_view and self._current_watched_item:
                
                item_under_mouse = self._current_view.itemAt(event.pos())
                if item_under_mouse != self._current_watched_item:
                    self._schedule_hide()

        # 4. HIDE LOGIC C: Click
        elif event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            self._schedule_hide(immediate=True)

        return super().eventFilter(obj, event)

    def _schedule_hide(self, immediate: bool = False):
        """Starts the timer to hide, or hides immediately."""
        
        # SAFETY CHECK: Lazy-load the timer if it doesn't exist 
        # (Fixes your specific crash if __init__ wasn't re-run)
        if not hasattr(self, '_hide_timer'):
            self._hide_timer = QTimer(self)
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(self._perform_hide)

        if immediate:
            self._perform_hide()
        else:
            # Only start if not already running to avoid resetting the delay constantly
            if not self._hide_timer.isActive():
                # specific safe get for config
                delay = self._tooltip._config.get('hide_delay_ms', 0)
                if delay > 0:
                    self._hide_timer.start(delay)
                else:
                    self._perform_hide()

    def _perform_hide(self):
        """Actually hides the widget."""
        if self._tooltip.isVisible():
            self._tooltip.hide()
            QToolTip.hideText()
        
        self._current_watched_item = None
        self._current_view = None