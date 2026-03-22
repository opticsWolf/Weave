# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_math_node.py
------------------
Element-wise and linear-algebra binary math node for NumPy arrays.

Provided node
-------------
``NumpyMathNode``
    Accepts two ``ndarray`` inputs (*A* and *B*) and emits a single
    ``ndarray`` output (*result*).  A hierarchical pair of combos selects
    the operation category and the specific operation within that category.
    When the *B* port is not connected a scalar fallback spinbox is used
    instead, allowing constant operands without requiring an upstream node.

    Input B handling
    ~~~~~~~~~~~~~~~~
    * If the *B* port receives an upstream ``ndarray``, it is used directly.
    * If the *B* port is unconnected or ``None``, the *Scalar B* spinbox
      value is broadcast against *A*.
    * The *B* port auto-disables the *Scalar B* spinbox when connected.

    Operation categories
    ~~~~~~~~~~~~~~~~~~~~
    Arithmetic
        Add, Subtract, Multiply, True Divide, Floor Divide, Modulo, Power
    Comparison   (output is bool array)
        Equal, Not Equal, Less, Less or Equal, Greater, Greater or Equal
    Bitwise      (integer inputs required)
        AND, OR, XOR, Left Shift, Right Shift
    Logical      (output is bool array)
        Logical AND, Logical OR, Logical XOR
    Element Math
        Minimum, Maximum, Arctan2, Hypot, Copysign, Fmod, GCD, LCM
    Linear Algebra
        Dot, Matrix Multiply, Outer Product, Cross Product, Tensordot

    Output dtype
    ~~~~~~~~~~~~
    Auto        NumPy determines the output dtype (default — recommended).
    float64 … bool
        Cast the result to the chosen dtype after the operation.

    Error handling
    ~~~~~~~~~~~~~~
    * Shape mismatches that cannot be broadcast are caught and logged at
      WARNING level; an empty ``float64`` array is emitted.
    * Type errors (e.g. bitwise ops on float arrays) are caught, logged,
      and the result is an empty array.
    * All errors are also reflected in the status label in the node body.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from PySide6.QtCore import Signal, Slot
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

from weave.logger import get_logger

log = get_logger("NumpyMathNode")


# ── Operation registry ────────────────────────────────────────────────────────
#
# Structure:
#   _OP_CATEGORIES  — ordered tuple of (display_label, category_key)
#   _OPS_BY_CAT     — dict[category_key -> tuple of (display_label, op_key)]
#   _OP_FLAT        — flat tuple of (display_label, op_key) in category order,
#                     used for building the operation combo.  Category headers
#                     are represented as (label, None) and rendered as
#                     disabled separator items.
#
# compute() dispatches solely on op_key strings; labels are UI-only.

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

# Build flat list: [(label, op_key_or_None), ...] preserving category order.
# None op_key marks a non-selectable category header item.
def _build_flat_ops() -> Tuple[Tuple[str, Optional[str]], ...]:
    flat: List[Tuple[str, Optional[str]]] = []
    for cat_label, cat_key in _OP_CATEGORIES:
        flat.append((f"── {cat_label} ──", None))
        for op_label, op_key in _OPS_BY_CAT[cat_key]:
            flat.append((op_label, op_key))
    return tuple(flat)

_OP_FLAT: Tuple[Tuple[str, Optional[str]], ...] = _build_flat_ops()

# Pre-built index→value and value→index maps (excluding header rows).
_OP_INDEX_TO_VALUE: Dict[int, str] = {
    i: op_key
    for i, (_, op_key) in enumerate(_OP_FLAT)
    if op_key is not None
}
_OP_VALUE_TO_INDEX: Dict[str, int] = {v: k for k, v in _OP_INDEX_TO_VALUE.items()}

# First real (non-header) combo index — used as the default.
_OP_DEFAULT_INDEX: int = next(iter(_OP_INDEX_TO_VALUE))
_OP_DEFAULT_VALUE: str = _OP_FLAT[_OP_DEFAULT_INDEX][1]  # "add"

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
    """
    Binary math node for NumPy arrays.

    Type: Threaded (compute() runs on QThreadPool; propagates downstream
    on any input or setting change).

    Inputs
    ------
    A : ndarray
        Left-hand operand.
    B : ndarray, optional
        Right-hand operand.  When unconnected the *Scalar B* spinbox
        value is broadcast against A instead.

    Outputs
    -------
    result : ndarray
        The computed array.  An empty ``float64`` array is emitted on
        error.

    Parameters
    ----------
    title : str
        Node title (default ``"Numpy Math"``).
    """

    result_changed = Signal(object)   # emits ndarray

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

    def __init__(self, title: str = "Numpy Math", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input / output ports ──────────────────────────────────────
        self.add_input("A", "ndarray")
        self.add_input("B", "ndarray")
        self.inputs[-1]._auto_disable = True   # B disables scalar spinbox

        self.add_output("result", "ndarray")

        # ── Form layout ───────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Operation combo ───────────────────────────────────────────
        self._combo_op = QComboBox()
        self._combo_op.setMinimumWidth(180)
        self._populate_op_combo(self._combo_op)
        form.addRow("Op:", self._combo_op)
        self._widget_core.register_widget(
            "op", self._combo_op,
            role="internal", datatype="string", default=_OP_DEFAULT_VALUE,
            add_to_layout=False,
        )

        # ── Dtype combo ───────────────────────────────────────────────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Out Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role="internal", datatype="string", default="auto",
            add_to_layout=False,
        )

        form.addRow(_make_separator())

        # ── Scalar B fallback (bidirectional — auto-disabled by B port) ──
        self._label_scalar_b = QLabel("Scalar B:")
        self._spin_scalar_b = QDoubleSpinBox()
        self._spin_scalar_b.setRange(-1e12, 1e12)
        self._spin_scalar_b.setValue(1.0)
        self._spin_scalar_b.setDecimals(6)
        self._spin_scalar_b.setMinimumWidth(130)
        form.addRow(self._label_scalar_b, self._spin_scalar_b)
        self._widget_core.register_widget(
            "B", self._spin_scalar_b,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        # ── Status display ────────────────────────────────────────────
        form.addRow(_make_separator())
        self._label_status = QLabel("--")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        self._label_status.setMinimumWidth(160)
        form.addRow(self._label_status)

        # ── Wire ──────────────────────────────────────────────────────
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._pending_status: Optional[str] = None

    # ── Combo population ──────────────────────────────────────────────────────

    @staticmethod
    def _populate_op_combo(combo: QComboBox) -> None:
        """
        Add all operation items to *combo*.

        Category header items (op_key is ``None``) are inserted as
        disabled, non-selectable rows so they act as visual section
        dividers without interfering with index-to-value mapping.
        """
        from PySide6.QtCore import Qt

        for label, op_key in _OP_FLAT:
            combo.addItem(label)
            if op_key is None:
                # Make the header row non-selectable
                model = combo.model()
                item: QStandardItem = model.item(combo.count() - 1)
                item.setFlags(Qt.ItemFlag.NoItemFlags)

        # Start on the first real operation
        combo.setCurrentIndex(_OP_DEFAULT_INDEX)

    # ── Widget snapshot ───────────────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture combo selections and scalar B before worker dispatch."""
        op_idx    = self._combo_op.currentIndex()
        dtype_idx = self._combo_dtype.currentIndex()
        return {
            "_ui_op": (
                _OP_INDEX_TO_VALUE.get(op_idx, _OP_DEFAULT_VALUE)
            ),
            "_ui_dtype": (
                _DTYPE_OPTIONS[dtype_idx][1]
                if 0 <= dtype_idx < len(_DTYPE_OPTIONS) else "auto"
            ),
            "_ui_scalar_b": self._spin_scalar_b.value(),
        }

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, _port_name: str) -> None:
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error("Exception in NumpyMathNode._on_core_changed: %s", exc)

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:  # noqa: C901
        """
        Apply the selected binary operation to inputs A and B.

        Runs on the worker thread — no Qt widget access.
        Op, dtype, and scalar-B arrive pre-snapshotted under ``_ui_`` keys.
        """
        _EMPTY = np.array([], dtype=np.float64)

        try:
            A = inputs.get("A")
            if A is None:
                self._pending_status = "waiting for A"
                return {"result": _EMPTY}
            if not isinstance(A, np.ndarray):
                A = np.asarray(A)

            # B: prefer upstream port, fall back to scalar spinbox value.
            B_raw = inputs.get("B")
            if B_raw is None:
                scalar_b = inputs.get("_ui_scalar_b", 1.0)
                B: Any = np.float64(scalar_b)
            elif isinstance(B_raw, np.ndarray):
                B = B_raw
            else:
                B = np.asarray(B_raw)

            op  = inputs.get("_ui_op",    _OP_DEFAULT_VALUE)
            out_dtype_key = inputs.get("_ui_dtype", "auto")

            # ── Dispatch ──────────────────────────────────────────────
            arr = self._apply_op(op, A, B)

            # ── Optional dtype cast ───────────────────────────────────
            if out_dtype_key != "auto":
                try:
                    arr = arr.astype(out_dtype_key, copy=False)
                except Exception as cast_exc:
                    log.warning(
                        "NumpyMathNode: dtype cast to %s failed: %s",
                        out_dtype_key, cast_exc,
                    )

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
        """
        Dispatch *op* to the corresponding NumPy call.

        Raises on shape / type mismatch so ``compute`` can catch and log it.
        """
        # ── Arithmetic ────────────────────────────────────────────────
        if op == "add":             return np.add(A, B)
        if op == "subtract":        return np.subtract(A, B)
        if op == "multiply":        return np.multiply(A, B)
        if op == "true_divide":     return np.true_divide(A, B)
        if op == "floor_divide":    return np.floor_divide(A, B)
        if op == "mod":             return np.mod(A, B)
        if op == "power":           return np.power(A, B)

        # ── Comparison ────────────────────────────────────────────────
        if op == "equal":           return np.equal(A, B)
        if op == "not_equal":       return np.not_equal(A, B)
        if op == "less":            return np.less(A, B)
        if op == "less_equal":      return np.less_equal(A, B)
        if op == "greater":         return np.greater(A, B)
        if op == "greater_equal":   return np.greater_equal(A, B)

        # ── Bitwise ───────────────────────────────────────────────────
        if op == "bitwise_and":     return np.bitwise_and(A, B)
        if op == "bitwise_or":      return np.bitwise_or(A, B)
        if op == "bitwise_xor":     return np.bitwise_xor(A, B)
        if op == "left_shift":      return np.left_shift(A, B)
        if op == "right_shift":     return np.right_shift(A, B)

        # ── Logical ───────────────────────────────────────────────────
        if op == "logical_and":     return np.logical_and(A, B)
        if op == "logical_or":      return np.logical_or(A, B)
        if op == "logical_xor":     return np.logical_xor(A, B)

        # ── Element-wise math ─────────────────────────────────────────
        if op == "minimum":         return np.minimum(A, B)
        if op == "maximum":         return np.maximum(A, B)
        if op == "arctan2":         return np.arctan2(A, B)
        if op == "hypot":           return np.hypot(A, B)
        if op == "copysign":        return np.copysign(A, B)
        if op == "fmod":            return np.fmod(A, B)
        if op == "gcd":             return np.gcd(A.astype(np.int64), np.asarray(B, dtype=np.int64))
        if op == "lcm":             return np.lcm(A.astype(np.int64), np.asarray(B, dtype=np.int64))

        # ── Linear algebra ────────────────────────────────────────────
        if op == "dot":             return np.dot(A, B)
        if op == "matmul":          return np.matmul(A, B)
        if op == "outer":           return np.outer(A, B)
        if op == "cross":           return np.cross(A, B)
        if op == "tensordot":       return np.tensordot(A, B, axes=1)

        raise ValueError(f"Unknown operation key: {op!r}")

    # ── Post-evaluation UI flush ──────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Flush the status label and emit result_changed on the main thread."""
        try:
            if self._pending_status is not None:
                try:
                    self._label_status.setText(self._pending_status)
                except RuntimeError:
                    pass
                self._pending_status = None

            result = self.get_output_value("result")
            if result is not None:
                self.result_changed.emit(result)
        except Exception as exc:
            log.error("Exception in NumpyMathNode.on_evaluate_finished: %s", exc)
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._pending_status = None
        self._widget_core.cleanup()
        super().cleanup()
