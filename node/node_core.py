# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Node class - Principal node for the QGraphicsScene.

Composes functionality from three mixins:
- NodeConfigMixin:   Configuration, color management, StyleManager integration
- NodePortsMixin:    Port CRUD, content widget, state management
- NodeGeometryMixin: Geometry, layout, path caching, animation

This file retains:
- __init__ (wiring all mixins together)
- Serialization (get_state / restore_state)
- Interaction events (itemChange, hover, selection)
- paint() — the main rendering method
"""

import sys
import math
import uuid
from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QApplication, QGraphicsObject, QGraphicsView, QGraphicsScene,
    QStyleOptionGraphicsItem, QGraphicsItem, QWidget, QLabel, QStyle
)
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QVariantAnimation, QEasingCurve, QTimer, Signal
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QFont, QBrush
)

# Import node components (unchanged from original)
from weave.node.node_components import NodeBody, NodeHeader
from weave.node.node_subcomponents import (
   NodeState, highlight_colors, ResizeHandle
)

from weave.node.node_port import NodePort

# Import style manager for integration
from weave.stylemanager import StyleManager, StyleCategory

# Import mixins
from weave.node.node_config_mixin import NodeConfigMixin
from weave.node.node_ports_mixin import NodePortsMixin
from weave.node.node_geometry_mixin import NodeGeometryMixin

from weave.logger import get_logger
log = get_logger("Node")


class Node(NodeConfigMixin, NodePortsMixin, NodeGeometryMixin, QGraphicsObject):
    """
    Principal node class for the QGraphicsScene.
    Handles visual representation, state transitions, and port management.

    Integrated with StyleManager for centralized styling (no backward compatibility).
    """

    # Signal emitted when state changes (old_state, new_state)
    state_changed = Signal(object, object)

    # Signal emitted when geometry changes significantly
    geometry_changed = Signal()

    def __init__(self, title: str = "Base Node", config: Optional[Dict[str, Any]] = None):
        """
        Initialize Node with optional configuration.

        Args:
            title (str): The node's title text
            config (dict, optional): Configuration dictionary for styling
        """
        super().__init__()

        # 1. Configuration & State Setup
        self._config = StyleManager.instance().get_all(StyleCategory.NODE)
        self._port_config = StyleManager.instance().get_all(StyleCategory.PORT)
        
        if config:
            self.set_config(strict=False, **config)

        self._state: NodeState = NodeState.NORMAL
        self._width = self._config['width']
        self._total_height = self._config['min_height']
        self._stored_height = self._config['min_height']

        self.is_minimized = False
        self._is_hovered = False

        # Custom color attributes (no longer using fallbacks)
        self._custom_header_bg = None
        self._custom_header_outline = None
        self._custom_body_bg = None
        self._custom_body_outline = None

        # Initialize cache containers
        self._cached_rect = QRectF()
        self._cached_outline_path = QPainterPath()
        self._cached_glow_path = QPainterPath()
        self._cached_sel_path = QPainterPath()
        self._cached_hover_path = QPainterPath()
        self._cached_computing_glow_path = QPainterPath()

        # Overlay color attribute
        self._overlay_color = QColor(0, 0, 0, 0)

        # Computing pulse state
        self._computing_pulse_phase: float = 0.0

        # 2. UI Components
        self.header = NodeHeader(self, title)
        self.body = NodeBody(self)
        self.handle = ResizeHandle(self, self._on_handle_resize)

        self.header.setPos(0, 0)
        self.body.setPos(0, self.header.get_height())

        # Ports
        self.inputs: List[NodePort] = []
        self.outputs: List[NodePort] = []

        # Summary Ports (for minimized state)
        self._summary_input = None
        self._summary_output = None

        # 3. Graphics Flags
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

        # 4. Animation Logic - simplified
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self._config['minimize_anim_duration'])
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._anim.valueChanged.connect(self._on_anim_value_changed)
        self._anim.finished.connect(self._on_anim_finished)

        # Computing Pulse Animation (looping 0.0 → 1.0 → 0.0)
        self._computing_pulse_anim = QVariantAnimation(self)
        self._computing_pulse_anim.setStartValue(0.0)
        self._computing_pulse_anim.setEndValue(1.0)
        self._computing_pulse_anim.setDuration(1200)
        self._computing_pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._computing_pulse_anim.setLoopCount(-1)
        self._computing_pulse_anim.valueChanged.connect(self._on_computing_pulse_tick)

        # 5. Initial Layout & Colors
        self._update_colors(is_selected=False)

        # Deferred refinement
        QTimer.singleShot(25, self._sync_to_widget_size)

        # Initialize summary ports after all setup
        self._initialize_summary_ports()

        # 6. Node UUID - NEW: Add unique identifier for this node instance
        self._node_uuid = uuid.uuid4()

    # ==================================================================
    # UUID METHODS (ADDED)
    # ==================================================================

    def get_uuid(self) -> uuid.UUID:
        """
        Get the unique identifier for this node.
        
        This UUID provides a persistent way to identify nodes across their lifetime,
        even if other attributes like title or position change.
        
        Returns:
            A uuid.UUID object that uniquely identifies this node instance.
        """
        return self._node_uuid
    
    def get_uuid_string(self) -> str:
        """
        Get the unique identifier for this node as a string representation.
        
        This is useful for serialization, logging, and other string-based operations
        where working with UUID objects directly might be cumbersome.
        
        Returns:
            A string representation of the node's UUID.
        """
        return str(self._node_uuid)

    # ==================================================================
    # SERIALIZATION
    # ==================================================================

    def get_state(self) -> Dict[str, Any]:
        """
        Serializes GUI-only state: position, geometry, colors, ports, visual state.

        Widget/dataflow state is NOT handled here.
        BaseControlNode.get_state() extends this with widget_data and dataflow metadata.
        """
        inputs_state = [p.get_state() for p in self.inputs]
        outputs_state = [p.get_state() for p in self.outputs]

        colors = {
            "header_bg": self._custom_header_bg.name() if self._custom_header_bg else None,
            "body_bg": self._custom_body_bg.name() if self._custom_body_bg else None,
            "header_outline": self._custom_header_outline.name() if self._custom_header_outline else None,
            "body_outline": self._custom_body_outline.name() if self._custom_body_outline else None,
        }

        return {
            "title": self.header._title.toPlainText(),
            "width": self._width,
            "height": self._total_height,
            "pos": (self.pos().x(), self.pos().y()),
            "config": self._config.copy(),
            "colors": colors,
            "inputs": inputs_state,
            "outputs": outputs_state,
            "minimized": self.is_minimized,
            "node_state": self._state.value,
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restores GUI-only state from a dictionary.

        Widget/dataflow state is NOT handled here.
        BaseControlNode.restore_state() calls super() then restores widget_data.
        """
        was_visible = self.isVisible()
        self.setVisible(False)

        # 1. Apply Configuration (directly, no side-effects)
        if "config" in state:
            for k, v in state["config"].items():
                if k in self._config:
                    self._config[k] = v

        # 2. Geometry & Title
        self.header._title.setPlainText(state.get("title", "Node"))
        if self.scene():
            self.prepareGeometryChange()
        self._width = state.get("width", 200)
        self._total_height = state.get("height", 100)

        self.header.set_width(self._width)
        pos = state.get("pos", [0, 0])
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            self.setPos(pos[0], pos[1])
        else:
            self.setPos(0, 0)

        # 3. Restore Colors
        colors = state.get("colors", {})

        def _parse_col(val):
            """Convert color value to QColor using StyleManager's utilities."""
            if not val:
                return None
            try:
                # If it's already a QColor or compatible with QColor constructor, use directly
                return QColor(val)
            except Exception:
                # Fallback: Return transparent black on error
                return QColor(0, 0, 0, 0)

        self.set_node_colors(
            header_bg=_parse_col(colors.get("header_bg")),
            body_bg=_parse_col(colors.get("body_bg")),
            outline=_parse_col(colors.get("header_outline")),
        )
        if colors.get("body_outline"):
            self._custom_body_outline = QColor(colors["body_outline"])
            self._update_colors(self.isSelected())

        # 4. Rebuild Ports
        self.clear_ports()
        for p_data in state.get("inputs", []):
            self.add_input(
                p_data["name"],
                p_data.get("datatype", "flow"),
                p_data.get("description", p_data.get("desc", "")),
            )
        for p_data in state.get("outputs", []):
            self.add_output(
                p_data["name"],
                p_data.get("datatype", "flow"),
                p_data.get("description", p_data.get("desc", "")),
            )

        # 5. Node Visual State
        if "node_state" in state:
            try:
                self.set_state(NodeState(state["node_state"]))
            except (ValueError, TypeError):
                log.error("Invalid node_state, defaulting to NORMAL")
                self.set_state(NodeState.NORMAL)

        # 6. Minimized State
        if state.get("minimized", False):
            self.is_minimized = True
            self._total_height = self.header.get_height()
            self._set_ports_visible(False)
            self.body.setVisible(False)
            self.handle.setVisible(False)
            self._summary_input.setVisible(True)
            self._summary_output.setVisible(True)
            if hasattr(self.header, 'sync_minimize_button'):
                self.header.sync_minimize_button(True)

        # 7. Full visual refresh
        if hasattr(self, 'header'):
            self.header._recalculate_layout()
            self.header._title.update_selection_style(self.isSelected())

        self._update_colors(self.isSelected())
        self.enforce_min_dimensions()
        self._recalculate_paths()
        self.update_geometry()
        self.update()
        if hasattr(self, 'header'):
            self.header.update()
        if hasattr(self, 'body'):
            self.body.update()

        self.setVisible(was_visible)

    # ==================================================================
    # INTERACTION
    # ==================================================================

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        """Handles item changes including selection and position handling."""
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self._handle_selection_change(bool(value))
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        """Handles hover enter."""
        self._is_hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave."""
        self._is_hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    # ==================================================================
    # PAINTING
    # ==================================================================

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None):
        """Paints the node (shadows, state overlays, selection)."""
        cfg = self._config

        # ==================================================================
        # 1. DROP SHADOW
        # ==================================================================
        if cfg['shadow_enabled']:
            dist = cfg['shadow_offset']
            angle_deg = cfg['shadow_angle']
            angle_rad = math.radians(angle_deg)
            dx = dist * math.cos(angle_rad)
            dy = dist * math.sin(angle_rad)

            painter.save()
            painter.translate(dx, dy)

            # A. Draw Solid Core
            shadow_color = QColor(0, 0, 0, cfg['shadow_opacity'])
            painter.setBrush(shadow_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(self._cached_outline_path)

            # B. Blur / Soft Edge
            blur_rad = cfg['shadow_blur_radius']
            layers = cfg['shadow_blur_layers']
            if blur_rad > 0 and layers > 0:
                margin = blur_rad * 2
                clip_path = QPainterPath()
                clip_path.addRect(self._cached_rect.adjusted(-margin, -margin, margin, margin))
                clip_path.addPath(self._cached_outline_path)
                clip_path.setFillRule(Qt.FillRule.OddEvenFill)

                painter.setClipPath(clip_path)

                blur_color = QColor(0, 0, 0)
                blur_color.setAlpha(cfg['shadow_blur_opacity'])
                painter.setBrush(Qt.BrushStyle.NoBrush)

                for i in range(layers):
                    progress = (layers - i) / layers
                    pen_width = (blur_rad * 2) * progress

                    pen = QPen(blur_color, pen_width)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)

                    painter.setPen(pen)
                    painter.drawPath(self._cached_outline_path)

            painter.restore()

        # ==================================================================
        # 2. STATE VISUALS
        # ==================================================================
        if isinstance(self._overlay_color, QColor) and self._overlay_color.alpha() > 0:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._overlay_color)
            painter.drawPath(self._cached_outline_path)
            painter.restore()

        # ==================================================================
        # 3. GLOW & BORDER LOGIC
        # ==================================================================
        is_selected = (option.state & QStyle.State.State_Selected)

        draw_glow = False
        glow_color = None
        glow_width = 0.0
        glow_opacity = 0
        current_glow_path = None

        if is_selected and cfg['sel_glow_enabled']:
            draw_glow = True
            current_glow_path = self._cached_glow_path
            glow_width = cfg['sel_glow_width']
            glow_layers = cfg['sel_glow_layers']
            glow_opacity = cfg['sel_glow_opacity_start']

            if cfg['use_header_color_for_glow']:
                glow_color = QColor(self.header._bg_color)
            else:
                glow_color = QColor(cfg['sel_border_color'])

        elif self._is_hovered and not is_selected and cfg['hover_glow_enabled']:
            draw_glow = True
            current_glow_path = self._cached_hover_path
            glow_width = cfg['hover_glow_width']
            glow_layers = cfg['hover_glow_layers']
            glow_opacity = cfg['hover_glow_opacity_start']

            if cfg['use_header_color_for_hover_glow']:
                glow_color = QColor(self.header._bg_color)
            else:
                glow_color = QColor(cfg['hover_glow_color'])

        # --- DRAW GLOW ---
        if draw_glow and glow_color and current_glow_path:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(Qt.PenStyle.NoPen)

            for i in range(glow_layers):
                progress = (glow_layers - i) / glow_layers
                pen_width = glow_width * progress

                glow_color.setAlpha(glow_opacity)

                pen = QPen(glow_color, pen_width)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)

                painter.drawPath(current_glow_path)

        # --- DRAW SELECTION BORDER ---
        if is_selected:
            if cfg['use_header_color_for_outline']:
                border_base = self.header._bg_color
            else:
                border_base = cfg['sel_border_color']

            pen = QPen(border_base, cfg['sel_border_width'])
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._cached_sel_path)

        # ==================================================================
        # 4. COMPUTING PULSE GLOW
        # ==================================================================
        if self._computing_pulse_phase > 0.001:
            phase = self._computing_pulse_phase

            pulse_base = QColor(self.header._bg_color)

            pulse_glow_width_min = cfg['computing_glow_width_min']
            pulse_glow_width_max = cfg['computing_glow_width_max']
            pulse_opacity_min = cfg['computing_glow_opacity_min']
            pulse_opacity_max = cfg['computing_glow_opacity_max']
            pulse_layers = cfg['computing_glow_layers']
            pulse_border_width = cfg['computing_border_width']
            pulse_border_opacity = cfg['computing_border_opacity']

            active_width = pulse_glow_width_min + phase * (pulse_glow_width_max - pulse_glow_width_min)
            active_opacity = int(pulse_opacity_min + phase * (pulse_opacity_max - pulse_opacity_min))

            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(pulse_layers):
                layer_progress = (pulse_layers - i) / pulse_layers
                pen_width = active_width * layer_progress

                layer_alpha = int(active_opacity * layer_progress)
                pulse_base.setAlpha(max(0, min(255, layer_alpha)))

                pen = QPen(pulse_base, pen_width)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawPath(self._cached_computing_glow_path)

            border_alpha = int(pulse_border_opacity * (0.5 + 0.5 * phase))
            pulse_base.setAlpha(max(0, min(255, border_alpha)))
            pen = QPen(pulse_base, pulse_border_width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._cached_sel_path)


# ==============================================================================
# TEST / DEMO
# ==============================================================================

if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)

    view = QGraphicsView()
    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, 800, 600)
    scene.setBackgroundBrush(QColor(30, 30, 30))
    setattr(scene, "grid_spacing", 20)
    setattr(scene, "snapping_enabled", True)

    view.setScene(scene)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Create Node
    node = Node("Gradient Node")
    node.setPos(200, 200)
    node.add_input("Data In", "flow", "Main data stream")
    node.add_output("Result", "float")
    node.add_input("Secondary", "flow")
    node.add_output("Log", "string")

    # Add Content
    content = QLabel("Enhanced Gradient\n& Header Line")
    content.setAlignment(Qt.AlignmentFlag.AlignCenter)
    node.set_content_widget(content)

    scene.addItem(node)

    view.resize(800, 600)
    view.show()

    sys.exit(app.exec())
