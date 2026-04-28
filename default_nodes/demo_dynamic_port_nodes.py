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

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QSpinBox,
)

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore, PortRole
from weave.node import VerticalSizePolicy
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
    # R3.1: Required for dynamic UI nodes
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Type Adapter", **kwargs: Any) -> None:
        # R4.1: Step 1 - super init
        super().__init__(title=title, **kwargs)

        _dbg("TypeAdapterNode.__init__: creating ports")

        # R4.1: Step 1 - Add ports (R5.1: lowercase datatypes, "any" replaces "generic")
        self.add_input("data_in", datatype="any")
        self.add_output("data_out", datatype="any")

        _dbg(f"  inputs:  {[p.name for p in self.inputs]}")
        _dbg(f"  outputs: {[p.name for p in self.outputs]}")

        self._current_out_type: str = "any"

        # R4.1: Step 2 - Layout + WidgetCore
        form = QFormLayout(); form.setContentsMargins(5, 5, 5, 5); form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # R4.1: Step 3 - Create widget & place in layout
        self._label_type = QLabel("any")
        self._label_type.setEnabled(False)
        self._label_type.setMinimumWidth(120)
        form.addRow("Type:", self._label_type)

        # R4.1: Step 4 - Wire signals (R7.1/R7.2)
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # R4.1: Step 5 & 6 - Mount + Patch
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        _dbg("TypeAdapterNode.__init__: done")

    # ── Port type adaptation ──────────────────────────────────────────────

    def _detect_input_type(self, value: Any) -> str:
        """R9.3: Inspect value type instead of port internals for thread-safety."""
        if value is None:
            return "any"
        if isinstance(value, (int, float)):
            return "float"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, str):
            return "str"
        if isinstance(value, (list, tuple)):
            return "list"
        if isinstance(value, dict):
            return "json"
        return "any"

    def _adapt_output_type(self, new_type: str) -> None:
        """Remove the current output port and create a new one with
        *new_type* — but only if the type actually changed.
        """
        if new_type == self._current_out_type:
            return

        _dbg(f"_adapt_output_type: '{self._current_out_type}' → '{new_type}'")

        # R5.8: remove_ports is plural and takes a list
        self.remove_ports(["data_out"], is_output=True)

        self.add_output("data_out", new_type)
        self._current_out_type = new_type

        try:
            self._label_type.setText(new_type)
        except RuntimeError:
            pass

    # ── Slots ─────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_value_changed(self, _port: str) -> None:
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, _port: str, _value: Any) -> None:
        # R7.3: Structural sync mirror (no-op here, but required for undo safety)
        pass

    # ── Computation ───────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        # R9.2: Read inputs via dict
        value = inputs.get("data_in")
        detected = self._detect_input_type(value)
        self._adapt_output_type(detected)
        return {"data_out": value}

    # ── Serialisation ────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["type_adapter"] = {
            "current_out_type": self._current_out_type,
        }
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        # R14.3: 1. Static restore first
        super().restore_state(state)

        ta = state.get("type_adapter", {})
        self._current_out_type = ta.get("current_out_type", "any")

        try:
            self._label_type.setText(self._current_out_type)
        except RuntimeError:
            pass

        # Rebuild output port if type changed during save
        if self._current_out_type != "any":
            self.remove_ports(["data_out"], is_output=True)
            self.add_output("data_out", self._current_out_type)

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        # R16.1: super() last
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# MultiFloatOutputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class MultiFloatOutputNode(ActiveNode):
    """
    Node with a configurable number of float output ports.
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
    # R3.1
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(
        self,
        title: str = "Multi Float",
        initial_count: int = 2,
        **kwargs: Any,
    ) -> None:
        # R4.1: Step 1
        super().__init__(title=title, **kwargs)

        _dbg("MultiFloatOutputNode.__init__: start")

        # R4.1: Step 1 - Ports
        self.add_input("count", datatype="int")

        # R4.1: Step 2
        form = QFormLayout(); form.setContentsMargins(5, 5, 5, 5); form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # R4.1: Step 3 - Widgets
        self._spin_count = QSpinBox()
        self._spin_count.setRange(1, self.MAX_OUTPUTS)
        self._spin_count.setValue(initial_count)
        self._spin_count.setMinimumWidth(60)
        form.addRow("Count:", self._spin_count)

        # R6.1: Use PortRole enum. BIDIRECTIONAL drives auto-disable on connection.
        self._widget_core.register_widget(
            "count", self._spin_count,
            role=PortRole.BIDIRECTIONAL, datatype="int", default=2,
            add_to_layout=False,
        )

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep)

        self._current_count: int = 0

        # R4.1: Step 4 - Wire BOTH signals for structural sync (R7.3)
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # R4.1: Step 5 & 6
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

        # Build initial outputs (signals blocked to avoid mid-init compute)
        self._spin_count.blockSignals(True)
        self._set_count(initial_count)
        self._spin_count.blockSignals(False)

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

        # R6.1: Use PortRole.OUTPUT enum
        self._widget_core.register_widget(
            port_name, spin,
            role=PortRole.OUTPUT, datatype="float", default=self._DEFAULT_VALUE,
            add_to_layout=False,
        )

        self.add_output(port_name, "float")

        _dbg(f"  _add_float_output({i}): port='{port_name}', "
             f"outputs now={[p.name for p in self.outputs]}")

    def _remove_float_outputs(self, from_idx: int, to_idx: int) -> None:
        """Remove float outputs from *to_idx - 1* down to *from_idx* (LIFO)."""
        form: QFormLayout = self._widget_core.layout()

        for i in range(to_idx - 1, from_idx - 1, -1):
            port_name = f"float_{i}"
            _dbg(f"  removing widget + port '{port_name}'")

            spin = self._widget_core.unregister_widget(port_name)
            if spin is not None:
                try:
                    form.removeRow(spin)
                except Exception as exc:
                    _dbg(f"  form.removeRow failed for {port_name}: {exc}")

        # R5.8: remove_ports is plural, takes a list
        port_names = [f"float_{i}" for i in range(from_idx, to_idx)]
        _dbg(f"  batch removing ports: {port_names}")
        self.remove_ports(port_names, is_output=True)
        _dbg(f"  remove_ports succeeded")

    # ── Slots ─────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        """R7.1: User edits → structural sync + mark dirty."""
        _dbg(f"_on_value_changed: port='{port}'")
        try:
            if port == "count":
                val = self._widget_core.get_port_value("count")
                if val is not None:
                    self._set_count(int(val))
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in _on_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        """R7.2/R7.3: Programmatic/undo writes → structural sync only."""
        _dbg(f"_on_port_value_written: port='{port}'")
        try:
            if port == "count":
                self._set_count(int(value))
            # NOTE: Do NOT call on_ui_change() here
        except Exception as exc:
            log.error(f"Exception in _on_port_value_written: {exc}")

    # ── Computation ───────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """R9.2/R9.3: Read strictly from inputs dict. No WidgetCore/Qt access."""
        _dbg(f"MultiFloatOutputNode.compute: "
             f"count={self._current_count}, inputs={list(inputs.keys())}")

        count_val = inputs.get("count")
        if count_val is not None:
            new_count = max(1, min(self.MAX_OUTPUTS, int(count_val)))
            if new_count != self._current_count:
                _dbg(f"  upstream count={new_count}, rebuilding")
                self._set_count(new_count)

        result: Dict[str, Any] = {}
        for i in range(self._current_count):
            port_name = f"float_{i}"
            # R9.2: Framework merges registered widget values into inputs
            val = inputs.get(port_name, self._DEFAULT_VALUE)
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
        # R14.3: Strict restore sequence
        _dbg("MultiFloatOutputNode.restore_state: start")
        super().restore_state(state)

        mf = state.get("multi_float", {})
        count = mf.get("count", 2)

        _dbg(f"  saved count={count}, "
             f"current outputs={[p.name for p in self.outputs]}")

        # 2. Tear down stale dynamic widgets/ports
        float_ports = [p for p in list(self.outputs) if p.name.startswith("float_")]
        if float_ports:
            _dbg(f"  removing stale float ports: {[p.name for p in float_ports]}")
            self.remove_ports([p.name for p in float_ports], is_output=True)

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

        # 3. Rebuild fresh, signals suppressed
        with self._widget_core.suppress_signals():
            self._spin_count.blockSignals(True)
            self._spin_count.setValue(count)
            self._spin_count.blockSignals(False)
            self._set_count(count)

            # 4. Apply per-widget saved values
            for i_str, val in mf.get("values", {}).items():
                port_name = f"float_{int(i_str)}"
                if self._widget_core.has_binding(port_name):
                    self._widget_core.set_port_value(port_name, float(val))

        # 5. Re-sync visibility/palettes
        self._widget_core.refresh_widget_palettes()

        _dbg(f"MultiFloatOutputNode.restore_state: done, "
             f"count={self._current_count}, "
             f"outputs={[(p.name, p.datatype) for p in self.outputs]}")

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        _dbg("MultiFloatOutputNode.cleanup")
        # WidgetCore manages widget signal lifecycle automatically.
        # No manual disconnects needed.
        super().cleanup()