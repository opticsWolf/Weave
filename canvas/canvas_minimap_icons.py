# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any

# PySide6 Core (Data structures, Flags)
from PySide6.QtCore import (
    Qt, 
    QRectF, 
    QPointF
)

# PySide6 Gui (Painting)
from PySide6.QtGui import (
    QPainter, 
    QPainterPath, 
    QPen
)

# ==============================================================================
# 2. ICON PAINTER SUBCLASSES (The New Logic)
# ==============================================================================

class MinimapButton(Enum):
    NONE = -1
    RESET = 0   
    FIT = 1     
    PIN = 2     
    SNAP = 3    

class MinimapIconPainter(ABC):
    """Abstract base class for drawing minimap icons."""
    
    @abstractmethod
    def paint(self, painter: QPainter, rect: QRectF, config: Dict[str, Any], state: bool = False):
        """
        Draws the icon symbol.
        :param painter: The QPainter instance (already translated/prepped).
        :param rect: The bounding rect of the button.
        :param config: The minimap configuration dictionary.
        :param state: A boolean representing active/toggled state (e.g., is_pinned).
        """
        pass

class IconReset(MinimapIconPainter):
    def paint(self, painter: QPainter, rect: QRectF, config: Dict[str, Any], state: bool = False):
        center = rect.center()
        r = rect.width() * 0.25
        painter.drawEllipse(center, r, r)
        painter.drawPoint(center)

class IconFit(MinimapIconPainter):
    def paint(self, painter: QPainter, rect: QRectF, config: Dict[str, Any], state: bool = False):
        center = rect.center()
        w_icon = rect.width() * 0.4
        h_icon = rect.height() * 0.4
        half_w = w_icon / 2
        half_h = h_icon / 2
        
        # Left Bracket
        path = QPainterPath()
        path.moveTo(center.x() - half_w + 3, center.y() - half_h)
        path.lineTo(center.x() - half_w, center.y() - half_h)
        path.lineTo(center.x() - half_w, center.y() + half_h)
        path.lineTo(center.x() - half_w + 3, center.y() + half_h)
        painter.drawPath(path)

        # Right Bracket
        path = QPainterPath()
        path.moveTo(center.x() + half_w - 3, center.y() - half_h)
        path.lineTo(center.x() + half_w, center.y() - half_h)
        path.lineTo(center.x() + half_w, center.y() + half_h)
        path.lineTo(center.x() + half_w - 3, center.y() + half_h)
        painter.drawPath(path)

class IconPin(MinimapIconPainter):
    def paint(self, painter: QPainter, rect: QRectF, config: Dict[str, Any], is_pinned: bool = False):
        center = rect.center()
        pin_len = rect.height() * 0.4
        
        if is_pinned: 
            # "Pinned" visual state: Active color, Upright
            pen_active = QPen(config['active_icon_color'], config['icon_symbol_width'])
            pen_active.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_active)
            
            painter.drawLine(QPointF(center.x(), center.y() - pin_len/2), 
                             QPointF(center.x(), center.y() + pin_len/2))
            painter.drawEllipse(QPointF(center.x(), center.y() - pin_len/2), 2, 2)
        else:
            # "Unpinned" visual state: Border color (faint), Rotated
            painter.save()
            painter.translate(center)
            painter.rotate(45)
            
            # --- CHANGE START ---
            # Use border_color and thin width to match IconSnap's disabled state
            pen_faint = QPen(config['border_color'], 1.0)
            pen_faint.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_faint)
            # --- CHANGE END ---

            painter.drawLine(QPointF(0, -pin_len/2), QPointF(0, pin_len/2))
            painter.drawEllipse(QPointF(0, -pin_len/2), 2, 2)
            painter.restore()

class IconSnap(MinimapIconPainter):
    def paint(self, painter: QPainter, rect: QRectF, config: Dict[str, Any], is_snapping: bool = False):
        center = rect.center()
        painter.save()
        painter.translate(center)

        target_pen = painter.pen() # Default pen
        
        if is_snapping:
            # Active: Upright + Active Color
            target_pen = QPen(config['active_icon_color'], config['icon_symbol_width'])
        else:
            # Disabled: Rotated 45 + Border Color (fainter look)
            painter.rotate(45)
            target_pen = QPen(config['border_color'], 1.0) 

        # Scaling logic from original code
        S = 0.5
        painter.scale(S, S)
        target_pen.setWidthF(target_pen.widthF() / S)
        target_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        target_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(target_pen)

        path = QPainterPath()
        
        # Center Crosshair
        path.moveTo(-2, -4); path.lineTo(-2, 4)
        path.moveTo(2, -4);  path.lineTo(2, 4)
        path.moveTo(-4, -2); path.lineTo(4, -2)
        path.moveTo(-4, 2);  path.lineTo(4, 2)

        # Corners
        # Top-Left
        path.moveTo(-12, -4); path.lineTo(-12, -8)
        path.arcTo(QRectF(-12, -12, 8, 8), 180, -90)
        path.lineTo(-4, -12)
        # Top-Right
        path.moveTo(12, -4); path.lineTo(12, -8)
        path.arcTo(QRectF(4, -12, 8, 8), 0, 90)
        path.lineTo(4, -12)
        # Bottom-Left
        path.moveTo(-12, 4); path.lineTo(-12, 8)
        path.arcTo(QRectF(-12, 4, 8, 8), 180, 90)
        path.lineTo(-4, 12)
        # Bottom-Right
        path.moveTo(12, 4); path.lineTo(12, 8)
        path.arcTo(QRectF(4, 4, 8, 8), 0, -90)
        path.lineTo(4, 12)
        
        painter.drawPath(path)
        painter.restore()