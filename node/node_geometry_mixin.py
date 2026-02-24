# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodeGeometryMixin - Geometry, Layout, Paths, and Animation for Node.

Handles:
- boundingRect / path caching (_recalculate_paths)
- Port stack height calculation
- Layout metrics and min-size enforcement
- Port layout positioning (input_rect / output_rect based)
- Minimize / maximize animation (toggle, value-changed, finished)
- Computing pulse animation (start / stop / tick)
- Resize handle callback
"""

from typing import Optional, List, Tuple, Any
from PySide6.QtCore import Qt, QRectF, QVariantAnimation
from PySide6.QtGui import QPainterPath

from weave.node.node_port import NodePort

from weave.logger import get_logger
log = get_logger("NodeGeometryMixin")


class NodeGeometryMixin:
    """
    Mixin providing geometry, layout, and animation for Node.

    Expects the host class to have:
        - self._config: Dict[str, Any]
        - self._width, self._total_height, self._stored_height: float
        - self.is_minimized: bool
        - self.header: NodeHeader (get_height, set_width, get_title_width, _recalculate_layout)
        - self.body: NodeBody (get_content_min_size, setPos, update_layout, setVisible)
        - self.handle: ResizeHandle (setPos, setVisible)
        - self.inputs, self.outputs: List[NodePort]
        - self._summary_input, self._summary_output: NodePort
        - self._anim: QVariantAnimation (minimize/maximize)
        - self._computing_pulse_anim: QVariantAnimation
        - self._computing_pulse_phase: float
        - self._cached_rect, _cached_outline_path, _cached_glow_path,
          _cached_sel_path, _cached_hover_path, _cached_computing_glow_path: Qt paths
        - self.geometry_changed: Signal
        - self.scene(), self.prepareGeometryChange(), self.update()
        - self._update_all_connected_traces()
        - self._set_ports_visible(visible: bool)
    """

    # ------------------------------------------------------------------
    # Bounding Rect
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return self._cached_rect

    # ------------------------------------------------------------------
    # Port Stack Height
    # ------------------------------------------------------------------

    def _calculate_port_stack_height(self, ports: List[NodePort]) -> float:
        """Calculates total height required for a stack of ports."""
        if not ports:
            return 0.0
        cfg = self._config
        margin = cfg.get('port_area_margin', 10)
        port_dia = cfg['port_radius'] * 2

        total_h = margin
        for p in ports:
            label_h = p.get_label_height() if hasattr(p, 'get_label_height') else port_dia
            row_h = max(port_dia, label_h)
            total_h += row_h + margin
        return total_h

    # ------------------------------------------------------------------
    # Layout Metrics
    # ------------------------------------------------------------------

    def _calculate_layout_metrics(self) -> Tuple[float, float, float, float]:
        """Calculates internal heights for top area, widget, and bottom area."""
        cfg = self._config

        h_in = self._calculate_port_stack_height(self.inputs)
        h_out = self._calculate_port_stack_height(self.outputs)
        area_h = max(h_in, h_out)

        if cfg.get('enable_port_area', False):
            if cfg.get('port_area_top', True):
                top_area_h = area_h
                bottom_area_h = 0.0
            else:
                top_area_h = 0.0
                bottom_area_h = area_h
        else:
            top_area_h = 0.0
            bottom_area_h = 0.0

        widget_min_w, widget_min_h = (
            self.body.get_content_min_size()
            if hasattr(self.body, 'get_content_min_size')
            else (0, 0)
        )

        max_in_w = max(
            (p.get_label_width() for p in self.inputs if hasattr(p, 'get_label_width')),
            default=0.0,
        )
        max_out_w = max(
            (p.get_label_width() for p in self.outputs if hasattr(p, 'get_label_width')),
            default=0.0,
        )
        min_middle_gap = 40.0
        required_port_width = max_in_w + min_middle_gap + max_out_w

        title_width = self.header.get_title_width() if hasattr(self.header, 'get_title_width') else 0
        final_min_w = max(cfg['min_width'], widget_min_w, required_port_width, title_width + 60)

        return top_area_h, widget_min_h, bottom_area_h, final_min_w

    def _calculate_expanded_min_size(self) -> Tuple[float, float]:
        """Calculates min size ignoring minimized state (used for restoration)."""
        top_h, widget_h, bot_h, min_w = self._calculate_layout_metrics()
        cfg = self._config
        padding = cfg.get('port_area_padding', 10)

        total_h = self.header.get_height()

        if top_h > 0:
            total_h += top_h + padding

        total_h += widget_h

        if bot_h > 0:
            total_h += padding + bot_h

        total_h += 10  # Extra bottom padding

        return min_w, total_h

    def calculate_min_size(self) -> Tuple[float, float]:
        """Calculates minimum size based on current state."""
        if self.is_minimized:
            title_min_w = (
                (self.header.get_title_width() if hasattr(self.header, 'get_title_width') else 0)
                + 80
            )
            return max(self._config['min_width'], title_min_w), self.header.get_height()
        return self._calculate_expanded_min_size()

    def enforce_min_dimensions(self):
        """Resizes node if current dimensions are too small."""
        min_w, min_h = self.calculate_min_size()
        new_w = max(self._width, min_w)
        new_h = max(self._total_height, min_h)

        if self.is_minimized:
            new_h = min_h

        if abs(new_w - self._width) > 0.1 or abs(new_h - self._total_height) > 0.1:
            if self.scene():
                self.prepareGeometryChange()
            self._width = new_w
            self._total_height = new_h
            if hasattr(self.header, 'set_width'):
                self.header.set_width(new_w)
            self._recalculate_paths()
            self.update_geometry()

    # ------------------------------------------------------------------
    # Geometry Update & Port Layout
    # ------------------------------------------------------------------

    def update_geometry(self):
        """Re-layouts the body, ports, and handles based on current width/height."""
        cfg = self._config
        header_h = self.header.get_height()

        top_h, widget_min_h, bot_h, _ = self._calculate_layout_metrics()
        padding = cfg.get('port_area_padding', 10)

        current_y = 0.0
        input_rect = QRectF()
        output_rect = QRectF()

        # 1. Top Port Area
        if cfg.get('enable_port_area', False) and cfg.get('port_area_top', True):
            if top_h > 0:
                half_w = self._width / 2
                input_rect = QRectF(0, 0, half_w, top_h)
                output_rect = QRectF(half_w, 0, half_w, top_h)
                current_y += top_h + padding

        # 2. Body Widget Area
        total_body_h = max(0, self._total_height - header_h)

        if not cfg.get('port_area_top', True) and bot_h > 0:
            widget_y = 0
            half_w = self._width / 2
            area_y = widget_min_h + padding
            input_rect = QRectF(0, area_y, half_w, bot_h)
            output_rect = QRectF(half_w, area_y, half_w, bot_h)
            widget_h_alloc = widget_min_h
        else:
            widget_y = current_y
            widget_h_alloc = max(widget_min_h, total_body_h - widget_y - 10)

        self.body.setPos(0, header_h)
        if hasattr(self.body, 'update_layout'):
            self.body.update_layout(
                self._width, total_body_h, input_rect, output_rect, widget_y, widget_h_alloc
            )
        self.handle.setPos(self._width, self._total_height)

        self.layout_ports(input_rect, output_rect, header_h)
        self._recalculate_paths()
        self._update_all_connected_traces()

        self.geometry_changed.emit()

    def layout_ports(
        self,
        input_rect: Optional[QRectF] = None,
        output_rect: Optional[QRectF] = None,
        header_h: Optional[float] = None,
    ):
        """Layouts ports with rect-based positioning."""
        if input_rect is None:
            input_rect = QRectF()
        if output_rect is None:
            output_rect = QRectF()
        if header_h is None:
            header_h = self.header.get_height()

        cfg = self._config
        margin = cfg.get('port_area_margin', 10)
        offset = cfg['port_offset']

        # 1. Position Summary Ports
        y_sum = header_h / 2
        self._summary_input.setPos(-offset, y_sum)
        self._summary_output.setPos(self._width + offset, y_sum)

        if self.is_minimized and self._anim.state() != QVariantAnimation.State.Running:
            return

        port_dia = cfg['port_radius'] * 2

        # 2. Input Ports
        if self.inputs:
            if not input_rect.isEmpty():
                start_y = header_h + input_rect.top() + margin
                for i, p in enumerate(self.inputs):
                    label_h = p.get_label_height() if hasattr(p, 'get_label_height') else port_dia
                    row_h = max(port_dia, label_h)
                    cy = start_y + (row_h / 2)
                    p.setPos(-offset, cy)
                    start_y += row_h + margin
            else:
                step = (self._total_height - header_h) / (len(self.inputs) + 1)
                for i, port in enumerate(self.inputs):
                    port.setPos(-offset, header_h + (i + 0.75) * step)

        # 3. Output Ports
        if self.outputs:
            if not output_rect.isEmpty():
                start_y = header_h + output_rect.top() + margin
                for i, p in enumerate(self.outputs):
                    label_h = p.get_label_height() if hasattr(p, 'get_label_height') else port_dia
                    row_h = max(port_dia, label_h)
                    cy = start_y + (row_h / 2)
                    p.setPos(self._width + offset, cy)
                    start_y += row_h + margin
            else:
                step = (self._total_height - header_h) / (len(self.outputs) + 1)
                for i, port in enumerate(self.outputs):
                    port.setPos(self._width + offset, header_h + (i + 0.75) * step)

    # ------------------------------------------------------------------
    # Path Cache
    # ------------------------------------------------------------------

    def _recalculate_paths(self):
        """Pre-calculates geometric paths for painting."""
        if self.scene():
            self.prepareGeometryChange()

        cfg = self._config
        w, h = self._width, self._total_height
        r = cfg['radius']
        eff_r = min(r, h / 2)

        # 1. Base Outline
        base_rect = QRectF(0, 0, w, h)
        self._cached_outline_path = QPainterPath()
        self._cached_outline_path.addRoundedRect(base_rect, eff_r, eff_r)

        # 2. Glow Path (Expanded)
        glow_off = cfg['sel_border_offset'] + cfg['sel_glow_offset']
        glow_rect = base_rect.adjusted(-glow_off, -glow_off, glow_off, glow_off)
        glow_rad = eff_r + glow_off
        self._cached_glow_path = QPainterPath()
        self._cached_glow_path.addRoundedRect(glow_rect, glow_rad, glow_rad)

        # 3. Hover Glow Path (Tighter Offset)
        hover_off = cfg['hover_glow_offset']
        hover_rect = base_rect.adjusted(-hover_off, -hover_off, hover_off, hover_off)
        hover_rad = eff_r + hover_off
        self._cached_hover_path = QPainterPath()
        self._cached_hover_path.addRoundedRect(hover_rect, hover_rad, hover_rad)

        # 4. Selection Sharp Border
        sel_off = cfg['sel_border_offset']
        sel_rect = base_rect.adjusted(-sel_off, -sel_off, sel_off, sel_off)
        sel_rad = eff_r + sel_off
        self._cached_sel_path = QPainterPath()
        self._cached_sel_path.addRoundedRect(sel_rect, sel_rad, sel_rad)

        # 5. Computing Pulse Glow Path
        computing_off = glow_off + cfg.get('computing_glow_extra_offset', 4.0)
        computing_rect = base_rect.adjusted(
            -computing_off, -computing_off, computing_off, computing_off
        )
        computing_rad = eff_r + computing_off
        self._cached_computing_glow_path = QPainterPath()
        self._cached_computing_glow_path.addRoundedRect(
            computing_rect, computing_rad, computing_rad
        )

        # 6. Bounding Rect
        margin = max(glow_off + 4, computing_off + 4, 30)
        self._cached_rect = base_rect.adjusted(-margin, -margin, margin, margin)

    # ------------------------------------------------------------------
    # Minimize / Maximize Animation
    # ------------------------------------------------------------------

    def toggle_minimize(self):
        """Triggers the minimize/maximize animation."""
        target_minimized = not self.is_minimized

        start_h = float(self._total_height)
        header_h = float(self.header.get_height())

        if target_minimized:
            self._stored_height = self._total_height
            end_h = header_h
        else:
            _, min_h = self._calculate_expanded_min_size()
            end_h = float(max(self._stored_height, min_h))

        self.is_minimized = target_minimized

        if hasattr(self.header, 'sync_minimize_button'):
            self.header.sync_minimize_button(target_minimized)

        # Pre-animation setup
        if not target_minimized:
            self._set_ports_visible(True)
            self.body.setVisible(True)
            self.handle.setVisible(True)
            if hasattr(self.header, '_recalculate_layout'):
                self.header._recalculate_layout()
        else:
            self._summary_input.setVisible(False)
            self._summary_output.setVisible(False)

        self._anim.stop()
        self._anim.setDuration(self._config['minimize_anim_duration'])
        self._anim.setStartValue(start_h)
        self._anim.setEndValue(end_h)
        self._anim.start()

    def _set_ports_visible(self, visible: bool):
        """Updates visibility for ports and their labels."""
        for p in self.inputs + self.outputs:
            p.setVisible(visible)

        if visible:
            self._summary_input.setVisible(False)
            self._summary_output.setVisible(False)
        else:
            self._summary_input.setVisible(True)
            self._summary_output.setVisible(True)
            if hasattr(self._summary_input, '_label') and self._summary_input._label:
                self._summary_input._label.setVisible(False)
            if hasattr(self._summary_output, '_label') and self._summary_output._label:
                self._summary_output._label.setVisible(False)

    def _on_anim_value_changed(self, value: Any):
        """Handles animation frame updates."""
        if value is None:
            return

        self.prepareGeometryChange()
        self._total_height = float(value)

        header_h = self.header.get_height()
        expanded_h = self._stored_height if self.is_minimized else self._anim.endValue()

        if abs(expanded_h - header_h) > 0.1:
            opacity = (self._total_height - header_h) / (expanded_h - header_h)
            opacity = max(0.0, min(1.0, opacity))
        else:
            opacity = 1.0

        for p in self.inputs + self.outputs:
            if hasattr(p, '_label') and p._label:
                p._label.setOpacity(opacity)

        self._recalculate_paths()
        self.update_geometry()
        self.update()

    def _on_anim_finished(self):
        """Cleanup after animation completes."""
        if self.is_minimized:
            self._set_ports_visible(False)
            self.body.setVisible(False)
            self.handle.setVisible(False)

            if self.inputs:
                self._summary_input.setVisible(True)
            if self.outputs:
                self._summary_output.setVisible(True)

            self.layout_ports(QRectF(), QRectF(), self.header.get_height())

            if hasattr(self.header, '_recalculate_layout'):
                self.header._recalculate_layout()
            self._recalculate_paths()
            self.update()

        self._update_all_connected_traces()

    # ------------------------------------------------------------------
    # Computing Pulse Animation
    # ------------------------------------------------------------------

    def _start_computing_pulse(self) -> None:
        """Start the looping pulse glow animation for COMPUTING state."""
        if self._computing_pulse_anim.state() != QVariantAnimation.State.Running:
            self._computing_pulse_phase = 0.0
            self._computing_pulse_anim.start()

    def _stop_computing_pulse(self) -> None:
        """Stop the pulse animation and clear the phase."""
        if self._computing_pulse_anim.state() == QVariantAnimation.State.Running:
            self._computing_pulse_anim.stop()
        self._computing_pulse_phase = 0.0
        self.update()

    def _on_computing_pulse_tick(self, value) -> None:
        """
        Receives the raw 0→1 sawtooth from QVariantAnimation and converts
        it into a 0→1→0 triangle wave so the glow breathes in and out.
        """
        if value is None:
            return
        self._computing_pulse_phase = 1.0 - abs(2.0 * float(value) - 1.0)
        self.update()

    # ------------------------------------------------------------------
    # Resize Handle Callback
    # ------------------------------------------------------------------

    def _on_handle_resize(self, target_w: float, target_h: float, is_ctrl: bool):
        """Callback from resize handle."""
        scene = self.scene()
        grid = getattr(scene, "grid_spacing", 10) if scene else 10

        target_w = round(target_w / grid) * grid
        target_h = round(target_h / grid) * grid

        min_w, min_h = self.calculate_min_size()
        target_w = max(target_w, min_w)
        target_h = max(target_h, min_h)

        self.prepareGeometryChange()
        self._width = target_w
        self._total_height = target_h

        if hasattr(self.header, 'set_width'):
            self.header.set_width(target_w)

        self._recalculate_paths()
        self.update_geometry()
