# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from typing import Dict, Any
from weave.themes.core_theme import StyleCategory

THEMES: Dict[str, Dict[StyleCategory, Dict[str, Any]]] = {
    "dark": {},
    "light": {
        StyleCategory.CANVAS: {
            "bg_color": [240, 242, 245, 255],
            "grid_color": [200, 205, 210, 255],
            "grid_type": 3,
            "shake_to_disconnect": True,
            "min_stroke_length": 100,
            "min_direction_changes": 4,
            "shake_time_window_ms": 100,
        },
        StyleCategory.NODE: {
            "header_bg": [54, 81, 217, 255],
            "header_color_palette": [
               [59, 86, 222, 255], [59, 168, 222, 255], [59, 222, 195, 255],
               [59, 222, 113, 255], [86, 222, 59, 255], [168, 222, 59, 255], 
               [222, 195, 59, 255], [222, 113, 59, 255],[222, 59, 86, 255], 
               [222, 59, 168, 255], [195, 59, 222, 255], [113, 59, 222, 255]
               ],
            'header_gradient_shift': 20,
            'header_bottom_line_shift': -30,
            'hl_title_bright': -25,
            "body_bg": [250, 252, 255, 255],
            "outline_color": [180, 185, 190, 255],
            "title_text_color": [30, 35, 40, 255],
            "body_text_color": [50, 55, 65, 255],
            "resize_handle_color": [120, 123, 128, 255],
            "resize_handle_hover_color": [160, 163, 168, 255],
            "font_weight": "normal",
        },
        StyleCategory.PORT: {
            "inner_color": [240, 242, 245, 255],
            "label_color": [60, 65, 70, 255],
            "label_connected_color_shift": -40
        },
        StyleCategory.TRACE: {
            "outline_color": [0, 0, 0, 32],
            "shadow_color": [0, 0, 0, 24],
            "connection_type": "straight",
            "style": "solid",
        },
        StyleCategory.MINIMAP: {
            "bg_color": [255, 255, 255, 200],
            "border_color": [180, 185, 195, 200],
            "lens_fill_color": [100, 150, 220, 40],
            "lens_border_color": [70, 120, 200, 80],
            "node_color": [180, 185, 195, 255],
            "text_color": [80, 85, 95, 255],
            "active_icon_color": [80, 85, 95, 255],
            "corner_radius": 8,
        },
    },
    "midnight": {
        StyleCategory.CANVAS: {
            "bg_color": [15, 18, 25, 255],
            "grid_color": [35, 40, 50, 255],
        },
        StyleCategory.NODE: {
            "header_bg": [54, 81, 217, 255],
            "header_color_palette": [
                [54, 81, 217, 255], [54, 163, 217, 255], [54, 217, 190, 255], 
                [54, 217, 108, 255], [81, 217, 54, 255], [163, 217, 54, 255], 
                [217, 190, 54, 255], [217, 108, 54, 255], [217, 54, 81, 255], 
                [217, 54, 163, 255], [190, 54, 217, 255], [108, 54, 217, 255]
                ],
            "body_bg": [22, 25, 32, 255],
            'title_text_color': [30, 35, 45, 255],
            'body_text_color': [200, 205, 220, 255],
            'header_gradient_shift': 20,
            'header_bottom_line_shift': -20,
            'hl_title_bright': -25,
            "outline_color": [10, 12, 18, 255],
            'radius': 6,
            'border_width': 2.5,
            'resize_handle_radius': 9,
            'resize_handle_extend': 4,
            'resize_handle_offset': -6,
            'resize_handle_width': 2.5,
            'resize_handle_hover_width': 3.0
        },
        StyleCategory.PORT: {
            "inner_color": [28, 32, 40, 255],
            "radius": 9,
            "offset": 4,
            "inner_radius": 6,
            "highlight": -35
        },
        StyleCategory.TRACE: {
            "connection_type": "angular",
        },
        StyleCategory.MINIMAP: {
            "bg_color": [20, 24, 32, 180],
            "border_color": [45, 50, 60, 200],
            "snap_color": [65, 70, 80, 164],
            "corner_radius": 6,
            "width": 225,
            "height": 165,
        },

    },
    "warm": {
        StyleCategory.CANVAS: {
            "bg_color": [45, 38, 35, 255],
            "grid_color": [65, 55, 50, 255],
        },
        StyleCategory.NODE: {
            "header_bg": [160, 90, 50, 255],
            "header_color_palette": [
                [160, 90, 50, 255], [161, 127, 50, 255], [157, 161, 50, 255], 
                [120, 161, 50, 255], [50, 161, 53, 255], [50, 161, 127, 255], 
                [50, 120, 161, 255], [54, 50, 161, 255], [127, 50, 161, 255], 
                [161, 50, 120, 255], [161, 50, 83, 255], [161, 54, 50, 255]
                ],
            "body_bg": [55, 48, 44, 255],
            "title_text_color": [255, 245, 230, 255],
            "body_text_color": [230, 220, 200, 255],
            "sel_border_color": [200, 140, 80, 255],
            "hover_glow_color": [180, 130, 100, 255],
            "font_weight": "bold",
        },
        StyleCategory.MINIMAP: {
            "bg_color": [55, 48, 45, 128],
            "border_color": [80, 70, 65, 180],
            "lens_fill_color": [250, 220, 210, 32],
            "lens_border_color": [245, 220, 200, 64],
            "node_color": [85, 80, 75, 255],
            "icon_hover_color": [255, 248, 245, 30],
            "active_icon_color": [173, 166, 163, 255],
            "text_color": [173, 166, 163, 255],
            "snap_color": [134, 126, 123, 128],
        },
    },
}