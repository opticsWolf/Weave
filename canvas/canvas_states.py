# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas Interaction States - Performance Optimized (v12)

Key Optimizations:
1. Cached Style Parameters: DefaultInteractionState now caches shake settings locally instead of 
   querying StyleManager on every mouse move (60-120 Hz)
2. Observer Pattern: Subscribes to StyleManager.style_changed signal for cache updates
3. Optimized Shake Recognizer: Uses delta-based movement tracking with reduced branching
4. Eliminated Dynamic Lookups: No hasattr checks or imports in hot paths

Performance Impact:
- on_mouse_move: O(N) → O(1) for style access
- Reduced interpreter overhead from repeated property calls
- Eliminated import statement resolution in tight loops
"""

from typing import Sequence
from PySide6.QtWidgets import QGraphicsItem
from weave.logger import get_logger
log = get_logger("Canvas")


# Import from the submodules to maintain orchestration
from weave.canvas.states.interaction_state import CanvasInteractionState
from weave.canvas.states.default_state import DefaultInteractionState
from weave.canvas.states.connection_drag_state import ConnectionDragState  
from weave.canvas.states.state_utils import OptimizedShakeRecognizer, ItemResolver
from weave.canvas.commands_mixin import CanvasCommandsMixin

from weave.node.node_trace import NodeTrace, DragTrace

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


def disconnect_selected_nodes(canvas) -> int:
    """Disconnect all traces from selected movable nodes without deleting them.

    Collects every trace attached to every port on every selected node,
    de-duplicates (a trace shared between two selected nodes should only
    be removed once), then removes them via ``ConnectionFactory.remove``
    which properly unregisters from both ports and triggers recomputation.

    Returns the number of traces removed.
    """
    from weave.portutils import ConnectionFactory

    selected = canvas.selectedItems()
    if not selected:
        return 0

    nodes = _get_movable_nodes(selected)
    if not nodes:
        return 0

    # Collect unique traces across all ports of all selected nodes.
    traces_to_remove: set = set()
    for node in nodes:
        for port_attr in ('inputs', 'outputs'):
            for port in getattr(node, port_attr, []):
                for trace in list(getattr(port, 'connected_traces', [])):
                    traces_to_remove.add(trace)

    removed = 0
    for trace in traces_to_remove:
        try:
            ConnectionFactory.remove(trace, trigger_compute=True)
            removed += 1
        except RuntimeError as e:
            log.debug(f"Trace already removed: {e}")
        except Exception as e:
            log.warning(f"Unexpected error disconnecting trace: {e}")

    return removed
