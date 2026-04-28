# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_array_node.py
-------------------
Advanced multidimensional NumPy array generator node with dynamic
per-dimension controls.

Provided node
-------------
``NumpyArrayNode``
    Source node that emits a ``numpy.ndarray``.  A *Dims* spinbox
    controls the number of dimensions (1–8).  For every dimension an
    additional ``QSpinBox`` row is inserted into the node body and
    **registered with WidgetCore** (``register_widget``), plus a
    matching auto-disable input port is added so upstream nodes can
    drive individual axis sizes. 

    Upgraded to ThreadedNode to ensure high-dimensional tensor allocations
    do not freeze the UI. Dynamic port generation accurately waits for
    the main thread synchronisation cycle.
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
from weave.widgetcore import WidgetCore
from weave.widgetcore.widgetcore_port_models import PortRole
from weave.node.node_enums import VerticalSizePolicy

from weave.logger import get_logger

log = get_logger("NumpyArrayNode")


# ── Module-level constants ─────────────────────────────────────────────────

_FILL_MODES: Tuple[Tuple[str, str], ...] = (
    ("Zeros",         "zeros"),
    ("Ones",          "ones"),
    ("Full",          "full"),
    ("Rand Uniform",  "random_uniform"),
    ("Rand Normal",   "random_normal"),
    ("Eye / Identity","eye"),
)

_DTYPE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("float64",    "float64"),
    ("float32",    "float32"),
    ("int64",      "int64"),
    ("int32",      "int32"),
    ("complex128", "complex128"),
    ("bool",       "bool"),
)


# ══════════════════════════════════════════════════════════════════════════════
# NumpyArrayNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpyArrayNode(ThreadedNode):
    """
    Advanced multidimensional NumPy array generator.

    Type: Threaded (propagates downstream automatically, evaluated in background).
    """

    MAX_DIMS: ClassVar[int] = 8
    _DEFAULT_DIM_SIZE: ClassVar[int] = 3

    array_changed = Signal(object)

    node_class:       ClassVar[str]            = "Numpy"
    node_subclass:    ClassVar[str]            = "Generator"
    node_name:        ClassVar[Optional[str]]  = "Numpy Array"
    node_description: ClassVar[Optional[str]]  = (
        "Builds a multidimensional NumPy array with dynamic per-dimension controls"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "array", "ndarray", "matrix",
        "generator", "multidimensional", "primitive",
    ]

    vertical_size_policy = VerticalSizePolicy.FIT

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(
        self,
        title: str = "Numpy Array",
        initial_dims: int = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        # Single output — always present
        self.add_output("array", "ndarray")

        # ── Shared QFormLayout ────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Dims spinbox (Now bidirectional to allow input port)
        self._spin_dims = QSpinBox()
        self._spin_dims.setRange(1, self.MAX_DIMS)
        self._spin_dims.setValue(initial_dims)
        self._spin_dims.setMinimumWidth(60)
        form.addRow("Dims:", self._spin_dims)
        self._widget_core.register_widget(
            "dims", self._spin_dims,
            role=PortRole.BIDIRECTIONAL, datatype="int", default=1,
            add_to_layout=False,
        )

        # Add the input port for Dims
        self.add_input("dims", "int")
        self.inputs[-1]._auto_disable = True  # Allows manual override when disconnected

        # ── Separator ─────────────────────────────────────────────────
        form.addRow(self._make_separator())

        # ── Fill mode combobox (internal) ─────────────────────────────
        self._combo_fill = QComboBox()
        for label, value in _FILL_MODES:
            self._combo_fill.addItem(label, userData=value)
        form.addRow("Fill:", self._combo_fill)
        self._widget_core.register_widget(
            "fill_type", self._combo_fill,
            role=PortRole.INTERNAL, datatype="string", default="zeros",
            add_to_layout=False,
        )

        # ── Fill value spinbox (internal) — visible only when Fill=Full
        self._label_fill_val = QLabel("Value:")
        self._spin_fill_val = QDoubleSpinBox()
        self._spin_fill_val.setRange(-1e9, 1e9)
        self._spin_fill_val.setValue(0.0)
        self._spin_fill_val.setDecimals(6)
        self._spin_fill_val.setMinimumWidth(100)
        form.addRow(self._label_fill_val, self._spin_fill_val)
        self._widget_core.register_widget(
            "fill_value", self._spin_fill_val,
            role=PortRole.INTERNAL, datatype="float", default=0.0,
            add_to_layout=False,
        )

        # ── Dtype combobox (internal) ─────────────────────────────────
        self._combo_dtype = QComboBox()
        for label, value in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label, userData=value)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role=PortRole.INTERNAL, datatype="string", default="float64",
            add_to_layout=False,
        )

        # ── Separator before dynamic dim rows ─────────────────────────
        form.addRow(self._make_separator())

        # ── Central dispatch: all WidgetCore value changes ────────────
        self._widget_core.value_changed.connect(self._on_wc_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # ── Dynamic dimension state ───────────────────────────────────
        self._current_dims: int = 0

        # ── Finalise widget tree ──────────────────────────────────────
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

        # Build initial dim rows (block WidgetCore signal to avoid
        # mid-init compute / panel reactions).
        with self._widget_core.suppress_signals():
            self._set_dims(initial_dims)

        # Sync fill-value visibility for the initial fill mode.
        self._sync_fill_value_visibility()

        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _make_separator() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep

    def _get_fill_mode(self) -> str:
        idx = self._combo_fill.currentIndex()
        return _FILL_MODES[idx][1] if 0 <= idx < len(_FILL_MODES) else "zeros"

    def _sync_fill_value_visibility(self) -> None:
        """Show the fill-value row only when Fill = Full."""
        is_full = self._get_fill_mode() == "full"
        self._label_fill_val.setVisible(is_full)
        self._spin_fill_val.setVisible(is_full)
        
        # Notify the dock system that dimensions and visibilities have shifted
        if hasattr(self._widget_core, 'resume_content_notify'):
            self._widget_core.resume_content_notify(True)

    # ── Dynamic dimension management ──────────────────────────────────────────

    def _set_dims(self, new_dims: int) -> None:
        """Grow or shrink the dim-widget/port set to *new_dims*."""
        new_dims = max(1, min(self.MAX_DIMS, new_dims))
        old_dims = self._current_dims

        if new_dims > old_dims:
            for d in range(old_dims, new_dims):
                self._add_dim(d)

        elif new_dims < old_dims:
            self._remove_dims(new_dims, old_dims)

        self._current_dims = new_dims

    def _add_dim(self, d: int) -> None:
        """Add a ``QSpinBox`` row for dimension *d*, register with WidgetCore,
        and create the matching input port."""
        form: QFormLayout = self._widget_core.layout()
        port_name = f"dim_{d}"

        spin = QSpinBox()
        spin.setRange(1, 9_999)
        spin.setValue(self._DEFAULT_DIM_SIZE)
        spin.setMinimumWidth(80)
        form.addRow(f"Dim {d}:", spin)

        self._widget_core.register_widget(
            port_name, spin,
            role=PortRole.BIDIRECTIONAL, datatype="int",
            default=self._DEFAULT_DIM_SIZE,
            add_to_layout=False,
        )

        self.add_input(port_name, "int")
        self.inputs[-1]._auto_disable = True

    def _remove_dims(self, from_idx: int, to_idx: int) -> None:
        """Remove dim widgets from *to_idx - 1* down to *from_idx* (LIFO)."""
        form: QFormLayout = self._widget_core.layout()

        for d in range(to_idx - 1, from_idx - 1, -1):
            port_name = f"dim_{d}"

            spin = self._widget_core.unregister_widget(port_name)
            if spin is not None:
                try:
                    form.removeRow(spin)
                except Exception as exc:
                    log.warning(
                        f"NumpyArrayNode: removeRow failed for dim {d}: {exc}"
                    )

        port_names = [f"dim_{d}" for d in range(from_idx, to_idx)]
        removed = self.remove_ports(port_names, is_output=False)
        if removed != len(port_names):
            log.warning(
                f"NumpyArrayNode._remove_dims: expected {len(port_names)} "
                f"removals, got {removed}"
            )

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_wc_value_changed(self, port_name: str) -> None:
        """Central handler for internal user UI changes."""
        try:
            if port_name == "dims":
                val = self._widget_core.get_port_value("dims")
                if val is not None:
                    # Explicit user interactions safely update GUI layouts directly
                    self._set_dims(int(val))
                    if hasattr(self._widget_core, 'refresh_widget_palettes'):
                        self._widget_core.refresh_widget_palettes()
            elif port_name == "fill_type":
                self._sync_fill_value_visibility()

            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode._on_wc_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port_name: str, value: Any) -> None:
        """Handle programmatic widget changes (like Undo/Redo)."""
        try:
            if port_name == "dims":
                self._set_dims(int(value))
                if hasattr(self._widget_core, 'refresh_widget_palettes'):
                    self._widget_core.refresh_widget_palettes()
            elif port_name == "fill_type":
                self._sync_fill_value_visibility()
                
            # RULE 7.3: DO NOT call self.on_ui_change() here.
            # Programmatic writes (Undo/Redo) automatically manage evaluation logic post-restoration.
        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode._on_port_value_written: {exc}")

    # ── Computation (Background Worker) ───────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Build the ndarray from current state.

        Executes on a background QRunnable. The `inputs` dict is pre-merged
        by ThreadedNode to contain upstream data falling back to local widgets.
        No Qt elements are accessed here to guarantee thread safety.
        """
        if self.is_compute_cancelled():
            return {"array": np.array([], dtype=np.float64)}

        try:
            # ── Determine Rank ──────────────────────────────────────────
            dims_val = inputs.get("dims")
            new_dims = max(1, min(self.MAX_DIMS, int(dims_val) if dims_val is not None else 1))

            # ── Shape ─────────────────────────────────────────────────
            shape: List[int] = []
            for d in range(new_dims):
                size_val = inputs.get(f"dim_{d}")
                size = int(size_val) if size_val is not None else self._DEFAULT_DIM_SIZE
                shape.append(max(1, size))

            if not shape:
                shape = [1]

            # ── Parameters ────────────────────────────────────────────
            dtype = np.dtype(inputs.get("dtype", "float64"))
            mode = inputs.get("fill_type", "zeros")

            # ── Processing ────────────────────────────────────────────
            if mode == "zeros":
                arr = np.zeros(shape, dtype=dtype)
            elif mode == "ones":
                arr = np.ones(shape, dtype=dtype)
            elif mode == "full":
                fill_val = float(inputs.get("fill_value", 0.0))
                arr = np.full(shape, fill_val, dtype=dtype)
            elif mode == "random_uniform":
                arr = np.random.uniform(0.0, 1.0, shape).astype(dtype)
            elif mode == "random_normal":
                arr = np.random.normal(0.0, 1.0, shape).astype(dtype)
            elif mode == "eye":
                rows = shape[0]
                cols = shape[1] if len(shape) > 1 else shape[0]
                eye_2d = np.eye(rows, cols, dtype=dtype)
                if len(shape) == 1:
                    arr = np.ones(shape, dtype=dtype)
                elif len(shape) == 2:
                    arr = eye_2d
                else:
                    arr = np.zeros(shape, dtype=dtype)
                    idx: List[Any] = [slice(None), slice(None)]
                    idx += [0] * (len(shape) - 2)
                    arr[tuple(idx)] = eye_2d
            else:
                arr = np.zeros(shape, dtype=dtype)

            if self.is_compute_cancelled():
                return {"array": np.array([], dtype=np.float64)}

            # Returns the array and a marker for the main thread to resync UI ports dynamically
            return {"array": arr, "_sync_dims": new_dims}

        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode.compute: {exc}")
            return {"array": np.array([], dtype=np.float64)}

    # ── Main Thread Result Hook ───────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Main thread callback bridging threaded output back to UI state."""
        # Detect if dimensions changed entirely via upstream traces
        sync_dims = self._get_cached_value("_sync_dims")
        if sync_dims is not None and sync_dims != self._current_dims:
            with self._widget_core.suppress_signals():
                self._set_dims(sync_dims)
                if hasattr(self._widget_core, 'refresh_widget_palettes'):
                    self._widget_core.refresh_widget_palettes()

        result = self._get_cached_value("array")
        if result is not None:
            self.array_changed.emit(result)
            
        super().on_evaluate_finished()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["numpy_array_node"] = {
            "num_dims":   self._current_dims,
            "dim_sizes":  {
                str(d): self._widget_core.get_port_value(f"dim_{d}")
                for d in range(self._current_dims)
            },
        }
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        super().restore_state(state)

        ns = state.get("numpy_array_node", {})
        num_dims = ns.get("num_dims", 1)

        # ── 1. Remove stale dim ports from __init__ + base restore ────
        stale_dim_ports = [p.name for p in self.inputs if p.name.startswith("dim_")]
        if stale_dim_ports:
            self.remove_ports(stale_dim_ports)

        # ── 2. Tear down stale dim widget bindings from __init__ ──────
        form: QFormLayout = self._widget_core.layout()
        for d in range(self._current_dims):
            port_name = f"dim_{d}"
            spin = self._widget_core.unregister_widget(port_name)
            if spin is not None:
                try:
                    form.removeRow(spin)
                except Exception:
                    pass
        self._current_dims = 0

        # ── 3. Rebuild from scratch ───────────────────────────────────
        with self._widget_core.suppress_signals():
            self._widget_core.set_port_value("dims", num_dims)
            self._set_dims(num_dims)

        # ── 4. Restore individual dim-size values via WidgetCore ──────
        for d_str, size in ns.get("dim_sizes", {}).items():
            port_name = f"dim_{int(d_str)}"
            if self._widget_core.has_binding(port_name):
                self._widget_core.set_port_value(port_name, int(size))

        self._sync_fill_value_visibility()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        if hasattr(self, 'cancel_compute'):
            self.cancel_compute()
        super().cleanup()