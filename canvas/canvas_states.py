# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas Interaction States - Performance Optimized (v12)

Key Optimizations:
1. Cached Style Parameters: IdleState now caches shake settings locally instead of 
   querying StyleManager on every mouse move (60-120 Hz)
2. Observer Pattern: Subscribes to StyleManager.style_changed signal for cache updates
3. Optimized Shake Recognizer: Uses delta-based movement tracking with reduced branching
4. Eliminated Dynamic Lookups: No hasattr checks or imports in hot paths

Performance Impact:
- on_mouse_move: O(N) → O(1) for style access
- Reduced interpreter overhead from repeated property calls
- Eliminated import statement resolution in tight loops
"""

from abc import ABC, abstractmethod
from collections import deque
from typing import Optional, List, Type, TypeVar, Sequence
from PySide6.QtWidgets import QGraphicsSceneMouseEvent, QGraphicsItem, QGraphicsProxyWidget, QGraphicsTextItem
from PySide6.QtCore import Qt, QPointF, QElapsedTimer, QTimer
from PySide6.QtGui import QTransform, QKeyEvent
from weave.logger import get_logger
log = get_logger("Canvas")


# Import from the submodules to maintain orchestration
from weave.canvas.states.interaction_state import CanvasInteractionState
from weave.canvas.states.idle_state import IdleState
from weave.canvas.states.connection_drag_state import ConnectionDragState  
from weave.canvas.states.state_utils import OptimizedShakeRecognizer, ItemResolver
from weave.canvas.commands_mixin import CanvasCommandsMixin

# Import StyleManager at module level (not in properties)
try:
    from weave.stylemanager import StyleCategory, StyleManager
    STYLEMANAGER_AVAILABLE = True
except ImportError:
    STYLEMANAGER_AVAILABLE = False
    log.warning("StyleManager not available - shake detection will use defaults")

# ============================================================================= 
# UTILITY FUNCTIONS
# =============================================================================

def _get_movable_nodes(items: Sequence[QGraphicsItem]) -> list[QGraphicsItem]:
    """Filter items to only movable nodes, excluding traces."""
    return [
        item for item in items
        if (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        and not isinstance(item, (NodeTrace, DragTrace))
    ]


# ============================================================================= 
# DELETE FUNCTIONALITY
# =============================================================================

def delete_selected_nodes(canvas) -> int:
    """Delete selected movable nodes from the canvas."""
    selected = canvas.selectedItems()
    if not selected:
        return 0

    nodes_to_delete = _get_movable_nodes(selected)
    if not nodes_to_delete:
        return 0

    node_manager = getattr(canvas, '_node_manager', None)
    deleted_count = 0

    for node in nodes_to_delete:
        try:
            if node_manager:
                node_manager.remove_node(node)
            else:
                # canvas IS the QGraphicsScene
                canvas.removeItem(node)
            deleted_count += 1
        except RuntimeError as e:
            log.debug(f"Node already removed: {e}")
        except AttributeError as e:
            log.warning(f"Unexpected error removing node: {e}")

    return deleted_count
