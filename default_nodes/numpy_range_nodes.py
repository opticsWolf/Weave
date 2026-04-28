# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QLabel, QSpinBox

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore, PortRole
from weave.node.node_enums import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("NumpyRangeNodes")

# ══════════════════════════════════════════════════════════════════════════════
# Constants & Helpers
# ══════════════════════════════════════════════════════════════════════════════

_GEN_MODES: Tuple[Tuple[str, str], ...] = (("Arange", "arange"), ("Linspace", "linspace"))
_DTYPE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("float64", "float64"), ("float32", "float32"),
    ("int64", "int64"), ("int32", "int32"), ("complex128", "complex128"),
)
_RESULT_MODES: Tuple[Tuple[str, str], ...] = (
    ("Meshgrid X", "meshgrid_x"), ("Meshgrid Y", "meshgrid_y"),
    ("Meshgrid Stack", "meshgrid_stack"), ("Outer Product", "outer"),
    ("Column Stack", "column_stack"),
)

def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep

def _make_float_spin(default: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(-1e9, 1e9)
    spin.setValue(float(default))
    return spin

def _make_step_spin(default: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(1e-9, 1e9)
    spin.setValue(float(default))
    return spin

def _make_int_spin(default: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(2, 1_000_000)
    spin.setValue(int(default))
    return spin


# ══════════════════════════════════════════════════════════════════════════════
# NumpyRange1DNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyRange1DNode(ThreadedNode):
    array_changed = Signal(object)

    node_class:        ClassVar[str]                 = "Numpy"
    node_subclass:     ClassVar[str]                 = "Generator"
    node_name:         ClassVar[Optional[str]]       = "Numpy Range 1D"
    node_description:  ClassVar[Optional[str]]       = "Generates 1D numpy arrays using arange or linspace."
    node_tags:         ClassVar[Optional[List[str]]] = ["numpy", "array", "1d", "range"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Numpy Range 1D", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        self.add_input("start", datatype="float")
        self.add_input("stop",  datatype="float")
        self.add_input("step",  datatype="float")
        self.add_input("num",   datatype="int")
        self.add_output("array", datatype="ndarray")

        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Mode & Dtype ──
        self._combo_mode = QComboBox()
        for label, value in _GEN_MODES:
            self._combo_mode.addItem(label, userData=value)
        form.addRow("Mode:", self._combo_mode)
        self._widget_core.register_widget("mode", self._combo_mode, role=PortRole.INTERNAL, datatype="str", default="arange", add_to_layout=False)

        self._combo_dtype = QComboBox()
        for label, value in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label, userData=value)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget("dtype", self._combo_dtype, role=PortRole.INTERNAL, datatype="str", default="float64", add_to_layout=False)

        form.addRow(_make_separator())

        # ── Parameters ──
        self._spin_start = _make_float_spin(0.0)
        self._widget_core.register_widget("start", self._spin_start, role=PortRole.BIDIRECTIONAL, datatype="float", default=0.0, add_to_layout=False)
        form.addRow("Start:", self._spin_start)

        self._spin_stop = _make_float_spin(10.0)
        self._widget_core.register_widget("stop", self._spin_stop, role=PortRole.BIDIRECTIONAL, datatype="float", default=10.0, add_to_layout=False)
        form.addRow("Stop:", self._spin_stop)

        self._spin_step = _make_step_spin(1.0)
        self._widget_core.register_widget("step", self._spin_step, role=PortRole.BIDIRECTIONAL, datatype="float", default=1.0, add_to_layout=False)
        self._label_step = QLabel("Step:")
        form.addRow(self._label_step, self._spin_step)

        self._spin_num = _make_int_spin(50)
        self._widget_core.register_widget("num", self._spin_num, role=PortRole.BIDIRECTIONAL, datatype="int", default=50, add_to_layout=False)
        self._label_num = QLabel("Num:")
        form.addRow(self._label_num, self._spin_num)

        # ── Signals & Initialization ──
        self._widget_core.value_changed.connect(self._on_core_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)
        
        self.set_content_widget(self._widget_core)
        self._sync_mode_visibility()

    def _sync_mode_visibility(self) -> None:
        is_arange = self._combo_mode.currentData() == "arange"
        
        self._label_step.setVisible(is_arange)
        self._spin_step.setVisible(is_arange)
        self._label_num.setVisible(not is_arange)
        self._spin_num.setVisible(not is_arange)
        
        if hasattr(self._widget_core, 'resume_content_notify'):
            self._widget_core.resume_content_notify(True)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        if port_name == "mode":
            self._sync_mode_visibility()
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, port_name: str, _value: Any) -> None:
        if port_name == "mode":
            self._sync_mode_visibility()

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_compute_cancelled():
            return {"array": np.empty(0)}

        mode = inputs.get("mode", "arange")
        dtype = np.dtype(inputs.get("dtype", "float64"))
        
        start = float(inputs.get("start", 0.0))
        stop  = float(inputs.get("stop", 10.0))

        if mode == "arange":
            step = float(inputs.get("step", 1.0))
            if step == 0.0: step = 1.0
            arr = np.arange(start, stop, step, dtype=dtype)
        else:
            num = max(2, int(inputs.get("num", 50)))
            arr = np.linspace(start, stop, num, dtype=dtype)

        return {"array": arr}

    def on_evaluate_finished(self) -> None:
        result = self._get_cached_value("array")
        if result is not None:
            self.array_changed.emit(result)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        self.cancel_compute()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# NumpyRange2DNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyRange2DNode(ThreadedNode):
    array_changed = Signal(object)

    node_class:        ClassVar[str]                 = "Numpy"
    node_subclass:     ClassVar[str]                 = "Generator"
    node_name:         ClassVar[Optional[str]]       = "Numpy Range 2D"
    node_description:  ClassVar[Optional[str]]       = "Generates 2D numpy arrays via meshgrid, outer product, or column stack."
    node_tags:         ClassVar[Optional[List[str]]] = ["numpy", "array", "2d", "range"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Numpy Range 2D", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        self.add_input("start_0", datatype="float")
        self.add_input("stop_0",  datatype="float")
        self.add_input("step_0",  datatype="float")
        self.add_input("num_0",   datatype="int")

        self.add_input("start_1", datatype="float")
        self.add_input("stop_1",  datatype="float")
        self.add_input("step_1",  datatype="float")
        self.add_input("num_1",   datatype="int")
        self.add_output("array", datatype="ndarray")

        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Mode & Dtype & Result ──
        self._combo_mode = QComboBox()
        for label, value in _GEN_MODES:
            self._combo_mode.addItem(label, userData=value)
        form.addRow("Mode:", self._combo_mode)
        self._widget_core.register_widget("mode", self._combo_mode, role=PortRole.INTERNAL, datatype="str", default="arange", add_to_layout=False)

        self._combo_result = QComboBox()
        for label, value in _RESULT_MODES:
            self._combo_result.addItem(label, userData=value)
        form.addRow("Result:", self._combo_result)
        self._widget_core.register_widget("result_mode", self._combo_result, role=PortRole.INTERNAL, datatype="str", default="meshgrid_x", add_to_layout=False)

        self._combo_dtype = QComboBox()
        for label, value in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label, userData=value)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget("dtype", self._combo_dtype, role=PortRole.INTERNAL, datatype="str", default="float64", add_to_layout=False)

        # ── Axis 0 ──
        form.addRow(_make_separator())
        form.addRow(QLabel("── Axis 0 ──"))
        
        self._spin_start_0 = _make_float_spin(0.0)
        self._widget_core.register_widget("start_0", self._spin_start_0, role=PortRole.BIDIRECTIONAL, datatype="float", default=0.0, add_to_layout=False)
        form.addRow("Start 0:", self._spin_start_0)

        self._spin_stop_0 = _make_float_spin(10.0)
        self._widget_core.register_widget("stop_0", self._spin_stop_0, role=PortRole.BIDIRECTIONAL, datatype="float", default=10.0, add_to_layout=False)
        form.addRow("Stop 0:", self._spin_stop_0)

        self._spin_step_0 = _make_step_spin(1.0)
        self._widget_core.register_widget("step_0", self._spin_step_0, role=PortRole.BIDIRECTIONAL, datatype="float", default=1.0, add_to_layout=False)
        self._label_step_0 = QLabel("Step 0:")
        form.addRow(self._label_step_0, self._spin_step_0)

        self._spin_num_0 = _make_int_spin(50)
        self._widget_core.register_widget("num_0", self._spin_num_0, role=PortRole.BIDIRECTIONAL, datatype="int", default=50, add_to_layout=False)
        self._label_num_0 = QLabel("Num 0:")
        form.addRow(self._label_num_0, self._spin_num_0)

        # ── Axis 1 ──
        form.addRow(_make_separator())
        form.addRow(QLabel("── Axis 1 ──"))
        
        self._spin_start_1 = _make_float_spin(0.0)
        self._widget_core.register_widget("start_1", self._spin_start_1, role=PortRole.BIDIRECTIONAL, datatype="float", default=0.0, add_to_layout=False)
        form.addRow("Start 1:", self._spin_start_1)

        self._spin_stop_1 = _make_float_spin(10.0)
        self._widget_core.register_widget("stop_1", self._spin_stop_1, role=PortRole.BIDIRECTIONAL, datatype="float", default=10.0, add_to_layout=False)
        form.addRow("Stop 1:", self._spin_stop_1)

        self._spin_step_1 = _make_step_spin(1.0)
        self._widget_core.register_widget("step_1", self._spin_step_1, role=PortRole.BIDIRECTIONAL, datatype="float", default=1.0, add_to_layout=False)
        self._label_step_1 = QLabel("Step 1:")
        form.addRow(self._label_step_1, self._spin_step_1)

        self._spin_num_1 = _make_int_spin(50)
        self._widget_core.register_widget("num_1", self._spin_num_1, role=PortRole.BIDIRECTIONAL, datatype="int", default=50, add_to_layout=False)
        self._label_num_1 = QLabel("Num 1:")
        form.addRow(self._label_num_1, self._spin_num_1)

        # ── Signals & Initialization ──
        self._widget_core.value_changed.connect(self._on_core_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)
        
        self.set_content_widget(self._widget_core)
        self._sync_mode_visibility()

    def _sync_mode_visibility(self) -> None:
        is_arange = self._combo_mode.currentData() == "arange"
        
        for label, spin in ((self._label_step_0, self._spin_step_0), (self._label_step_1, self._spin_step_1)):
            label.setVisible(is_arange)
            spin.setVisible(is_arange)
            
        for label, spin in ((self._label_num_0, self._spin_num_0), (self._label_num_1, self._spin_num_1)):
            label.setVisible(not is_arange)
            spin.setVisible(not is_arange)

        if hasattr(self._widget_core, 'resume_content_notify'):
            self._widget_core.resume_content_notify(True)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        if port_name == "mode":
            self._sync_mode_visibility()
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, port_name: str, _value: Any) -> None:
        if port_name == "mode":
            self._sync_mode_visibility()

    def _build_1d(self, inputs: Dict[str, Any], axis: int, mode: str, dtype: "np.dtype") -> np.ndarray:
        sfx = f"_{axis}"
        start = float(inputs.get(f"start{sfx}", 0.0))
        stop  = float(inputs.get(f"stop{sfx}", 10.0))

        if mode == "arange":
            step = float(inputs.get(f"step{sfx}", 1.0))
            if step == 0.0: step = 1.0
            return np.arange(start, stop, step, dtype=dtype)
        else:
            num = max(2, int(inputs.get(f"num{sfx}", 50)))
            return np.linspace(start, stop, num, dtype=dtype)

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_compute_cancelled():
            return {"array": np.empty(0)}

        dtype       = np.dtype(inputs.get("dtype", "float64"))
        mode        = inputs.get("mode", "arange")
        result_mode = inputs.get("result_mode", "meshgrid_x")

        a0 = self._build_1d(inputs, 0, mode, dtype)
        a1 = self._build_1d(inputs, 1, mode, dtype)

        if result_mode in ("meshgrid_x", "meshgrid_y", "meshgrid_stack"):
            X, Y = np.meshgrid(a0, a1, indexing="ij")
            if result_mode == "meshgrid_x": arr = X
            elif result_mode == "meshgrid_y": arr = Y
            else: arr = np.stack([X, Y])
        elif result_mode == "outer":
            arr = np.outer(a0, a1)
        elif result_mode == "column_stack":
            n = min(len(a0), len(a1))
            arr = np.column_stack([a0[:n], a1[:n]])
        else:
            arr = np.outer(a0, a1)

        return {"array": arr}

    def on_evaluate_finished(self) -> None:
        result = self._get_cached_value("array")
        if result is not None:
            self.array_changed.emit(result)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        self.cancel_compute()
        super().cleanup()
