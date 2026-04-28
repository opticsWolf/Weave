# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

advanced_list_generator.py
---------------------------
Advanced Range List Generator node with per-parameter auto-disable.
"""

import numpy as np
from typing import Any, ClassVar, Dict, Optional, List

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore
from weave.widgetcore.widgetcore_port_models import PortRole
from weave.node.node_enums import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("AdvancedListGen")


@register_node
class AdvancedRangeListNode(ThreadedNode):
    """Generates numerical lists with framework-managed auto-disabling widgets."""
    
    list_changed = Signal(list)

    # ── Registry metadata ────────────────────────────────────────────
    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Generator"
    node_name:         ClassVar[Optional[str]]       = "Advanced Range List"
    node_description:  ClassVar[Optional[str]]       = "Generates numerical lists with per-parameter auto-disable"
    node_tags:         ClassVar[List[str]]           = ["list", "range", "generator", "advanced", "auto-disable"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Adv Range List", **kwargs: Any) -> None:
        # 1. Base init
        super().__init__(title=title, **kwargs)

        # 2. Graph ports
        self.add_input("start", datatype="float")
        self.add_input("stop", datatype="float")
        self.add_input("step", datatype="float")
        self.add_output("list", datatype="list")

        # 3. WidgetCore setup
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # 4. Create widgets & register bindings
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

        form.addRow("Start:", spin_start)
        form.addRow("Stop:",  spin_stop)
        form.addRow("Step:",  spin_step)

        self._widget_core.register_widget(
            "start", spin_start, role=PortRole.BIDIRECTIONAL, datatype="float", default=0.0, add_to_layout=False
        )
        self._widget_core.register_widget(
            "stop", spin_stop, role=PortRole.BIDIRECTIONAL, datatype="float", default=10.0, add_to_layout=False
        )
        self._widget_core.register_widget(
            "step", spin_step, role=PortRole.BIDIRECTIONAL, datatype="float", default=1.0, add_to_layout=False
        )

        # 5. Signal wiring (both required for undo/structural safety)
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # 6. Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port_name: str) -> None:
        """User edits — mark dirty for re-evaluation."""
        self.on_ui_change()

    @Slot(str, object)
    def _on_port_value_written(self, port_name: str, value: Any) -> None:
        """Programmatic writes (undo/restore) — structural sync only. Do NOT call on_ui_change."""
        pass

    # ── Computation (Worker Thread) ──────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            start = float(inputs.get("start", 0.0))
            stop  = float(inputs.get("stop", 10.0))
            step  = float(inputs.get("step", 1.0))

            if step == 0.0:
                step = 1.0

            # Cooperative cancellation check
            if self.is_compute_cancelled():
                return {"list": []}

            result_list = np.arange(start, stop, step).tolist()
            return {"list": result_list}

        except Exception as e:
            log.error(f"Exception in AdvancedRangeListNode.compute: {e}")
            return {"list": []}

    # ── Main Thread Hooks ────────────────────────────────────────────

    @Slot()
    def on_evaluate_finished(self) -> None:
        """Emit list_changed on the main thread after results are cached."""
        result = self._get_cached_value("list")
        if result is not None:
            self.list_changed.emit(result)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        self.cancel_compute()
        # MUST be last to prevent ghost-signal segfaults
        super().cleanup()
