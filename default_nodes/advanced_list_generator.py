# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

advanced_list_generator.py
---------------------------
Advanced Range List Generator node with per-parameter auto-disable.

Each parameter (start, stop, step) has both an input port AND a QSpinBox.
When an upstream node connects to a parameter's input port, the
corresponding QSpinBox is automatically disabled (greyed out) so the
user cannot conflict with the incoming data.  Disconnecting the trace
re-enables the widget.

This is achieved by setting ``_auto_disable = True`` on each input port
immediately after creation.
"""

import numpy as np
from typing import Any, Dict, Optional, ClassVar, List
from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout

from weave.widgetcore import WidgetCore
from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node

from weave.logger import get_logger
log = get_logger("AdvancedListGen")


@register_node
class AdvancedRangeListNode(ThreadedNode):
    """
    Advanced list generator with auto-disabling spinboxes.

    Each of the three parameters (start, stop, step) is exposed as:
      * A QSpinBox in the node body for manual editing.
      * An input port that accepts an upstream connection.

    When a trace is connected to an input port the matching spinbox is
    automatically disabled; when the trace is removed the spinbox becomes
    editable again.  This is driven by the ``_auto_disable`` flag on the
    port object — see ``weave.widgetcore`` for the mechanism.

    Inherits from ThreadedNode so that numpy work runs off the main thread.
    Widget values are snapshotted on the main thread before dispatch via
    ``snapshot_widget_inputs()``, using ``_ui_`` prefixed keys so they
    never overwrite upstream port values in the inputs dict.

    Output
    ------
    list : list[float]
        The generated sequence ``numpy.arange(start, stop, step).tolist()``.

    Type: Threaded (compute() runs on QThreadPool; main thread stays responsive).
    """
    list_changed = Signal(list)

    # ── Registry metadata ────────────────────────────────────────────
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[Optional[str]] = "Advanced Range List"
    node_description: ClassVar[Optional[str]] = (
        "Generates numerical lists with per-parameter auto-disable"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "list", "range", "generator", "advanced", "auto-disable",
    ]

    def __init__(self, title: str = "Adv Range List", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # ── Input ports (with auto-disable) ──────────────────────────
        self.add_input("start", "float")
        self.inputs[-1]._auto_disable = True

        self.add_input("stop", "float")
        self.inputs[-1]._auto_disable = True

        self.add_input("step", "float")
        self.inputs[-1]._auto_disable = True

        # ── Output port ──────────────────────────────────────────────
        self.add_output("list", "list")

        # ── Widget body (compact form layout) ────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        spin_start = QDoubleSpinBox()
        spin_start.setRange(-99999999, 99999999)
        spin_start.setDecimals(3)
        spin_start.setSingleStep(1.0)
        spin_start.setValue(0)

        spin_stop = QDoubleSpinBox()
        spin_stop.setRange(-99999999, 99999999)
        spin_stop.setDecimals(3)
        spin_stop.setSingleStep(1.0)
        spin_stop.setValue(10)

        spin_step = QDoubleSpinBox()
        spin_step.setRange(0.001, 99999999)
        spin_step.setDecimals(3)
        spin_step.setSingleStep(1.0)
        spin_step.setValue(1)

        # Place labelled rows in the form before registering with core
        form.addRow("Start:", spin_start)
        form.addRow("Stop:",  spin_stop)
        form.addRow("Step:",  spin_step)

        # Register each spinbox as bidirectional (UI ↔ upstream).
        # add_to_layout=False because we already placed them above.
        self._widget_core.register_widget(
            "start", spin_start,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            "stop", spin_stop,
            role="bidirectional", datatype="float", default=10.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            "step", spin_step,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )

        # Single connection covers all three spinboxes
        self._widget_core.value_changed.connect(self._on_core_changed)

        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

    # ── Widget snapshot (main thread → worker thread) ────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """
        Capture spinbox values on the main thread before worker dispatch.

        Uses ``_ui_`` prefixed keys so these fallback values never
        overwrite a live upstream port value in the inputs dict.
        """
        return {
            f"_ui_{k}": v
            for k, v in self._widget_core.get_all_values().items()
        }

    # ── Signal handling ──────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates any spinbox change into the graph."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in AdvancedRangeListNode._on_core_changed: {e}")

    def on_evaluate_finished(self) -> None:
        """Emit list_changed on the main thread after results are cached."""
        result = self.get_output_value("list")
        if result is not None:
            self.list_changed.emit(result)
        super().on_evaluate_finished()

    # ── Computation (worker thread — no Qt widget access) ────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a list via numpy.arange.

        Upstream port values take priority over widget fallbacks.
        Widget values arrive pre-snapshotted under ``_ui_`` keys so
        this method never needs to touch Qt widgets directly.
        """
        try:
            def _get(port: str, default: float) -> float:
                # Upstream connection takes priority; fall back to spinbox snapshot.
                v = inputs.get(port)
                if v is None:
                    v = inputs.get(f"_ui_{port}", default)
                return float(v) if v is not None else default

            start = _get("start", 0.0)
            stop  = _get("stop",  10.0)
            step  = _get("step",  1.0)

            if step == 0:
                step = 1.0

            result_list = np.arange(start, stop, step).tolist()
            return {"list": result_list}

        except Exception as e:
            log.error(f"Exception in AdvancedRangeListNode.compute: {e}")
            return {"list": []}

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()
