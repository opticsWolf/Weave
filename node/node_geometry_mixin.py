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
- Resize: apply_resize (geometry-only, called by canvas state machine)

Style Integration:
    All visual properties are resolved through StyleManager.get() with their
    correct StyleCategory (NODE or PORT) and canonical schema field names
    defined in core_theme.py.  Port geometry values (radius, offset,
    area_margin, etc.) are read from StyleCategory.PORT; node-level values
    (corner radius, glow offsets, etc.) from StyleCategory.NODE.  This
    guarantees read-time conversion (list → QColor, str → enum) and ensures
    live theme / batch_update changes are respected.
"""

from typing import Optional, List, Tuple, Any
import math
from PySide6.QtCore import Qt, QRectF, QVariantAnimation
from PySide6.QtGui import QPainterPath

from weave.node.node_port import NodePort
from weave.node.node_enums import VerticalSizePolicy
from weave.stylemanager import StyleManager, StyleCategory

from weave.logger import get_logger
log = get_logger("NodeGeometryMixin")


# ==============================================================================
# NodeGeometryMixin - Mixin providing geometry, layout, and animation for Node
# ==============================================================================

class NodeGeometryMixin:
    """
    Mixin providing geometry, layout, and animation for Node.

    Expects the host class to have:
        - self._config: Dict[str, Any]
        - self._width, self._total_height, self._stored_height: float
        - self.is_minimized: bool
        - self._vertical_size_policy: VerticalSizePolicy
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
    # Visible-Port Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _visible_ports(ports: List[NodePort]) -> List[NodePort]:
        """Return only ports whose QGraphicsItem visibility is True."""
        return [p for p in ports if p.isVisible()]

    # ------------------------------------------------------------------
    # Vertical Size Policy
    # ------------------------------------------------------------------

    def get_vertical_size_policy(self) -> 'VerticalSizePolicy':
        """Return the active vertical size policy for this instance."""
        return getattr(self, '_vertical_size_policy', VerticalSizePolicy.GROW_ONLY)

    def set_vertical_size_policy(self, policy: 'VerticalSizePolicy') -> None:
        """Set the vertical size policy and apply it immediately.

        Note:
            ``BaseControlNode`` subclasses can declare a class-level default
            via ``vertical_size_policy = VerticalSizePolicy.FIT`` which is
            applied in ``__init__``.  This method is for *runtime* changes
            and triggers an immediate ``auto_resize()``.

        Args:
            policy: ``VerticalSizePolicy.GROW_ONLY`` (default) or
                    ``VerticalSizePolicy.FIT``.
        """
        self._vertical_size_policy = VerticalSizePolicy(policy)
        # Apply immediately so the node reflects the new policy.
        self.auto_resize()

    # ------------------------------------------------------------------
    # Bounding Rect
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        """
        Returns the outer drawing bounds of the item.
        Dynamically calculates exact bounds based on path offsets, 
        specific pen widths for each state, and drop shadow gaussian tails.
        """
        w = getattr(self, '_width', 100)
        h = getattr(self, '_total_height', 100)
        base_rect = QRectF(0, 0, w, h)
        
        cfg = getattr(self, '_config', {})
        
        # 1. Base Border Extent
        base_extent = cfg.get('border_width', 0) / 2.0
        
        # 2. Selection Glow Extent (Path offset + Glow Pen thickness)
        sel_offset = cfg.get('sel_border_offset', 0) + cfg.get('sel_glow_offset', 0)
        sel_pen = cfg.get('sel_glow_width', 0)
        sel_extent = sel_offset + (sel_pen / 2.0)
        
        # 3. Hover Glow Extent
        hover_offset = cfg.get('hover_glow_offset', 0)
        hover_pen = cfg.get('hover_glow_width', 0)
        hover_extent = hover_offset + (hover_pen / 2.0)
        
        # 4. Computing Pulse Extent
        comp_offset = sel_offset + cfg.get('computing_glow_extra_offset', 0)
        comp_pen = cfg.get('computing_glow_width_max', 0)
        comp_extent = comp_offset + (comp_pen / 2.0)
        
        # The absolute maximum outward reach of any drawn path + its specific pen width
        max_stroke_extent = max(base_extent, sel_extent, hover_extent, comp_extent)
        
        # 5. Drop Shadow (Angle-aware)  
        shadow_margin = 0.0
        if cfg.get('shadow_enabled', False):  
            dist = cfg.get('shadow_offset', 0.0)  
            angle_deg = cfg.get('shadow_angle', 0.0)  
            angle_rad = math.radians(angle_deg)  
  
            dx = abs(dist * math.cos(angle_rad))  
            dy = abs(dist * math.sin(angle_rad))  
  
            blur_tail = cfg.get('shadow_blur_radius', 0.0) * 2.0  
  
            shadow_margin = max(dx, dy) + blur_tail

        # 6. Absolute mathematical max required margin
        total_margin = max(max_stroke_extent, shadow_margin)
        
        # math.ceil() ensures we snap to the next full pixel boundary.
        # The + 1.0 is the standard mathematical safe-guard for sub-pixel anti-aliasing 
        # (Qt draws floats; screen pixels are discrete integers).
        exact_margin = math.ceil(total_margin) + 1.0
        
        return base_rect.adjusted(-exact_margin, -exact_margin, exact_margin, exact_margin)

    def shape(self) -> QPainterPath:
        """
        Defines the precise interactive area for mouse collisions, selection, and hover events.
        This ensures the expanded boundingRect (for shadows/glows) doesn't intercept mouse events.
        """
        # Since node_core.py uses self._cached_sel_path to draw the selection glow, 
        # we know it perfectly outlines the physical node body and header.
        if hasattr(self, '_cached_sel_path') and not self._cached_sel_path.isEmpty():
            return self._cached_sel_path
            
        # Safe fallback if paths haven't been calculated yet during initial instantiation
        path = QPainterPath()
        path.addRect(QRectF(0, 0, getattr(self, '_width', 100), getattr(self, '_total_height', 100)))
        return path

    # ------------------------------------------------------------------
    # Port Stack Height
    # ------------------------------------------------------------------

    def _calculate_port_stack_height(self, ports: List[NodePort]) -> float:
        """Calculates total height required for a stack of visible ports.

        Hidden ports (``isVisible() == False``) are excluded so that
        toggling port visibility correctly adjusts the node's height.
        """
        visible = self._visible_ports(ports)
        if not visible:
            return 0.0

        margin = self._port_config['area_margin']
        port_dia = self._port_config['radius'] * 2

        total_h = margin
        for p in visible:
            label_h = p.get_label_height() if hasattr(p, 'get_label_height') else port_dia
            row_h = max(port_dia, label_h)
            total_h += row_h + margin
        return total_h

    # ------------------------------------------------------------------
    # Layout Metrics
    # ------------------------------------------------------------------

    def _calculate_layout_metrics(self) -> Tuple[float, float, float, float]:
        """Calculates internal heights for top area, widget, and bottom area.

        Only visible ports contribute to stack height and label width.
        """
        h_in = self._calculate_port_stack_height(self.inputs)
        h_out = self._calculate_port_stack_height(self.outputs)
        area_h = max(h_in, h_out)

        enable_area = self._port_config['enable_area']
        area_top = self._port_config['area_top']

        if enable_area:
            if area_top:
                top_area_h = area_h
                bottom_area_h = 0.0
            else:
                top_area_h = 0.0
                bottom_area_h = area_h
        else:
            top_area_h = 0.0
            bottom_area_h = 0.0

        widget_min_w, widget_min_h = (
            self.widget_host.get_content_min_size()
            if hasattr(self, 'widget_host') and self.widget_host is not None
            else (0, 0)
        )

        visible_in = self._visible_ports(self.inputs)
        visible_out = self._visible_ports(self.outputs)

        max_in_w = max(
            (p.get_label_width() for p in visible_in if hasattr(p, 'get_label_width')),
            default=0.0,
        )
        max_out_w = max(
            (p.get_label_width() for p in visible_out if hasattr(p, 'get_label_width')),
            default=0.0,
        )
        min_middle_gap = 40.0
        required_port_width = max_in_w + min_middle_gap + max_out_w

        title_width = self.header.get_title_width() if hasattr(self.header, 'get_title_width') else 0
        node_min_width = self._config['min_width']
        final_min_w = max(node_min_width, widget_min_w, required_port_width, title_width + 60)

        return top_area_h, widget_min_h, bottom_area_h, final_min_w

    def _calculate_expanded_min_size(self) -> Tuple[float, float]:
        """Calculates min size ignoring minimized state (used for restoration)."""
        top_h, widget_h, bot_h, min_w = self._calculate_layout_metrics()
        padding = self._port_config['area_padding']

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
            node_min_width = self._config['min_width']
            return max(node_min_width, title_min_w), self.header.get_height()
        return self._calculate_expanded_min_size()

    def enforce_min_dimensions(self):
        """Resizes node if current dimensions are too small.

        This method is **always grow-only**: it never shrinks the node
        below its current size.  It is used by style/config/theme change
        callbacks where preserving user-set dimensions is the correct
        behaviour.

        For structural changes (port add/remove, widget set, visibility
        toggle) use :meth:`auto_resize` instead, which respects the
        node's ``_vertical_size_policy``.
        """
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
    # Auto-Resize (policy-aware)
    # ------------------------------------------------------------------

    def auto_resize(self) -> None:
        """Resize the node to fit its content, respecting the size policy.

        This is the primary resize method for **structural content
        changes** — port additions / removals, widget changes, and port
        visibility toggles.

        Horizontal behaviour (always the same):
            Width only *grows* to accommodate content.  If the node has
            been manually widened beyond the minimum, the extra width is
            preserved.

        Vertical behaviour (policy-dependent):
            ``VerticalSizePolicy.GROW_ONLY``
                Height increases when content exceeds the current height
                but never shrinks — identical to ``enforce_min_dimensions``.
            ``VerticalSizePolicy.FIT``
                Height always matches the minimum required to display
                the current content.  Removing a port or hiding a widget
                causes the node to shrink accordingly.

        This method is a no-op while the node is minimised or while
        the minimise/maximise animation is running.
        """
        if self.is_minimized:
            return

        # Don't fight the animation
        anim = getattr(self, '_anim', None)
        if anim is not None and anim.state() == QVariantAnimation.State.Running:
            return

        min_w, min_h = self._calculate_expanded_min_size()

        # Horizontal: grow-only (never shrink from user-set width)
        new_w = max(self._width, min_w)

        # Vertical: policy-dependent
        policy = getattr(self, '_vertical_size_policy', VerticalSizePolicy.GROW_ONLY)
        if policy == VerticalSizePolicy.FIT:
            new_h = min_h
        else:
            new_h = max(self._total_height, min_h)

        changed = (abs(new_w - self._width) > 0.1
                    or abs(new_h - self._total_height) > 0.1)
        if not changed:
            return

        if self.scene():
            self.prepareGeometryChange()
        self._width = new_w
        self._total_height = new_h
        self._stored_height = new_h

        if hasattr(self.header, 'set_width'):
            self.header.set_width(new_w)

        self._recalculate_paths()
        self.update_geometry()

    # ------------------------------------------------------------------
    # Content Change Notification
    # ------------------------------------------------------------------

    def notify_content_changed(self) -> None:
        """Notify the node that body-widget dimensions may have changed.

        Call this after toggling widget visibility, adding or removing
        widgets from the body layout, or anything else that affects the
        embedded content's minimum size.

        This method is also called **automatically** by ``WidgetCore``
        when it detects a ``QEvent.LayoutRequest`` on itself (fired by
        Qt whenever a child widget is shown, hidden, added, removed, or
        changes its size hint).  Manual calls are therefore only needed
        when the content widget is *not* a ``WidgetCore``.

        Skipped silently while the node is minimised or animating.
        """
        if self.is_minimized:
            return
        anim = getattr(self, '_anim', None)
        if anim is not None and anim.state() == QVariantAnimation.State.Running:
            return
        if self.scene():
            self.prepareGeometryChange()
        self.auto_resize()
        self.update_geometry()
        self.update()

    # ------------------------------------------------------------------
    # Port Visibility
    # ------------------------------------------------------------------

    def set_port_visible(self, port: NodePort, visible: bool) -> None:
        """Show or hide a single port and auto-resize the node.

        Hidden ports are excluded from layout calculations so the node
        can shrink (if the policy allows).  Their traces remain intact —
        use :meth:`remove_port` to disconnect them.

        Args:
            port:    The port to show or hide.
            visible: ``True`` to show, ``False`` to hide.
        """
        if port.isVisible() == visible:
            return

        port.setVisible(visible)
        if hasattr(port, '_label') and port._label:
            port._label.setVisible(visible)

        self.auto_resize()
        self._recalculate_paths()
        self.update_geometry()
        self._update_all_connected_traces()
        self.update()

    def set_ports_visible_by_filter(
        self,
        predicate,
        ports: Optional[List[NodePort]] = None,
    ) -> int:
        """Batch-toggle port visibility via a predicate.

        More efficient than calling :meth:`set_port_visible` in a loop
        because the geometry rebuild runs only once.

        Args:
            predicate: A callable ``(NodePort) -> bool``.  Returns
                       ``True`` for ports that should be visible.
            ports:     Ports to evaluate.  Defaults to all inputs + outputs.

        Returns:
            Number of ports whose visibility actually changed.

        Example::

            # Hide all 'debug' ports
            node.set_ports_visible_by_filter(
                lambda p: p.datatype != 'debug'
            )
        """
        if ports is None:
            ports = self.inputs + self.outputs

        changed = 0
        for p in ports:
            want_visible = bool(predicate(p))
            if p.isVisible() != want_visible:
                p.setVisible(want_visible)
                if hasattr(p, '_label') and p._label:
                    p._label.setVisible(want_visible)
                changed += 1

        if changed:
            self.auto_resize()
            self._recalculate_paths()
            self.update_geometry()
            self._update_all_connected_traces()
            self.update()

        return changed

    # ------------------------------------------------------------------
    # Geometry Update & Port Layout
    # ------------------------------------------------------------------

    def update_geometry(self):
        """Re-layouts the body, ports, and handles based on current width/height."""
        header_h = self.header.get_height()

        top_h, widget_min_h, bot_h, _ = self._calculate_layout_metrics()
        padding = self._port_config['area_padding']
        enable_area = self._port_config['enable_area']
        area_top = self._port_config['area_top']

        current_y = 0.0
        input_rect = QRectF()
        output_rect = QRectF()

        # 1. Top Port Area
        if enable_area and area_top:
            if top_h > 0:
                half_w = self._width / 2
                input_rect = QRectF(0, 0, half_w, top_h)
                output_rect = QRectF(half_w, 0, half_w, top_h)
                current_y += top_h + padding

        # 2. Body Widget Area
        total_body_h = max(0, self._total_height - header_h)

        if not area_top and bot_h > 0:
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
                self._width, total_body_h, input_rect, output_rect,
            )

        # Position and size the widget host (sibling of body in node coords)
        if hasattr(self, 'widget_host') and self.widget_host is not None:
            self.widget_host.setPos(0, header_h)
            self.widget_host.update_layout(
                widget_y, widget_h_alloc, self._width,
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
        """Layouts visible ports with rect-based positioning.

        Hidden ports are skipped entirely so they do not consume layout
        space.  Their ``QGraphicsItem`` positions remain unchanged (they
        are invisible anyway).
        """
        if input_rect is None:
            input_rect = QRectF()
        if output_rect is None:
            output_rect = QRectF()
        if header_h is None:
            header_h = self.header.get_height()

        margin = self._port_config['area_margin']
        offset = self._port_config['offset']

        # 1. Position Summary Ports
        y_sum = header_h / 2
        self._summary_input.setPos(-offset, y_sum)
        self._summary_output.setPos(self._width + offset, y_sum)

        if self.is_minimized:
            anim = getattr(self, '_anim', None)
            if anim is None or anim.state() != QVariantAnimation.State.Running:
                return
            return

        port_dia = self._port_config['radius'] * 2

        visible_in = self._visible_ports(self.inputs)
        visible_out = self._visible_ports(self.outputs)

        # 2. Input Ports
        if visible_in:
            if not input_rect.isEmpty():
                start_y = header_h + input_rect.top() + margin
                for p in visible_in:
                    label_h = p.get_label_height() if hasattr(p, 'get_label_height') else port_dia
                    row_h = max(port_dia, label_h)
                    cy = start_y + (row_h / 2)
                    p.setPos(-offset, cy)
                    start_y += row_h + margin
            else:
                step = (self._total_height - header_h) / (len(visible_in) + 1)
                for i, port in enumerate(visible_in):
                    port.setPos(-offset, header_h + (i + 0.75) * step)

        # 3. Output Ports
        if visible_out:
            if not output_rect.isEmpty():
                start_y = header_h + output_rect.top() + margin
                for p in visible_out:
                    label_h = p.get_label_height() if hasattr(p, 'get_label_height') else port_dia
                    row_h = max(port_dia, label_h)
                    cy = start_y + (row_h / 2)
                    p.setPos(self._width + offset, cy)
                    start_y += row_h + margin
            else:
                step = (self._total_height - header_h) / (len(visible_out) + 1)
                for i, port in enumerate(visible_out):
                    port.setPos(self._width + offset, header_h + (i + 0.75) * step)

    # ------------------------------------------------------------------
    # Path Cache
    # ------------------------------------------------------------------

    def _recalculate_paths(self):
        """Pre-calculates geometric paths for painting."""
        # DO NOT call prepareGeometryChange here - only call it when boundingRect actually changes
        
        w, h = self._width, self._total_height
        r = self._config['radius']
        eff_r = min(r, h / 2)

        # 1. Base Outline
        base_rect = QRectF(0, 0, w, h)
        self._cached_outline_path = QPainterPath()
        self._cached_outline_path.addRoundedRect(base_rect, eff_r, eff_r)

        # 2. Glow Path (Expanded)
        sel_border_offset = self._config['sel_border_offset']
        sel_glow_offset = self._config['sel_glow_offset']
        glow_off = sel_border_offset + sel_glow_offset
        glow_rect = base_rect.adjusted(-glow_off, -glow_off, glow_off, glow_off)
        glow_rad = eff_r + glow_off
        self._cached_glow_path = QPainterPath()
        self._cached_glow_path.addRoundedRect(glow_rect, glow_rad, glow_rad)

        # 3. Hover Glow Path (Tighter Offset)
        hover_off = self._config['hover_glow_offset']
        hover_rect = base_rect.adjusted(-hover_off, -hover_off, hover_off, hover_off)
        hover_rad = eff_r + hover_off
        self._cached_hover_path = QPainterPath()
        self._cached_hover_path.addRoundedRect(hover_rect, hover_rad, hover_rad)

        # 4. Selection Sharp Border
        sel_rect = base_rect.adjusted(
            -sel_border_offset, -sel_border_offset,
            sel_border_offset, sel_border_offset,
        )
        sel_rad = eff_r + sel_border_offset
        self._cached_sel_path = QPainterPath()
        self._cached_sel_path.addRoundedRect(sel_rect, sel_rad, sel_rad)

        # 5. Computing Pulse Glow Path
        computing_extra = self._config['computing_glow_extra_offset']
        computing_off = glow_off + computing_extra
        computing_rect = base_rect.adjusted(
            -computing_off, -computing_off, computing_off, computing_off
        )
        computing_rad = eff_r + computing_off
        self._cached_computing_glow_path = QPainterPath()
        self._cached_computing_glow_path.addRoundedRect(
            computing_rect, computing_rad, computing_rad
        )

        # ======================================================================
        # 6. Bounding Rect Cache — MUST mirror boundingRect() logic exactly
        #    so that paint() clip regions never exceed what Qt invalidates.
        #
        #    GHOSTING FIX: The previous code used full pen widths (sel_glow_width,
        #    hover_glow_width, etc.) instead of half-widths, making _cached_rect
        #    wider than boundingRect().  The shadow blur clip in paint() then
        #    expanded _cached_rect even further, painting pixels outside Qt's
        #    dirty invalidation region.  On node movement those orphaned pixels
        #    were never cleared — producing the visible ghost trails.
        #
        #    Now _cached_rect uses the identical half-pen-width math as
        #    boundingRect(), and paint()'s blur clip is clamped to
        #    boundingRect() directly.
        # ======================================================================

        base_extent = self._config.get('border_width', 0) / 2.0

        sel_offset_val = sel_border_offset + sel_glow_offset
        sel_pen_val = self._config['sel_glow_width']
        sel_extent = sel_offset_val + (sel_pen_val / 2.0)

        hover_extent = hover_off + (self._config['hover_glow_width'] / 2.0)

        comp_offset_val = sel_offset_val + computing_extra
        comp_extent = comp_offset_val + (self._config['computing_glow_width_max'] / 2.0)

        max_stroke_extent = max(base_extent, sel_extent, hover_extent, comp_extent)

        shadow_margin = 0.0
        if self._config.get('shadow_enabled', False):
            dist = self._config.get('shadow_offset', 0.0)
            angle_deg = self._config.get('shadow_angle', 0.0)
            angle_rad = math.radians(angle_deg)
            dx = abs(dist * math.cos(angle_rad))
            dy = abs(dist * math.sin(angle_rad))
            blur_tail = self._config.get('shadow_blur_radius', 0.0) * 2.0
            shadow_margin = max(dx, dy) + blur_tail

        total_margin = max(max_stroke_extent, shadow_margin)
        exact_margin = math.ceil(total_margin) + 1.0

        self._cached_rect = base_rect.adjusted(
            -exact_margin, -exact_margin, exact_margin, exact_margin
        )

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
            if hasattr(self, 'widget_host') and self.widget_host is not None:
                self.widget_host.setVisible(True)
            self.handle.setVisible(True)
            if hasattr(self.header, '_recalculate_layout'):
                self.header._recalculate_layout()
        else:
            self._summary_input.setVisible(False)
            self._summary_output.setVisible(False)

        anim_duration = self._config['minimize_anim_duration']

        anim = getattr(self, '_anim', None)
        if anim is not None:
            anim.stop()
            anim.setDuration(anim_duration)
            anim.setStartValue(start_h)
            anim.setEndValue(end_h)
            anim.start()

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
        anim = getattr(self, '_anim', None)
        expanded_h = self._stored_height if self.is_minimized else (
            anim.endValue() if anim is not None else self._stored_height
        )

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
            if hasattr(self, 'widget_host') and self.widget_host is not None:
                self.widget_host.setVisible(False)
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
    # fallback if pulse animation mixin is not availbe
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

    def apply_resize(self, target_w: float, target_h: float) -> None:
        """Apply a resize to the given dimensions (clamped to min size).

        This is the single geometry-update entry point used by the canvas
        state machine during a resize drag.  Snapping and undo are the
        caller's responsibility — this method only enforces min-size
        constraints and updates the visual layout.
        """
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
        self.update()
        if hasattr(self.header, 'update'):
            self.header.update()
        if hasattr(self.body, 'update'):
            self.body.update()
