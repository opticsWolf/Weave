# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

timer_node.py
-------------
Countdown timer node backed by ``ThreadedManualNode``.

The countdown runs in a background thread, keeping the UI responsive
and the node in COMPUTING state for the full duration. Live UI updates
are marshalled back to the main thread through the ``progress_updated`` signal.

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
from weave.widgetcore import WidgetCore, PortRole
from weave.node import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("CountdownTimer")


@register_node
class CountdownTimerNode(ThreadedManualNode):
    """
    Countdown timer that runs in a background thread.

    Stays in COMPUTING state for the full countdown duration.
    Live ``remaining`` / ``progress`` values are pushed to output ports
    on every update cycle (~20 Hz) via emit_intermediate, while UI widgets
    are updated cross-thread via the progress_updated signal.
    """

    # Signal for live UI updates emitted from the worker thread
    progress_updated = Signal(float, float)  # (remaining, progress)

    node_class: ClassVar[str] = "Demo"
    node_subclass: ClassVar[str] = "Utility"
    node_name: ClassVar[Optional[str]] = "Countdown Timer"
    node_description: ClassVar[Optional[str]] = (
        "Counts down from a set duration; COMPUTING while running"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "timer", "countdown", "control", "time", "delay", "threaded",
    ]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    # ── Construction (Canonical 6-Step Recipe) ────────────────────────────

    def __init__(self, title: str = "Countdown Timer", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # 1. Add ports
        self.add_output("remaining", datatype="float")
        self.add_output("progress", datatype="float")
        self.add_output("finished", datatype="bool")

        # 2. Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # 3. Create widgets & place in layout
        self._spin_duration = QDoubleSpinBox()
        self._spin_duration.setRange(0.1, 3600.0)
        self._spin_duration.setValue(10.0)
        self._spin_duration.setDecimals(1)
        self._spin_duration.setSuffix(" s")
        form.addRow("Duration:", self._spin_duration)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep)

        self._label_status = QLabel("Idle")
        self._label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow("Status:", self._label_status)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(8)
        form.addRow(self._progress_bar)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(sep2)

        self._btn_start_stop = QPushButton("▶  Start")
        self._btn_start_stop.setMinimumWidth(100)
        self._btn_start_stop.clicked.connect(self._on_btn_clicked)
        form.addRow(self._btn_start_stop)

        # Register widget with WidgetCore (enables auto-serialization, undo, & inputs injection)
        self._widget_core.register_widget(
            "duration", self._spin_duration,
            role=PortRole.BIDIRECTIONAL, datatype="float", default=10.0,
            add_to_layout=False,
        )

        # 4. Wire signals (required for framework state sync & undo capture)
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Wire framework lifecycle signals for UI state management
        self.compute_started.connect(self._on_compute_started)
        self.compute_finished.connect(self._on_compute_finished)

        # 5 + 6. Mount content widget & patch render proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    # ── Signal Handlers ───────────────────────────────────────────────────

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        """User edited a widget. Mark dirty for framework tracking."""
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        """Programmatic write (e.g., undo replay). No structural sync needed here."""
        pass

    @Slot()
    def _on_btn_clicked(self) -> None:
        """Toggle start / cancel. Uses framework's internal flag."""
        if self._is_computing:  # Fixed: matches threadednodes.py internal state
            log.debug("cancelling compute")
            self.cancel_compute()
        else:
            log.debug("starting compute")
            self.execute()

    @Slot()
    def _on_compute_started(self) -> None:
        """Framework signal: worker thread has started."""
        self._btn_start_stop.setText("■  Stop")
        self._spin_duration.setEnabled(False)
        self._label_status.setText("Running…")
        self._progress_bar.setValue(0)

    @Slot()
    def _on_compute_finished(self) -> None:
        """Framework signal: worker thread has finished/cancelled."""
        self._btn_start_stop.setText("▶  Start")
        self._spin_duration.setEnabled(True)

        # Check cached result to determine natural finish vs cancellation
        finished = self._get_cached_value("finished")
        if finished is True:
            self._label_status.setText("Finished ✓")
            self._progress_bar.setValue(1000)
        else:
            self._label_status.setText("Stopped")
            self._progress_bar.setValue(0)

    # ── Background Compute (Worker Thread) ────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Countdown loop — runs entirely on the worker thread.
        Reads duration from inputs (auto-injected by WidgetCore registration).
        """
        duration = float(inputs.get("duration", 10.0))
        start_time = time.perf_counter()

        while True:
            if self.is_compute_cancelled():
                return {"remaining": 0.0, "progress": 0.0, "finished": False}

            elapsed = time.perf_counter() - start_time
            remaining = max(0.0, duration - elapsed)
            progress = min(1.0, elapsed / duration)

            # Push live data to downstream nodes (updates output cache on main thread)
            self.emit_intermediate({
                "remaining": float(remaining),
                "progress": float(progress),
                "finished": False,
            })

            # Marshal UI widget updates to main thread
            self.progress_updated.emit(remaining, progress)

            if elapsed >= duration:
                break

            time.sleep(0.05)  # ~20 Hz tick rate

        return {"remaining": 0.0, "progress": 1.0, "finished": True}

    # ── Main-Thread Live Update Slot ──────────────────────────────────────

    @Slot(float, float)
    def _on_live_update(self, remaining: float, progress: float) -> None:
        """Receive live tick data on the main thread and update UI widgets."""
        mins = int(remaining) // 60
        secs = remaining % 60
        self._label_status.setText(f"{mins:02d}:{secs:05.2f} left")
        self._progress_bar.setValue(int(progress * 1000))

    # ── Passthrough / Bypass ──────────────────────────────────────────────

    def _apply_passthrough(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Emit None on all outputs when the node is in PASSTHROUGH state."""
        return {"remaining": None, "progress": None, "finished": None}

    # ── Cleanup ───────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Safely terminate background work and release framework resources."""
        self.cancel_compute()
        super().cleanup()
