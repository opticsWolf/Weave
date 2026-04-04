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
    grid_type: int = 2
    grid_spacing: int = 20
    grid_line_width: float = 0.75
    grid_line_major_width: float = 2.0
    grid_dot_width: float = 2.5
    grid_dot_major_width: float = 4.0
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
    
    zoom_min: float = 0.2
    zoom_max: float = 3.0
    zoom_factor: float = 1.15
    scrollbar_policy: str = "never"


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
    header_icon: bool = True
    header_icon_default_width: int = 1.5
    
    # Minimize Button
    minimize_btn_size: int = 12
    minimize_btn_normal_color: Optional[List[int]] = None
    minimize_btn_hover_color: Optional[List[int]] = None
    minimize_btn_minimized_color: Optional[List[int]] = None
    minimize_btn_border_width: float = 0.0
    minimize_btn_border_color: Optional[List[int]] = None
    
    # Resize Handle
    resize_handle_radius: int = 10
    resize_handle_extend: int = 0
    resize_handle_offset: int = -14
    resize_handle_width: int = 3
    resize_handle_hover_width: int = 4
    resize_handle_color: Optional[List[int]] = None
    resize_handle_hover_color: Optional[List[int]] = None
    
    # Colors
    header_bg: Optional[List[int]] = None
    body_bg: Optional[List[int]] = None
    outline_color: Optional[List[int]] = None
    title_text_color: Optional[List[int]] = None
    body_text_color: Optional[List[int]] = None
    
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
    body_gradient_enabled: bool = True
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
    
    # Computing Glow
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
    font_weight: Union[str, int, QFont.Weight] = "normal"
    font_italic: bool = False
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

    # Pulse Animation — Waveform & Timing
    pulse_waveform: str = 'orbital' #'breathe'|'flash'|'heartbeat'|'ripple'|'sawtooth'|'orbital' (or custom)
    pulse_duration: int = 1200
    pulse_easing: str = 'InOutSine' #QEasingCurve.Type name applied to the raw sawtooth
    # Pulse Animation — Glow Geometry
    pulse_glow_offset: float = 0.0
    pulse_glow_width_min: float = 2.0
    pulse_glow_width_max: float = 16.0
    pulse_glow_layers: int = 8
    # Pulse Animation — Glow Opacity
    pulse_glow_opacity_min: int = 16
    pulse_glow_opacity_max: int = 128
    # Pulse Animation — Border
    pulse_border_width: float = 1.5
    pulse_border_opacity: int = 160
    # Pulse Animation — Colour Override (None = derive from header bg)
    pulse_color: Optional[List[int]] = None


@dataclass
class PortStyleSchema:
    """Port-specific style defaults using list-based color and enum string definitions."""
    radius: int = 8
    offset: int = 1
    min_spacing: int = 25
    highlight: int = 50
    
    inner_radius: int = 4
    inner_color: Optional[List[int]] = None
    use_outline_color: bool = True
    outline_bright: int = 50
    
    compatible_saturation: int = 30
    compatible_brightness: int = 40
    incompatible_opacity: float = 0.67
    incompatible_saturation: int = -60
    incompatible_brightness: int = -60
    
    enable_area: bool = True
    area_top: bool = True
    area_padding: int = 10
    area_margin: int = 10
    area_bg: Optional[List[int]] = None
    
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
    width: float = 3.0
    color: Optional[List[int]] = None
    connection_type: str = "bezier"
    style: Union[str, int, Qt.PenStyle] = "solid"
    cap_style: Union[str, int, Qt.PenCapStyle] = "round"
    join_style: Union[str, int, Qt.PenJoinStyle] = "round"
    
    trace_color_palette: Optional[List[List[int]]] = None
    
    outline_width: float = 1.0
    outline_color: Optional[List[int]] = None
    
    shadow_enable: bool = True
    shadow_color: Optional[List[int]] = None
    shadow_offset_x: float = 1.5
    shadow_offset_y: float = 2.5
    
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
    width: int = 200
    height: int = 150
    margin: int = 20
    minimized_size: int = 40
    
    anim_duration: int = 35
    hover_enter_delay: int = 25
    hover_leave_delay: int = 200
    
    bg_color: Optional[List[int]] = None
    border_color: Optional[List[int]] = None
    border_width: float = 1.5
    corner_radius: int = 10
    
    lens_fill_color: Optional[List[int]] = None
    lens_border_color: Optional[List[int]] = None
    lens_border_width: float = 1.0
    lens_corner_radius: int = 3
    
    node_color: Optional[List[int]] = None
    node_radius: float = 4.0
    
    icon_size: int = 22
    icon_padding: int = 6
    icon_spacing: int = 4
    icon_symbol_width: float = 1.5
    icon_hover_color: Optional[List[int]] = None
    active_icon_color: Optional[List[int]] = None
    
    text_color: Optional[List[int]] = None
    font_size: int = 10
    font_family: str = "Segoe UI"
    
    snap_color: Optional[List[int]] = None
    snap_width: float = 1.5


# ============================================================================ 
# BASE DEFAULTS DICTIONARY  
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
        "body_text_color": [200, 205, 215, 255],
        "header_color_palette": [
                [32, 64, 128, 255], [32, 112, 128, 255], [32, 128, 96, 255], 
                [32, 128, 48, 255], [64, 128, 32, 255], [112, 128, 32, 255], 
                [128, 96, 32, 255], [128, 48, 32, 255], [128, 32, 64, 255], 
                [128, 32, 112, 255], [96, 32, 128, 255], [48, 32, 128, 255], 
        ],
        "sel_border_color": [60, 120, 200, 255],
        "hover_glow_color": [134, 137, 145, 255],
        "state_visuals": {
            'NORMAL': { "overlay": [0, 0, 0, 0], "opacity": 1.0, "icon_color": [100, 220, 100, 255] },
            'PASSTHROUGH': { "overlay": [128, 0, 164, 128], "opacity": 0.50, "icon_color": [255, 180, 50, 255] },
            'DISABLED': { "overlay": [225, 40, 0, 128], "opacity": 0.34, "icon_color": [220, 60, 60, 255] },
            'COMPUTING': { "overlay": [0, 0, 0, 0], "opacity": 1.0, "icon_color": [80, 180, 255, 255] },        
        },
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
        "trace_color_palette": [
            [0, 0, 0, 255], [13, 15, 17, 255], [27, 30, 33, 255],
            [40, 45, 50, 255], [53, 60, 67, 255], [67, 75, 83, 255],
            [80, 90, 100, 255], [93, 105, 117, 255], [106, 120, 134, 255],
            [122, 135, 148, 255], [138, 150, 162, 255], [155, 165, 175, 255],
            [172, 180, 188, 255], [188, 195, 202, 255], [205, 210, 215, 255],
            [222, 225, 228, 255], [238, 240, 242, 255], [255, 255, 255, 255],
            [178, 89, 89, 255], [216, 162, 162, 255], [127, 51, 51, 255],
            [76, 0, 0, 255], [127, 0, 0, 255], [255, 0, 0, 255],
            [255, 76, 76, 255], [255, 153, 153, 255], [161, 54, 50, 255],
            [128, 48, 32, 255], [180, 60, 30, 255], [127, 31, 0, 255],
            [255, 178, 153, 255], [255, 63, 0, 255], [178, 111, 89, 255],
            [127, 70, 51, 255], [76, 19, 0, 255], [255, 121, 76, 255],
            [222, 113, 59, 255], [217, 108, 54, 255], [80, 40, 20, 255],
            [200, 100, 50, 255], [160, 90, 50, 255], [140, 80, 40, 255],
            [100, 60, 30, 255], [230, 150, 80, 255], [178, 133, 89, 255],
            [127, 63, 0, 255], [255, 165, 76, 255], [255, 127, 0, 255],
            [127, 89, 51, 255], [170, 120, 70, 255], [76, 38, 0, 255],
            [255, 204, 153, 255], [216, 189, 162, 255], [200, 160, 100, 255],
            [128, 96, 32, 255], [161, 127, 50, 255], [255, 229, 153, 255],
            [127, 95, 0, 255], [255, 210, 76, 255], [255, 191, 0, 255],
            [127, 108, 51, 255], [76, 57, 0, 255], [178, 156, 89, 255],
            [222, 195, 59, 255], [217, 190, 54, 255], [178, 178, 89, 255],
            [216, 216, 162, 255], [127, 127, 51, 255], [76, 76, 0, 255],
            [127, 127, 0, 255], [255, 255, 0, 255], [255, 255, 76, 255],
            [255, 255, 153, 255], [157, 161, 50, 255], [112, 128, 32, 255],
            [156, 178, 89, 255], [108, 127, 51, 255], [57, 76, 0, 255],
            [191, 255, 0, 255], [210, 255, 76, 255], [95, 127, 0, 255],
            [229, 255, 153, 255], [163, 217, 54, 255], [168, 222, 59, 255],
            [120, 161, 50, 255], [189, 216, 162, 255], [89, 127, 51, 255],
            [38, 76, 0, 255], [204, 255, 153, 255], [127, 255, 0, 255],
            [165, 255, 76, 255], [63, 127, 0, 255], [133, 178, 89, 255],
            [64, 128, 32, 255], [121, 255, 76, 255], [70, 127, 51, 255],
            [19, 76, 0, 255], [111, 178, 89, 255], [63, 255, 0, 255],
            [178, 255, 153, 255], [31, 127, 0, 255], [81, 217, 54, 255],
            [86, 222, 59, 255], [89, 178, 89, 255], [162, 216, 162, 255],
            [51, 127, 51, 255], [0, 76, 0, 255], [0, 127, 0, 255],
            [0, 255, 0, 255], [76, 255, 76, 255], [153, 255, 153, 255],
            [50, 161, 53, 255], [32, 128, 48, 255], [0, 127, 31, 255],
            [153, 255, 178, 255], [0, 255, 63, 255], [89, 178, 111, 255],
            [51, 127, 70, 255], [0, 76, 19, 255], [76, 255, 121, 255],
            [54, 217, 108, 255], [59, 222, 113, 255], [89, 178, 133, 255],
            [0, 127, 63, 255], [76, 255, 165, 255], [0, 255, 127, 255],
            [51, 127, 89, 255], [0, 76, 38, 255], [153, 255, 204, 255],
            [162, 216, 189, 255], [32, 128, 96, 255], [50, 161, 127, 255],
            [153, 255, 229, 255], [0, 127, 95, 255], [76, 255, 210, 255],
            [0, 255, 191, 255], [51, 127, 108, 255], [0, 76, 57, 255],
            [89, 178, 156, 255], [54, 217, 190, 255], [59, 222, 195, 255],
            [89, 178, 178, 255], [162, 216, 216, 255], [51, 127, 127, 255],
            [0, 76, 76, 255], [0, 127, 127, 255], [0, 255, 255, 255],
            [76, 255, 255, 255], [153, 255, 255, 255], [32, 112, 128, 255],
            [89, 156, 178, 255], [51, 108, 127, 255], [0, 57, 76, 255],
            [0, 191, 255, 255], [76, 210, 255, 255], [0, 95, 127, 255],
            [153, 229, 255, 255], [54, 163, 217, 255], [59, 168, 222, 255],
            [50, 120, 161, 255], [100, 160, 200, 255], [162, 189, 216, 255],
            [70, 120, 170, 255], [51, 89, 127, 255], [0, 38, 76, 255],
            [153, 204, 255, 255], [0, 127, 255, 255], [76, 165, 255, 255],
            [0, 63, 127, 255], [89, 133, 178, 255], [80, 150, 230, 255],
            [30, 60, 100, 255], [40, 80, 140, 255], [20, 40, 80, 255],
            [32, 64, 128, 255], [50, 100, 200, 255], [76, 121, 255, 255],
            [51, 70, 127, 255], [0, 19, 76, 255], [89, 111, 178, 255],
            [0, 63, 255, 255], [153, 178, 255, 255], [0, 31, 127, 255],
            [30, 60, 180, 255], [54, 81, 217, 255], [59, 86, 222, 255],
            [89, 89, 178, 255], [162, 162, 216, 255], [51, 51, 127, 255],
            [0, 0, 76, 255], [0, 0, 127, 255], [0, 0, 255, 255],
            [76, 76, 255, 255], [153, 153, 255, 255], [54, 50, 161, 255],
            [48, 32, 128, 255], [31, 0, 127, 255], [178, 153, 255, 255],
            [63, 0, 255, 255], [111, 89, 178, 255], [70, 51, 127, 255],
            [19, 0, 76, 255], [121, 76, 255, 255], [108, 54, 217, 255],
            [113, 59, 222, 255], [133, 89, 178, 255], [63, 0, 127, 255],
            [165, 76, 255, 255], [127, 0, 255, 255], [189, 162, 216, 255],
            [89, 51, 127, 255], [38, 0, 76, 255], [204, 153, 255, 255],
            [96, 32, 128, 255], [127, 50, 161, 255], [229, 153, 255, 255],
            [95, 0, 127, 255], [210, 76, 255, 255], [191, 0, 255, 255],
            [108, 51, 127, 255], [57, 0, 76, 255], [156, 89, 178, 255],
            [195, 59, 222, 255], [190, 54, 217, 255], [178, 89, 178, 255],
            [216, 162, 216, 255], [127, 51, 127, 255], [76, 0, 76, 255],
            [127, 0, 127, 255], [255, 0, 255, 255], [255, 76, 255, 255],
            [255, 153, 255, 255], [128, 32, 112, 255], [178, 89, 156, 255],
            [127, 51, 108, 255], [76, 0, 57, 255], [255, 0, 191, 255],
            [255, 76, 210, 255], [127, 0, 95, 255], [255, 153, 229, 255],
            [217, 54, 163, 255], [222, 59, 168, 255], [161, 50, 120, 255],
            [216, 162, 189, 255], [76, 0, 38, 255], [255, 153, 204, 255],
            [127, 51, 89, 255], [255, 0, 127, 255], [255, 76, 165, 255],
            [127, 0, 63, 255], [178, 89, 133, 255], [128, 32, 64, 255],
            [161, 50, 83, 255], [255, 76, 121, 255], [127, 51, 70, 255],
            [76, 0, 19, 255]
            ]
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