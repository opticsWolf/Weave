# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Union
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

# ============================================================================ 
# STYLE MANAGER WITH LIST AND ENUM STRING SUPPORT  
# ============================================================================

class StyleCategory(Enum):
    """Namespaces for different component style groups."""
    CANVAS = auto()
    NODE = auto() 
    TRACE = auto()
    MINIMAP = auto()
    PORT = auto()


@dataclass
class CanvasStyleSchema:
    """Canvas/Grid style purely defines structure. Mutable defaults handled by Manager."""
    bg_color: Optional[List[int]] = None
    grid_color: Optional[List[int]] = None
    grid_type: int = 1
    grid_spacing: int = 20
    grid_line_width: float = 2.0
    margin: int = 500
    min_width: int = 3000
    min_height: int = 2000
    debounce_ms: int = 50
    snapping_enabled: bool = True
    connection_snap_radius: float = 20.0
    max_visible_grid_lines: int = 5000
    min_direction_changes: int = 4
    shake_time_window_ms: int = 500
    min_stroke_length: int = 50
    shake_to_disconnect: bool = True


@dataclass
class NodeStyleSchema:
    """Node appearance defaults using list-based color and enum string definitions."""
    # Geometry
    width: int = 200
    header_height: int = 24
    min_width: int = 100
    min_height: int = 30
    
    # Animation
    minimize_anim_duration: int = 50
    
    # Header Layout
    header_left_padding: int = 10
    header_v_padding: int = 8
    header_right_padding: int = 15
    header_title_slider_spacing: int = 40
    header_slider_minimize_spacing: int = 10
    header_h_padding: int = 15
    header_item_spacing: int = 10
    
    # Minimize Button
    minimize_btn_size: int = 12
    minimize_btn_normal_color: Optional[List[int]] = None
    minimize_btn_hover_color: Optional[List[int]] = None
    minimize_btn_minimized_color: Optional[List[int]] = None
    minimize_btn_border_width: float = 0.0
    minimize_btn_border_color: Optional[List[int]] = None
    
    # Resize Handle
    resize_handle_radius: int = 10
    resize_handle_offset: int = -15
    resize_handle_width: int = 3
    resize_handle_hover_width: int = 4
    resize_handle_color: Optional[List[int]] = None
    resize_handle_hover_color: Optional[List[int]] = None
    
    # Colors
    header_bg: Optional[List[int]] = None
    body_bg: Optional[List[int]] = None
    outline_color: Optional[List[int]] = None
    title_text_color: Optional[List[int]] = None
    
    # Header Color Palette
    header_color_palette: Optional[List[List[int]]] = None
    
    # Auto Color Derivation
    title_text_color_from_header: bool = True
    use_header_color_for_outline: bool = True
    outline_derive_lightness: int = -25
    outline_derive_saturation: int = -25
    
    # Gradients & Separators
    header_gradient_enabled: bool = True
    header_gradient_angle: float = 0.0
    header_gradient_shift: int = -20
    body_gradient_enabled: bool = False
    body_gradient_angle: float = 0.0
    body_gradient_shift: int = 5
    header_bottom_line_enabled: bool = True
    header_bottom_line_width: float = 4.0
    header_bottom_line_shift: int = 40
    
    # Selection / Glow
    sel_border_color: Optional[List[int]] = None
    sel_border_width: float = 1.5
    sel_border_offset: float = 3.0
    sel_glow_enabled: bool = True
    sel_glow_offset: float = 1.0
    sel_glow_width: float = 8.0
    sel_glow_layers: int = 6
    sel_glow_opacity_start: int = 20
    use_header_color_for_glow: bool = True
    
    # Hover Glow
    hover_glow_enabled: bool = True
    hover_glow_color: Optional[List[int]] = None
    hover_glow_offset: float = 3.0
    hover_glow_width: float = 6.0
    hover_glow_layers: int = 3
    hover_glow_opacity_start: int = 25
    use_header_color_for_hover_glow: bool = True
    
    # Computing Glow (animated pulse for COMPUTING state)
    computing_glow_extra_offset: float = 4.0
    computing_glow_width_min: float = 2.0
    computing_glow_width_max: float = 14.0
    computing_glow_opacity_min: int = 15
    computing_glow_opacity_max: int = 90
    computing_glow_layers: int = 5
    computing_border_width: float = 1.5
    computing_border_opacity: int = 160
    
    # Highlight Offsets
    hl_header_bg: int = 15
    hl_header_sat: int = 25
    hl_body_bg: int = 5
    hl_outline: int = 10
    hl_title_bright: int = 25
    
    # Shadow
    shadow_enabled: bool = True
    shadow_offset: float = 3.0
    shadow_angle: float = 60.0
    shadow_opacity: int = 30
    shadow_blur_radius: float = 8.0
    shadow_blur_layers: int = 4
    shadow_blur_opacity: int = 5
    
    # Styling
    radius: int = 10
    border_width: float = 1.5
    connection_thickness: int = 3
    link_header_body_outline: bool = True
    
    # Font
    font_family: str = "Segoe UI"
    font_size: int = 10
    font_weight: Union[str, int, QFont.Weight] = "bold"  # Support string or numeric values
    font_italic: bool = True
    sel_font_weight: Union[str, int, QFont.Weight] = "bold"
    sel_font_italic: bool = False
    
    # State Slider
    state_slider_height: float = 12.0
    state_slider_width_ratio: float = 1.9
    state_slider_padding: float = 2.0
    state_slider_animation_duration: int = 75
    state_slider_highlight_on_hover: bool = True
    state_slider_highlight_color_shift: int = 10
    
    # State Visuals
    state_visuals: Optional[Dict[Any, Dict[str, Any]]] = None


@dataclass
class PortStyleSchema:
    """Port-specific style defaults using list-based color and enum string definitions."""
    # Port Geometry
    radius: int = 8
    offset: int = 1
    min_spacing: int = 25
    highlight: int = 50
    
    # Inner Circle
    inner_radius: int = 4
    inner_color: Optional[List[int]] = None
    use_outline_color: bool = True
    outline_bright: int = 50
    
    # Connection Drag Visuals
    compatible_saturation: int = 30
    compatible_brightness: int = 40
    incompatible_opacity: float = 0.67
    incompatible_saturation: int = -60
    incompatible_brightness: int = -60
    
    # Port Area
    enable_area: bool = True
    area_top: bool = True
    area_padding: int = 10
    area_margin: int = 10
    area_bg: Optional[List[int]] = None
    
    # Port Labels
    label_font_family: str = "Segoe UI"
    label_font_size: int = 9
    label_font_weight: Union[str, int, QFont.Weight] = "normal"
    label_font_italic: bool = False
    label_color: Optional[List[int]] = None
    label_max_width: int = 120
    label_spacing: int = 8
    label_connected_color_shift: int = 40
    label_connected_weight: Union[str, int, QFont.Weight] = "bold"
    label_connected_italic: bool = False


@dataclass
class TraceStyleSchema:
    """Connection trace style defaults using list-based color and enum string definitions."""
    # Main trace
    width: float = 3.0
    color: Optional[List[int]] = None
    style: Union[str, int, Qt.PenStyle] = "solid"  # Support strings like "dash", numbers, or enums
    cap_style: Union[str, int, Qt.PenCapStyle] = "round"
    join_style: Union[str, int, Qt.PenJoinStyle] = "round"
    
    # Outline/Halo
    outline_width: float = 1.0
    outline_color: Optional[List[int]] = None
    
    # Shadow
    shadow_enable: bool = True
    shadow_color: Optional[List[int]] = None
    shadow_offset_x: float = 1.5
    shadow_offset_y: float = 2.5
    
    # Drag trace specific
    drag_width: float = 2.0
    drag_style: Union[str, int, Qt.PenStyle] = "dash"
    drag_color: Optional[List[int]] = None
    drag_cap_style: Union[str, int, Qt.PenCapStyle] = "round"
    drag_join_style: Union[str, int, Qt.PenJoinStyle] = "round"
    drag_outline_width: float = 0.0
    drag_outline_color: Optional[List[int]] = None
    drag_shadow_enable: bool = False
    drag_shadow_color: Optional[List[int]] = None
    drag_shadow_offset_x: float = 1.5
    drag_shadow_offset_y: float = 2.5


@dataclass
class MinimapStyleSchema:
    """Minimap widget style defaults using list-based color and enum string definitions."""
    # Dimensions
    width: int = 240
    height: int = 180
    margin: int = 20
    minimized_size: int = 40
    
    # Animation
    anim_duration: int = 35
    hover_enter_delay: int = 25
    hover_leave_delay: int = 200
    
    # Body
    bg_color: Optional[List[int]] = None
    border_color: Optional[List[int]] = None
    border_width: float = 1.5
    corner_radius: int = 10
    
    # Viewport Lens
    lens_fill_color: Optional[List[int]] = None
    lens_border_color: Optional[List[int]] = None
    lens_border_width: float = 1.0
    lens_corner_radius: int = 3
    
    # Node representation
    node_color: Optional[List[int]] = None
    node_radius: float = 4.0
    
    # UI Buttons
    icon_size: int = 22
    icon_padding: int = 6
    icon_spacing: int = 4
    icon_symbol_width: float = 1.5
    icon_hover_color: Optional[List[int]] = None
    active_icon_color: Optional[List[int]] = None
    
    # Text
    text_color: Optional[List[int]] = None
    font_size: int = 10
    font_family: str = "Segoe UI"
    
    # Snapping overlay
    snap_color: Optional[List[int]] = None
    snap_width: float = 1.5


# ============================================================================ 
# BASE DEFAULTS DICTIONARY (Replaces lambda factories)  
# ============================================================================

BASE_DEFAULTS: Dict[StyleCategory, Dict[str, Any]] = {
    StyleCategory.CANVAS: {
        "bg_color": [30, 33, 40, 255],
        "grid_color": [50, 55, 62, 255],
    },
    StyleCategory.NODE: {
        "minimize_btn_normal_color": [192, 195, 203, 192],
        "minimize_btn_hover_color": [202, 205, 213, 224],
        "minimize_btn_minimized_color": [165, 168, 176, 168],
        "minimize_btn_border_color": [0, 0, 0, 0],
        "resize_handle_color": [92, 95, 103, 255],
        "resize_handle_hover_color": [128, 134, 150, 255],
        "header_bg": [32, 64, 128, 255],
        "body_bg": [38, 41, 46, 255],
        "outline_color": [20, 20, 20, 255],
        "title_text_color": [224, 236, 255, 255],
        "header_color_palette": [
            [32, 64, 128, 255], [160, 50, 50, 255], [50, 160, 50, 255], 
            [160, 160, 50, 255], [50, 50, 160, 255], [160, 50, 160, 255], 
            [50, 160, 160, 255], [200, 100, 50, 255], [100, 50, 200, 255], [50, 200, 100, 255]
        ],
        "sel_border_color": [60, 120, 200, 255],
        "hover_glow_color": [134, 137, 145, 255],
        "state_visuals": {
            'NORMAL': { "overlay": [0, 0, 0, 0], "opacity": 1.0, "icon_color": [100, 220, 100, 255] },
            'PASSTHROUGH': { "overlay": [128, 0, 164, 128], "opacity": 0.50, "icon_color": [255, 180, 50, 255] },
            'DISABLED': { "overlay": [225, 40, 0, 128], "opacity": 0.34, "icon_color": [220, 60, 60, 255] },
            'COMPUTING': { "overlay": [0, 0, 0, 0], "opacity": 1.0, "icon_color": [80, 180, 255, 255] }
        }
    },
    StyleCategory.PORT: {
        "inner_color": [50, 53, 61, 255],
        "label_color": [200, 200, 200, 255],
    },
    StyleCategory.TRACE: {
        "outline_color": [0, 3, 11, 64],
        "shadow_color": [0, 3, 11, 48],
        "drag_color": [255, 255, 255, 200],
        "drag_outline_color": [24, 27, 35, 64],
        "drag_shadow_color": [24, 27, 35, 48],
    },
    StyleCategory.MINIMAP: {
        "bg_color": [40, 43, 48, 128],
        "border_color": [65, 70, 78, 180],
        "lens_fill_color": [225, 230, 255, 32],
        "lens_border_color": [200, 220, 245, 64],
        "node_color": [80, 80, 80, 255],
        "icon_hover_color": [245, 248, 255, 30],
        "active_icon_color": [163, 166, 173, 255],
        "text_color": [163, 166, 173, 255],
        "snap_color": [123, 126, 134, 128],
    }
}