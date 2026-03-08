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

# Scale factors applied to the base pen width for minor/major elements.
_MINOR_WIDTH_FACTOR = 0.75
_MAJOR_WIDTH_FACTOR = 1.25
# Every Nth line/dot intersection is treated as a major (accent) element.
_ACCENT_INTERVAL = 5


class GridType(IntEnum):
    """Grid rendering style enumeration using IntEnum for faster comparisons."""
    LINES = 0
    DOTS = 1
    NONE = 2
    # Every _ACCENT_INTERVAL-th vertical/horizontal line is drawn thicker;
    # the remaining lines are drawn thinner than the base pen width.
    LINES_ACCENT = 3
    # Every intersection where both X and Y indices are multiples of
    # _ACCENT_INTERVAL is drawn as a larger dot; all others are smaller.
    DOTS_ACCENT = 4


class GridRenderer:
    """
    Unified Grid Renderer.
    Handles coordinate math and dispatches to specific draw logic based on style.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            pen: The QPen to use for drawing.  For LINES_ACCENT and
                 DOTS_ACCENT the pen's width is used as the *base* width
                 from which minor (_MINOR_WIDTH_FACTOR) and major
                 (_MAJOR_WIDTH_FACTOR) widths are derived.
            style: The GridType enum member.
        """
        if style == GridType.NONE or spacing <= 0:
            return

        # Common coordinate calculations
        left, right = int(rect.left()), int(rect.right())
        top, bottom = int(rect.top()), int(rect.bottom())

        first_left = left - (left % spacing)
        first_top  = top  - (top  % spacing)

        if style == GridType.LINES:
            painter.setPen(pen)
            self._draw_lines(painter, left, right, top, bottom,
                             first_left, first_top, spacing)

        elif style == GridType.DOTS:
            painter.setPen(pen)
            self._draw_dots(painter, left, right, top, bottom,
                            first_left, first_top, spacing)

        elif style == GridType.LINES_ACCENT:
            self._draw_lines_accent(painter, left, right, top, bottom,
                                    first_left, first_top, spacing, pen)

        elif style == GridType.DOTS_ACCENT:
            self._draw_dots_accent(painter, left, right, top, bottom,
                                   first_left, first_top, spacing, pen)

    def should_render(self, rect: QRectF, spacing: int, max_elements: int,
                      style: GridType) -> bool:
        """
        Checks if the grid density exceeds performance thresholds.

        Note: DOTS / DOTS_ACCENT use area density (W*H);
              LINES / LINES_ACCENT use linear density (W+H).
        """
        if style == GridType.NONE or spacing <= 0:
            return False

        w_count = (int(rect.right())  - (int(rect.left()) % spacing)) // spacing
        h_count = (int(rect.bottom()) - (int(rect.top())  % spacing)) // spacing

        if style in (GridType.DOTS, GridType.DOTS_ACCENT):
            return (w_count * h_count) <= max_elements
        return (w_count + h_count) <= max_elements

    # ------------------------------------------------------------------
    # Private helpers — plain styles
    # ------------------------------------------------------------------

    def _draw_lines(self, painter, left, right, top, bottom,
                    f_left, f_top, spacing) -> None:
        """Batch draws grid lines."""
        lines = [QLineF(x, top, x, bottom)
                 for x in range(f_left, right + spacing, spacing)]
        lines.extend(QLineF(left, y, right, y)
                     for y in range(f_top, bottom + spacing, spacing))
        painter.drawLines(lines)

    def _draw_dots(self, painter, left, right, top, bottom,
                   f_left, f_top, spacing) -> None:
        """Batch draws grid points using vectorized generation."""
        x_coords = np.arange(f_left, right + spacing, spacing)
        y_coords = np.arange(f_top,  bottom + spacing, spacing)

        xx, yy = np.meshgrid(x_coords, y_coords)
        # Using a list comprehension for QPointF conversion is the fastest
        # path for PySide6's drawPoints C++ binding
        pts = [QPointF(x, y) for x, y in zip(xx.ravel(), yy.ravel())]
        painter.drawPoints(pts)

    # ------------------------------------------------------------------
    # Private helpers — accent styles
    # ------------------------------------------------------------------

    def _make_accent_pens(self, base_pen: QPen):
        """
        Returns (minor_pen, major_pen) derived from *base_pen*.

        A zero pen width is treated as hairline (1.0) for scaling purposes,
        then set back to 0 for hairline rendering on the minor pen.
        """
        raw_width = base_pen.widthF()
        base_width = raw_width if raw_width > 0.0 else 1.0

        minor_pen = QPen(base_pen)
        minor_pen.setWidthF(base_width * _MINOR_WIDTH_FACTOR)

        major_pen = QPen(base_pen)
        major_pen.setWidthF(base_width * _MAJOR_WIDTH_FACTOR)

        return minor_pen, major_pen

    def _draw_lines_accent(self, painter, left, right, top, bottom,
                           f_left, f_top, spacing, pen: QPen) -> None:
        """
        Draws grid lines in two passes:
          • Minor lines  — width × _MINOR_WIDTH_FACTOR  (non-accent)
          • Major lines  — width × _MAJOR_WIDTH_FACTOR  (every _ACCENT_INTERVAL-th)

        Accent index is based on the line's position index within the visible
        range, aligned to the global grid origin so that major lines remain
        stable as the viewport is panned.
        """
        minor_pen, major_pen = self._make_accent_pens(pen)

        v_coords = range(f_left, right  + spacing, spacing)
        h_coords = range(f_top,  bottom + spacing, spacing)

        minor_lines: List[QLineF] = []
        major_lines: List[QLineF] = []

        for x in v_coords:
            # Use absolute grid index so major lines don't shift while panning.
            bucket = major_lines if (x // spacing) % _ACCENT_INTERVAL == 0 \
                     else minor_lines
            bucket.append(QLineF(x, top, x, bottom))

        for y in h_coords:
            bucket = major_lines if (y // spacing) % _ACCENT_INTERVAL == 0 \
                     else minor_lines
            bucket.append(QLineF(left, y, right, y))

        painter.setPen(minor_pen)
        if minor_lines:
            painter.drawLines(minor_lines)

        painter.setPen(major_pen)
        if major_lines:
            painter.drawLines(major_lines)

    def _draw_dots_accent(self, painter, left, right, top, bottom,
                          f_left, f_top, spacing, pen: QPen) -> None:
        """
        Draws grid dots in two passes:
          • Minor dots  — width × _MINOR_WIDTH_FACTOR  (all non-accent intersections)
          • Major dots  — width × _MAJOR_WIDTH_FACTOR  (intersections where *both*
                          X and Y grid indices are multiples of _ACCENT_INTERVAL)

        The accent index uses the absolute grid coordinate so major dots stay
        fixed relative to the scene origin regardless of viewport position.
        """
        minor_pen, major_pen = self._make_accent_pens(pen)

        x_coords = np.arange(f_left, right  + spacing, spacing)
        y_coords = np.arange(f_top,  bottom + spacing, spacing)

        # Boolean mask: True where the coordinate sits on a major grid line.
        x_major = (x_coords // spacing) % _ACCENT_INTERVAL == 0  # shape (Nx,)
        y_major = (y_coords // spacing) % _ACCENT_INTERVAL == 0  # shape (Ny,)

        xx, yy = np.meshgrid(x_coords, y_coords)          # (Ny, Nx)
        # A dot is major only when BOTH its column and row are accent lines.
        major_mask = np.outer(y_major, x_major)            # (Ny, Nx)

        flat_x     = xx.ravel()
        flat_y     = yy.ravel()
        flat_major = major_mask.ravel()

        minor_pts = [QPointF(float(x), float(y))
                     for x, y, m in zip(flat_x, flat_y, flat_major) if not m]
        major_pts = [QPointF(float(x), float(y))
                     for x, y, m in zip(flat_x, flat_y, flat_major) if m]

        painter.setPen(minor_pen)
        if minor_pts:
            painter.drawPoints(minor_pts)

        painter.setPen(major_pen)
        if major_pts:
            painter.drawPoints(major_pts)