# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_constant_nodes.py
-----------------------
Two lightweight source nodes that emit a single ``float64`` scalar
constant selected from a dropdown.  No input ports; one output port.

Provided nodes
--------------
``MathConstantNode``
    Emits a fundamental mathematical constant.

    ============= ========================= ============================
    Label         Value                     Description
    ============= ========================= ============================
    π             3.14159265358979…         Pi
    τ  (2π)       6.28318530717958…         Tau — full circle
    e             2.71828182845904…         Euler's number
    φ  (phi)      1.61803398874989…         Golden ratio
    √2            1.41421356237309…         Square root of 2
    √3            1.73205080756887…         Square root of 3
    √5            2.23606797749978…         Square root of 5
    ln 2          0.69314718055994…         Natural log of 2
    ln 10         2.30258509299404…         Natural log of 10
    log₂ e        1.44269504088896…         Log base-2 of e
    log₁₀ e       0.43429448190325…         Log base-10 of e
    γ  (Euler)    0.57721566490153…         Euler–Mascheroni constant
    ζ(2)          1.64493406684822…         Basel problem (π²/6)
    ζ(3)          1.20205690315959…         Apéry's constant
    Catalan G     0.91596559417721…         Catalan's constant
    ============= ========================= ============================

``ScientificConstantNode``
    Emits a physical / scientific constant in SI units.

    ====================== ========================= ====================
    Label                  Value                     Unit
    ====================== ========================= ====================
    Speed of light  c      2.99792458e8              m/s
    Planck  h              6.62607015e-34            J·s
    Reduced Planck  ħ      1.05457181764e-34         J·s
    Boltzmann  k_B         1.380649e-23              J/K
    Avogadro  N_A          6.02214076e23             mol⁻¹
    Electron charge  e     1.602176634e-19           C
    Electron mass  m_e     9.1093837139e-31          kg
    Proton mass  m_p       1.67262192595e-27         kg
    Neutron mass  m_n      1.67492750056e-27         kg
    Gravitational  G       6.67430e-11               m³/(kg·s²)
    Vacuum permittivity ε₀ 8.8541878188e-12          F/m
    Vacuum permeability μ₀ 1.25663706127e-6          N/A²
    Fine-structure  α      7.2973525643e-3           (dimensionless)
    Rydberg  R∞            1.0973731568157e7         m⁻¹
    Bohr radius  a₀        5.29177210544e-11         m
    Faraday  F             96485.33212               C/mol
    Gas constant  R        8.314462618               J/(mol·K)
    Stefan-Boltzmann  σ    5.670374419e-8            W/(m²·K⁴)
    Wien displacement  b   2.897771955e-3            m·K
    Atomic mass unit  u    1.66053906660e-27         kg
    Standard gravity  g    9.80665                   m/s²
    Atm pressure  atm      101325.0                  Pa
    ====================== ========================= ====================

Both nodes
----------
* Single output port ``value`` (``float``).
* Status label shows the selected constant's value and unit/description.
* ``snapshot_widget_inputs`` reads combo by index → value string to avoid
  the label-vs-value bug.
* No input ports — these are pure source nodes.
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

log = get_logger("ConstantNodes")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


def _populate_combo(combo: QComboBox, entries: Tuple) -> None:
    """Add entries to *combo*.  Header items (value is None) are disabled."""
    from PySide6.QtCore import Qt
    for label, _val, _desc in entries:
        combo.addItem(label)
        if _val is None:
            model = combo.model()
            item: QStandardItem = model.item(combo.count() - 1)
            item.setFlags(Qt.ItemFlag.NoItemFlags)


# ── Entry tuples: (display_label, key_string, description_string) ─────────────
# key_string is None for category headers.

_MATH_ENTRIES: Tuple[Tuple[str, Optional[str], str], ...] = (
    # ── Core circle / exponential ─────────────────────────────────────────────
    ("── Circle & Exponential ──",  None,          ""),
    ("π",                           "pi",          "Pi  ≈ 3.14159265…"),
    ("τ  (2π)",                     "tau",         "Tau = 2π  ≈ 6.28318530…"),
    ("e",                           "e",           "Euler's number  ≈ 2.71828182…"),
    # ── Algebraic ─────────────────────────────────────────────────────────────
    ("── Algebraic ──",             None,          ""),
    ("φ  (golden ratio)",           "phi",         "Golden ratio  ≈ 1.61803398…"),
    ("√2",                          "sqrt2",       "Square root of 2  ≈ 1.41421356…"),
    ("√3",                          "sqrt3",       "Square root of 3  ≈ 1.73205080…"),
    ("√5",                          "sqrt5",       "Square root of 5  ≈ 2.23606797…"),
    # ── Logarithms ────────────────────────────────────────────────────────────
    ("── Logarithms ──",            None,          ""),
    ("ln 2",                        "ln2",         "Natural log of 2  ≈ 0.69314718…"),
    ("ln 10",                       "ln10",        "Natural log of 10  ≈ 2.30258509…"),
    ("log₂ e",                      "log2e",       "Log base-2 of e  ≈ 1.44269504…"),
    ("log₁₀ e",                     "log10e",      "Log base-10 of e  ≈ 0.43429448…"),
    # ── Special constants ─────────────────────────────────────────────────────
    ("── Special ──",               None,          ""),
    ("γ  (Euler–Mascheroni)",       "euler_gamma", "Euler–Mascheroni  ≈ 0.57721566…"),
    ("ζ(2)  (π²/6)",                "zeta2",       "Basel problem  ≈ 1.64493406…"),
    ("ζ(3)  (Apéry)",               "zeta3",       "Apéry's constant  ≈ 1.20205690…"),
    ("G  (Catalan)",                "catalan",     "Catalan's constant  ≈ 0.91596559…"),
)

_MATH_VALUES: Dict[str, float] = {
    "pi":          np.pi,
    "tau":         2.0 * np.pi,
    "e":           np.e,
    "phi":         (1.0 + np.sqrt(5.0)) / 2.0,
    "sqrt2":       np.sqrt(2.0),
    "sqrt3":       np.sqrt(3.0),
    "sqrt5":       np.sqrt(5.0),
    "ln2":         np.log(2.0),
    "ln10":        np.log(10.0),
    "log2e":       np.log2(np.e),
    "log10e":      np.log10(np.e),
    "euler_gamma": 0.5772156649015328606065120900824024310421593359,
    "zeta2":       np.pi ** 2 / 6.0,
    "zeta3":       1.2020569031595942853997381615114499907649862923,
    "catalan":     0.9159655941772190462697242271544405641208767166,
}

_SCI_ENTRIES: Tuple[Tuple[str, Optional[str], str], ...] = (
    # ── Electromagnetic ───────────────────────────────────────────────────────
    ("── Electromagnetic ──",                  None,           ""),
    ("c  — Speed of light",                   "c",            "2.99792458×10⁸ m/s"),
    ("e  — Electron charge",                  "e_charge",     "1.602176634×10⁻¹⁹ C"),
    ("ε₀ — Vacuum permittivity",              "eps0",         "8.8541878188×10⁻¹² F/m"),
    ("μ₀ — Vacuum permeability",              "mu0",          "1.25663706127×10⁻⁶ N/A²"),
    ("α  — Fine-structure constant",          "alpha",        "7.2973525643×10⁻³  (dimensionless)"),
    # ── Quantum ───────────────────────────────────────────────────────────────
    ("── Quantum ──",                          None,           ""),
    ("h  — Planck constant",                  "h",            "6.62607015×10⁻³⁴ J·s"),
    ("ħ  — Reduced Planck",                   "hbar",         "1.05457181764×10⁻³⁴ J·s"),
    ("m_e — Electron mass",                   "m_e",          "9.1093837139×10⁻³¹ kg"),
    ("m_p — Proton mass",                     "m_p",          "1.67262192595×10⁻²⁷ kg"),
    ("m_n — Neutron mass",                    "m_n",          "1.67492750056×10⁻²⁷ kg"),
    ("a₀ — Bohr radius",                      "a0",           "5.29177210544×10⁻¹¹ m"),
    ("R∞ — Rydberg constant",                 "rydberg",      "1.0973731568157×10⁷ m⁻¹"),
    # ── Thermodynamic ─────────────────────────────────────────────────────────
    ("── Thermodynamic ──",                    None,           ""),
    ("k_B — Boltzmann constant",              "k_B",          "1.380649×10⁻²³ J/K"),
    ("N_A — Avogadro constant",               "N_A",          "6.02214076×10²³ mol⁻¹"),
    ("R  — Gas constant",                     "R_gas",        "8.314462618 J/(mol·K)"),
    ("F  — Faraday constant",                 "faraday",      "96485.33212 C/mol"),
    ("σ  — Stefan-Boltzmann",                 "sigma_sb",     "5.670374419×10⁻⁸ W/(m²·K⁴)"),
    ("b  — Wien displacement",                "wien",         "2.897771955×10⁻³ m·K"),
    # ── Gravitational & mechanical ────────────────────────────────────────────
    ("── Gravitational & Mechanical ──",       None,           ""),
    ("G  — Gravitational constant",           "G",            "6.67430×10⁻¹¹ m³/(kg·s²)"),
    ("g  — Standard gravity",                 "g_std",        "9.80665 m/s²"),
    ("u  — Atomic mass unit",                 "amu",          "1.66053906660×10⁻²⁷ kg"),
    ("atm — Standard atmosphere",             "atm",          "101325.0 Pa"),
)

_SCI_VALUES: Dict[str, float] = {
    # Electromagnetic
    "c":        2.99792458e8,
    "e_charge": 1.602176634e-19,
    "eps0":     8.8541878188e-12,
    "mu0":      1.25663706127e-6,
    "alpha":    7.2973525643e-3,
    # Quantum
    "h":        6.62607015e-34,
    "hbar":     1.05457181764e-34,
    "m_e":      9.1093837139e-31,
    "m_p":      1.67262192595e-27,
    "m_n":      1.67492750056e-27,
    "a0":       5.29177210544e-11,
    "rydberg":  1.0973731568157e7,
    # Thermodynamic
    "k_B":      1.380649e-23,
    "N_A":      6.02214076e23,
    "R_gas":    8.314462618,
    "faraday":  96485.33212,
    "sigma_sb": 5.670374419e-8,
    "wien":     2.897771955e-3,
    # Gravitational & mechanical
    "G":        6.67430e-11,
    "g_std":    9.80665,
    "amu":      1.66053906660e-27,
    "atm":      101325.0,
}


# ── Pre-built index maps (shared pattern from math/signal nodes) ──────────────

def _build_index_maps(
    entries: Tuple[Tuple[str, Optional[str], str], ...],
) -> Tuple[Dict[int, str], Dict[str, int], int, str]:
    idx_to_val: Dict[int, str] = {
        i: key
        for i, (_, key, _d) in enumerate(entries)
        if key is not None
    }
    val_to_idx: Dict[str, int] = {v: k for k, v in idx_to_val.items()}
    default_idx = next(iter(idx_to_val))
    default_val = entries[default_idx][1]
    return idx_to_val, val_to_idx, default_idx, default_val


(
    _MATH_IDX_TO_VAL,
    _MATH_VAL_TO_IDX,
    _MATH_DEFAULT_IDX,
    _MATH_DEFAULT_VAL,
) = _build_index_maps(_MATH_ENTRIES)

(
    _SCI_IDX_TO_VAL,
    _SCI_VAL_TO_IDX,
    _SCI_DEFAULT_IDX,
    _SCI_DEFAULT_VAL,
) = _build_index_maps(_SCI_ENTRIES)


# ── Shared base ───────────────────────────────────────────────────────────────

class _ConstantNodeBase(ThreadedNode):
    """
    Internal base for constant nodes.

    Subclasses must set:
        _ENTRIES   — the entries tuple
        _VALUES    — the values dict
        _IDX_MAP   — index→value dict
        _DEFAULT_IDX / _DEFAULT_VAL

    And call ``_build_ui(form)`` after ``super().__init__``.
    """

    value_changed_sig = Signal(float)   # emits the constant value

    _ENTRIES:     ClassVar[Tuple]
    _VALUES:      ClassVar[Dict[str, float]]
    _IDX_MAP:     ClassVar[Dict[int, str]]
    _DEFAULT_IDX: ClassVar[int]
    _DEFAULT_VAL: ClassVar[str]

    def __init__(self, title: str, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
        self.add_output("value", "float")

        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Constant combo ────────────────────────────────────────────
        self._combo_const = QComboBox()
        self._combo_const.setMinimumWidth(200)
        _populate_combo(self._combo_const, self._ENTRIES)
        self._combo_const.setCurrentIndex(self._DEFAULT_IDX)
        form.addRow("Constant:", self._combo_const)
        self._widget_core.register_widget(
            "constant", self._combo_const,
            role="internal", datatype="string", default=self._DEFAULT_VAL,
            add_to_layout=False,
        )

        # ── Status label ──────────────────────────────────────────────
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

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        idx = self._combo_const.currentIndex()
        return {
            "_ui_constant": self._IDX_MAP.get(idx, self._DEFAULT_VAL),
        }

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, _port_name: str) -> None:
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error("Exception in %s._on_core_changed: %s",
                      type(self).__name__, exc)

    # ── Compute ───────────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            key   = inputs.get("_ui_constant", self._DEFAULT_VAL)
            value = self._VALUES.get(key)
            if value is None:
                raise KeyError(f"Unknown constant key: {key!r}")

            # Look up description for status label
            desc = next(
                (d for (_l, k, d) in self._ENTRIES if k == key), ""
            )
            self._pending_status = f"{value:.15g}\n{desc}"
            log.debug("%s: %s = %.15g", type(self).__name__, key, value)
            return {"value": np.float64(value)}

        except Exception as exc:
            log.warning("%s.compute: %s", type(self).__name__, exc)
            self._pending_status = f"error: {exc}"
            return {"value": np.float64(0.0)}

    # ── Post-eval flush ───────────────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        try:
            if self._pending_status is not None:
                try:
                    self._label_status.setText(self._pending_status)
                except RuntimeError:
                    pass
                self._pending_status = None

            result = self.get_output_value("value")
            if result is not None:
                self.value_changed_sig.emit(float(result))
        except Exception as exc:
            log.error("Exception in %s.on_evaluate_finished: %s",
                      type(self).__name__, exc)
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._pending_status = None
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# MathConstantNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class MathConstantNode(_ConstantNodeBase):
    """
    Emits a fundamental mathematical constant as a ``float64`` scalar.

    Type: Threaded.

    Outputs
    -------
    value : float
        The selected mathematical constant.

    Parameters
    ----------
    title : str
        Node title (default ``"Math Constant"``).
    """

    _ENTRIES     = _MATH_ENTRIES
    _VALUES      = _MATH_VALUES
    _IDX_MAP     = _MATH_IDX_TO_VAL
    _DEFAULT_IDX = _MATH_DEFAULT_IDX
    _DEFAULT_VAL = _MATH_DEFAULT_VAL

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Constant"
    node_name:        ClassVar[Optional[str]] = "Math Constant"
    node_description: ClassVar[Optional[str]] = (
        "Emits a fundamental mathematical constant (π, e, φ, √2, γ, …)"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "constant", "math", "pi", "tau", "euler", "golden ratio",
        "sqrt2", "phi", "log", "zeta", "catalan", "primitive",
    ]

    def __init__(self, title: str = "Math Constant", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# ScientificConstantNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class ScientificConstantNode(_ConstantNodeBase):
    """
    Emits a physical / scientific constant (SI units) as a ``float64`` scalar.

    Type: Threaded.

    Outputs
    -------
    value : float
        The selected scientific constant in SI units.

    Parameters
    ----------
    title : str
        Node title (default ``"Scientific Constant"``).
    """

    _ENTRIES     = _SCI_ENTRIES
    _VALUES      = _SCI_VALUES
    _IDX_MAP     = _SCI_IDX_TO_VAL
    _DEFAULT_IDX = _SCI_DEFAULT_IDX
    _DEFAULT_VAL = _SCI_DEFAULT_VAL

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Constant"
    node_name:        ClassVar[Optional[str]] = "Scientific Constant"
    node_description: ClassVar[Optional[str]] = (
        "Emits a physical/scientific constant in SI units "
        "(c, h, k_B, N_A, G, e, …)"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "constant", "physics", "science", "si", "planck", "boltzmann",
        "avogadro", "speed of light", "electron", "gravity", "primitive",
    ]

    def __init__(self, title: str = "Scientific Constant", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
