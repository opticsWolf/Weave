# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodePortsMixin - Port management, content widget, and execution state for Node.

Changelog
---------
- remove_port() rewritten with full disconnection chain:
    trace teardown → widget re-enable → StyleManager unregister →
    scene removal → list removal → geometry rebuild → signal emission.
- Added remove_port_by_uuid(), remove_ports() (batch), find_port_by_uuid().
- clear_ports() now delegates to remove_ports() for consistent cleanup.
- Added port_removed / port_added signals (declared on Node).
- FIXED: Removed undefined _dbg reference in _detach_port
"""

import uuid
from typing import Optional, List, Dict, Any, Union
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QColor

from weave.node.node_enums import NodeState
from weave.node.node_port import NodePort
from weave.stylemanager import StyleManager, StyleCategory

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
        self.state_changed: Signal(object, object)
        self.port_removed: Signal(object)   — emitted after a port is fully removed
        self.port_added: Signal(object)     — emitted after a port is added
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
        """Add a port, returning the existing one if a duplicate exists.

        Idempotent: if a port with the same name already exists on the
        same side, it is returned directly and no signal is emitted.
        """
        port_list = self.outputs if is_output else self.inputs
        existing = next((p for p in port_list if p.name == name), None)
        if existing is not None:
            log.debug(
                f"_add_port: {'output' if is_output else 'input'} port "
                f"'{name}' already exists on '{self._get_title_text()}' "
                f"— returning existing"
            )
            return existing

        port = NodePort(self, name, datatype, is_output, desc)
        port_list.append(port)

        log.debug(
            f"_add_port: created {'output' if is_output else 'input'} "
            f"'{name}' on '{self._get_title_text()}'"
        )

        self.auto_resize()
        self.update_geometry()
        self._update_all_connected_traces()

        if hasattr(self, 'port_added'):
            self.port_added.emit(port)

        return port

    # ------------------------------------------------------------------
    # Port Removal — single port
    # ------------------------------------------------------------------

    def remove_port(
        self,
        port: Union[NodePort, str],
        is_output: Optional[bool] = None,
    ) -> bool:
        """
        Fully remove a port from this node, tearing down every subsystem
        that references it.

        Accepts a ``NodePort`` instance **or** a port-name string.  When a
        name is given, *is_output* can narrow the search to one side.

        Removal sequence
        ~~~~~~~~~~~~~~~~
        1. **Resolve** the port reference (by object or name).
        2. **Disconnect every trace** via ``trace.remove_from_scene()`` which
           cascades through ``ConnectionFactory.remove()``:
           - unregisters the trace from both source and target ports
             (``port.remove_trace``),
           - removes the trace ``QGraphicsItem`` from the scene,
           - re-triggers downstream recomputation (``set_dirty``).
        3. **Re-enable** any auto-disabled widget bound to this port
           (``_auto_disable`` / ``_set_widget_enabled``).
        4. **Unregister** the port from the ``StyleManager`` (PORT + TRACE
           categories) so it stops receiving theme-change callbacks.
        5. **Remove** the port ``QGraphicsItem`` from the scene graph.
        6. **Remove** the port from ``self.inputs`` / ``self.outputs``.
        7. **Rebuild** node geometry (min dimensions, cached paths, layout).
        8. **Emit** ``port_removed`` so external listeners can react.

        Args:
            port:      The port to remove — ``NodePort`` or port-name ``str``.
            is_output: When *port* is a string, optionally restrict the
                       lookup to inputs (``False``) or outputs (``True``).

        Returns:
            ``True`` if the port was found and removed, ``False`` otherwise.
        """
        # 1. Resolve -------------------------------------------------------
        port_label = port if isinstance(port, str) else getattr(port, 'name', '?')
        log.debug(
            f"remove_port ENTER: '{port_label}' is_output={is_output} "
            f"on '{self._get_title_text()}'"
        )
        if isinstance(port, str):
            resolved = self.find_port(port, is_output=is_output)
            if resolved is None:
                log.warning(
                    f"remove_port: port '{port}' not found on node "
                    f"'{self._get_title_text()}'"
                )
                return False
            port = resolved

        if not isinstance(port, NodePort):
            log.warning(f"remove_port: expected NodePort or str, got {type(port)}")
            return False

        if port not in self.inputs and port not in self.outputs:
            log.warning(
                f"remove_port: port '{port.name}' does not belong to node "
                f"'{self._get_title_text()}'"
            )
            return False

        self._detach_port(port)
        self._rebuild_after_port_change()

        if hasattr(self, 'port_removed'):
            self.port_removed.emit(port)

        log.debug(
            f"Removed {'output' if port.is_output else 'input'} port "
            f"'{port.name}' from node '{self._get_title_text()}'"
        )
        return True

    # ------------------------------------------------------------------
    # Port Removal — by UUID
    # ------------------------------------------------------------------

    def remove_port_by_uuid(self, port_uuid: Union[str, uuid.UUID]) -> bool:
        """
        Remove a port identified by its UUID.

        Searches both inputs and outputs for a port matching *port_uuid*
        and delegates to :meth:`remove_port`.

        Returns:
            ``True`` if found and removed, ``False`` otherwise.
        """
        for port in self.inputs + self.outputs:
            if hasattr(port, 'matches_uuid') and port.matches_uuid(port_uuid):
                return self.remove_port(port)

        log.warning(
            f"remove_port_by_uuid: no port with UUID {port_uuid} on node "
            f"'{self._get_title_text()}'"
        )
        return False

    # ------------------------------------------------------------------
    # Port Removal — batch
    # ------------------------------------------------------------------

    def remove_ports(
        self,
        ports: List[Union[NodePort, str]],
        is_output: Optional[bool] = None,
    ) -> int:
        """
        Batch-remove multiple ports with a single geometry rebuild.

        More efficient than calling :meth:`remove_port` in a loop because
        the expensive geometry recalculation runs only once at the end.

        Args:
            ports:     List of ``NodePort`` instances or port-name strings.
            is_output: When names are used, optionally restrict the lookup.

        Returns:
            The number of ports successfully removed.
        """
        removed: List[NodePort] = []

        for p in ports:
            if isinstance(p, str):
                resolved = self.find_port(p, is_output=is_output)
                if resolved is None:
                    continue
                p = resolved

            if not isinstance(p, NodePort):
                continue
            if p not in self.inputs and p not in self.outputs:
                continue

            self._detach_port(p)
            removed.append(p)

        if removed:
            self._rebuild_after_port_change()
            if hasattr(self, 'port_removed'):
                for p in removed:
                    self.port_removed.emit(p)

        return len(removed)

    # ------------------------------------------------------------------
    # Internal: detach a single port (no geometry rebuild)
    # ------------------------------------------------------------------

    def _detach_port(self, port: NodePort) -> None:
        """
        Perform the destructive teardown of *port* without rebuilding node
        geometry.  Called by both :meth:`remove_port` and
        :meth:`remove_ports`; the caller is responsible for the final
        geometry pass.
        """
        trace_count = len(getattr(port, 'connected_traces', []))
        log.debug(
            f"_detach_port: '{port.name}' on '{self._get_title_text()}' "
            f"(traces={trace_count})"
        )

        # 2. Disconnect traces --------------------------------------------
        for trace in list(getattr(port, 'connected_traces', [])):
            try:
                if hasattr(trace, 'remove_from_scene'):
                    trace.remove_from_scene(trigger_compute=True)
            except Exception as exc:
                log.warning(
                    f"_detach_port: failed to remove trace from port "
                    f"'{port.name}': {exc}"
                )

        # Safety belt — clear any stranded references.
        if port.connected_traces:
            port.connected_traces.clear()

        # 3. Re-enable auto-disabled widget --------------------------------
        if getattr(port, '_auto_disable', False) and not port.is_output:
            try:
                port._set_widget_enabled(True)
            except Exception:
                pass

        # 4. Unregister from StyleManager ----------------------------------
        try:
            sm = StyleManager.instance()
            sm.unregister(port, StyleCategory.PORT)
            sm.unregister(port, StyleCategory.TRACE)
        except Exception as exc:
            log.debug(f"_detach_port: StyleManager unregister note: {exc}")

        # 5. Remove QGraphicsItem from scene -------------------------------
        port.setParentItem(None)
        scene = self.scene()
        if scene and port.scene() is scene:
            scene.removeItem(port)

        # 6. Remove from node's port lists ---------------------------------
        if port.is_output and port in self.outputs:
            self.outputs.remove(port)
        elif not port.is_output and port in self.inputs:
            self.inputs.remove(port)

    # ------------------------------------------------------------------
    # Internal: single geometry rebuild after one or more port removals
    # ------------------------------------------------------------------

    def _rebuild_after_port_change(self) -> None:
        """Recalculate layout, paths, and repaint after ports changed."""
        self.auto_resize()
        self._recalculate_paths()
        self.update_geometry()
        self._update_all_connected_traces()
        self.update()
        if hasattr(self, 'header'):
            self.header.update()
        if hasattr(self, 'body'):
            self.body.update()

    # ------------------------------------------------------------------
    # Port Lookup
    # ------------------------------------------------------------------

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

    def find_port_by_uuid(self, port_uuid: Union[str, uuid.UUID]) -> Optional[NodePort]:
        """Find a port by its UUID across both inputs and outputs."""
        for port in self.inputs + self.outputs:
            if hasattr(port, 'matches_uuid') and port.matches_uuid(port_uuid):
                return port
        return None

    # ------------------------------------------------------------------
    # Clear All Ports
    # ------------------------------------------------------------------

    def clear_ports(self, side: Optional[str] = None):
        """
        Remove all ports, or only one side.

        Args:
            side: ``"input"`` / ``"output"`` / ``None`` (both).
        """
        if side == "input":
            targets = list(self.inputs)
        elif side == "output":
            targets = list(self.outputs)
        else:
            targets = list(self.inputs + self.outputs)

        self.remove_ports(targets)

    # ------------------------------------------------------------------
    # Connected Traces
    # ------------------------------------------------------------------

    def _update_all_connected_traces(self):
        """Refresh bezier paths for all traces connected to this node."""
        all_ports = (
            self.inputs
            + self.outputs
            + [self._summary_input, self._summary_output]
        )
        traces = {
            t
            for p in all_ports
            if p is not None
            for t in getattr(p, 'connected_traces', [])
        }
        for trace in traces:
            trace.update_path()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_title_text(self) -> str:
        """Safely retrieve the node's title string for log messages."""
        try:
            return self.header._title.toPlainText()
        except Exception:
            return "<unknown>"

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

        self.widget_host.set_content(widget)

        if hasattr(widget, 'set_node') and hasattr(widget, 'get_port_definitions'):
            widget.set_node(self)
            self._weave_core = widget

        self._sync_to_widget_size()

    def _sync_to_widget_size(self):
        """Force geometry recalc based on body contents."""
        if self.scene():
            self.prepareGeometryChange()
        self.auto_resize()
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
