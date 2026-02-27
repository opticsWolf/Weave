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


class NodeTrace(QGraphicsPathItem):
    """Visual representation of a finalized connection."""

    __slots__ = (
        'source', 'target', 'drag_pos',
        '_last_src_pos', '_last_dst_pos',
        '_local_style', '_style_manager',
        '_main_pen', '_outline_pen', '_shadow_pen', '_shadow_offset',
        '_source_uuid', '_target_uuid',
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

        self._source_uuid = getattr(source_port, '_port_uuid', None)
        self._target_uuid = getattr(dest_port, '_port_uuid', None) if dest_port else None

        self._last_src_pos = QPointF()
        self._last_dst_pos = QPointF()

        self._style_manager = StyleManager.instance()
        self._local_style = dict(style) if style else {}

        self._main_pen = QPen()
        self._outline_pen = None
        self._shadow_pen = None
        self._shadow_offset = QPointF(0, 0)

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
        self.update()

    def _setup_pens(self, config: Dict[str, Any]) -> None:
        """Builds main, outline, and shadow pens from config."""
        # 1. Main Pen
        c = config.get("color")
        if c is None:
            base_color = getattr(self.source, 'color', QColor(200, 200, 200))
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
        self._target_uuid = getattr(port, '_port_uuid', None)
        if hasattr(self.target, 'add_trace'):
            self.target.add_trace(self)
        self.update_path()

    def set_state_overlay(self, overlay_color) -> None:
        """Triggered when the source port's overlay changes; rebuilds pen color."""
        self.refresh_style()

    def _blend_color_with_overlay(self, base_color: QColor, overlay_color) -> QColor:
        """
        Blend overlay into base_color using the overlay's alpha as the blend factor.
        Returns base_color unchanged if overlay is transparent or absent.
        """
        if overlay_color is None or overlay_color == Qt.GlobalColor.transparent:
            return base_color

        if isinstance(overlay_color, QColor):
            if overlay_color.alpha() == 0:
                return base_color

            blend_factor = (overlay_color.alpha() / 255) / 1.5
            r = int(base_color.red()   * (1 - blend_factor) + overlay_color.red()   * blend_factor)
            g = int(base_color.green() * (1 - blend_factor) + overlay_color.green() * blend_factor)
            b = int(base_color.blue()  * (1 - blend_factor) + overlay_color.blue()  * blend_factor)
            return QColor(r, g, b, base_color.alpha())

        return base_color

    def update_geometry(self) -> None:
        """Alias for update_path."""
        self.update_path()

    def update_path(self) -> None:
        """Recalculate and update the bezier path between source and target."""
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
        self._style_manager.unregister(self, StyleCategory.TRACE)
        ConnectionFactory.remove(self, trigger_compute=trigger_compute)

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


class DragTrace(QGraphicsPathItem):
    """Temporary visual trace drawn while the user is dragging a new connection."""

    __slots__ = (
        "start_pos", "end_pos", "_start_sign", "_local_style", "_style_manager",
        "_main_pen", "_outline_pen", "_shadow_pen", "_shadow_offset",
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

        self.refresh_style()
        self._style_manager.register(self, StyleCategory.TRACE)

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        if category == StyleCategory.TRACE:
            drag_keys = {
                'drag_width', 'drag_style', 'drag_color', 'drag_cap_style',
                'drag_join_style', 'drag_outline_width', 'drag_outline_color',
                'drag_shadow_enable', 'drag_shadow_color',
                'drag_shadow_offset_x', 'drag_shadow_offset_y',
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
        })

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
        """Recalculate and update the bezier path."""
        p_src = self.start_pos
        p_dst = self.end_pos

        dist = math.hypot(p_dst.x() - p_src.x(), p_dst.y() - p_src.y())
        ctrl_dist = min(dist * 0.5, 150.0)
        if abs(p_dst.x() - p_src.x()) < 50.0 and abs(p_dst.y() - p_src.y()) > 50.0:
            ctrl_dist = max(ctrl_dist, 50.0)

        cp1 = QPointF(p_src.x() + self._start_sign * ctrl_dist, p_src.y())
        cp2 = QPointF(p_dst.x() - self._start_sign * ctrl_dist, p_dst.y())

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