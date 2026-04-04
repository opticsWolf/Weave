# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

State Utilities — Consolidated helpers for the canvas state machine.

This module is the single source of truth for:
  • Item filtering    (get_movable_nodes)
  • Item resolution   (ItemResolver — multi-hit aware)
  • Shake detection   (OptimizedShakeRecognizer)
  • Style caching     (StylableStateMixin)
  • Connection tuples (build_connection_tuples — shared undo helper)
"""

from __future__ import annotations

from typing import Optional, Sequence, Type, TypeVar

from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsProxyWidget,
    QGraphicsTextItem,
)
from PySide6.QtCore import Qt, QPointF, QElapsedTimer
from PySide6.QtGui import QTransform

from weave.basenode import BaseControlNode
from weave.node.node_port import NodePort
from weave.node.node_trace import DragTrace, NodeTrace

from weave.logger import get_logger

log = get_logger("State Utils")

# ---------------------------------------------------------------------------
# StyleManager availability (module-level, resolved once)
# ---------------------------------------------------------------------------
try:
    from weave.stylemanager import StyleCategory, StyleManager

    STYLEMANAGER_AVAILABLE = True
except ImportError:
    STYLEMANAGER_AVAILABLE = False
    log.warning("StyleManager not available — shake detection will use defaults")


# ============================================================================
# ITEM FILTERING
# ============================================================================

def get_movable_nodes(items: Sequence[QGraphicsItem]) -> list[QGraphicsItem]:
    """Return only movable nodes, excluding traces.

    This is the *canonical* implementation — every call site in the state
    machine must use this rather than inlining the filter.
    """
    return [
        item
        for item in items
        if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        and not isinstance(item, (NodeTrace, DragTrace))
    ]


# ============================================================================
# SHAKE GESTURE RECOGNIZER
# ============================================================================

class OptimizedShakeRecognizer:
    """High-performance shake detection with minimal branching.

    Delta-based tracking with O(1) time complexity per update.
    """

    def __init__(
        self,
        threshold: float = 50.0,
        min_changes: int = 4,
        timeout_ms: int = 500,
        debug: bool = False,
    ):
        self.threshold = threshold
        self.min_changes = min_changes
        self.timeout_ms = timeout_ms
        self.debug = debug

        self._direction_changes = 0
        self._last_dx = 0.0
        self._stroke_dist = 0.0
        self._timer = QElapsedTimer()
        self._timer.start()

    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all tracking state."""
        self._direction_changes = 0
        self._stroke_dist = 0.0
        self._last_dx = 0.0
        self._timer.restart()
        if self.debug:
            log.debug("OptimizedShakeRecognizer: Reset")

    def update(self, delta: QPointF) -> bool:
        """Process a relative movement delta.  Returns *True* on detection."""
        if self._timer.elapsed() > self.timeout_ms:
            self.reset()
            return False

        dx = delta.x()
        if abs(dx) < 2.0:          # filter mouse jitter
            return False

        # Direction reversal with sufficient stroke length
        if (dx > 0) != (self._last_dx > 0) and self._last_dx != 0.0:
            if self._stroke_dist >= self.threshold:
                self._direction_changes += 1
                self._stroke_dist = 0.0
                if self.debug:
                    log.debug(
                        f"OptimizedShake: reversal #{self._direction_changes}"
                    )

        self._last_dx = dx
        self._stroke_dist += abs(dx)

        if self._direction_changes >= self.min_changes:
            if self.debug:
                log.info("OptimizedShake: GESTURE DETECTED")
            self.reset()
            return True
        return False


# ============================================================================
# ITEM RESOLVER  (multi-hit aware)
# ============================================================================

T = TypeVar("T", bound=QGraphicsItem)


class ItemResolver:
    """Resolve scene items from a position, aware of overlapping z-layers.

    Uses ``scene.items(pos)`` instead of ``scene.itemAt(pos)`` so that
    transparent overlays (DragTrace, NodeTrace, selection rectangles) are
    skipped rather than shadowing the real target.
    """

    @staticmethod
    def resolve_at(
        scene,
        scene_pos: QPointF,
        target_type: Type[T],
        transform: Optional[QTransform] = None,
    ) -> Optional[T]:
        """Find the topmost item of *target_type* at *scene_pos*.

        Walks the z-sorted hit-list and, for each candidate, walks up its
        parent chain looking for *target_type*.
        """
        if transform is None:
            transform = QTransform()

        for item in scene.items(scene_pos, Qt.ItemSelectionMode.IntersectsItemShape,
                                Qt.SortOrder.DescendingOrder, transform):
            result = ItemResolver._walk_up(item, target_type)
            if result is not None:
                return result
        return None

    @staticmethod
    def resolve_port_at(scene, scene_pos: QPointF) -> Optional[NodePort]:
        """Convenience: find a port at *scene_pos*."""
        return ItemResolver.resolve_at(scene, scene_pos, NodePort)

    @staticmethod
    def resolve_node_at(scene, scene_pos: QPointF) -> Optional[BaseControlNode]:
        """Convenience: find a node at *scene_pos*."""
        return ItemResolver.resolve_at(scene, scene_pos, BaseControlNode)

    # ------------------------------------------------------------------
    @staticmethod
    def _walk_up(
        item: Optional[QGraphicsItem], target_type: Type[T]
    ) -> Optional[T]:
        current = item
        while current is not None:
            if isinstance(current, target_type):
                return current
            current = current.parentItem()
        return None


# ============================================================================
# STYLE-CACHING MIXIN  (Review §5)
# ============================================================================

class StylableStateMixin:
    """Mixin that subscribes to StyleManager and maintains a local cache.

    Any state class that needs style parameters should inherit from this
    mixin.  Override ``_on_style_cache_updated()`` to react to changes.

    Attributes populated by the mixin:
        _shake_enabled, _shake_timeout_ms, _shake_threshold, _shake_min_changes
    """

    # Defaults — used when StyleManager is absent
    _shake_enabled: bool = False
    _shake_timeout_ms: int = 500
    _shake_threshold: float = 50.0
    _shake_min_changes: int = 4

    def _init_style_cache(self) -> None:
        """Call once from ``__init__`` *after* ``self.canvas`` is set."""
        self._sync_style_cache()
        if STYLEMANAGER_AVAILABLE:
            try:
                # Use memory-safe WeakSet registration instead of direct signal connections
                StyleManager.instance().register(self, StyleCategory.CANVAS)
            except Exception as exc:
                log.warning(f"Failed to subscribe to StyleManager: {exc}")

    def _sync_style_cache(self) -> None:
        """Pull current values from canvas / StyleManager."""
        canvas = getattr(self, "canvas", None)

        if canvas is not None and hasattr(canvas, "shake_to_disconnect"):
            self._shake_enabled = canvas.shake_to_disconnect

        if STYLEMANAGER_AVAILABLE:
            try:
                schema = StyleManager.instance().get_schema(StyleCategory.CANVAS)
                if schema:
                    if not (canvas and hasattr(canvas, "shake_to_disconnect")):
                        self._shake_enabled = getattr(
                            schema, "shake_to_disconnect", False
                        )
                    self._shake_timeout_ms = getattr(
                        schema, "shake_time_window_ms", 500
                    )
                    self._shake_threshold = float(
                        getattr(schema, "min_stroke_length", 50)
                    )
                    self._shake_min_changes = getattr(
                        schema, "min_direction_changes", 4
                    )
            except Exception as exc:
                log.debug(f"Style cache sync failed: {exc}")
        elif not (canvas and hasattr(canvas, "shake_to_disconnect")):
            self._shake_enabled = False

        # Push updated values into an existing recognizer if present
        recognizer = getattr(self, "_shake_recognizer", None)
        if recognizer is not None:
            recognizer.threshold = self._shake_threshold
            recognizer.min_changes = self._shake_min_changes
            recognizer.timeout_ms = self._shake_timeout_ms

        self._on_style_cache_updated()

    def on_style_changed(self, category, changes: dict) -> None:
        """Called automatically via StyleManager's WeakSet subscriber loop."""
        if category != StyleCategory.CANVAS:
            return
        _SHAKE_KEYS = {
            "shake_to_disconnect",
            "shake_time_window_ms",
            "min_stroke_length",
            "min_direction_changes",
        }
        if _SHAKE_KEYS & changes.keys():
            self._sync_style_cache()

    def _on_style_cache_updated(self) -> None:
        """Override in subclasses to react to a cache refresh."""


# ============================================================================
# CONNECTION-TUPLE BUILDER  (deduplicates undo-tuple logic)
# ============================================================================

def build_connection_tuples(traces) -> list[tuple]:
    """Build ``(src_uid, src_name, dst_uid, dst_name)`` tuples for undo.

    Accepts any iterable of trace objects.  Silently skips traces whose
    port/node metadata is incomplete.
    """
    from weave.canvas.undo_commands import get_node_uid

    result: list[tuple] = []
    for trace in traces:
        src = getattr(trace, "source", None)
        dst = getattr(trace, "target", None)
        if not (src and dst):
            continue
        src_node = getattr(src, "node", None)
        dst_node = getattr(dst, "node", None)
        if not (src_node and dst_node):
            continue

        # Resolve strictly by port name to maintain compatibility
        # with UndoCommands and avoid array-index shifting corruption (§1)
        result.append((
            get_node_uid(src_node),
            getattr(src, "name", ""),
            get_node_uid(dst_node),
            getattr(dst, "name", ""),
        ))
    return result
