# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

import math
import uuid
from typing import Optional, Any, Dict
from PySide6.QtWidgets import QGraphicsPathItem, QWidget, QStyleOptionGraphicsItem
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from weave.node.node_port import NodePort
from weave.portutils import PortUtils, ConnectionFactory
from weave.stylemanager import StyleManager, StyleCategory


class TracePathMixin:
    """Mixin providing shared path calculation logic for traces."""
    
    def _calculate_path(
        self, 
        p_src: QPointF, 
        p_dst: QPointF, 
        connection_type: str, 
        start_sign: float = 1.0, 
        end_sign: float = -1.0
    ) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(p_src)

        if connection_type == "straight":
            # Initial 20px horizontal line for both start and end ports
            p1 = QPointF(p_src.x() + start_sign * 20.0, p_src.y())
            p2 = QPointF(p_dst.x() + end_sign * 20.0, p_dst.y())
            
            path.lineTo(p1)
            path.lineTo(p2)
            path.lineTo(p_dst)

        elif connection_type == "angular":
            # Initial 20px horizontal line for both ends, breaks halfway horizontally, turns vertically
            p1 = QPointF(p_src.x() + start_sign * 10.0, p_src.y())
            p2 = QPointF(p_dst.x() + end_sign * 10.0, p_dst.y())
            mid_x = (p1.x() + p2.x()) / 2.0
            
            path.lineTo(p1)
            path.lineTo(mid_x, p1.y())
            path.lineTo(mid_x, p2.y())
            path.lineTo(p2)
            path.lineTo(p_dst)

        else: # "bezier" (default)
            dist = math.hypot(p_dst.x() - p_src.x(), p_dst.y() - p_src.y())
            ctrl_dist = min(dist * 0.5, 150.0)
            if abs(p_dst.x() - p_src.x()) < 50.0 and abs(p_dst.y() - p_src.y()) > 50.0:
                ctrl_dist = max(ctrl_dist, 50.0)

            cp1 = QPointF(p_src.x() + start_sign * ctrl_dist, p_src.y())
            cp2 = QPointF(p_dst.x() + end_sign * ctrl_dist, p_dst.y())

            path.cubicTo(cp1, cp2, p_dst)
            
        return path


class NodeTrace(TracePathMixin, QGraphicsPathItem):
    """Visual representation of a finalized connection."""

    __slots__ = (
        'source', 'target', 'drag_pos',
        '_last_src_pos', '_last_dst_pos',
        '_local_style', '_style_manager',
        '_main_pen', '_outline_pen', '_shadow_pen', '_shadow_offset',
        '_source_uuid', '_target_uuid', '_connection_type',
        '_state_overlay_color',
    )

    def __init__(
        self,
        source_port: NodePort,
        dest_port: Optional[NodePort] = None,
        cursor_pos: Optional[QPointF] = None,
        style: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self.setZValue(-1)
        self.setAcceptHoverEvents(False)

        self.source = source_port
        self.target = dest_port
        self.drag_pos = cursor_pos if cursor_pos else QPointF(0, 0)

        # Use unified UUID extractor instead of direct _port_uuid access (§1)
        self._source_uuid = PortUtils.get_port_uuid(source_port)
        self._target_uuid = PortUtils.get_port_uuid(dest_port) if dest_port else None

        self._last_src_pos = QPointF()
        self._last_dst_pos = QPointF()

        self._style_manager = StyleManager.instance()
        self._local_style = dict(style) if style else {}

        self._main_pen = QPen()
        self._outline_pen = None
        self._shadow_pen = None
        self._shadow_offset = QPointF(0, 0)
        self._connection_type = "bezier" # Default type
        self._state_overlay_color = QColor(0, 0, 0, 0)

        self.refresh_style()
        self._register_connection()
        self.update_path()

        self._style_manager.register(self, StyleCategory.TRACE)

    def get_source_uuid(self) -> Optional[uuid.UUID]:
        """Get the UUID of the source port."""
        return self._source_uuid

    def get_target_uuid(self) -> Optional[uuid.UUID]:
        """Get the UUID of the target port."""
        return self._target_uuid

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        if category == StyleCategory.TRACE:
            self.refresh_style()

    def refresh_style(self) -> None:
        """Re-reads config from StyleManager and rebuilds pens."""
        config = self._style_manager.get_all(StyleCategory.TRACE)
        config.update(self._local_style)
        self._setup_pens(config)
        
        # Determine connection type (bezier, straight, angular)
        self._connection_type = config.get("connection_type", "bezier")

        # Invalidate the position cache so update_path() always redraws the
        # path with the new connection type, even when no node has moved.
        self._last_src_pos = QPointF()
        self._last_dst_pos = QPointF()

        self.update_path()
        self.update()

    def _setup_pens(self, config: Dict[str, Any]) -> None:
        """Builds main, outline, and shadow pens from config."""
        # 1. Main Pen — blend port colour with state overlay
        c = config.get("color")
        if c is None:
            base_color = getattr(self.source, 'color', QColor(200, 200, 200))
            c = self._blend_color_with_overlay(base_color, self._state_overlay_color)

        width = config.get("width", 3.0)
        self._main_pen = QPen(QColor(c), width)
        self._main_pen.setStyle(config.get("style", Qt.PenStyle.SolidLine))
        self._main_pen.setCapStyle(config.get("cap_style", Qt.PenCapStyle.RoundCap))
        self._main_pen.setJoinStyle(config.get("join_style", Qt.PenJoinStyle.RoundJoin))

        # 2. Outline Pen
        o_width = config.get("outline_width", 0.0)
        if o_width > 0:
            o_color = config.get("outline_color", QColor(0, 0, 0, 50))
            self._outline_pen = QPen(QColor(o_color), width + (o_width * 2.0))
            self._outline_pen.setStyle(self._main_pen.style())
            self._outline_pen.setCapStyle(self._main_pen.capStyle())
            self._outline_pen.setJoinStyle(self._main_pen.joinStyle())
        else:
            self._outline_pen = None

        # 3. Shadow Pen
        if config.get("shadow_enable", False):
            self._shadow_pen = QPen(QColor(config.get("shadow_color", QColor(0, 0, 0, 100))), width + 1.0)
            self._shadow_pen.setStyle(self._main_pen.style())
            self._shadow_pen.setCapStyle(self._main_pen.capStyle())
            self._shadow_pen.setJoinStyle(self._main_pen.joinStyle())
            self._shadow_offset = QPointF(
                float(config.get("shadow_offset_x", 3.0)),
                float(config.get("shadow_offset_y", 3.0)),
            )
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
        self._target_uuid = PortUtils.get_port_uuid(port)
        if hasattr(self.target, 'add_trace'):
            self.target.add_trace(self)
        self.update_path()

    def set_state_overlay(self, overlay_color) -> None:
        """Store the state overlay and rebuild pens to reflect the new colour.

        The overlay's alpha also controls the trace's scene opacity so that
        traces emanating from DISABLED / PASSTHROUGH nodes visually fade
        to match the node body.
        """
        if isinstance(overlay_color, QColor):
            self._state_overlay_color = QColor(overlay_color)
        else:
            self._state_overlay_color = QColor(0, 0, 0, 0)

        self.refresh_style()

    def _blend_color_with_overlay(self, base_color: QColor, overlay_color) -> QColor:
        """
        Alpha-composite *overlay_color* over *base_color*.

        Uses the overlay's alpha channel as the blend factor so the result
        matches the visual intensity of the QPainter-drawn overlay on the
        node body.  The base colour's own alpha is preserved.
        """
        if not isinstance(overlay_color, QColor) or overlay_color.alpha() == 0:
            return base_color

        factor = overlay_color.alphaF()
        inv = 1.0 - factor

        r = int(base_color.red()   * inv + overlay_color.red()   * factor)
        g = int(base_color.green() * inv + overlay_color.green() * factor)
        b = int(base_color.blue()  * inv + overlay_color.blue()  * factor)

        return QColor(r, g, b, base_color.alpha())

    def update_geometry(self) -> None:
        """Alias for update_path."""
        self.update_path()

    def update_path(self) -> None:
        """Recalculate and update the path between source and target based on connection type."""
        src = self.source
        if hasattr(src, 'get_visual_target'):
            src = src.get_visual_target()

        p_src = src.get_scene_center() if hasattr(src, 'get_scene_center') else src.scenePos()

        if self.target:
            dst = self.target
            if hasattr(dst, 'get_visual_target'):
                dst = dst.get_visual_target()
            p_dst = dst.get_scene_center() if hasattr(dst, 'get_scene_center') else dst.scenePos()
        else:
            p_dst = self.drag_pos

        if p_src == self._last_src_pos and p_dst == self._last_dst_pos:
            return

        self._last_src_pos = p_src
        self._last_dst_pos = p_dst

        start_sign = PortUtils.get_direction_sign(self.source)
        if self.target:
            end_sign = PortUtils.get_direction_sign(self.target)
        else:
            end_sign = -start_sign

        path = self._calculate_path(p_src, p_dst, self._connection_type, start_sign, end_sign)
        self.setPath(path)

    def remove_from_scene(self, trigger_compute: bool = True) -> None:
        """Remove this trace from the scene and unregister from ports.

        Args:
            trigger_compute: Whether to mark the downstream node dirty.
                             Pass False during mid-drag detachment so compute
                             only fires when the user finalises the disconnect.
        """
        self._style_manager.unregister(self, StyleCategory.TRACE)
        ConnectionFactory.remove(self, trigger_compute=trigger_compute)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = self.path()
        if not path:
            return

        # Fade trace when its source node has a state overlay (DISABLED,
        # PASSTHROUGH, …).  The overlay alpha drives a proportional opacity
        # reduction so the trace dims together with the node body.
        if self._state_overlay_color.alpha() > 0:
            opacity = max(0.35, 1.0 - self._state_overlay_color.alphaF() * 0.5)
            painter.setOpacity(opacity)

        # Shadow -> Outline -> Main
        if self._shadow_pen:
            painter.setPen(self._shadow_pen)
            painter.drawPath(path.translated(self._shadow_offset))

        if self._outline_pen:
            painter.setPen(self._outline_pen)
            painter.drawPath(path)

        painter.setPen(self._main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class DragTrace(TracePathMixin, QGraphicsPathItem):
    """Temporary visual trace drawn while the user is dragging a new connection."""

    __slots__ = (
        "start_pos", "end_pos", "_start_sign", "_local_style", "_style_manager",
        "_main_pen", "_outline_pen", "_shadow_pen", "_shadow_offset", "_connection_type"
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
        self._start_sign = PortUtils.get_direction_sign(start_port)

        self.setZValue(100)
        self.setAcceptHoverEvents(False)
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, False)

        self._style_manager = StyleManager.instance()

        self._local_style = dict(style) if style else {}
        if color:
            self._local_style["drag_color"] = QColor(color)

        self._main_pen = QPen()
        self._outline_pen = None
        self._shadow_pen = None
        self._shadow_offset = QPointF(0, 0)
        self._connection_type = "bezier"

        self.refresh_style()
        # refresh_style() resets end_pos to QPointF(-1, -1) as a cache-busting
        # sentinel. Re-anchor it to start_pos so the trace doesn't draw to the
        # scene origin before the user moves the mouse.
        self.end_pos = start_pos
        self.update_path()
        self._style_manager.register(self, StyleCategory.TRACE)

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        if category == StyleCategory.TRACE:
            drag_keys = {
                'drag_width', 'drag_style', 'drag_color', 'drag_cap_style',
                'drag_join_style', 'drag_outline_width', 'drag_outline_color',
                'drag_shadow_enable', 'drag_shadow_color',
                'drag_shadow_offset_x', 'drag_shadow_offset_y',
                'connection_type', 'drag_connection_type'
            }
            if drag_keys.intersection(changes.keys()):
                self.refresh_style()

    def refresh_style(self) -> None:
        """
        Resolves style with priority:
        1. Local drag_ override  2. Global drag_ key  3. Global standard key
        """
        global_config = self._style_manager.get_all(StyleCategory.TRACE)

        def resolve(base_key: str, drag_key: str):
            if drag_key in self._local_style:
                return self._local_style[drag_key]
            if base_key in self._local_style:
                return self._local_style[base_key]
            if drag_key in global_config:
                return global_config[drag_key]
            return global_config.get(base_key)

        self._setup_pens({
            "width":           resolve("width",          "drag_width"),
            "color":           resolve("color",          "drag_color"),
            "style":           resolve("style",          "drag_style"),
            "cap_style":       resolve("cap_style",      "drag_cap_style"),
            "join_style":      resolve("join_style",     "drag_join_style"),
            "outline_width":   resolve("outline_width",  "drag_outline_width"),
            "outline_color":   resolve("outline_color",  "drag_outline_color"),
            "shadow_enable":   resolve("shadow_enable",  "drag_shadow_enable"),
            "shadow_color":    resolve("shadow_color",   "drag_shadow_color"),
            "shadow_offset_x": resolve("shadow_offset_x", "drag_shadow_offset_x"),
            "shadow_offset_y": resolve("shadow_offset_y", "drag_shadow_offset_y"),
            "connection_type": resolve("connection_type", "drag_connection_type"),
        })

        self._connection_type = resolve("connection_type", "drag_connection_type") or "bezier"

        # Invalidate the position cache so update_path() always redraws the
        # path with the new connection type, even when the cursor hasn't moved.
        self.end_pos = QPointF(-1, -1)

        self.update_path()

    def _setup_pens(self, config: Dict[str, Any]) -> None:
        """Builds main, outline, and shadow pens from resolved config."""
        # 1. Main Pen
        width = config.get("width", 2.0)
        self._main_pen = QPen(QColor(config.get("color", QColor(255, 255, 255, 200))), width)
        self._main_pen.setStyle(config.get("style", Qt.PenStyle.DashLine))
        self._main_pen.setCapStyle(config.get("cap_style", Qt.PenCapStyle.RoundCap))
        self._main_pen.setJoinStyle(config.get("join_style", Qt.PenJoinStyle.RoundJoin))

        # 2. Outline Pen
        o_width = config.get("outline_width", 0.0)
        if o_width > 0:
            o_color = QColor(config.get("outline_color", QColor(0, 0, 0, 50)))
            self._outline_pen = QPen(o_color, width + (o_width * 2.0))
            self._outline_pen.setStyle(self._main_pen.style())
            self._outline_pen.setCapStyle(self._main_pen.capStyle())
            self._outline_pen.setJoinStyle(self._main_pen.joinStyle())
        else:
            self._outline_pen = None

        # 3. Shadow Pen
        if config.get("shadow_enable", False):
            self._shadow_pen = QPen(QColor(config.get("shadow_color", QColor(0, 0, 0, 100))), width + 1.0)
            self._shadow_pen.setStyle(self._main_pen.style())
            self._shadow_pen.setCapStyle(self._main_pen.capStyle())
            self._shadow_pen.setJoinStyle(self._main_pen.joinStyle())
            self._shadow_offset = QPointF(
                float(config.get("shadow_offset_x", 1.5)),
                float(config.get("shadow_offset_y", 2.5)),
            )
        else:
            self._shadow_pen = None
            self._shadow_offset = QPointF(0, 0)

        self.setPen(self._main_pen)

    def set_state_overlay(self, overlay_color) -> None:
        pass

    def update_position(self, end_pos: QPointF) -> None:
        """Update the cursor end position of the drag trace."""
        if self.end_pos == end_pos:
            return
        self.end_pos = end_pos
        self.update_path()

    def update_path(self) -> None:
        """Recalculate and update the trace path based on connection type."""
        p_src = self.start_pos
        p_dst = self.end_pos

        path = self._calculate_path(p_src, p_dst, self._connection_type, self._start_sign, -self._start_sign)
        self.setPath(path)

    def remove_from_scene(self) -> None:
        """Remove this drag trace from the scene and unregister from StyleManager."""
        self._style_manager.unregister(self, StyleCategory.TRACE)
        scene = self.scene()
        if scene:
            scene.removeItem(self)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = self.path()
        if not path:
            return

        # Shadow -> Outline -> Main
        if self._shadow_pen:
            painter.setPen(self._shadow_pen)
            painter.drawPath(path.translated(self._shadow_offset))

        if self._outline_pen:
            painter.setPen(self._outline_pen)
            painter.drawPath(path)

        painter.setPen(self._main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)