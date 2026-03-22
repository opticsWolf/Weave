# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_unary_math_node.py
------------------------
Element-wise unary math node for NumPy arrays.

Provided node
-------------
``NumpyUnaryMathNode``
    Accepts a single ``ndarray`` input (*A*) and emits a single
    ``ndarray`` output (*result*).  A dropdown selects the operation
    to apply.  An optional *Out Dtype* combo casts the result after
    the operation.

    Operation categories
    ~~~~~~~~~~~~~~~~~~~~
    Trigonometric
        sin, cos, tan, arcsin, arccos, arctan,
        sinh, cosh, tanh, arcsinh, arccosh, arctanh,
        deg2rad, rad2deg
    Exponential & Log
        exp, exp2, expm1, log, log2, log10, log1p
    Rounding
        floor, ceil, trunc, rint, fix, round (nearest even)
    Sign & Magnitude
        abs, sign, negative, positive, reciprocal, cbrt, square, sqrt
    Complex
        real, imag, conj, angle (radians), angle_deg (degrees)
    Bit / Int
        bitwise_not, isnan, isinf, isfinite, isneginf, isposinf
    Sorting & Order
        sort (axis=-1), argsort (axis=-1), flip (axis=None),
        fliplr, flipud, cumsum (axis=None), cumprod (axis=None)
    Reductions        (output shape is scalar or reduced)
        sum, prod, nansum, nanprod, min, max, nanmin, nanmax,
        mean, nanmean, std, nanstd, var, nanvar,
        norm (Frobenius), trace, any, all

    Output dtype
    ~~~~~~~~~~~~
    Auto        NumPy determines the output dtype (default).
    float64 … bool
        Cast the result to the chosen dtype after the operation.

    Error handling
    ~~~~~~~~~~~~~~
    All exceptions in ``compute`` are caught, logged at WARNING, and
    reflected in the status label; an empty ``float64`` array is
    emitted on failure.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
)

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore

from weave.logger import get_logger

log = get_logger("NumpyUnaryMathNode")


# ── Operation registry ────────────────────────────────────────────────────────

_OP_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("Trigonometric",    "trig"),
    ("Exponential & Log","exp_log"),
    ("Rounding",         "rounding"),
    ("Sign & Magnitude", "sign_mag"),
    ("Complex",          "complex"),
    ("Bit / Bool",       "bit_bool"),
    ("Sorting & Order",  "sort_order"),
    ("Reductions",       "reductions"),
)

_OPS_BY_CAT: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "trig": (
        ("Sin",               "sin"),
        ("Cos",               "cos"),
        ("Tan",               "tan"),
        ("Arcsin",            "arcsin"),
        ("Arccos",            "arccos"),
        ("Arctan",            "arctan"),
        ("Sinh",              "sinh"),
        ("Cosh",              "cosh"),
        ("Tanh",              "tanh"),
        ("Arcsinh",           "arcsinh"),
        ("Arccosh",           "arccosh"),
        ("Arctanh",           "arctanh"),
        ("Deg → Rad",         "deg2rad"),
        ("Rad → Deg",         "rad2deg"),
    ),
    "exp_log": (
        ("Exp  (eˣ)",         "exp"),
        ("Exp2  (2ˣ)",        "exp2"),
        ("Expm1  (eˣ − 1)",   "expm1"),
        ("Log  (ln)",         "log"),
        ("Log2",              "log2"),
        ("Log10",             "log10"),
        ("Log1p  (ln(1+x))",  "log1p"),
    ),
    "rounding": (
        ("Floor  (⌊x⌋)",      "floor"),
        ("Ceil   (⌈x⌉)",      "ceil"),
        ("Trunc  (→ 0)",      "trunc"),
        ("Rint   (round int)","rint"),
        ("Fix    (→ 0 int)",  "fix"),
        ("Round  (nearest)",  "round"),
    ),
    "sign_mag": (
        ("Abs  (|x|)",        "abs"),
        ("Sign",              "sign"),
        ("Negative  (−x)",    "negative"),
        ("Positive  (+x)",    "positive"),
        ("Reciprocal  (1/x)", "reciprocal"),
        ("Square  (x²)",      "square"),
        ("Sqrt  (√x)",        "sqrt"),
        ("Cbrt  (∛x)",        "cbrt"),
    ),
    "complex": (
        ("Real part",         "real"),
        ("Imag part",         "imag"),
        ("Conjugate",         "conj"),
        ("Angle  (radians)",  "angle_rad"),
        ("Angle  (degrees)",  "angle_deg"),
    ),
    "bit_bool": (
        ("Bitwise NOT  (~x)",         "bitwise_not"),
        ("Is NaN",                    "isnan"),
        ("Is Inf",                    "isinf"),
        ("Is Finite",                 "isfinite"),
        ("Is Neg Inf",                "isneginf"),
        ("Is Pos Inf",                "isposinf"),
    ),
    "sort_order": (
        ("Sort  (axis=−1)",           "sort"),
        ("Argsort  (axis=−1)",        "argsort"),
        ("Flip  (all axes)",          "flip"),
        ("Fliplr  (axis=1)",          "fliplr"),
        ("Flipud  (axis=0)",          "flipud"),
        ("Cumsum  (flattened)",       "cumsum"),
        ("Cumprod  (flattened)",      "cumprod"),
    ),
    "reductions": (
        ("Sum",                       "sum"),
        ("Product",                   "prod"),
        ("Sum  (nan-safe)",           "nansum"),
        ("Product  (nan-safe)",       "nanprod"),
        ("Min",                       "min"),
        ("Max",                       "max"),
        ("Min  (nan-safe)",           "nanmin"),
        ("Max  (nan-safe)",           "nanmax"),
        ("Mean",                      "mean"),
        ("Mean  (nan-safe)",          "nanmean"),
        ("Std Dev",                   "std"),
        ("Std Dev  (nan-safe)",       "nanstd"),
        ("Variance",                  "var"),
        ("Variance  (nan-safe)",      "nanvar"),
        ("Norm  (Frobenius)",         "norm"),
        ("Trace  (sum diagonal)",     "trace"),
        ("Any  (bool)",               "any"),
        ("All  (bool)",               "all"),
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
    i: op_key
    for i, (_, op_key) in enumerate(_OP_FLAT)
    if op_key is not None
}
_OP_VALUE_TO_INDEX: Dict[str, int] = {v: k for k, v in _OP_INDEX_TO_VALUE.items()}

_OP_DEFAULT_INDEX: int = next(iter(_OP_INDEX_TO_VALUE))   # first real item
_OP_DEFAULT_VALUE: str = _OP_FLAT[_OP_DEFAULT_INDEX][1]   # "sin"

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
# NumpyUnaryMathNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyUnaryMathNode(ThreadedNode):
    """
    Unary math node for NumPy arrays.

    Type: Threaded (compute() runs on QThreadPool; propagates downstream
    on any input or setting change).

    Inputs
    ------
    A : ndarray
        The operand array.

    Outputs
    -------
    result : ndarray
        The computed array.  Reduction operations return a 0-D or 1-D
        array rather than a Python scalar so the output port type is
        always ``ndarray``.  An empty ``float64`` array is emitted on
        error.

    Parameters
    ----------
    title : str
        Node title (default ``"Numpy Unary Math"``).
    """

    result_changed = Signal(object)   # emits ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Math"
    node_name:        ClassVar[Optional[str]] = "Numpy Unary Math"
    node_description: ClassVar[Optional[str]] = (
        "Element-wise and reduction unary math on a single NumPy array"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "math", "unary", "trig", "exp", "log", "round",
        "sort", "reduction", "sum", "mean", "array", "ndarray",
    ]

    def __init__(self, title: str = "Numpy Unary Math", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Ports ─────────────────────────────────────────────────────
        self.add_input("A", "ndarray")
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
        from PySide6.QtCore import Qt
        for label, op_key in _OP_FLAT:
            combo.addItem(label)
            if op_key is None:
                model = combo.model()
                item: QStandardItem = model.item(combo.count() - 1)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        combo.setCurrentIndex(_OP_DEFAULT_INDEX)

    # ── Widget snapshot ───────────────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
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
        }

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, _port_name: str) -> None:
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error("Exception in NumpyUnaryMathNode._on_core_changed: %s", exc)

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply the selected unary operation to input A.

        Runs on the worker thread — no Qt widget access.
        Op and dtype arrive pre-snapshotted under ``_ui_`` keys.
        """
        _EMPTY = np.array([], dtype=np.float64)

        try:
            A = inputs.get("A")
            if A is None:
                self._pending_status = "waiting for A"
                return {"result": _EMPTY}
            if not isinstance(A, np.ndarray):
                A = np.asarray(A)

            op            = inputs.get("_ui_op",    _OP_DEFAULT_VALUE)
            out_dtype_key = inputs.get("_ui_dtype", "auto")

            arr = self._apply_op(op, A)

            # Ensure result is always an ndarray (reductions may return scalar)
            arr = np.asarray(arr)

            if out_dtype_key != "auto":
                try:
                    arr = arr.astype(out_dtype_key, copy=False)
                except Exception as cast_exc:
                    log.warning(
                        "NumpyUnaryMathNode: dtype cast to %s failed: %s",
                        out_dtype_key, cast_exc,
                    )

            shape_str = "×".join(str(d) for d in arr.shape) or "scalar"
            self._pending_status = f"shape: ({shape_str})\ndtype: {arr.dtype}"
            log.debug(
                "NumpyUnaryMathNode: op=%s  out=%s  dtype=%s",
                op, arr.shape, arr.dtype,
            )
            return {"result": arr}

        except Exception as exc:
            log.warning("NumpyUnaryMathNode.compute: %s", exc)
            self._pending_status = f"error: {exc}"
            return {"result": _EMPTY}

    @staticmethod
    def _apply_op(op: str, A: np.ndarray) -> np.ndarray:  # noqa: C901
        """
        Dispatch *op* to the corresponding NumPy call.

        Raises on type / shape incompatibility so ``compute`` can catch
        and report it cleanly.
        """
        # ── Trigonometric ─────────────────────────────────────────────
        if op == "sin":         return np.sin(A)
        if op == "cos":         return np.cos(A)
        if op == "tan":         return np.tan(A)
        if op == "arcsin":      return np.arcsin(A)
        if op == "arccos":      return np.arccos(A)
        if op == "arctan":      return np.arctan(A)
        if op == "sinh":        return np.sinh(A)
        if op == "cosh":        return np.cosh(A)
        if op == "tanh":        return np.tanh(A)
        if op == "arcsinh":     return np.arcsinh(A)
        if op == "arccosh":     return np.arccosh(A)
        if op == "arctanh":     return np.arctanh(A)
        if op == "deg2rad":     return np.deg2rad(A)
        if op == "rad2deg":     return np.rad2deg(A)

        # ── Exponential & Log ─────────────────────────────────────────
        if op == "exp":         return np.exp(A)
        if op == "exp2":        return np.exp2(A)
        if op == "expm1":       return np.expm1(A)
        if op == "log":         return np.log(A)
        if op == "log2":        return np.log2(A)
        if op == "log10":       return np.log10(A)
        if op == "log1p":       return np.log1p(A)

        # ── Rounding ──────────────────────────────────────────────────
        if op == "floor":       return np.floor(A)
        if op == "ceil":        return np.ceil(A)
        if op == "trunc":       return np.trunc(A)
        if op == "rint":        return np.rint(A)
        if op == "fix":         return np.fix(A)
        if op == "round":       return np.round(A)

        # ── Sign & Magnitude ──────────────────────────────────────────
        if op == "abs":         return np.abs(A)
        if op == "sign":        return np.sign(A)
        if op == "negative":    return np.negative(A)
        if op == "positive":    return np.positive(A)
        if op == "reciprocal":  return np.reciprocal(A)
        if op == "square":      return np.square(A)
        if op == "sqrt":        return np.sqrt(A)
        if op == "cbrt":        return np.cbrt(A)

        # ── Complex ───────────────────────────────────────────────────
        if op == "real":        return np.real(A)
        if op == "imag":        return np.imag(A)
        if op == "conj":        return np.conj(A)
        if op == "angle_rad":   return np.angle(A, deg=False)
        if op == "angle_deg":   return np.angle(A, deg=True)

        # ── Bit / Bool ────────────────────────────────────────────────
        if op == "bitwise_not": return np.bitwise_not(A)
        if op == "isnan":       return np.isnan(A)
        if op == "isinf":       return np.isinf(A)
        if op == "isfinite":    return np.isfinite(A)
        if op == "isneginf":    return np.isneginf(A)
        if op == "isposinf":    return np.isposinf(A)

        # ── Sorting & Order ───────────────────────────────────────────
        if op == "sort":        return np.sort(A, axis=-1)
        if op == "argsort":     return np.argsort(A, axis=-1)
        if op == "flip":        return np.flip(A)
        if op == "fliplr":      return np.fliplr(A)
        if op == "flipud":      return np.flipud(A)
        if op == "cumsum":      return np.cumsum(A)
        if op == "cumprod":     return np.cumprod(A)

        # ── Reductions ────────────────────────────────────────────────
        if op == "sum":         return np.asarray(np.sum(A))
        if op == "prod":        return np.asarray(np.prod(A))
        if op == "nansum":      return np.asarray(np.nansum(A))
        if op == "nanprod":     return np.asarray(np.nanprod(A))
        if op == "min":         return np.asarray(np.min(A))
        if op == "max":         return np.asarray(np.max(A))
        if op == "nanmin":      return np.asarray(np.nanmin(A))
        if op == "nanmax":      return np.asarray(np.nanmax(A))
        if op == "mean":        return np.asarray(np.mean(A))
        if op == "nanmean":     return np.asarray(np.nanmean(A))
        if op == "std":         return np.asarray(np.std(A))
        if op == "nanstd":      return np.asarray(np.nanstd(A))
        if op == "var":         return np.asarray(np.var(A))
        if op == "nanvar":      return np.asarray(np.nanvar(A))
        if op == "norm":        return np.asarray(np.linalg.norm(A))
        if op == "trace":       return np.asarray(np.trace(A))
        if op == "any":         return np.asarray(np.any(A))
        if op == "all":         return np.asarray(np.all(A))

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
            log.error(
                "Exception in NumpyUnaryMathNode.on_evaluate_finished: %s", exc
            )
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._pending_status = None
        self._widget_core.cleanup()
        super().cleanup()
