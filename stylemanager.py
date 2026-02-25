# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Centralized Style Management System for Node Canvas Components.

Based on your implementation with a few suggested enhancements:
1. Added PortStyleSchema (was declared but missing)
2. Added copy() method to StyleProxy for full dict compatibility
3. Added batch update signal suppression option
4. Added export/import for saving/loading custom themes
5. Minor docstring improvements

Original Features (preserved):
- Centralized configuration management via Dataclasses
- Strongly typed Qt Enums (no magic integers)
- Theme support (light/dark/warm/midnight)
- Runtime style updates with subscriber notifications (Observer Pattern)
- Thread-safe singleton design
- Backward compatibility layer (Dict-like access)
"""

from __future__ import annotations
import copy
import json
from dataclasses import dataclass, field, asdict, fields
from enum import Enum, auto
from typing import Dict, Any, Optional, List, Set, Union
from weakref import WeakSet
from contextlib import contextmanager

from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, QObject, Signal

from weave.logger import get_logger
log = get_logger("StyleManager")


# ==============================================================================
# STYLE CATEGORIES
# ==============================================================================

class StyleCategory(Enum):
    """Namespaces for different component style groups."""
    CANVAS = auto()      # Grid, background, margins
    NODE = auto()        # Node appearance (header, body, shadows, glow)
    TRACE = auto()       # Connection lines (NodeTrace, DragTrace)
    MINIMAP = auto()     # Minimap widget styles
    PORT = auto()        # Port-specific styles (separated from NODE for flexibility)


# ==============================================================================
# STYLE SCHEMAS (Strongly Typed)
# ==============================================================================

@dataclass
class CanvasStyleSchema:
    """Canvas/Grid style defaults."""
    bg_color: QColor = field(default_factory=lambda: QColor(30, 33, 40))
    grid_color: QColor = field(default_factory=lambda: QColor(50, 55, 62))
    grid_type: int = 1  # 0=LINES, 1=DOTS, 2=NONE (could be enum but matches existing code)
    grid_spacing: int = 20
    grid_line_width: float = 2.0
    margin: int = 500
    min_width: int = 3000
    min_height: int = 2000
    debounce_ms: int = 50
    snapping_enabled: bool = True
    connection_snap_radius: float = 20.0
    max_visible_grid_lines: int = 5000
    # Shake feature parameters  
    min_direction_changes: int = 4        # defines the number of direction changes when shaking
    shake_time_window_ms: int = 500       # milliseconds to measure movement speed over
    min_stroke_length: int = 50          # Pixels per stroke direction
    shake_to_disconnect: bool = True      # minimum connections needed before shake activates

@dataclass 
class NodeStyleSchema:
    """Node appearance defaults."""
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
    minimize_btn_normal_color: QColor = field(default_factory=lambda: QColor(192, 195, 203, 192))
    minimize_btn_hover_color: QColor = field(default_factory=lambda: QColor(202, 205, 213, 224))
    minimize_btn_minimized_color: QColor = field(default_factory=lambda: QColor(165, 168, 176, 168))
    minimize_btn_border_width: float = 0.0
    minimize_btn_border_color: QColor = field(default_factory=lambda: QColor(0, 0, 0, 0))
    
    # Resize Handle
    resize_handle_radius: int = 10
    resize_handle_offset: int = -15
    resize_handle_width: int = 3
    resize_handle_hover_width: int = 4
    resize_handle_color: QColor = field(default_factory=lambda: QColor(92, 95, 103))
    resize_handle_hover_color: QColor = field(default_factory=lambda: QColor(128, 134, 150))
    
    # Colors
    header_bg: QColor = field(default_factory=lambda: QColor(32, 64, 128))
    body_bg: QColor = field(default_factory=lambda: QColor(38, 41, 46))
    outline_color: QColor = field(default_factory=lambda: QColor(20, 20, 20))
    title_text_color: QColor = field(default_factory=lambda: QColor(224, 236, 255))
    
    # Header Color Palette - Added 10 default colors instead of just one  
    header_color_palette: List[QColor] = field(default_factory=lambda: [
    QColor(32, 64, 128),      # Default blue
    QColor(160, 50, 50),      # Red
    QColor(50, 160, 50),      # Green  
    QColor(160, 160, 50),     # Yellow
    QColor(50, 50, 160),      # Blue
    QColor(160, 50, 160),     # Magenta
    QColor(50, 160, 160),     # Cyan
    QColor(200, 100, 50),     # Orange
    QColor(100, 50, 200),     # Purple
    QColor(50, 200, 100)      # Teal
    ])
    
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
    sel_border_color: QColor = field(default_factory=lambda: QColor(60, 120, 200))
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
    hover_glow_color: QColor = field(default_factory=lambda: QColor(134, 137, 145))
    hover_glow_offset: float = 3.0
    hover_glow_width: float = 6.0
    hover_glow_layers: int = 3
    hover_glow_opacity_start: int = 25
    use_header_color_for_hover_glow: bool = True
    
    # Computing Glow (animated pulse for COMPUTING state)
    computing_glow_extra_offset: float = 4.0       # Extra expansion beyond selection glow path
    computing_glow_width_min: float = 2.0           # Pen width at dimmest phase
    computing_glow_width_max: float = 14.0          # Pen width at brightest phase
    computing_glow_opacity_min: int = 15            # Alpha at dimmest phase
    computing_glow_opacity_max: int = 90            # Alpha at brightest phase
    computing_glow_layers: int = 5                  # Concentric soft-glow strokes
    computing_border_width: float = 1.5             # Inner crisp border pen width
    computing_border_opacity: int = 160             # Inner border max alpha
    
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
    font_weight: QFont.Weight = QFont.Weight.Bold
    font_italic: bool = True
    sel_font_weight: QFont.Weight = QFont.Weight.Bold
    sel_font_italic: bool = False
    
    # State Slider
    state_slider_height: float = 12.0
    state_slider_width_ratio: float = 1.9
    state_slider_padding: float = 2.0
    state_slider_animation_duration: int = 75
    
    # State Visuals - Enhanced with proper overlay configurations for all states
    # Note: Keys should be NodeState enum values when used, but stored as dict for serialization
    state_visuals: Dict[Any, Dict[str, Any]] = field(default_factory=lambda: {
        'NORMAL': {
            "overlay": QColor(0, 0, 0, 0),
            "opacity": 1.0,
            "icon_color": QColor(100, 220, 100)
        },       # Normal opacity - full visibility
        'PASSTHROUGH': {                  # Pass-through mode - yellow highlight  
            "overlay": QColor(128, 0, 164, 128),
            "opacity": 0.50,
            "icon_color": QColor(255, 180, 50)
        },
        'DISABLED': {                     # Disabled state - red highlight
            "overlay": QColor(225, 40, 0, 128),
            "opacity": 0.34,
            "icon_color": QColor(220, 60, 60)
        },
        'COMPUTING': {                    # Computing state - subtle blue tint while working
            "overlay": QColor(0, 0, 0, 0),
            "opacity": 1.0,
            "icon_color": QColor(80, 180, 255)
        }
    })


@dataclass
class PortStyleSchema:
    """Port-specific style defaults (separated for flexibility)."""
    # Port Geometry
    radius: int = 8
    offset: int = 1
    min_spacing: int = 25
    highlight: int = 50
    
    # Inner Circle
    inner_radius: int = 4
    inner_color: QColor = field(default_factory=lambda: QColor(50, 53, 61))
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
    area_bg: Optional[QColor] = None
    
    # Port Labels
    label_font_family: str = "Segoe UI"
    label_font_size: int = 9
    label_font_weight: QFont.Weight = QFont.Weight.Normal
    label_font_italic: bool = False
    label_color: QColor = field(default_factory=lambda: QColor(200, 200, 200))
    label_max_width: int = 120
    label_spacing: int = 8
    label_connected_color_shift: int = 40
    label_connected_weight: QFont.Weight = QFont.Weight.Bold
    label_connected_italic: bool = False


@dataclass
class TraceStyleSchema:
    """Connection trace style defaults."""
    # Main trace
    width: float = 3.0
    color: Optional[QColor] = None  # None = inherit from port
    style: Qt.PenStyle = Qt.PenStyle.SolidLine
    cap_style: Qt.PenCapStyle = Qt.PenCapStyle.RoundCap
    join_style: Qt.PenJoinStyle = Qt.PenJoinStyle.RoundJoin
    
    # Outline/Halo
    outline_width: float = 1.0
    outline_color: QColor = field(default_factory=lambda: QColor(0, 3, 11, 64))
    
    # Shadow
    shadow_enable: bool = True
    shadow_color: QColor = field(default_factory=lambda: QColor(0, 3, 11, 48))
    shadow_offset_x: float = 1.5
    shadow_offset_y: float = 2.5
    
    # Drag trace specific
    drag_width: float = 2.0
    drag_style: Qt.PenStyle = Qt.PenStyle.DashLine
    drag_color: QColor = field(default_factory=lambda: QColor(255, 255, 255, 200))
    drag_cap_style: Qt.PenCapStyle = Qt.PenCapStyle.RoundCap
    drag_join_style: Qt.PenJoinStyle = Qt.PenJoinStyle.RoundJoin
    drag_outline_width: float = 0.0
    drag_outline_color: QColor = field(default_factory=lambda: QColor(24, 27, 35, 64))
    drag_shadow_enable: bool = False
    drag_shadow_color: QColor = field(default_factory=lambda: QColor(24, 27, 35, 48))
    drag_shadow_offset_x: float = 1.5
    drag_shadow_offset_y: float = 2.5


@dataclass
class MinimapStyleSchema:
    """Minimap widget style defaults."""
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
    bg_color: QColor = field(default_factory=lambda: QColor(40, 43, 48, 128))
    border_color: QColor = field(default_factory=lambda: QColor(65, 70, 78, 180))
    border_width: float = 1.5
    corner_radius: int = 10
    
    # Viewport Lens
    lens_fill_color: QColor = field(default_factory=lambda: QColor(225, 230, 255, 32))
    lens_border_color: QColor = field(default_factory=lambda: QColor(200, 220, 245, 64))
    lens_border_width: float = 1.0
    lens_corner_radius: int = 3
    
    # Node representation
    node_color: QColor = field(default_factory=lambda: QColor(80, 80, 80))
    node_radius: float = 4.0
    
    # UI Buttons
    icon_size: int = 22
    icon_padding: int = 6
    icon_spacing: int = 4
    icon_symbol_width: float = 1.5
    icon_hover_color: QColor = field(default_factory=lambda: QColor(245, 248, 255, 30))
    active_icon_color: QColor = field(default_factory=lambda: QColor(163, 166, 173))
    
    # Text
    text_color: QColor = field(default_factory=lambda: QColor(163, 166, 173))
    font_size: int = 10
    font_family: str = "Segoe UI"
    
    # Snapping overlay
    snap_color: QColor = field(default_factory=lambda: QColor(123, 126, 134, 128))
    snap_width: float = 1.5


# ==============================================================================
# SCHEMA FACTORY
# ==============================================================================

def _create_default_schema(category: StyleCategory) -> Any:
    """Factory function to create default schema for a category."""
    schema_map = {
        StyleCategory.CANVAS: CanvasStyleSchema,
        StyleCategory.NODE: NodeStyleSchema,
        StyleCategory.TRACE: TraceStyleSchema,
        StyleCategory.MINIMAP: MinimapStyleSchema,
        StyleCategory.PORT: PortStyleSchema,
    }
    return schema_map[category]()


# ==============================================================================
# THEME DEFINITIONS
# ==============================================================================

THEMES: Dict[str, Dict[StyleCategory, Dict[str, Any]]] = {
    "dark": {
        # Dark theme is the default - no overrides needed
    },
    "light": {
        StyleCategory.CANVAS: {
            "bg_color": QColor(240, 242, 245),
            "grid_color": QColor(200, 205, 210),
            "shake_to_disconnect": True,
            "min_stroke_length": 100,
            "min_direction_changes": 4,
            "shake_time_window_ms": 100,
        },
        StyleCategory.NODE: {
            "header_bg": QColor(70, 130, 200),
            "body_bg": QColor(250, 252, 255),
            "outline_color": QColor(180, 185, 190),
            "title_text_color": QColor(255, 255, 255),
            "resize_handle_color": QColor(160, 165, 172),
            "resize_handle_hover_color": QColor(100, 110, 130),
        },
        StyleCategory.PORT: {
            "inner_color": QColor(240, 242, 245),
            "label_color": QColor(60, 65, 70),
        },
        StyleCategory.TRACE: {
            "outline_color": QColor(0, 0, 0, 32),
            "shadow_color": QColor(0, 0, 0, 24),
        },
        StyleCategory.MINIMAP: {
            "bg_color": QColor(255, 255, 255, 200),
            "border_color": QColor(180, 185, 195, 200),
            "lens_fill_color": QColor(100, 150, 220, 40),
            "lens_border_color": QColor(70, 120, 200, 80),
            "node_color": QColor(180, 185, 195),
            "text_color": QColor(80, 85, 95),
            "active_icon_color": QColor(80, 85, 95),
        },
    },
    "midnight": {
        StyleCategory.CANVAS: {
            "bg_color": QColor(15, 18, 25),
            "grid_color": QColor(35, 40, 50),
        },
        StyleCategory.NODE: {
            "header_bg": QColor(45, 85, 160),
            "body_bg": QColor(22, 25, 32),
            "outline_color": QColor(10, 12, 18),
        },
        StyleCategory.PORT: {
            "inner_color": QColor(28, 32, 40),
        },
        StyleCategory.MINIMAP: {
            "bg_color": QColor(20, 24, 32, 180),
            "border_color": QColor(45, 50, 60, 200),
        },
    },
    "warm": {
        StyleCategory.CANVAS: {
            "bg_color": QColor(45, 38, 35),
            "grid_color": QColor(65, 55, 50),
        },
        StyleCategory.NODE: {
            "header_bg": QColor(160, 90, 50),
            "body_bg": QColor(55, 48, 44),
            "title_text_color": QColor(255, 245, 230),
            "sel_border_color": QColor(200, 140, 80),
            "hover_glow_color": QColor(180, 130, 100),
        },
    },
}


# ==============================================================================
# STYLE MANAGER (Singleton)
# ==============================================================================

class StyleManager(QObject):
    """
    Central manager for all visual styles across the node graph system.
    
    Features:
    - Unified access to all style categories
    - Theme support with instant switching
    - Subscriber notification on changes (Observer Pattern)
    - Batch updates with signal suppression
    - Thread-safe singleton design
    """
    
    # Signal emitted when any style changes: (category, changed_keys_dict)
    style_changed = Signal(object, dict)
    
    # Signal emitted when theme changes: (theme_name)
    theme_changed = Signal(str)
    
    _instance: Optional['StyleManager'] = None
    
    @classmethod
    def instance(cls) -> 'StyleManager':
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for testing)."""
        if cls._instance is not None:
            cls._instance.deleteLater()
        cls._instance = None
    
    def __init__(self):
        super().__init__()
        if StyleManager._instance is not None:
            raise RuntimeError("Use StyleManager.instance() to get the singleton.")
        
        # Initialize default schemas
        self._schemas: Dict[StyleCategory, Any] = {
            cat: _create_default_schema(cat) for cat in StyleCategory
        }
        
        # Subscribers by category (using WeakSet to avoid preventing GC)
        self._subscribers: Dict[StyleCategory, WeakSet] = {
            cat: WeakSet() for cat in StyleCategory
        }
        
        # Current theme name
        self._current_theme = "dark"
        
        # Cache for dict representations (optimization)
        self._dict_cache: Dict[StyleCategory, Optional[Dict[str, Any]]] = {
            cat: None for cat in StyleCategory
        }
        
        # Signal suppression flag for batch updates
        self._suppress_signals = False
        self._pending_changes: Dict[StyleCategory, Dict[str, Any]] = {}
    
    # ==========================================================================
    # BATCH UPDATES
    # ==========================================================================
    
    @contextmanager
    def batch_update(self):
        """
        Context manager for batch updates without intermediate signals.
        
        Usage:
            with manager.batch_update():
                manager.update(StyleCategory.NODE, header_bg=...)
                manager.update(StyleCategory.NODE, body_bg=...)
            # Single notification emitted here with all changes
        """
        self._suppress_signals = True
        self._pending_changes = {cat: {} for cat in StyleCategory}
        try:
            yield
        finally:
            self._suppress_signals = False
            # Emit accumulated changes
            for category, changes in self._pending_changes.items():
                if changes:
                    self._notify_subscribers(category, changes)
            self._pending_changes = {}
    
    # ==========================================================================
    # REGISTRATION
    # ==========================================================================
    
    def register(self, subscriber: Any, category: StyleCategory) -> None:
        """
        Register a subscriber to receive updates for a category.
        
        The subscriber should implement either:
        - on_style_changed(category: StyleCategory, changes: Dict[str, Any])
        - refresh_style() (fallback)
        """
        self._subscribers[category].add(subscriber)
    
    def unregister(self, subscriber: Any, category: Optional[StyleCategory] = None) -> None:
        """Unregister a subscriber from one or all categories."""
        if category is not None:
            self._subscribers[category].discard(subscriber)
        else:
            for sub_set in self._subscribers.values():
                sub_set.discard(subscriber)
    
    # ==========================================================================
    # ACCESS
    # ==========================================================================
    
    def get(self, category: StyleCategory, key: str, default: Any = None) -> Any:
        """Get a single style value."""
        schema = self._schemas.get(category)
        if schema and hasattr(schema, key):
            return getattr(schema, key)
        return default
    
    def get_all(self, category: StyleCategory) -> Dict[str, Any]:
        """Get all styles for a category as a dictionary (cached)."""
        if self._dict_cache[category] is None:
            schema = self._schemas[category]
            self._dict_cache[category] = asdict(schema)
        return self._dict_cache[category].copy()
    
    def get_schema(self, category: StyleCategory) -> Any:
        """Get the raw dataclass schema for a category."""
        return self._schemas.get(category)
    
    # ==========================================================================
    # UPDATES
    # ==========================================================================
    
    def update(self, category: StyleCategory, **kwargs) -> Set[str]:
        """
        Update style values for a category and notify subscribers.
        
        Returns:
            Set of keys that were actually changed.
        """
        schema = self._schemas.get(category)
        if not schema:
            return set()
        
        changed = set()
        for key, value in kwargs.items():
            if hasattr(schema, key):
                current = getattr(schema, key)
                # Type-aware equality check
                if not (current == value) or type(current) != type(value):
                    setattr(schema, key, value)
                    changed.add(key)
        
        if changed:
            # Invalidate cache
            self._dict_cache[category] = None
            
            if self._suppress_signals:
                # Accumulate for batch
                self._pending_changes[category].update({k: kwargs[k] for k in changed})
            else:
                # Notify immediately
                self._notify_subscribers(category, {k: kwargs[k] for k in changed})
        
        return changed
    
    # ==========================================================================
    # THEMES
    # ==========================================================================
    
    @property
    def current_theme(self) -> str:
        return self._current_theme
    
    @property
    def available_themes(self) -> List[str]:
        return list(THEMES.keys())
    
    def apply_theme(self, theme_name: str) -> bool:
        """Apply a named theme, resetting to defaults first."""
        if theme_name not in THEMES:
            log.warning(f"Unknown theme: {theme_name}")
            return False
        
        with self.batch_update():
            # Reset to defaults
            self._reset_to_defaults()
            
            # Apply theme overrides
            theme_overrides = THEMES[theme_name]
            for category, overrides in theme_overrides.items():
                self.update(category, **overrides)
        
        self._current_theme = theme_name
        self.theme_changed.emit(theme_name)
        return True
    
    def register_theme(self, name: str, overrides: Dict[StyleCategory, Dict[str, Any]]) -> None:
        """Register a custom theme at runtime."""
        THEMES[name] = overrides
    
    def _reset_to_defaults(self) -> None:
        """Reset all schemas to their default values."""
        self._schemas = {
            cat: _create_default_schema(cat) for cat in StyleCategory
        }
        for cat in StyleCategory:
            self._dict_cache[cat] = None
    
    # ==========================================================================
    # SERIALIZATION HELPERS
    # ==========================================================================
    
    @staticmethod
    def _serialize_value(obj: Any) -> Any:
        """Convert Qt objects to JSON-serializable formats."""
        if obj is None:
            return None
        elif isinstance(obj, QColor):
            return {"__type__": "QColor", "rgba": [obj.red(), obj.green(), obj.blue(), obj.alpha()]}
        elif isinstance(obj, Qt.GlobalColor):
            # Convert GlobalColor enum to a QColor first, then serialize
            color = QColor(obj)
            return {"__type__": "QColor", "rgba": [color.red(), color.green(), color.blue(), color.alpha()]}
        elif isinstance(obj, Qt.PenStyle):
            return {"__type__": "PenStyle", "value": obj.value}
        elif isinstance(obj, Qt.PenCapStyle):
            return {"__type__": "PenCapStyle", "value": obj.value}
        elif isinstance(obj, Qt.PenJoinStyle):
            return {"__type__": "PenJoinStyle", "value": obj.value}
        elif isinstance(obj, QFont.Weight):
            return {"__type__": "FontWeight", "value": obj.value}
        elif isinstance(obj, dict):
            return {k: StyleManager._serialize_value(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [StyleManager._serialize_value(v) for v in obj]
        elif isinstance(obj, Enum):
            # Catch-all for any other Qt/Python enum types
            return {"__type__": "Enum", "class": type(obj).__qualname__, "value": obj.value}
        return obj
    
    @staticmethod
    def _deserialize_value(obj: Any) -> Any:
        """Convert JSON data back to Qt objects."""
        if obj is None:
            return None
        elif isinstance(obj, dict):
            if "__type__" in obj:
                type_name = obj["__type__"]
                if type_name == "QColor":
                    return QColor(*obj["rgba"])
                elif type_name == "PenStyle":
                    return Qt.PenStyle(obj["value"])
                elif type_name == "PenCapStyle":
                    return Qt.PenCapStyle(obj["value"])
                elif type_name == "PenJoinStyle":
                    return Qt.PenJoinStyle(obj["value"])
                elif type_name == "FontWeight":
                    return QFont.Weight(obj["value"])
            return {k: StyleManager._deserialize_value(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [StyleManager._deserialize_value(v) for v in obj]
        return obj
    
    # ==========================================================================
    # EXPORT / IMPORT (Dict)
    # ==========================================================================
    
    def export_current(self) -> Dict[str, Any]:
        """
        Export current styles as a serializable dictionary.
        
        Returns:
            Dictionary with all current style values, JSON-serializable.
        """
        data = {
            "__meta__": {
                "version": "1.0",
                "base_theme": self._current_theme,
            }
        }
        
        for cat in StyleCategory:
            cat_data = self.get_all(cat)
            data[cat.name] = self._serialize_value(cat_data)
        
        return data
    
    def export_theme(self, theme_name: str) -> Optional[Dict[str, Any]]:
        """
        Export a registered theme as a serializable dictionary.
        
        Args:
            theme_name: Name of theme to export
            
        Returns:
            Serialized theme dict, or None if theme doesn't exist.
        """
        if theme_name not in THEMES:
            log.warning(f"Unknown theme: {theme_name}")
            return None
        
        data = {
            "__meta__": {
                "version": "1.0",
                "theme_name": theme_name,
            }
        }
        
        for cat, overrides in THEMES[theme_name].items():
            data[cat.name] = self._serialize_value(overrides)
        
        return data
    
    def import_theme(self, name: str, data: Dict[str, Any], apply: bool = False) -> bool:
        """
        Import a theme from a dictionary.
        
        Args:
            name: Name to register the theme under
            data: Serialized theme data
            apply: If True, apply the theme immediately after importing
            
        Returns:
            True if import was successful
        """
        overrides: Dict[StyleCategory, Dict[str, Any]] = {}
        
        for key, value in data.items():
            if key == "__meta__":
                continue
            
            try:
                category = StyleCategory[key]
                deserialized = self._deserialize_value(value)
                if isinstance(deserialized, dict):
                    overrides[category] = deserialized
            except KeyError:
                log.warning(f"Unknown category in import: {key}")
        
        if overrides:
            self.register_theme(name, overrides)
            if apply:
                self.apply_theme(name)
            return True
        
        return False
    
    # ==========================================================================
    # FILE I/O
    # ==========================================================================
    
    def save_to_file(
        self, 
        filepath: str, 
        theme_name: Optional[str] = None,
        indent: int = 2
    ) -> bool:
        """
        Save styles to a JSON file.
        
        Args:
            filepath: Path to save the JSON file
            theme_name: If provided, save that theme. Otherwise save current state.
            indent: JSON indentation level (default 2, use None for compact)
            
        Returns:
            True if save was successful
            
        Example:
            # Save current state
            manager.save_to_file("my_styles.json")
            
            # Save a specific theme
            manager.save_to_file("dark_theme.json", theme_name="dark")
        """
        try:
            if theme_name:
                data = self.export_theme(theme_name)
                if data is None:
                    return False
            else:
                data = self.export_current()
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            
            log.info(f"Saved styles to: {filepath}")
            return True
            
        except (IOError, OSError, TypeError) as e:
            log.error(f"Failed to save styles: {e}")
            return False
    
    def load_from_file(
        self, 
        filepath: str, 
        theme_name: Optional[str] = None,
        apply: bool = True
    ) -> bool:
        """
        Load styles from a JSON file.
        
        Args:
            filepath: Path to the JSON file
            theme_name: Name to register the loaded styles under.
                       If None, uses name from file metadata or filename.
            apply: If True, apply the loaded styles immediately.
            
        Returns:
            True if load was successful
            
        Example:
            # Load and apply immediately
            manager.load_from_file("my_styles.json")
            
            # Load as a named theme without applying
            manager.load_from_file("corporate.json", theme_name="corporate", apply=False)
            # Later...
            manager.apply_theme("corporate")
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Determine theme name
            if theme_name is None:
                meta = data.get("__meta__", {})
                theme_name = meta.get("theme_name") or meta.get("base_theme")
                if theme_name is None:
                    # Use filename without extension
                    import os
                    theme_name = os.path.splitext(os.path.basename(filepath))[0]
            
            # Check if this is a full state export or just theme overrides
            meta = data.get("__meta__", {})
            is_full_state = "base_theme" in meta and "theme_name" not in meta
            
            if is_full_state and apply:
                # Apply as direct state replacement
                with self.batch_update():
                    for cat in StyleCategory:
                        if cat.name in data:
                            cat_data = self._deserialize_value(data[cat.name])
                            if isinstance(cat_data, dict):
                                self.update(cat, **cat_data)
                self._current_theme = theme_name
                log.info(f"Loaded and applied styles from: {filepath}")
                return True
            else:
                # Import as theme
                success = self.import_theme(theme_name, data, apply=apply)
                if success:
                    action = "applied" if apply else "registered"
                    log.info(f"Loaded and {action} theme '{theme_name}' from: {filepath}")
                return success
                
        except FileNotFoundError:
            log.warning(f"File not found: {filepath}")
            return False
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON in {filepath}: {e}")
            return False
        except (IOError, OSError) as e:
            log.error(f"Failed to load styles: {e}")
            return False
    
    def save_current_as_theme(self, theme_name: str, filepath: Optional[str] = None) -> bool:
        """
        Save current styles as a named theme, optionally to a file.
        
        This captures only the differences from the default 'dark' theme,
        making it suitable for creating portable theme files.
        
        Args:
            theme_name: Name for the new theme
            filepath: Optional path to save to file
            
        Returns:
            True if successful
        """
        # Calculate differences from defaults
        overrides: Dict[StyleCategory, Dict[str, Any]] = {}
        
        for cat in StyleCategory:
            default_schema = _create_default_schema(cat)
            default_dict = asdict(default_schema)
            current_dict = self.get_all(cat)
            
            diff = {}
            for key, current_val in current_dict.items():
                default_val = default_dict.get(key)
                # Compare values (handle QColor specially)
                if isinstance(current_val, QColor) and isinstance(default_val, QColor):
                    if current_val.rgba() != default_val.rgba():
                        diff[key] = current_val
                elif current_val != default_val:
                    diff[key] = current_val
            
            if diff:
                overrides[cat] = diff
        
        # Register the theme
        self.register_theme(theme_name, overrides)
        
        # Optionally save to file
        if filepath:
            return self.save_to_file(filepath, theme_name=theme_name)
        
        return True
    
    # ==========================================================================
    # NOTIFICATION
    # ==========================================================================
    
    def _notify_subscribers(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        """Notify all subscribers of changes."""
        # Emit Qt signal
        self.style_changed.emit(category, changes)
        
        # Call subscriber methods directly
        for subscriber in list(self._subscribers[category]):  # list() to avoid mutation during iteration
            if hasattr(subscriber, 'on_style_changed'):
                try:
                    subscriber.on_style_changed(category, changes)
                except Exception as e:
                    log.error(f"Subscriber notification failed: {e}")
            elif hasattr(subscriber, 'refresh_style'):
                try:
                    subscriber.refresh_style()
                except Exception as e:
                    log.error(f"refresh_style failed: {e}")


# ==============================================================================
# CONVENIENCE FUNCTIONS
# ==============================================================================

def get_style_manager() -> StyleManager:
    """Get the global StyleManager instance."""
    return StyleManager.instance()


def get_style(category: StyleCategory, key: str, default: Any = None) -> Any:
    """Get a single style value."""
    return StyleManager.instance().get(category, key, default)


def update_style(category: StyleCategory, **kwargs) -> Set[str]:
    """Update styles for a category."""
    return StyleManager.instance().update(category, **kwargs)


def apply_theme(theme_name: str) -> bool:
    """Apply a named theme."""
    return StyleManager.instance().apply_theme(theme_name)


# ==============================================================================
# COMPATIBILITY LAYER - Dict-like Access
# ==============================================================================

class StyleProxy:
    """
    Provides dict-like access to StyleManager for backward compatibility.
    
    Usage:
        NODE_STYLE = StyleProxy(StyleCategory.NODE)
        color = NODE_STYLE['header_bg']
        NODE_STYLE['header_bg'] = QColor(...)  # Updates and notifies
    """
    
    def __init__(self, category: StyleCategory):
        self._category = category
    
    def __getitem__(self, key: str) -> Any:
        val = StyleManager.instance().get(self._category, key)
        if val is None and key not in self:
            raise KeyError(f"Key '{key}' not found in {self._category.name} schema")
        return val
    
    def __setitem__(self, key: str, value: Any) -> None:
        StyleManager.instance().update(self._category, **{key: value})
    
    def __contains__(self, key: str) -> bool:
        schema = StyleManager.instance().get_schema(self._category)
        return hasattr(schema, key)
    
    def get(self, key: str, default: Any = None) -> Any:
        return StyleManager.instance().get(self._category, key, default)
    
    def update(self, mapping: Dict[str, Any]) -> None:
        StyleManager.instance().update(self._category, **mapping)
    
    def copy(self) -> Dict[str, Any]:
        """Return a copy of all styles as a dictionary."""
        return StyleManager.instance().get_all(self._category)
    
    def keys(self):
        return StyleManager.instance().get_all(self._category).keys()
    
    def values(self):
        return StyleManager.instance().get_all(self._category).values()
    
    def items(self):
        return StyleManager.instance().get_all(self._category).items()
    
    def __repr__(self) -> str:
        return f"StyleProxy({self._category.name})"


# Create backward-compatible proxies
NODE_STYLE = StyleProxy(StyleCategory.NODE)
TRACE_STYLE = StyleProxy(StyleCategory.TRACE)
CANVAS_STYLE = StyleProxy(StyleCategory.CANVAS)
MINIMAP_STYLE = StyleProxy(StyleCategory.MINIMAP)
PORT_STYLE = StyleProxy(StyleCategory.PORT)


# ==============================================================================
# TEST
# ==============================================================================

if __name__ == "__main__":
    print("=== StyleManager Test (Final) ===\n")
    
    manager = StyleManager.instance()
    
    # 1. Test Strong Typing
    print("1. Strong Typing Test:")
    font_weight = manager.get(StyleCategory.NODE, 'font_weight')
    pen_style = manager.get(StyleCategory.TRACE, 'style')
    print(f"   Node font_weight: {font_weight} (type: {type(font_weight).__name__})")
    print(f"   Trace style: {pen_style} (type: {type(pen_style).__name__})")
    
    # 2. Test Port Schema (new)
    print("\n2. Port Schema Test:")
    print(f"   Port radius: {PORT_STYLE['radius']}")
    print(f"   Port label_color: {PORT_STYLE['label_color']}")
    
    # 3. Test Batch Updates
    print("\n3. Batch Update Test:")
    
    class TestSubscriber:
        def __init__(self):
            self.call_count = 0
        def on_style_changed(self, category, changes):
            self.call_count += 1
            print(f"   Subscriber notified: {list(changes.keys())}")
    
    sub = TestSubscriber()
    manager.register(sub, StyleCategory.NODE)
    
    print("   Without batch:")
    manager.update(StyleCategory.NODE, width=210)
    manager.update(StyleCategory.NODE, header_height=26)
    print(f"   Notifications: {sub.call_count}")
    
    sub.call_count = 0
    print("   With batch:")
    with manager.batch_update():
        manager.update(StyleCategory.NODE, width=220)
        manager.update(StyleCategory.NODE, header_height=28)
    print(f"   Notifications: {sub.call_count}")
    
    # 4. Test File I/O
    print("\n4. File I/O Test:")
    import tempfile
    import os
    
    # Create a temp directory for test files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Save current state
        state_file = os.path.join(tmpdir, "current_state.json")
        manager.save_to_file(state_file)
        print(f"   Saved current state to: {state_file}")
        
        # Modify some values
        manager.update(StyleCategory.NODE, header_bg=QColor(200, 50, 100))
        print(f"   Modified header_bg to: {manager.get(StyleCategory.NODE, 'header_bg')}")
        
        # Load saved state back
        manager.load_from_file(state_file, theme_name="restored")
        print(f"   Restored header_bg: {manager.get(StyleCategory.NODE, 'header_bg')}")
        
        # Save as a custom theme (only differences from default)
        manager.update(StyleCategory.NODE, header_bg=QColor(100, 150, 200))
        manager.update(StyleCategory.CANVAS, bg_color=QColor(25, 28, 35))
        
        theme_file = os.path.join(tmpdir, "my_custom_theme.json")
        manager.save_current_as_theme("my_custom", theme_file)
        print(f"   Saved custom theme to file")
        
        # Verify file contents
        with open(theme_file, 'r') as f:
            theme_data = json.load(f)
        print(f"   Theme file categories: {[k for k in theme_data.keys() if k != '__meta__']}")
    
    # 5. Test Themes
    print("\n5. Theme Test:")
    manager.apply_theme("light")
    print(f"   After 'light' theme - bg_color: {CANVAS_STYLE['bg_color']}")
    
    manager.apply_theme("dark")
    print(f"   After 'dark' theme - bg_color: {CANVAS_STYLE['bg_color']}")
    
    # 6. Demonstrate serialization format
    print("\n6. Serialization Format:")
    sample = manager._serialize_value(QColor(100, 150, 200, 255))
    print(f"   QColor serializes to: {sample}")
    print(f"   And deserializes back to: {manager._deserialize_value(sample)}")
    
    print("\n=== Test Complete ===")
    print("\nUsage Examples:")
    print("  # Save current styles to file")
    print("  manager.save_to_file('my_styles.json')")
    print("")
    print("  # Load styles from file")
    print("  manager.load_from_file('my_styles.json')")
    print("")
    print("  # Save only differences as a portable theme")
    print("  manager.save_current_as_theme('corporate', 'corporate_theme.json')")
