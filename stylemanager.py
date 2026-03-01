# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Simplified Style Manager with automatic type-driven conversion.

Conversion strategy:
  - Colors:      Detected by shape (list/tuple of 3-4 numbers) → recursive, no field registry.
  - Qt enums:    Detected by inspecting dataclass type annotations (Union[str, int, Qt.*]).
  - Storage:     Always raw Python types. Qt objects are never stored.
  - Read-time:   get() / get_all() convert raw → Qt automatically.
  - Write-time:  update() coerces Qt → raw automatically.
"""

from __future__ import annotations

import copy
import json
import typing
from dataclasses import dataclass, fields
from enum import Enum, auto
from typing import Dict, Any, Optional, List, Set, Union
from weakref import WeakSet
from contextlib import contextmanager

from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, QObject, Signal

from weave.logger import get_logger
log = get_logger("Stylemanager")

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
# VALUE-SHAPE HELPERS
# ============================================================================

def _is_color_list(val: Any) -> bool:
    """True if val looks like [r, g, b] or [r, g, b, a]."""
    return (isinstance(val, (list, tuple))
            and 3 <= len(val) <= 4
            and all(isinstance(c, (int, float)) for c in val))


# ============================================================================
# FORWARD CONVERTERS  (raw → Qt)
# ============================================================================

def to_qcolor(val) -> QColor:
    """Convert various color representations to QColor."""
    if isinstance(val, QColor):
        return val
    if isinstance(val, str) and val.startswith("#"):
        return QColor(val)
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        r, g, b = int(val[0]), int(val[1]), int(val[2])
        a = int(val[3]) if len(val) > 3 else 255
        return QColor(r, g, b, a)
    return QColor(0, 0, 0, 255)


def to_qfont_weight(val) -> QFont.Weight:
    """Convert string/int to QFont.Weight."""
    if isinstance(val, QFont.Weight):
        return val
    _MAP = {
        "thin": QFont.Weight.Thin, "light": QFont.Weight.Light,
        "normal": QFont.Weight.Normal, "medium": QFont.Weight.Medium,
        "bold": QFont.Weight.Bold, "black": QFont.Weight.Black,
    }
    if isinstance(val, str):
        return _MAP.get(val.lower(), QFont.Weight.Normal)
    if isinstance(val, (int, float)):
        v = int(val)
        if v >= 900: return QFont.Weight.Black
        if v >= 700: return QFont.Weight.Bold
        if v >= 500: return QFont.Weight.Medium
        if v >= 400: return QFont.Weight.Normal
        if v >= 300: return QFont.Weight.Light
        if v >= 100: return QFont.Weight.Thin
    return QFont.Weight.Normal


def to_pen_style(val) -> Qt.PenStyle:
    if isinstance(val, Qt.PenStyle):
        return val
    _MAP = {
        "solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine,
        "dot": Qt.PenStyle.DotLine, "dashdot": Qt.PenStyle.DashDotLine,
        "dashdotdot": Qt.PenStyle.DashDotDotLine,
    }
    if isinstance(val, str):
        return _MAP.get(val.lower(), Qt.PenStyle.SolidLine)
    if isinstance(val, (int, float)) and 1 <= int(val) <= 5:
        return Qt.PenStyle(int(val))
    return Qt.PenStyle.SolidLine


def to_pen_cap_style(val) -> Qt.PenCapStyle:
    if isinstance(val, Qt.PenCapStyle):
        return val
    _MAP = {
        "flat": Qt.PenCapStyle.FlatCap, "square": Qt.PenCapStyle.SquareCap,
        "round": Qt.PenCapStyle.RoundCap,
    }
    if isinstance(val, str):
        return _MAP.get(val.lower(), Qt.PenCapStyle.RoundCap)
    return Qt.PenCapStyle.RoundCap


def to_pen_join_style(val) -> Qt.PenJoinStyle:
    if isinstance(val, Qt.PenJoinStyle):
        return val
    _MAP = {
        "miter": Qt.PenJoinStyle.MiterJoin, "round": Qt.PenJoinStyle.RoundJoin,
        "bevel": Qt.PenJoinStyle.BevelJoin,
    }
    if isinstance(val, str):
        return _MAP.get(val.lower(), Qt.PenJoinStyle.MiterJoin)
    return Qt.PenJoinStyle.MiterJoin


# ============================================================================
# REVERSE CONVERTERS  (Qt → raw for storage)
# ============================================================================

_WEIGHT_TO_STR = {
    QFont.Weight.Thin: "thin", QFont.Weight.Light: "light",
    QFont.Weight.Normal: "normal", QFont.Weight.Medium: "medium",
    QFont.Weight.Bold: "bold", QFont.Weight.Black: "black",
}
_PEN_STYLE_TO_STR = {
    Qt.PenStyle.SolidLine: "solid", Qt.PenStyle.DashLine: "dash",
    Qt.PenStyle.DotLine: "dot", Qt.PenStyle.DashDotLine: "dashdot",
    Qt.PenStyle.DashDotDotLine: "dashdotdot",
}
_PEN_CAP_TO_STR = {
    Qt.PenCapStyle.FlatCap: "flat", Qt.PenCapStyle.SquareCap: "square",
    Qt.PenCapStyle.RoundCap: "round",
}
_PEN_JOIN_TO_STR = {
    Qt.PenJoinStyle.MiterJoin: "miter", Qt.PenJoinStyle.RoundJoin: "round",
    Qt.PenJoinStyle.BevelJoin: "bevel",
}


# ============================================================================
# TYPE-ANNOTATION INTROSPECTION  (replaces field-name sets)
# ============================================================================

# Qt types we care about, paired with their read-time converter
_QT_ENUM_CONVERTERS: Dict[type, callable] = {
    QFont.Weight:      to_qfont_weight,
    Qt.PenStyle:       to_pen_style,
    Qt.PenCapStyle:    to_pen_cap_style,
    Qt.PenJoinStyle:   to_pen_join_style,
}

# Cache: schema_class → { field_name: converter_fn }
_enum_field_cache: Dict[type, Dict[str, callable]] = {}


def _get_enum_fields(schema_class: type) -> Dict[str, callable]:
    """
    Inspect a dataclass's type annotations and return a mapping of
    field_name → converter for any field whose Union includes a Qt enum type.
    Cached per schema class.
    """
    if schema_class in _enum_field_cache:
        return _enum_field_cache[schema_class]

    result: Dict[str, callable] = {}
    try:
        hints = typing.get_type_hints(schema_class)
    except Exception:
        hints = {}

    for name, hint in hints.items():
        # Unwrap Union / Optional to get the inner types
        args = typing.get_args(hint)
        check = args if args else (hint,)
        for qt_type, converter in _QT_ENUM_CONVERTERS.items():
            if qt_type in check:
                result[name] = converter
                break

    _enum_field_cache[schema_class] = result
    return result


# ============================================================================
# RECURSIVE READ CONVERSION  (raw → Qt)
# ============================================================================

def _deep_convert_for_read(value: Any) -> Any:
    """
    Recursively convert raw stored values to Qt types:
      - Any list/tuple of 3-4 numbers → QColor
      - Nested dicts / lists are traversed recursively
    Does NOT handle enum fields (font weight, pen style) — those are
    handled separately via type-annotation lookup.
    """
    if isinstance(value, QColor):
        return value
    if _is_color_list(value):
        return to_qcolor(value)
    if isinstance(value, dict):
        return {k: _deep_convert_for_read(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_convert_for_read(item) for item in value]
    return value


def _convert_field_for_read(field_name: str, value: Any, enum_fields: Dict[str, callable]) -> Any:
    """
    Convert a single schema field value for the caller.
    1. If the field is a known Qt-enum field (from type annotations), apply its converter.
    2. Otherwise, recursively convert any color-shaped data.
    """
    if value is None:
        return None

    # Qt enum fields (font weight, pen style, etc.) — identified by annotation
    converter = enum_fields.get(field_name)
    if converter is not None:
        if not isinstance(value, (QFont.Weight, Qt.PenStyle, Qt.PenCapStyle, Qt.PenJoinStyle)):
            return converter(value)
        return value

    # Everything else: recursive color detection
    return _deep_convert_for_read(value)


# ============================================================================
# RECURSIVE WRITE COERCION  (Qt → raw for storage)
# ============================================================================

def _deep_coerce_for_storage(value: Any) -> Any:
    """
    Recursively convert any Qt objects back to raw Python types.
    Also normalises hex color strings to [r,g,b,a] lists.
    """
    if isinstance(value, QColor):
        return [value.red(), value.green(), value.blue(), value.alpha()]
    if isinstance(value, QFont.Weight):
        return _WEIGHT_TO_STR.get(value, "normal")
    if isinstance(value, Qt.PenStyle):
        return _PEN_STYLE_TO_STR.get(value, "solid")
    if isinstance(value, Qt.PenCapStyle):
        return _PEN_CAP_TO_STR.get(value, "round")
    if isinstance(value, Qt.PenJoinStyle):
        return _PEN_JOIN_TO_STR.get(value, "miter")
    if isinstance(value, Qt.GlobalColor):
        c = QColor(value)
        return [c.red(), c.green(), c.blue(), c.alpha()]
    if isinstance(value, str) and value.startswith("#"):
        c = QColor(value)
        if c.isValid():
            return [c.red(), c.green(), c.blue(), c.alpha()]
        return value
    if isinstance(value, dict):
        return {k: _deep_coerce_for_storage(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_coerce_for_storage(item) for item in value]
    return value


# ============================================================================
# SCHEMA HELPERS
# ============================================================================

_SCHEMA_MAP: Dict[StyleCategory, type] = {
    StyleCategory.CANVAS:  CanvasStyleSchema,
    StyleCategory.NODE:    NodeStyleSchema,
    StyleCategory.TRACE:   TraceStyleSchema,
    StyleCategory.MINIMAP: MinimapStyleSchema,
    StyleCategory.PORT:    PortStyleSchema,
}


def _schema_to_dict(schema) -> Dict[str, Any]:
    """Convert a dataclass schema to a plain dict (no deep copy, no Qt issues)."""
    return {f.name: getattr(schema, f.name) for f in fields(schema)}


def _create_default_schema(category: StyleCategory) -> Any:
    """Create a default-populated schema, applying BASE_DEFAULTS on top."""
    schema = _SCHEMA_MAP[category]()
    if category in BASE_DEFAULTS:
        for key, val in copy.deepcopy(BASE_DEFAULTS[category]).items():
            setattr(schema, key, val)
    return schema


# ============================================================================
# STYLE MANAGER
# ============================================================================

class StyleManager(QObject):
    """
    Central manager for all visual styles across the node graph system.
    
    Storage invariant:
        Schemas always hold raw Python types (list, str, int, float, bool, dict).
        Qt types are produced at read-time and coerced away at write-time.
    """
    
    style_changed = Signal(object, dict)
    theme_changed = Signal(str)
    
    _instance: Optional['StyleManager'] = None
    
    @classmethod
    def instance(cls) -> 'StyleManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None:
            cls._instance.deleteLater()
        cls._instance = None
    
    def __init__(self):
        super().__init__()
        if StyleManager._instance is not None:
            raise RuntimeError("Use StyleManager.instance() to get the singleton.")
        
        self._schemas: Dict[StyleCategory, Any] = {
            cat: _create_default_schema(cat) for cat in StyleCategory
        }
        self._subscribers: Dict[StyleCategory, WeakSet] = {
            cat: WeakSet() for cat in StyleCategory
        }
        self._current_theme = "dark"
        self._dict_cache: Dict[StyleCategory, Optional[Dict[str, Any]]] = {
            cat: None for cat in StyleCategory
        }
        self._suppress_signals = False
        self._pending_changes: Dict[StyleCategory, Dict[str, Any]] = {}
    
    # ==========================================================================
    # BATCH UPDATES
    # ==========================================================================
    
    @contextmanager
    def batch_update(self):
        self._suppress_signals = True
        self._pending_changes = {cat: {} for cat in StyleCategory}
        try:
            yield
        finally:
            self._suppress_signals = False
            for category, changes in self._pending_changes.items():
                if changes:
                    self._notify_subscribers(category, changes)
            self._pending_changes = {}
    
    # ==========================================================================
    # REGISTRATION
    # ==========================================================================
    
    def register(self, subscriber: Any, category: StyleCategory) -> None:
        self._subscribers[category].add(subscriber)
    
    def unregister(self, subscriber: Any, category: Optional[StyleCategory] = None) -> None:
        if category is not None:
            self._subscribers[category].discard(subscriber)
        else:
            for sub_set in self._subscribers.values():
                sub_set.discard(subscriber)
    
    # ==========================================================================
    # ACCESS  (read-time: raw → Qt)
    # ==========================================================================
    
    def get(self, category: StyleCategory, key: str, default: Any = None) -> Any:
        """Retrieve a single style value, auto-converted to its Qt type."""
        schema = self._schemas.get(category)
        if schema and hasattr(schema, key):
            value = getattr(schema, key)
            enum_fields = _get_enum_fields(type(schema))
            return _convert_field_for_read(key, value, enum_fields)
        return default
    
    def get_all(self, category: StyleCategory) -> Dict[str, Any]:
        """
        Return all style values for a category with Qt-type conversion.
        Colors are always QColor, font weights are QFont.Weight, etc.
        """
        if self._dict_cache[category] is None:
            schema = self._schemas[category]
            raw = _schema_to_dict(schema)
            enum_fields = _get_enum_fields(type(schema))
            self._dict_cache[category] = {
                k: _convert_field_for_read(k, v, enum_fields)
                for k, v in raw.items()
            }
        return dict(self._dict_cache[category])
    
    def get_schema(self, category: StyleCategory) -> Any:
        """Return the raw schema object (raw Python types only)."""
        return self._schemas.get(category)
    
    # ==========================================================================
    # UPDATES  (write-time: Qt → raw)
    # ==========================================================================
    
    def update(self, category: StyleCategory, **kwargs) -> Set[str]:
        """
        Update style values. Accepts raw Python types or Qt types.
        Qt types are coerced to raw before storage.
        """
        schema = self._schemas.get(category)
        if not schema:
            return set()
        
        changed = set()
        for key, value in kwargs.items():
            if not hasattr(schema, key):
                continue
            
            store_value = _deep_coerce_for_storage(value)
            current = getattr(schema, key)
            
            if current != store_value:
                setattr(schema, key, store_value)
                changed.add(key)
        
        if changed:
            self._dict_cache[category] = None
            if self._suppress_signals:
                self._pending_changes[category].update({k: kwargs[k] for k in changed})
            else:
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
        if theme_name not in THEMES:
            return False
        
        with self.batch_update():
            self._reset_to_defaults()
            for category, overrides in THEMES[theme_name].items():
                self.update(category, **overrides)
        
        self._current_theme = theme_name
        self.theme_changed.emit(theme_name)
        return True
    
    def register_theme(self, name: str, overrides: Dict[StyleCategory, Dict[str, Any]]) -> None:
        THEMES[name] = overrides
    
    def _reset_to_defaults(self) -> None:
        self._schemas = {
            cat: _create_default_schema(cat) for cat in StyleCategory
        }
        for cat in StyleCategory:
            self._dict_cache[cat] = None
    
    # ==========================================================================
    # SERIALIZATION  (JSON-compatible)
    # ==========================================================================
    
    @staticmethod
    def _serialize_value(obj: Any) -> Any:
        """
        Convert to JSON-safe form. Schemas hold raw types so this is mostly
        a passthrough, but defensively handles any Qt objects that slip through.
        """
        return _deep_coerce_for_storage(obj) if obj is not None else None
    
    @staticmethod
    def _deserialize_value(obj: Any) -> Any:
        """
        Deserialize JSON values back to raw Python types.
        Handles the legacy {__type__: ...} wrappers for backward compatibility.
        """
        if obj is None:
            return None
        if isinstance(obj, dict):
            if "__type__" in obj:
                t = obj["__type__"]
                if t == "QColor":
                    return list(obj["rgba"])
                if t == "FontWeight":
                    return obj["value"]
                if t in ("PenStyle", "PenCapStyle", "PenJoinStyle"):
                    return obj["value"]
            return {k: StyleManager._deserialize_value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [StyleManager._deserialize_value(v) for v in obj]
        return obj
    
    # ==========================================================================
    # EXPORT / IMPORT
    # ==========================================================================
    
    def export_current(self) -> Dict[str, Any]:
        data = {
            "__meta__": {"version": "1.0", "base_theme": self._current_theme}
        }
        for cat in StyleCategory:
            raw = _schema_to_dict(self._schemas[cat])
            data[cat.name] = self._serialize_value(raw)
        return data
    
    def export_theme(self, theme_name: str) -> Optional[Dict[str, Any]]:
        if theme_name not in THEMES:
            return None
        data = {
            "__meta__": {"version": "1.0", "theme_name": theme_name}
        }
        for cat, overrides in THEMES[theme_name].items():
            data[cat.name] = self._serialize_value(overrides)
        return data
    
    def import_theme(self, name: str, data: Dict[str, Any], apply: bool = False) -> bool:
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
                pass
        
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
        try:
            data = self.export_theme(theme_name) if theme_name else self.export_current()
            if data is None:
                return False
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            return True
        except (IOError, OSError, TypeError) as e:
            _debug_print(f"save_to_file() failed: {e}")
            return False
    
    def load_from_file(self, filepath: str, theme_name: Optional[str] = None, apply: bool = True) -> bool:
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
                return True
            else:
                return self.import_theme(theme_name, data, apply=apply)
                
        except (FileNotFoundError, json.JSONDecodeError, IOError, OSError) as e:
            _debug_print(f"load_from_file() failed: {e}")
            return False
    
    def save_current_as_theme(self, theme_name: str, filepath: Optional[str] = None) -> bool:
        overrides: Dict[StyleCategory, Dict[str, Any]] = {}
        for cat in StyleCategory:
            default_dict = _schema_to_dict(_create_default_schema(cat))
            current_dict = _schema_to_dict(self._schemas[cat])
            diff = {k: v for k, v in current_dict.items() if v != default_dict.get(k)}
            if diff:
                overrides[cat] = diff
        
        self.register_theme(theme_name, overrides)
        return self.save_to_file(filepath, theme_name=theme_name) if filepath else True
    
    # ==========================================================================
    # NOTIFICATION
    # ==========================================================================
    
    def _notify_subscribers(self, category: StyleCategory, changes: Dict[str, Any]) -> None:
        try:
            self.style_changed.emit(category, changes)
        except Exception as e:
            _debug_print(f"Signal emission failed: {e}")
        
        for subscriber in list(self._subscribers[category]):
            # Guard against dead C++ objects
            if isinstance(subscriber, QObject):
                try:
                    _ = subscriber.objectName()
                except RuntimeError:
                    continue
            
            try:
                if hasattr(subscriber, 'on_style_changed'):
                    subscriber.on_style_changed(category, changes)
                elif hasattr(subscriber, 'refresh_style'):
                    subscriber.refresh_style()
            except Exception as e:
                _debug_print(f"Subscriber notification failed for {subscriber}: {e}")


# ============================================================================ 
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_style_manager() -> StyleManager:
    return StyleManager.instance()

def get_style(category: StyleCategory, key: str, default: Any = None) -> Any:
    return StyleManager.instance().get(category, key, default)

def update_style(category: StyleCategory, **kwargs) -> Set[str]:
    return StyleManager.instance().update(category, **kwargs)

def apply_theme(theme_name: str) -> bool:
    return StyleManager.instance().apply_theme(theme_name)