# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

serializer.py - Comprehensive Graph Serializer v3
-----------------------------------------------------
Handles full application state serialization/deserialization for the Node Canvas.

Captures:
- Canvas settings (grid, background, snapping, shake-to-disconnect)
- View state (center position, zoom/transform)
- Minimap state (corner, pinned, minimized, dimensions)
- Style/theme state (current theme + any overrides)
- Node state via clean ownership boundary:
    Serializer adds: id, class (graph metadata)
    QtNode.get_state(): pos, size, colors, port defs, minimized, node_state
    BaseControlNode.get_state(): widget_data (via WeaveWidgetCore), dataflow metadata
- Connections (source/target node IDs and port indices)

Format v3:
{
    "meta": { "version": "3.0", "timestamp": "...", "app_name": "QtNodeCanvas" },
    "canvas": { ... },
    "view": { ... },
    "minimap": { ... },
    "style": { ... },
    "nodes": [
        {
            "id": "...", "class": "FloatNode",           # serializer
            "title", "width", "height", "pos", "config",  # QtNode
            "colors", "inputs", "outputs", "minimized",   # QtNode
            "node_state",                                  # QtNode
            "widget_data": { ... },                        # BaseControlNode → WeaveWidgetCore
            "dataflow": { ... },                           # BaseControlNode
        },
        ...
    ],
    "connections": [ ... ]
}
"""

import json
import uuid
import time
from typing import Dict, Any, Type, Optional, List

from PySide6.QtGui import QColor, QFont, QTransform
from PySide6.QtCore import Qt, QPointF, QRectF

from logger import get_logger
log = get_logger("Serializer")

from weave.portutils import ConnectionFactory


DEBUG_SERIALIZER = True

def _debug_print(msg: str):
    if DEBUG_SERIALIZER:
        print(f"[Serializer DEBUG] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Helpers for QColor / QTransform serialization
# ---------------------------------------------------------------------------

def _color_to_list(color: QColor) -> List[int]:
    """Serialize a QColor to [r, g, b, a]."""
    if color is None:
        return [0, 0, 0, 255]
    return [color.red(), color.green(), color.blue(), color.alpha()]


def _list_to_color(lst: List[int]) -> QColor:
    """Deserialize [r, g, b, a] back to QColor."""
    if not lst or len(lst) < 3:
        return QColor(0, 0, 0, 255)
    a = lst[3] if len(lst) > 3 else 255
    return QColor(lst[0], lst[1], lst[2], a)


def _transform_to_list(t: QTransform) -> List[float]:
    """Serialize a QTransform to its 6 core components [m11, m12, m21, m22, dx, dy]."""
    return [t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()]


def _list_to_transform(lst: List[float]) -> QTransform:
    """Deserialize 6-element list back to QTransform."""
    if not lst or len(lst) < 6:
        return QTransform()
    return QTransform(lst[0], lst[1], lst[2], lst[3], lst[4], lst[5])


# ---------------------------------------------------------------------------
# Custom JSON Encoder/Decoder for Qt types
# ---------------------------------------------------------------------------

class _QtJsonEncoder(json.JSONEncoder):
    """JSON encoder that converts Qt types to tagged dicts for round-tripping.
    
    Uses {"__type__": "QColor", "rgba": [r,g,b,a]} format — the same convention
    used by the StyleManager — so that the decoder can reconstruct the
    original objects on load.
    """
    def default(self, obj):
        if isinstance(obj, QColor):
            return {"__type__": "QColor",
                    "rgba": _color_to_list(obj)}
        if isinstance(obj, QFont.Weight):
            return {"__type__": "FontWeight", "value": int(obj)}
        if isinstance(obj, QPointF):
            return {"__type__": "QPointF", "xy": [obj.x(), obj.y()]}
        if isinstance(obj, QRectF):
            return {"__type__": "QRectF",
                    "xywh": [obj.x(), obj.y(), obj.width(), obj.height()]}
        if isinstance(obj, QTransform):
            return {"__type__": "QTransform",
                    "matrix": _transform_to_list(obj)}
        # Let the base class raise TypeError for truly unknown types
        return super().default(obj)


def _qt_object_hook(obj: dict) -> Any:
    """JSON object_hook that reconstructs Qt types from tagged dicts."""
    tag = obj.get("__type__")
    if tag is None:
        return obj

    if tag == "QColor":
        rgba = obj.get("rgba", [0, 0, 0, 255])
        a = rgba[3] if len(rgba) > 3 else 255
        return QColor(rgba[0], rgba[1], rgba[2], a)

    if tag == "FontWeight":
        v = obj.get("value", 400)
        if v >= 900: return QFont.Weight.Black
        if v >= 700: return QFont.Weight.Bold
        if v >= 500: return QFont.Weight.Medium
        if v >= 400: return QFont.Weight.Normal
        if v >= 300: return QFont.Weight.Light
        if v >= 100: return QFont.Weight.Thin
        return QFont.Weight.Normal

    if tag == "PenStyle":
        v = obj.get("value", 1)
        if 1 <= v <= 5:
            return Qt.PenStyle(v)
        return Qt.PenStyle.SolidLine

    if tag == "PenCapStyle":
        _CAP_MAP = {0x00: Qt.PenCapStyle.FlatCap, 0x10: Qt.PenCapStyle.SquareCap,
                    0x20: Qt.PenCapStyle.RoundCap}
        return _CAP_MAP.get(obj.get("value"), Qt.PenCapStyle.RoundCap)

    if tag == "PenJoinStyle":
        _JOIN_MAP = {0x00: Qt.PenJoinStyle.MiterJoin, 0x40: Qt.PenJoinStyle.BevelJoin,
                     0x80: Qt.PenJoinStyle.RoundJoin}
        return _JOIN_MAP.get(obj.get("value"), Qt.PenJoinStyle.MiterJoin)

    if tag == "QPointF":
        xy = obj.get("xy", [0, 0])
        return QPointF(xy[0], xy[1])

    if tag == "QRectF":
        xywh = obj.get("xywh", [0, 0, 0, 0])
        return QRectF(xywh[0], xywh[1], xywh[2], xywh[3])

    if tag == "QTransform":
        return _list_to_transform(obj.get("matrix", []))

    # Unknown tagged type — return dict as-is
    return obj


# ===========================================================================
# GRAPH SERIALIZER
# ===========================================================================

class GraphSerializer:
    """
    Comprehensive serializer for the entire Node Canvas application state.
    """

    FORMAT_VERSION = "3.0"

    def __init__(self, registry_map: Dict[str, Type]) -> None:
        """
        Args:
            registry_map: Map of class name strings to their Python types.
                          e.g. {'FloatNode': FloatNode, 'IntNode': IntNode}
        """
        self.node_registry = registry_map

    # =======================================================================
    # SERIALIZE (Save)
    # =======================================================================

    def serialize(
        self,
        canvas,
        view=None,
        minimap=None,
        include_style: bool = True,
    ) -> str:
        """
        Serialize the entire application state to a JSON string.

        Args:
            canvas:  The QtNodeCanvas (QGraphicsScene subclass).
            view:    Optional QGraphicsView (QtCanvasView) for viewport state.
            minimap: Optional QtNodeMinimap for minimap state.
            include_style: Whether to embed full style/theme data.

        Returns:
            A JSON string representing the complete application state.
        """
        data: Dict[str, Any] = {
            "meta": self._serialize_meta(),
            "canvas": self._serialize_canvas(canvas),
            "nodes": [],
            "connections": [],
        }

        # Optional sections
        if view is not None:
            data["view"] = self._serialize_view(view)

        if minimap is not None:
            data["minimap"] = self._serialize_minimap(minimap)

        if include_style:
            data["style"] = self._serialize_style(canvas)

        # Nodes & Connections
        node_id_map: Dict[Any, str] = {}  # object -> unique_id

        # Get nodes from the node manager's tracked list (preferred),
        # canvas.nodes property, or fall back to scanning scene items.
        nodes: list = []
        if hasattr(canvas, '_node_manager'):
            nodes = canvas._node_manager.nodes
        elif hasattr(canvas, 'nodes'):
            nodes = list(canvas.nodes)
        else:
            nodes = [item for item in canvas.items()
                     if hasattr(item, "get_state") and hasattr(item, "unique_id")]

        for node in nodes:
            if not hasattr(node, "get_state"):
                continue
            node_data = self._serialize_node(node)
            data["nodes"].append(node_data)
            # CRITICAL: Use the SAME id that was written into node_data.
            # The old code called getattr(node, "unique_id", str(uuid.uuid4()))
            # separately, which generated a DIFFERENT UUID when unique_id was
            # missing — causing connections to reference non-existent node IDs.
            node_id_map[node] = node_data["id"]

        data["connections"] = self._serialize_connections(canvas, node_id_map)

        return json.dumps(data, indent=2, ensure_ascii=False, cls=_QtJsonEncoder)

    # -- Meta ---------------------------------------------------------------

    def _serialize_meta(self) -> Dict[str, Any]:
        return {
            "version": self.FORMAT_VERSION,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "app_name": "QtNodeCanvas",
        }

    # -- Canvas Settings ----------------------------------------------------

    def _serialize_canvas(self, canvas) -> Dict[str, Any]:
        """Capture grid, background, snapping, and layout settings."""
        scene_rect = canvas.sceneRect()
        result: Dict[str, Any] = {
            "scene_rect": [scene_rect.x(), scene_rect.y(),
                           scene_rect.width(), scene_rect.height()],
        }

        # Cached canvas properties (available as attributes or properties)
        _safe = _safe_attr
        result["bg_color"] = _color_to_list(_safe(canvas, "bg_color", "_cached_bg_color"))
        result["grid_color"] = _color_to_list(_safe(canvas, "grid_color", "_cached_grid_color"))
        result["grid_spacing"] = _safe(canvas, "grid_spacing", "_cached_grid_spacing", default=20)
        result["grid_line_width"] = _safe(canvas, "grid_line_width", "_cached_grid_line_width", default=2.0)
        result["snapping_enabled"] = _safe(canvas, "snapping_enabled", "_cached_snapping_enabled", default=True)
        result["connection_snap_radius"] = _safe(canvas, "connection_snap_radius", "_cached_connection_snap_radius", default=25.0)
        result["shake_to_disconnect"] = _safe(canvas, "shake_to_disconnect", "_cached_shake_to_disconnect", default=False)
        result["max_visible_grid_lines"] = _safe(canvas, None, "_cached_max_visible_grid_lines", default=5000)

        # Grid type (enum -> int)
        grid_type = _safe(canvas, "grid_type", "_cached_grid_type", default=1)
        result["grid_type"] = grid_type.value if hasattr(grid_type, "value") else int(grid_type)

        return result

    # -- View State ---------------------------------------------------------

    def _serialize_view(self, view) -> Dict[str, Any]:
        """Capture the viewport center, zoom level, and full transform."""
        center = view.mapToScene(
            view.viewport().width() // 2,
            view.viewport().height() // 2,
        )
        transform = view.transform()
        return {
            "center": [center.x(), center.y()],
            "zoom": transform.m11(),  # uniform scale assumed
            "transform": _transform_to_list(transform),
            "viewport_size": [view.viewport().width(), view.viewport().height()],
        }

    # -- Minimap State ------------------------------------------------------

    def _serialize_minimap(self, minimap) -> Dict[str, Any]:
        """Capture minimap corner, pin state, minimized state, and dimensions."""
        result: Dict[str, Any] = {}

        # Corner (enum name)
        corner = getattr(minimap, "_current_corner", None)
        result["corner"] = corner.name if hasattr(corner, "name") else "TOP_RIGHT"

        # Pin / Auto-hide
        result["is_pinned"] = not getattr(minimap, "_auto_hide_enabled", False)
        result["is_minimized"] = getattr(minimap, "_is_minimized", False)

        # Dimensions from config
        config = getattr(minimap, "_config", {})
        result["width"] = config.get("width", 240)
        result["height"] = config.get("height", 180)

        return result

    # -- Style/Theme State --------------------------------------------------

    def _serialize_style(self, canvas) -> Dict[str, Any]:
        """Capture the current theme name and full style export."""
        result: Dict[str, Any] = {}

        sm = getattr(canvas, "_style_manager", None)
        if sm is None:
            return result

        result["theme"] = getattr(sm, "_current_theme", "dark")

        # Use StyleManager's own export for a complete, portable snapshot
        if hasattr(sm, "export_current"):
            try:
                result["full_state"] = sm.export_current()
            except Exception as e:
                log.error(f"Style export failed: {e}")

        return result

    # -- Single Node --------------------------------------------------------

    def _serialize_node(self, node) -> Dict[str, Any]:
        """
        Serialize a single node to a dict.
        """
        # Node's own protocol captures everything
        try:
            node_data = node.get_state()
            if not isinstance(node_data, dict):
                node_data = {}
        except Exception:
            node_data = {}

        # Graph-level metadata (not owned by the node)
        # Generate a stable ID: use existing unique_id, or create one and
        # stamp it back onto the node so all subsequent references are consistent.
        node_id = getattr(node, "unique_id", None)
        if not node_id:
            node_id = str(uuid.uuid4())
            node.unique_id = node_id
        node_data["id"] = node_id
        node_data["class"] = node.__class__.__name__

        # Pre-process: QFont.Weight is int-compatible in PySide6, so
        # json.JSONEncoder serializes it as a bare int without ever
        # calling default(). We must convert it to a tagged dict here
        # so the object_hook can reconstruct it on load.
        self._sanitize_font_weights(node_data)

        return node_data

    @staticmethod
    def _sanitize_font_weights(data: Any) -> None:
        """
        Walk a dict/list structure in-place, replacing any Qt enum values
        with tagged dicts so the object_hook can reconstruct them on load.

        Necessary because PySide6 enum values pass isinstance(x, int),
        so json.JSONEncoder silently serializes them as bare ints — the
        custom default() method is never called.
        """
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, QFont.Weight):
                    data[key] = {"__type__": "FontWeight", "value": int(val)}
                elif isinstance(val, Qt.PenStyle):
                    data[key] = {"__type__": "PenStyle", "value": int(val)}
                elif isinstance(val, Qt.PenCapStyle):
                    data[key] = {"__type__": "PenCapStyle", "value": int(val)}
                elif isinstance(val, Qt.PenJoinStyle):
                    data[key] = {"__type__": "PenJoinStyle", "value": int(val)}
                elif isinstance(val, (dict, list)):
                    GraphSerializer._sanitize_font_weights(val)
        elif isinstance(data, list):
            for i, val in enumerate(data):
                if isinstance(val, QFont.Weight):
                    data[i] = {"__type__": "FontWeight", "value": int(val)}
                elif isinstance(val, Qt.PenStyle):
                    data[i] = {"__type__": "PenStyle", "value": int(val)}
                elif isinstance(val, Qt.PenCapStyle):
                    data[i] = {"__type__": "PenCapStyle", "value": int(val)}
                elif isinstance(val, Qt.PenJoinStyle):
                    data[i] = {"__type__": "PenJoinStyle", "value": int(val)}
                elif isinstance(val, (dict, list)):
                    GraphSerializer._sanitize_font_weights(val)

    # -- Connections --------------------------------------------------------

    def _serialize_connections(
        self, canvas, node_id_map: Dict[Any, str]
        ) -> List[Dict[str, Any]]:
        """Serialize all NodeTrace connections in the scene."""
        connections: List[Dict[str, Any]] = []

        # Fix: Use the correct import path for the weave package
        try:
            from weave.node.node_trace import NodeTrace
        except ImportError:
            try:
                from qt_nodetrace import NodeTrace # Fallback for legacy
            except ImportError:
                log.warning("Could not import NodeTrace. Connections will not be saved.")
                return connections

        for item in canvas.items():
            if not isinstance(item, NodeTrace):
                continue

            src = getattr(item, "source", None)
            dst = getattr(item, "target", None)
            if not (src and dst):
                continue

            src_node = getattr(src, "node", None)
            dst_node = getattr(dst, "node", None)
            if src_node not in node_id_map or dst_node not in node_id_map:
                continue

            try:
                src_outputs = getattr(src_node, "outputs", [])
                dst_inputs = getattr(dst_node, "inputs", [])
                
                # Fix: Safely normalize dicts to lists
                src_outputs_list = list(src_outputs.values()) if isinstance(src_outputs, dict) else list(src_outputs)
                dst_inputs_list = list(dst_inputs.values()) if isinstance(dst_inputs, dict) else list(dst_inputs)

                src_idx = src_outputs_list.index(src)
                dst_idx = dst_inputs_list.index(dst)

                connections.append({
                    "source_id": node_id_map[src_node],
                    "source_port": src_idx,
                    "source_port_name": getattr(src, "name", None),
                    "target_id": node_id_map[dst_node],
                    "target_port": dst_idx,
                    "target_port_name": getattr(dst, "name", None),
                })
            except (ValueError, AttributeError) as e:
                log.debug(f"Skipping connection on non-indexed port: {e}")

        return connections

    # =======================================================================
    # DESERIALIZE (Load)
    # =======================================================================

    def deserialize(
        self,
        canvas,
        json_str: str,
        view=None,
        minimap=None,
        restore_style: bool = True,
        clear_first: bool = True,
    ) -> bool:
        """
        Restore the full application state from a JSON string.

        Restore order:
            1. Clear canvas
            2. Style/theme (so nodes get correct colors)
            3. Canvas settings (grid, bg, snapping)
            4. ALL nodes (instantiate, restore_state, add_node)
            5. View (zoom, center)
            6. Minimap (corner, pin, minimize)
            7. Connections (LAST — all nodes and ports guaranteed to exist)
            8. Mark nodes dirty (trigger evaluation with connections in place)

        Args:
            canvas:        The QtNodeCanvas to restore into.
            json_str:      The JSON string produced by serialize().
            view:          Optional QGraphicsView to restore viewport state.
            minimap:       Optional QtNodeMinimap to restore minimap state.
            restore_style: Whether to restore the saved theme/styles.
            clear_first:   Whether to clear the scene before restoring.

        Returns:
            True on success, False on failure.
        """
        _debug_print("=" * 70)
        _debug_print("DESERIALIZE START")
        _debug_print("=" * 70)

        try:
            data = json.loads(json_str, object_hook=_qt_object_hook)
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON: {e}")
            _debug_print(f"ABORT: Invalid JSON: {e}")
            return False

        version = data.get("meta", {}).get("version", "1.0")
        _debug_print(f"Format version: {version}")

        # ---------------------------------------------------------------
        # 1. Clear
        # ---------------------------------------------------------------
        if clear_first:
            _debug_print("Phase 1: Clearing canvas")
            try:
                self._clear_canvas(canvas)
            except Exception as e:
                log.error(f"Exception in _clear_canvas: {type(e).__name__}: {e}")
                _debug_print(f"  FAILED: {e}")

        # ---------------------------------------------------------------
        # 2. Style (restore BEFORE nodes so colors/settings are correct)
        # ---------------------------------------------------------------
        if restore_style and "style" in data:
            _debug_print("Phase 2: Restoring style/theme")
            try:
                self._restore_style(canvas, data["style"])
                _debug_print(f"  Theme: {data['style'].get('theme', '?')}")
            except Exception as e:
                log.error(f"Exception in _restore_style: {type(e).__name__}: {e}")
                _debug_print(f"  FAILED: {e}")

        # ---------------------------------------------------------------
        # 3. Canvas settings
        # ---------------------------------------------------------------
        if "canvas" in data:
            _debug_print("Phase 3: Restoring canvas settings")
            try:
                self._restore_canvas(canvas, data["canvas"])
            except Exception as e:
                log.error(f"Exception in _restore_canvas: {type(e).__name__}: {e}")
                _debug_print(f"  FAILED: {e}")

        # ---------------------------------------------------------------
        # 4. ALL nodes — create every node before any connections
        # ---------------------------------------------------------------
        uuid_map: Dict[str, Any] = {}  # id string -> node instance
        node_list = data.get("nodes", [])
        _debug_print(f"Phase 4: Restoring {len(node_list)} nodes")

        for i, n_data in enumerate(node_list):
            cls_name = n_data.get("class", "???")
            node_id = n_data.get("id", "???")
            _debug_print(f"  Node [{i}] class={cls_name} id={node_id[:12]}...")

            try:
                node = self._restore_node(canvas, n_data)
                if node is not None:
                    uuid_map[getattr(node, "unique_id", "")] = node

                    # Diagnostic: report port counts after restore + add_node
                    inputs = getattr(node, "inputs", [])
                    outputs = getattr(node, "outputs", [])
                    inputs_list = list(inputs.values()) if isinstance(inputs, dict) else list(inputs)
                    outputs_list = list(outputs.values()) if isinstance(outputs, dict) else list(outputs)

                    input_names = [getattr(p, 'name', '?') for p in inputs_list]
                    output_names = [getattr(p, 'name', '?') for p in outputs_list]
                    _debug_print(
                        f"    OK: inputs={len(inputs_list)} {input_names}, "
                        f"outputs={len(outputs_list)} {output_names}"
                    )
                else:
                    _debug_print(f"    FAILED: _restore_node returned None")
            except Exception as e:
                log.error(f"Exception restoring node {cls_name}: {type(e).__name__}: {e}")
                _debug_print(f"    EXCEPTION: {type(e).__name__}: {e}")

        _debug_print(f"  Node restore complete: {len(uuid_map)}/{len(node_list)} nodes in uuid_map")

        # ---------------------------------------------------------------
        # 5. View
        # ---------------------------------------------------------------
        if view is not None and "view" in data:
            _debug_print("Phase 5: Restoring view state")
            try:
                self._restore_view(view, data["view"])
            except Exception as e:
                log.error(f"Exception in _restore_view: {type(e).__name__}: {e}")
                _debug_print(f"  FAILED: {e}")

        # ---------------------------------------------------------------
        # 6. Minimap
        # ---------------------------------------------------------------
        if minimap is not None and "minimap" in data:
            _debug_print("Phase 6: Restoring minimap state")
            try:
                self._restore_minimap(minimap, data["minimap"])
            except Exception as e:
                log.error(f"Exception in _restore_minimap: {type(e).__name__}: {e}")
                _debug_print(f"  FAILED: {e}")

        # ---------------------------------------------------------------
        # 7. Connections — LAST, after all nodes and ports are guaranteed
        #    to exist and be added to the scene
        # ---------------------------------------------------------------
        conn_list = data.get("connections", [])
        _debug_print(f"Phase 7: Restoring {len(conn_list)} connections")
        conn_success = 0
        conn_failed = 0

        for i, c_data in enumerate(conn_list):
            try:
                result = self._restore_connection(canvas, c_data, uuid_map)
                if result:
                    conn_success += 1
                else:
                    conn_failed += 1
            except Exception as e:
                conn_failed += 1
                log.error(f"Exception restoring connection [{i}]: {type(e).__name__}: {e}")
                _debug_print(f"  Connection [{i}] EXCEPTION: {type(e).__name__}: {e}")

        _debug_print(f"  Connection restore complete: {conn_success} OK, {conn_failed} failed")

        # ---------------------------------------------------------------
        # 8. Post-restore: mark all nodes dirty so they evaluate with
        #    connections in place
        # ---------------------------------------------------------------
        _debug_print(f"Phase 8: Marking {len(uuid_map)} nodes dirty")
        for node in uuid_map.values():
            try:
                if hasattr(node, "set_dirty"):
                    node.set_dirty("restore_complete")
                elif hasattr(node, "_is_dirty"):
                    node._is_dirty = True
            except Exception as e:
                log.error(f"Exception marking dirty on {type(node).__name__}: {type(e).__name__}: {e}")

        _debug_print("DESERIALIZE COMPLETE")
        _debug_print("=" * 70)
        return True

    # -- Clear --------------------------------------------------------------

    def _clear_canvas(self, canvas) -> None:
        """Clear the scene, preferring the canvas's own method."""
        if hasattr(canvas, "clear_scene"):
            canvas.clear_scene()
        elif hasattr(canvas, "_node_manager"):
            canvas._node_manager.clear_all()
        else:
            canvas.clear()

    # -- Restore Canvas Settings --------------------------------------------

    def _restore_canvas(self, canvas, canvas_data: Dict[str, Any]) -> None:
        """Apply grid, background, and snapping settings."""
        config_updates: Dict[str, Any] = {}

        # Color fields need conversion from list → QColor
        _COLOR_KEYS = {"bg_color", "grid_color"}
        # Everything else passes through as-is
        _PASSTHROUGH_KEYS = {
            "grid_spacing", "grid_type", "grid_line_width",
            "snapping_enabled", "connection_snap_radius",
            "shake_to_disconnect", "max_visible_grid_lines",
        }

        for key in _COLOR_KEYS:
            if key in canvas_data:
                val = canvas_data[key]
                # Handle both list (normal) and QColor (from object_hook)
                if isinstance(val, QColor):
                    config_updates[key] = val
                else:
                    config_updates[key] = _list_to_color(val)

        for key in _PASSTHROUGH_KEYS:
            if key in canvas_data:
                config_updates[key] = canvas_data[key]

        # Apply via set_config (which updates StyleManager and triggers cache sync)
        if config_updates and hasattr(canvas, "set_config"):
            canvas.set_config(**config_updates)

    # -- Restore View -------------------------------------------------------

    def _restore_view(self, view, view_data: Dict[str, Any]) -> None:
        """Restore viewport center, zoom, and transform."""
        # Restore full transform if available
        if "transform" in view_data:
            t = view_data["transform"]
            if not isinstance(t, QTransform):
                t = _list_to_transform(t)
            view.setTransform(t)
        elif "zoom" in view_data:
            # Fallback: just set scale
            zoom = view_data["zoom"]
            view.resetTransform()
            view.scale(zoom, zoom)

        # Center on the saved position
        if "center" in view_data:
            center = view_data["center"]
            if isinstance(center, QPointF):
                view.centerOn(center)
            else:
                view.centerOn(center[0], center[1])

    # -- Restore Minimap ----------------------------------------------------

    def _restore_minimap(self, minimap, mm_data: Dict[str, Any]) -> None:
        """Restore minimap corner, pin/hide state, and dimensions."""
        # Dimensions first (affects positioning)
        config_updates: Dict[str, Any] = {}
        if "width" in mm_data:
            config_updates["width"] = mm_data["width"]
        if "height" in mm_data:
            config_updates["height"] = mm_data["height"]
        if config_updates and hasattr(minimap, "set_config"):
            minimap.set_config(**config_updates)

        # Corner
        if "corner" in mm_data:
            try:
                from qt_minimap import MinimapCorner
                corner = MinimapCorner[mm_data["corner"]]
                minimap._current_corner = corner
            except (KeyError, ImportError):
                pass

        # Pin state
        if "is_pinned" in mm_data:
            minimap._auto_hide_enabled = not mm_data["is_pinned"]

        # Minimized state
        if "is_minimized" in mm_data:
            if mm_data["is_minimized"] and not minimap._is_minimized:
                if hasattr(minimap, "_perform_minimize"):
                    minimap._perform_minimize()
            elif not mm_data["is_minimized"] and minimap._is_minimized:
                if hasattr(minimap, "_perform_expand"):
                    minimap._perform_expand()

        # Reposition
        if hasattr(minimap, "update_position"):
            minimap.update_position()

    # -- Restore Style/Theme ------------------------------------------------

    def _restore_style(self, canvas, style_data: Dict[str, Any]) -> None:
        """Restore the theme and/or full style state."""
        sm = getattr(canvas, "_style_manager", None)
        if sm is None:
            return

        # Full state takes priority
        if "full_state" in style_data and hasattr(sm, "import_theme"):
            try:
                sm.import_theme("_restored", style_data["full_state"], apply=True)
                return
            except Exception as e:
                log.error(f"Full style restore failed, falling back to theme: {e}")

        # Fallback: apply named theme
        if "theme" in style_data:
            theme_name = style_data["theme"]
            if hasattr(sm, "apply_theme"):
                sm.apply_theme(theme_name)

    # -- Restore Single Node ------------------------------------------------

    def _restore_node(self, canvas, n_data: Dict[str, Any]) -> Optional[Any]:
        """
        Instantiate a node, place it on the canvas, and restore its state.
        """
        cls_name = n_data.get("class")
        cls_type = self.node_registry.get(cls_name)

        if cls_type is None:
            log.warning(f"Unknown node type: {cls_name}")
            _debug_print(f"    SKIP: '{cls_name}' not in registry. "
                         f"Available: {list(self.node_registry.keys())}")
            return None

        try:
            node = cls_type()
            _debug_print(f"    Instantiated {cls_name}")
        except Exception as e:
            log.error(f"Failed to instantiate {cls_name}: {e}")
            _debug_print(f"    Instantiation FAILED: {e}")
            return None

        try:
            # --- Graph-level metadata (serializer's responsibility) ---
            node.unique_id = n_data.get("id", str(uuid.uuid4()))

            # --- Restore state BEFORE adding to scene ---
            if hasattr(node, "restore_state"):
                _debug_print(f"    Calling restore_state() ...")
                node.restore_state(n_data)

                # Check ports immediately after restore_state
                inputs_after = getattr(node, "inputs", [])
                outputs_after = getattr(node, "outputs", [])
                in_count = len(list(inputs_after.values()) if isinstance(inputs_after, dict) else inputs_after)
                out_count = len(list(outputs_after.values()) if isinstance(outputs_after, dict) else outputs_after)
                _debug_print(f"    After restore_state: inputs={in_count}, outputs={out_count}")

            # --- Now place on canvas ---
            pos = n_data.get("pos", [0, 0])
            if isinstance(pos, QPointF):
                pos = [pos.x(), pos.y()]
            elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
                pass
            else:
                pos = [0, 0]

            _debug_print(f"    Adding to canvas at ({pos[0]:.0f}, {pos[1]:.0f})")
            canvas.add_node(node, (pos[0], pos[1]))

            # Check ports again after add_node (some nodes create ports here)
            inputs_final = getattr(node, "inputs", [])
            outputs_final = getattr(node, "outputs", [])
            in_final = len(list(inputs_final.values()) if isinstance(inputs_final, dict) else inputs_final)
            out_final = len(list(outputs_final.values()) if isinstance(outputs_final, dict) else outputs_final)
            if in_final != in_count or out_final != out_count:
                _debug_print(
                    f"    PORT COUNT CHANGED after add_node: "
                    f"inputs {in_count}→{in_final}, outputs {out_count}→{out_final}"
                )

            return node

        except Exception as e:
            log.error(f"Failed to restore node {cls_name}: {e}")
            _debug_print(f"    Restore FAILED: {type(e).__name__}: {e}")
            return None

    # -- Restore Connection -------------------------------------------------

    def _restore_connection(
        self, canvas, c_data: Dict[str, Any], uuid_map: Dict[str, Any]
    ) -> bool:
        """
        Recreate a single connection between two ports.
        
        Returns True if the connection was created successfully, False otherwise.
        """
        source_id = c_data.get("source_id")
        target_id = c_data.get("target_id")
        src_port_name = c_data.get("source_port_name", "?")
        dst_port_name = c_data.get("target_port_name", "?")

        _debug_print(
            f"  Connection: {src_port_name}→{dst_port_name} "
            f"(src_id={str(source_id)[:12]}, dst_id={str(target_id)[:12]})"
        )

        # --- Resolve nodes ---
        src_node = uuid_map.get(source_id)
        dst_node = uuid_map.get(target_id)

        # --- Fallback: if ID lookup fails, try to find nodes by class + port name ---
        # This recovers connections from save files with the double-UUID bug
        # (where node IDs and connection IDs were generated independently).
        if src_node is None or dst_node is None:
            _debug_print(
                f"    ID lookup missed "
                f"(src={'OK' if src_node else 'MISSING'}, dst={'OK' if dst_node else 'MISSING'}). "
                f"Trying fallback by port name..."
            )
            src_node_fb, dst_node_fb = self._find_nodes_by_port_name(
                uuid_map, src_port_name, dst_port_name, is_output_src=True
            )
            if src_node is None and src_node_fb is not None:
                src_node = src_node_fb
                _debug_print(f"    Fallback: src_node → {src_node.__class__.__name__}")
            if dst_node is None and dst_node_fb is not None:
                dst_node = dst_node_fb
                _debug_print(f"    Fallback: dst_node → {dst_node.__class__.__name__}")

        if src_node is None:
            _debug_print(f"    FAILED: source node not found (id={source_id})")
            _debug_print(f"    Available IDs: {[k[:12] for k in uuid_map.keys()]}")
            return False
        if dst_node is None:
            _debug_print(f"    FAILED: target node not found (id={target_id})")
            _debug_print(f"    Available IDs: {[k[:12] for k in uuid_map.keys()]}")
            return False

        _debug_print(
            f"    Nodes found: src={src_node.__class__.__name__}, "
            f"dst={dst_node.__class__.__name__}"
        )

        try:
            src_port = None
            dst_port = None

            src_idx = c_data.get("source_port")
            dst_idx = c_data.get("target_port")

            src_outputs = getattr(src_node, "outputs", [])
            dst_inputs = getattr(dst_node, "inputs", [])

            # Normalize dicts to lists so indexing works properly
            src_outputs_list = list(src_outputs.values()) if isinstance(src_outputs, dict) else list(src_outputs)
            dst_inputs_list = list(dst_inputs.values()) if isinstance(dst_inputs, dict) else list(dst_inputs)

            _debug_print(
                f"    Port lists: src_outputs={len(src_outputs_list)} "
                f"{[getattr(p, 'name', '?') for p in src_outputs_list]}, "
                f"dst_inputs={len(dst_inputs_list)} "
                f"{[getattr(p, 'name', '?') for p in dst_inputs_list]}"
            )

            # --- Index-based lookup ---
            if src_idx is not None and src_idx < len(src_outputs_list):
                src_port = src_outputs_list[src_idx]
                _debug_print(f"    src_port by index [{src_idx}]: {getattr(src_port, 'name', '?')}")
            else:
                _debug_print(
                    f"    src_port index lookup failed: idx={src_idx}, "
                    f"list_len={len(src_outputs_list)}"
                )

            if dst_idx is not None and dst_idx < len(dst_inputs_list):
                dst_port = dst_inputs_list[dst_idx]
                _debug_print(f"    dst_port by index [{dst_idx}]: {getattr(dst_port, 'name', '?')}")
            else:
                _debug_print(
                    f"    dst_port index lookup failed: idx={dst_idx}, "
                    f"list_len={len(dst_inputs_list)}"
                )

            # --- Fallback: name-based lookup ---
            if src_port is None and src_port_name and src_port_name != "?":
                src_port = self._find_port_by_name(src_outputs_list, src_port_name)
                if src_port:
                    _debug_print(f"    src_port by name '{src_port_name}': FOUND")
                else:
                    _debug_print(f"    src_port by name '{src_port_name}': NOT FOUND")

            if dst_port is None and dst_port_name and dst_port_name != "?":
                dst_port = self._find_port_by_name(dst_inputs_list, dst_port_name)
                if dst_port:
                    _debug_print(f"    dst_port by name '{dst_port_name}': FOUND")
                else:
                    _debug_print(f"    dst_port by name '{dst_port_name}': NOT FOUND")

            # --- Create connection ---
            if src_port and dst_port:
                trace = ConnectionFactory.create(
                    canvas, src_port, dst_port,
                    validate=False, trigger_compute=False
                )
                if trace:
                    _debug_print(f"    OK: trace created")
                    return True
                else:
                    _debug_print(f"    FAILED: ConnectionFactory.create() returned None")
                    return False
            else:
                _debug_print(
                    f"    FAILED: Could not resolve ports. "
                    f"src_port={'OK' if src_port else 'NONE'}, "
                    f"dst_port={'OK' if dst_port else 'NONE'}"
                )
                return False

        except Exception as e:
            log.error(f"Connection restore error: {e}")
            _debug_print(f"    EXCEPTION: {type(e).__name__}: {e}")
            return False
            
    @staticmethod
    def _find_port_by_name(ports, name: str):
        """Find a port in a list by its name attribute."""
        if not name:
            return None
        for port in ports:
            if getattr(port, "name", None) == name:
                return port
        return None

    @staticmethod
    def _find_nodes_by_port_name(
        uuid_map: Dict[str, Any],
        src_port_name: str,
        dst_port_name: str,
        is_output_src: bool = True,
    ):
        """
        Fallback: find source and target nodes by scanning all nodes for
        matching port names. Used to recover connections from save files
        where node IDs in connections don't match node IDs in the nodes array.
        
        Returns (src_node_or_None, dst_node_or_None).
        """
        src_node = None
        dst_node = None

        for node in uuid_map.values():
            if src_node is None and src_port_name:
                outputs = getattr(node, "outputs", [])
                outputs_list = list(outputs.values()) if isinstance(outputs, dict) else list(outputs)
                for port in outputs_list:
                    if getattr(port, "name", None) == src_port_name:
                        src_node = node
                        break

            if dst_node is None and dst_port_name:
                inputs = getattr(node, "inputs", [])
                inputs_list = list(inputs.values()) if isinstance(inputs, dict) else list(inputs)
                for port in inputs_list:
                    if getattr(port, "name", None) == dst_port_name:
                        dst_node = node
                        break

            if src_node and dst_node:
                break

        return src_node, dst_node

    # =======================================================================
    # CONVENIENCE: File I/O
    # =======================================================================

    def save_to_file(
        self,
        filepath: str,
        canvas,
        view=None,
        minimap=None,
        include_style: bool = True,
    ) -> bool:
        """Serialize and write to a file."""
        try:
            json_str = self.serialize(
                canvas, view=view, minimap=minimap, include_style=include_style
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(json_str)
            log.info(f"Saved to: {filepath}")
            return True
        except (IOError, OSError) as e:
            log.error(f"Save failed: {e}")
            return False

    def load_from_file(
        self,
        filepath: str,
        canvas,
        view=None,
        minimap=None,
        restore_style: bool = True,
    ) -> bool:
        """Read from a file and deserialize."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                json_str = f.read()
            result = self.deserialize(
                canvas, json_str,
                view=view, minimap=minimap, restore_style=restore_style
            )
            return result
        except FileNotFoundError:
            log.warning(f"File not found: {filepath}")
            return False
        except Exception as e:
            log.error(f"Load failed: {e}")
            return False

    # =======================================================================
    # BACKWARD COMPATIBILITY (Removed for clean version)
    # =======================================================================


# ===========================================================================
# Internal helper
# ===========================================================================

def _safe_attr(obj, prop_name: Optional[str], attr_name: Optional[str], default=None):
    """
    Try to read a property first, then fall back to a private attribute.
    Handles missing attributes gracefully.
    """
    if prop_name:
        try:
            val = getattr(obj, prop_name, None)
            if val is not None:
                return val
        except Exception:
            pass
    if attr_name:
        try:
            val = getattr(obj, attr_name, None)
            if val is not None:
                return val
        except Exception:
            pass
    return default