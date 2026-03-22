# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas Interaction State — abstract base and state factory.

Design changes (refactor):
  • States no longer import each other.  Transitions go through
    ``self.request_transition(name, **kwargs)`` which delegates to
    ``StateFactory`` on the canvas.
  • ``InteractionHandler`` protocol formalises the chain-of-responsibility
    used by DefaultInteractionState.on_mouse_press.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Protocol, runtime_checkable

from PySide6.QtWidgets import QGraphicsSceneMouseEvent, QGraphicsItem
from PySide6.QtGui import QKeyEvent


# ============================================================================
# INTERACTION HANDLER PROTOCOL  (Review §3 — Chain of Responsibility)
# ============================================================================

@runtime_checkable
class InteractionHandler(Protocol):
    """Small, single-responsibility handler for one category of press.

    Return *True* from ``try_handle`` to consume the event and stop the
    chain.  Handlers are evaluated in priority order; the first to
    consume wins.
    """

    def try_handle(
        self,
        event: QGraphicsSceneMouseEvent,
        state: "CanvasInteractionState",
    ) -> bool:
        ...


# ============================================================================
# STATE FACTORY  (Review §4 — decoupled transitions)
# ============================================================================

class StateFactory:
    """Instantiate states by name so that states never import each other.

    Register state classes once at application startup::

        factory = StateFactory()
        factory.register("default", DefaultInteractionState)
        factory.register("connection_drag", ConnectionDragState)
        canvas.state_factory = factory

    States request transitions via::

        self.request_transition("connection_drag", start_port=port)
    """

    def __init__(self) -> None:
        self._registry: dict[str, type] = {}

    def register(self, name: str, cls: type) -> None:
        self._registry[name] = cls

    def create(self, name: str, canvas, **kwargs) -> "CanvasInteractionState":
        cls = self._registry.get(name)
        if cls is None:
            raise KeyError(
                f"Unknown state '{name}'. "
                f"Registered: {list(self._registry)}"
            )
        return cls(canvas, **kwargs)


# ============================================================================
# BASE STATE
# ============================================================================

class CanvasInteractionState(ABC):
    """Abstract base for all canvas interaction states."""

    def __init__(self, canvas):
        self.canvas = canvas

    # ── Transition helper ─────────────────────────────────────────────

    def request_transition(self, state_name: str, **kwargs) -> None:
        """Ask the canvas to transition to *state_name*.

        The canvas owns a :class:`StateFactory` and performs the actual
        instantiation, keeping states decoupled from each other.
        """
        factory: Optional[StateFactory] = getattr(
            self.canvas, "state_factory", None
        )
        if factory is None:
            raise RuntimeError(
                "Canvas has no state_factory — register one at startup."
            )
        new_state = factory.create(state_name, self.canvas, **kwargs)
        self.canvas.set_state(new_state)

    # ── Abstract event interface ──────────────────────────────────────

    @abstractmethod
    def on_mouse_press(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Handle mouse press.  Return True to consume."""

    @abstractmethod
    def on_mouse_move(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Handle mouse move.  Return True to consume."""

    @abstractmethod
    def on_mouse_release(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Handle mouse release.  Return True to consume."""

    @abstractmethod
    def on_mouse_double_click(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Handle mouse double click.  Return True to consume."""

    # ── Lifecycle hooks ───────────────────────────────────────────────

    def on_enter(self) -> None:
        """Called when entering this state."""

    def on_exit(self) -> None:
        """Called when exiting this state."""

    def on_selection_changed(self, selected_items: list) -> None:
        """Called when scene selection changes."""

    def apply_grid_snapping(self, event: QGraphicsSceneMouseEvent) -> None:
        """Apply grid snapping after default mouse move behaviour."""

    # ── Widget-editing API (for Canvas to query without peeking) ──────

    @property
    def suppressed_node(self) -> Optional[QGraphicsItem]:
        """The node whose ``ItemIsMovable`` was temporarily cleared.

        Returns ``None`` by default.  ``DefaultInteractionState`` overrides
        this to expose the node whose movability was suppressed so a proxy
        widget could receive mouse events.

        The Canvas uses this to decide whether widget-editing mode is
        active — without reaching into private state attributes.
        """
        return None

    def exit_widget_editing(self) -> None:
        """Restore the suppressed node and clean up widget-editing state.

        The default implementation is a no-op.  Override in states that
        actually manage widget interactions.
        """

    def keyPressEvent(self, event: QKeyEvent) -> bool:
        """Handle key press.  Return True to consume."""
        return False
