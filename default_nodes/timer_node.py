# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

timer_node.py
-------------
Countdown timer node backed by ``ThreadedManualNode``.

The countdown runs in a background thread (via ``ThreadedManualNode``),
keeping the UI responsive and the node in ``COMPUTING`` state for the
full duration.  Live UI updates are marshalled back to the main thread
through the ``progress_updated`` signal.

Outputs
-------
remaining : float  – seconds left (counts down to 0.0)
progress  : float  – completion fraction 0.0 → 1.0
finished  : bool   – True only on natural zero-crossing, False on cancel
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Dict, List, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QProgressBar,
    QPushButton,
)

from weave.threadednodes import ThreadedManualNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore
from weave.node.node_enums import VerticalSizePolicy

from weave.logger import get_logger

log = get_logger("CountdownTimer")


def _dbg(msg: str) -> None:
    print(f"[CountdownTimer] {msg}", flush=True)
    log.debug(msg)


# ══════════════════════════════════════════════════════════════════════════════
# CountdownTimerNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class CountdownTimerNode(ThreadedManualNode):
    """
    Countdown timer that runs in a background thread.

    Stays in ``COMPUTING`` state for the full countdown duration.
    Live ``remaining`` / ``progress`` values are pushed to output ports
    on every update cycle (~20 Hz) via a cross-thread signal so downstream
    nodes see continuously updated data without blocking the UI.

    Outputs
    -------
    remaining : float  – seconds left
    progress  : float  – 0.0 (just started) → 1.0 (finished)
    finished  : bool   – False while running; True on natural completion

    Controls
    --------
    Duration spinbox  – total countdown in seconds (0.1 – 3 600 s)
    Start / Stop button – starts the timer or cancels mid-run
    Progress bar      – visual fill while running
    Status label      – MM:SS countdown or idle message
    """

    # Signal for live UI updates emitted from the worker thread
    progress_updated = Signal(float, float)   # (remaining, progress)

    node_class:       ClassVar[str]           = "Utility"
    node_subclass:    ClassVar[str]           = "Timer"
    node_name:        ClassVar[Optional[str]] = "Countdown Timer"
    node_description: ClassVar[Optional[str]] = (
        "Counts down from a set duration; COMPUTING while running"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "timer", "countdown", "control", "time", "delay", "threaded",
    ]

    vertical_size_policy = VerticalSizePolicy.FIT

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(self, title: str = "Countdown Timer", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        _dbg("CountdownTimerNode.__init__: start")

        # ── Output ports ─────────────────────────────────────────────
        self.add_output("remaining", "float")
        self.add_output("progress",  "float")
        self.add_output("finished",  "bool")

        # ── Widget layout ─────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Duration spinner
        self._spin_duration = QDoubleSpinBox()
        self._spin_duration.setRange(0.1, 3600.0)
        self._spin_duration.setValue(10.0)
        self._spin_duration.setDecimals(1)
        self._spin_duration.setSuffix(" s")
        self._spin_duration.setMinimumWidth(100)
        form.addRow("Duration:", self._spin_duration)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep)

        # Status label
        self._label_status = QLabel("Idle")
        self._label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_status.setMinimumWidth(140)
        form.addRow("Status:", self._label_status)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)   # 0.1 % resolution
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(8)
        form.addRow(self._progress_bar)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep2)

        # Start / Stop button
        self._btn_start_stop = QPushButton("▶  Start")
        self._btn_start_stop.setMinimumWidth(100)
        self._btn_start_stop.clicked.connect(self._on_btn_clicked)
        form.addRow(self._btn_start_stop)

        # ── Finalise ──────────────────────────────────────────────────
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        # Wire cross-thread UI update signal
        self.progress_updated.connect(self._on_live_update)

        _dbg("CountdownTimerNode.__init__: done")

    # ── UI slots ──────────────────────────────────────────────────────────

    @Slot()
    def _on_btn_clicked(self) -> None:
        """Toggle start / cancel."""
        if self.is_computing:
            _dbg("_on_btn_clicked: cancelling")
            self.cancel_compute()
        else:
            _dbg("_on_btn_clicked: starting")
            self.execute()

    # ── ThreadedManualNode interface ──────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Capture the duration before the worker thread starts."""
        return {"duration": self._spin_duration.value()}

    def on_evaluate_start(self) -> None:
        """Called on the main thread just before the worker starts."""
        super().on_evaluate_start()
        _dbg("on_evaluate_start")
        self._btn_start_stop.setText("■  Stop")
        self._spin_duration.setEnabled(False)
        self._label_status.setText("Running…")
        self._progress_bar.setValue(0)

    def on_evaluate_finished(self) -> None:
        """Called on the main thread after the worker returns."""
        super().on_evaluate_finished()
        _dbg("on_evaluate_finished")
        self._btn_start_stop.setText("▶  Start")
        self._spin_duration.setEnabled(True)

        finished = self._cached_values.get("finished", False)
        if finished:
            self._label_status.setText("Finished ✓")
            self._progress_bar.setValue(1000)
        else:
            self._label_status.setText("Stopped")
            self._progress_bar.setValue(0)

    # ── Background compute (worker thread) ────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Countdown loop — runs entirely on the worker thread.

        Uses ``time.perf_counter`` for sub-millisecond accuracy.
        Checks ``is_compute_cancelled()`` every iteration so the node
        responds promptly to the Stop button.

        Live values are pushed to the main thread via ``progress_updated``
        rather than touching Qt widgets directly from the worker.
        """
        duration: float = inputs.get("duration", 10.0)
        _dbg(f"compute: starting, duration={duration:.1f}s")

        start_time = time.perf_counter()

        while True:
            if self.is_compute_cancelled():
                _dbg("compute: cancelled")
                return {"remaining": 0.0, "progress": 0.0, "finished": False}

            elapsed   = time.perf_counter() - start_time
            remaining = max(0.0, duration - elapsed)
            progress  = min(1.0, elapsed / duration)

            # Marshal UI update + output cache refresh to main thread
            self.progress_updated.emit(remaining, progress)

            if elapsed >= duration:
                _dbg("compute: finished naturally")
                break

            time.sleep(0.05)   # ~20 Hz

        return {"remaining": 0.0, "progress": 1.0, "finished": True}

    # ── Main-thread live update ────────────────────────────────────────────

    @Slot(float, float)
    def _on_live_update(self, remaining: float, progress: float) -> None:
        """
        Receive live tick data on the main thread and update UI + output
        cache so downstream nodes see continuously refreshed values.
        """
        # Update display
        mins = int(remaining) // 60
        secs = remaining % 60
        self._label_status.setText(f"{mins:02d}:{secs:05.2f} left")
        self._progress_bar.setValue(int(progress * 1000))

        # Refresh output cache so downstream nodes see live data
        self._cached_values["remaining"] = float(remaining)
        self._cached_values["progress"]  = float(progress)
        self._cached_values["finished"]  = False
        self.data_updated.emit()

    # ── Passthrough / bypass ──────────────────────────────────────────────

    def _apply_passthrough(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Emit None on all outputs when the node is in PASSTHROUGH state."""
        _dbg("_apply_passthrough: emitting None on all outputs")
        return {"remaining": None, "progress": None, "finished": None}

    # ── Serialisation ─────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["countdown_timer"] = {
            "duration": self._spin_duration.value(),
        }
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        _dbg("restore_state: start")
        super().restore_state(state)

        ct = state.get("countdown_timer", {})
        duration = ct.get("duration", 10.0)

        self._spin_duration.blockSignals(True)
        self._spin_duration.setValue(duration)
        self._spin_duration.blockSignals(False)

        self._label_status.setText("Idle")
        self._progress_bar.setValue(0)
        self._widget_core.refresh_widget_palettes()
        _dbg(f"restore_state: done, duration={duration:.1f}s")

    # ── Cleanup ───────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        _dbg("cleanup")
        self.cancel_compute()
        try:
            self._btn_start_stop.clicked.disconnect(self._on_btn_clicked)
        except RuntimeError:
            pass
        try:
            self.progress_updated.disconnect(self._on_live_update)
        except RuntimeError:
            pass
        self._widget_core.cleanup()
        super().cleanup()