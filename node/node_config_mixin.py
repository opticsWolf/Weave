# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodeConfigMixin - Configuration and Color Management for Node.

Handles:
- Style/config merging and validation
- Port config key translation (StyleManager → legacy keys)
- Color derivation and selection-state color updates
- Custom color overrides
- StyleManager change callbacks
"""

from typing import Optional, Dict, Any
from PySide6.QtGui import QColor

from weave.node.node_subcomponents import NodeState, highlight_colors
#from weave.node import highlight_colors
from weave.stylemanager import StyleManager, StyleCategory

from weave.logger import get_logger
log = get_logger("NodeConfigMixin")


class NodeConfigMixin:
    """
    Mixin providing configuration and color management for Node.

    Expects the host class to have:
        - self._config: Dict[str, Any]
        - self.header: NodeHeader (with set_colors, _bg_color, _recalculate_layout, _title)
        - self.body: NodeBody (with set_colors)
        - self._custom_header_bg, _custom_header_outline,
          _custom_body_bg, _custom_body_outline: Optional[QColor]
        - self.isSelected() -> bool
        - self.update(), self.enforce_min_dimensions(), self._recalculate_paths(),
          self.update_geometry()
    """

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_config(self, strict: bool = False, **kwargs):
        """Updates node configuration with optional strict key validation."""
        should_update = False
        unknown_keys = []

        for k, v in kwargs.items():
            if k in self._config:
                self._config[k] = v
                should_update = True
            else:
                unknown_keys.append(k)

        if unknown_keys:
            msg = f"Unknown config keys: {unknown_keys}"
            if strict:
                raise ValueError(f"[Node] {msg}")
            else:
                log.warning(f"Warning: {msg}")

        if should_update:
            if hasattr(self, 'header'):
                self.header._recalculate_layout()
                self.header._title.update_selection_style(self.isSelected())

            _COLOR_KEYS = {
                'header_bg', 'body_bg', 'outline_color',
                'hl_header_bg', 'hl_header_sat', 'hl_body_bg', 'hl_outline', 'hl_title_bright',
                'use_header_color_for_outline', 'link_header_body_outline',
                'title_text_color', 'title_text_color_from_header',
                'outline_derive_lightness', 'outline_derive_saturation',
            }
            if kwargs.keys() & _COLOR_KEYS:
                self._update_colors(is_selected=self.isSelected())

            self.enforce_min_dimensions()
            self._recalculate_paths()
            self.update_geometry()
            self.update()
            if hasattr(self, 'header'): self.header.update()
            if hasattr(self, 'body'): self.body.update()

    @staticmethod
    def _get_port_config_for_node() -> Dict[str, Any]:
        """
        Get port configuration with translated keys for use in node layout calculations.

        The PortStyleSchema uses short keys (e.g., 'radius', 'offset') but the node
        layout code expects legacy prefixed keys (e.g., 'port_radius', 'port_offset').
        """
        raw = StyleManager.instance().get_all(StyleCategory.PORT)

        return {
            # Port Geometry
            'port_radius': raw.get('radius', 8),
            'port_offset': raw.get('offset', 1),
            'port_min_spacing': raw.get('min_spacing', 25),
            'port_highlight': raw.get('highlight', 50),

            # Inner Circle
            'inner_port_radius': raw.get('inner_radius', 4),
            'inner_port_color': raw.get('inner_color'),
            'port_use_outline_color': raw.get('use_outline_color', True),
            'port_use_outline_bright': raw.get('outline_bright', 50),

            # Port Area
            'enable_port_area': raw.get('enable_area', True),
            'port_area_top': raw.get('area_top', True),
            'port_area_padding': raw.get('area_padding', 10),
            'port_area_margin': raw.get('area_margin', 10),
            'port_area_bg': raw.get('area_bg'),

            # Port Labels
            'port_label_font_family': raw.get('label_font_family', 'Segoe UI'),
            'port_label_font_size': raw.get('label_font_size', 9),
            'port_label_font_weight': raw.get('label_font_weight'),
            'port_label_font_italic': raw.get('label_font_italic', False),
            'port_label_color': raw.get('label_color'),
            'port_label_max_width': raw.get('label_max_width', 120),
            'port_label_spacing': raw.get('label_spacing', 8),
            'port_label_connected_color_shift': raw.get('label_connected_color_shift', 40),
            'port_label_connected_weight': raw.get('label_connected_weight'),
            'port_label_connected_italic': raw.get('label_connected_italic', False),
        }

    # ------------------------------------------------------------------
    # StyleManager callback
    # ------------------------------------------------------------------

    def on_style_changed(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        """
        Callback method called when the StyleManager notifies about style changes.
        """
        if category == StyleCategory.NODE:
            self._config.update(changes)

            if hasattr(self, 'header'):
                self.header._recalculate_layout()
                self.header._title.update_selection_style(self.isSelected())

            self._recalculate_paths()
            self.enforce_min_dimensions()
            self.update_geometry()
            self.update()

    # ------------------------------------------------------------------
    # Color Management
    # ------------------------------------------------------------------

    def _derive_outline_color(self, base_bg: QColor) -> QColor:
        """Derives outline color from header background."""
        cfg = self._config
        if cfg.get('use_header_color_for_outline', False):
            return highlight_colors(
                base_bg,
                cfg.get('outline_derive_lightness', -40),
                cfg.get('outline_derive_saturation', 0)
            )
        else:
            return self._custom_header_outline or cfg['outline_color']

    def _update_colors(self, is_selected: bool):
        """Updates component colors based on selection state."""
        cfg = self._config

        # Determine Base Colors
        base_h_bg = self._custom_header_bg or cfg['header_bg']
        base_h_outline = self._derive_outline_color(base_h_bg)
        base_b_bg = self._custom_body_bg or cfg['body_bg']

        if cfg['link_header_body_outline']:
            base_b_outline = base_h_outline
        else:
            base_b_outline = self._custom_body_outline or cfg['outline_color']

        if cfg['title_text_color_from_header']:
            title_color_base = cfg['title_text_color']
            _, s, l, a = title_color_base.getHsl()
            h, _, _, _ = base_h_bg.getHsl()
            title_color_base = QColor.fromHsl(int(h), int(s), int(l), int(a))
        else:
            title_color_base = cfg['title_text_color']

        if is_selected:
            h_bg = highlight_colors(base_h_bg, cfg['hl_header_bg'], cfg['hl_header_sat'])
            b_bg = highlight_colors(base_b_bg, cfg['hl_body_bg'])
            h_outline = highlight_colors(base_h_outline, cfg['hl_outline'])
            b_outline = highlight_colors(base_b_outline, cfg['hl_outline'])
            t_color = highlight_colors(title_color_base, cfg['hl_title_bright'])

            self.header.set_colors(h_bg, h_outline, t_color)
            self.body.set_colors(b_bg, b_outline)
        else:
            self.header.set_colors(base_h_bg, base_h_outline, title_color_base)
            self.body.set_colors(base_b_bg, base_b_outline)

    def _handle_selection_change(self, is_selected: bool):
        """Triggers color updates and styling changes when selection changes."""
        self.header.update_selection_style(is_selected)
        self._update_colors(is_selected)

        # Don't force min dimensions if animating
        from PySide6.QtCore import QVariantAnimation
        if self._anim.state() != QVariantAnimation.State.Running:
            self.enforce_min_dimensions()

    def set_node_colors(
        self,
        header_bg: Optional[QColor] = None,
        body_bg: Optional[QColor] = None,
        outline: Optional[QColor] = None
    ) -> None:
        """Sets custom colors for the node components."""
        if header_bg is not None:
            self._custom_header_bg = header_bg

        if body_bg is not None:
            self._custom_body_bg = body_bg

        if outline is not None:
            self._custom_header_outline = outline
            self._custom_body_outline = outline

        self._update_colors(is_selected=self.isSelected())
        self.update()
        if hasattr(self, 'header'):
            self.header.update()
        if hasattr(self, 'body'):
            self.body.update()
