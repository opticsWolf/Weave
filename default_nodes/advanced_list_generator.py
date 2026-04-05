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
automatically disabled (greyed out). Disconnecting the trace re-enables it.
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
    """Generates numerical lists with framework-managed auto-disabling widgets."""
    
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

        # ── 1. Ports (Graph Layer) ───────────────────────────────────
        self.add_input("start", datatype="float")
        self.add_input("stop", datatype="float")
        self.add_input("step", datatype="float")
        self.add_output("list", datatype="list")

        # ── 2. UI Layout (WidgetCore) ────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Initialize Qt Widgets (strict PySide6 float casting)
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

        # Add to layout
        form.addRow("Start:", spin_start)
        form.addRow("Stop:",  spin_stop)
        form.addRow("Step:",  spin_step)

        # ── 3. Binding (The Bridge) ──────────────────────────────────
        # Registering as BIDIRECTIONAL tells the framework to auto-disable 
        # these widgets when traces connect to the matching port names.
        self._widget_core.register_widget("start", spin_start, role="BIDIRECTIONAL", datatype="float", default=0.0, add_to_layout=False)
        self._widget_core.register_widget("stop",  spin_stop,  role="BIDIRECTIONAL", datatype="float", default=10.0, add_to_layout=False)
        self._widget_core.register_widget("step",  spin_step,  role="BIDIRECTIONAL", datatype="float", default=1.0, add_to_layout=False)

        # ── 4. The Critical Fix: UI Evaluation Link ──────────────────
        # This ties the Qt widget interactions (like mouse wheel scrolls) 
        # directly to the node's evaluation fence, eliminating lag and 
        # properly grouping Undo commands.
        self._widget_core.value_changed.connect(self.on_ui_change)

        self.set_content_widget(self._widget_core)

    # ── Computation (Worker Thread) ──────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates the list.
        The framework automatically resolves whether 'inputs' come from 
        an upstream trace or the local widget fallback.
        """
        try:
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

    # ── Main Thread Hooks ────────────────────────────────────────────

    def on_evaluate_finished(self) -> None:
        """Emit list_changed on the main thread after results are cached."""
        if self._pending_list is not None:
            self.list_changed.emit(self._pending_list)
            self._pending_list = None
            
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        self._pending_list = None
        
        # Cancel any running computation first 
        if hasattr(self, 'cancel_compute'):
            self.cancel_compute()

        # ALWAYS call super().cleanup() last — it safely severs 
        # WidgetCore bindings and active event loops.
        super().cleanup()