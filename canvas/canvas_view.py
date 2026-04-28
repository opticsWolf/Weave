# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from typing import Dict, Any
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsProxyWidget
)
from PySide6.QtCore import Qt, QEvent, QPointF, QTimer
from PySide6.QtGui import QPainter, QMouseEvent, QColor

from weave.stylemanager import get_style_manager, StyleCategory


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

        # StyleManager for dynamic theme updates
        self._style_manager = get_style_manager()
        
        # Use standard weakref registration instead of direct signal
        # connections to prevent memory leaks if the view is closed/re-created.
        self._style_manager.register(self, StyleCategory.CANVAS)
        
        # Render hints
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Zoom state
        self._current_zoom = 1.0
        self._config: Dict[str, Any] = {}
        self._initial_theme_applied = False

        # Default behavior: RubberBand for selection
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # UX settings
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        # Apply initial styles and config
        self._apply_initial_styles()

        # BoundingRectViewportUpdate is the best trade-off for node editors:
        # more efficient than FullViewportUpdate, avoids the under-invalidation
        # issues of MinimalViewportUpdate with overlapping items and glows.
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)

    # ==========================================================================
    # Initialization
    # ==========================================================================

    def _apply_initial_styles(self):
        """Apply initial canvas style configuration from StyleManager."""
        self.set_config(**self._get_canvas_config())
        
        # Update background color to match theme
        bg_color = self._style_manager.get(StyleCategory.CANVAS, 'bg_color')
        if isinstance(bg_color, QColor):
            self._apply_bg_color(bg_color)
        elif isinstance(bg_color, (list, tuple)) and len(bg_color) >= 3:
            r, g, b = int(bg_color[0]), int(bg_color[1]), int(bg_color[2])
            a = int(bg_color[3]) if len(bg_color) > 3 else 255
            self._apply_bg_color(QColor(r, g, b, a))
        elif isinstance(bg_color, str):
            self._apply_bg_color(QColor(bg_color))

    def _get_canvas_config(self) -> Dict[str, Any]:
        """Fetch zoom and scrollbar configuration from StyleManager."""
        return {
            'zoom_min': self._style_manager.get(StyleCategory.CANVAS, 'zoom_min', 0.2),
            'zoom_max': self._style_manager.get(StyleCategory.CANVAS, 'zoom_max', 3.0),
            'zoom_factor': self._style_manager.get(StyleCategory.CANVAS, 'zoom_factor', 1.15),
            'scrollbar_policy': self._style_manager.get(StyleCategory.CANVAS, 'scrollbar_policy', 'never')
        }

    def _apply_bg_color(self, color: QColor):
        """Apply background color to the view's palette."""
        palette = self.palette()
        palette.setColor(self.backgroundRole(), color)
        self.setPalette(palette)

    # ==========================================================================
    # Configuration API
    # ==========================================================================

    def set_config(self, **kwargs):
        """
        Updates configuration dynamically.

        Usage:
            view.set_config(zoom_max=5.0, zoom_factor=1.2)
        """
        self._config.update(kwargs)
        self._apply_scrollbar_policy()

    def _apply_scrollbar_policy(self):
        """Map the scrollbar_policy config string to a Qt enum and apply."""
        policy_map = {
            "always": Qt.ScrollBarPolicy.ScrollBarAlwaysOn,
            "never":  Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            "auto":   Qt.ScrollBarPolicy.ScrollBarAsNeeded
        }
        
        policy_str = self._config.get('scrollbar_policy', 'never').lower()
        qt_policy = policy_map.get(policy_str, Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.setVerticalScrollBarPolicy(qt_policy)
        self.setHorizontalScrollBarPolicy(qt_policy)

    # ==========================================================================
    # Event Handling
    # ==========================================================================

    def showEvent(self, event) -> None:
        """
        Reapply the active theme once the view is fully visible.
        
        StyleManager._boot() applies the theme during construction — before
        the window manager has settled the viewport to its final pixel
        geometry.  The grid drawn with those transient dimensions gets cached
        and every subsequent partial repaint mismatches against it.
        
        A single deferred apply_theme() after the show event is exactly what
        the manual menu click does, and we know that fixes it permanently.
        The singleShot(0) ensures all pending layout/resize events from the
        show() chain have been processed first.
        """
        super().showEvent(event)
        if not self._initial_theme_applied:
            self._initial_theme_applied = True
            QTimer.singleShot(0, self._reapply_theme)

    def _reapply_theme(self) -> None:
        """Reapply the current theme + workspace prefs now that geometry is settled.

        Uses ``apply_theme_and_prefs`` so that the user's persisted
        grid type, trace style, and snapping override the theme defaults
        — matching exactly what ``_boot()`` established before the
        view's geometry was ready.
        """
        self._style_manager.apply_theme_and_prefs(
            self._style_manager.current_theme
        )

    def wheelEvent(self, event):
        """Zoom the canvas, or delegate to a proxy widget under the cursor."""
        item = self.itemAt(event.position().toPoint())
        
        # Walk up the item hierarchy: the hit item may be a child of the proxy.
        while item is not None:
            if isinstance(item, QGraphicsProxyWidget):
                # 1. Save current canvas scrollbar positions
                h_scroll = self.horizontalScrollBar().value()
                v_scroll = self.verticalScrollBar().value()
                
                # 2. Pass the scroll event to the embedded text box/widget
                super().wheelEvent(event)
                
                # 3. Undo Qt's default fallback canvas panning
                # If the widget ignored the event (e.g. reached the end of its scroll),
                # Qt naturally scrolled the canvas. This reverts it synchronously.
                self.horizontalScrollBar().setValue(h_scroll)
                self.verticalScrollBar().setValue(v_scroll)
                
                # 4. Force accept to prevent the event from bubbling up to parent windows
                event.accept()
                return
            item = item.parentItem()

        # Canvas zoom
        factor = self._config.get('zoom_factor', 1.15)
        zoom_factor = factor if event.angleDelta().y() > 0 else 1.0 / factor

        new_zoom = self._current_zoom * zoom_factor

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
        """Ctrl+Right-DoubleClick: focus item or fit content."""
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
        """Resets zoom to 1.0 (100%) and centers on the content."""
        rect = self.scene().itemsBoundingRect()
        target_center = rect.center() if not rect.isNull() else QPointF(0, 0)
        self._perform_safe_zoom_and_center(1.0, target_center)

    def fit_content(self):
        """
        Fits viewport to the bounding box of all items, 
        respecting configurable zoom limits.
        """
        rect = self.scene().itemsBoundingRect()
        if rect.isNull():
            return

        # Add 5% margin
        rect.adjust(-rect.width() * 0.05, -rect.height() * 0.05, 
                     rect.width() * 0.05,  rect.height() * 0.05)

        viewport_rect = self.viewport().rect()
        if viewport_rect.width() == 0 or viewport_rect.height() == 0:
            return

        ratio_w = viewport_rect.width() / rect.width()
        ratio_h = viewport_rect.height() / rect.height()
        new_scale = min(ratio_w, ratio_h)

        z_min = self._config.get('zoom_min', 0.2)
        z_max = self._config.get('zoom_max', 3.0)
        new_scale = max(z_min, min(z_max, new_scale))

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
        """Centers the view on a specific item and zooms to max."""
        target_center = item.sceneBoundingRect().center()
        max_zoom = self._config.get('zoom_max', 3.0)
        self._perform_safe_zoom_and_center(max_zoom, target_center)

    def _perform_safe_zoom_and_center(self, target_scale: float, center_point: QPointF):
        """
        Safely scale and center without AnchorUnderMouse interference.
        Keeps self._current_zoom synchronized.
        """
        original_anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        try:
            if self._current_zoom == 0:
                self._current_zoom = 0.0001

            scale_factor = target_scale / self._current_zoom
            self.scale(scale_factor, scale_factor)
            self._current_zoom = target_scale

            self.centerOn(center_point)
        finally:
            self.setTransformationAnchor(original_anchor)
    
    # ==========================================================================
    # Style Management
    # ==========================================================================
    
    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]):
        """Handle live style updates from the StyleManager."""
        if category != StyleCategory.CANVAS:
            return

        # Background color → update view palette
        if 'bg_color' in changes:
            bg = changes['bg_color']

            # Bulletproof C++ color casting to prevent crashes from
            # malformed lists or float values in the theme config.
            try:
                if isinstance(bg, QColor):
                    self._apply_bg_color(bg)
                elif isinstance(bg, (list, tuple)) and len(bg) >= 3:
                    r, g, b = int(bg[0]), int(bg[1]), int(bg[2])
                    a = int(bg[3]) if len(bg) > 3 else 255
                    self._apply_bg_color(QColor(r, g, b, a))
                elif isinstance(bg, str):
                    self._apply_bg_color(QColor(bg))
            except Exception as e:
                from weave.logger import get_logger
                get_logger("CanvasView").warning(
                    f"Failed to parse view bg_color '{bg}': {e}")

        # Zoom/scrollbar config → update config dict
        zoom_keys = {'zoom_min', 'zoom_max', 'zoom_factor', 'scrollbar_policy'}
        if zoom_keys & changes.keys():
            self.set_config(**self._get_canvas_config())

    # ==========================================================================
    # Shutdown
    # ==========================================================================

    def closeEvent(self, event) -> None:
        """Persist workspace preferences and unregister from StyleManager.

        The view is typically the last widget destroyed, so this is the
        safest place to flush preferences to QSettings and sever the
        StyleManager subscription.
        """
        self._style_manager.unregister(self, StyleCategory.CANVAS)
        self._style_manager.persist_all()
        super().closeEvent(event)