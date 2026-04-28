# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_constant_nodes.py
-----------------------
Two lightweight source nodes that emit a single ``float`` scalar constant 
selected from a dropdown. No input ports; one output port.

Provided nodes
--------------
``MathConstantNode``      Emits fundamental mathematical constants.
``ScientificConstantNode``Emits physical / scientific constants (SI units).

Both nodes
----------
* Single output port ``value`` (``float``).
* Status label shows the selected constant's value and description.
* Thread-safe computation with main-thread UI synchronization.
* Proper undo/redo snapshot handling via WidgetCore.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QComboBox, QFormLayout, QFrame, QLabel

# Canonical imports per Weave rules
from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore
from weave.widgetcore.widgetcore_port_models import PortRole
from weave.node.node_enums import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("ConstantNodes")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


def _populate_combo(combo: QComboBox, entries: Tuple) -> None:
    """Add entries to *combo*. Header items (value is None) are disabled."""
    for label, key, _desc in entries:
        # Attach internal key as userData so WidgetCore's adapter reads it via currentData()
        combo.addItem(label, userData=key if key is not None else "")
        if key is None:
            model = combo.model()
            item: QStandardItem = model.item(combo.count() - 1)
            item.setFlags(Qt.ItemFlag.NoItemFlags)


# ── Entry tuples: (display_label, key_string, description_string) ─────────────
_MATH_ENTRIES: Tuple[Tuple[str, Optional[str], str], ...] = (
    ("── Circle & Exponential ──", None, ""),
    ("π", "pi", "Pi  ≈ 3.14159265…"),
    ("τ  (2π)", "tau", "Tau = 2π  ≈ 6.28318530…"),
    ("e", "e", "Euler's number  ≈ 2.71828182…"),
    ("── Algebraic ──", None, ""),
    ("φ  (golden ratio)", "phi", "Golden ratio  ≈ 1.61803398…"),
    ("√2", "sqrt2", "Square root of 2  ≈ 1.41421356…"),
    ("√3", "sqrt3", "Square root of 3  ≈ 1.73205080…"),
    ("√5", "sqrt5", "Square root of 5  ≈ 2.23606797…"),
    ("── Logarithms ──", None, ""),
    ("ln 2", "ln2", "Natural log of 2  ≈ 0.69314718…"),
    ("ln 10", "ln10", "Natural log of 10  ≈ 2.30258509…"),
    ("log₂ e", "log2e", "Log base-2 of e  ≈ 1.44269504…"),
    ("log₁₀ e", "log10e", "Log base-10 of e  ≈ 0.43429448…"),
    ("── Special ──", None, ""),
    ("γ  (Euler–Mascheroni)", "euler_gamma", "Euler–Mascheroni  ≈ 0.57721566…"),
    ("ζ(2)  (π²/6)", "zeta2", "Basel problem  ≈ 1.64493406…"),
    ("ζ(3)  (Apéry)", "zeta3", "Apéry's constant  ≈ 1.20205690…"),
    ("G  (Catalan)", "catalan", "Catalan's constant  ≈ 0.91596559…"),
)

_MATH_VALUES: Dict[str, float] = {
    "pi": np.pi, "tau": 2.0 * np.pi, "e": np.e,
    "phi": (1.0 + np.sqrt(5.0)) / 2.0,
    "sqrt2": np.sqrt(2.0), "sqrt3": np.sqrt(3.0), "sqrt5": np.sqrt(5.0),
    "ln2": np.log(2.0), "ln10": np.log(10.0),
    "log2e": np.log2(np.e), "log10e": np.log10(np.e),
    "euler_gamma": 0.5772156649015328606065120900824024310421593359,
    "zeta2": np.pi ** 2 / 6.0,
    "zeta3": 1.2020569031595942853997381615114499907649862923,
    "catalan": 0.9159655941772190462697242271544405641208767166,
}

_SCI_ENTRIES: Tuple[Tuple[str, Optional[str], str], ...] = (
    ("── Electromagnetic ──", None, ""),
    ("c  — Speed of light", "c", "2.99792458×10⁸ m/s"),
    ("e  — Electron charge", "e_charge", "1.602176634×10⁻¹⁹ C"),
    ("ε₀ — Vacuum permittivity", "eps0", "8.8541878188×10⁻¹² F/m"),
    ("μ₀ — Vacuum permeability", "mu0", "1.25663706127×10⁻⁶ N/A²"),
    ("α  — Fine-structure constant", "alpha", "7.2973525643×10⁻³ (dimensionless)"),
    ("── Quantum ──", None, ""),
    ("h  — Planck constant", "h", "6.62607015×10⁻³⁴ J·s"),
    ("ħ  — Reduced Planck", "hbar", "1.05457181764×10⁻³⁴ J·s"),
    ("m_e — Electron mass", "m_e", "9.1093837139×10⁻³¹ kg"),
    ("m_p — Proton mass", "m_p", "1.67262192595×10⁻²⁷ kg"),
    ("m_n — Neutron mass", "m_n", "1.67492750056×10⁻²⁷ kg"),
    ("a₀ — Bohr radius", "a0", "5.29177210544×10⁻¹¹ m"),
    ("R∞ — Rydberg constant", "rydberg", "1.0973731568157×10⁷ m⁻¹"),
    ("── Thermodynamic ──", None, ""),
    ("k_B — Boltzmann constant", "k_B", "1.380649×10⁻²³ J/K"),
    ("N_A — Avogadro constant", "N_A", "6.02214076×10²³ mol⁻¹"),
    ("R  — Gas constant", "R_gas", "8.314462618 J/(mol·K)"),
    ("F  — Faraday constant", "faraday", "96485.33212 C/mol"),
    ("σ  — Stefan-Boltzmann", "sigma_sb", "5.670374419×10⁻⁸ W/(m²·K⁴)"),
    ("b  — Wien displacement", "wien", "2.897771955×10⁻³ m·K"),
    ("── Gravitational & Mechanical ──", None, ""),
    ("G  — Gravitational constant", "G", "6.67430×10⁻¹¹ m³/(kg·s²)"),
    ("g  — Standard gravity", "g_std", "9.80665 m/s²"),
    ("u  — Atomic mass unit", "amu", "1.66053906660×10⁻²⁷ kg"),
    ("atm — Standard atmosphere", "atm", "101325.0 Pa"),
)

_SCI_VALUES: Dict[str, float] = {
    "c": 2.99792458e8, "e_charge": 1.602176634e-19, "eps0": 8.8541878188e-12,
    "mu0": 1.25663706127e-6, "alpha": 7.2973525643e-3,
    "h": 6.62607015e-34, "hbar": 1.05457181764e-34,
    "m_e": 9.1093837139e-31, "m_p": 1.67262192595e-27, "m_n": 1.67492750056e-27,
    "a0": 5.29177210544e-11, "rydberg": 1.0973731568157e7,
    "k_B": 1.380649e-23, "N_A": 6.02214076e23, "R_gas": 8.314462618,
    "faraday": 96485.33212, "sigma_sb": 5.670374419e-8, "wien": 2.897771955e-3,
    "G": 6.67430e-11, "g_std": 9.80665, "amu": 1.66053906660e-27, "atm": 101325.0,
}


# ── Pre-built index maps ─────────────────────────────────────────────────────

def _build_index_maps(entries: Tuple) -> Tuple[Dict[int, str], Dict[str, int], int, str]:
    idx_to_val = {i: key for i, (_, key, _) in enumerate(entries) if key is not None}
    val_to_idx = {v: k for k, v in idx_to_val.items()}
    default_idx = next(iter(idx_to_val))
    default_val = entries[default_idx][1]
    return idx_to_val, val_to_idx, default_idx, default_val

_MATH_IDX_TO_VAL, _MATH_VAL_TO_IDX, _MATH_DEFAULT_IDX, _MATH_DEFAULT_VAL = _build_index_maps(_MATH_ENTRIES)
_SCI_IDX_TO_VAL, _SCI_VAL_TO_IDX, _SCI_DEFAULT_IDX, _SCI_DEFAULT_VAL = _build_index_maps(_SCI_ENTRIES)


# ── Shared base ───────────────────────────────────────────────────────────────

class _ConstantNodeBase(ThreadedNode):
    """Internal base for constant nodes. Handles UI binding, thread-safe compute, and state sync."""

    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT
    value_changed_sig: ClassVar[Signal] = Signal(float)

    _ENTRIES: ClassVar[Tuple]
    _VALUES: ClassVar[Dict[str, float]]
    _IDX_MAP: ClassVar[Dict[int, str]]
    _DEFAULT_IDX: ClassVar[int]
    _DEFAULT_VAL: ClassVar[str]

    def __init__(self, title: str, **kwargs: Any) -> None:
        # 1. Super init
        super().__init__(title=title, **kwargs)

        # 2. Add ports
        self.add_output("value", datatype="float")

        # 3. Layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # 4. Create widgets, place in layout, register with WidgetCore
        self._combo_const = QComboBox()
        self._combo_const.setMinimumWidth(200)
        _populate_combo(self._combo_const, self._ENTRIES)
        self._combo_const.setCurrentIndex(self._DEFAULT_IDX)
        form.addRow("Constant:", self._combo_const)
        
        # datatype="str" matches the key strings stored as userData
        self._widget_core.register_widget(
            "constant", self._combo_const,
            role=PortRole.INTERNAL, datatype="str", default=self._DEFAULT_VAL,
            add_to_layout=False,
        )

        # Status label (read-only display; no registration needed)
        form.addRow(_make_separator())
        self._label_status = QLabel("--")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        self._label_status.setMinimumWidth(160)
        form.addRow(self._label_status)

        # 5. Wire BOTH signals (canonical pattern for undo/redo structural sync)
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # 6. Mount + Patch render proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    # ── Signal Slots (Rule R7 / R4.1 Step 5) ────────────────────────────────
    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        # User edit → mark dirty for re-evaluation
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        # Programmatic write / undo replay → structural sync only. 
        # Do NOT call on_ui_change() here; framework handles re-eval after restore.
        pass

    # ── Compute (Background Thread) ────────────────────────────────────────
    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_compute_cancelled():
            return {"value": np.float64(0.0), "_status_text": "cancelled"}

        try:
            key = inputs.get("constant", self._DEFAULT_VAL)
            value = self._VALUES.get(key)
            if value is None:
                raise KeyError(f"Unknown constant key: {key!r}")

            desc = next((d for (_l, k, d) in self._ENTRIES if k == key), "")
            log.debug("%s: %s = %.15g", type(self).__name__, key, value)
            
            # Return status string alongside value. Weave caches all returned keys safely.
            return {"value": np.float64(value), "_status_text": f"{value:.15g}\n{desc}"}

        except Exception as exc:
            log.warning("%s.compute: %s", type(self).__name__, exc)
            return {"value": np.float64(0.0), "_status_text": f"error: {exc}"}

    # ── Post-eval flush (Main Thread) ───────────────────────────────────────
    def on_evaluate_finished(self) -> None:
        try:
            status = self._get_cached_value("_status_text")
            if isinstance(status, str):
                self._label_status.setText(status)

            result = self._get_cached_value("value")
            if result is not None:
                self.value_changed_sig.emit(float(result))
        except Exception as exc:
            log.error("Exception in %s.on_evaluate_finished: %s", type(self).__name__, exc)
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ─────────────────────────────────────────────────────────────
    def cleanup(self) -> None:
        self.cancel_compute()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# MathConstantNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class MathConstantNode(_ConstantNodeBase):
    node_class: ClassVar[str] = "Numpy"
    node_subclass: ClassVar[str] = "Constant"
    node_name: ClassVar[Optional[str]] = "Math Constant"
    node_description: ClassVar[Optional[str]] = "Emits a fundamental mathematical constant (π, e, φ, √2, γ, …)"
    node_tags: ClassVar[Optional[List[str]]] = [
        "constant", "math", "pi", "tau", "euler", "golden ratio",
        "sqrt2", "phi", "log", "zeta", "catalan", "primitive"
    ]

    _ENTRIES = _MATH_ENTRIES
    _VALUES = _MATH_VALUES
    _IDX_MAP = _MATH_IDX_TO_VAL
    _DEFAULT_IDX = _MATH_DEFAULT_IDX
    _DEFAULT_VAL = _MATH_DEFAULT_VAL

    def __init__(self, title: str = "Math Constant", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# ScientificConstantNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class ScientificConstantNode(_ConstantNodeBase):
    node_class: ClassVar[str] = "Numpy"
    node_subclass: ClassVar[str] = "Constant"
    node_name: ClassVar[Optional[str]] = "Scientific Constant"
    node_description: ClassVar[Optional[str]] = (
        "Emits a physical/scientific constant in SI units (c, h, k_B, N_A, G, e, …)"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "constant", "physics", "science", "si", "planck", "boltzmann",
        "avogadro", "speed of light", "electron", "gravity", "primitive"
    ]

    _ENTRIES = _SCI_ENTRIES
    _VALUES = _SCI_VALUES
    _IDX_MAP = _SCI_IDX_TO_VAL
    _DEFAULT_IDX = _SCI_DEFAULT_IDX
    _DEFAULT_VAL = _SCI_DEFAULT_VAL

    def __init__(self, title: str = "Scientific Constant", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
