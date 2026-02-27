# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Integrated Color Management System for Qt Node Canvas Framework

This module provides seamless integration of color conversion utilities 
into both StyleManager and GraphSerializer, enabling consistent handling 
of colors throughout the system while maintaining backward compatibility.
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, fields
from enum import Enum, auto
from typing import Dict, Any, Optional, List, Set, Union
from weakref import WeakSet
from contextlib import contextmanager

from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, QObject, Signal

from weave.themes.core_theme import (
    StyleCategory, CanvasStyleSchema, NodeStyleSchema, PortStyleSchema,
    TraceStyleSchema, MinimapStyleSchema, BASE_DEFAULTS
    )

from weave.themes.custome_theme import THEMES

DEBUG_STYLE_MANAGER = True

def _debug_print(msg: str):
    if DEBUG_STYLE_MANAGER:
        print(f"[StyleManager DEBUG] {msg}", flush=True)


# ============================================================================
# FIELD CLASSIFICATION HELPERS
# ============================================================================

# Exhaustive sets of field names that require special conversion.
# Using explicit names avoids dangerous substring matching on "style", etc.

_COLOR_FIELDS: Set[str] = {
    # Canvas
    "bg_color", "grid_color",
    # Node
    "minimize_btn_normal_color", "minimize_btn_hover_color",
    "minimize_btn_minimized_color", "minimize_btn_border_color",
    "resize_handle_color", "resize_handle_hover_color",
    "header_bg", "body_bg", "outline_color", "title_text_color",
    "sel_border_color", "hover_glow_color",
    # Port
    "inner_color", "area_bg", "label_color",
    # Trace
    "color", "shadow_color", "drag_color",
    "drag_outline_color", "drag_shadow_color",
    # Minimap
    "border_color", "lens_fill_color", "lens_border_color",
    "node_color", "icon_hover_color", "active_icon_color",
    "text_color", "snap_color",
}

_FONT_WEIGHT_FIELDS: Set[str] = {
    "font_weight", "sel_font_weight",
    "label_font_weight", "label_connected_weight",
}

_PEN_STYLE_FIELDS: Set[str] = {
    "style", "drag_style",
}

_PEN_CAP_STYLE_FIELDS: Set[str] = {
    "cap_style", "drag_cap_style",
}

_PEN_JOIN_STYLE_FIELDS: Set[str] = {
    "join_style", "drag_join_style",
}

_PALETTE_FIELD = "header_color_palette"
_STATE_VISUALS_FIELD = "state_visuals"


def _is_color_list(val: Any) -> bool:
    """Check if a value is a color-like list: 3-4 numeric elements."""
    return (isinstance(val, (list, tuple))
            and 3 <= len(val) <= 4
            and all(isinstance(c, (int, float)) for c in val))


# ============================================================================
# CONVERSION UTILITIES  (raw → Qt)
# ============================================================================

def to_qcolor(val) -> QColor:
    """
    Convert various color formats into a QColor object.
    Accepts: QColor, "#hex", [r,g,b], [r,g,b,a], (r,g,b), (r,g,b,a).
    """
    if isinstance(val, QColor):
        return val
        
    # Handle Hex Strings
    if isinstance(val, str) and val.startswith("#"):
        try:
            return QColor(val)
        except Exception as e:
            _debug_print(f"to_qcolor: Exception parsing hex {val}: {e}")
            
    # Handle Lists/Tuples [r, g, b] or [r, g, b, a]
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        try:
            r, g, b = val[0], val[1], val[2]
            a = int(val[3]) if len(val) > 3 else 255
            return QColor(int(r), int(g), int(b), a)
        except Exception as e:
            _debug_print(f"to_qcolor: Exception parsing list {val}: {e}")
            
    # Fallback: Return black with full opacity
    _debug_print(f"to_qcolor: Falling back to black for val: {val!r}")
    return QColor(0, 0, 0, 255)


def to_qfont_weight(val) -> QFont.Weight:
    """Convert various font weight formats into QFont.Weight enum."""
    if isinstance(val, QFont.Weight):
        return val
    
    weight_map = {
        "thin": QFont.Weight.Thin,
        "light": QFont.Weight.Light,
        "normal": QFont.Weight.Normal,
        "medium": QFont.Weight.Medium,
        "bold": QFont.Weight.Bold,
        "black": QFont.Weight.Black
    }
    
    if isinstance(val, str) and val.lower() in weight_map:
        return weight_map[val.lower()]
        
    if isinstance(val, (int, float)):
        val = int(val)
        if val >= 900: return QFont.Weight.Black
        elif val >= 700: return QFont.Weight.Bold
        elif val >= 500: return QFont.Weight.Medium
        elif val >= 400: return QFont.Weight.Normal
        elif val >= 300: return QFont.Weight.Light
        elif val >= 100: return QFont.Weight.Thin
    
    _debug_print(f"to_qfont_weight: Falling back to Normal for val: {val!r}")
    return QFont.Weight.Normal


def to_pen_style(val) -> Qt.PenStyle:
    """Convert various pen style formats into Qt.PenStyle enum."""
    if isinstance(val, Qt.PenStyle):
        return val
    
    style_map = {
        "solid": Qt.PenStyle.SolidLine,
        "dash": Qt.PenStyle.DashLine,
        "dot": Qt.PenStyle.DotLine,
        "dashdot": Qt.PenStyle.DashDotLine,
        "dashdotdot": Qt.PenStyle.DashDotDotLine
    }
    
    if isinstance(val, str) and val.lower() in style_map:
        return style_map[val.lower()]
        
    if isinstance(val, (int, float)):
        v = int(val)
        # Qt.PenStyle: 0=NoPen, 1=Solid, 2=Dash, 3=Dot, 4=DashDot, 5=DashDotDot
        if 1 <= v <= 5:
            return Qt.PenStyle(v)
    
    _debug_print(f"to_pen_style: Falling back to SolidLine for val: {val!r}")
    return Qt.PenStyle.SolidLine


def to_pen_cap_style(val) -> Qt.PenCapStyle:
    """Convert various pen cap style formats into Qt.PenCapStyle enum."""
    if isinstance(val, Qt.PenCapStyle):
        return val
    
    style_map = {
        "flat": Qt.PenCapStyle.FlatCap,
        "square": Qt.PenCapStyle.SquareCap,
        "round": Qt.PenCapStyle.RoundCap
    }
    
    if isinstance(val, str) and val.lower() in style_map:
        return style_map[val.lower()]
        
    if isinstance(val, (int, float)):
        v = int(val)
        # Qt.PenCapStyle: 0x00=FlatCap, 0x10=SquareCap, 0x20=RoundCap
        cap_map = {0: Qt.PenCapStyle.FlatCap, 1: Qt.PenCapStyle.SquareCap, 2: Qt.PenCapStyle.RoundCap}
        if v in cap_map:
            return cap_map[v]
    
    _debug_print(f"to_pen_cap_style: Falling back to RoundCap for val: {val!r}")
    return Qt.PenCapStyle.RoundCap


def to_pen_join_style(val) -> Qt.PenJoinStyle:
    """Convert various pen join style formats into Qt.PenJoinStyle enum."""
    if isinstance(val, Qt.PenJoinStyle):
        return val
    
    style_map = {
        "miter": Qt.PenJoinStyle.MiterJoin,
        "round": Qt.PenJoinStyle.RoundJoin,
        "bevel": Qt.PenJoinStyle.BevelJoin
    }
    
    if isinstance(val, str) and val.lower() in style_map:
        return style_map[val.lower()]
        
    if isinstance(val, (int, float)):
        v = int(val)
        # Qt.PenJoinStyle: 0x00=MiterJoin, 0x40=BevelJoin, 0x80=RoundJoin
        join_map = {0: Qt.PenJoinStyle.MiterJoin, 1: Qt.PenJoinStyle.BevelJoin, 2: Qt.PenJoinStyle.RoundJoin}
        if v in join_map:
            return join_map[v]
    
    _debug_print(f"to_pen_join_style: Falling back to MiterJoin for val: {val!r}")
    return Qt.PenJoinStyle.MiterJoin


# ============================================================================
# REVERSE CONVERSION UTILITIES  (Qt → raw for storage)
# ============================================================================

def _qcolor_to_list(color: QColor) -> List[int]:
    """Convert QColor back to [r, g, b, a] list."""
    return [color.red(), color.green(), color.blue(), color.alpha()]


def _font_weight_to_str(weight: QFont.Weight) -> str:
    """Convert QFont.Weight back to string."""
    weight_map = {
        QFont.Weight.Thin: "thin",
        QFont.Weight.Light: "light",
        QFont.Weight.Normal: "normal",
        QFont.Weight.Medium: "medium",
        QFont.Weight.Bold: "bold",
        QFont.Weight.Black: "black",
    }
    return weight_map.get(weight, "normal")


def _pen_style_to_str(style: Qt.PenStyle) -> str:
    """Convert Qt.PenStyle back to string."""
    style_map = {
        Qt.PenStyle.SolidLine: "solid",
        Qt.PenStyle.DashLine: "dash",
        Qt.PenStyle.DotLine: "dot",
        Qt.PenStyle.DashDotLine: "dashdot",
        Qt.PenStyle.DashDotDotLine: "dashdotdot",
    }
    return style_map.get(style, "solid")


def _pen_cap_to_str(cap: Qt.PenCapStyle) -> str:
    cap_map = {
        Qt.PenCapStyle.FlatCap: "flat",
        Qt.PenCapStyle.SquareCap: "square",
        Qt.PenCapStyle.RoundCap: "round",
    }
    return cap_map.get(cap, "round")


def _pen_join_to_str(join: Qt.PenJoinStyle) -> str:
    join_map = {
        Qt.PenJoinStyle.MiterJoin: "miter",
        Qt.PenJoinStyle.RoundJoin: "round",
        Qt.PenJoinStyle.BevelJoin: "bevel",
    }
    return join_map.get(join, "miter")


# ============================================================================
# READ-TIME CONVERSION  (raw schema value → Qt type for callers)
# ============================================================================

def _convert_for_read(field_name: str, value: Any) -> Any:
    """
    Convert a raw schema value to its Qt type for the caller.
    Called by get() and get_all() so consumers always receive Qt objects.
    """
    # Already a Qt type (shouldn't happen with the new design, but defensive)
    if isinstance(value, (QColor, QFont.Weight, Qt.PenStyle, Qt.PenCapStyle, Qt.PenJoinStyle)):
        return value

    # Color fields
    if field_name in _COLOR_FIELDS:
        if isinstance(value, (list, tuple, str)):
            return to_qcolor(value)
        return value

    # Palette (list of colors)
    if field_name == _PALETTE_FIELD:
        if isinstance(value, list) and len(value) > 0:
            return [to_qcolor(c) if isinstance(c, (list, tuple)) else c for c in value]
        return value

    # State visuals (nested dict with color sub-fields)
    if field_name == _STATE_VISUALS_FIELD:
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                if isinstance(v, dict):
                    new_v = {}
                    for vk, vv in v.items():
                        if vk in {"overlay", "icon_color"} and isinstance(vv, (list, tuple)):
                            new_v[vk] = to_qcolor(vv)
                        else:
                            new_v[vk] = vv
                    result[k] = new_v
                else:
                    result[k] = v
            return result
        return value

    # Font weight fields
    if field_name in _FONT_WEIGHT_FIELDS:
        if isinstance(value, (str, int)):
            return to_qfont_weight(value)
        return value

    # Pen style fields
    if field_name in _PEN_STYLE_FIELDS:
        if isinstance(value, (str, int)):
            return to_pen_style(value)
        return value

    # Pen cap style fields
    if field_name in _PEN_CAP_STYLE_FIELDS:
        if isinstance(value, (str, int)):
            return to_pen_cap_style(value)
        return value

    # Pen join style fields
    if field_name in _PEN_JOIN_STYLE_FIELDS:
        if isinstance(value, (str, int)):
            return to_pen_join_style(value)
        return value

    return value


# ============================================================================
# SAFE SCHEMA → DICT  (replaces asdict() everywhere)
# ============================================================================

def _schema_to_dict(schema) -> Dict[str, Any]:
    """
    Safely convert a dataclass schema to a plain dict by reading attributes
    directly via dataclasses.fields().  Unlike asdict(), this does NOT
    deep-copy values, so it never chokes on Qt objects that may have leaked
    onto the schema (defensive measure).
    """
    return {f.name: getattr(schema, f.name) for f in fields(schema)}


def _create_default_schema(category: StyleCategory) -> Any:
    """Factory function to create the default populated schema for a category."""
    _debug_print(f"_create_default_schema creating schema for {category}")
    schema_map = {
        StyleCategory.CANVAS: CanvasStyleSchema,
        StyleCategory.NODE: NodeStyleSchema,
        StyleCategory.TRACE: TraceStyleSchema,
        StyleCategory.MINIMAP: MinimapStyleSchema,
        StyleCategory.PORT: PortStyleSchema,
    }
    
    schema = schema_map[category]()
    
    # Manager handles the defaults to bypass lambda default_factory bindings!
    if category in BASE_DEFAULTS:
        defaults = copy.deepcopy(BASE_DEFAULTS[category])
        for key, val in defaults.items():
            setattr(schema, key, val)
            
    return schema


class StyleManager(QObject):
    """
    Central manager for all visual styles across the node graph system.
    
    STORAGE INVARIANT:
        Schemas always hold raw Python types (list, str, int, float, bool, dict).
        Qt types (QColor, QFont.Weight, Qt.PenStyle, …) are never stored.
        Conversion to Qt types happens at read-time in get() / get_all().
        Conversion FROM Qt types happens at write-time in update() via _coerce_for_storage().
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
            _debug_print("Creating new StyleManager singleton instance.")
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for testing)."""
        _debug_print("reset_instance called.")
        if cls._instance is not None:
            _debug_print("Calling deleteLater() on current instance.")
            cls._instance.deleteLater()
        cls._instance = None
    
    def __init__(self):
        super().__init__()
        _debug_print("StyleManager.__init__ started.")
        if StyleManager._instance is not None:
            raise RuntimeError("Use StyleManager.instance() to get the singleton.")
        
        # Initialize default schemas with raw Python types only
        self._schemas: Dict[StyleCategory, Any] = {
            cat: _create_default_schema(cat) for cat in StyleCategory
        }
        
        # Subscribers by category (using WeakSet to avoid preventing GC)
        self._subscribers: Dict[StyleCategory, WeakSet] = {
            cat: WeakSet() for cat in StyleCategory
        }
        
        self._current_theme = "dark"
        
        # Cache for converted dict representations (optimization)
        self._dict_cache: Dict[StyleCategory, Optional[Dict[str, Any]]] = {
            cat: None for cat in StyleCategory
        }
        
        # Signal suppression flag for batch updates
        self._suppress_signals = False
        self._pending_changes: Dict[StyleCategory, Dict[str, Any]] = {}
        _debug_print("StyleManager.__init__ completed.")
    
    # ==========================================================================
    # BATCH UPDATES
    # ==========================================================================
    
    @contextmanager
    def batch_update(self):
        """Context manager for batch updates without intermediate signals."""
        _debug_print("Entering batch_update context.")
        self._suppress_signals = True
        self._pending_changes = {cat: {} for cat in StyleCategory}
        try:
            yield
        finally:
            _debug_print("Exiting batch_update context. Emitting accumulated changes.")
            self._suppress_signals = False
            for category, changes in self._pending_changes.items():
                if changes:
                    _debug_print(f"batch_update emitting changes for {category}")
                    self._notify_subscribers(category, changes)
            self._pending_changes = {}
    
    # ==========================================================================
    # REGISTRATION
    # ==========================================================================
    
    def register(self, subscriber: Any, category: StyleCategory) -> None:
        _debug_print(f"register called for subscriber {subscriber} in category {category}")
        self._subscribers[category].add(subscriber)
    
    def unregister(self, subscriber: Any, category: Optional[StyleCategory] = None) -> None:
        _debug_print(f"unregister called for subscriber {subscriber} in category {category}")
        if category is not None:
            self._subscribers[category].discard(subscriber)
        else:
            for sub_set in self._subscribers.values():
                sub_set.discard(subscriber)
    
    # ==========================================================================
    # ACCESS  (read-time conversion: raw → Qt)
    # ==========================================================================
    
    def get(self, category: StyleCategory, key: str, default: Any = None) -> Any:
        """
        Retrieve a single style value, converted to its Qt type.
        Colors → QColor, font weights → QFont.Weight, pen styles → Qt.PenStyle, etc.
        """
        schema = self._schemas.get(category)
        if schema and hasattr(schema, key):
            value = getattr(schema, key)
            return _convert_for_read(key, value)
        return default
    
    def get_all(self, category: StyleCategory) -> Dict[str, Any]:
        """
        Return a dict of all style values for a category, with Qt-type conversion.
        Result is a fresh copy — safe for callers to mutate.
        """
        if self._dict_cache[category] is None:
            schema = self._schemas[category]
            raw_dict = _schema_to_dict(schema)
            
            # Convert every field to its Qt read-type
            converted = {}
            for key, value in raw_dict.items():
                converted[key] = _convert_for_read(key, value)
            
            self._dict_cache[category] = converted
            
        # Return a shallow copy so callers can't corrupt the cache keys
        return dict(self._dict_cache[category])
    
    def get_schema(self, category: StyleCategory) -> Any:
        """Return the raw schema object (contains raw Python types only)."""
        return self._schemas.get(category)
    
    # ==========================================================================
    # UPDATES  (write-time coercion: Qt → raw, or validate raw input)
    # ==========================================================================
    
    def update(self, category: StyleCategory, **kwargs) -> Set[str]:
        """
        Update style values.  Accepts raw Python types or Qt types.
        Qt types are coerced back to raw before storage.
        Color lists/hex strings are validated but stored as-is (lists/hex).
        """
        _debug_print(f"update() called for category {category} with keys: {list(kwargs.keys())}")
        schema = self._schemas.get(category)
        if not schema:
            _debug_print(f"update() failed: schema not found for {category}")
            return set()
        
        changed = set()
        for key, value in kwargs.items():
            if not hasattr(schema, key):
                _debug_print(f"update() skipping unknown key '{key}' for {category}")
                continue
            
            # Coerce Qt types back to raw Python types for storage
            store_value = self._coerce_for_storage(key, value)
            
            current = getattr(schema, key)
            
            if current != store_value:
                _debug_print(f"update() modifying key '{key}'. Old: {current!r}, New: {store_value!r}")
                setattr(schema, key, store_value)
                changed.add(key)
        
        if changed:
            _debug_print(f"update() invalidating cache for {category}.")
            self._dict_cache[category] = None
            
            if self._suppress_signals:
                _debug_print(f"update() accumulating batch changes for {category}.")
                self._pending_changes[category].update({k: kwargs[k] for k in changed})
            else:
                _debug_print(f"update() notifying subscribers immediately for {category}.")
                self._notify_subscribers(category, {k: kwargs[k] for k in changed})
        
        return changed
    
    def _coerce_for_storage(self, key: str, value: Any) -> Any:
        """
        Ensure a value is in raw Python form before storing on the schema.
        - QColor → [r,g,b,a]
        - QFont.Weight → str
        - Qt.PenStyle → str
        - hex color string → [r,g,b,a] list  (normalise to one color format)
        - palette list of QColors → list of lists
        - Everything else passes through unchanged.
        """
        # Handle Qt types passed in directly
        if isinstance(value, QColor):
            return _qcolor_to_list(value)
        if isinstance(value, QFont.Weight):
            return _font_weight_to_str(value)
        if isinstance(value, Qt.PenStyle):
            return _pen_style_to_str(value)
        if isinstance(value, Qt.PenCapStyle):
            return _pen_cap_to_str(value)
        if isinstance(value, Qt.PenJoinStyle):
            return _pen_join_to_str(value)
        
        # Handle palette: list of colors
        if key == _PALETTE_FIELD and isinstance(value, list) and len(value) > 0:
            coerced = []
            for item in value:
                if isinstance(item, QColor):
                    coerced.append(_qcolor_to_list(item))
                elif isinstance(item, (list, tuple)):
                    coerced.append(list(item))
                else:
                    coerced.append(item)
            return coerced
        
        # Handle state_visuals: nested dict with possible QColor sub-values
        if key == _STATE_VISUALS_FIELD and isinstance(value, dict):
            result = {}
            for k, v in value.items():
                if isinstance(v, dict):
                    new_v = {}
                    for vk, vv in v.items():
                        if isinstance(vv, QColor):
                            new_v[vk] = _qcolor_to_list(vv)
                        else:
                            new_v[vk] = vv
                    result[k] = new_v
                else:
                    result[k] = v
            return result
        
        # For known color fields: convert hex strings to list for consistency
        if key in _COLOR_FIELDS:
            if isinstance(value, str) and value.startswith("#"):
                c = QColor(value)
                return [c.red(), c.green(), c.blue(), c.alpha()]
            if isinstance(value, (list, tuple)):
                return list(value)
            return value
        
        # For known enum-string fields, keep as string (already raw)
        # No conversion needed — they're stored as str/int
        
        return value
    
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
        _debug_print(f"apply_theme() called for theme: {theme_name}")
        if theme_name not in THEMES:
            _debug_print(f"apply_theme() Failed: Unknown theme: {theme_name}")
            return False
        
        with self.batch_update():
            _debug_print("apply_theme() Resetting to defaults.")
            self._reset_to_defaults()
            
            theme_overrides = THEMES[theme_name]
            for category, overrides in theme_overrides.items():
                _debug_print(f"apply_theme() applying overrides for {category}")
                self.update(category, **overrides)
        
        self._current_theme = theme_name
        _debug_print(f"apply_theme() emitting theme_changed signal for {theme_name}")
        self.theme_changed.emit(theme_name)
        return True
    
    def register_theme(self, name: str, overrides: Dict[StyleCategory, Dict[str, Any]]) -> None:
        _debug_print(f"register_theme() called for '{name}'")
        THEMES[name] = overrides
    
    def _reset_to_defaults(self) -> None:
        _debug_print("_reset_to_defaults() called.")
        self._schemas = {
            cat: _create_default_schema(cat) for cat in StyleCategory
        }
        for cat in StyleCategory:
            self._dict_cache[cat] = None
    
    # ==========================================================================
    # SERIALIZATION HELPERS (JSON-compatible)
    # ==========================================================================
    
    @staticmethod
    def _serialize_value(obj: Any) -> Any:
        """
        Convert a value into a JSON-safe representation.
        Since schemas now always hold raw types, this is mostly a passthrough
        with recursive dict/list handling for safety.
        """
        if obj is None:
            return None
        elif isinstance(obj, QColor):
            # Defensive — shouldn't happen with the new storage invariant
            return {"__type__": "QColor", "rgba": _qcolor_to_list(obj)}
        elif isinstance(obj, QFont.Weight):
            return {"__type__": "FontWeight", "value": _font_weight_to_str(obj)}
        elif isinstance(obj, Qt.PenStyle):
            return {"__type__": "PenStyle", "value": obj.value}
        elif isinstance(obj, Qt.PenCapStyle):
            return {"__type__": "PenCapStyle", "value": obj.value}
        elif isinstance(obj, Qt.PenJoinStyle):
            return {"__type__": "PenJoinStyle", "value": obj.value}
        elif isinstance(obj, Qt.GlobalColor):
            color = QColor(obj)
            return {"__type__": "QColor", "rgba": _qcolor_to_list(color)}
        elif isinstance(obj, dict):
            return {k: StyleManager._serialize_value(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [StyleManager._serialize_value(v) for v in obj]
        elif isinstance(obj, Enum):
            return {"__type__": "Enum", "class": type(obj).__qualname__, "value": obj.value}
        return obj

    @staticmethod
    def _deserialize_value(obj: Any) -> Any:
        """
        Deserialize JSON-loaded values back to raw Python types.
        Deliberately does NOT produce Qt types — preserves the storage invariant.
        """
        if obj is None:
            return None
        elif isinstance(obj, dict):
            if "__type__" in obj:
                type_name = obj["__type__"]
                if type_name == "QColor":
                    # Deserialize to raw list (not QColor) to preserve storage invariant
                    return list(obj["rgba"])
                elif type_name == "FontWeight":
                    # Deserialize to raw string
                    return obj["value"]
                elif type_name == "PenStyle":
                    return obj["value"]
                elif type_name == "PenCapStyle":
                    return obj["value"]
                elif type_name == "PenJoinStyle":
                    return obj["value"]
            return {k: StyleManager._deserialize_value(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [StyleManager._deserialize_value(v) for v in obj]
        return obj
    
    # ==========================================================================
    # EXPORT / IMPORT (Dict)
    # ==========================================================================
    
    def export_current(self) -> Dict[str, Any]:
        """
        Export the full current state as a JSON-serializable dict.
        Because schemas hold only raw Python types, this is straightforward.
        """
        _debug_print("export_current() called.")
        data = {
            "__meta__": {
                "version": "1.0",
                "base_theme": self._current_theme,
            }
        }
        
        for cat in StyleCategory:
            schema = self._schemas[cat]
            raw_dict = _schema_to_dict(schema)
            data[cat.name] = self._serialize_value(raw_dict)
        
        return data
    
    def export_theme(self, theme_name: str) -> Optional[Dict[str, Any]]:
        _debug_print(f"export_theme() called for {theme_name}")
        if theme_name not in THEMES:
            _debug_print(f"Unknown theme: {theme_name}")
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
        _debug_print(f"import_theme() called for {name}")
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
                _debug_print(f"import_theme() Unknown category in import: {key}")
        
        if overrides:
            self.register_theme(name, overrides)
            if apply:
                self.apply_theme(name)
            return True
        
        return False
    
    # ==========================================================================
    # FILE I/O
    # ==========================================================================
    
    def save_to_file(self, filepath: str, theme_name: Optional[str] = None, indent: int = 2) -> bool:
        _debug_print(f"save_to_file() called. path={filepath}, theme_name={theme_name}")
        try:
            if theme_name:
                data = self.export_theme(theme_name)
                if data is None:
                    return False
            else:
                data = self.export_current()
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            
            _debug_print(f"save_to_file() Successfully saved styles to: {filepath}")
            return True
            
        except (IOError, OSError, TypeError) as e:
            _debug_print(f"save_to_file() Failed to save styles: {e}")
            return False
    
    def load_from_file(self, filepath: str, theme_name: Optional[str] = None, apply: bool = True) -> bool:
        _debug_print(f"load_from_file() called. path={filepath}, theme_name={theme_name}")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if theme_name is None:
                meta = data.get("__meta__", {})
                theme_name = meta.get("theme_name") or meta.get("base_theme")
                if theme_name is None:
                    import os
                    theme_name = os.path.splitext(os.path.basename(filepath))[0]
            
            meta = data.get("__meta__", {})
            is_full_state = "base_theme" in meta and "theme_name" not in meta
            
            if is_full_state and apply:
                with self.batch_update():
                    for cat in StyleCategory:
                        if cat.name in data:
                            cat_data = self._deserialize_value(data[cat.name])
                            if isinstance(cat_data, dict):
                                self.update(cat, **cat_data)
                self._current_theme = theme_name
                _debug_print(f"load_from_file() Loaded and applied full styles from: {filepath}")
                return True
            else:
                success = self.import_theme(theme_name, data, apply=apply)
                if success:
                    action = "applied" if apply else "registered"
                    _debug_print(f"load_from_file() Loaded and {action} theme '{theme_name}' from: {filepath}")
                return success
                
        except FileNotFoundError:
            _debug_print(f"load_from_file() File not found: {filepath}")
            return False
        except json.JSONDecodeError as e:
            _debug_print(f"load_from_file() Invalid JSON in {filepath}: {e}")
            return False
        except (IOError, OSError) as e:
            _debug_print(f"load_from_file() Failed to load styles: {e}")
            return False
    
    def save_current_as_theme(self, theme_name: str, filepath: Optional[str] = None) -> bool:
        """Diff current state against defaults and save as a named theme."""
        _debug_print(f"save_current_as_theme() called. theme_name={theme_name}, path={filepath}")
        overrides: Dict[StyleCategory, Dict[str, Any]] = {}
        
        for cat in StyleCategory:
            default_schema = _create_default_schema(cat)
            default_dict = _schema_to_dict(default_schema)
            current_dict = _schema_to_dict(self._schemas[cat])
            
            diff = {}
            for key, current_val in current_dict.items():
                default_val = default_dict.get(key)
                if current_val != default_val:
                    diff[key] = current_val
            
            if diff:
                overrides[cat] = diff
        
        self.register_theme(theme_name, overrides)
        
        if filepath:
            return self.save_to_file(filepath, theme_name=theme_name)
        
        return True
    
    # ==========================================================================
    # NOTIFICATION
    # ==========================================================================
    
    def _notify_subscribers(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        """Notify all subscribers of changes."""
        _debug_print(f"_notify_subscribers() Emitting Qt Signal style_changed for {category.name}.")
        try:
            self.style_changed.emit(category, changes)
        except Exception as e:
            _debug_print(f"_notify_subscribers() FATAL ERROR emitting Qt signal: {e}")
            
        subscribers_list = list(self._subscribers[category])
        _debug_print(f"_notify_subscribers() Found {len(subscribers_list)} subscribers for {category.name}.")
        
        for subscriber in subscribers_list:
            _debug_print(f"_notify_subscribers() -> Preparing to notify pure python subscriber: {subscriber}")
            
            # PySide6 Safety Check: Validate the underlying C++ object isn't dead
            if isinstance(subscriber, QObject):
                try:
                    # Accessing a trivial property forces PySide6 to check the C++ pointer
                    _ = subscriber.objectName()
                except RuntimeError as e:
                    _debug_print(f"_notify_subscribers() -> [WARNING] C++ object already deleted for {subscriber}. Skipping. Exception: {e}")
                    continue
                except Exception as e:
                    _debug_print(f"_notify_subscribers() -> [WARNING] Unexpected error checking C++ object for {subscriber}: {e}")
            
            try:
                _debug_print(f"_notify_subscribers() -> Checking hasattr 'on_style_changed'")
                has_on_style = hasattr(subscriber, 'on_style_changed')
                _debug_print(f"_notify_subscribers() -> Checking hasattr 'refresh_style'")
                has_refresh = hasattr(subscriber, 'refresh_style')
            except Exception as e:
                _debug_print(f"_notify_subscribers() -> [WARNING] hasattr check failed: {e}")
                continue

            if has_on_style:
                _debug_print(f"_notify_subscribers() -> ENTERING on_style_changed() for {subscriber}")
                try:
                    subscriber.on_style_changed(category, changes)
                    _debug_print(f"_notify_subscribers() -> EXITING on_style_changed() for {subscriber} (SUCCESS)")
                except Exception as e:
                    _debug_print(f"_notify_subscribers() -> [ERROR] on_style_changed failed: {e}")
            elif has_refresh:
                _debug_print(f"_notify_subscribers() -> ENTERING refresh_style() for {subscriber}")
                try:
                    subscriber.refresh_style()
                    _debug_print(f"_notify_subscribers() -> EXITING refresh_style() for {subscriber} (SUCCESS)")
                except Exception as e:
                    _debug_print(f"_notify_subscribers() -> [ERROR] refresh_style failed: {e}")


# ============================================================================ 
# CONVENIENCE FUNCTIONS
# ==============================================================================

def get_style_manager() -> StyleManager:
    return StyleManager.instance()

def get_style(category: StyleCategory, key: str, default: Any = None) -> Any:
    return StyleManager.instance().get(category, key, default)

def update_style(category: StyleCategory, **kwargs) -> Set[str]:
    return StyleManager.instance().update(category, **kwargs)

def apply_theme(theme_name: str) -> bool:
    return StyleManager.instance().apply_theme(theme_name)