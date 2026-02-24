# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Node Trace - Fixed version with complete overlay rendering support.

This file contains the fixed NodeTrace class that properly receives and draws 
state overlays.
"""

import math
from typing import Optional, Any, Dict
from PySide6.QtWidgets import QGraphicsPathItem, QWidget, QStyleOptionGraphicsItem
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from weave.node.node_port import NodePort

# Import consolidated port utilities
from weave.portutils import PortUtils, ConnectionFactory

# Import StyleManager for centralized styling
from weave.stylemanager import StyleManager, StyleCategory


class NodeTrace(QGraphicsPathItem):
    """
    Visual representation of a finalized connection.
    
    Integrated with StyleManager for centralized styling.
    Now supports complete state overlay rendering as described in the issue fix.
    """
    __slots__ = (
        'source', 'target', 'drag_pos', 
        '_last_src_pos', '_last_dst_pos',
        '_local_style', '_style_manager',
        '_main_pen', '_outline_pen', '_shadow_pen', '_shadow_offset'
    )

    def __init__(
        self, 
        source_port: NodePort, 
        dest_port: Optional[NodePort] = None, 
        cursor_pos: Optional[QPointF] = None,
        style: Optional[Dict[str, Any]] = None
    ):
        super().__init__()
        self.setZValue(-1)
        self.setAcceptHoverEvents(False)
        
        self.source = source_port
        self.target = dest_port
        self.drag_pos = cursor_pos if cursor_pos else QPointF(0, 0)
        
        self._last_src_pos = QPointF()
        self._last_dst_pos = QPointF()

        # Get StyleManager instance
        self._style_manager = StyleManager.instance()
        
        # Stores instance-specific overrides if any
        self._local_style = {} 
        if style:
            self._local_style = style

        self._main_pen = QPen()
        self._outline_pen = None  
        self._shadow_pen = None
        self._shadow_offset = QPointF(0, 0)
        
        self.refresh_style()
        self._register_connection()
        self.update_path()
        
        # Register for style change notifications
        self._style_manager.register(self, StyleCategory.TRACE)

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        """
        Callback method called when StyleManager notifies about style changes.
        
        Args:
            category: The style category that changed (should be StyleCategory.TRACE)
            changes: Dictionary of changed keys and their new values
        """
        if category == StyleCategory.TRACE:
            # Rebuild pens with updated styles
            self.refresh_style()

    def _get_trace_config(self) -> Dict[str, Any]:
        """
        Get the current trace configuration from StyleManager.
        
        Returns:
            A dictionary of trace styling parameters.
        """
        return self._style_manager.get_all(StyleCategory.TRACE)

    def refresh_style(self) -> None:
        """Re-reads the config from StyleManager and rebuilds pens."""
        full_config = self._get_trace_config()
        full_config.update(self._local_style)
        
        self._setup_pens(full_config)
        self.update()  # Trigger repaint

    def _setup_pens(self, config: Dict[str, Any]) -> None:
        """Sets up main, outline, and shadow pens from config."""
        # 1. Main Pen
        c = config.get("color")
        if c is None:
            # Use source port color as default for trace color
            base_color = getattr(self.source, 'color', QColor(200, 200, 200))
            # Get the port's state overlay and blend it in to match the port's outer circle
            port_overlay = getattr(self.source, '_state_overlay_color', Qt.GlobalColor.transparent)
            c = self._blend_color_with_overlay(base_color, port_overlay)
        
        width = config.get("width", 3.0)
        self._main_pen = QPen(QColor(c), width)
        self._main_pen.setStyle(config.get("style", Qt.PenStyle.SolidLine))
        self._main_pen.setCapStyle(config.get("cap_style", Qt.PenCapStyle.RoundCap))
        self._main_pen.setJoinStyle(config.get("join_style", Qt.PenJoinStyle.RoundJoin))

        # 2. Outline Pen
        o_width = config.get("outline_width", 0.0)
        if o_width > 0:
            o_color = config.get("outline_color", QColor(0, 0, 0, 50))
            total_width = width + (o_width * 2.0)
            self._outline_pen = QPen(QColor(o_color), total_width)
            self._outline_pen.setStyle(self._main_pen.style()) 
            self._outline_pen.setCapStyle(self._main_pen.capStyle())
            self._outline_pen.setJoinStyle(self._main_pen.joinStyle())
        else:
            self._outline_pen = None

        # 3. Shadow Pen
        if config.get("shadow_enable", False):
            s_color = QColor(config.get("shadow_color", QColor(0, 0, 0, 100)))
            self._shadow_pen = QPen(s_color, width + 1.0)
            self._shadow_pen.setStyle(self._main_pen.style())
            self._shadow_pen.setCapStyle(self._main_pen.capStyle())
            self._shadow_pen.setJoinStyle(self._main_pen.joinStyle())
            
            off_x = float(config.get("shadow_offset_x", 3.0))
            off_y = float(config.get("shadow_offset_y", 3.0))
            self._shadow_offset = QPointF(off_x, off_y)
        else:
            self._shadow_pen = None
            
        self.setPen(self._main_pen)

    def _register_connection(self) -> None:
        """Register this trace with its connected ports."""
        if hasattr(self.source, 'add_trace'):
            self.source.add_trace(self)
        if self.target and hasattr(self.target, 'add_trace'):
            self.target.add_trace(self)

    def set_target(self, port: NodePort) -> None:
        """Set the target port for this trace."""
        self.target = port
        if hasattr(self.target, 'add_trace'):
            self.target.add_trace(self)
        self.update_path()

    def set_state_overlay(self, overlay_color):
        """
        Triggered when the port's overlay changes.
        
        The trace doesn't store the overlay separately - it reads it directly from 
        the port during refresh_style() and bakes it into the pen color.
        
        Args:
            overlay_color: QColor or Qt.GlobalColor.transparent (for compatibility)
        """
        # Trigger a style refresh to rebuild pens with the port's current blended color
        self.refresh_style()

    def _blend_color_with_overlay(self, base_color: QColor, overlay_color) -> QColor:
        """
        Blend the overlay color with the base color, preserving the overlay's alpha
        as the blend factor.
        
        Args:
            base_color: The original trace color
            overlay_color: The state overlay color (with alpha indicating blend strength)
        
        Returns:
            A new QColor blended between base and overlay
        """
        if overlay_color is None or overlay_color == Qt.GlobalColor.transparent:
            return base_color
        
        # Handle QColor objects
        if isinstance(overlay_color, QColor):
            if overlay_color.alpha() == 0:
                return base_color
            
            # Use overlay alpha as blend factor (0-255 -> 0.0-1.0)
            blend_factor = (overlay_color.alpha() / 255) / 1.5
            
            # Linear interpolation between base and overlay RGB values
            r = int(base_color.red() * (1 - blend_factor) + overlay_color.red() * blend_factor)
            g = int(base_color.green() * (1 - blend_factor) + overlay_color.green() * blend_factor)
            b = int(base_color.blue() * (1 - blend_factor) + overlay_color.blue() * blend_factor)
            
            # Preserve original base alpha
            return QColor(r, g, b, base_color.alpha())
        
        return base_color

    def update_geometry(self) -> None:
        """Update the trace geometry (alias for update_path)."""
        self.update_path()

    def update_path(self) -> None:
        """Recalculate and update the bezier path between source and target."""
        src = self.source
        if hasattr(src, 'get_visual_target'):
             src = src.get_visual_target()
        
        if hasattr(src, 'get_scene_center'):
            p_src = src.get_scene_center()
        else:
            p_src = src.scenePos()

        if self.target:
            dst = self.target
            if hasattr(dst, 'get_visual_target'):
                dst = dst.get_visual_target()
            if hasattr(dst, 'get_scene_center'):
                p_dst = dst.get_scene_center()
            else:
                p_dst = dst.scenePos()
        else:
            p_dst = self.drag_pos

        if p_src == self._last_src_pos and p_dst == self._last_dst_pos:
            return

        self._last_src_pos = p_src
        self._last_dst_pos = p_dst

        dx = p_dst.x() - p_src.x()
        dy = p_dst.y() - p_src.y()
        dist = math.hypot(dx, dy)
        ctrl_dist = min(dist * 0.5, 150.0)
        if abs(dx) < 50 and abs(dy) > 50:
            ctrl_dist = max(ctrl_dist, 50.0)

        cp1 = QPointF(p_src.x() + ctrl_dist, p_src.y())
        cp2 = QPointF(p_dst.x() - ctrl_dist, p_dst.y())

        path = QPainterPath()
        path.moveTo(p_src)
        path.cubicTo(cp1, cp2, p_dst)
        self.setPath(path)

    def remove_from_scene(self, trigger_compute: bool = True) -> None:
        """Remove this trace from the scene and unregister from ports.
        
        Args:
            trigger_compute: Whether to mark the downstream node dirty.
                             Pass False during mid-drag detachment so compute
                             only fires when the user finalises the disconnect.
        """
        # Unregister from StyleManager
        self._style_manager.unregister(self, StyleCategory.TRACE)
        
        # Delegate port unregistration, scene removal, and compute
        # triggering to ConnectionFactory (mirrors create()).
        ConnectionFactory.remove(self, trigger_compute=trigger_compute)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        """
        Paint the trace with shadow, outline, and main line.
        
        The trace color is determined during pen setup and matches the port's blended color.
        No additional overlay blending is applied during painting.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = self.path()
        if not path:
            return
        
        # === RENDERING ORDER: Shadow -> Outline -> Main Trace ===
        
        # 1. Draw Shadow (if enabled)
        if self._shadow_pen:
            painter.setPen(self._shadow_pen)
            shadow_path = path.translated(self._shadow_offset)
            painter.drawPath(shadow_path)

        # 2. Draw Outline (if enabled) 
        if self._outline_pen:
            painter.setPen(self._outline_pen)
            painter.drawPath(path)

        # 3. Draw Main Trace with pre-blended color from port
        painter.setPen(self._main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class DragTrace(QGraphicsPathItem):
    """
    Temporary visual trace during connection dragging.
    Uses drag_ prefixed keys from StyleManager's TRACE category.
    
    Integrated with StyleManager for centralized styling.
    Now supports complete state overlay rendering as described in the issue fix.
    """
    __slots__ = (
        "start_pos", "end_pos", "_start_sign", "_local_style", "_style_manager",
        "_main_pen", "_outline_pen", "_shadow_pen", "_shadow_offset"
    )

    def __init__(
        self,
        start_port: NodePort,
        start_pos: QPointF,
        color: Optional[QColor] = None, 
        style: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()

        self.start_pos = start_pos
        self.end_pos = start_pos
        
        # Use PortUtils.get_direction_sign instead of duplicate _detect_port_direction
        self._start_sign = PortUtils.get_direction_sign(start_port)

        self.setZValue(100)
        self.setAcceptHoverEvents(False)
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, False)

        # Get StyleManager instance
        self._style_manager = StyleManager.instance()

        self._local_style = {}
        if style:
            self._local_style = style
        if color:
            self._local_style["drag_color"] = QColor(color)

        # Initialize pen attributes
        self._main_pen = QPen()
        self._outline_pen = None
        self._shadow_pen = None
        self._shadow_offset = QPointF(0, 0)

        self.refresh_style()
        
        # Register for style change notifications
        self._style_manager.register(self, StyleCategory.TRACE)

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        """
        Callback method called when StyleManager notifies about style changes.
        
        Args:
            category: The style category that changed (should be StyleCategory.TRACE)
            changes: Dictionary of changed keys and their new values
        """
        if category == StyleCategory.TRACE:
            # Check if any drag-related keys changed
            drag_keys = {'drag_width', 'drag_style', 'drag_color', 'drag_cap_style',
                        'drag_join_style', 'drag_outline_width', 'drag_outline_color',
                        'drag_shadow_enable', 'drag_shadow_color', 
                        'drag_shadow_offset_x', 'drag_shadow_offset_y'}
            
            if drag_keys.intersection(changes.keys()):
                self.refresh_style()

    def _get_trace_config(self) -> Dict[str, Any]:
        """
        Get the current trace configuration from StyleManager.
        
        Returns:
            A dictionary of trace styling parameters.
        """
        return self._style_manager.get_all(StyleCategory.TRACE)

    def refresh_style(self) -> None:
        """
        Resolves style by checking:
        1. Local 'drag_' override -> 2. Global 'drag_' key -> 3. Global standard key
        """
        global_config = self._get_trace_config()
        
        # Create a resolved config specifically for this drag instance
        resolved = {}
        
        # Helper to pick the right value
        def resolve_key(base_key: str, drag_key: str):
            # 1. Check local instance overrides (passed in __init__)
            if drag_key in self._local_style: 
                return self._local_style[drag_key]
            if base_key in self._local_style: 
                return self._local_style[base_key]
            
            # 2. Check global config from StyleManager
            if drag_key in global_config: 
                return global_config[drag_key]
            return global_config.get(base_key)

        # Map the specific drag keys to the standard keys the pen expects
        resolved["width"] = resolve_key("width", "drag_width")
        resolved["color"] = resolve_key("color", "drag_color")
        resolved["style"] = resolve_key("style", "drag_style")
        resolved["cap_style"] = resolve_key("cap_style", "drag_cap_style")
        resolved["join_style"] = resolve_key("join_style", "drag_join_style")
        
        # Shadows / Outlines
        resolved["outline_width"] = resolve_key("outline_width", "drag_outline_width")
        resolved["outline_color"] = resolve_key("outline_color", "drag_outline_color")
        resolved["shadow_enable"] = resolve_key("shadow_enable", "drag_shadow_enable")
        resolved["shadow_color"] = resolve_key("shadow_color", "drag_shadow_color")
        resolved["shadow_offset_x"] = resolve_key("shadow_offset_x", "drag_shadow_offset_x")
        resolved["shadow_offset_y"] = resolve_key("shadow_offset_y", "drag_shadow_offset_y")

        self._setup_pens(resolved)

    def _setup_pens(self, config: Dict[str, Any]) -> None:
        """Sets up main, outline, and shadow pens using resolved config."""
        # 1. Main Pen
        width = config.get("width", 2.0)
        c = QColor(config.get("color", QColor(255, 255, 255, 200)))
        
        self._main_pen = QPen(c, width)
        self._main_pen.setStyle(config.get("style", Qt.PenStyle.DashLine))
        self._main_pen.setCapStyle(config.get("cap_style", Qt.PenCapStyle.RoundCap))
        self._main_pen.setJoinStyle(config.get("join_style", Qt.PenJoinStyle.RoundJoin))

        # 2. Outline Pen
        o_width = config.get("outline_width", 0.0)
        if o_width and o_width > 0:
            o_color = QColor(config.get("outline_color", QColor(0, 0, 0, 50)))
            total_width = width + (o_width * 2.0)
            self._outline_pen = QPen(o_color, total_width)
            self._outline_pen.setStyle(self._main_pen.style())
            self._outline_pen.setCapStyle(self._main_pen.capStyle())
            self._outline_pen.setJoinStyle(self._main_pen.joinStyle())
        else:
            self._outline_pen = None

        # 3. Shadow Pen
        if config.get("shadow_enable", False):
            s_color = QColor(config.get("shadow_color", QColor(0, 0, 0, 100)))
            self._shadow_pen = QPen(s_color, width + 1.0)
            self._shadow_pen.setStyle(self._main_pen.style())
            self._shadow_pen.setCapStyle(self._main_pen.capStyle())
            self._shadow_pen.setJoinStyle(self._main_pen.joinStyle())
            
            off_x = float(config.get("shadow_offset_x", 1.5))
            off_y = float(config.get("shadow_offset_y", 2.5))
            self._shadow_offset = QPointF(off_x, off_y)
        else:
            self._shadow_pen = None
            self._shadow_offset = QPointF(0, 0)

        self.setPen(self._main_pen)

    def set_state_overlay(self, overlay_color):
        """
        Triggered when the port's overlay changes (compatibility method).
        
        DragTrace typically doesn't use overlays, but this is kept for compatibility.
        
        Args:
            overlay_color: QColor or Qt.GlobalColor.transparent (for compatibility)
        """
        # DragTrace doesn't use overlay - just refresh style if needed
        pass


    def update_position(self, end_pos: QPointF) -> None:
        """Update the end position of the drag trace."""
        if self.end_pos == end_pos:
            return
        self.end_pos = end_pos
        self.update_path()

    def update_path(self) -> None:
        """Recalculate and update the bezier path."""
        p_src = self.start_pos
        p_dst = self.end_pos

        dx = p_dst.x() - p_src.x()
        dy = p_dst.y() - p_src.y()
        dist = math.hypot(dx, dy)
        ctrl_dist = min(dist * 0.5, 150.0)
        if abs(dx) < 50.0 and abs(dy) > 50.0:
            ctrl_dist = max(ctrl_dist, 50.0)

        src_sign = self._start_sign
        cp1 = QPointF(p_src.x() + src_sign * ctrl_dist, p_src.y())
        cp2 = QPointF(p_dst.x() - src_sign * ctrl_dist, p_dst.y())

        path = QPainterPath()
        path.moveTo(p_src)
        path.cubicTo(cp1, cp2, p_dst)
        self.setPath(path)

    def remove_from_scene(self) -> None:
        """Remove this drag trace from the scene and unregister from StyleManager."""
        self._style_manager.unregister(self, StyleCategory.TRACE)
        scene = self.scene()
        if scene:
            scene.removeItem(self)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        """
        Paint the drag trace with shadow, outline, and main line.
        
        The trace color is determined during pen setup and matches the port's blended color.
        No additional overlay blending is applied during painting.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = self.path()
        if not path:
            return
        
        # === RENDERING ORDER: Shadow -> Outline -> Main Trace ===
        
        # 1. Draw Shadow (if enabled)
        if self._shadow_pen:
            painter.setPen(self._shadow_pen)
            shadow_path = path.translated(self._shadow_offset)
            painter.drawPath(shadow_path)

        # 2. Draw Outline (if enabled) 
        if self._outline_pen:
            painter.setPen(self._outline_pen)
            painter.drawPath(path)

        # 3. Draw Main Trace with pre-blended color
        painter.setPen(self._main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


# =============================================================================

class _TraceStyleProxy:
    """
    Backward compatibility proxy for DEFAULT_TRACE_STYLE.
    Redirects all access to StyleManager.
    """
    def __getitem__(self, key: str) -> Any:
        return StyleManager.instance().get(StyleCategory.TRACE, key)
    
    def __setitem__(self, key: str, value: Any) -> None:
        StyleManager.instance().update(StyleCategory.TRACE, **{key: value})
    
    def get(self, key: str, default: Any = None) -> Any:
        return StyleManager.instance().get(StyleCategory.TRACE, key, default)
    
    def update(self, new_config: Dict[str, Any]) -> None:
        StyleManager.instance().update(StyleCategory.TRACE, **new_config)
    
    def copy(self) -> Dict[str, Any]:
        return StyleManager.instance().get_all(StyleCategory.TRACE)
    
    def __repr__(self) -> str:
        return f"_TraceStyleProxy -> StyleManager.TRACE"


# Backward compatibility: DEFAULT_TRACE_STYLE now proxies to StyleManager
DEFAULT_TRACE_STYLE = _TraceStyleProxy()