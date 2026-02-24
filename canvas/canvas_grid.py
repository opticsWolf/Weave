# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from enum import IntEnum, auto
import math
import numpy as np
from typing import List, Union
from PySide6.QtCore import QRectF, QPointF, QLineF
from PySide6.QtGui import QPainter, QPen

class GridType(IntEnum):
    """Grid rendering style enumeration using IntEnum for faster comparisons."""
    LINES = 0
    DOTS = 1
    NONE = 2

class GridRenderer:
    """
    Unified Grid Renderer.
    Handles coordinate math and dispatches to specific draw logic based on style.
    """

    def draw_grid(self, 
                  painter: QPainter, 
                  rect: QRectF, 
                  spacing: int, 
                  pen: QPen, 
                  style: GridType = GridType.LINES) -> None:
        """
        Draws the grid using the specified style.
        
        Args:
            painter: The QPainter to use.
            rect: The visible rectangle in scene coordinates.
            spacing: The distance between grid intersections.
            pen: The QPen to use for drawing.
            style: The GridType enum member.
        """
        if style == GridType.NONE or spacing <= 0:
            return

        # Common coordinate calculations
        left, right = int(rect.left()), int(rect.right())
        top, bottom = int(rect.top()), int(rect.bottom())
        
        first_left = left - (left % spacing)
        first_top = top - (top % spacing)
        
        painter.setPen(pen)

        if style == GridType.LINES:
            self._draw_lines(painter, left, right, top, bottom, first_left, first_top, spacing)
        elif style == GridType.DOTS:
            self._draw_dots(painter, left, right, top, bottom, first_left, first_top, spacing)

    def _draw_lines(self, painter, left, right, top, bottom, f_left, f_top, spacing) -> None:
        """Batch draws grid lines."""
        lines = [QLineF(x, top, x, bottom) for x in range(f_left, right + spacing, spacing)]
        lines.extend(QLineF(left, y, right, y) for y in range(f_top, bottom + spacing, spacing))
        painter.drawLines(lines)

    def _draw_dots(self, painter, left, right, top, bottom, f_left, f_top, spacing) -> None:
        """Batch draws grid points using vectorized generation."""
        x_coords = np.arange(f_left, right + spacing, spacing)
        y_coords = np.arange(f_top, bottom + spacing, spacing)
        
        xx, yy = np.meshgrid(x_coords, y_coords)
        # Using a list comprehension for QPointF conversion is the fastest 
        # path for PySide6's drawPoints C++ binding
        pts = [QPointF(x, y) for x, y in zip(xx.ravel(), yy.ravel())]
        painter.drawPoints(pts)

    def should_render(self, rect: QRectF, spacing: int, max_elements: int, style: GridType) -> bool:
        """
        Checks if the grid density exceeds performance thresholds.
        
        Note: DOTS use area density (W*H), LINES use linear density (W+H).
        """
        if style == GridType.NONE or spacing <= 0:
            return False

        w_count = (int(rect.right()) - (int(rect.left()) % spacing)) // spacing
        h_count = (int(rect.bottom()) - (int(rect.top()) % spacing)) // spacing

        if style == GridType.DOTS:
            return (w_count * h_count) <= max_elements
        return (w_count + h_count) <= max_elements
