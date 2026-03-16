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
from PySide6.QtWidgets import QSpinBox, QFormLayout

from weave.widgetcore import WidgetCore
from weave.basenode import ActiveNode
from weave.noderegistry import register_node

from weave.logger import get_logger
log = get_logger("AdvancedListGen")


@register_node
class AdvancedRangeListNode(ActiveNode):
    """
    Advanced list generator with auto-disabling spinboxes.

    Each of the three parameters (start, stop, step) is exposed as:
      * A QSpinBox in the node body for manual editing.
      * An input port that accepts an upstream connection.

    When a trace is connected to an input port the matching spinbox is
    automatically disabled; when the trace is removed the spinbox becomes
    editable again.  This is driven by the ``_auto_disable`` flag on the
    port object — see ``weave.widgetcore`` for the mechanism.

    Output
    ------
    list : list[float]
        The generated sequence ``numpy.arange(start, stop, step).tolist()``.

    Type: Active (updates downstream immediately on any change).
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

        spin_start = QSpinBox()
        spin_start.setRange(-9999, 9999)
        spin_start.setValue(0)

        spin_stop = QSpinBox()
        spin_stop.setRange(-9999, 9999)
        spin_stop.setValue(10)

        spin_step = QSpinBox()
        spin_step.setRange(1, 9999)
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

    # ── Signal handling ──────────────────────────────────────────────

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates any spinbox change into the graph."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in AdvancedRangeListNode._on_core_changed: {e}")

    # ── Computation ──────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a list via numpy.arange.

        For each parameter the connected upstream value takes priority;
        if no connection is present the local spinbox value is used.
        """
        try:
            start = inputs.get("start")
            stop  = inputs.get("stop")
            step  = inputs.get("step")

            if start is None:
                start = self._widget_core.get_port_value("start")
            if stop is None:
                stop = self._widget_core.get_port_value("stop")
            if step is None:
                step = self._widget_core.get_port_value("step")

            start = float(start) if start is not None else 0.0
            stop  = float(stop)  if stop  is not None else 10.0
            step  = float(step)  if step  is not None else 1.0

            if step == 0:
                step = 1.0

            result_list = np.arange(start, stop, step).tolist()
            self.list_changed.emit(result_list)
            return {"list": result_list}

        except Exception as e:
            log.error(f"Exception in AdvancedRangeListNode.compute: {e}")
            return {"list": []}

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()
