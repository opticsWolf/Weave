# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_range_nodes.py
--------------------
NumPy range-based array generator nodes with full per-parameter
auto-disable input ports.

Provided nodes
--------------
``NumpyRange1DNode``
    Generates a 1-D ``numpy.ndarray`` from a configurable range spec.
    Supports two generation modes selected by a *Mode* combo:

    * **Arange** — ``numpy.arange(start, stop, step)``
    * **Linspace** — ``numpy.linspace(start, stop, num)``

    Each numeric parameter (start, stop, step / num) has both a
    bidirectional spinbox *and* an auto-disable input port so that
    upstream nodes can drive individual parameters while the spinbox
    reflects and falls back to the connected value.

``NumpyRange2DNode``
    Generates a 2-D ``numpy.ndarray`` from two independent 1-D range
    specs (Axis 0 and Axis 1), each with their own bidirectional
    spinboxes and auto-disable input ports (``start_0`` … ``step_0``,
    ``start_1`` … ``step_1``).

    A *Result* combo selects how the two 1-D arrays are combined:

    ===================== ============================================
    Meshgrid X            First output of ``numpy.meshgrid(a, b)``
                          shape ``(len_b, len_a)`` — each row is *a*.
    Meshgrid Y            Second output — each column is *b*.
    Meshgrid Stack        ``numpy.stack([X, Y])`` shape ``(2, M, N)``
    Outer Product         ``numpy.outer(a, b)`` shape ``(len_a, len_b)``
    Column Stack          ``numpy.column_stack([a_trim, b_trim])``
                          rows are pairs; arrays are broadcast to the
                          same length by truncation.
    ===================== ============================================

    Both axes share the same *Mode* (Arange / Linspace) and *Dtype*.

Design notes
------------
* All spinboxes are registered with ``role="bidirectional"`` so that
  ``WidgetCore`` serialises their values automatically and exposes the
  ``set_port_enabled`` auto-disable path wired by the port's
  ``_auto_disable = True`` flag.
* ``add_to_layout=False`` is used throughout because each spinbox is
  placed manually into the ``QFormLayout`` first (so the label appears
  on the same row), then registered with WidgetCore.
* The *Step* row is hidden and the *Num* row is shown when
  Mode = Linspace, and vice versa for Arange.  Visibility is toggled
  in ``_sync_mode_visibility()`` which is called from the mode
  combo's ``currentIndexChanged`` slot *before* ``on_ui_change()``
  so the correct parameters are read in the next ``compute()``.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QSpinBox,
)

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import PortRole, WidgetCore

from weave.logger import get_logger

log = get_logger("NumpyRangeNodes")


# ── Shared constants ──────────────────────────────────────────────────────────

_GEN_MODES: Tuple[Tuple[str, str], ...] = (
    ("Arange",   "arange"),
    ("Linspace", "linspace"),
)

_DTYPE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("float64",    "float64"),
    ("float32",    "float32"),
    ("int64",      "int64"),
    ("int32",      "int32"),
    ("complex128", "complex128"),
)

_RESULT_MODES: Tuple[Tuple[str, str], ...] = (
    ("Meshgrid X",     "meshgrid_x"),
    ("Meshgrid Y",     "meshgrid_y"),
    ("Meshgrid Stack", "meshgrid_stack"),
    ("Outer Product",  "outer"),
    ("Column Stack",   "column_stack"),
)


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# ══════════════════════════════════════════════════════════════════════════════
# NumpyRange1DNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyRange1DNode(ThreadedNode):
    """
    Generates a 1-D NumPy array from a configurable range specification.

    Type: Threaded (compute() runs on QThreadPool; propagates downstream
    on any widget or port change).

    Inputs  (all auto-disable their matching spinbox on connection)
    ------
    start : float
        Range start value.
    stop : float
        Range stop value.
    step : float  [Arange mode only]
        Step between elements.
    num : int  [Linspace mode only]
        Number of evenly-spaced samples.

    Outputs
    -------
    array : ndarray
        The generated 1-D array.

    Parameters
    ----------
    title : str
        Node title (default ``"Numpy Range 1D"``).
    """

    array_changed = Signal(object)  # emits ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Generator"
    node_name:        ClassVar[Optional[str]] = "Numpy Range 1D"
    node_description: ClassVar[Optional[str]] = (
        "Generates a 1-D NumPy array using arange or linspace"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "array", "1d", "range", "arange", "linspace",
        "generator", "primitive",
    ]

    def __init__(self, title: str = "Numpy Range 1D", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input ports (auto-disable matching spinboxes) ─────────────
        self.add_input("start", "float"); self.inputs[-1]._auto_disable = True
        self.add_input("stop",  "float"); self.inputs[-1]._auto_disable = True
        self.add_input("step",  "float"); self.inputs[-1]._auto_disable = True
        self.add_input("num",   "int");   self.inputs[-1]._auto_disable = True

        self.add_output("array", "ndarray")

        # ── Shared form layout ────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Mode combo (internal — serialised by WidgetCore) ──────────
        self._combo_mode = QComboBox()
        for label, _ in _GEN_MODES:
            self._combo_mode.addItem(label)
        form.addRow("Mode:", self._combo_mode)
        self._widget_core.register_widget(
            "mode", self._combo_mode,
            role="internal", datatype="string", default="arange",
            add_to_layout=False,
        )
        # Visibility sync fires before on_ui_change (which arrives via
        # the WidgetCore → value_changed → _on_core_changed path)
        self._combo_mode.currentIndexChanged.connect(self._on_mode_changed)

        # ── Dtype combo (internal — serialised by WidgetCore) ─────────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role="internal", datatype="string", default="float64",
            add_to_layout=False,
        )

        form.addRow(_make_separator())

        # ── Start (bidirectional) ─────────────────────────────────────
        self._spin_start = self._make_float_spin(0.0)
        form.addRow("Start:", self._spin_start)
        self._widget_core.register_widget(
            "start", self._spin_start,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )

        # ── Stop (bidirectional) ──────────────────────────────────────
        self._spin_stop = self._make_float_spin(10.0)
        form.addRow("Stop:", self._spin_stop)
        self._widget_core.register_widget(
            "stop", self._spin_stop,
            role="bidirectional", datatype="float", default=10.0,
            add_to_layout=False,
        )

        # ── Step (bidirectional — Arange only) ────────────────────────
        self._label_step = QLabel("Step:")
        self._spin_step  = self._make_step_spin(1.0)
        form.addRow(self._label_step, self._spin_step)
        self._widget_core.register_widget(
            "step", self._spin_step,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        # ── Num (bidirectional — Linspace only) ───────────────────────
        self._label_num = QLabel("Num:")
        self._spin_num  = self._make_int_spin(50)
        form.addRow(self._label_num, self._spin_num)
        self._widget_core.register_widget(
            "num", self._spin_num,
            role="bidirectional", datatype="int", default=50,
            add_to_layout=False,
        )

        # Single WidgetCore connection covers all bidirectional + internal widgets
        self._widget_core.value_changed.connect(self._on_core_changed)

        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._sync_mode_visibility()
        self._widget_core.refresh_widget_palettes()

    # ── Widget factory helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_float_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1e9, 1e9)
        spin.setValue(default)
        spin.setDecimals(4)
        spin.setMinimumWidth(100)
        return spin

    @staticmethod
    def _make_step_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(1e-9, 1e9)
        spin.setValue(default)
        spin.setDecimals(4)
        spin.setMinimumWidth(100)
        return spin

    @staticmethod
    def _make_int_spin(default: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(2, 1_000_000)
        spin.setValue(default)
        spin.setMinimumWidth(100)
        return spin

    # ── Widget snapshot (main thread → worker thread) ─────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture all combo and spinbox values before worker dispatch.

        Spinbox values come from ``get_all_values()``.  Combos are read
        by index and mapped through their options tuples so that the value
        string (e.g. ``"arange"``) is stored rather than the display label
        (e.g. ``"Arange"``).
        """
        snap = {f"_ui_{k}": v for k, v in self._widget_core.get_all_values().items()}

        idx = self._combo_mode.currentIndex()
        snap["_ui_mode"] = (
            _GEN_MODES[idx][1] if 0 <= idx < len(_GEN_MODES) else "arange"
        )
        idx = self._combo_dtype.currentIndex()
        snap["_ui_dtype"] = (
            _DTYPE_OPTIONS[idx][1] if 0 <= idx < len(_DTYPE_OPTIONS) else "float64"
        )
        return snap

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_mode(self) -> str:
        idx = self._combo_mode.currentIndex()
        return _GEN_MODES[idx][1] if 0 <= idx < len(_GEN_MODES) else "arange"

    def _get_dtype(self) -> "np.dtype":
        idx = self._combo_dtype.currentIndex()
        key = _DTYPE_OPTIONS[idx][1] if 0 <= idx < len(_DTYPE_OPTIONS) else "float64"
        return np.dtype(key)

    def _sync_mode_visibility(self) -> None:
        """Show Step row for Arange, Num row for Linspace."""
        is_arange = self._get_mode() == "arange"
        self._label_step.setVisible(is_arange)
        self._spin_step.setVisible(is_arange)
        self._label_num.setVisible(not is_arange)
        self._spin_num.setVisible(not is_arange)

    def _build_1d(self, inputs: Dict[str, Any], mode: str, dtype: "np.dtype") -> np.ndarray:
        """
        Build the 1-D array from port inputs with spinbox fallback.

        Upstream port values are preferred; snapshotted widget values
        (under ``_ui_`` keys) are the fallback — no Qt widget access.
        """
        def _f(name: str, default: float) -> float:
            v = inputs.get(name)
            if v is not None:
                return float(v)
            raw = inputs.get(f"_ui_{name}")
            return float(raw) if raw is not None else default

        def _i(name: str, default: int) -> int:
            v = inputs.get(name)
            if v is not None:
                return max(2, int(v))
            raw = inputs.get(f"_ui_{name}")
            return int(raw) if raw is not None else default

        if mode == "arange":
            start = _f("start", 0.0)
            stop  = _f("stop",  10.0)
            step  = _f("step",  1.0)
            if step == 0.0:
                step = 1.0
            return np.arange(start, stop, step, dtype=dtype)
        else:
            start = _f("start", 0.0)
            stop  = _f("stop",  10.0)
            num   = _i("num",   50)
            return np.linspace(start, stop, num, dtype=dtype)

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(int)
    def _on_mode_changed(self, _index: int) -> None:
        """Sync row visibility; on_ui_change arrives via WidgetCore path."""
        try:
            self._sync_mode_visibility()
        except Exception as exc:
            log.error(f"Exception in NumpyRange1DNode._on_mode_changed: {exc}")

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            # When mode changes via the panel mirror, set_port_value
            # blocks the combo's own signals so currentIndexChanged
            # (→ _on_mode_changed) never fires.  Catch that case here.
            if port_name == "mode":
                self._sync_mode_visibility()
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in NumpyRange1DNode._on_core_changed: {exc}")

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Delegates to ``_build_1d`` and returns the result.

        Runs on the worker thread — no Qt widget access.
        Mode and dtype arrive pre-snapshotted under ``_ui_`` keys.
        """
        try:
            mode  = inputs.get("_ui_mode",  "arange")
            dtype = np.dtype(inputs.get("_ui_dtype", "float64"))
            arr   = self._build_1d(inputs, mode, dtype)
            return {"array": arr}
        except Exception as exc:
            log.error(f"Exception in NumpyRange1DNode.compute: {exc}")
            return {"array": np.array([], dtype=np.float64)}

    # ── Post-compute UI update (main thread) ──────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Emit array_changed on the main thread after results are cached."""
        try:
            result = self.get_output_value("array")
            if result is not None:
                self.array_changed.emit(result)
        except Exception as exc:
            log.error(f"Exception in NumpyRange1DNode.on_evaluate_finished: {exc}")
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# NumpyRange2DNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyRange2DNode(ThreadedNode):
    """
    Generates a 2-D NumPy array by combining two independent 1-D range
    specifications (Axis 0 and Axis 1).

    Type: Threaded (compute() runs on QThreadPool; propagates downstream
    on any widget or port change).

    Inputs  (all auto-disable their matching spinbox on connection)
    ------
    start_0, stop_0, step_0 : float  [Arange mode — Axis 0]
    num_0 : int                       [Linspace mode — Axis 0]
    start_1, stop_1, step_1 : float  [Arange mode — Axis 1]
    num_1 : int                       [Linspace mode — Axis 1]

    Outputs
    -------
    array : ndarray
        The 2-D result array whose shape and contents depend on the
        selected *Result* mode.

    Parameters
    ----------
    title : str
        Node title (default ``"Numpy Range 2D"``).
    """

    array_changed = Signal(object)  # emits ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Generator"
    node_name:        ClassVar[Optional[str]] = "Numpy Range 2D"
    node_description: ClassVar[Optional[str]] = (
        "Generates a 2-D NumPy array from two independent range specs"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "array", "2d", "range", "meshgrid", "outer",
        "generator", "primitive",
    ]

    def __init__(self, title: str = "Numpy Range 2D", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input ports — Axis 0 ─────────────────────────────────────
        self.add_input("start_0", "float"); self.inputs[-1]._auto_disable = True
        self.add_input("stop_0",  "float"); self.inputs[-1]._auto_disable = True
        self.add_input("step_0",  "float"); self.inputs[-1]._auto_disable = True
        self.add_input("num_0",   "int");   self.inputs[-1]._auto_disable = True

        # ── Input ports — Axis 1 ─────────────────────────────────────
        self.add_input("start_1", "float"); self.inputs[-1]._auto_disable = True
        self.add_input("stop_1",  "float"); self.inputs[-1]._auto_disable = True
        self.add_input("step_1",  "float"); self.inputs[-1]._auto_disable = True
        self.add_input("num_1",   "int");   self.inputs[-1]._auto_disable = True

        self.add_output("array", "ndarray")

        # ── Shared form layout ────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Mode combo (internal) ─────────────────────────────────────
        self._combo_mode = QComboBox()
        for label, _ in _GEN_MODES:
            self._combo_mode.addItem(label)
        form.addRow("Mode:", self._combo_mode)
        self._widget_core.register_widget(
            "mode", self._combo_mode,
            role="internal", datatype="string", default="arange",
            add_to_layout=False,
        )
        self._combo_mode.currentIndexChanged.connect(self._on_mode_changed)

        # ── Result mode combo (internal) ──────────────────────────────
        self._combo_result = QComboBox()
        for label, _ in _RESULT_MODES:
            self._combo_result.addItem(label)
        form.addRow("Result:", self._combo_result)
        self._widget_core.register_widget(
            "result_mode", self._combo_result,
            role="internal", datatype="string", default="meshgrid_x",
            add_to_layout=False,
        )

        # ── Dtype combo (internal) ────────────────────────────────────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role="internal", datatype="string", default="float64",
            add_to_layout=False,
        )

        # ── Axis 0 parameters ─────────────────────────────────────────
        form.addRow(_make_separator())
        form.addRow(QLabel("── Axis 0 ──"))

        self._spin_start_0 = self._make_float_spin(0.0)
        form.addRow("Start 0:", self._spin_start_0)
        self._widget_core.register_widget(
            "start_0", self._spin_start_0,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )

        self._spin_stop_0 = self._make_float_spin(10.0)
        form.addRow("Stop 0:", self._spin_stop_0)
        self._widget_core.register_widget(
            "stop_0", self._spin_stop_0,
            role="bidirectional", datatype="float", default=10.0,
            add_to_layout=False,
        )

        self._label_step_0 = QLabel("Step 0:")
        self._spin_step_0  = self._make_step_spin(1.0)
        form.addRow(self._label_step_0, self._spin_step_0)
        self._widget_core.register_widget(
            "step_0", self._spin_step_0,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        self._label_num_0 = QLabel("Num 0:")
        self._spin_num_0  = self._make_int_spin(50)
        form.addRow(self._label_num_0, self._spin_num_0)
        self._widget_core.register_widget(
            "num_0", self._spin_num_0,
            role="bidirectional", datatype="int", default=50,
            add_to_layout=False,
        )

        # ── Axis 1 parameters ─────────────────────────────────────────
        form.addRow(_make_separator())
        form.addRow(QLabel("── Axis 1 ──"))

        self._spin_start_1 = self._make_float_spin(0.0)
        form.addRow("Start 1:", self._spin_start_1)
        self._widget_core.register_widget(
            "start_1", self._spin_start_1,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )

        self._spin_stop_1 = self._make_float_spin(10.0)
        form.addRow("Stop 1:", self._spin_stop_1)
        self._widget_core.register_widget(
            "stop_1", self._spin_stop_1,
            role="bidirectional", datatype="float", default=10.0,
            add_to_layout=False,
        )

        self._label_step_1 = QLabel("Step 1:")
        self._spin_step_1  = self._make_step_spin(1.0)
        form.addRow(self._label_step_1, self._spin_step_1)
        self._widget_core.register_widget(
            "step_1", self._spin_step_1,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        self._label_num_1 = QLabel("Num 1:")
        self._spin_num_1  = self._make_int_spin(50)
        form.addRow(self._label_num_1, self._spin_num_1)
        self._widget_core.register_widget(
            "num_1", self._spin_num_1,
            role="bidirectional", datatype="int", default=50,
            add_to_layout=False,
        )

        # Single WidgetCore connection covers everything
        self._widget_core.value_changed.connect(self._on_core_changed)

        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._sync_mode_visibility()
        self._widget_core.refresh_widget_palettes()

    # ── Widget factory helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_float_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1e9, 1e9)
        spin.setValue(default)
        spin.setDecimals(4)
        spin.setMinimumWidth(100)
        return spin

    @staticmethod
    def _make_step_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(1e-9, 1e9)
        spin.setValue(default)
        spin.setDecimals(4)
        spin.setMinimumWidth(100)
        return spin

    @staticmethod
    def _make_int_spin(default: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(2, 1_000_000)
        spin.setValue(default)
        spin.setMinimumWidth(100)
        return spin

    # ── Widget snapshot (main thread → worker thread) ─────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture all combo and spinbox values before worker dispatch.

        Spinbox values come from ``get_all_values()``.  Combos are read
        by index and mapped through their options tuples so that the value
        string (e.g. ``"arange"``, ``"meshgrid_x"``) is stored rather than
        the display label (e.g. ``"Arange"``, ``"Meshgrid X"``).
        """
        snap = {f"_ui_{k}": v for k, v in self._widget_core.get_all_values().items()}

        idx = self._combo_mode.currentIndex()
        snap["_ui_mode"] = (
            _GEN_MODES[idx][1] if 0 <= idx < len(_GEN_MODES) else "arange"
        )
        idx = self._combo_dtype.currentIndex()
        snap["_ui_dtype"] = (
            _DTYPE_OPTIONS[idx][1] if 0 <= idx < len(_DTYPE_OPTIONS) else "float64"
        )
        idx = self._combo_result.currentIndex()
        snap["_ui_result_mode"] = (
            _RESULT_MODES[idx][1] if 0 <= idx < len(_RESULT_MODES) else "meshgrid_x"
        )
        return snap

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_mode(self) -> str:
        """Read current mode from combo (main-thread only — used by _sync_mode_visibility)."""
        idx = self._combo_mode.currentIndex()
        return _GEN_MODES[idx][1] if 0 <= idx < len(_GEN_MODES) else "arange"

    def _get_result_mode(self) -> str:
        idx = self._combo_result.currentIndex()
        return _RESULT_MODES[idx][1] if 0 <= idx < len(_RESULT_MODES) else "meshgrid_x"

    def _get_dtype(self) -> "np.dtype":
        idx = self._combo_dtype.currentIndex()
        key = _DTYPE_OPTIONS[idx][1] if 0 <= idx < len(_DTYPE_OPTIONS) else "float64"
        return np.dtype(key)

    def _sync_mode_visibility(self) -> None:
        """Toggle Step vs Num row visibility for both axes."""
        is_arange = self._get_mode() == "arange"
        for label, spin in (
            (self._label_step_0, self._spin_step_0),
            (self._label_step_1, self._spin_step_1),
        ):
            label.setVisible(is_arange)
            spin.setVisible(is_arange)
        for label, spin in (
            (self._label_num_0, self._spin_num_0),
            (self._label_num_1, self._spin_num_1),
        ):
            label.setVisible(not is_arange)
            spin.setVisible(not is_arange)

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(int)
    def _on_mode_changed(self, _index: int) -> None:
        try:
            self._sync_mode_visibility()
        except Exception as exc:
            log.error(f"Exception in NumpyRange2DNode._on_mode_changed: {exc}")

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            # When mode changes via the panel mirror, set_port_value
            # blocks the combo's own signals so currentIndexChanged
            # (→ _on_mode_changed) never fires.  Catch that case here.
            if port_name == "mode":
                self._sync_mode_visibility()
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in NumpyRange2DNode._on_core_changed: {exc}")

    # ── Computation ───────────────────────────────────────────────────────────

    def _build_1d(
        self,
        inputs: Dict[str, Any],
        axis: int,
        mode: str,
        dtype: "np.dtype",
    ) -> np.ndarray:
        """
        Build one 1-D array from the parameters for *axis* (0 or 1).

        Upstream port values are preferred; snapshotted widget values
        (under ``_ui_`` keys) are the fallback — no Qt widget access.
        """
        sfx = f"_{axis}"

        def _f(name: str, default: float) -> float:
            v = inputs.get(name + sfx)
            if v is not None:
                return float(v)
            raw = inputs.get(f"_ui_{name}{sfx}")
            return float(raw) if raw is not None else default

        def _i(name: str, default: int) -> int:
            v = inputs.get(name + sfx)
            if v is not None:
                return max(2, int(v))
            raw = inputs.get(f"_ui_{name}{sfx}")
            return int(raw) if raw is not None else default

        if mode == "arange":
            start = _f("start", 0.0)
            stop  = _f("stop",  10.0)
            step  = _f("step",  1.0)
            if step == 0.0:
                step = 1.0
            return np.arange(start, stop, step, dtype=dtype)
        else:
            start = _f("start", 0.0)
            stop  = _f("stop",  10.0)
            num   = _i("num",   50)
            return np.linspace(start, stop, num, dtype=dtype)

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Combine the two 1-D arrays into a 2-D result.

        Runs on the worker thread — no Qt widget access.
        Mode, result mode, and dtype arrive pre-snapshotted under ``_ui_`` keys.
        """
        try:
            dtype       = np.dtype(inputs.get("_ui_dtype",       "float64"))
            mode        = inputs.get("_ui_mode",        "arange")
            result_mode = inputs.get("_ui_result_mode", "meshgrid_x")

            a0 = self._build_1d(inputs, 0, mode, dtype)
            a1 = self._build_1d(inputs, 1, mode, dtype)

            if result_mode in ("meshgrid_x", "meshgrid_y", "meshgrid_stack"):
                X, Y = np.meshgrid(a0, a1, indexing="ij")
                if result_mode == "meshgrid_x":
                    arr = X
                elif result_mode == "meshgrid_y":
                    arr = Y
                else:
                    arr = np.stack([X, Y])

            elif result_mode == "outer":
                arr = np.outer(a0, a1)

            elif result_mode == "column_stack":
                n   = min(len(a0), len(a1))
                arr = np.column_stack([a0[:n], a1[:n]])

            else:
                arr = np.outer(a0, a1)

            return {"array": arr}

        except Exception as exc:
            log.error(f"Exception in NumpyRange2DNode.compute: {exc}")
            return {"array": np.empty((0, 0), dtype=np.float64)}

    # ── Post-compute UI update (main thread) ──────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Emit array_changed on the main thread after results are cached."""
        try:
            result = self.get_output_value("array")
            if result is not None:
                self.array_changed.emit(result)
        except Exception as exc:
            log.error(f"Exception in NumpyRange2DNode.on_evaluate_finished: {exc}")
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._widget_core.cleanup()
        super().cleanup()