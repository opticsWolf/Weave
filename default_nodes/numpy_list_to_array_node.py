# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0[Canvas] ERROR Failed to spawn CountdownTimerNode: 'CountdownTimerNode' object has no attribute 'set_vertical_policy'

numpy_list_to_array_node.py
----------------------
Converts a Python list (or nested list) into a NumPy ndarray, trimming
ragged dimensions as needed to produce a fully rectangular result.

Provided classes
----------------
``ListNormalizer``
    Standalone helper that inspects, trims and converts any nested
    Python list into a numpy ndarray.  Can be used independently of
    the node graph.

``ListToArrayNode``
    Active node that wraps ``ListNormalizer``.  Accepts a ``list``
    on the *list* input port and emits a ``ndarray`` on the *array*
    output port.  All inspection decisions are reported to the Weave
    logger.

Conversion pipeline
-------------------
1. **Type check** — input must be a ``list`` *or* a ``numpy.ndarray``.
   If an ``ndarray`` is received the dtype-override combo is applied
   directly (``array.astype(dtype)``); the shape-analysis and trim
   steps are skipped.  Any other type logs a WARNING and emits an
   empty ``float64`` array.
2. **Shape analysis** (``ListNormalizer.analyse``) — walks all nesting
   levels and records the *minimum* length seen at each depth.  Any
   mismatch (ragged siblings) is logged at INFO.
3. **Trim** (``ListNormalizer.trim``) — clips every sub-list at every
   level to the target shape.
4. **Dtype detection** — scans every reachable leaf value and picks the
   tightest dtype: ``bool`` < ``int64`` < ``float64`` < ``complex128``.
   A *Dtype* combo in the node body can override the auto-detected dtype.
5. **Conversion** — ``numpy.array(trimmed, dtype=dtype)``.
6. Any non-numeric leaves are reported at WARNING before conversion is
   attempted.

Dtype override options
----------------------
Auto        Use the tightest dtype detected from the leaf values.
float64     Force 64-bit float.
float32     Force 32-bit float.
int64       Force 64-bit integer (complex/float values will be truncated).
int32       Force 32-bit integer.
complex128  Force 128-bit complex.
bool        Force boolean (non-zero → True).
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
from weave.widgetcore import WidgetCore

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

    All three public methods return a ``messages`` list of human-readable
    strings describing every decision made (shape mismatches, dtype
    selection, non-numeric leaves, etc.).  Callers are responsible for
    forwarding those messages to whatever logging system they use.

    Usage::

        arr, messages = ListNormalizer.convert(my_list)
        for msg in messages:
            log.info(msg)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def analyse(
        cls, data: Any
    ) -> Tuple[Optional[Tuple[int, ...]], str, List[str]]:
        """
        Inspect *data* and return ``(target_shape, detected_dtype, messages)``.

        *target_shape* is ``None`` when conversion is impossible (not a
        list, empty, or structurally invalid).

        *detected_dtype* is the tightest NumPy dtype string compatible
        with all reachable leaf values:
        ``"bool"`` < ``"int64"`` < ``"float64"`` < ``"complex128"``.
        """
        messages: List[str] = []

        if not isinstance(data, list):
            messages.append(
                f"Input is {type(data).__name__}, not list -- cannot convert"
            )
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
    def trim(
        cls, data: Any, target_shape: Tuple[int, ...], depth: int = 0
    ) -> Any:
        """
        Recursively clip every nesting level of *data* to *target_shape*.

        Returns the trimmed structure without modifying *data* in-place.
        """
        if not isinstance(data, list) or depth >= len(target_shape):
            return data
        n = target_shape[depth]
        return [cls.trim(item, target_shape, depth + 1) for item in data[:n]]

    @classmethod
    def convert(
        cls, data: Any, dtype_override: Optional[str] = None
    ) -> Tuple[Optional[np.ndarray], List[str]]:
        """
        Full pipeline: analyse → trim → convert.

        Parameters
        ----------
        data : Any
            The input to convert.
        dtype_override : str, optional
            If ``None`` or ``"auto"``, the detected dtype is used.
            Otherwise, force this NumPy dtype string (e.g. ``"float32"``).

        Returns
        -------
        (ndarray | None, messages)
            *ndarray* is ``None`` when conversion fails.
            *messages* always contains a full account of every decision.
        """
        shape, detected, messages = cls.analyse(data)
        if shape is None:
            return None, messages

        dtype = (
            dtype_override
            if dtype_override and dtype_override != "auto"
            else detected
        )
        trimmed = cls.trim(data, shape)

        try:
            arr = np.array(trimmed, dtype=dtype)
            messages.append(
                f"Converted to ndarray: shape={arr.shape}  dtype={arr.dtype}"
            )
            return arr, messages
        except Exception as exc:
            messages.append(f"numpy conversion failed: {exc}")
            return None, messages

    # ------------------------------------------------------------------
    # Shape walking
    # ------------------------------------------------------------------

    @classmethod
    def _walk_shape(
        cls,
        node: Any,
        shape: List[int],
        messages: List[str],
        depth: int,
    ) -> bool:
        """
        Recursively determine the minimum-safe shape.

        ``shape[depth]`` stores the *minimum* length seen across all
        sub-lists at this depth.  Every time a shorter sibling is
        encountered the stored value is reduced and a message is appended.
        Longer siblings are silently clipped (message still appended for
        transparency).
        """
        if not isinstance(node, list):
            return True   # leaf value -- no further dimensions

        n = len(node)

        if n == 0:
            # An empty sub-list forces this dimension to 0.
            if len(shape) <= depth:
                shape.append(0)
            elif shape[depth] != 0:
                messages.append(
                    f"Depth {depth}: empty sub-list encountered -- "
                    f"trimming dimension from {shape[depth]} to 0"
                )
                shape[depth] = 0
            return True

        if len(shape) <= depth:
            # First time visiting this depth -- record length.
            shape.append(n)
        elif n < shape[depth]:
            messages.append(
                f"Depth {depth}: sub-list length {n} < current minimum "
                f"{shape[depth]} -- trimming to {n}"
            )
            shape[depth] = n
        elif n > shape[depth]:
            messages.append(
                f"Depth {depth}: sub-list length {n} > current minimum "
                f"{shape[depth]} -- will clip to {shape[depth]}"
            )
            # shape[depth] is already the minimum; leave it unchanged.

        # Classify children within the safe slice.
        safe_n         = shape[depth]
        list_children  = [c for c in node[:safe_n] if isinstance(c, list)]
        other_children = [c for c in node[:safe_n] if not isinstance(c, list)]

        if list_children and other_children:
            messages.append(
                f"Depth {depth}: {len(other_children)} scalar(s) mixed with "
                f"{len(list_children)} sub-list(s) -- scalars are treated "
                "as leaves and cannot contribute further dimensions"
            )

        for child in list_children:
            if not cls._walk_shape(child, shape, messages, depth + 1):
                return False

        return True

    # ------------------------------------------------------------------
    # Dtype detection
    # ------------------------------------------------------------------

    @classmethod
    def _detect_dtype(
        cls, data: Any, shape: Tuple[int, ...]
    ) -> Tuple[str, List[str]]:
        """
        Scan every leaf value reachable within *shape* and return the
        tightest compatible NumPy dtype string.

        Precedence: ``complex128`` > ``float64`` > ``int64`` > ``bool``
        """
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
            # Leaf
            if isinstance(node, bool):
                has_bool = True
            elif isinstance(node, complex):
                has_complex = True
            elif isinstance(node, float):
                has_float = True
            elif isinstance(node, int):
                has_int = True
            elif isinstance(node, np.generic):
                if np.issubdtype(type(node), np.complexfloating):
                    has_complex = True
                elif np.issubdtype(type(node), np.floating):
                    has_float = True
                elif np.issubdtype(type(node), np.integer):
                    has_int = True
                elif np.issubdtype(type(node), np.bool_):
                    has_bool = True
                else:
                    invalid.append(repr(node)[:30])
            else:
                invalid.append(f"{type(node).__name__}({repr(node)[:20]})")

        _scan(data, 0)

        if invalid:
            sample = ", ".join(invalid[:3])
            extra  = f" and {len(invalid) - 3} more" if len(invalid) > 3 else ""
            messages.append(
                f"Non-numeric leaf values: {sample}{extra} -- "
                "conversion may produce object dtype or raise an error"
            )

        if has_complex:
            return "complex128", messages
        if has_float:
            return "float64", messages
        if has_int:
            return "int64", messages
        if has_bool:
            return "bool", messages
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
    """
    Converts a Python list (or nested list) into a NumPy ndarray.

    Type: Threaded (compute() runs on QThreadPool; propagates downstream on
    any input list or dtype setting change).

    Inputs
    ------
    list : list | ndarray
        The source list.  Nested lists are fully supported.  Ragged
        dimensions are trimmed to the shortest common length at each
        level.  If an ``ndarray`` is supplied instead, the node skips
        shape analysis and applies only the dtype conversion.

    Outputs
    -------
    array : ndarray
        The converted array.  An empty ``float64`` array is emitted
        when conversion fails.

    Parameters
    ----------
    title : str
        Node title (default ``"List to Array"``).
    """

    array_changed = Signal(object)   # emits ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Converter"
    node_name:        ClassVar[Optional[str]] = "List to Array"
    node_description: ClassVar[Optional[str]] = (
        "Converts a Python list (or nested list) to a NumPy ndarray, "
        "trimming ragged dimensions automatically"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "list", "array", "converter", "ndarray",
        "nested", "primitive",
    ]

    def __init__(self, title: str = "List to Array", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        self.add_input("list", "list")
        self.add_output("array", "ndarray")

        # ── Form layout ───────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Dtype override combo (internal) ───────────────────────────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role="internal", datatype="string", default="auto",
            add_to_layout=False,
        )

        # ── Status display ────────────────────────────────────────────
        form.addRow(_make_separator())
        self._label_status = QLabel("waiting for input")
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

    # ── Widget snapshot (main thread → worker thread) ─────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture the dtype combo selection before worker dispatch."""
        idx = self._combo_dtype.currentIndex()
        return {
            "_ui_dtype": (
                _DTYPE_OPTIONS[idx][1] if 0 <= idx < len(_DTYPE_OPTIONS) else "auto"
            )
        }

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, _port_name: str) -> None:
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error(
                "Exception in ListToArrayNode._on_core_changed: %s", exc
            )

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run ListNormalizer on the incoming list, forward every message to
        the Weave logger, and return the resulting ndarray.

        Runs on the worker thread — no Qt widget access.  The dtype
        override arrives pre-snapshotted under ``_ui_dtype``.
        """
        try:
            raw           = inputs.get("list")
            dtype_override = inputs.get("_ui_dtype", "auto")

            # ── Not connected ─────────────────────────────────────────
            # None means no upstream trace yet — return empty silently.
            if raw is None:
                return {"array": np.array([], dtype=np.float64)}

            # ── Type guard ────────────────────────────────────────────
            if isinstance(raw, np.ndarray):
                # Input is already an array — just apply dtype conversion.
                target_dtype = (
                    dtype_override
                    if dtype_override and dtype_override != "auto"
                    else str(raw.dtype)
                )
                try:
                    arr = raw.astype(target_dtype, copy=False)
                    log.debug(
                        "ListToArrayNode: received ndarray -- "
                        "applied dtype=%s (was %s)",
                        arr.dtype, raw.dtype,
                    )
                    shape_str = "x".join(str(d) for d in arr.shape)
                    self._pending_status = (
                        f"shape: ({shape_str})\ndtype: {arr.dtype}"
                    )
                    return {"array": arr}
                except Exception as exc:
                    log.warning(
                        "ListToArrayNode: dtype cast failed (%s) -- "
                        "emitting original array unchanged",
                        exc,
                    )
                    self._pending_status = f"dtype cast failed: {exc}"
                    return {"array": raw}

            if not isinstance(raw, list):
                log.warning(
                    "ListToArrayNode: received %s instead of list or ndarray"
                    " -- emitting empty array",
                    type(raw).__name__,
                )
                self._pending_status = (
                    f"error: expected list or ndarray, got {type(raw).__name__}"
                )
                return {"array": np.array([], dtype=np.float64)}

            # ── Convert via ListNormalizer ─────────────────────────────
            arr, messages = ListNormalizer.convert(raw, dtype_override)

            # Forward every message to the logger at the appropriate level
            for msg in messages:
                lower = msg.lower()
                if "error" in lower or "fail" in lower or "cannot" in lower:
                    log.warning("ListToArrayNode: %s", msg)
                elif (
                    "trim" in lower
                    or "clip" in lower
                    or "mismatch" in lower
                    or "ragged" in lower
                    or "non-numeric" in lower
                    or "mixed" in lower
                    or "empty" in lower
                ):
                    log.info("ListToArrayNode: %s", msg)
                else:
                    log.debug("ListToArrayNode: %s", msg)

            # ── Build status text ─────────────────────────────────────
            if arr is not None:
                shape_str = "x".join(str(d) for d in arr.shape)
                self._pending_status = (
                    f"shape: ({shape_str})\ndtype: {arr.dtype}"
                )
                result = arr
            else:
                self._pending_status = "conversion failed\n(see log)"
                result = np.array([], dtype=np.float64)

            return {"array": result}

        except Exception as exc:
            log.error("Exception in ListToArrayNode.compute: %s", exc)
            self._pending_status = f"error: {exc}"
            return {"array": np.array([], dtype=np.float64)}

    # ── Post-evaluation UI flush ──────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Flush the status label and emit array_changed on the main thread."""
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
                "Exception in ListToArrayNode.on_evaluate_finished: %s", exc
            )
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._pending_status = None
        self._widget_core.cleanup()
        super().cleanup()
