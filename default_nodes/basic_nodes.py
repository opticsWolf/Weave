# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

basic_nodes.py
------------------
Fundamental source and display nodes for the five base types used
throughout the Weave node graph.

Provided nodes
--------------
``IntInputNode``
    Source node with a ``QSpinBox``.  Outputs the current integer value
    immediately on every change.

``FloatInputNode``
    Source node with a ``QDoubleSpinBox``.  Outputs the current float
    value immediately on every change.

``StringInputNode``
    Source node with a single-line ``QLineEdit``.  Outputs the current
    text immediately on every keystroke.

``TextBoxInputNode``
    Source / utility node with a multi-line ``QTextEdit``.  Accepts an
    optional upstream ``text_in`` string to replace the editor contents
    while still exposing the result downstream.

Design notes
------------
* All nodes follow the ``ActiveNode`` + ``WidgetCore`` convention
  established in ``simple_nodes.py`` and ``text_nodes.py``.
* ``_widget_core`` is always the attribute name so
  ``BaseControlNode.get_state()`` / ``restore_state()`` can reach it
  for serialisation automatically.
* UI updates for sink nodes are deferred to ``on_evaluate_finished()``
  to avoid triggering redraws during the evaluation cycle itself.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Dict, List, Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QTextEdit,
)

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore

from weave.logger import get_logger

log = get_logger("PrimitiveNodes")


# ══════════════════════════════════════════════════════════════════════════════
# IntInputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class IntInputNode(ActiveNode):
    """
    Source node that emits an integer via an editable ``QSpinBox``.

    Type: Active (propagates downstream on every value change).
    """

    value_changed = Signal(int)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[str] = "Integer Value"
    node_description: ClassVar[str] = "Integer value source"
    node_tags: ClassVar[List[str]] = ["int", "integer", "number", "input", "primitive"]
    node_icon: ClassVar[str] = "node"

    def __init__(
        self,
        title: str = "Integer Value",
        initial_value: int = 0,
        minimum: int = -2_147_483_648,
        maximum: int = 2_147_483_647,
        step: int = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        # ── WidgetCore + QSpinBox ─────────────────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        spin = QSpinBox()
        # Enforce C++ boundary type safety
        spin.setRange(int(minimum), int(maximum))
        spin.setSingleStep(int(step))
        spin.setValue(int(initial_value))
        spin.setMinimumWidth(100)

        self._widget_core.register_widget(
            port_name="value",
            widget=spin,
            role="OUTPUT",
            datatype="int",
            default=0,
        )

        self.add_output("value", datatype="int")

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

        self._cached_values["value"] = int(initial_value)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            if port_name == "value":
                self.on_ui_change()
                self.value_changed.emit(
                    int(self._widget_core.get_port_value("value"))
                )
        except Exception as exc:
            log.error(f"Exception in IntInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current spinbox integer."""
        try:
            return {"value": int(self._widget_core.get_port_value("value"))}
        except Exception as exc:
            log.error(f"Exception in IntInputNode.compute: {exc}")
            return {"value": 0}

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# FloatInputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class FloatInputNode(ActiveNode):
    """
    Source node that emits a float via an editable ``QDoubleSpinBox``.

    Type: Active (propagates downstream on every value change).
    """

    value_changed = Signal(float)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[str] = "Float Value"
    node_description: ClassVar[str] = "Floating-point value source"
    node_tags: ClassVar[List[str]] = ["float", "number", "input", "primitive"]
    node_icon: ClassVar[str] = "node"

    def __init__(
        self,
        title: str = "Float Value",
        initial_value: float = 0.0,
        minimum: float = -1e9,
        maximum: float = 1e9,
        step: float = 0.1,
        decimals: int = 4,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        # ── WidgetCore + QDoubleSpinBox ───────────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        spin = QDoubleSpinBox()
        # Enforce C++ boundary type safety
        spin.setRange(float(minimum), float(maximum))
        spin.setSingleStep(float(step))
        spin.setDecimals(int(decimals))
        spin.setValue(float(initial_value))
        spin.setMinimumWidth(110)

        self._widget_core.register_widget(
            port_name="value",
            widget=spin,
            role="OUTPUT",
            datatype="float",
            default=0.0,
        )

        self.add_output("value", datatype="float")

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

        self._cached_values["value"] = float(initial_value)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            if port_name == "value":
                self.on_ui_change()
                self.value_changed.emit(
                    float(self._widget_core.get_port_value("value"))
                )
        except Exception as exc:
            log.error(f"Exception in FloatInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current spinbox float."""
        try:
            return {"value": float(self._widget_core.get_port_value("value"))}
        except Exception as exc:
            log.error(f"Exception in FloatInputNode.compute: {exc}")
            return {"value": 0.0}

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# StringInputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class StringInputNode(ActiveNode):
    """
    Source node that emits a string via an editable single-line
    ``QLineEdit``.  Responds immediately on every keystroke.

    Type: Active (propagates downstream on every keystroke).
    """

    text_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[str] = "String Value"
    node_description: ClassVar[str] = "Single-line string source"
    node_tags: ClassVar[List[str]] = ["string", "text", "input", "primitive"]
    node_icon: ClassVar[str] = "node"

    def __init__(
        self,
        title: str = "String Value",
        initial_text: str = "",
        placeholder: str = "Enter text…",
        max_length: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        # ── WidgetCore + QLineEdit ────────────────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        line_edit = QLineEdit()
        line_edit.setText(str(initial_text))
        line_edit.setPlaceholderText(str(placeholder))
        line_edit.setMinimumWidth(130)
        
        if int(max_length) > 0:
            line_edit.setMaxLength(int(max_length))

        self._widget_core.register_widget(
            port_name="text",
            widget=line_edit,
            role="OUTPUT",
            datatype="string",
            default="",
        )

        self.add_output("text", datatype="string")

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

        self._cached_values["text"] = str(initial_text)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(
                    str(self._widget_core.get_port_value("text"))
                )
        except Exception as exc:
            log.error(f"Exception in StringInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current line-edit text."""
        try:
            return {"text": str(self._widget_core.get_port_value("text"))}
        except Exception as exc:
            log.error(f"Exception in StringInputNode.compute: {exc}")
            return {"text": ""}

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextBoxInputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TextBoxInputNode(ActiveNode):
    """
    Source node with an editable multi-line ``QTextEdit``.
    Propagates downstream on every text change.

    Type: Active (propagates downstream on every text change).
    """

    text_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[str] = "Text Box"
    node_description: ClassVar[str] = "Multi-line text editor source"
    node_tags: ClassVar[List[str]] = ["text", "multiline", "editor", "input", "primitive"]
    node_icon: ClassVar[str] = "node"

    def __init__(
        self,
        title: str = "Text Box",
        initial_text: str = "",
        min_width: int = 180,
        min_height: int = 90,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        # ── WidgetCore + QTextEdit ────────────────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        self._editor = QTextEdit()
        self._editor.setPlainText(str(initial_text))
        self._editor.setMinimumSize(int(min_width), int(min_height))
        self._editor.setAcceptRichText(False)

        self._widget_core.register_widget(
            port_name="text",
            widget=self._editor,
            role="OUTPUT",
            datatype="string",
            default="",
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
        )

        self.add_output("text", datatype="string")

        # textChanged has no arguments — use a lambda shim
        self._editor.textChanged.connect(lambda: self._on_core_changed("text"))
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

        self._cached_values["text"] = str(initial_text)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(
                    str(self._widget_core.get_port_value("text"))
                )
        except Exception as exc:
            log.error(f"Exception in TextBoxInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current editor plain-text."""
        try:
            return {"text": str(self._widget_core.get_port_value("text"))}
        except Exception as exc:
            log.error(f"Exception in TextBoxInputNode.compute: {exc}")
            return {"text": ""}

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        super().cleanup()

# ══════════════════════════════════════════════════════════════════════════════
# RangeListNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class RangeListNode(ActiveNode):
    """
    List generator node using Python's built-in ``range``.

    Generates integer sequences with configurable start, stop, and step
    values via embedded spinboxes.  There are no input ports — all parameters
    are controlled exclusively through the node body widgets.

    Type: Active (Updates downstream on parameter change).
    """
    list_changed = Signal(list)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[str] = "Range List"
    node_description: ClassVar[str] = "Creates numerical lists"
    node_tags: ClassVar[List[str]] = ["list", "range", "generator"]
    node_icon: ClassVar[str] = "node"

    def __init__(self, title: str = "Range List", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        self.add_output("list", datatype="list")

        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        spin_start = QSpinBox()
        spin_start.setRange(int(-9999), int(9999))
        spin_start.setValue(0)

        spin_stop = QSpinBox()
        spin_stop.setRange(int(-9999), int(9999))
        spin_stop.setValue(10)

        spin_step = QSpinBox()
        spin_step.setRange(int(1), int(9999))
        spin_step.setValue(1)

        form.addRow("Start:", spin_start)
        form.addRow("Stop:",  spin_stop)
        form.addRow("Step:",  spin_step)

        # Registered as INTERNAL so they do not auto-generate unintended output ports
        self._widget_core.register_widget(
            port_name="start",
            widget=spin_start,
            role="INTERNAL",
            datatype="int",
            default=0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            port_name="stop",
            widget=spin_stop,
            role="INTERNAL",
            datatype="int",
            default=10,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            port_name="step",
            widget=spin_step,
            role="INTERNAL",
            datatype="int",
            default=1,
            add_to_layout=False,
        )

        self._widget_core.value_changed.connect(self._on_core_changed)

        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in RangeListNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a list using Python's range() from the widget values."""
        try:
            start = int(self._widget_core.get_port_value("start") or 0)
            stop  = int(self._widget_core.get_port_value("stop")  or 10)
            step  = int(self._widget_core.get_port_value("step")  or 1)

            if step == 0:
                step = 1

            result_list = list(range(start, stop, step))
            self.list_changed.emit(result_list)
            return {"list": result_list}

        except Exception as e:
            log.error(f"Exception in RangeListNode.compute: {e}")
            return {"list": []}

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextListNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TextListNode(ActiveNode):
    """
    Source node that emits a ``list[str]`` built from a multi-line
    ``QTextEdit``.  Each non-empty line becomes one element.
    """

    list_changed = Signal(list)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[str] = "Text List"
    node_description: ClassVar[str] = "Builds a list from newline-separated editor text"
    node_tags: ClassVar[List[str]] = ["list", "generator", "text", "input", "primitive"]
    node_icon: ClassVar[str] = "node"

    def __init__(
        self,
        title: str = "Text List",
        initial_text: str = "",
        min_width: int = 160,
        min_height: int = 90,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        self.add_output("list", datatype="list")

        # ── WidgetCore + QTextEdit ────────────────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        self._editor = QTextEdit()
        self._editor.setPlainText(str(initial_text))
        self._editor.setMinimumSize(int(min_width), int(min_height))
        self._editor.setAcceptRichText(False)
        self._editor.setPlaceholderText("One item per line…")

        self._widget_core.register_widget(
            port_name="text",
            widget=self._editor,
            role="DISPLAY",
            datatype="string",
            default="",
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
        )

        self._editor.textChanged.connect(lambda: self._on_editor_changed())
        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

        self._cached_values["list"] = self._parse_lines(str(initial_text))

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_lines(raw: str) -> List[str]:
        """Split *raw* on newlines, strip each line, drop empties."""
        return [line.strip() for line in raw.splitlines() if line.strip()]

    # ── Signal handling ──────────────────────────────────────────────

    def _on_editor_changed(self) -> None:
        try:
            self.on_ui_change()
            self.list_changed.emit(
                self._parse_lines(self._editor.toPlainText())
            )
        except Exception as exc:
            log.error(f"Exception in TextListNode._on_editor_changed: {exc}")

    # ── Computation ──────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Parses the editor text and returns the item list."""
        try:
            result = self._parse_lines(self._editor.toPlainText())
            return {"list": result}
        except Exception as exc:
            log.error(f"Exception in TextListNode.compute: {exc}")
            return {"list": []}

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        super().cleanup()