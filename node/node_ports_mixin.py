# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodePortsMixin - Port management, content widget, and execution state for Node.
"""

from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QColor

from weave.node.node_subcomponents import NodeState
from weave.node.node_port import NodePort

from weave.logger import get_logger
log = get_logger("NodePortsMixin")


class NodePortsMixin:
    """
    Mixin providing port management, content widget, and state management for Node.

    Expects the host class to have:
        self.inputs, self.outputs: List[NodePort]
        self._summary_input, self._summary_output: Optional[NodePort]
        self._overlay_color: QColor
        self._config: Dict[str, Any]
        self.header, self.body
        self.enforce_min_dimensions(), self.update_geometry(), self.update()
        self._computing_pulse_anim: QVariantAnimation
        self.state_changed: Signal
    """

    # ------------------------------------------------------------------
    # Summary Ports
    # ------------------------------------------------------------------

    def _initialize_summary_ports(self):
        """Create hidden summary ports for minimized-node connection forwarding."""
        if self._summary_input is not None:
            return

        self._summary_input = NodePort(self, "In", "dummy", False)
        self._summary_output = NodePort(self, "Out", "dummy", True)

        for port in (self._summary_input, self._summary_output):
            port.is_summary_port = True
            port.setVisible(False)
            if hasattr(self, '_overlay_color'):
                port.set_state_overlay(self._overlay_color)

    # ------------------------------------------------------------------
    # Port Overlay Propagation
    # ------------------------------------------------------------------

    def refresh_port_overlays(self):
        """Push current state overlay color to all ports."""
        for port in self.inputs + self.outputs:
            port.set_state_overlay(self._overlay_color)

    # ------------------------------------------------------------------
    # Port CRUD
    # ------------------------------------------------------------------

    def add_input(self, name: str, datatype: str = "flow", desc: str = "") -> NodePort:
        return self._add_port(name, datatype, is_output=False, desc=desc)

    def add_output(self, name: str, datatype: str = "flow", desc: str = "") -> NodePort:
        return self._add_port(name, datatype, is_output=True, desc=desc)

    def _add_port(self, name: str, datatype: str, is_output: bool, desc: str = "") -> NodePort:
        """Add a port with duplicate-name validation."""
        port_list = self.outputs if is_output else self.inputs
        if any(p.name == name for p in port_list):
            side = "Output" if is_output else "Input"
            raise ValueError(
                f"{side} port '{name}' already exists on node "
                f"'{self.header._title.toPlainText()}'"
            )

        port = NodePort(self, name, datatype, is_output, desc)
        port_list.append(port)

        self.enforce_min_dimensions()
        self.update_geometry()
        self._update_all_connected_traces()
        return port

    def remove_port(self, port: NodePort) -> bool:
        """Remove a port, disconnecting all its traces first."""
        for trace in list(getattr(port, 'connected_traces', [])):
            if hasattr(trace, 'remove_from_scene'):
                trace.remove_from_scene()

        for port_list in (self.inputs, self.outputs):
            if port in port_list:
                port_list.remove(port)
                port.setParentItem(None)
                if self.scene():
                    self.scene().removeItem(port)
                self.update_geometry()
                return True

        return False

    def find_port(self, name: str, is_output: Optional[bool] = None) -> Optional[NodePort]:
        """Find a port by name, optionally filtering by direction."""
        if is_output is True:
            search = self.outputs
        elif is_output is False:
            search = self.inputs
        else:
            search = self.inputs + self.outputs

        for port in search:
            if port.name == name:
                return port
        return None

    def clear_ports(self):
        """Remove all ports."""
        for port in list(self.inputs + self.outputs):
            self.remove_port(port)
        self.inputs.clear()
        self.outputs.clear()
        self.update_geometry()

    def _update_all_connected_traces(self):
        """Refresh bezier paths for all traces connected to this node."""
        all_ports = self.inputs + self.outputs + [self._summary_input, self._summary_output]
        traces = {t for p in all_ports for t in getattr(p, 'connected_traces', [])}
        for trace in traces:
            trace.update_path()

    # ------------------------------------------------------------------
    # Content Widget
    # ------------------------------------------------------------------

    def set_content_widget(self, widget: QWidget):
        """
        Set the node's internal widget. If it's a WeaveWidgetCore,
        automatically wire up the back-reference for serialisation.
        """
        if widget.layout():
            widget.layout().activate()

        self.body.set_content(widget)

        if hasattr(widget, 'set_node') and hasattr(widget, 'get_port_definitions'):
            widget.set_node(self)
            self._weave_core = widget

        self._sync_to_widget_size()

    def _sync_to_widget_size(self):
        """Force geometry recalc based on body contents."""
        if self.scene():
            self.prepareGeometryChange()
        self.enforce_min_dimensions()
        self.update_geometry()
        self.update()

    # ------------------------------------------------------------------
    # Execution State
    # ------------------------------------------------------------------

    def set_state(self, state: NodeState) -> None:
        """Set the execution state (visual aspects only; subclasses handle dataflow)."""
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
        """Cycle through NORMAL → PASSTHROUGH → DISABLED."""
        cycle = [NodeState.NORMAL, NodeState.PASSTHROUGH, NodeState.DISABLED]
        try:
            idx = cycle.index(self._state)
        except ValueError:
            self.set_state(cycle[0])
            return

        step = -1 if reverse else 1
        self.set_state(cycle[(idx + step) % len(cycle)])

    @property
    def state(self) -> NodeState:
        return self._state

    def _apply_state_visuals(self, state: NodeState) -> None:
        """Apply opacity, overlay color, and computing pulse for the given state."""
        vis_conf = self._config.get('state_visuals', {})
        default_vis = vis_conf.get('NORMAL', {'opacity': 1.0, 'overlay': QColor(0, 0, 0, 0)})

        state_map = {
            NodeState.NORMAL: 'NORMAL',
            NodeState.PASSTHROUGH: 'PASSTHROUGH',
            NodeState.DISABLED: 'DISABLED',
        }
        if hasattr(NodeState, 'COMPUTING'):
            state_map[NodeState.COMPUTING] = 'COMPUTING'

        visuals = vis_conf.get(state_map.get(state, 'NORMAL'), default_vis)

        if "opacity" in visuals:
            self.setOpacity(max(0.0, min(1.0, float(visuals["opacity"]))))

        self._overlay_color = visuals.get("overlay", QColor(0, 0, 0, 0))
        self.refresh_port_overlays()

        if hasattr(NodeState, 'COMPUTING') and state == NodeState.COMPUTING:
            self._start_computing_pulse()
        else:
            self._stop_computing_pulse()

        self.update()