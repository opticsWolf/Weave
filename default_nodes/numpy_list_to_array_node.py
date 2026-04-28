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
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
)

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore, PortRole
from weave.node import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("ListToArrayNode")


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# =============================================================================
# ListNormalizer  --  standalone helper, no Qt dependency
# =============================================================================

class ListNormalizer:
    """
    Inspects and trims a (possibly ragged) nested Python list so it can
    be safely converted to a ``numpy.ndarray``.
    """

    @classmethod
    def analyse(cls, data: Any) -> Tuple[Optional[Tuple[int, ...]], str, List[str]]:
        messages: List[str] = []
        if not isinstance(data, list):
            messages.append(f"Input is {type(data).__name__}, not list -- cannot convert")
            return None, "float64", messages
        if len(data) == 0:
            messages.append("Input list is empty -- cannot convert")
            return None, "float64", messages

        shape: List[int] = []
        if not cls._walk_shape(data, shape, messages, depth=0):
            return None, "float64", messages
        if not shape:
            messages.append("Could not determine array shape -- cannot convert")
            return None, "float64", messages

        target = tuple(shape)
        dtype_str, dtype_msgs = cls._detect_dtype(data, target)
        messages.extend(dtype_msgs)
        return target, dtype_str, messages

    @classmethod
    def trim(cls, data: Any, target_shape: Tuple[int, ...], depth: int = 0) -> Any:
        if not isinstance(data, list) or depth >= len(target_shape):
            return data
        n = target_shape[depth]
        return [cls.trim(item, target_shape, depth + 1) for item in data[:n]]

    @classmethod
    def convert(cls, data: Any, dtype_override: Optional[str] = None) -> Tuple[Optional[np.ndarray], List[str]]:
        shape, detected, messages = cls.analyse(data)
        if shape is None:
            return None, messages

        dtype = (dtype_override if dtype_override and dtype_override != "auto" else detected)
        trimmed = cls.trim(data, shape)

        try:
            arr = np.array(trimmed, dtype=dtype)
            messages.append(f"Converted to ndarray: shape={arr.shape}  dtype={arr.dtype}")
            return arr, messages
        except Exception as exc:
            messages.append(f"numpy conversion failed: {exc}")
            return None, messages

    @classmethod
    def _walk_shape(cls, node: Any, shape: List[int], messages: List[str], depth: int) -> bool:
        if not isinstance(node, list):
            return True
        n = len(node)
        if n == 0:
            if len(shape) <= depth:
                shape.append(0)
            elif shape[depth] != 0:
                messages.append(f"Depth {depth}: empty sub-list encountered -- trimming dimension from {shape[depth]} to 0")
                shape[depth] = 0
            return True

        if len(shape) <= depth:
            shape.append(n)
        elif n < shape[depth]:
            messages.append(f"Depth {depth}: sub-list length {n} < current minimum {shape[depth]} -- trimming to {n}")
            shape[depth] = n
        elif n > shape[depth]:
            messages.append(f"Depth {depth}: sub-list length {n} > current minimum {shape[depth]} -- will clip to {shape[depth]}")

        safe_n = shape[depth]
        list_children = [c for c in node[:safe_n] if isinstance(c, list)]
        other_children = [c for c in node[:safe_n] if not isinstance(c, list)]

        if list_children and other_children:
            messages.append(f"Depth {depth}: {len(other_children)} scalar(s) mixed with {len(list_children)} sub-list(s)")

        for child in list_children:
            if not cls._walk_shape(child, shape, messages, depth + 1):
                return False
        return True

    @classmethod
    def _detect_dtype(cls, data: Any, shape: Tuple[int, ...]) -> Tuple[str, List[str]]:
        messages: List[str] = []
        has_complex = has_float = has_int = has_bool = False
        invalid: List[str] = []

        def _scan(node: Any, d: int) -> None:
            nonlocal has_complex, has_float, has_int, has_bool
            if isinstance(node, list):
                if d < len(shape):
                    for item in node[:shape[d]]:
                        _scan(item, d + 1)
                return
            if isinstance(node, bool):
                has_bool = True
            elif isinstance(node, complex):
                has_complex = True
            elif isinstance(node, float):
                has_float = True
            elif isinstance(node, int):
                has_int = True
            elif isinstance(node, np.generic):
                if np.issubdtype(type(node), np.complexfloating): has_complex = True
                elif np.issubdtype(type(node), np.floating): has_float = True
                elif np.issubdtype(type(node), np.integer): has_int = True
                elif np.issubdtype(type(node), np.bool_): has_bool = True
                else: invalid.append(repr(node)[:30])
            else:
                invalid.append(f"{type(node).__name__}({repr(node)[:20]})")

        _scan(data, 0)
        if invalid:
            sample = ", ".join(invalid[:3])
            extra = f" and {len(invalid) - 3} more" if len(invalid) > 3 else ""
            messages.append(f"Non-numeric leaf values: {sample}{extra}")

        if has_complex: return "complex128", messages
        if has_float: return "float64", messages
        if has_int: return "int64", messages
        if has_bool: return "bool", messages
        return "float64", messages


# =============================================================================
# Dtype combo options
# =============================================================================

_DTYPE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("Auto",       "auto"),
    ("float64",    "float64"),
    ("float32",    "float32"),
    ("int64",      "int64"),
    ("int32",      "int32"),
    ("complex128", "complex128"),
    ("bool",       "bool"),
)


# =============================================================================
# ListToArrayNode
# =============================================================================

@register_node
class ListToArrayNode(ThreadedNode):
    array_changed = Signal(object)

    node_class: ClassVar[str] = "Numpy"
    node_subclass: ClassVar[str] = "Converter"
    node_name: ClassVar[Optional[str]] = "List to Array"
    node_description: ClassVar[Optional[str]] = (
        "Converts a Python list (or nested list) to a NumPy ndarray, "
        "trimming ragged dimensions automatically"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "list", "array", "converter", "ndarray", "nested", "primitive"
    ]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "List to Array", **kwargs: Any) -> None:
        # 1. Super init
        super().__init__(title=title, **kwargs)

        # 2. Add ports
        self.add_input("list", datatype="list")
        self.add_output("array", datatype="ndarray")

        # 3. Layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # 4. Widgets + Registration
        self._combo_dtype = QComboBox()
        for label, val in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label, userData=val)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role=PortRole.INTERNAL, datatype="str", default="auto",
            add_to_layout=False,
        )

        form.addRow(_make_separator())
        self._label_status = QLabel("waiting for input")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        self._label_status.setMinimumWidth(160)
        form.addRow(self._label_status)

        # 5. Wire signals (both required by framework spec)
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # 6. Mount + Patch
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

        # Thread-safe UI state placeholder
        self._pending_status: Optional[str] = None

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        # No structural UI changes driven by this widget; framework handles re-eval.
        pass

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Runs on worker thread. Zero Qt/GUI access."""
        if self.is_compute_cancelled():
            return {"array": np.array([], dtype=np.float64)}

        try:
            raw = inputs.get("list")
            # INTERNAL widgets are auto-snapshot by WidgetCore into `inputs`
            dtype_override = inputs.get("dtype", "auto")

            # Normalize in case framework passes index or label text instead of userData
            if isinstance(dtype_override, int):
                idx = max(0, min(dtype_override, len(_DTYPE_OPTIONS) - 1))
                dtype_override = _DTYPE_OPTIONS[idx][1]
            elif dtype_override not in dict(_DTYPE_OPTIONS).values():
                dtype_override = "auto"

            if raw is None:
                return {"array": np.array([], dtype=np.float64)}

            # Fast path for already-converted arrays
            if isinstance(raw, np.ndarray):
                target_dtype = dtype_override if dtype_override != "auto" else str(raw.dtype)
                try:
                    arr = raw.astype(target_dtype, copy=False)
                    self._pending_status = f"shape: {arr.shape}\ndtype: {arr.dtype}"
                    return {"array": arr}
                except Exception as exc:
                    log.warning("dtype cast failed (%s)", exc)
                    self._pending_status = f"dtype cast failed: {exc}"
                    return {"array": raw}

            if not isinstance(raw, list):
                log.warning("received %s instead of list or ndarray", type(raw).__name__)
                self._pending_status = f"error: expected list/ndarray, got {type(raw).__name__}"
                return {"array": np.array([], dtype=np.float64)}

            # Full normalization pipeline
            arr, messages = ListNormalizer.convert(raw, dtype_override)
            for msg in messages:
                lower = msg.lower()
                if any(kw in lower for kw in ("error", "fail", "cannot")):
                    log.warning("ListToArrayNode: %s", msg)
                elif any(kw in lower for kw in ("trim", "clip", "mismatch", "ragged", "non-numeric", "mixed", "empty")):
                    log.info("ListToArrayNode: %s", msg)
                else:
                    log.debug("ListToArrayNode: %s", msg)

            if arr is not None:
                self._pending_status = f"shape: {arr.shape}\ndtype: {arr.dtype}"
                return {"array": arr}
            else:
                self._pending_status = "conversion failed\n(see log)"
                return {"array": np.array([], dtype=np.float64)}

        except Exception as exc:
            log.error("Exception in compute: %s", exc)
            self._pending_status = f"error: {exc}"
            return {"array": np.array([], dtype=np.float64)}

    def on_evaluate_finished(self) -> None:
        """Flush status label and emit signal on main thread."""
        try:
            if hasattr(self, '_pending_status') and self._pending_status is not None:
                self._label_status.setText(self._pending_status)
                self._pending_status = None

            result = self._get_cached_value("array")
            if result is not None:
                self.array_changed.emit(result)
        except Exception as exc:
            log.error("Exception in on_evaluate_finished: %s", exc)
        finally:
            super().on_evaluate_finished()

    def cleanup(self) -> None:
        self.cancel_compute()
        if hasattr(self, '_pending_status'):
            self._pending_status = None
        super().cleanup()
