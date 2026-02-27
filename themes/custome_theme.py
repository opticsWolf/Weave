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
            "shake_to_disconnect": True,
            "min_stroke_length": 100,
            "min_direction_changes": 4,
            "shake_time_window_ms": 100,
        },
        StyleCategory.NODE: {
            "header_bg": [70, 130, 200, 255],
            "body_bg": [250, 252, 255, 255],
            "outline_color": [180, 185, 190, 255],
            "title_text_color": [255, 255, 255, 255],
            "resize_handle_color": [160, 165, 172, 255],
            "resize_handle_hover_color": [100, 110, 130, 255],
            "font_weight": "normal",
        },
        StyleCategory.PORT: {
            "inner_color": [240, 242, 245, 255],
            "label_color": [60, 65, 70, 255],
        },
        StyleCategory.TRACE: {
            "outline_color": [0, 0, 0, 32],
            "shadow_color": [0, 0, 0, 24],
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
        },
    },
    "midnight": {
        StyleCategory.CANVAS: {
            "bg_color": [15, 18, 25, 255],
            "grid_color": [35, 40, 50, 255],
        },
        StyleCategory.NODE: {
            "header_bg": [45, 85, 160, 255],
            "body_bg": [22, 25, 32, 255],
            "outline_color": [10, 12, 18, 255],
        },
        StyleCategory.PORT: {
            "inner_color": [28, 32, 40, 255],
        },
        StyleCategory.MINIMAP: {
            "bg_color": [20, 24, 32, 180],
            "border_color": [45, 50, 60, 200],
        },
    },
    "warm": {
        StyleCategory.CANVAS: {
            "bg_color": [45, 38, 35, 255],
            "grid_color": [65, 55, 50, 255],
        },
        StyleCategory.NODE: {
            "header_bg": [160, 90, 50, 255],
            "body_bg": [55, 48, 44, 255],
            "title_text_color": [255, 245, 230, 255],
            "sel_border_color": [200, 140, 80, 255],
            "hover_glow_color": [180, 130, 100, 255],
            "font_weight": "bold",
        },
    },
}