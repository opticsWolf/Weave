# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodePulseAnimMixin - Configurable Pulse Animation for Node.

Handles:
- Starting / stopping a looping glow-pulse animation
- Multiple pulse waveform types selectable by keyword
- All visual parameters (width, opacity, duration, layers, easing, offset)
  resolved from StyleManager config so each theme can define its own feel
- Paint helper that renders the pulse glow + border onto the node
- Glow layers are offset outward from the node border by ``pulse_glow_offset``
  and fade in opacity towards the outside

Waveform types:
    'breathe'   — smooth sine triangle wave (0→1→0), the classic default
    'flash'     — sharp on/off with a brief sustain at peak
    'heartbeat' — double-peak mimicking a cardiac rhythm
    'ripple'    — fast attack, slow exponential decay
    'sawtooth'  — linear ramp up, instant drop
    'orbital'   — traveling pulse around node perimeter with breathing effect
                  Rotation always uses Linear easing for constant angular
                  velocity; the breathing width modulation uses the easing
                  curve defined in the theme (e.g. InOutSine).

Integration:
    The host Node class should:
    1. Call ``_init_pulse_anim()`` at the end of ``__init__``
    2. Replace direct ``_computing_pulse_anim`` setup with this mixin
    3. Call ``_paint_pulse()`` from ``paint()`` in the computing-glow section
    4. Include ``pulse_*`` fields in the theme's ``NodeStyleSchema``
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, TYPE_CHECKING

from PySide6.QtCore import QVariantAnimation, QEasingCurve, QRectF
from PySide6.QtGui import QColor, QPen, QPainter, QPainterPath, QConicalGradient, QBrush
from PySide6.QtCore import Qt

from weave.logger import get_logger

if TYPE_CHECKING:
    pass  # Node forward reference not needed; mixin binds at runtime

log = get_logger("NodePulseAnimMixin")


# ======================================================================
# Waveform Functions
# ======================================================================
# Each receives `t` in [0.0, 1.0] (the raw sawtooth from
# QVariantAnimation) and returns a phase value in [0.0, 1.0].

def _wave_breathe(t: float) -> float:
    """Smooth sine triangle: 0 → 1 → 0."""
    return 1.0 - abs(2.0 * t - 1.0)


def _wave_flash(t: float) -> float:
    """Sharp attack, brief sustain at peak, sharp release.

    Profile: ramp 0→1 in first 15 %, hold at 1.0 for 20 %, ramp down
    in next 15 %, idle at 0 for the remaining 50 %.
    """
    if t < 0.15:
        return t / 0.15
    elif t < 0.35:
        return 1.0
    elif t < 0.50:
        return 1.0 - (t - 0.35) / 0.15
    else:
        return 0.0


def _wave_heartbeat(t: float) -> float:
    """Double-peak cardiac rhythm.

    Two bumps: a large peak at ~25 % and a smaller secondary at ~55 %,
    with a rest period filling the remainder of the cycle.
    """
    if t < 0.30:
        # Primary peak (sine half-wave scaled to [0, 0.30])
        return math.sin(math.pi * t / 0.30)
    elif t < 0.40:
        # Brief valley
        return 0.0
    elif t < 0.60:
        # Secondary peak (lower amplitude)
        return 0.6 * math.sin(math.pi * (t - 0.40) / 0.20)
    else:
        return 0.0


def _wave_ripple(t: float) -> float:
    """Fast attack, slow exponential decay."""
    if t < 0.08:
        return t / 0.08
    else:
        # Exponential decay from 1.0 over the remaining 92 % of the cycle
        decay = (t - 0.08) / 0.92
        return math.exp(-4.0 * decay)


def _wave_sawtooth(t: float) -> float:
    """Linear ramp up, instant drop."""
    return t


def _wave_orbital(t: float) -> float:
    """Linear pass-through for constant-velocity orbital rotation.

    Functionally identical to ``_wave_sawtooth`` but kept separate so
    ``_apply_pulse_timing`` can detect the 'orbital' key by name and
    force ``Linear`` easing on the animation — ensuring the gradient
    rotates at a constant angular velocity regardless of the theme's
    ``pulse_easing`` setting.  The easing curve is instead applied to
    the width-breathing effect inside ``_paint_orbital_pulse``.
    """
    return t


# Registry mapping keyword → function
PULSE_WAVEFORMS: Dict[str, callable] = {
    'breathe':   _wave_breathe,
    'flash':     _wave_flash,
    'heartbeat': _wave_heartbeat,
    'ripple':    _wave_ripple,
    'sawtooth':  _wave_sawtooth,
    'orbital':   _wave_orbital,
}


# ======================================================================
# Mixin
# ======================================================================

class NodePulseAnimMixin:
    """
    Mixin providing a configurable looping pulse-glow animation for Node.

    Expects the host class to have:
        - self._config: Dict[str, Any]          (NodeStyleSchema values)
        - self.header._bg_color: QColor
        - self._cached_sel_path: QPainterPath
        - self.update()                          (schedule repaint)
    """

    # ------------------------------------------------------------------
    # Initialisation (call from Node.__init__)
    # ------------------------------------------------------------------

    def _init_pulse_anim(self) -> None:
        """Create the QVariantAnimation used for the computing pulse.

        Reads initial duration and easing from ``_config``.  Call this
        once at the end of ``Node.__init__``, **replacing** the manual
        ``_computing_pulse_anim`` setup that previously lived there.
        """
        self._computing_pulse_phase: float = 0.0
        self._pulse_active: bool = False
        self._active_waveform_key: str = self._config.get(
            'pulse_waveform', 'breathe'
        )

        self._computing_pulse_anim = QVariantAnimation(self)
        self._computing_pulse_anim.setStartValue(0.0)
        self._computing_pulse_anim.setEndValue(1.0)
        self._computing_pulse_anim.setLoopCount(-1)

        # Apply theme-driven timing
        self._apply_pulse_timing()

        self._computing_pulse_anim.valueChanged.connect(
            self._on_pulse_tick
        )

    # ------------------------------------------------------------------
    # Timing helpers
    # ------------------------------------------------------------------

    def _apply_pulse_timing(self) -> None:
        """(Re-)apply duration and easing curve from ``_config``.

        For the 'orbital' waveform the animation easing is always forced
        to ``Linear`` so the gradient rotates at constant angular velocity.
        The theme's ``pulse_easing`` value is still honoured by the orbital
        width-breathing effect inside ``_paint_orbital_pulse``.

        Safe to call while the animation is running — QVariantAnimation
        picks up the new duration on the next loop iteration.
        """
        cfg = self._config

        duration = cfg.get('pulse_duration', 1200)
        self._computing_pulse_anim.setDuration(int(duration))

        if self._active_waveform_key == 'orbital':
            # Rotation must be constant-velocity; easing is applied to
            # the breathing effect in _paint_orbital_pulse instead.
            self._computing_pulse_anim.setEasingCurve(QEasingCurve.Type.Linear)
        else:
            easing_name: str = cfg.get('pulse_easing', 'InOutSine')
            easing_type = _resolve_easing(easing_name)
            self._computing_pulse_anim.setEasingCurve(easing_type)

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _start_computing_pulse(self) -> None:
        """Start the looping pulse glow animation for COMPUTING state."""
        if self._computing_pulse_anim.state() != QVariantAnimation.State.Running:
            self._computing_pulse_phase = 0.0

            # Refresh waveform selection (theme may have changed since last run)
            self._active_waveform_key = self._config.get(
                'pulse_waveform', 'breathe'
            )
            self._apply_pulse_timing()

            self._pulse_active = True
            self._computing_pulse_anim.start()

    def _stop_computing_pulse(self) -> None:
        """Stop the pulse animation and clear the phase.

        The ``_pulse_active`` flag is cleared **before** calling ``stop()``
        so that any ``valueChanged`` signal emitted synchronously during
        the stop (or still queued from the event loop) is discarded by
        ``_on_pulse_tick`` and cannot re-set a non-zero phase.
        """
        self._pulse_active = False
        if self._computing_pulse_anim.state() == QVariantAnimation.State.Running:
            self._computing_pulse_anim.stop()
        self._computing_pulse_phase = 0.0
        self.update()

    # ------------------------------------------------------------------
    # Tick (connected to valueChanged)
    # ------------------------------------------------------------------

    def _on_pulse_tick(self, value) -> None:
        """Convert the raw 0→1 sawtooth into the selected waveform shape.

        Guarded by ``_pulse_active`` so that stale or queued ticks that
        arrive after ``_stop_computing_pulse`` are silently discarded,
        preventing the phase from being re-set to a non-zero value.
        """
        if value is None or not self._pulse_active:
            return

        t = float(value)

        waveform_fn = PULSE_WAVEFORMS.get(
            self._active_waveform_key, _wave_breathe
        )
        self._computing_pulse_phase = waveform_fn(t)
        self.update()

    # ------------------------------------------------------------------
    # Waveform selection at runtime
    # ------------------------------------------------------------------

    def set_pulse_waveform(self, name: str) -> None:
        """Switch the active waveform by keyword.

        Also re-applies pulse timing so that the animation easing is
        immediately correct for the new waveform (e.g. 'orbital' forces
        Linear easing on the animation itself).

        Args:
            name: One of ``'breathe'``, ``'flash'``, ``'heartbeat'``,
                  ``'ripple'``, ``'sawtooth'``, ``'orbital'``.  Unknown
                  names fall back to ``'breathe'`` with a warning.
        """
        if name not in PULSE_WAVEFORMS:
            log.warning(
                f"Unknown pulse waveform '{name}'. "
                f"Available: {sorted(PULSE_WAVEFORMS)}. Falling back to 'breathe'."
            )
            name = 'breathe'
        self._active_waveform_key = name
        # Re-evaluate easing: orbital forces Linear, all others use the theme value.
        self._apply_pulse_timing()

    def get_pulse_waveform(self) -> str:
        """Return the keyword of the currently active waveform."""
        return self._active_waveform_key

    @staticmethod
    def available_waveforms() -> list[str]:
        """Return the list of registered waveform keywords."""
        return sorted(PULSE_WAVEFORMS.keys())

    # ------------------------------------------------------------------
    # Custom waveform registration
    # ------------------------------------------------------------------

    @staticmethod
    def register_waveform(name: str, fn: callable) -> None:
        """Register a custom waveform function.

        Args:
            name: Keyword string (must not collide with builtins unless
                  you intend to override them).
            fn:   Callable ``(t: float) -> float`` where *t* is in
                  [0, 1] and the return value should be in [0, 1].
        """
        PULSE_WAVEFORMS[name] = fn

    # ------------------------------------------------------------------
    # Geometry helper
    # ------------------------------------------------------------------

    def _make_offset_path(self, extra_offset: float) -> QPainterPath:
        """Return a rounded-rect path offset ``extra_offset`` pixels outward from ``border_path``.

        Mirrors the pattern in ``NodeGeometryMixin._recalculate_paths``:
        the base rect (0,0,w,h) is expanded by ``sel_border_offset + extra_offset``
        and the corner radius is scaled by the same amount.

        Args:
            extra_offset: Additional outward offset beyond ``sel_border_offset``,
                          in local-item pixels (e.g. ``pulse_glow_offset`` or
                          ``pulse_glow_offset + active_width``).
        """
        w     = self._width
        h     = self._total_height
        eff_r = min(self._config.get('radius', 8), h / 2)
        total = self._config.get('sel_border_offset', 0) + extra_offset
        rect  = QRectF(0, 0, w, h).adjusted(-total, -total, total, total)
        path  = QPainterPath()
        path.addRoundedRect(rect, eff_r + total, eff_r + total)
        return path

    # ------------------------------------------------------------------
    # Paint helper (call from Node.paint)
    # ------------------------------------------------------------------

    def _paint_pulse(
        self,
        painter: QPainter,
        border_path: QPainterPath,
    ) -> None:
        """Render the pulse glow and border for the current phase.

        ``border_path`` (typically ``self._cached_sel_path``) is the sole
        geometric reference.  All glow layers are derived from it using the
        ``pulse_glow_offset`` style parameter — no pre-expanded path is
        required from the host.

        For standard waveforms the layers are drawn outward from
        ``border_path``, fading in opacity towards the outside.
        For the 'orbital' waveform delegates to ``_paint_orbital_pulse``.

        Call from ``paint()`` like::

            if self._computing_pulse_phase > 0.001:
                self._paint_pulse(painter, self._cached_sel_path)

        All visual tuning knobs are read from ``self._config``.
        """
        phase = self._computing_pulse_phase
        if phase < 0.001:
            return

        if self._active_waveform_key == 'orbital':
            self._paint_orbital_pulse(painter, border_path)
            return

        cfg = self._config

        # --- Resolve colour source ---
        pulse_color_cfg = cfg.get('pulse_color')
        if pulse_color_cfg is not None:
            # Explicit override from theme (already a QColor after
            # StyleManager conversion)
            pulse_base = QColor(pulse_color_cfg)
        else:
            # Default: derive from the header background
            pulse_base = QColor(self.header._bg_color)

        # --- Glow parameters (all concrete defaults) ---
        width_min:   float = cfg.get('pulse_glow_width_min',    2.0)
        width_max:   float = cfg.get('pulse_glow_width_max',   14.0)
        opacity_min:   int = cfg.get('pulse_glow_opacity_min',   15)
        opacity_max:   int = cfg.get('pulse_glow_opacity_max',   90)
        layers:        int = cfg.get('pulse_glow_layers',         5)
        offset:      float = cfg.get('pulse_glow_offset',        4.0)
        border_w:    float = cfg.get('pulse_border_width',       1.5)
        border_op:     int = cfg.get('pulse_border_opacity',     160)

        # Total glow band width driven by phase
        active_width = width_min + phase * (width_max - width_min)

        # --- Draw glow layers centered on the offset reference path ---
        #
        # All layers are drawn on a single path at pulse_glow_offset so the
        # pen spreads symmetrically inward and outward from that ring.
        # Pen width steps from active_width (widest, outermost feel) down to
        # active_width/layers (narrowest, sharpest) drawn last on top.
        # Opacity runs the opposite direction so the brightest part is the
        # tightest stroke nearest the reference path.
        ref_path = self._make_offset_path(offset)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(layers):
            t = i / (layers - 1) if layers > 1 else 1.0  # 0.0 widest → 1.0 narrowest
            pen_width  = active_width * (1.0 - t * (layers - 1) / layers)
            layer_alpha = int(opacity_min + t * (opacity_max - opacity_min))

            pulse_base.setAlpha(max(0, min(255, layer_alpha)))
            pen = QPen(pulse_base, pen_width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPath(ref_path)

        # --- Draw border ---
        border_alpha = int(border_op * (0.5 + 0.5 * phase))
        pulse_base.setAlpha(max(0, min(255, border_alpha)))

        pen = QPen(pulse_base, border_w)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(border_path)

    def _paint_orbital_pulse(
        self,
        painter: QPainter,
        border_path: QPainterPath,
    ) -> None:
        """Render a traveling pulse orb moving around the node outline.

        Uses ``border_path`` as the sole geometric reference.  The glow band
        is built from geometrically offset paths (via ``_make_offset_path``)
        spaced between ``pulse_glow_offset`` and
        ``pulse_glow_offset + active_width``, matching the behaviour of the
        standard waveforms.

        Rotation uses the linear ``phase`` value directly (constant angular
        velocity, guaranteed by ``_apply_pulse_timing`` forcing ``Linear``
        easing on the animation).

        Width breathing completes two full cycles per lap and is shaped by
        the theme's ``pulse_easing`` curve (e.g. ``InOutSine``) applied via
        ``QEasingCurve.valueForProgress``, giving the swell a feel consistent
        with the rest of the node's animation personality.

        Opacity is capped to ``pulse_glow_opacity_max`` so the orbital glow
        matches the visual weight of the other waveforms on the same node.
        """
        phase = self._computing_pulse_phase  # Linear 0.0 → 1.0 from _wave_orbital
        cfg = self._config

        # --- Resolve colour source ---
        pulse_color_cfg = cfg.get('pulse_color')
        pulse_base = (
            QColor(pulse_color_cfg) if pulse_color_cfg
            else QColor(self.header._bg_color)
        )

        # --- Cap opacity to match other waveforms ---
        opacity_max: int = cfg.get('pulse_glow_opacity_max', 90)
        pulse_base.setAlpha(max(0, min(255, opacity_max)))

        # --- Width breathing (2 cycles per lap, shaped by theme easing) ---
        # Raw sine oscillation in [0, 1], completing two full cycles per lap.
        breath_raw = (math.sin(phase * 4 * math.pi) + 1) / 2

        # Apply the theme's pulse_easing curve so the swell character matches
        # the rest of the node's animation personality.
        easing_name: str = cfg.get('pulse_easing', 'InOutSine')
        breath_easing = QEasingCurve(_resolve_easing(easing_name))
        breath_shaped = breath_easing.valueForProgress(breath_raw)

        width_min: float = cfg.get('pulse_glow_width_min',  4.0)
        width_max: float = cfg.get('pulse_glow_width_max', 12.0)
        offset:    float = cfg.get('pulse_glow_offset',     4.0)
        active_width = width_min + breath_shaped * (width_max - width_min)

        # --- Traveling conical gradient (rotation driven by linear phase) ---
        # Centered on the offset reference path so the gradient is consistent
        # across all layers (which are all drawn on the same path).
        ref_path = self._make_offset_path(offset)
        rect = ref_path.boundingRect()
        gradient = QConicalGradient(rect.center(), -phase * 360)

        transparent = QColor(pulse_base)
        transparent.setAlpha(0)

        # Head of the pulse at 10 %, tail fades back to transparent by 20 %.
        gradient.setColorAt(0.0,  transparent)
        gradient.setColorAt(0.1,  pulse_base)   # Peak intensity
        gradient.setColorAt(0.2,  transparent)
        gradient.setColorAt(1.0,  transparent)

        # --- Draw layered glow centered on the reference path ---
        # Same centered-pen approach as standard waveforms: all layers on the
        # same ref_path, pen width steps from active_width down to
        # active_width/layers so the glow spreads inward and outward.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        layers: int = cfg.get('pulse_glow_layers', 3)
        for i in range(layers):
            t = i / (layers - 1) if layers > 1 else 1.0  # 0.0 widest → 1.0 narrowest
            pen_width = active_width * (1.0 - t * (layers - 1) / layers)
            pen = QPen(QBrush(gradient), pen_width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPath(ref_path)

        # --- Draw border ---
        border_w: float = cfg.get('pulse_border_width', 1.5)
        border_pen = QPen(QBrush(gradient), border_w)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(border_path)

    # ------------------------------------------------------------------
    # StyleManager callback integration
    # ------------------------------------------------------------------

    def _on_pulse_style_changed(self, changes: Dict[str, Any]) -> None:
        """Handle pulse-related keys inside a NODE style-change notification.

        Call this from ``on_style_changed()`` when ``category == NODE``,
        after ``_config`` has already been updated::

            self._on_pulse_style_changed(changes)

        It refreshes timing and waveform if the theme altered them.
        """
        _PULSE_KEYS = {
            'pulse_waveform', 'pulse_duration', 'pulse_easing',
            'pulse_glow_width_min', 'pulse_glow_width_max',
            'pulse_glow_opacity_min', 'pulse_glow_opacity_max',
            'pulse_glow_layers', 'pulse_glow_offset',
            'pulse_border_width', 'pulse_border_opacity', 'pulse_color',
        }

        if changes.keys() & _PULSE_KEYS:
            self._apply_pulse_timing()

            if 'pulse_waveform' in changes:
                new_wf = changes['pulse_waveform']
                if new_wf in PULSE_WAVEFORMS:
                    self._active_waveform_key = new_wf
                else:
                    log.warning(
                        f"Theme supplied unknown pulse_waveform '{new_wf}'. "
                        f"Keeping current: '{self._active_waveform_key}'."
                    )


# ======================================================================
# Helpers
# ======================================================================

# Easing-curve name → QEasingCurve.Type mapping.  Supports both the
# short names used in themes ("InOutSine") and the fully-qualified
# Qt enum paths just in case.
_EASING_CACHE: Dict[str, QEasingCurve.Type] = {}


def _resolve_easing(name: str) -> QEasingCurve.Type:
    """Resolve a string easing name to a ``QEasingCurve.Type``.

    Falls back to ``InOutSine`` for unrecognised names.
    """
    if name in _EASING_CACHE:
        return _EASING_CACHE[name]

    # Try direct attribute lookup on the enum
    attr = getattr(QEasingCurve.Type, name, None)
    if attr is not None:
        _EASING_CACHE[name] = attr
        return attr

    log.warning(
        f"Unknown easing curve '{name}'. "
        f"Falling back to InOutSine."
    )
    fallback = QEasingCurve.Type.InOutSine
    _EASING_CACHE[name] = fallback
    return fallback