# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_complex_combiner_node.py
-------------------------------
Combines two numeric inputs into a single complex-valued NumPy array.

Provided node
-------------
``NumpyComplexCombinerNode``
    Takes a *Real* input and an *Imaginary* input and assembles them
    into a ``complex128`` ``ndarray``.

    Accepted input types
    ~~~~~~~~~~~~~~~~~~~~
    ========================= ==========================================
    Incoming type              Behaviour
    ========================= ==========================================
    ``ndarray`` complex dtype  ``.real`` / ``.imag`` extracted
    ``ndarray`` real/int dtype values used as-is
    ``list`` / ``tuple``       converted via ``numpy.asarray``; if the
                               conversion fails an ERROR is logged and
                               the fallback spinbox value is used
    ``int`` / ``float``        wrapped in a length-1 array
    Not connected / ``None``   fallback spinbox value used
    Any other type             WARNING logged, fallback used
    ========================= ==========================================

    Dimension alignment
    ~~~~~~~~~~~~~~~~~~~
    When the two arrays have different numbers of dimensions the
    higher-rank array is reduced to match the lower rank by merging
    its leading axes:

    * ``(2, 3, 4)`` → 2-D : reshape to ``(6, 4)``
    * ``(2, 3, 4)`` → 1-D : reshape to ``(24,)``

    Shape mismatch behaviour (axis-0 length)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Controlled by the *Mismatch* combo in the node body:

    ============= ==========================================================
    Truncate      Clip both arrays to ``min(len_re, len_im)``.
    Continue      Extend the shorter array by repeating its last element
                  until both arrays reach ``max(len_re, len_im)``.
    Fill          Extend the shorter array with zeros until both arrays
                  reach ``max(len_re, len_im)``.
    ============= ==========================================================

    Every adjustment is reported at ``INFO`` level via the Weave logger.

    Outputs
    -------
    array : ndarray (complex128)
        ``real_part + 1j * imag_part``

    Type: Active (propagates downstream on any change).
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
)

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore

from weave.logger import get_logger

log = get_logger("NumpyComplexCombinerNode")


# ── Module-level constants ────────────────────────────────────────────────────

_MISMATCH_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("Truncate", "truncate"),
    ("Continue", "continue"),
    ("Fill",     "fill"),
)


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# =============================================================================
# NumpyComplexCombinerNode
# =============================================================================

@register_node
class NumpyComplexCombinerNode(ThreadedNode):
    """
    Combines real and imaginary inputs into a complex128 ndarray.

    Type: Threaded (compute() runs on QThreadPool; propagates downstream
    on any widget or port change).

    Inputs
    ------
    real : ndarray | list | tuple | int | float, optional
        Source for the real component.  Complex-dtype arrays have their
        ``.real`` part extracted.  Lists and tuples are converted via
        ``numpy.asarray``; on failure an ERROR is logged and 0 is used.
        Scalars (int/float) are broadcast as a length-1 array.
        When not connected the *Real* spinbox is used as a fallback.

    imag : ndarray | list | tuple | int | float, optional
        Source for the imaginary component.  Same rules as *real*.
        When not connected the *Imag* spinbox is used as a fallback.

    Outputs
    -------
    array : ndarray (complex128)
        ``real_part + 1j * imag_part``

    Parameters
    ----------
    title : str
        Node title (default ``"Complex Combiner"``).
    """

    array_changed = Signal(object)   # emits ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Generator"
    node_name:        ClassVar[Optional[str]] = "Complex Combiner"
    node_description: ClassVar[Optional[str]] = (
        "Combines real and imaginary inputs into a complex128 ndarray"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "complex", "combiner", "array", "real", "imaginary",
        "generator", "primitive",
    ]

    def __init__(self, title: str = "Complex Combiner", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input ports (auto-disable matching spinboxes) ─────────────
        self.add_input("real", "ndarray"); self.inputs[-1]._auto_disable = True
        self.add_input("imag", "ndarray"); self.inputs[-1]._auto_disable = True

        self.add_output("array", "ndarray")

        # ── Form layout ───────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Mismatch behaviour combo (internal) ───────────────────────
        self._combo_mismatch = QComboBox()
        for label, _ in _MISMATCH_OPTIONS:
            self._combo_mismatch.addItem(label)
        form.addRow("Mismatch:", self._combo_mismatch)
        self._widget_core.register_widget(
            "mismatch", self._combo_mismatch,
            role="internal", datatype="string", default="truncate",
            add_to_layout=False,
        )

        # ── Scalar fallbacks ──────────────────────────────────────────
        form.addRow(_make_separator())
        form.addRow(QLabel("Fallback scalars:"))

        self._spin_real = self._make_spin(0.0)
        form.addRow("Real:", self._spin_real)
        self._widget_core.register_widget(
            "real_fallback", self._spin_real,
            role="internal", datatype="float", default=0.0,
            add_to_layout=False,
        )

        self._spin_imag = self._make_spin(0.0)
        form.addRow("Imag:", self._spin_imag)
        self._widget_core.register_widget(
            "imag_fallback", self._spin_imag,
            role="internal", datatype="float", default=0.0,
            add_to_layout=False,
        )

        # ── Status display ────────────────────────────────────────────
        form.addRow(_make_separator())
        self._label_status = QLabel("--")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        form.addRow(self._label_status)

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._pending_status: Optional[str] = None

    # ── Widget snapshot (main thread → worker thread) ─────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture combo and spinbox fallbacks before worker dispatch."""
        idx = self._combo_mismatch.currentIndex()
        return {
            "_ui_mismatch":      (
                _MISMATCH_OPTIONS[idx][1]
                if 0 <= idx < len(_MISMATCH_OPTIONS) else "truncate"
            ),
            "_ui_real_fallback": self._spin_real.value(),
            "_ui_imag_fallback": self._spin_imag.value(),
        }

    # ── Widget factory ────────────────────────────────────────────────────────

    @staticmethod
    def _make_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1e15, 1e15)
        spin.setValue(default)
        spin.setDecimals(6)
        spin.setMinimumWidth(110)
        return spin

    # ── Helpers ───────────────────────────────────────────────────────────────

    # ── Type conversion ───────────────────────────────────────────────────────

    @classmethod
    def _to_float64(
        cls,
        value: Any,
        component: str,   # "real" or "imag" — for complex ndarray extraction
        fallback: float,
        port_name: str,
    ) -> Tuple[np.ndarray, bool]:
        """
        Convert *value* to a float64 ndarray representing *component*.

        Returns ``(array, is_fallback)``.  *is_fallback* is ``True``
        whenever the spinbox default was used instead of the input.

        Accepted types
        --------------
        None             → fallback scalar (not connected)
        int / float      → length-1 array
        list / tuple     → ``np.asarray``; ERROR + zero fallback on failure
        ndarray complex  → ``.real`` or ``.imag`` extracted
        ndarray other    → cast to float64 as-is
        anything else    → WARNING + zero fallback
        """
        # ── Not connected ─────────────────────────────────────────────
        if value is None:
            return np.array([fallback], dtype=np.float64), True

        # ── Scalar int / float ────────────────────────────────────────
        if isinstance(value, bool):
            # bool is a subclass of int — treat as numeric 0/1
            return np.array([float(value)], dtype=np.float64), False
        if isinstance(value, (int, float)):
            return np.array([float(value)], dtype=np.float64), False

        # ── list / tuple ──────────────────────────────────────────────
        if isinstance(value, (list, tuple)):
            try:
                arr = np.asarray(value, dtype=np.float64)
                if arr.ndim == 0:
                    arr = arr.reshape(1)
                return arr, False
            except (ValueError, TypeError) as exc:
                log.error(
                    "Port '%s': cannot convert %s to float64 ndarray -- %s"
                    " -- using fallback value 0",
                    port_name, type(value).__name__, exc,
                )
                return np.array([0.0], dtype=np.float64), True

        # ── ndarray ───────────────────────────────────────────────────
        if isinstance(value, np.ndarray):
            if np.issubdtype(value.dtype, np.complexfloating):
                part = value.real if component == "real" else value.imag
                return part.astype(np.float64), False
            return value.astype(np.float64), False

        # ── Unsupported type ──────────────────────────────────────────
        log.warning(
            "Port '%s': unsupported type %s -- using fallback value 0",
            port_name, type(value).__name__,
        )
        return np.array([0.0], dtype=np.float64), True

    # ── Dimension helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _reduce_dims(arr: np.ndarray, target_ndim: int) -> np.ndarray:
        """
        Reduce *arr* to *target_ndim* dimensions by merging leading axes.

        ``(2, 3, 4)`` → target 2 → ``(6, 4)``
        ``(2, 3, 4)`` → target 1 → ``(24,)``
        """
        if arr.ndim <= target_ndim:
            return arr
        leading   = arr.ndim - target_ndim
        merged    = int(np.prod(arr.shape[:leading + 1]))
        new_shape = (merged,) + arr.shape[leading + 1:]
        return arr.reshape(new_shape)

    @staticmethod
    def _align_length(
        re: np.ndarray,
        im: np.ndarray,
        mode: str,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Align the axis-0 lengths of *re* and *im* according to *mode*.

        truncate
            Clip both to ``min(len_re, len_im)``.
        continue
            Extend the shorter array by repeating its last element.
        fill
            Extend the shorter array with zeros.

        Returns the (possibly modified) pair ``(re, im)``.
        """
        lr, li = re.shape[0], im.shape[0]
        if lr == li:
            return re, im

        if mode == "truncate":
            n = min(lr, li)
            return re[:n], im[:n]

        n = max(lr, li)

        if mode == "continue":
            if lr < n:
                pad = np.repeat(re[-1:], n - lr, axis=0)
                re  = np.concatenate([re, pad], axis=0)
            if li < n:
                pad = np.repeat(im[-1:], n - li, axis=0)
                im  = np.concatenate([im, pad], axis=0)

        elif mode == "fill":
            if lr < n:
                pad = np.zeros((n - lr,) + re.shape[1:], dtype=re.dtype)
                re  = np.concatenate([re, pad], axis=0)
            if li < n:
                pad = np.zeros((n - li,) + im.shape[1:], dtype=im.dtype)
                im  = np.concatenate([im, pad], axis=0)

        return re, im

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, _port_name: str) -> None:
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error(
                "Exception in NumpyComplexCombinerNode._on_core_changed: %s", exc
            )

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        1. Convert both inputs to float64 ndarrays.
        2. Align rank (reduce higher-D to lower-D by merging leading axes).
        3. Align axis-0 length using the selected mismatch strategy.
        4. Combine into complex128.

        Runs on the worker thread — no Qt widget access.
        Fallback scalars and mismatch mode arrive pre-snapshotted.
        """
        try:
            fb_real  = float(inputs.get("_ui_real_fallback", 0.0))
            fb_imag  = float(inputs.get("_ui_imag_fallback", 0.0))
            mismatch = inputs.get("_ui_mismatch", "truncate")

            re, re_fallback = self._to_float64(
                inputs.get("real"), "real", fb_real, "real"
            )
            im, im_fallback = self._to_float64(
                inputs.get("imag"), "imag", fb_imag, "imag"
            )

            # ── Step 1 — Dimension alignment ──────────────────────────
            if re.ndim != im.ndim:
                target_ndim = min(re.ndim, im.ndim)

                if re.ndim > target_ndim:
                    old_shape = re.shape
                    re = self._reduce_dims(re, target_ndim)
                    log.info(
                        "Real: reduced from %d-D %s to %d-D %s "
                        "(merged leading axes to match imaginary rank)",
                        len(old_shape), old_shape, re.ndim, re.shape,
                    )

                if im.ndim > target_ndim:
                    old_shape = im.shape
                    im = self._reduce_dims(im, target_ndim)
                    log.info(
                        "Imag: reduced from %d-D %s to %d-D %s "
                        "(merged leading axes to match real rank)",
                        len(old_shape), old_shape, im.ndim, im.shape,
                    )

            # ── Step 2 — Axis-0 length alignment ──────────────────────
            len_re = re.shape[0]
            len_im = im.shape[0]

            if len_re != len_im:
                log.info(
                    "Shape mismatch: real axis-0=%d, imag axis-0=%d "
                    "-- applying '%s' strategy",
                    len_re, len_im, mismatch,
                )
                re, im = self._align_length(re, im, mismatch)
                log.info(
                    "After alignment: real=%s, imag=%s",
                    re.shape, im.shape,
                )

            # ── Step 3 — Combine ──────────────────────────────────────
            result: np.ndarray = re + 1j * im   # always complex128

            # ── Build status label ────────────────────────────────────
            shape_str = "x".join(str(d) for d in result.shape)
            status = f"shape: ({shape_str})\ndtype: complex128"
            if re_fallback or im_fallback:
                parts = []
                if re_fallback:
                    parts.append(f"real={fb_real:.4g}")
                if im_fallback:
                    parts.append(f"imag={fb_imag:.4g}")
                status += f"\nfallback: {', '.join(parts)}"
            self._pending_status = status

            return {"array": result}

        except Exception as exc:
            log.error(
                "Exception in NumpyComplexCombinerNode.compute: %s", exc
            )
            self._pending_status = f"error: {exc}"
            return {"array": np.array([], dtype=np.complex128)}

    # ── Post-evaluation UI flush ──────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Flush status label and emit array_changed on the main thread."""
        try:
            if self._pending_status is not None:
                try:
                    self._label_status.setText(self._pending_status)
                except RuntimeError:
                    pass
                self._pending_status = None

            result = self.get_output_value("array")
            if result is not None:
                self.array_changed.emit(result)
        except Exception as exc:
            log.error(
                "Exception in NumpyComplexCombinerNode.on_evaluate_finished: %s",
                exc,
            )
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._pending_status = None
        self._widget_core.cleanup()
        super().cleanup()