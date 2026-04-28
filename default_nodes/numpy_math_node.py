# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_math_node.py
------------------
Element-wise and linear-algebra binary math node for NumPy arrays.
Refactored to strictly adhere to Weave Framework Custom Node Implementation Guide.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

# 1. Import Signal from PySide6.QtCore
from PySide6.QtCore import Slot, Signal 
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
)

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore
from weave.widgetcore.widgetcore_port_models import PortRole
from weave.node.node_enums import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("NumpyMathNode")


# ── Operation registry (unchanged) ────────────────────────────────────────────
_OP_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("Arithmetic",    "arithmetic"),
    ("Comparison",    "comparison"),
    ("Bitwise",       "bitwise"),
    ("Logical",       "logical"),
    ("Element Math",  "element_math"),
    ("Linear Algebra","linear_algebra"),
)

_OPS_BY_CAT: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "arithmetic": (
        ("Add  (A + B)",          "add"),
        ("Subtract  (A − B)",     "subtract"),
        ("Multiply  (A × B)",     "multiply"),
        ("True Divide  (A / B)",  "true_divide"),
        ("Floor Divide  (A // B)","floor_divide"),
        ("Modulo  (A % B)",       "mod"),
        ("Power  (A ** B)",       "power"),
    ),
    "comparison": (
        ("Equal  (A == B)",       "equal"),
        ("Not Equal  (A != B)",   "not_equal"),
        ("Less  (A < B)",         "less"),
        ("Less or Equal",         "less_equal"),
        ("Greater  (A > B)",      "greater"),
        ("Greater or Equal",      "greater_equal"),
    ),
    "bitwise": (
        ("Bitwise AND",           "bitwise_and"),
        ("Bitwise OR",            "bitwise_or"),
        ("Bitwise XOR",           "bitwise_xor"),
        ("Left Shift  (A << B)",  "left_shift"),
        ("Right Shift  (A >> B)", "right_shift"),
    ),
    "logical": (
        ("Logical AND",           "logical_and"),
        ("Logical OR",            "logical_or"),
        ("Logical XOR",           "logical_xor"),
    ),
    "element_math": (
        ("Minimum  (elem-wise)",  "minimum"),
        ("Maximum  (elem-wise)",  "maximum"),
        ("Arctan2  (y=A, x=B)",   "arctan2"),
        ("Hypot  (√(A²+B²))",     "hypot"),
        ("Copysign  (|A|·sgn B)", "copysign"),
        ("Fmod  (C fmod)",        "fmod"),
        ("GCD  (integer)",        "gcd"),
        ("LCM  (integer)",        "lcm"),
    ),
    "linear_algebra": (
        ("Dot Product",           "dot"),
        ("Matrix Multiply  (A@B)","matmul"),
        ("Outer Product",         "outer"),
        ("Cross Product",         "cross"),
        ("Tensordot  (axes=1)",   "tensordot"),
    ),
}

def _build_flat_ops() -> Tuple[Tuple[str, Optional[str]], ...]:
    flat: List[Tuple[str, Optional[str]]] = []
    for cat_label, cat_key in _OP_CATEGORIES:
        flat.append((f"── {cat_label} ──", None))
        for op_label, op_key in _OPS_BY_CAT[cat_key]:
            flat.append((op_label, op_key))
    return tuple(flat)

_OP_FLAT: Tuple[Tuple[str, Optional[str]], ...] = _build_flat_ops()

_OP_INDEX_TO_VALUE: Dict[int, str] = {
    i: op_key for i, (_, op_key) in enumerate(_OP_FLAT) if op_key is not None
}
_OP_DEFAULT_INDEX: int = next(iter(_OP_INDEX_TO_VALUE))
_OP_DEFAULT_VALUE: str = _OP_FLAT[_OP_DEFAULT_INDEX][1]

_DTYPE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("Auto",       "auto"),
    ("float64",    "float64"),
    ("float32",    "float32"),
    ("int64",      "int64"),
    ("int32",      "int32"),
    ("complex128", "complex128"),
    ("bool",       "bool"),
)

def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# ══════════════════════════════════════════════════════════════════════════════
# NumpyMathNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyMathNode(ThreadedNode):
    """Binary math node for NumPy arrays. Threaded execution per Weave §4."""

    # 2. Define the Signal here so it exists on the instance
    result_changed = Signal(object) 

    # ── Registry Metadata (§1) ────────────────────────────────────────
    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Math"
    node_name:        ClassVar[Optional[str]] = "Numpy Math"
    node_description: ClassVar[Optional[str]] = (
        "Element-wise and linear-algebra binary math on two NumPy arrays"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "math", "arithmetic", "operator", "binary",
        "add", "multiply", "dot", "matmul", "array", "ndarray",
    ]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Numpy Math", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input / output ports (§3.1) ───────────────────────────────
        self.add_input("A", "ndarray")
        self.add_input("B", "ndarray")
        self.inputs[-1]._auto_disable = True   # B disables scalar spinbox
        self.add_output("result", "ndarray")

        # ── Form layout & WidgetCore (§3.3) ───────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Operation combo (§6.1) ────────────────────────────────────
        self._combo_op = QComboBox()
        self._combo_op.setMinimumWidth(180)
        self._populate_op_combo(self._combo_op)
        form.addRow("Op:", self._combo_op)
        self._widget_core.register_widget(
            "op", self._combo_op,
            role=PortRole.INTERNAL, datatype="string", default=_OP_DEFAULT_VALUE,
            add_to_layout=False,
        )

        # ── Dtype combo ───────────────────────────────────────────────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Out Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role=PortRole.INTERNAL, datatype="string", default="auto",
            add_to_layout=False,
        )

        form.addRow(_make_separator())

        # ── Scalar B fallback (§3.2) ──────────────────────────────────
        self._label_scalar_b = QLabel("Scalar B:")
        self._spin_scalar_b = QDoubleSpinBox()
        self._spin_scalar_b.setRange(-1e12, 1e12)
        self._spin_scalar_b.setValue(1.0)
        self._spin_scalar_b.setDecimals(6)
        self._spin_scalar_b.setMinimumWidth(130)
        form.addRow(self._label_scalar_b, self._spin_scalar_b)
        self._widget_core.register_widget(
            "B", self._spin_scalar_b,
            role=PortRole.BIDIRECTIONAL, datatype="float", default=1.0,
            add_to_layout=False,
        )

        # ── Status display (§3.2) ─────────────────────────────────────
        form.addRow(_make_separator())
        self._label_status = QLabel("--")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        self._label_status.setMinimumWidth(160)
        form.addRow(self._label_status)

        # ── Wire UI to evaluation loop (§3.3, §5) ─────────────────────
        self._widget_core.value_changed.connect(self.on_ui_change)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._pending_status: Optional[str] = None

    @staticmethod
    def _populate_op_combo(combo: QComboBox) -> None:
        from PySide6.QtCore import Qt
        for label, op_key in _OP_FLAT:
            combo.addItem(label)
            if op_key is None:
                model = combo.model()
                item: QStandardItem = model.item(combo.count() - 1)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        combo.setCurrentIndex(_OP_DEFAULT_INDEX)

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture UI state before worker dispatch (§4)."""
        op_idx    = self._combo_op.currentIndex()
        dtype_idx = self._combo_dtype.currentIndex()
        return {
            "_ui_op": _OP_INDEX_TO_VALUE.get(op_idx, _OP_DEFAULT_VALUE),
            "_ui_dtype": (
                _DTYPE_OPTIONS[dtype_idx][1] if 0 <= dtype_idx < len(_DTYPE_OPTIONS) else "auto"
            ),
            "_ui_scalar_b": self._spin_scalar_b.value(),
        }

    # ── Computation (§4, §7) ─────────────────────────────────────────
    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:  # noqa: C901
        _EMPTY = np.array([], dtype=np.float64)

        # Cooperative cancellation pre-flight (§7.2)
        if self.is_compute_cancelled():
            return {"result": _EMPTY}

        try:
            A = inputs.get("A")
            if A is None:
                self._pending_status = "waiting for A"
                return {"result": _EMPTY}
            if not isinstance(A, np.ndarray):
                A = np.asarray(A)

            B_raw = inputs.get("B")
            if B_raw is None:
                scalar_b = inputs.get("_ui_scalar_b", 1.0)
                B: Any = np.float64(scalar_b)
            elif isinstance(B_raw, np.ndarray):
                B = B_raw
            else:
                B = np.asarray(B_raw)

            op  = inputs.get("_ui_op", _OP_DEFAULT_VALUE)
            out_dtype_key = inputs.get("_ui_dtype", "auto")

            # Dispatch (§4)
            arr = self._apply_op(op, A, B)

            # Post-op cancellation check (NumPy C-extensions are atomic, but we honor framework state)
            if self.is_compute_cancelled():
                return {"result": _EMPTY}

            if out_dtype_key != "auto":
                try:
                    arr = arr.astype(out_dtype_key, copy=False)
                except Exception as cast_exc:
                    log.warning("NumpyMathNode: dtype cast to %s failed: %s", out_dtype_key, cast_exc)

            shape_str = "×".join(str(d) for d in arr.shape) or "scalar"
            self._pending_status = f"shape: ({shape_str})\ndtype: {arr.dtype}"
            log.debug("NumpyMathNode: op=%s  out=%s  dtype=%s", op, arr.shape, arr.dtype)
            return {"result": arr}

        except Exception as exc:
            log.warning("NumpyMathNode.compute: %s", exc)
            self._pending_status = f"error: {exc}"
            return {"result": _EMPTY}

    @staticmethod
    def _apply_op(op: str, A: np.ndarray, B: Any) -> np.ndarray:  # noqa: C901
        if op == "add":             return np.add(A, B)
        if op == "subtract":        return np.subtract(A, B)
        if op == "multiply":        return np.multiply(A, B)
        if op == "true_divide":     return np.true_divide(A, B)
        if op == "floor_divide":    return np.floor_divide(A, B)
        if op == "mod":             return np.mod(A, B)
        if op == "power":           return np.power(A, B)
        if op == "equal":           return np.equal(A, B)
        if op == "not_equal":       return np.not_equal(A, B)
        if op == "less":            return np.less(A, B)
        if op == "less_equal":      return np.less_equal(A, B)
        if op == "greater":         return np.greater(A, B)
        if op == "greater_equal":   return np.greater_equal(A, B)
        if op == "bitwise_and":     return np.bitwise_and(A, B)
        if op == "bitwise_or":      return np.bitwise_or(A, B)
        if op == "bitwise_xor":     return np.bitwise_xor(A, B)
        if op == "left_shift":      return np.left_shift(A, B)
        if op == "right_shift":     return np.right_shift(A, B)
        if op == "logical_and":     return np.logical_and(A, B)
        if op == "logical_or":      return np.logical_or(A, B)
        if op == "logical_xor":     return np.logical_xor(A, B)
        if op == "minimum":         return np.minimum(A, B)
        if op == "maximum":         return np.maximum(A, B)
        if op == "arctan2":         return np.arctan2(A, B)
        if op == "hypot":           return np.hypot(A, B)
        if op == "copysign":        return np.copysign(A, B)
        if op == "fmod":            return np.fmod(A, B)
        if op == "gcd":             return np.gcd(A.astype(np.int64), np.asarray(B, dtype=np.int64))
        if op == "lcm":             return np.lcm(A.astype(np.int64), np.asarray(B, dtype=np.int64))
        if op == "dot":             return np.dot(A, B)
        if op == "matmul":          return np.matmul(A, B)
        if op == "outer":           return np.outer(A, B)
        if op == "cross":           return np.cross(A, B)
        if op == "tensordot":       return np.tensordot(A, B, axes=1)
        raise ValueError(f"Unknown operation key: {op!r}")

    # ── Post-evaluation UI flush (§5) ────────────────────────────────
    def on_evaluate_finished(self) -> None:
        try:
            if self._pending_status is not None:
                try:
                    self._label_status.setText(self._pending_status)
                except RuntimeError:
                    pass  # Widget destroyed during cleanup
                self._pending_status = None

            result = self._get_cached_value("result")
            if result is not None:
                # This line now works because 'result_changed' is defined at class level
                self.result_changed.emit(result) 
        except Exception as exc:
            log.error("Exception in NumpyMathNode.on_evaluate_finished: %s", exc)
        finally:
            super().on_evaluate_finished()

    # ── Cleanup (§5, §7) ─────────────────────────────────────────────
    def cleanup(self) -> None:
        self._pending_status = None
        if hasattr(self, 'cancel_compute'):
            self.cancel_compute()
        self._widget_core.cleanup()
        super().cleanup()
