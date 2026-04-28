# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_complex_combiner_node.py
-------------------------------
Combines two numeric inputs into a single complex-valued NumPy array.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
)

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore, PortRole
from weave.node import VerticalSizePolicy
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
    """

    array_changed = Signal(object)   # emits ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Generator"
    node_name:        ClassVar[Optional[str]] = "Complex Combiner"
    node_description: ClassVar[Optional[str]] = (
        "Combines real and imaginary inputs into a complex128 ndarray"
    )
    node_tags: ClassVar[List[str]] = [
        "numpy", "complex", "combiner", "array", "real", "imaginary",
        "generator", "primitive",
    ]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Complex Combiner", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input ports (auto-disable matching spinboxes when connected) ──
        self.add_input("real", "ndarray")
        self.inputs[-1]._auto_disable = True
        
        self.add_input("imag", "ndarray")
        self.inputs[-1]._auto_disable = True

        self.add_output("array", "ndarray")

        # ── Form layout & WidgetCore initialization ───────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Mismatch behaviour combo (internal state → inputs dict) ───
        self._combo_mismatch = QComboBox()
        for label, _ in _MISMATCH_OPTIONS:
            self._combo_mismatch.addItem(label)
        form.addRow("Mismatch:", self._combo_mismatch)
        self._widget_core.register_widget(
            "mismatch_mode", self._combo_mismatch,
            role=PortRole.INTERNAL, datatype="string", default="truncate",
            add_to_layout=False
        )

        # ── Scalar fallbacks (internal state → inputs dict) ───────────
        form.addRow(_make_separator())
        form.addRow(QLabel("Fallback scalars:"))

        self._spin_real = self._make_spin(0.0)
        form.addRow("Real:", self._spin_real)
        self._widget_core.register_widget(
            "real_fallback", self._spin_real,
            role=PortRole.INTERNAL, datatype="float", default=0.0,
            add_to_layout=False
        )

        self._spin_imag = self._make_spin(0.0)
        form.addRow("Imag:", self._spin_imag)
        self._widget_core.register_widget(
            "imag_fallback", self._spin_imag,
            role=PortRole.INTERNAL, datatype="float", default=0.0,
            add_to_layout=False
        )

        # ── Status display (main-thread only) ────────────────────────
        form.addRow(_make_separator())
        self._label_status = QLabel("--")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        form.addRow(self._label_status)

        # ── Signal wiring & content attachment ───────────────────────
        self._widget_core.value_changed.connect(self.on_ui_change)
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    # ── Widget factory ───────────────────────────────────────────────
    @staticmethod
    def _make_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1e15, 1e15)
        spin.setValue(default)
        spin.setDecimals(6)
        spin.setMinimumWidth(110)
        return spin

    # ── Type conversion helper ───────────────────────────────────────
    @classmethod
    def _to_float64(
        cls, value: Any, component: str, fallback: float, port_name: str
    ) -> Tuple[np.ndarray, bool]:
        if value is None:
            return np.array([fallback], dtype=np.float64), True

        if isinstance(value, bool):
            return np.array([float(value)], dtype=np.float64), False
        if isinstance(value, (int, float)):
            return np.array([float(value)], dtype=np.float64), False

        if isinstance(value, (list, tuple)):
            try:
                arr = np.asarray(value, dtype=np.float64)
                return arr.reshape(1) if arr.ndim == 0 else arr, False
            except (ValueError, TypeError) as exc:
                log.error("Port '%s': conversion failed -- %s", port_name, exc)
                return np.array([0.0], dtype=np.float64), True

        if isinstance(value, np.ndarray):
            if np.issubdtype(value.dtype, np.complexfloating):
                part = value.real if component == "real" else value.imag
                return part.astype(np.float64), False
            return value.astype(np.float64), False

        log.warning("Port '%s': unsupported type %s", port_name, type(value).__name__)
        return np.array([0.0], dtype=np.float64), True

    # ── Dimension alignment helpers ──────────────────────────────────
    @staticmethod
    def _reduce_dims(arr: np.ndarray, target_ndim: int) -> np.ndarray:
        if arr.ndim <= target_ndim:
            return arr
        leading = arr.ndim - target_ndim
        merged  = int(np.prod(arr.shape[:leading + 1]))
        return arr.reshape((merged,) + arr.shape[leading + 1:])

    @staticmethod
    def _align_length(re_arr: np.ndarray, im_arr: np.ndarray, mode: str) -> Tuple[np.ndarray, np.ndarray]:
        lr, li = re_arr.shape[0], im_arr.shape[0]
        if lr == li:
            return re_arr, im_arr

        if mode == "truncate":
            n = min(lr, li)
            return re_arr[:n], im_arr[:n]

        n = max(lr, li)
        if mode == "continue":
            if lr < n:
                pad = np.repeat(re_arr[-1:], n - lr, axis=0)
                re_arr = np.concatenate([re_arr, pad], axis=0)
            if li < n:
                pad = np.repeat(im_arr[-1:], n - li, axis=0)
                im_arr = np.concatenate([im_arr, pad], axis=0)
        elif mode == "fill":
            if lr < n:
                pad = np.zeros((n - lr,) + re_arr.shape[1:], dtype=re_arr.dtype)
                re_arr = np.concatenate([re_arr, pad], axis=0)
            if li < n:
                pad = np.zeros((n - li,) + im_arr.shape[1:], dtype=im_arr.dtype)
                im_arr = np.concatenate([im_arr, pad], axis=0)
        return re_arr, im_arr

    # ── Background Computation (STRICTLY THREAD-SAFE) ────────────────
    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_compute_cancelled():
            return {"array": np.array([], dtype=np.complex128)}

        try:
            fb_real  = float(inputs.get("real_fallback", 0.0))
            fb_imag  = float(inputs.get("imag_fallback", 0.0))
            
            # Handle combo box value (may be string or index depending on Weave version)
            raw_mode = inputs.get("mismatch_mode", "truncate")
            if isinstance(raw_mode, int):
                mode = _MISMATCH_OPTIONS[raw_mode][1] if 0 <= raw_mode < len(_MISMATCH_OPTIONS) else "truncate"
            else:
                mode = str(raw_mode).lower()

            re_arr, re_fb = self._to_float64(inputs.get("real"), "real", fb_real, "real")
            im_arr, im_fb = self._to_float64(inputs.get("imag"), "imag", fb_imag, "imag")

            # Dimension alignment
            if re_arr.ndim != im_arr.ndim:
                target_ndim = min(re_arr.ndim, im_arr.ndim)
                if re_arr.ndim > target_ndim:
                    old_s = re_arr.shape
                    re_arr = self._reduce_dims(re_arr, target_ndim)
                    log.info("Real reduced %s → %s", old_s, re_arr.shape)
                if im_arr.ndim > target_ndim:
                    old_s = im_arr.shape
                    im_arr = self._reduce_dims(im_arr, target_ndim)
                    log.info("Imag reduced %s → %s", old_s, im_arr.shape)

            # Axis-0 length alignment
            if re_arr.shape[0] != im_arr.shape[0]:
                log.info("Aligning axis-0: real=%d, imag=%d (mode='%s')", 
                         re_arr.shape[0], im_arr.shape[0], mode)
                re_arr, im_arr = self._align_length(re_arr, im_arr, mode)

            # Combine
            result = (re_arr + 1j * im_arr).astype(np.complex128)
            
            if self.is_compute_cancelled():
                return {"array": np.array([], dtype=np.complex128)}

            return {"array": result}

        except Exception as exc:
            log.error("NumpyComplexCombinerNode.compute failed: %s", exc)
            return {"array": np.array([], dtype=np.complex128)}

    # ── Main-Thread Post-Evaluation Hook ─────────────────────────────
    def on_evaluate_finished(self) -> None:
        try:
            result = self._get_cached_value("array")
            
            if result is not None and isinstance(result, np.ndarray):
                shape_str = "×".join(str(d) for d in result.shape) or "scalar"
                status_text = f"shape: ({shape_str})\ndtype: complex128"
                self._label_status.setText(status_text)
            else:
                self._label_status.setText("--")

            if result is not None:
                self.array_changed.emit(result)
                
        except Exception as exc:
            log.error("Exception in on_evaluate_finished: %s", exc)
        finally:
            super().on_evaluate_finished()

    # ── Lifecycle Cleanup ────────────────────────────────────────────
    def cleanup(self) -> None:
        if hasattr(self, 'cancel_compute'):
            self.cancel_compute()
        self._widget_core.cleanup()
        super().cleanup()
