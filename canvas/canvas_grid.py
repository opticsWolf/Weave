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
from typing import List, Union, Optional
from PySide6.QtCore import QRectF, QPointF, QLineF
from PySide6.QtGui import QPainter, QPen

# Every Nth line/dot intersection is treated as a major (accent) element.
_ACCENT_INTERVAL = 5

# Fallback scale factors used only when no explicit major_pen is supplied to
# draw_grid() — keeps the API backward-compatible for callers that have not
# yet migrated to providing separate per-width pens.
_FALLBACK_MINOR_FACTOR = 0.75
_FALLBACK_MAJOR_FACTOR = 1.25


class GridType(IntEnum):
    """Grid rendering style enumeration using IntEnum for faster comparisons."""
    NONE = 0
    LINES = 1
    DOTS = 2
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
                  style: GridType = GridType.LINES,
                  major_pen: Optional[QPen] = None) -> None:
        """
        Draws the grid using the specified style.

        Args:
            painter:   The QPainter to use.
            rect:      The visible rectangle in scene coordinates.
            spacing:   The distance between grid intersections.
            pen:       Base / minor pen.  Used as-is for LINES and DOTS.
                       For LINES_ACCENT / DOTS_ACCENT it is the *minor*
                       (non-accent) pen.
            style:     The GridType enum member.
            major_pen: Optional major (accent) pen for LINES_ACCENT /
                       DOTS_ACCENT modes.  When *None* the renderer derives
                       minor/major pens from *pen* via the fallback scale
                       factors so that callers that have not yet migrated
                       to explicit per-width pens continue to work correctly.
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
            minor_pen, resolved_major = self._resolve_accent_pens(pen, major_pen)
            self._draw_lines_accent(painter, left, right, top, bottom,
                                    first_left, first_top, spacing,
                                    minor_pen, resolved_major)

        elif style == GridType.DOTS_ACCENT:
            minor_pen, resolved_major = self._resolve_accent_pens(pen, major_pen)
            self._draw_dots_accent(painter, left, right, top, bottom,
                                   first_left, first_top, spacing,
                                   minor_pen, resolved_major)

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

    @staticmethod
    def _resolve_accent_pens(pen: QPen, major_pen: Optional[QPen]) -> tuple:
        """
        Return *(minor_pen, major_pen)* ready for accent drawing.

        When an explicit *major_pen* has been provided (the normal path after
        the canvas migrated to per-width pens), both pens are returned as-is.

        When *major_pen* is *None* the method falls back to deriving both
        pens from *pen* via the legacy scale factors so that callers that
        have not yet migrated continue to work correctly.
        """
        if major_pen is not None:
            return pen, major_pen

        # ── Fallback: derive widths from base pen ───────────────────────
        raw_width = pen.widthF()
        base_width = raw_width if raw_width > 0.0 else 1.0

        minor = QPen(pen)
        minor.setWidthF(base_width * _FALLBACK_MINOR_FACTOR)

        major = QPen(pen)
        major.setWidthF(base_width * _FALLBACK_MAJOR_FACTOR)

        return minor, major

    def _draw_lines_accent(self, painter, left, right, top, bottom,
                           f_left, f_top, spacing,
                           minor_pen: QPen, major_pen: QPen) -> None:
        """
        Draws grid lines in two passes:
          • Minor lines  — drawn with *minor_pen*  (non-accent)
          • Major lines  — drawn with *major_pen*  (every _ACCENT_INTERVAL-th)

        Accent index is aligned to the global grid origin so that major
        lines remain stable while the viewport is panned.
        """
        v_coords = range(f_left, right  + spacing, spacing)
        h_coords = range(f_top,  bottom + spacing, spacing)

        minor_lines: List[QLineF] = []
        major_lines: List[QLineF] = []

        for x in v_coords:
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
                          f_left, f_top, spacing,
                          minor_pen: QPen, major_pen: QPen) -> None:
        """
        Draws grid dots in two passes:
          • Minor dots  — drawn with *minor_pen*  (all non-accent intersections)
          • Major dots  — drawn with *major_pen*  (intersections where *both*
                          X and Y indices are multiples of _ACCENT_INTERVAL)

        Uses absolute grid coordinates so major dots stay fixed relative to
        the scene origin regardless of viewport position.
        """
        x_coords = np.arange(f_left, right  + spacing, spacing)
        y_coords = np.arange(f_top,  bottom + spacing, spacing)

        # GAP FIX: Cast coordinates to integer indices safely using np.round() 
        # to prevent IEEE 754 floating-point precision errors from randomly 
        # dropping major grid accents as the user pans the camera!
        x_indices = np.round(x_coords / spacing).astype(np.int64)
        y_indices = np.round(y_coords / spacing).astype(np.int64)

        x_major = (x_indices % _ACCENT_INTERVAL) == 0  # (Nx,)
        y_major = (y_indices % _ACCENT_INTERVAL) == 0  # (Ny,)

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