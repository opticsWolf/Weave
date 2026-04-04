# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas States — orchestrator module.

Public API re-exports and the ``create_state_factory()`` helper that
wires up the state machine without mutual imports between states.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtWidgets import QGraphicsItem

from weave.logger import get_logger

log = get_logger("Canvas")

# ── Re-exports (public API for the package) ───────────────────────────
from weave.canvas.states.interaction_state import (        # noqa: F401
    CanvasInteractionState,
    InteractionHandler,
    StateFactory,
)
from weave.canvas.states.default_state import DefaultInteractionState       # noqa: F401
from weave.canvas.states.connection_drag_state import ConnectionDragState   # noqa: F401
from weave.canvas.states.state_utils import (              # noqa: F401
    OptimizedShakeRecognizer,
    ItemResolver,
    StylableStateMixin,
    get_movable_nodes,
    build_connection_tuples,
)
from weave.canvas.commands_mixin import CanvasCommandsMixin  # noqa: F401
from weave.node.node_trace import NodeTrace, DragTrace
from weave.portutils import ConnectionFactory


# ============================================================================
# STATE FACTORY SETUP  (Review §4)
# ============================================================================

def create_state_factory() -> StateFactory:
    """Build and return a :class:`StateFactory` with all known states.

    Call once during canvas initialisation::

        canvas.state_factory = create_state_factory()
    """
    factory = StateFactory()
    factory.register("default", DefaultInteractionState)
    factory.register("connection_drag", ConnectionDragState)
    return factory


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def disconnect_selected_nodes(canvas, undo_manager=None) -> int:
    """Disconnect all traces from selected movable nodes.

    Uses deferred compute triggers and wraps the operation in an
    explicit macro (if *undo_manager* is provided) so all downstream
    state changes are bundled into one undo step.

    Returns the number of traces removed.
    """
    selected = canvas.selectedItems()
    if not selected:
        return 0

    nodes = get_movable_nodes(selected)
    if not nodes:
        return 0

    traces_to_remove: set = set()
    affected_input_ports: list = []
    for node in nodes:
        for attr in ("inputs", "outputs"):
            for port in getattr(node, attr, []):
                for trace in list(getattr(port, "connected_traces", [])):
                    if trace not in traces_to_remove:
                        traces_to_remove.add(trace)
                        target = getattr(trace, "target", None)
                        if target is not None:
                            affected_input_ports.append(target)

    if not traces_to_remove:
        return 0

    # Capture name-based trace tuples BEFORE removing them — once the
    # trace is deleted its port/node references become invalid.
    from weave.canvas.undo_commands import RemoveConnectionsCommand, get_node_uid
    trace_tuples = []
    for t in traces_to_remove:
        src, dst = getattr(t, 'source', None), getattr(t, 'target', None)
        if src and dst and getattr(src, 'node', None) and getattr(dst, 'node', None):
            trace_tuples.append((
                get_node_uid(src.node), getattr(src, 'name', ''),
                get_node_uid(dst.node), getattr(dst, 'name', '')
            ))

    if undo_manager:
        undo_manager.begin_macro(
            f"Disconnect {len(traces_to_remove)} traces")
        # Push the command so the macro actually records the operation
        if trace_tuples:
            undo_manager.push(RemoveConnectionsCommand(trace_tuples))

    removed = 0
    for trace in traces_to_remove:
        try:
            ConnectionFactory.remove(trace, trigger_compute=False)
            removed += 1
        except RuntimeError as exc:
            log.debug(f"Trace already removed: {exc}")
        except Exception as exc:
            log.warning(f"Unexpected error disconnecting trace: {exc}")

    # Single batch recompute per downstream node
    if removed:
        seen: set = set()
        for port in affected_input_ports:
            node = getattr(port, "node", None)
            if node is not None and id(node) not in seen:
                seen.add(id(node))
                if hasattr(node, "set_dirty"):
                    node.set_dirty("disconnect")
                elif hasattr(node, "evaluate"):
                    node.evaluate()

    if undo_manager:
        undo_manager.end_macro()

    return removed
