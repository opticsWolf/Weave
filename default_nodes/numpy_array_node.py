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
    additional ``QSpinBox`` row is inserted into the node body and a
    matching auto-disable input port is added to the graph so that
    upstream nodes can drive individual axis sizes.  Reducing the
    dimensionality removes the excess rows and ports in reverse order.

    Additional controls
    ~~~~~~~~~~~~~~~~~~~
    *Fill* — how the array values are populated:

    =========== =================================================
    Zeros       All elements are 0 (default).
    Ones        All elements are 1.
    Full        All elements equal the *Value* spinbox.
    Rand Uniform  Uniform [0, 1) random values.
    Rand Normal   Standard-normal (μ=0, σ=1) random values.
    Eye / Identity  Identity matrix (2-D); zeros elsewhere for
                    higher-rank tensors with a unit 2-D slice at
                    index [..., 0, 0].
    =========== =================================================

    *Dtype* — output array dtype (float64, float32, int64, int32,
    complex128, bool).

    *Value* — fill constant; visible only when Fill = *Full*.

Dynamic behaviour
-----------------
* Increasing *Dims* → appends ``QSpinBox`` rows labelled
  ``Dim 0:``, ``Dim 1:``, … and registers the matching input ports
  ``dim_0``, ``dim_1``, … with ``_auto_disable = True``.
* Decreasing *Dims* → tears down widget rows first (signals
  disconnected, form rows deleted), then batch-removes all excess
  ports via ``remove_ports()`` for a single geometry rebuild.
  The ``VerticalSizePolicy.FIT`` class attribute ensures the node
  shrinks to match the reduced content.
* Each dim port accepts an upstream integer; when connected the
  matching spinbox is greyed out automatically via the ``_auto_disable``
  mechanism in ``WidgetCore``.
* ``remove_port()`` handles the full disconnection chain: trace
  teardown, StyleManager unregistration, widget auto-disable cleanup,
  dataflow cache purge, and ``port_removed`` signal emission.

Serialisation
-------------
Static widget state (fill, dtype, value) is handled by WidgetCore's
built-in ``get_state()`` / ``set_state()`` because those widgets are
registered with ``role="internal"``.  The *dims* spinbox and all
dynamic dim-size spinboxes are serialised in the ``"numpy_array_node"``
sub-key added by the overriding ``get_state()`` / ``restore_state()``.

``restore_state`` follows the clean-slate pattern from
``MultiFloatOutputNode``: all stale ``dim_N`` ports that the base class
may have re-created from saved data are batch-removed via
``remove_ports()``, the matching widget rows are torn down and
``_current_dims`` is reset to 0, then ``_set_dims`` rebuilds everything
from scratch in a single geometry pass before individual spinbox values
are reapplied.
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

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import PortRole, WidgetCore
from weave.node.node_enums import VerticalSizePolicy

from weave.logger import get_logger

log = get_logger("NumpyArrayNode")


# ── Module-level constants (avoids repeated tuple construction) ───────────────

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
class NumpyArrayNode(ActiveNode):
    """
    Advanced multidimensional NumPy array generator.

    Type: Active (propagates downstream on any widget or port change).

    Inputs
    ------
    dim_0 : int, optional
        Size of axis 0.  Auto-disables the *Dim 0* spinbox when connected.
    dim_1 : int, optional
        Size of axis 1.  Added when *Dims* ≥ 2.
    …
    dim_N : int, optional
        Added dynamically as *Dims* is increased.

    Outputs
    -------
    array : ndarray
        The generated NumPy array with the configured shape, fill, and dtype.

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    initial_dims : int
        Number of dimensions on first creation (default ``1``).
    """

    MAX_DIMS: ClassVar[int] = 8
    _DEFAULT_DIM_SIZE: ClassVar[int] = 3

    array_changed = Signal(object)   # emits numpy.ndarray

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

    # FIT so the node shrinks when dims are removed
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

        # ── Dims spinbox ──────────────────────────────────────────────
        # Not registered with WidgetCore to avoid double value_changed
        # emissions.  Serialised manually in get_state / restore_state.
        self._spin_dims = QSpinBox()
        self._spin_dims.setRange(1, self.MAX_DIMS)
        self._spin_dims.setValue(initial_dims)
        self._spin_dims.setMinimumWidth(60)
        form.addRow("Dims:", self._spin_dims)

        # ── Separator ─────────────────────────────────────────────────
        form.addRow(self._make_separator())

        # ── Fill mode combobox (internal — serialised by WidgetCore) ──
        self._combo_fill = QComboBox()
        for label, _ in _FILL_MODES:
            self._combo_fill.addItem(label)
        form.addRow("Fill:", self._combo_fill)
        self._widget_core.register_widget(
            "fill_type", self._combo_fill,
            role="internal", datatype="string", default="zeros",
            add_to_layout=False,
        )
        # Extra direct connection — updates fill-value row visibility.
        # on_ui_change() is already called via WidgetCore → value_changed
        # → _on_static_changed, so _on_fill_type_changed does NOT call it.
        self._combo_fill.currentIndexChanged.connect(self._on_fill_type_changed)

        # ── Fill value spinbox (internal — serialised by WidgetCore) ──
        self._label_fill_val = QLabel("Value:")
        self._spin_fill_val = QDoubleSpinBox()
        self._spin_fill_val.setRange(-1e9, 1e9)
        self._spin_fill_val.setValue(0.0)
        self._spin_fill_val.setDecimals(6)
        self._spin_fill_val.setMinimumWidth(100)
        form.addRow(self._label_fill_val, self._spin_fill_val)
        self._widget_core.register_widget(
            "fill_value", self._spin_fill_val,
            role="internal", datatype="float", default=0.0,
            add_to_layout=False,
        )

        # ── Dtype combobox (internal — serialised by WidgetCore) ──────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role="internal", datatype="string", default="float64",
            add_to_layout=False,
        )

        # ── Separator before dynamic dim rows ─────────────────────────
        form.addRow(self._make_separator())

        # ── Connect WidgetCore signal (covers fill, value, dtype) ─────
        self._widget_core.value_changed.connect(self._on_static_changed)

        # ── Dynamic dimension state ───────────────────────────────────
        # Dict[dim_index -> QSpinBox].  Spinboxes are NOT registered with
        # WidgetCore; they are wired directly and serialised manually.
        self._dim_widgets: Dict[int, QSpinBox] = {}
        self._current_dims: int = 0

        # ── Finalise widget tree ──────────────────────────────────────
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()

        # Build initial dim rows without triggering on_ui_change mid-init
        self._spin_dims.blockSignals(True)
        self._set_dims(initial_dims)
        self._spin_dims.blockSignals(False)

        # Now wire dims signal (AFTER blockSignals to avoid spurious fire)
        self._spin_dims.valueChanged.connect(self._on_dims_changed)

        # Sync fill-value visibility for initial fill mode
        self._sync_fill_value_visibility()

        self._widget_core.refresh_widget_palettes()

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _make_separator() -> QFrame:
        """Return a styled horizontal rule for visual section breaks."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep

    def _get_fill_mode(self) -> str:
        idx = self._combo_fill.currentIndex()
        return _FILL_MODES[idx][1] if 0 <= idx < len(_FILL_MODES) else "zeros"

    def _get_dtype(self) -> "np.dtype":
        idx = self._combo_dtype.currentIndex()
        key = _DTYPE_OPTIONS[idx][1] if 0 <= idx < len(_DTYPE_OPTIONS) else "float64"
        return np.dtype(key)

    def _sync_fill_value_visibility(self) -> None:
        """Show the fill-value row only when Fill = Full."""
        is_full = self._get_fill_mode() == "full"
        self._label_fill_val.setVisible(is_full)
        self._spin_fill_val.setVisible(is_full)

    # ── Dynamic dimension management ──────────────────────────────────────────

    def _set_dims(self, new_dims: int) -> None:
        """
        Grow or shrink the dim-widget/port set to exactly *new_dims*.

        Always processes changes in the safe direction: adds highest index
        last, removes highest index first (LIFO), so port numbering stays
        contiguous and no gaps appear in the form layout.

        When shrinking, widget rows are torn down first (signals disconnected,
        form rows deleted), then all excess ports are removed in a single
        ``remove_ports()`` call so the node only rebuilds geometry once.
        """
        new_dims = max(1, min(self.MAX_DIMS, new_dims))
        old_dims = self._current_dims

        if new_dims > old_dims:
            for d in range(old_dims, new_dims):
                self._add_dim(d)

        elif new_dims < old_dims:
            # ── 1. Tear down widget rows (highest index first) ────────
            form: QFormLayout = self._widget_core.layout()
            for d in range(old_dims - 1, new_dims - 1, -1):
                spin = self._dim_widgets.pop(d, None)
                if spin is not None:
                    try:
                        spin.valueChanged.disconnect(self._on_dim_spin_changed)
                    except RuntimeError:
                        pass
                    try:
                        form.removeRow(spin)
                    except Exception as exc:
                        log.warning(
                            f"NumpyArrayNode: could not remove form row "
                            f"for dim {d}: {exc}"
                        )

            # ── 2. Batch-remove the excess ports in one geometry pass ─
            port_names = [f"dim_{d}" for d in range(new_dims, old_dims)]
            removed = self.remove_ports(port_names, is_output=False)
            if removed != len(port_names):
                log.warning(
                    f"NumpyArrayNode._set_dims: expected to remove "
                    f"{len(port_names)} ports, actually removed {removed}"
                )

        self._current_dims = new_dims

    def _add_dim(self, d: int) -> None:
        """
        Append a ``QSpinBox`` row labelled ``Dim {d}:`` to the form
        and register the matching auto-disable input port ``dim_{d}``.
        """
        form: QFormLayout = self._widget_core.layout()

        spin = QSpinBox()
        spin.setRange(1, 9_999)
        spin.setValue(self._DEFAULT_DIM_SIZE)
        spin.setMinimumWidth(80)
        form.addRow(f"Dim {d}:", spin)

        # Direct signal — bypasses WidgetCore to avoid double evaluation
        spin.valueChanged.connect(self._on_dim_spin_changed)
        self._dim_widgets[d] = spin

        # Input port with auto-disable so the matching spinbox greys out
        # when an upstream node is connected
        self.add_input(f"dim_{d}", "int")
        self.inputs[-1]._auto_disable = True

    def _remove_dim(self, d: int) -> None:
        """
        Remove a single ``QSpinBox`` row and its input port for dimension *d*.

        Signals are disconnected before ``removeRow`` is called because
        ``QFormLayout.removeRow`` deletes the underlying C++ widget object,
        which would make a subsequent ``disconnect`` crash.

        Uses ``remove_ports([name], is_output=False)`` — the same batch API
        used by ``_set_dims`` and ``MultiFloatOutputNode._set_count`` — so
        the removal path is consistent and a single geometry rebuild is
        issued even for one-shot removals.
        """
        form: QFormLayout = self._widget_core.layout()

        spin = self._dim_widgets.pop(d, None)
        if spin is not None:
            # Disconnect before Qt deletes the C++ object
            try:
                spin.valueChanged.disconnect(self._on_dim_spin_changed)
            except RuntimeError:
                pass
            try:
                form.removeRow(spin)
            except Exception as exc:
                log.warning(
                    f"NumpyArrayNode: could not remove form row for dim {d}: {exc}"
                )

        port_name = f"dim_{d}"
        removed = self.remove_ports([port_name], is_output=False)
        if removed != 1:
            log.warning(
                f"NumpyArrayNode: port '{port_name}' not found for removal"
            )

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(int)
    def _on_dims_changed(self, value: int) -> None:
        """Rebuild dimension rows/ports then propagate downstream."""
        try:
            self._set_dims(value)
            self.on_ui_change()
            # Re-theme newly created spinboxes so they match the node body
            self._widget_core.refresh_widget_palettes()
        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode._on_dims_changed: {exc}")

    @Slot(int)
    def _on_fill_type_changed(self, _index: int) -> None:
        """
        Show or hide the fill-value spinbox row.

        ``on_ui_change`` is *not* called here because WidgetCore has already
        wired ``_combo_fill.currentIndexChanged`` and will emit
        ``value_changed("fill_type")`` → ``_on_static_changed`` →
        ``on_ui_change`` on the same signal dispatch.
        """
        try:
            self._sync_fill_value_visibility()
        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode._on_fill_type_changed: {exc}")

    @Slot(str)
    def _on_static_changed(self, _port_name: str) -> None:
        """Propagates any WidgetCore change (fill, dtype, value) downstream."""
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode._on_static_changed: {exc}")

    @Slot(int)
    def _on_dim_spin_changed(self, _value: int) -> None:
        """Propagates a dim-size spinbox change downstream."""
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode._on_dim_spin_changed: {exc}")

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the ndarray from current state.

        For each axis the upstream port value is preferred; the local
        spinbox is the fallback when no connection is present (mirrors
        the ``_auto_disable`` pattern used elsewhere in the codebase).
        """
        try:
            # ── Shape ─────────────────────────────────────────────────
            shape: List[int] = []
            for d in range(self._current_dims):
                upstream = inputs.get(f"dim_{d}")
                if upstream is not None:
                    size = max(1, int(upstream))
                else:
                    spin = self._dim_widgets.get(d)
                    size = spin.value() if spin is not None else 1
                shape.append(size)

            if not shape:
                shape = [1]

            # ── Dtype ─────────────────────────────────────────────────
            dtype = self._get_dtype()

            # ── Fill ──────────────────────────────────────────────────
            mode = self._get_fill_mode()

            if mode == "zeros":
                arr = np.zeros(shape, dtype=dtype)

            elif mode == "ones":
                arr = np.ones(shape, dtype=dtype)

            elif mode == "full":
                fill_val = float(
                    self._widget_core.get_port_value("fill_value") or 0.0
                )
                arr = np.full(shape, fill_val, dtype=dtype)

            elif mode == "random_uniform":
                arr = np.random.uniform(0.0, 1.0, shape).astype(dtype)

            elif mode == "random_normal":
                arr = np.random.normal(0.0, 1.0, shape).astype(dtype)

            elif mode == "eye":
                # np.eye produces a 2-D identity matrix.  For rank > 2 we
                # build a zeros tensor and embed the identity at slice [..., 0, 0].
                rows, cols = shape[0], (shape[1] if len(shape) > 1 else shape[0])
                eye_2d = np.eye(rows, cols, dtype=dtype)
                if len(shape) == 1:
                    # 1-D: return all-ones (no meaningful identity)
                    arr = np.ones(shape, dtype=dtype)
                elif len(shape) == 2:
                    arr = eye_2d
                else:
                    arr = np.zeros(shape, dtype=dtype)
                    # Build a leading-slice index: keep first two axes, fix
                    # all remaining axes at 0 so the 2-D identity is visible
                    # at tensor[:, :, 0, 0, ...]
                    idx: List[Any] = [slice(None), slice(None)]
                    idx += [0] * (len(shape) - 2)
                    arr[tuple(idx)] = eye_2d

            else:
                arr = np.zeros(shape, dtype=dtype)

            self.array_changed.emit(arr)
            return {"array": arr}

        except Exception as exc:
            log.error(f"Exception in NumpyArrayNode.compute: {exc}")
            return {"array": np.array([], dtype=np.float64)}

    # ── Serialisation ─────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        """
        Extends ``BaseControlNode.get_state()`` with dims-spinbox value
        and all dynamic dim-size values.

        WidgetCore already handles ``fill_type``, ``fill_value``, and
        ``dtype`` automatically via ``"widget_data"``.
        """
        state = super().get_state()
        state["numpy_array_node"] = {
            "num_dims":   self._current_dims,
            "dims_value": self._spin_dims.value(),
            "dim_sizes":  {
                str(d): spin.value()
                for d, spin in self._dim_widgets.items()
            },
        }
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restores the dims spinbox and dynamic dim-size widgets after the
        base class has restored WidgetCore state (fill, dtype, value).

        Mirrors the clean-slate pattern from ``MultiFloatOutputNode``:

        1. Remove any stale ``dim_N`` input ports the base-class restore
           may have re-created from saved data, so there are no duplicates.
        2. Tear down all widget rows built by ``__init__``'s ``_set_dims``
           call (signals disconnected first to avoid crashes on deleted C++
           objects), then clear ``_dim_widgets`` and reset ``_current_dims``
           to 0.
        3. Rebuild the full dim set from scratch via ``_set_dims``, which
           issues a single geometry rebuild rather than one per port.
        4. Restore individual dim-size spinbox values.
        """
        super().restore_state(state)

        ns = state.get("numpy_array_node", {})
        num_dims = ns.get("num_dims",   1)
        dims_val = ns.get("dims_value", num_dims)

        log.debug(
            f"NumpyArrayNode.restore_state: saved num_dims={num_dims}, "
            f"current inputs={[p.name for p in self.inputs]}"
        )

        # ── 1. Remove stale dim ports from __init__ + base restore ────────
        # The base class re-creates ports from saved data, so dim_N ports
        # may already exist.  Batch-remove them so _set_dims can rebuild
        # with matching widgets and a single geometry pass.
        stale_dim_ports = [p for p in list(self.inputs)
                           if p.name.startswith("dim_")]
        if stale_dim_ports:
            log.debug(
                f"  removing stale dim ports: "
                f"{[p.name for p in stale_dim_ports]}"
            )
            self.remove_ports(stale_dim_ports, is_output=False)

        # ── 2. Tear down stale widget rows from __init__'s _set_dims ──────
        form: QFormLayout = self._widget_core.layout()
        for spin in list(self._dim_widgets.values()):
            try:
                spin.valueChanged.disconnect(self._on_dim_spin_changed)
            except RuntimeError:
                pass
            try:
                form.removeRow(spin)
            except Exception:
                pass
        self._dim_widgets.clear()
        self._current_dims = 0

        # ── 3. Rebuild from scratch ───────────────────────────────────────
        self._spin_dims.blockSignals(True)
        self._spin_dims.setValue(dims_val)
        self._spin_dims.blockSignals(False)

        self._set_dims(num_dims)

        # ── 4. Restore individual dim-size spinbox values ─────────────────
        for d_str, size in ns.get("dim_sizes", {}).items():
            spin = self._dim_widgets.get(int(d_str))
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(int(size))
                spin.blockSignals(False)

        # Re-sync visibility now that fill_type has been restored by super()
        self._sync_fill_value_visibility()
        self._widget_core.refresh_widget_palettes()

        log.debug(
            f"NumpyArrayNode.restore_state: done, "
            f"num_dims={self._current_dims}, "
            f"inputs={[(p.name, p.datatype) for p in self.inputs]}"
        )

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Disconnect dynamic signals then hand off to WidgetCore and super."""
        for spin in list(self._dim_widgets.values()):
            try:
                spin.valueChanged.disconnect(self._on_dim_spin_changed)
            except RuntimeError:
                pass  # C++ object already deleted
        self._dim_widgets.clear()

        try:
            self._spin_dims.valueChanged.disconnect(self._on_dims_changed)
        except RuntimeError:
            pass

        self._widget_core.cleanup()
        super().cleanup()
