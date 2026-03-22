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

def disconnect_selected_nodes(canvas) -> int:
    """Disconnect all traces from selected movable nodes.

    Returns the number of traces removed.
    """
    selected = canvas.selectedItems()
    if not selected:
        return 0

    nodes = get_movable_nodes(selected)
    if not nodes:
        return 0

    traces_to_remove: set = set()
    for node in nodes:
        for attr in ("inputs", "outputs"):
            for port in getattr(node, attr, []):
                for trace in list(getattr(port, "connected_traces", [])):
                    traces_to_remove.add(trace)

    removed = 0
    for trace in traces_to_remove:
        try:
            ConnectionFactory.remove(trace, trigger_compute=True)
            removed += 1
        except RuntimeError as exc:
            log.debug(f"Trace already removed: {exc}")
        except Exception as exc:
            log.warning(f"Unexpected error disconnecting trace: {exc}")

    return removed
