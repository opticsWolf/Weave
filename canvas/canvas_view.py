# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

import sys
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QApplication, QGraphicsView, QGraphicsScene, QGraphicsItem
)
from PySide6.QtCore import Qt, QRectF, QEvent, QPointF
from PySide6.QtGui import (
    QPainter, QMouseEvent, QColor, QBrush, QPen
)

# ==============================================================================
# 1. IMPORTS FROM STYLE MANAGEMENT SYSTEM
# ==============================================================================

from weave.stylemanager import StyleManager, get_style_manager, StyleCategory
from weave.themes.core_theme import CanvasStyleSchema

# ==============================================================================
# 2. COMPONENT: Node View (Enhanced with Styles)
# ==============================================================================

class CanvasView(QGraphicsView):
    """
    A highly optimized QGraphicsView for node editors.
    
    Features:
    - Configurable Zoom Limits & Sensitivity via set_config().
    - Middle-click panning (ScrollHandDrag).
    - Rubber band selection.
    - Centralized Zoom API.
    - Dynamic style management from StyleManager.
    """

    def __init__(
        self, 
        scene: QGraphicsScene, 
        parent=None
    ):
        """
        Initializes the Node View with dynamic styling.

        Args:
            scene: The QGraphicsScene to visualize.
            parent: The parent widget.
        """
        super().__init__(scene, parent)

        # Initialize StyleManager and connect signals for dynamic updates
        self._style_manager = get_style_manager()
        self._style_manager.style_changed.connect(self._on_style_change)
        
        # Setup render hints
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # State tracking for zoom
        self._current_zoom = 1.0

        # Initialize config dictionary - THIS WAS MISSING!
        self._config = {}

        # Default behavior: RubberBand for selection
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # UX settings
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        # Apply initial styles and config
        self._apply_initial_styles()

    def _apply_initial_styles(self):
        """Apply initial canvas style configuration from StyleManager."""
        # Get current style values for canvas
        config = self._get_canvas_config()
        print ('_apply_initial_styles', config)
        self.set_config(**config)
        
        # Update background color to match theme
        bg_color = self._style_manager.get(StyleCategory.CANVAS, 'bg_color')
        if isinstance(bg_color, list):
            bg_qcolor = QColor(*bg_color)  # Convert [r,g,b,a] to QColor
            palette = self.palette()
            palette.setColor(self.backgroundRole(), bg_qcolor)
            self.setPalette(palette)

    def _get_canvas_config(self) -> Dict[str, Any]:
        """Get zoom configuration from CanvasStyleSchema."""
        style_manager = get_style_manager()
        
        # Fetch all canvas-related styles that might affect config
        return {
            'zoom_min': style_manager.get(StyleCategory.CANVAS, 'zoom_min', 0.2),
            'zoom_max': style_manager.get(StyleCategory.CANVAS, 'zoom_max', 3.0),
            'zoom_factor': style_manager.get(StyleCategory.CANVAS, 'zoom_factor', 1.15),
            'scrollbar_policy': style_manager.get(StyleCategory.CANVAS, 'scrollbar_policy', 'never')
        }

    # ==========================================================================
    # Configuration API
    # ==========================================================================

    def set_config(self, **kwargs):
        """
        Updates configuration dynamically.

        Usage:
            view.set_config(
                zoom_max=5.0, 
                zoom_factor=1.2
            )
        """
        for key, value in kwargs.items():
            self._config[key] = value
            self._apply_scrollbar_policy()

    def _apply_scrollbar_policy(self):
        # Mapping string keywords to Qt Enum constants
        policy_map = {
        "always": Qt.ScrollBarPolicy.ScrollBarAlwaysOn,
        "never":  Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        "auto":   Qt.ScrollBarPolicy.ScrollBarAsNeeded
        }
        
        policy_str = self._config.get('scrollbar_policy', 'never').lower()
        qt_policy = policy_map.get(policy_str, Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        print ('_apply_scrollbar_policy', qt_policy)
        
        self.setVerticalScrollBarPolicy(qt_policy)
        self.setHorizontalScrollBarPolicy(qt_policy)

    # ==========================================================================
    # Event Handling
    # ==========================================================================

    def wheelEvent(self, event):
        """Zoom Logic with Configurable Limits."""
        # FIXED: Now properly uses self._config instead of fallback values
        factor = self._config.get('zoom_factor', 1.15)

        if event.angleDelta().y() > 0:
            zoom_factor = factor
        else:
            zoom_factor = 1 / factor

        new_zoom = self._current_zoom * zoom_factor

        # Check constraints from config - FIXED: Uses proper config values now
        z_min = self._config.get('zoom_min', 0.2)
        z_max = self._config.get('zoom_max', 3.0)

        if new_zoom < z_min or new_zoom > z_max:
            return

        self.scale(zoom_factor, zoom_factor)
        self._current_zoom = new_zoom

    def mousePressEvent(self, event: QMouseEvent):
        """Intercepts Middle Click to trigger Panning."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._start_panning(event)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Intercepts Middle Release to stop Panning."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._stop_panning(event)
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """
        Handles Double Click actions.
        """
        if (event.modifiers() == Qt.KeyboardModifier.ControlModifier and 
            event.button() == Qt.MouseButton.RightButton):
            
            item = self.itemAt(event.position().toPoint())
            
            if item:
                self._focus_on_item(item)
            else:
                self.fit_content()
            
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    # ==========================================================================
    # Public API (Used by Minimap)
    # ==========================================================================

    def reset_zoom(self):
        """
        Resets zoom to 1.0 (100%) and centers on the content.
        Safely updates internal state.
        """
        # Determine center target
        rect = self.scene().itemsBoundingRect()
        target_center = rect.center() if not rect.isNull() else QPointF(0, 0)

        # Use helper to apply zoom safely
        self._perform_safe_zoom_and_center(1.0, target_center)

    def fit_content(self):
        """
        Calculates the bounding box of all items and updates the viewport transform 
        to fit them, respecting configurable limits.
        """
        rect = self.scene().itemsBoundingRect()
        if rect.isNull():
            return

        # Add margin (5%)
        rect.adjust(-rect.width() * 0.05, -rect.height() * 0.05, 
                     rect.width() * 0.05,  rect.height() * 0.05)

        viewport_rect = self.viewport().rect()
        if viewport_rect.width() == 0 or viewport_rect.height() == 0:
            return

        ratio_w = viewport_rect.width() / rect.width()
        ratio_h = viewport_rect.height() / rect.height()

        new_scale = min(ratio_w, ratio_h)

        # Enforce Limits from Config - FIXED: Uses proper config values
        z_min = self._config.get('zoom_min', 0.2)
        z_max = self._config.get('zoom_max', 3.0)

        if new_scale < z_min:
            new_scale = z_min
        elif new_scale > z_max:
            new_scale = z_max

        self._perform_safe_zoom_and_center(new_scale, rect.center())

    # ==========================================================================
    # Helper Logic
    # ==========================================================================

    def _start_panning(self, event: QMouseEvent):
        """Switch context to panning mode."""
        fake_release = QMouseEvent(
            QEvent.Type.MouseButtonRelease, event.position(), event.globalPosition(),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, event.modifiers()
        )
        super().mouseReleaseEvent(fake_release)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        fake_press = QMouseEvent(
            event.type(), event.position(), event.globalPosition(),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, event.modifiers()
        )
        super().mousePressEvent(fake_press)

    def _stop_panning(self, event: QMouseEvent):
        """Switch context back to selection mode."""
        fake_release = QMouseEvent(
            event.type(), event.position(), event.globalPosition(),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, event.modifiers()
        )
        super().mouseReleaseEvent(fake_release)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    def _focus_on_item(self, item: QGraphicsItem):
        """
        Centers the view on a specific item and zooms in to MAX zoom.
        """
        target_center = item.sceneBoundingRect().center()
        max_zoom = self._config.get('zoom_max', 3.0)
        self._perform_safe_zoom_and_center(max_zoom, target_center)

    def _perform_safe_zoom_and_center(self, target_scale: float, center_point: QPointF):
        """
        Helper to safely scale and center without 'AnchorUnderMouse' interference.
        Updates self._current_zoom to keep state synchronized.
        """
        # 1. Temporarily disable AnchorUnderMouse. 
        original_anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        try:
            # 2. Apply Scale relative to current state
            if self._current_zoom == 0: self._current_zoom = 0.0001 # Safety

            scale_factor = target_scale / self._current_zoom
            self.scale(scale_factor, scale_factor)
            self._current_zoom = target_scale

            # 3. Apply Center (Absolute positioning)
            self.centerOn(center_point)

        finally:
            # 4. Restore original anchor
            self.setTransformationAnchor(original_anchor)
    
    # ==========================================================================
    # STYLE MANAGEMENT API
    # ==========================================================================
    
    def _on_style_change(self, category: StyleCategory, changes: Dict[str, Any]):
        """Handle style updates from the manager."""
        if category == StyleCategory.CANVAS:
            for key, value in changes.items():
                if key in ['bg_color', 'grid_color']:
                    # Update internal state or redraw as needed
                    pass  # For now we just note it - actual painting would need to be implemented
                elif key in ['zoom_min', 'zoom_max', 'zoom_factor']:
                    # Reapply config from new values
                    self.set_config(**self._get_canvas_config())
