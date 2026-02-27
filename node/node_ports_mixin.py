# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodePortsMixin - Port Management, Content Widget, and State Management for Node.

Handles:
- Adding / removing / finding / clearing ports
- Summary port initialization
- Port overlay propagation
- Content widget setup (incl. WeaveWidgetCore wiring)
- Node execution state management (NORMAL / PASSTHROUGH / DISABLED / COMPUTING)
"""

from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QColor
from PySide6.QtCore import QRectF

from weave.node.node_subcomponents import NodeState
from weave.node.node_port import NodePort

from weave.logger import get_logger
log = get_logger("NodePortsMixin")


class NodePortsMixin:
    """
    Mixin providing port management, content widget, and state management for Node.

    Expects the host class to have:
        - self.inputs: List[NodePort]
        - self.outputs: List[NodePort]
        - self._summary_input, self._summary_output: Optional[NodePort]
        - self._overlay_color: QColor
        - self._config: Dict[str, Any]
        - self.header: NodeHeader
        - self.body: NodeBody
        - self.enforce_min_dimensions(), self.update_geometry(), self.update()
        - self._computing_pulse_anim: QVariantAnimation
        - self.state_changed: Signal
    """

    # ------------------------------------------------------------------
    # Summary Port Initialization
    # ------------------------------------------------------------------

    def _initialize_summary_ports(self):
        """Initialize summary ports after main Node initialization."""
        if self._summary_input is None:
            self._summary_input = NodePort(self, "In", "dummy", False)
            self._summary_output = NodePort(self, "Out", "dummy", True)
            self._summary_input.is_summary_port = True
            self._summary_output.is_summary_port = True
            self._summary_input.setVisible(False)
            self._summary_output.setVisible(False)

            if hasattr(self, '_overlay_color'):
                self._summary_input.set_state_overlay(self._overlay_color)
                self._summary_output.set_state_overlay(self._overlay_color)

    # ------------------------------------------------------------------
    # Port Overlay Propagation
    # ------------------------------------------------------------------

    def refresh_port_overlays(self):
        """
        Notify all ports about current state overlay color.

        Ensures that when node states change, connected ports receive
        the appropriate visual overlay colors for blending with their base colors.
        """
        for port in self.inputs:
            if hasattr(port, 'set_state_overlay'):
                port.set_state_overlay(self._overlay_color)

        for port in self.outputs:
            if hasattr(port, 'set_state_overlay'):
                port.set_state_overlay(self._overlay_color)

    # ------------------------------------------------------------------
    # Port CRUD
    # ------------------------------------------------------------------

    def add_input(self, name: str, datatype: str = "flow", desc: str = "") -> NodePort:
        """Adds an input port and returns it."""
        return self._add_port(name, datatype, is_output=False, desc=desc)

    def add_output(self, name: str, datatype: str = "flow", desc: str = "") -> NodePort:
        """Adds an output port and returns it."""
        return self._add_port(name, datatype, is_output=True, desc=desc)

    def _add_port(self, name: str, datatype: str, is_output: bool, desc: str = "") -> NodePort:
        """Adds a port to the node with duplicate name validation."""
        existing_ports = self.outputs if is_output else self.inputs
        if any(p.name == name for p in existing_ports):
            port_type = "Output" if is_output else "Input"
            raise ValueError(
                f"{port_type} port '{name}' already exists on node '{self.header._title.toPlainText()}'"
            )

        port = NodePort(self, name, datatype, is_output, desc)

        if is_output:
            self.outputs.append(port)
        else:
            self.inputs.append(port)

        self.enforce_min_dimensions()
        self.update_geometry()
        self._update_all_connected_traces()

        return port

    def remove_port(self, port: NodePort) -> bool:
        """Removes a single port and disconnects its traces."""
        traces_attr = getattr(port, 'connected_traces', [])
        for trace in list(traces_attr):
            if hasattr(trace, 'remove_from_scene'):
                trace.remove_from_scene()

        removed = False
        if port in self.inputs:
            self.inputs.remove(port)
            removed = True
        elif port in self.outputs:
            self.outputs.remove(port)
            removed = True

        if removed:
            port.setParentItem(None)
            if self.scene():
                self.scene().removeItem(port)
            self.update_geometry()

        return removed

    def find_port(self, name: str, is_output: Optional[bool] = None) -> Optional[NodePort]:
        """Finds a port by name."""
        if is_output is None:
            ports_to_search = self.inputs + self.outputs
        elif is_output:
            ports_to_search = self.outputs
        else:
            ports_to_search = self.inputs

        for port in ports_to_search:
            if port.name == name:
                return port
        return None

    def clear_ports(self):
        """Removes all ports (creates copy of list before iterating)."""
        for p in list(self.inputs + self.outputs):
            self.remove_port(p)
        self.inputs.clear()
        self.outputs.clear()
        self.update_geometry()

    def _update_all_connected_traces(self):
        """Updates paths for all connected traces."""
        traces_to_update = set()
        for port in (self.inputs + self.outputs + [self._summary_input, self._summary_output]):
            traces_attr = getattr(port, 'connected_traces', [])
            for trace in traces_attr:
                traces_to_update.add(trace)

        for trace in traces_to_update:
            trace.update_path()

    # ------------------------------------------------------------------
    # Content Widget
    # ------------------------------------------------------------------

    def set_content_widget(self, widget: QWidget):
        """
        Sets the internal widget and forces the UI engine to calculate
        metrics immediately.

        If the widget is a WeaveWidgetCore, automatically wires up the
        back-reference so BaseControlNode can find it for serialisation.
        """
        if widget.layout():
            widget.layout().activate()

        self.body.set_content(widget)

        # WeaveWidgetCore awareness (duck-typed, no hard import)
        if hasattr(widget, 'set_node') and hasattr(widget, 'get_port_definitions'):
            widget.set_node(self)
            self._weave_core = widget

        self._sync_to_widget_size()

    def _sync_to_widget_size(self):
        """Helper to force geometry re-calc based on body contents."""
        if self.scene():
            self.prepareGeometryChange()
        self.enforce_min_dimensions()
        self.update_geometry()
        self.update()

    # ------------------------------------------------------------------
    # State Management
    # ------------------------------------------------------------------

    def set_state(self, state: NodeState) -> None:
        """
        Sets the execution state of the node.
        Handles ONLY visual aspects; subclasses should override for dataflow logic.
        """
        if not isinstance(state, NodeState):
            raise TypeError(f"state must be NodeState enum, got {type(state)}")

        old_state = self._state
        if old_state == state:
            return

        self._state = state
        self._apply_state_visuals(state)

        if hasattr(self, 'header'):
            self.header.sync_state_slider(state, animate=True)
            self.header.update()
        self.update()

        self.state_changed.emit(old_state, state)

    def cycle_state(self, reverse: bool = False) -> None:
        """
        Cycles through states: NORMAL -> PASSTHROUGH -> DISABLED.

        Called by the canvas state machine (IdleState) when user clicks on the
        StateSlider.
        """
        cycle_order = [
            NodeState.NORMAL,
            NodeState.PASSTHROUGH,
            NodeState.DISABLED
        ]

        try:
            current_idx = cycle_order.index(self._state)
        except ValueError:
            self.set_state(cycle_order[0])
            return

        direction = -1 if reverse else 1
        next_idx = (current_idx + direction) % len(cycle_order)
        self.set_state(cycle_order[next_idx])

    @property
    def state(self) -> NodeState:
        """Get the current execution state of the node."""
        return self._state

    def _apply_state_visuals(self, state: NodeState) -> None:
        """
        Applies visual styling based on the node state.

        Handles COMPUTING state with animated pulsing glow.
        """
        #from PySide6.QtCore import QVariantAnimation

        vis_conf = self._config.get('state_visuals', {})
        default_vis = vis_conf.get('NORMAL', {'opacity': 1.0, 'overlay': QColor(0, 0, 0, 0)})

        state_map = {
            NodeState.NORMAL: 'NORMAL',
            NodeState.PASSTHROUGH: 'PASSTHROUGH',
            NodeState.DISABLED: 'DISABLED',
        }
        if hasattr(NodeState, 'COMPUTING'):
            state_map[NodeState.COMPUTING] = 'COMPUTING'

        state_key = state_map.get(state, 'NORMAL')
        visuals = vis_conf.get(state_key, default_vis)

        if "opacity" in visuals:
            opacity = float(visuals["opacity"])
            self.setOpacity(max(0.0, min(1.0, opacity)))

        self._overlay_color = visuals.get("overlay", QColor(0, 0, 0, 0))
        self.refresh_port_overlays()

        # COMPUTING pulse animation
        if hasattr(NodeState, 'COMPUTING') and state == NodeState.COMPUTING:
            self._start_computing_pulse()
        else:
            self._stop_computing_pulse()

        self.update()
