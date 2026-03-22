# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Connection Drag State — handles connection dragging with port snapping.

Refactor highlights:
  • Returns to idle via ``self.request_transition("default")`` — no
    direct import of DefaultInteractionState.
  • Uses the shared ``ItemResolver`` from state_utils.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QGraphicsSceneMouseEvent
from PySide6.QtCore import Qt

from weave.canvas.states.interaction_state import CanvasInteractionState
from weave.canvas.states.state_utils import ItemResolver
from weave.portutils import PortUtils, PortFinder, ConnectionFactory
from weave.node.node_port import NodePort
from weave.node.node_trace import DragTrace, NodeTrace


class ConnectionDragState(CanvasInteractionState):
    """Handles connection dragging with port snapping."""

    DROP_RADIUS_MULTIPLIER = 2.0

    def __init__(
        self,
        canvas,
        start_port: NodePort,
        pending_detach_port: Optional[NodePort] = None,
        pending_detach_trace: Optional[NodeTrace] = None,
    ):
        super().__init__(canvas)
        self.start_port = start_port
        self.drag_trace: Optional[DragTrace] = None
        self.snapped_port: Optional[NodePort] = None

        # Detachment state
        self._pending_detach_port = pending_detach_port
        self._pending_detach_trace = pending_detach_trace
        self._detached_from_port: Optional[NodePort] = None
        self._detached_target_node = None
        self._detachment_occurred = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        visual_start = (
            self.start_port.get_visual_target()
            if hasattr(self.start_port, "get_visual_target")
            else self.start_port
        )
        start_pos = PortUtils.get_scene_center(visual_start)

        self.drag_trace = DragTrace(
            self.start_port,
            start_pos=start_pos,
            color=self.start_port.color,
        )
        self.canvas.addItem(self.drag_trace)
        self.canvas._set_global_port_dimming(True, self.start_port)

    def on_exit(self) -> None:
        if self.drag_trace:
            try:
                self.canvas.removeItem(self.drag_trace)
            except RuntimeError:
                pass
            self.drag_trace = None

        if self.snapped_port:
            self.snapped_port.reset_connection_state()

        self.canvas._set_global_port_dimming(False, self.start_port)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_mouse_press(self, event: QGraphicsSceneMouseEvent) -> bool:
        return True  # consume presses during drag

    def on_mouse_double_click(self, event: QGraphicsSceneMouseEvent) -> bool:
        return False

    def on_mouse_move(self, event: QGraphicsSceneMouseEvent) -> bool:
        if not self.drag_trace:
            return False

        mouse_pos = event.scenePos()
        snap_radius = self.canvas.connection_snap_radius

        # Pending detachment
        if self._pending_detach_port and not self._detachment_occurred:
            self._try_detach(mouse_pos, snap_radius)

        # Find compatible port
        target_port = PortFinder.find_nearest_compatible(
            self.canvas,
            mouse_pos,
            self.start_port,
            snap_radius=snap_radius,
            check_existing=False,
        )

        self._update_snap_feedback(target_port)

        end_pos = (
            PortUtils.get_scene_center(target_port) if target_port else mouse_pos
        )
        self.drag_trace.update_position(end_pos)
        return True

    def on_mouse_release(self, event: QGraphicsSceneMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return True

        # Pending detach that never moved far enough → keep original
        if self._pending_detach_port and not self._detachment_occurred:
            self.request_transition("default")
            return True

        if self.snapped_port:
            self._finalize_connection()
        elif self._detachment_occurred and self._detached_target_node is not None:
            node = self._detached_target_node
            if hasattr(node, "set_dirty"):
                node.set_dirty("disconnect")
            elif hasattr(node, "evaluate"):
                node.evaluate()

        self.request_transition("default")
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _try_detach(self, mouse_pos, snap_radius: float) -> None:
        input_center = PortUtils.get_scene_center(self._pending_detach_port)
        distance = (mouse_pos - input_center).manhattanLength()

        if distance <= snap_radius * self.DROP_RADIUS_MULTIPLIER:
            return

        trace = self._pending_detach_trace
        target_port = getattr(trace, "target", None)
        if target_port is not None:
            self._detached_target_node = getattr(target_port, "node", None)

        ConnectionFactory.remove(trace, trigger_compute=False)

        self._detachment_occurred = True
        self._detached_from_port = self._pending_detach_port
        self._pending_detach_trace = None
        self._pending_detach_port = None

    def _update_snap_feedback(self, target_port: Optional[NodePort]) -> None:
        if self.snapped_port and self.snapped_port != target_port:
            self.snapped_port.reset_connection_state()
        if target_port:
            target_port.set_connection_state(True)
        self.snapped_port = target_port

    def _finalize_connection(self) -> None:
        if not self.snapped_port:
            return
        output_port, input_port = PortUtils.order_ports(
            self.start_port, self.snapped_port
        )
        self.canvas._create_connection(output_port, input_port)
