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
Using the WidgetCore system's BIDIRECTIONAL role, when an upstream node
connects to a parameter's input port, the corresponding widget is
automatically disabled (greyed out) so the user cannot conflict with the
incoming data. Disconnecting the trace re-enables the widget.
"""

import numpy as np
from typing import Any, Dict, Optional, ClassVar, List
from PySide6.QtCore import Signal
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

    Inherits from ThreadedNode so that numpy work runs off the main thread.
    Uses BIDIRECTIONAL widget registration to automatically manage port
    creation, widget fallback values, and auto-disabling UI states when
    upstream traces are connected.
    """
    list_changed = Signal(list)

    # ── Registry metadata ────────────────────────────────────────────
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[str] = "Advanced Range List"
    node_description: ClassVar[str] = "Generates numerical lists with per-parameter auto-disable"
    node_tags: ClassVar[List[str]] = ["list", "range", "generator", "advanced", "auto-disable"]

    def __init__(self, title: str = "Adv Range List", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Thread-safe handoff for the custom Qt Signal
        self._pending_list: Optional[list] = None

        # ── Output port ──────────────────────────────────────────────
        self.add_output("list", datatype="list")

        # ── Widget body (compact form layout) ────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        
        # Weave WidgetCore initialized with our layout
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Initialize Qt Widgets (ensure strict typing at PySide boundary)
        spin_start = QDoubleSpinBox()
        spin_start.setRange(float(-99999999), float(99999999))
        spin_start.setDecimals(3)
        spin_start.setSingleStep(1.0)
        spin_start.setValue(0.0)

        spin_stop = QDoubleSpinBox()
        spin_stop.setRange(float(-99999999), float(99999999))
        spin_stop.setDecimals(3)
        spin_stop.setSingleStep(1.0)
        spin_stop.setValue(10.0)

        spin_step = QDoubleSpinBox()
        spin_step.setRange(0.001, float(99999999))
        spin_step.setDecimals(3)
        spin_step.setSingleStep(1.0)
        spin_step.setValue(1.0)

        # Place labelled rows in the form before registering with core
        form.addRow("Start:", spin_start)
        form.addRow("Stop:",  spin_stop)
        form.addRow("Step:",  spin_step)

        # Register each spinbox as BIDIRECTIONAL (UI ↔ upstream).
        # This auto-creates the input ports and handles auto-disable on connect.
        self._widget_core.register_widget(
            port_name="start",
            widget=spin_start,
            role="BIDIRECTIONAL",
            datatype="float",
            default=0.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            port_name="stop",
            widget=spin_stop,
            role="BIDIRECTIONAL",
            datatype="float",
            default=10.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            port_name="step",
            widget=spin_step,
            role="BIDIRECTIONAL",
            datatype="float",
            default=1.0,
            add_to_layout=False,
        )

        self.set_content_widget(self._widget_core)
        
        # Patch and refresh palettes for styling integration
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

    # ── Computation (worker thread — no Qt widget access) ────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a list via numpy.arange.

        Upstream port values take priority over widget fallbacks.
        The framework automatically injects the BIDIRECTIONAL widget 
        values into the `inputs` dict if no trace is connected.
        """
        try:
            # Safely extract from framework-resolved inputs and cast 
            start = float(inputs.get("start", 0.0))
            stop  = float(inputs.get("stop", 10.0))
            step  = float(inputs.get("step", 1.0))

            if step == 0.0:
                step = 1.0

            # Cooperative cancellation check for long-running thread safety
            if self.is_compute_cancelled():
                return {"list": []}

            result_list = np.arange(start, stop, step).tolist()
            
            # Store safely for the main thread to emit
            self._pending_list = result_list

            return {"list": result_list}

        except Exception as e:
            log.error(f"Exception in AdvancedRangeListNode.compute: {e}")
            return {"list": []}

    # ── Signal handling (main thread — safe for Qt GUI) ──────────────

    def on_evaluate_finished(self) -> None:
        """Emit list_changed on the main thread after results are cached."""
        if self._pending_list is not None:
            self.list_changed.emit(self._pending_list)
            self._pending_list = None
            
        super().on_evaluate_finished()

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        self._pending_list = None
        
        # Cancel any running computation first 
        if hasattr(self, 'cancel_compute'):
            self.cancel_compute()

        # ALWAYS call super().cleanup() last — it safely severs 
        # WidgetCore bindings and active event loops.
        super().cleanup()