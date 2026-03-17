# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from typing import Optional
from PySide6.QtWidgets import QGraphicsSceneMouseEvent, QGraphicsItem
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QTransform

# Import from the orchestrator to avoid circular dependencies  
from weave.canvas.states.interaction_state import CanvasInteractionState
from weave.canvas.states.state_utils import ItemResolver
# NOTE: DefaultInteractionState  is imported locally inside on_mouse_release to
# break the mutual import cycle (DefaultInteractionState  ↔ ConnectionDragState).
from weave.portutils import PortUtils, PortFinder, ConnectionFactory
from weave.node.node_port import NodePort
from weave.node.node_trace import DragTrace, NodeTrace


#--------------------------------------------------------
# Connection Drag State - Handles connection dragging with port snapping.
#--------------------------------------------------------

class ConnectionDragState(CanvasInteractionState):
    """Handles connection dragging with port snapping."""
    
    DROP_RADIUS_MULTIPLIER = 2.0
    
    def __init__(
        self, 
        canvas, 
        start_port: NodePort,
        pending_detach_port: Optional[NodePort] = None,
        pending_detach_trace: Optional[NodeTrace] = None
    ):
        super().__init__(canvas)
        self.start_port = start_port
        self.drag_trace: Optional[DragTrace] = None
        self.snapped_port: Optional[NodePort] = None
        
        # Detachment state
        self._pending_detach_port = pending_detach_port
        self._pending_detach_trace = pending_detach_trace
        self._detached_from_port: Optional[NodePort] = None
        self._detached_target_node = None  # For deferred set_dirty on release
        self._detachment_occurred = False

    def on_enter(self):
        """Create drag trace and set up port dimming."""
        # Use the visual port for the start position: if the source node is
        # minimized, the real port is hidden and stale — get_visual_target()
        # returns the visible summary port whose position is always current.
        visual_start = self.start_port.get_visual_target() \
            if hasattr(self.start_port, 'get_visual_target') else self.start_port
        start_pos = PortUtils.get_scene_center(visual_start)
        port_color = self.start_port.color
        
        self.drag_trace = DragTrace(
            self.start_port,
            start_pos=start_pos,
            color=port_color
        )
        self.canvas.addItem(self.drag_trace)
        self.canvas._set_global_port_dimming(True, self.start_port)

    def on_exit(self):
        """Clean up drag trace and reset port states."""
        if self.drag_trace:
            try:
                self.canvas.removeItem(self.drag_trace)
            except RuntimeError:
                pass
            self.drag_trace = None
            
        if self.snapped_port:
            self.snapped_port.reset_connection_state()
            
        self.canvas._set_global_port_dimming(False, self.start_port)

    def on_mouse_press(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Consume press events during drag."""
        return True

    def on_mouse_double_click(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Double-click doesn't affect connection dragging."""
        return False

    def on_mouse_move(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Update drag trace and handle port snapping."""
        if not self.drag_trace:
            return False
            
        mouse_pos = event.scenePos()
        snap_radius = self.canvas.connection_snap_radius
        
        # Handle pending detachment
        if self._pending_detach_port and not self._detachment_occurred:
            self._try_detach(mouse_pos, snap_radius)
        
        # Find compatible port
        target_port = PortFinder.find_nearest_compatible(
            self.canvas,
            mouse_pos,
            self.start_port,
            snap_radius=snap_radius,
            check_existing=False
        )
        
        # Update visual feedback
        self._update_snap_feedback(target_port)
        
        # Update trace position
        end_pos = PortUtils.get_scene_center(target_port) if target_port else mouse_pos
        self.drag_trace.update_position(end_pos)
                
        return True
    
    def _try_detach(self, mouse_pos, snap_radius: float) -> None:
        """Attempt to detach connection if mouse moved far enough."""
        input_center = PortUtils.get_scene_center(self._pending_detach_port)
        distance = (mouse_pos - input_center).manhattanLength()
        
        if distance <= snap_radius * self.DROP_RADIUS_MULTIPLIER:
            return
        
        trace = self._pending_detach_trace

        # Remember the downstream node so we can dirty it on mouse release
        # if the user doesn't reconnect to another port.
        target_port = getattr(trace, 'target', None)
        if target_port is not None:
            self._detached_target_node = getattr(target_port, 'node', None)

        # Remove without triggering compute — deferred to on_mouse_release
        ConnectionFactory.remove(trace, trigger_compute=False)
        
        self._detachment_occurred = True
        self._detached_from_port = self._pending_detach_port
        self._pending_detach_trace = None
        self._pending_detach_port = None

    def on_mouse_release(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Finalize connection if valid, then return to idle."""
        # Deferred import to break the IdleState ↔ ConnectionDragState cycle.
        from weave.canvas.states.default_state import DefaultInteractionState 
        if event.button() != Qt.MouseButton.LeftButton:
            return True
        
        # If pending detach but never moved far enough, keep original connection
        if self._pending_detach_port and not self._detachment_occurred:
            self.canvas.set_state(DefaultInteractionState(self.canvas))
            return True
            
        # Create connection if we have a valid snap target
        if self.snapped_port:
            self._finalize_connection()
        elif self._detachment_occurred and self._detached_target_node is not None:
            # Disconnect finalised without reconnection — now trigger compute
            node = self._detached_target_node
            if hasattr(node, 'set_dirty'):
                node.set_dirty("disconnect")
            elif hasattr(node, 'evaluate'):
                node.evaluate()
        
        self.canvas.set_state(DefaultInteractionState(self.canvas))
        return True
    
    def _update_snap_feedback(self, target_port: Optional[NodePort]) -> None:
        """Update visual feedback for port snapping."""
        if self.snapped_port and self.snapped_port != target_port:
            self.snapped_port.reset_connection_state()
        
        if target_port:
            target_port.set_connection_state(True)
        
        self.snapped_port = target_port
    
    def _finalize_connection(self) -> None:
        """Create the connection between ports."""
        if not self.snapped_port:
            return
            
        output_port, input_port = PortUtils.order_ports(self.start_port, self.snapped_port)
        self.canvas._create_connection(output_port, input_port)
