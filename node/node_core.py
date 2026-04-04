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
from weave.node.node_components import NodeBody, NodeHeader, NodeWidgetHost
from weave.node.node_subcomponents import ResizeHandle
from weave.node.node_port import NodePort

from weave.node.node_enums import (
    NodeState, VerticalSizePolicy, DisabledBehavior, highlight_colors,
)

# Import style manager for integration
from weave.stylemanager import StyleManager, StyleCategory

# Import mixins
from weave.node.node_config_mixin import NodeConfigMixin
from weave.node.node_ports_mixin import NodePortsMixin
from weave.node.node_geometry_mixin import NodeGeometryMixin
from weave.node.node_pulse_anim_mixin import NodePulseAnimMixin
from weave.node.uuid_mixin import UUIDMixin  # <-- NEW IMPORT

from weave.logger import get_logger
log = get_logger("Node")


class Node(UUIDMixin, NodeConfigMixin, NodePortsMixin, NodePulseAnimMixin, NodeGeometryMixin, QGraphicsObject):
    """
    Principal node class for the QGraphicsScene.
    Handles visual representation, state transitions, and port management.

    Integrated with StyleManager for centralized styling (no backward compatibility).
    """

    # Signal emitted when state changes (old_state, new_state)
    state_changed = Signal(object, object)

    # Signal emitted when the title is edited by the user
    title_changed = Signal(str)

    # Signal emitted when geometry changes significantly
    geometry_changed = Signal()

    # Signal emitted after a port is fully removed (receives the detached NodePort)
    port_removed = Signal(object)

    # Signal emitted after a port is added (receives the new NodePort)
    port_added = Signal(object)

    def __init__(self, title: str = "Base Node", config: Optional[Dict[str, Any]] = None):
        """
        Initialize Node with optional configuration.

        Args:
            title (str): The node's title text
            config (dict, optional): Configuration dictionary for styling
        """
        # Call all mixin __init__ methods via MRO - this properly initializes UUIDMixin
        super().__init__()

        # Initialize UUID via mixin (early, so it's available immediately)
        self._init_uuid()

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

        # Vertical resize policy — controls whether the node shrinks when
        # content is removed (FIT) or only ever grows (GROW_ONLY).
        self._vertical_size_policy: VerticalSizePolicy = VerticalSizePolicy.GROW_ONLY

        # Custom color attributes (no longer using fallbacks)
        self._custom_header_bg = None
        self._custom_header_outline = None
        self._custom_body_bg = None
        self._custom_body_outline = None

        # Palette index for the header color.
        # Stored as an int (position in header_color_palette) so that when the
        # active theme changes, _reapply_header_color_from_index() can swap in the
        # equivalent color from the new palette.  None means "use theme default".
        self._header_color_index: Optional[int] = None

        # Initialize cache containers
        self._cached_rect = QRectF()
        self._cached_outline_path = QPainterPath()
        self._cached_glow_path = QPainterPath()
        self._cached_sel_path = QPainterPath()
        self._cached_hover_path = QPainterPath()
        self._cached_computing_glow_path = QPainterPath()

        # Overlay color attribute
        self._overlay_color = QColor(0, 0, 0, 0)

        # 2. UI Components
        self.header = NodeHeader(self, title)
        self.body = NodeBody(self)
        self.widget_host = NodeWidgetHost(self)
        self.handle = ResizeHandle(self)

        self.header.setPos(0, 0)
        self.body.setPos(0, self.header.get_height())
        self.widget_host.setPos(0, self.header.get_height())

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

        # Computing Pulse Animation (configurable via NodePulseAnimMixin)
        self._init_pulse_anim()

        # 5. Initial Layout & Colors
        self._update_colors(is_selected=False)

        # Deferred refinement
        QTimer.singleShot(25, self._sync_to_widget_size)

        self.setCacheMode(QGraphicsItem.NoCache)

        # Initialize summary ports after all setup
        self._initialize_summary_ports()

        # 6. Node UUID - NOW HANDLED BY UUIDMixin

    # ==================================================================
    # HEADER COLOR — INDEX-BASED API
    # ==================================================================

    def get_header_color_index(self) -> Optional[int]:
        """
        Return the currently active palette index for the header color.

        Returns None when the node is using the theme's default header color
        (i.e. no per-node override has been set).
        """
        return self._header_color_index

    def set_header_color_by_index(self, index: Optional[int]) -> None:
        """
        Set the node's header color via its position in the active theme's
        ``header_color_palette``.

        Storing an index rather than an absolute colour value means that when
        the active theme changes, ``_reapply_header_color_from_index()`` can
        substitute the equivalent colour from the new palette automatically.

        Passing ``None`` (or an out-of-range index) clears the per-node
        override so the node falls back to the theme's default header colour.

        Args:
            index: 0-based position in ``header_color_palette``, or None.
        """
        palette = self._config.get('header_color_palette') or []
        if index is not None and 0 <= index < len(palette):
            self._header_color_index = index
            raw = palette[index]
            if isinstance(raw, QColor):
                self._custom_header_bg = QColor(raw)
            else:
                r, g, b = int(raw[0]), int(raw[1]), int(raw[2])
                a = int(raw[3]) if len(raw) > 3 else 255
                self._custom_header_bg = QColor(r, g, b, a)
        else:
            # Out-of-range or explicit None → revert to theme default.
            self._header_color_index = None
            self._custom_header_bg = None

        self._update_colors(self.isSelected())
        if hasattr(self, 'header'):
            self.header.update()
        self.update()

    def _reapply_header_color_from_index(self) -> None:
        """
        Re-derive ``_custom_header_bg`` from ``_header_color_index`` against
        the *current* theme's palette.

        Must be called from ``on_style_changed()`` **after** ``self._config``
        has been refreshed from the StyleManager so that the new palette is
        available.

        Behaviour:
        - If ``_header_color_index`` is None → nothing to do (no override).
        - If the index is valid in the new palette → update ``_custom_header_bg``
          to the new palette's colour at that position.
        - If the index is out-of-range in the new palette → clear the override
          and reset ``_header_color_index`` to None (fall back to theme default).

        The caller is responsible for invoking ``_update_colors()`` afterwards.
        """
        if self._header_color_index is None:
            return  # No per-node override — nothing to do.

        palette = self._config.get('header_color_palette') or []
        if 0 <= self._header_color_index < len(palette):
            raw = palette[self._header_color_index]
            if isinstance(raw, QColor):
                self._custom_header_bg = QColor(raw)
            else:
                r, g, b = int(raw[0]), int(raw[1]), int(raw[2])
                a = int(raw[3]) if len(raw) > 3 else 255
                self._custom_header_bg = QColor(r, g, b, a)
        else:
            # The new theme's palette is shorter — gracefully reset to default.
            log.debug(
                f"Node '{self.header._title.toPlainText()}': header_color_index "
                f"{self._header_color_index} out of range for new theme palette "
                f"(len={len(palette)}). Reverting to theme default."
            )
            self._header_color_index = None
            self._custom_header_bg = None

    # ==================================================================
    # SERIALIZATION
    # ==================================================================

    def get_state(self) -> Dict[str, Any]:
        """
        Serializes GUI-only state, now including the persistent UUID.
        
        Returns:
            dict: State dictionary with node configuration and identity
        """
        inputs_state = [p.get_state() for p in self.inputs]
        outputs_state = [p.get_state() for p in self.outputs]

        # Use HexArgb (#AARRGGBB) so that alpha is preserved in the JSON.
        # Also store the palette index so that theme-switching can map the color
        # to the equivalent slot in a different theme's palette.
        def _col_name(c):
            return c.name(QColor.NameFormat.HexArgb) if c else None

        colors = {
            "header_bg": _col_name(self._custom_header_bg),
            "header_color_index": self._header_color_index,
            "body_bg": _col_name(self._custom_body_bg),
            "header_outline": _col_name(self._custom_header_outline),
            "body_outline": _col_name(self._custom_body_outline),
        }

        # Use toolTip() as the full-text source of truth: _recalculate_layout() always
        # stores the full (unelided) title there, while toPlainText() may be truncated
        # when the node is narrow.  Fall back to toPlainText() for brand-new nodes
        # whose toolTip has not been populated yet.
        _full_title = self.header._title.toolTip() or self.header._title.toPlainText()

        return {
            "id": self.unique_id,
            "title": _full_title,
            "width": self._width,
            "height": self._total_height,
            "pos": (self.pos().x(), self.pos().y()),
            "colors": colors,
            "inputs": inputs_state,
            "outputs": outputs_state,
            "minimized": self.is_minimized,
            "node_state": self._state.value,
            "vertical_size_policy": self._vertical_size_policy.value,
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restores GUI-only state from a dictionary including persistent UUID.
        
        Args:
            state (dict): State dictionary containing node configuration
        """
        was_visible = self.isVisible()
        self.setVisible(False)

        # 1. Restore Identity First - CRITICAL STEP
        if "id" in state:
            self.unique_id = state["id"]


        # NOTE: _config is not restored from the save file — style properties are
        # owned by the StyleManager (theme). Only the per-node custom color overrides
        # are restored below via set_node_colors().

        # 1. Geometry & Title
        # FIX: Restore the full title into BOTH setPlainText and setToolTip.
        # _recalculate_layout() (called at step 7) reads toolTip() as the full-text
        # source of truth for base nodes.  Without this, it finds an empty toolTip
        # and falls back to the stale toPlainText(), silently discarding the saved title.
        _title_text = state.get("title", "Node")
        self.header._title.setToolTip(_title_text)
        self.header._title.setPlainText(_title_text)

        # FIX: If the node exposes a `name` attribute (used by subclasses and read
        # by _recalculate_layout() with higher priority than toolTip()), update it
        # now so the layout pass doesn't overwrite the restored title with the
        # stale constructor value.
        if hasattr(self, "set_name") and callable(self.set_name):
            self.set_name(_title_text)
        elif hasattr(self, "name"):
            try:
                self.name = _title_text
            except (AttributeError, TypeError):
                pass  # read-only or method — leave it alone
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
                return QColor(val)
            except Exception:
                return QColor(0, 0, 0, 0)

        # Restore non-header custom colors unconditionally.
        body_bg      = _parse_col(colors.get("body_bg"))
        hdr_outline  = _parse_col(colors.get("header_outline"))
        body_outline = _parse_col(colors.get("body_outline"))

        # Header color: prefer the saved palette index.  This ensures that when the
        # file is loaded under a different active theme the correct equivalent color
        # from that theme's palette is used (and falls back to the theme default if
        # the index no longer exists in the new palette).
        header_color_index = colors.get("header_color_index")
        if header_color_index is not None:
            # set_header_color_by_index also calls _update_colors internally.
            self.set_header_color_by_index(header_color_index)
            # Apply remaining custom colors without touching the header.
            if body_bg or hdr_outline:
                self.set_node_colors(body_bg=body_bg, outline=hdr_outline)
        else:
            # Legacy path: header stored as a raw hex color value.
            self.set_node_colors(
                header_bg=_parse_col(colors.get("header_bg")),
                body_bg=body_bg,
                outline=hdr_outline,
            )

        if body_outline:
            self._custom_body_outline = body_outline
            self._update_colors(self.isSelected())

        # 4. Rebuild Ports
        self.clear_ports()
        for p_data in state.get("inputs", []):
            port = self.add_input(
                p_data["name"],
                p_data.get("datatype", "flow"),
                p_data.get("description", ""),
            )
            if p_data.get("auto_disable", False):
                port._auto_disable = True
            if not p_data.get("visible", True):
                port.setVisible(False)
                if hasattr(port, '_label') and port._label:
                    port._label.setVisible(False)

        for p_data in state.get("outputs", []):
            port = self.add_output(
                p_data["name"],
                p_data.get("datatype", "flow"),
                p_data.get("description", ""),
            )
            if not p_data.get("visible", True):
                port.setVisible(False)
                if hasattr(port, '_label') and port._label:
                    port._label.setVisible(False)

        # 5. Node Visual State
        if "node_state" in state:
            try:
                self.set_state(NodeState(state["node_state"]))
            except (ValueError, TypeError):
                log.error("Invalid node_state, defaulting to NORMAL")
                self.set_state(NodeState.NORMAL)

        # 5b. Vertical Size Policy
        if "vertical_size_policy" in state:
            try:
                self._vertical_size_policy = VerticalSizePolicy(state["vertical_size_policy"])
            except (ValueError, TypeError):
                self._vertical_size_policy = VerticalSizePolicy.GROW_ONLY

        # 6. Minimized State
        if state.get("minimized", False):
            self.is_minimized = True
            self._total_height = self.header.get_height()
            self._set_ports_visible(False)
            self.body.setVisible(False)
            if hasattr(self, 'widget_host') and self.widget_host is not None:
                self.widget_host.setVisible(False)
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
                # FIX: Clamp the blur clip to boundingRect() so no painted
                # pixels can ever land outside the region Qt invalidates
                # on movement.  We translate by (-dx, -dy) because the
                # painter is currently offset for the shadow position.
                clip_path = QPainterPath()
                clip_path.addRect(self.boundingRect().translated(-dx, -dy))
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
            self._paint_pulse(
                painter,
                #self._cached_computing_glow_path,
                self._cached_sel_path,
            )

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
