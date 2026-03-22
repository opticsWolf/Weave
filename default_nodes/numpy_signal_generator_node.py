# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

numpy_signal_generator_node.py
-------------------------------
1-D signal generator node.  Emits a ``numpy.ndarray`` representing a
discrete-time signal constructed purely from NumPy (no SciPy dependency).

Provided node
-------------
``NumpySignalGeneratorNode``
    Source node that generates a 1-D ``ndarray`` of *n_samples* values.
    A *Signal* combo (with category headers) selects the waveform type.
    All numeric parameters have both a bidirectional spinbox and an
    auto-disable input port so upstream nodes can drive individual
    parameters while the spinbox reflects and falls back to the current
    value.

    Signal categories & types
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Periodic
        Sine, Cosine, Square (duty-cycle), Sawtooth Rising,
        Sawtooth Falling, Triangle, Pulse / PWM (duty-cycle)

    Transient
        Step (Heaviside), Ramp, Exponential Rise, Exponential Decay,
        Gaussian Envelope, Sinc

    Chirp / Sweep
        Chirp Linear (linear instantaneous frequency),
        Chirp Exponential (geometric frequency sweep)

    Noise
        White Noise Uniform, White Noise Normal (Gaussian),
        Pink Noise (~1/f via FFT colouring)

    Parameters (all have matching input ports + spinboxes)
    -------------------------------------------------------
    n_samples   int     Number of output samples          [2, 1 000 000]
    sample_rate float   Samples per second (Hz)           (0, 1e9]
    frequency   float   Signal frequency (Hz)             (0, 1e9]
    amplitude   float   Peak amplitude                    [-1e9, 1e9]
    offset      float   DC bias added after generation    [-1e9, 1e9]
    phase       float   Phase shift (radians)             [-2π, 2π]
    duty_cycle  float   Duty cycle 0–1 (Square / Pulse)   [0.001, 0.999]
    width       float   Shape width 0–1 (Gaussian, Sinc,  [1e-6, 1.0]
                        Exp time constant fraction)
    f_end       float   Chirp end frequency (Hz)          (0, 1e9]

    Visibility
    ~~~~~~~~~~
    Parameter rows are shown or hidden based on the selected signal so
    that only relevant controls are displayed:

    ============== ==========================================
    Signal group   Visible parameter rows
    ============== ==========================================
    All signals    n_samples, sample_rate, amplitude, offset
    Periodic       + frequency, phase
    Square / Pulse + frequency, phase, duty_cycle
    Sinc           + frequency, phase, width
    Step           + phase  (used as step-location fraction)
    Transient      + width  (time-constant / std-dev)
    Chirp          + frequency, phase, f_end
    Noise          (no extra rows)
    ============== ==========================================

    Output
    ------
    signal : ndarray  shape (n_samples,)  dtype float64
    time   : ndarray  shape (n_samples,)  dtype float64
        The time-axis array ``numpy.linspace(0, (n_samples-1)/sample_rate,
        n_samples)`` so downstream nodes can plot or process without
        recomputing the axis.

    Error handling
    ~~~~~~~~~~~~~~
    All exceptions in ``compute`` are caught, logged at WARNING, and
    reflected in the status label; empty ``float64`` arrays are emitted
    on failure.

Serialisation
-------------
All widget state (signal type, dtype, every parameter spinbox) is
handled by WidgetCore's built-in ``get_state()`` / ``set_state()``
because those widgets are registered with ``role="bidirectional"`` or
``role="internal"``.

``restore_state`` overrides the base-class implementation to clear
``_pending_status`` and ``_noise_cache`` before delegating to
``super()``, and then calls ``_sync_param_visibility()`` so that
parameter rows hidden for the restored signal type are correctly
concealed — without this step the combo selection is restored but the
row visibility is not updated until the user manually interacts with
the combo.
"""

from __future__ import annotations

import numpy as np
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Tuple

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QStandardItem
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

from weave.logger import get_logger

log = get_logger("NumpySignalGeneratorNode")


# ── Operation registry ────────────────────────────────────────────────────────

_SIG_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("Periodic",      "periodic"),
    ("Transient",     "transient"),
    ("Chirp / Sweep", "chirp"),
    ("Noise",         "noise"),
)

_SIGS_BY_CAT: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "periodic": (
        ("Sine",                  "sine"),
        ("Cosine",                "cosine"),
        ("Square",                "square"),
        ("Sawtooth  (rising)",    "sawtooth_rise"),
        ("Sawtooth  (falling)",   "sawtooth_fall"),
        ("Triangle",              "triangle"),
        ("Pulse / PWM",           "pulse"),
    ),
    "transient": (
        ("Step  (Heaviside)",     "step"),
        ("Ramp  (linear)",        "ramp"),
        ("Exponential Rise",      "exp_rise"),
        ("Exponential Decay",     "exp_decay"),
        ("Gaussian Envelope",     "gaussian"),
        ("Sinc",                  "sinc"),
    ),
    "chirp": (
        ("Chirp  (linear)",       "chirp_linear"),
        ("Chirp  (exponential)",  "chirp_exp"),
    ),
    "noise": (
        ("White  (uniform)",      "noise_uniform"),
        ("White  (normal)",       "noise_normal"),
        ("Pink   (~1/f)",         "noise_pink"),
    ),
}


def _build_flat_sigs() -> Tuple[Tuple[str, Optional[str]], ...]:
    flat: List[Tuple[str, Optional[str]]] = []
    for cat_label, cat_key in _SIG_CATEGORIES:
        flat.append((f"── {cat_label} ──", None))
        for sig_label, sig_key in _SIGS_BY_CAT[cat_key]:
            flat.append((sig_label, sig_key))
    return tuple(flat)


_SIG_FLAT: Tuple[Tuple[str, Optional[str]], ...] = _build_flat_sigs()

_SIG_INDEX_TO_VALUE: Dict[int, str] = {
    i: sig_key
    for i, (_, sig_key) in enumerate(_SIG_FLAT)
    if sig_key is not None
}
_SIG_VALUE_TO_INDEX: Dict[str, int] = {v: k for k, v in _SIG_INDEX_TO_VALUE.items()}

_SIG_DEFAULT_INDEX: int = next(iter(_SIG_INDEX_TO_VALUE))   # "sine"
_SIG_DEFAULT_VALUE: str = _SIG_FLAT[_SIG_DEFAULT_INDEX][1]

_DTYPE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("float64",    "float64"),
    ("float32",    "float32"),
    ("complex128", "complex128"),
)

# ── Parameter visibility map ──────────────────────────────────────────────────
# Maps each signal key to the frozenset of parameter-group tags that should
# be VISIBLE.  Tags: "freq_phase", "duty", "width", "f_end".
# The base group (n_samples, sample_rate, amplitude, offset) is always visible.

_VIS: Dict[str, FrozenSet[str]] = {
    "sine":          frozenset({"freq_phase"}),
    "cosine":        frozenset({"freq_phase"}),
    "square":        frozenset({"freq_phase", "duty"}),
    "sawtooth_rise": frozenset({"freq_phase"}),
    "sawtooth_fall": frozenset({"freq_phase"}),
    "triangle":      frozenset({"freq_phase"}),
    "pulse":         frozenset({"freq_phase", "duty"}),
    "step":          frozenset({"freq_phase"}),   # phase = step location (rad→frac)
    "ramp":          frozenset(),
    "exp_rise":      frozenset({"width"}),
    "exp_decay":     frozenset({"width"}),
    "gaussian":      frozenset({"freq_phase", "width"}),
    "sinc":          frozenset({"freq_phase", "width"}),
    "chirp_linear":  frozenset({"freq_phase", "f_end"}),
    "chirp_exp":     frozenset({"freq_phase", "f_end"}),
    "noise_uniform": frozenset(),
    "noise_normal":  frozenset(),
    "noise_pink":    frozenset(),
}


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# ══════════════════════════════════════════════════════════════════════════════
# NumpySignalGeneratorNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class NumpySignalGeneratorNode(ThreadedNode):
    """
    1-D discrete-time signal generator.

    Type: Threaded (compute() runs on QThreadPool; propagates downstream
    on any widget or port change).

    Inputs  (all auto-disable their matching spinbox when connected)
    ------
    n_samples   : int
    sample_rate : float
    frequency   : float
    amplitude   : float
    offset      : float
    phase       : float
    duty_cycle  : float
    width       : float
    f_end       : float

    Outputs
    -------
    signal : ndarray  shape (n_samples,)
    time   : ndarray  shape (n_samples,)

    Parameters
    ----------
    title : str
        Node title (default ``"Signal Generator"``).
    """

    signal_changed = Signal(object)   # emits signal ndarray

    node_class:       ClassVar[str]           = "Numpy"
    node_subclass:    ClassVar[str]           = "Generator"
    node_name:        ClassVar[Optional[str]] = "Signal Generator"
    node_description: ClassVar[Optional[str]] = (
        "Generates a 1-D discrete-time signal array (sine, square, "
        "chirp, noise, …) with configurable parameters"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "numpy", "signal", "generator", "sine", "cosine", "square",
        "sawtooth", "triangle", "chirp", "noise", "waveform", "1d",
    ]

    def __init__(self, title: str = "Signal Generator", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Ports ─────────────────────────────────────────────────────
        _auto_port = lambda name, dtype: (  # noqa: E731
            self.add_input(name, dtype),
            setattr(self.inputs[-1], "_auto_disable", True),
        )
        _auto_port("n_samples",   "int")
        _auto_port("sample_rate", "float")
        _auto_port("frequency",   "float")
        _auto_port("amplitude",   "float")
        _auto_port("offset",      "float")
        _auto_port("phase",       "float")
        _auto_port("duty_cycle",  "float")
        _auto_port("width",       "float")
        _auto_port("f_end",       "float")

        self.add_output("signal", "ndarray")
        self.add_output("time",   "ndarray")

        # ── Form layout ───────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # ── Signal combo ──────────────────────────────────────────────
        self._combo_sig = QComboBox()
        self._combo_sig.setMinimumWidth(180)
        self._populate_sig_combo(self._combo_sig)
        form.addRow("Signal:", self._combo_sig)
        self._widget_core.register_widget(
            "signal_type", self._combo_sig,
            role="internal", datatype="string", default=_SIG_DEFAULT_VALUE,
            add_to_layout=False,
        )
        # Visibility sync fires before on_ui_change (via WidgetCore path)
        self._combo_sig.currentIndexChanged.connect(self._on_sig_changed)

        # ── Dtype combo ───────────────────────────────────────────────
        self._combo_dtype = QComboBox()
        for label, _ in _DTYPE_OPTIONS:
            self._combo_dtype.addItem(label)
        form.addRow("Dtype:", self._combo_dtype)
        self._widget_core.register_widget(
            "dtype", self._combo_dtype,
            role="internal", datatype="string", default="float64",
            add_to_layout=False,
        )

        form.addRow(_make_separator())

        # ── Base parameters (always visible) ──────────────────────────
        self._spin_n_samples = self._make_int_spin(1_000, 2, 1_000_000)
        form.addRow("N Samples:", self._spin_n_samples)
        self._widget_core.register_widget(
            "n_samples", self._spin_n_samples,
            role="bidirectional", datatype="int", default=1_000,
            add_to_layout=False,
        )

        self._spin_sample_rate = self._make_float_spin(1_000.0, 1e-3, 1e9)
        form.addRow("Sample Rate:", self._spin_sample_rate)
        self._widget_core.register_widget(
            "sample_rate", self._spin_sample_rate,
            role="bidirectional", datatype="float", default=1_000.0,
            add_to_layout=False,
        )

        self._spin_amplitude = self._make_float_spin(1.0, -1e9, 1e9)
        form.addRow("Amplitude:", self._spin_amplitude)
        self._widget_core.register_widget(
            "amplitude", self._spin_amplitude,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        self._spin_offset = self._make_float_spin(0.0, -1e9, 1e9)
        form.addRow("Offset:", self._spin_offset)
        self._widget_core.register_widget(
            "offset", self._spin_offset,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )

        # ── freq_phase group ──────────────────────────────────────────
        self._label_frequency = QLabel("Frequency (Hz):")
        self._spin_frequency  = self._make_float_spin(1.0, 1e-9, 1e9)
        form.addRow(self._label_frequency, self._spin_frequency)
        self._widget_core.register_widget(
            "frequency", self._spin_frequency,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        self._label_phase = QLabel("Phase (rad):")
        self._spin_phase  = self._make_float_spin(0.0, -2 * np.pi, 2 * np.pi, decimals=5)
        form.addRow(self._label_phase, self._spin_phase)
        self._widget_core.register_widget(
            "phase", self._spin_phase,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )

        # ── duty group ────────────────────────────────────────────────
        self._label_duty  = QLabel("Duty Cycle:")
        self._spin_duty   = self._make_float_spin(0.5, 0.001, 0.999, decimals=4)
        form.addRow(self._label_duty, self._spin_duty)
        self._widget_core.register_widget(
            "duty_cycle", self._spin_duty,
            role="bidirectional", datatype="float", default=0.5,
            add_to_layout=False,
        )

        # ── width group ───────────────────────────────────────────────
        self._label_width = QLabel("Width (0–1):")
        self._spin_width  = self._make_float_spin(0.1, 1e-6, 1.0, decimals=6)
        form.addRow(self._label_width, self._spin_width)
        self._widget_core.register_widget(
            "width", self._spin_width,
            role="bidirectional", datatype="float", default=0.1,
            add_to_layout=False,
        )

        # ── f_end group ───────────────────────────────────────────────
        self._label_f_end = QLabel("End Freq (Hz):")
        self._spin_f_end  = self._make_float_spin(100.0, 1e-9, 1e9)
        form.addRow(self._label_f_end, self._spin_f_end)
        self._widget_core.register_widget(
            "f_end", self._spin_f_end,
            role="bidirectional", datatype="float", default=100.0,
            add_to_layout=False,
        )

        # ── Status display ────────────────────────────────────────────
        form.addRow(_make_separator())
        self._label_status = QLabel("--")
        self._label_status.setEnabled(False)
        self._label_status.setWordWrap(True)
        self._label_status.setMinimumWidth(160)
        form.addRow(self._label_status)

        # ── Parameter-group visibility map ────────────────────────────
        # Maps tag → list of (QLabel, QDoubleSpinBox) tuples
        self._vis_groups: Dict[str, List[Tuple[QLabel, QDoubleSpinBox]]] = {
            "freq_phase": [
                (self._label_frequency, self._spin_frequency),
                (self._label_phase,     self._spin_phase),
            ],
            "duty": [
                (self._label_duty, self._spin_duty),
            ],
            "width": [
                (self._label_width, self._spin_width),
            ],
            "f_end": [
                (self._label_f_end, self._spin_f_end),
            ],
        }

        # ── Wire ──────────────────────────────────────────────────────
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._sync_param_visibility()
        self._widget_core.refresh_widget_palettes()

        self._pending_status: Optional[str] = None

        # ── Noise cache ───────────────────────────────────────────────
        # Keyed by (sig_type, n, amplitude, offset); avoids re-generating
        # a new random array on every evaluation when nothing has changed.
        self._noise_cache: Optional[Dict[str, Any]] = None

    # ── Widget factory helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_float_spin(
        default: float,
        lo: float,
        hi: float,
        decimals: int = 4,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.setDecimals(decimals)
        spin.setMinimumWidth(110)
        return spin

    @staticmethod
    def _make_int_spin(default: int, lo: int, hi: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        spin.setMinimumWidth(110)
        return spin

    # ── Combo population ──────────────────────────────────────────────────────

    @staticmethod
    def _populate_sig_combo(combo: QComboBox) -> None:
        from PySide6.QtCore import Qt
        for label, sig_key in _SIG_FLAT:
            combo.addItem(label)
            if sig_key is None:
                model = combo.model()
                item: QStandardItem = model.item(combo.count() - 1)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        combo.setCurrentIndex(_SIG_DEFAULT_INDEX)

    # ── Visibility sync ───────────────────────────────────────────────────────

    def _current_sig_key(self) -> str:
        idx = self._combo_sig.currentIndex()
        return _SIG_INDEX_TO_VALUE.get(idx, _SIG_DEFAULT_VALUE)

    def _sync_param_visibility(self) -> None:
        """Show only parameter rows relevant to the current signal type."""
        visible_tags = _VIS.get(self._current_sig_key(), frozenset())
        for tag, pairs in self._vis_groups.items():
            show = tag in visible_tags
            for label, spin in pairs:
                label.setVisible(show)
                spin.setVisible(show)

    # ── Widget snapshot ───────────────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture all combo and spinbox values before worker dispatch."""
        sig_idx   = self._combo_sig.currentIndex()
        dtype_idx = self._combo_dtype.currentIndex()
        return {
            "_ui_signal_type": (
                _SIG_INDEX_TO_VALUE.get(sig_idx, _SIG_DEFAULT_VALUE)
            ),
            "_ui_dtype": (
                _DTYPE_OPTIONS[dtype_idx][1]
                if 0 <= dtype_idx < len(_DTYPE_OPTIONS) else "float64"
            ),
            "_ui_n_samples":   self._spin_n_samples.value(),
            "_ui_sample_rate": self._spin_sample_rate.value(),
            "_ui_amplitude":   self._spin_amplitude.value(),
            "_ui_offset":      self._spin_offset.value(),
            "_ui_frequency":   self._spin_frequency.value(),
            "_ui_phase":       self._spin_phase.value(),
            "_ui_duty_cycle":  self._spin_duty.value(),
            "_ui_width":       self._spin_width.value(),
            "_ui_f_end":       self._spin_f_end.value(),
        }

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(int)
    def _on_sig_changed(self, _index: int) -> None:
        try:
            self._sync_param_visibility()
        except Exception as exc:
            log.error("Exception in NumpySignalGeneratorNode._on_sig_changed: %s", exc)

    @Slot(str)
    def _on_core_changed(self, _port_name: str) -> None:
        try:
            self.on_ui_change()
        except Exception as exc:
            log.error("Exception in NumpySignalGeneratorNode._on_core_changed: %s", exc)

    # ── Computation ───────────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate the signal array from current parameters.

        Runs on the worker thread — no Qt widget access.
        All parameter values arrive via ``inputs``: upstream port values
        are preferred; ``_ui_`` keys are the spinbox fallback.
        """
        _EMPTY = np.array([], dtype=np.float64)

        def _p_f(name: str, default: float) -> float:
            v = inputs.get(name)
            if v is not None:
                return float(v)
            return float(inputs.get(f"_ui_{name}", default))

        def _p_i(name: str, default: int) -> int:
            v = inputs.get(name)
            if v is not None:
                return max(2, int(v))
            return max(2, int(inputs.get(f"_ui_{name}", default)))

        try:
            sig_type    = inputs.get("_ui_signal_type", _SIG_DEFAULT_VALUE)
            dtype_key   = inputs.get("_ui_dtype",       "float64")

            n           = _p_i("n_samples",   1_000)
            sample_rate = _p_f("sample_rate", 1_000.0)
            amplitude   = _p_f("amplitude",   1.0)
            offset      = _p_f("offset",      0.0)
            frequency   = max(1e-9, _p_f("frequency", 1.0))
            phase       = _p_f("phase",       0.0)
            duty_cycle  = np.clip(_p_f("duty_cycle", 0.5), 1e-6, 1.0 - 1e-6)
            width       = np.clip(_p_f("width",      0.1),  1e-9, 1.0)
            f_end       = max(1e-9, _p_f("f_end",    100.0))

            t = np.linspace(0.0, (n - 1) / sample_rate, n)

            _NOISE_TYPES = {"noise_uniform", "noise_normal", "noise_pink"}
            if sig_type in _NOISE_TYPES:
                # Only regenerate when the noise-relevant parameters change.
                _noise_key = (sig_type, n, amplitude, offset)
                if (
                    self._noise_cache is not None
                    and self._noise_cache["key"] == _noise_key
                ):
                    sig = self._noise_cache["signal"]
                else:
                    sig = self._generate(
                        sig_type, t, n, sample_rate,
                        amplitude, offset, frequency, phase,
                        duty_cycle, width, f_end,
                    )
                    self._noise_cache = {"key": _noise_key, "signal": sig}
            else:
                # Non-noise signal — discard any stale noise cache.
                self._noise_cache = None
                sig = self._generate(
                    sig_type, t, n, sample_rate,
                    amplitude, offset, frequency, phase,
                    duty_cycle, width, f_end,
                )

            sig = sig.astype(dtype_key, copy=False)

            shape_str = str(sig.shape[0])
            self._pending_status = (
                f"samples: {shape_str}  sr: {sample_rate:.0f} Hz\n"
                f"dtype: {sig.dtype}"
            )
            log.debug(
                "NumpySignalGeneratorNode: %s  n=%d  sr=%.0f",
                sig_type, n, sample_rate,
            )
            return {"signal": sig, "time": t}

        except Exception as exc:
            log.warning("NumpySignalGeneratorNode.compute: %s", exc)
            self._pending_status = f"error: {exc}"
            return {"signal": _EMPTY, "time": _EMPTY}

    @staticmethod
    def _generate(  # noqa: C901
        sig_type: str,
        t: np.ndarray,
        n: int,
        sample_rate: float,
        amplitude: float,
        offset: float,
        frequency: float,
        phase: float,
        duty_cycle: float,
        width: float,
        f_end: float,
    ) -> np.ndarray:
        """
        Pure-NumPy signal generation dispatcher.

        All periodic signals use the time axis *t* so that the output
        accurately reflects the sample-rate / frequency relationship.
        """
        T = t[-1] - t[0] if len(t) > 1 else 1.0   # total duration (s)

        # ── Periodic ──────────────────────────────────────────────────
        if sig_type == "sine":
            return amplitude * np.sin(2.0 * np.pi * frequency * t + phase) + offset

        if sig_type == "cosine":
            return amplitude * np.cos(2.0 * np.pi * frequency * t + phase) + offset

        if sig_type == "square":
            # Generalised square wave with arbitrary duty cycle
            phi = (frequency * t + phase / (2.0 * np.pi)) % 1.0
            return amplitude * np.where(phi < duty_cycle, 1.0, -1.0) + offset

        if sig_type == "sawtooth_rise":
            phi = (frequency * t + phase / (2.0 * np.pi)) % 1.0
            return amplitude * (2.0 * phi - 1.0) + offset

        if sig_type == "sawtooth_fall":
            phi = (frequency * t + phase / (2.0 * np.pi)) % 1.0
            return amplitude * (1.0 - 2.0 * phi) + offset

        if sig_type == "triangle":
            phi = (frequency * t + phase / (2.0 * np.pi)) % 1.0
            return amplitude * (2.0 * np.abs(2.0 * phi - 1.0) - 1.0) + offset

        if sig_type == "pulse":
            phi = (frequency * t + phase / (2.0 * np.pi)) % 1.0
            return amplitude * np.where(phi < duty_cycle, 1.0, 0.0) + offset

        # ── Transient ─────────────────────────────────────────────────
        if sig_type == "step":
            # phase maps [-2π, 2π] → step location as fraction of total time.
            # 0 = midpoint, -2π = very start, +2π = very end.
            t_step = T * (phase / (2.0 * np.pi) + 0.5)
            return amplitude * np.heaviside(t - t_step, 0.5) + offset

        if sig_type == "ramp":
            if T == 0.0:
                return np.full(n, offset)
            return amplitude * (t / T) + offset

        if sig_type == "exp_rise":
            tau = max(width * T, 1e-12)
            return amplitude * (1.0 - np.exp(-t / tau)) + offset

        if sig_type == "exp_decay":
            tau = max(width * T, 1e-12)
            return amplitude * np.exp(-t / tau) + offset

        if sig_type == "gaussian":
            t_center = T * 0.5
            sigma    = max(width * T, 1e-12)
            return (
                amplitude * np.exp(-0.5 * ((t - t_center) / sigma) ** 2) + offset
            )

        if sig_type == "sinc":
            # width controls the main-lobe half-width in seconds
            t_center  = T * 0.5
            half_lobe = max(width * T, 1e-12)
            arg       = frequency * (t - t_center)
            return amplitude * np.sinc(arg) + offset

        # ── Chirp / Sweep ─────────────────────────────────────────────
        if sig_type == "chirp_linear":
            # Instantaneous frequency sweeps linearly from `frequency` to `f_end`
            k = (f_end - frequency) / max(T, 1e-12)
            return (
                amplitude
                * np.sin(2.0 * np.pi * (frequency * t + 0.5 * k * t ** 2) + phase)
                + offset
            )

        if sig_type == "chirp_exp":
            # Instantaneous frequency sweeps geometrically (log-linear)
            if frequency <= 0 or f_end <= 0:
                raise ValueError("Chirp exp requires frequency > 0 and f_end > 0")
            if frequency == f_end:
                # Degenerate: behave like sine
                return (
                    amplitude * np.sin(2.0 * np.pi * frequency * t + phase) + offset
                )
            log_ratio = np.log(f_end / frequency)
            phase_arg = (
                2.0 * np.pi * frequency * T / log_ratio
                * (np.exp(t / T * log_ratio) - 1.0)
            ) + phase
            return amplitude * np.sin(phase_arg) + offset

        # ── Noise ─────────────────────────────────────────────────────
        if sig_type == "noise_uniform":
            return amplitude * np.random.uniform(-1.0, 1.0, n) + offset

        if sig_type == "noise_normal":
            return amplitude * np.random.normal(0.0, 1.0, n) + offset

        if sig_type == "noise_pink":
            # 1/f colouring via FFT: generate white noise, shape spectrum,
            # inverse-transform, then normalise to ±1 before applying amplitude.
            white    = np.random.normal(0.0, 1.0, n)
            freqs    = np.fft.rfftfreq(n)
            spectrum = np.fft.rfft(white)
            # Avoid division by zero at DC; leave DC at 0
            with np.errstate(divide="ignore", invalid="ignore"):
                psd_shape = np.where(freqs == 0.0, 0.0, 1.0 / np.sqrt(freqs))
            coloured = np.fft.irfft(spectrum * psd_shape, n=n)
            peak     = np.max(np.abs(coloured))
            if peak > 0.0:
                coloured /= peak
            return amplitude * coloured + offset

        raise ValueError(f"Unknown signal type key: {sig_type!r}")

    # ── Serialisation ────────────────────────────────────────────────────────

    def restore_state(self, state: Dict[str, Any]) -> None:
        """
        Restores WidgetCore state (signal type, dtype, all parameter
        spinboxes) via ``super()``, then re-syncs parameter-row visibility.

        Without this override the signal combo is correctly restored by the
        base class, but ``_sync_param_visibility`` is never called afterward.
        This leaves rows that should be hidden (e.g. *Duty Cycle* for a Sine
        signal) incorrectly visible until the user manually changes the combo —
        the same class of post-restore UI-sync omission fixed in
        ``NumpyArrayNode`` by calling ``_sync_fill_value_visibility`` and in
        ``MultiFloatOutputNode`` by rebuilding its dynamic widget set.

        Also clears ``_pending_status`` and ``_noise_cache`` before restoring
        so no stale in-flight state leaks across sessions.
        """
        log.debug(
            "NumpySignalGeneratorNode.restore_state: start, "
            "inputs=%s", [p.name for p in self.inputs]
        )

        # ── 1. Clear transient state ──────────────────────────────────────
        self._pending_status = None
        self._noise_cache    = None

        # ── 2. Restore WidgetCore values (signal type, dtype, spinboxes) ─
        super().restore_state(state)

        # ── 3. Re-sync parameter-row visibility from restored combo ───────
        # super() restores the combo selection but does not call
        # _sync_param_visibility, so visibility must be re-derived here.
        self._sync_param_visibility()
        self._widget_core.refresh_widget_palettes()

        log.debug(
            "NumpySignalGeneratorNode.restore_state: done, "
            "signal=%s", self._current_sig_key()
        )

    # ── Post-evaluation UI flush ──────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Flush the status label and emit signal_changed on the main thread."""
        try:
            if self._pending_status is not None:
                try:
                    self._label_status.setText(self._pending_status)
                except RuntimeError:
                    pass
                self._pending_status = None

            result = self.get_output_value("signal")
            if result is not None:
                self.signal_changed.emit(result)
        except Exception as exc:
            log.error(
                "Exception in NumpySignalGeneratorNode.on_evaluate_finished: %s", exc
            )
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._pending_status = None
        self._noise_cache = None
        self._widget_core.cleanup()
        super().cleanup()
