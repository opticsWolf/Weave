# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

demo_dynamic_port_nodes.py
--------------------------
Two test / demo nodes for exercising the dynamic port removal and
addition API introduced in ``NodePortsMixin``.

Provided nodes
--------------

``TypeAdapterNode``
    Starts with a generic input and a generic output.  When an
    upstream trace is connected, ``compute()`` detects the source
    port's datatype, removes the old output, and creates a new one
    that matches.  When disconnected, the output reverts to generic.

``MultiFloatOutputNode``
    A *Count* spinbox (with an auto-disable integer input port)
    controls how many float output ports exist.  Each output has a
    matching ``QDoubleSpinBox`` in the node body **registered with
    WidgetCore** so that dock panels mirror them automatically.

    Increasing the count calls ``register_widget()`` for new spinboxes;
    decreasing calls ``unregister_widget()`` which emits
    ``widget_unregistered`` — dock panels hear this and remove the
    mirror row incrementally.

    Uses ``VerticalSizePolicy.FIT`` so the node visually shrinks when
    outputs are removed.

Both nodes include extensive ``log.debug`` / ``print`` tracing so
the port lifecycle can be followed in the console.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QSpinBox,
)

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore

from weave.logger import get_logger

log = get_logger("DemoDynamicPorts")


def _dbg(msg: str) -> None:
    """Print + log.debug helper for maximum visibility during testing."""
    print(f"[DemoDynamicPorts] {msg}", flush=True)
    log.debug(msg)


# ══════════════════════════════════════════════════════════════════════════════
# TypeAdapterNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TypeAdapterNode(ActiveNode):
    """
    Passthrough node that adapts its output port type to match
    whatever is connected to its input.

    Starts with:
        input  ``data_in``   datatype = ``"generic"``
        output ``data_out``  datatype = ``"generic"``

    When a trace connects to ``data_in``, ``compute()`` reads the
    source port's ``datatype`` attribute.  If it differs from the
    current output, the old output port is removed and a new one is
    created with the matching type.  On disconnect the output reverts
    to ``"generic"``.

    The node body shows a read-only status label reflecting the
    current adapted type.

    Type: Active (propagates downstream immediately).
    """

    node_class:       ClassVar[str]            = "Demo"
    node_subclass:    ClassVar[str]            = "Utility"
    node_name:        ClassVar[Optional[str]]  = "Type Adapter"
    node_description: ClassVar[Optional[str]]  = (
        "Adapts output port type to match connected input type"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "demo", "adapter", "type", "dynamic", "passthrough",
    ]

    def __init__(self, title: str = "Type Adapter", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        _dbg("TypeAdapterNode.__init__: creating ports")

        # ── Ports ─────────────────────────────────────────────────────
        self.add_input("data_in", "generic")
        self.add_output("data_out", "generic")

        _dbg(f"  inputs:  {[p.name for p in self.inputs]}")
        _dbg(f"  outputs: {[p.name for p in self.outputs]}")

        # Track the current output datatype so we know when to swap
        self._current_out_type: str = "generic"

        # ── Widget: status label ──────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        self._label_type = QLabel("generic")
        self._label_type.setEnabled(False)
        self._label_type.setMinimumWidth(120)
        form.addRow("Type:", self._label_type)

        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        _dbg("TypeAdapterNode.__init__: done")

    # ── Port type adaptation ──────────────────────────────────────────────

    def _detect_input_type(self) -> str:
        """Read the datatype of the port connected to ``data_in``."""
        in_port = self.find_port("data_in", is_output=False)
        if in_port is None:
            return "generic"

        traces = getattr(in_port, 'connected_traces', [])
        if not traces:
            return "generic"

        src_port = getattr(traces[0], 'source', None)
        if src_port is None:
            return "generic"

        dtype = getattr(src_port, 'datatype', "generic")
        _dbg(f"_detect_input_type: source port '{src_port.name}' "
             f"datatype='{dtype}'")
        return dtype

    def _adapt_output_type(self, new_type: str) -> None:
        """Remove the current output port and create a new one with
        *new_type* — but only if the type actually changed.
        """
        if new_type == self._current_out_type:
            return

        old_type = self._current_out_type
        _dbg(f"_adapt_output_type: '{old_type}' → '{new_type}'")

        # Remove old output
        self.remove_port("data_out", is_output=True)

        # Create new output
        self.add_output("data_out", new_type)
        self._current_out_type = new_type

        try:
            self._label_type.setText(new_type)
        except RuntimeError:
            pass

    # ── Computation ───────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        detected = self._detect_input_type()
        self._adapt_output_type(detected)

        value = inputs.get("data_in")
        return {"data_out": value}

    # ── Serialisation ────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["type_adapter"] = {
            "current_out_type": self._current_out_type,
        }
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        super().restore_state(state)

        ta = state.get("type_adapter", {})
        self._current_out_type = ta.get("current_out_type", "generic")

        try:
            self._label_type.setText(self._current_out_type)
        except RuntimeError:
            pass

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# MultiFloatOutputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class MultiFloatOutputNode(ActiveNode):
    """
    Node with a configurable number of float output ports.

    A *Count* ``QSpinBox`` (range 1–16) controls how many outputs
    exist.  Each output ``float_0``, ``float_1``, … has a matching
    ``QDoubleSpinBox`` **registered with WidgetCore** via
    ``register_widget()``.

    When the count increases, new spinboxes are registered —
    ``WidgetCore`` emits ``widget_registered`` and any connected dock
    panel adds a mirror row automatically.

    When the count decreases, spinboxes are unregistered —
    ``WidgetCore`` emits ``widget_unregistered`` and dock panels
    remove the mirror row.  No full panel rebuild is needed.

    An integer input port ``count`` drives the spinbox value with
    ``_auto_disable``.

    Type: Active (propagates downstream on every change).
    """

    MAX_OUTPUTS: ClassVar[int] = 16
    _DEFAULT_VALUE: ClassVar[float] = 0.0

    node_class:       ClassVar[str]            = "Demo"
    node_subclass:    ClassVar[str]            = "Generator"
    node_name:        ClassVar[Optional[str]]  = "Multi Float"
    node_description: ClassVar[Optional[str]]  = (
        "Dynamic number of float output ports with per-output spinboxes"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "demo", "float", "multi", "dynamic", "generator",
    ]

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(
        self,
        title: str = "Multi Float",
        initial_count: int = 2,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        _dbg("MultiFloatOutputNode.__init__: start")

        # ── Count input port (auto-disables the spinbox) ──────────
        self.add_input("count", "int")
        self.inputs[-1]._auto_disable = True

        # ── Widget layout ─────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Count spinbox — registered with WidgetCore for mirroring
        self._spin_count = QSpinBox()
        self._spin_count.setRange(1, self.MAX_OUTPUTS)
        self._spin_count.setValue(initial_count)
        self._spin_count.setMinimumWidth(60)
        form.addRow("Count:", self._spin_count)

        self._widget_core.register_widget(
            "count", self._spin_count,
            role="bidirectional", datatype="int", default=2,
            add_to_layout=False,
        )

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep)

        # ── Dynamic state ─────────────────────────────────────────
        self._current_count: int = 0

        # ── Finalise ──────────────────────────────────────────────
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()

        # Build initial outputs (signals blocked to avoid mid-init compute)
        self._spin_count.blockSignals(True)
        self._set_count(initial_count)
        self._spin_count.blockSignals(False)

        # Wire count change — the spinbox drives _set_count directly,
        # and WidgetCore's value_changed handles upstream count changes.
        self._spin_count.valueChanged.connect(self._on_count_changed)
        self._widget_core.value_changed.connect(self._on_core_changed)
        self._widget_core.refresh_widget_palettes()

        _dbg(f"MultiFloatOutputNode.__init__: done, "
             f"outputs={[p.name for p in self.outputs]}")

    # ── Dynamic output management ─────────────────────────────────────────

    def _set_count(self, new_count: int) -> None:
        """Grow or shrink the float-output/widget set to *new_count*."""
        new_count = max(1, min(self.MAX_OUTPUTS, new_count))
        old_count = self._current_count

        _dbg(f"_set_count: {old_count} → {new_count}")

        if new_count > old_count:
            for i in range(old_count, new_count):
                self._add_float_output(i)

        elif new_count < old_count:
            self._remove_float_outputs(new_count, old_count)

        self._current_count = new_count
        _dbg(f"  _current_count={self._current_count}, "
             f"outputs={[p.name for p in self.outputs]}, "
             f"wc_bindings={list(self._widget_core.bindings().keys())}")

    def _add_float_output(self, i: int) -> None:
        """Add a single float output port + spinbox row at index *i*."""
        form: QFormLayout = self._widget_core.layout()
        port_name = f"float_{i}"

        spin = QDoubleSpinBox()
        spin.setRange(-1e9, 1e9)
        spin.setValue(self._DEFAULT_VALUE)
        spin.setDecimals(4)
        spin.setMinimumWidth(100)
        form.addRow(f"Float {i}:", spin)

        # Register with WidgetCore — this:
        #   1. Wires the spinbox's valueChanged → wc.value_changed
        #   2. Emits widget_registered(port_name) so dock panels add a mirror
        self._widget_core.register_widget(
            port_name, spin,
            role="output", datatype="float", default=self._DEFAULT_VALUE,
            add_to_layout=False,  # already placed in the form above
        )

        self.add_output(port_name, "float")

        _dbg(f"  _add_float_output({i}): port='{port_name}', "
             f"outputs now={[p.name for p in self.outputs]}")

    def _remove_float_outputs(self, from_idx: int, to_idx: int) -> None:
        """Remove float outputs from *to_idx - 1* down to *from_idx* (LIFO).

        For each output:
        1. Unregister the widget from WidgetCore (emits ``widget_unregistered``
           so dock panels remove the mirror row).
        2. Remove the spinbox row from the node body's form layout.
        3. Batch-remove the output ports.
        """
        form: QFormLayout = self._widget_core.layout()

        for i in range(to_idx - 1, from_idx - 1, -1):
            port_name = f"float_{i}"
            _dbg(f"  removing widget + port '{port_name}'")

            # Unregister from WidgetCore — disconnects signals, emits
            # widget_unregistered so any connected panel drops the mirror.
            spin = self._widget_core.unregister_widget(port_name)
            if spin is not None:
                try:
                    form.removeRow(spin)
                except Exception as exc:
                    _dbg(f"  form.removeRow failed for {port_name}: {exc}")

        # Batch-remove ports for a single geometry rebuild.
        port_names = [f"float_{i}" for i in range(from_idx, to_idx)]
        _dbg(f"  batch removing ports: {port_names}")
        removed = self.remove_ports(port_names, is_output=True)
        _dbg(f"  remove_ports returned: {removed}")

    # ── Slots ─────────────────────────────────────────────────────────────

    @Slot(int)
    def _on_count_changed(self, value: int) -> None:
        """Rebuild outputs when the count spinbox changes."""
        _dbg(f"_on_count_changed: value={value}")
        try:
            self._set_count(value)
            self.on_ui_change()
            self._widget_core.refresh_widget_palettes()
        except Exception as exc:
            log.error(f"Exception in _on_count_changed: {exc}")
            _dbg(f"  EXCEPTION: {exc}")

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Handle WidgetCore value changes."""
        _dbg(f"_on_core_changed: port_name='{port_name}'")
        try:
            if port_name == "count":
                # Count changed via upstream connection → rebuild
                val = self._widget_core.get_port_value("count")
                if val is not None:
                    self._set_count(int(val))
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in _on_core_changed: {exc}")

    # ── Computation ───────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Read each float spinbox via WidgetCore and emit on the matching
        output port.

        The ``count`` input (if connected) drives the spinbox; by the
        time ``compute`` runs, ``_on_core_changed`` has already called
        ``_set_count`` so the port count is current.
        """
        _dbg(f"MultiFloatOutputNode.compute: "
             f"count={self._current_count}, inputs={inputs}")

        # Handle upstream count
        count_val = inputs.get("count")
        if count_val is not None:
            new_count = max(1, min(self.MAX_OUTPUTS, int(count_val)))
            if new_count != self._current_count:
                _dbg(f"  upstream count={new_count}, rebuilding")
                self._set_count(new_count)

        result: Dict[str, Any] = {}
        for i in range(self._current_count):
            port_name = f"float_{i}"
            # Read via WidgetCore — the single source of truth.
            val = self._widget_core.get_port_value(port_name)
            if val is None:
                val = self._DEFAULT_VALUE
            result[port_name] = float(val)

        _dbg(f"  result keys: {list(result.keys())}")
        return result

    # ── Serialisation ────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["multi_float"] = {
            "count": self._current_count,
            "values": {
                str(i): self._widget_core.get_port_value(f"float_{i}")
                for i in range(self._current_count)
            },
        }
        _dbg(f"get_state: count={self._current_count}, "
             f"outputs={[p.name for p in self.outputs]}")
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        _dbg("MultiFloatOutputNode.restore_state: start")
        super().restore_state(state)

        mf = state.get("multi_float", {})
        count = mf.get("count", 2)

        _dbg(f"  saved count={count}, "
             f"current outputs={[p.name for p in self.outputs]}")

        # ── Clean up stale state from __init__ + base restore ─────
        # Base class re-created float_N ports from saved data.
        # Remove them so _set_count can rebuild with matching widgets.
        float_ports = [p for p in list(self.outputs)
                       if p.name.startswith("float_")]
        if float_ports:
            _dbg(f"  removing stale float ports: "
                 f"{[p.name for p in float_ports]}")
            self.remove_ports(float_ports)

        # Remove stale widget bindings from __init__'s _set_count
        form: QFormLayout = self._widget_core.layout()
        for i in range(self._current_count):
            port_name = f"float_{i}"
            spin = self._widget_core.unregister_widget(port_name)
            if spin is not None:
                try:
                    form.removeRow(spin)
                except Exception:
                    pass
        self._current_count = 0

        # ── Rebuild from scratch ──────────────────────────────────
        self._spin_count.blockSignals(True)
        self._spin_count.setValue(count)
        self._spin_count.blockSignals(False)

        self._set_count(count)

        # Restore individual float values via WidgetCore
        for i_str, val in mf.get("values", {}).items():
            port_name = f"float_{int(i_str)}"
            if self._widget_core.has_binding(port_name):
                self._widget_core.set_port_value(port_name, float(val))

        self._widget_core.refresh_widget_palettes()

        _dbg(f"MultiFloatOutputNode.restore_state: done, "
             f"count={self._current_count}, "
             f"outputs={[(p.name, p.datatype) for p in self.outputs]}")

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        _dbg("MultiFloatOutputNode.cleanup")
        try:
            self._spin_count.valueChanged.disconnect(self._on_count_changed)
        except RuntimeError:
            pass

        self._widget_core.cleanup()
        super().cleanup()
