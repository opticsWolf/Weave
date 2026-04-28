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

Provided Nodes
--------------
``IntInputNode``
    Source node with a ``QSpinBox``. Outputs the current integer value.
``FloatInputNode``
    Source node with a ``QDoubleSpinBox``. Outputs the current float value.
``StringInputNode``
    Source node with a single-line ``QLineEdit``. Outputs the current text.
``TextBoxInputNode``
    Source / utility node with a multi-line ``QTextEdit``.
``RangeListNode``
    List generator node using Python's built-in ``range``.
``TextListNode``
    Source node that emits a ``list[str]`` built from newline-separated editor text.

Design Notes
------------
* All nodes follow the ``ActiveNode`` + ``WidgetCore`` convention.
* Construction strictly follows the canonical 6-step recipe (§4).
* Widget roles use ``PortRole`` enums; datatypes are lowercase strings.
* ``compute()`` reads exclusively from ``inputs`` per §9.2.
* Custom signals are emitted in ``on_evaluate_finished()`` to ensure graph consistency (§12.1).
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
from weave.widgetcore import WidgetCore, PortRole
from weave.node import VerticalSizePolicy
from weave.logger import get_logger

log = get_logger("PrimitiveNodes")


# ══════════════════════════════════════════════════════════════════════════════
# IntInputNode
# ══════════════════════════════════════════════════════════════════════════════
@register_node
class IntInputNode(ActiveNode):
    """Source node that emits an integer via an editable ``QSpinBox``."""

    value_changed = Signal(int)

    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Input"
    node_name:         ClassVar[Optional[str]]       = "Integer Value"
    node_description:  ClassVar[Optional[str]]       = "Integer value source"
    node_tags:         ClassVar[Optional[List[str]]] = ["int", "integer", "number", "input", "primitive"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Integer Value", initial_value: int = 0, minimum: int = -2_147_483_648, maximum: int = 2_147_483_647, step: int = 1, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Step 1: Add ports
        self.add_output("value", datatype="int")

        # Step 2: Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Step 3: Create widgets, place in layout, register with WidgetCore
        spin = QSpinBox()
        spin.setRange(int(minimum), int(maximum))
        spin.setSingleStep(int(step))
        spin.setValue(int(initial_value))
        spin.setMinimumWidth(100)
        form.addRow("Value:", spin)

        self._widget_core.register_widget(
            "value", spin,
            role=PortRole.OUTPUT, datatype="int", default=int(initial_value),
            add_to_layout=False,
        )

        # Step 4: Wire signals
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Step 5 + 6: Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        try:
            # Mark dirty to trigger re-evaluation
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}._on_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        # Structural sync hook for undo-replay. No structural changes here.
        pass

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            val = inputs.get("value")
            return {"value": int(val) if val is not None else 0}
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}.compute: {exc}")
            return {"value": 0}

    def on_evaluate_finished(self) -> None:
        # Emit custom signal only after graph cache is updated (§12.1)
        val = self._get_cached_value("value")
        if val is not None:
            self.value_changed.emit(val)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# FloatInputNode
# ══════════════════════════════════════════════════════════════════════════════
@register_node
class FloatInputNode(ActiveNode):
    """Source node that emits a float via an editable ``QDoubleSpinBox``."""

    value_changed = Signal(float)

    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Input"
    node_name:         ClassVar[Optional[str]]       = "Float Value"
    node_description:  ClassVar[Optional[str]]       = "Floating-point value source"
    node_tags:         ClassVar[Optional[List[str]]] = ["float", "number", "input", "primitive"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Float Value", initial_value: float = 0.0, minimum: float = -1e9, maximum: float = 1e9, step: float = 0.1, decimals: int = 4, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Step 1: Add ports
        self.add_output("value", datatype="float")

        # Step 2: Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Step 3: Create widgets, place in layout, register with WidgetCore
        spin = QDoubleSpinBox()
        spin.setRange(float(minimum), float(maximum))
        spin.setSingleStep(float(step))
        spin.setDecimals(int(decimals))
        spin.setValue(float(initial_value))
        spin.setMinimumWidth(110)
        form.addRow("Value:", spin)

        self._widget_core.register_widget(
            "value", spin,
            role=PortRole.OUTPUT, datatype="float", default=float(initial_value),
            add_to_layout=False,
        )

        # Step 4: Wire signals
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Step 5 + 6: Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        try:
            # Mark dirty to trigger re-evaluation
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}._on_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        pass

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            val = inputs.get("value")
            return {"value": float(val) if val is not None else 0.0}
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}.compute: {exc}")
            return {"value": 0.0}

    def on_evaluate_finished(self) -> None:
        # Emit custom signal only after graph cache is updated (§12.1)
        val = self._get_cached_value("value")
        if val is not None:
            self.value_changed.emit(val)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# StringInputNode
# ══════════════════════════════════════════════════════════════════════════════
@register_node
class StringInputNode(ActiveNode):
    """Source node that emits a string via an editable single-line ``QLineEdit``."""

    text_changed = Signal(str)

    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Input"
    node_name:         ClassVar[Optional[str]]       = "String Value"
    node_description:  ClassVar[Optional[str]]       = "Single-line string source"
    node_tags:         ClassVar[Optional[List[str]]] = ["string", "text", "input", "primitive"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "String Value", initial_text: str = "", placeholder: str = "Enter text…", max_length: int = 0, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Step 1: Add ports
        self.add_output("text", datatype="str")

        # Step 2: Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Step 3: Create widgets, place in layout, register with WidgetCore
        line_edit = QLineEdit()
        line_edit.setText(str(initial_text))
        line_edit.setPlaceholderText(str(placeholder))
        line_edit.setMinimumWidth(130)
        if int(max_length) > 0:
            line_edit.setMaxLength(int(max_length))

        form.addRow("Text:", line_edit)

        self._widget_core.register_widget(
            "text", line_edit,
            role=PortRole.OUTPUT, datatype="str", default=str(initial_text),
            add_to_layout=False,
        )

        # Step 4: Wire signals
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Step 5 + 6: Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        try:
            # Mark dirty to trigger re-evaluation
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}._on_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        pass

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            val = inputs.get("text")
            return {"text": str(val) if val is not None else ""}
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}.compute: {exc}")
            return {"text": ""}

    def on_evaluate_finished(self) -> None:
        # Emit custom signal only after graph cache is updated (§12.1)
        val = self._get_cached_value("text")
        if val is not None:
            self.text_changed.emit(val)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextBoxInputNode
# ══════════════════════════════════════════════════════════════════════════════
@register_node
class TextBoxInputNode(ActiveNode):
    """Source node with an editable multi-line ``QTextEdit``."""

    text_changed = Signal(str)

    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Input"
    node_name:         ClassVar[Optional[str]]       = "Text Box"
    node_description:  ClassVar[Optional[str]]       = "Multi-line text editor source"
    node_tags:         ClassVar[Optional[List[str]]] = ["text", "multiline", "editor", "input", "primitive"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Text Box", initial_text: str = "", min_width: int = 180, min_height: int = 90, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Step 1: Add ports
        self.add_output("text", datatype="str")

        # Step 2: Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Step 3: Create widgets, place in layout, register with WidgetCore
        self._editor = QTextEdit()
        self._editor.setPlainText(str(initial_text))
        self._editor.setMinimumSize(int(min_width), int(min_height))
        self._editor.setAcceptRichText(False)

        form.addRow("Content:", self._editor)

        self._widget_core.register_widget(
            "text", self._editor,
            role=PortRole.OUTPUT, datatype="str", default=str(initial_text),
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
            add_to_layout=False,
        )

        # Step 4: Wire signals
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Step 5 + 6: Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        try:
            # Mark dirty to trigger re-evaluation
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}._on_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        pass

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            val = inputs.get("text")
            return {"text": str(val) if val is not None else ""}
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}.compute: {exc}")
            return {"text": ""}

    def on_evaluate_finished(self) -> None:
        # Emit custom signal only after graph cache is updated (§12.1)
        val = self._get_cached_value("text")
        if val is not None:
            self.text_changed.emit(val)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# RangeListNode
# ══════════════════════════════════════════════════════════════════════════════
@register_node
class RangeListNode(ActiveNode):
    """List generator node using Python's built-in ``range``."""

    list_changed = Signal(list)

    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Generator"
    node_name:         ClassVar[Optional[str]]       = "Range List"
    node_description:  ClassVar[Optional[str]]       = "Creates numerical lists"
    node_tags:         ClassVar[Optional[List[str]]] = ["list", "range", "generator"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Range List", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Step 1: Add ports
        self.add_output("list", datatype="list")

        # Step 2: Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Step 3: Create widgets, place in layout, register with WidgetCore (INTERNAL)
        spin_start = QSpinBox()
        spin_start.setRange(int(-9999), int(9999))
        spin_start.setValue(0)
        form.addRow("Start:", spin_start)
        self._widget_core.register_widget("start", spin_start, role=PortRole.INTERNAL, datatype="int", default=0, add_to_layout=False)

        spin_stop = QSpinBox()
        spin_stop.setRange(int(-9999), int(9999))
        spin_stop.setValue(10)
        form.addRow("Stop:", spin_stop)
        self._widget_core.register_widget("stop", spin_stop, role=PortRole.INTERNAL, datatype="int", default=10, add_to_layout=False)

        spin_step = QSpinBox()
        spin_step.setRange(int(1), int(9999))
        spin_step.setValue(1)
        form.addRow("Step:", spin_step)
        self._widget_core.register_widget("step", spin_step, role=PortRole.INTERNAL, datatype="int", default=1, add_to_layout=False)

        # Step 4: Wire signals
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Step 5 + 6: Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        try:
            # Mark dirty to trigger re-evaluation
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in {self.__class__.__name__}._on_value_changed: {e}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        pass

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            start = int(inputs.get("start") or 0)
            stop  = int(inputs.get("stop")  or 10)
            step  = int(inputs.get("step")  or 1)

            if step == 0:
                step = 1

            result_list = list(range(start, stop, step))
            return {"list": result_list}
        except Exception as e:
            log.error(f"Exception in {self.__class__.__name__}.compute: {e}")
            return {"list": []}

    def on_evaluate_finished(self) -> None:
        # Emit custom signal only after graph cache is updated (§12.1)
        val = self._get_cached_value("list")
        if val is not None:
            self.list_changed.emit(val)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextListNode
# ══════════════════════════════════════════════════════════════════════════════
@register_node
class TextListNode(ActiveNode):
    """Source node that emits a ``list[str]`` built from a multi-line ``QTextEdit``."""

    list_changed = Signal(list)

    node_class:        ClassVar[str]                 = "Basic"
    node_subclass:     ClassVar[str]                 = "Generator"
    node_name:         ClassVar[Optional[str]]       = "Text List"
    node_description:  ClassVar[Optional[str]]       = "Builds a list from newline-separated editor text"
    node_tags:         ClassVar[Optional[List[str]]] = ["list", "generator", "text", "input", "primitive"]
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.FIT

    def __init__(self, title: str = "Text List", initial_text: str = "", min_width: int = 160, min_height: int = 90, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        # Step 1: Add ports
        self.add_output("list", datatype="list")

        # Step 2: Build layout + WidgetCore
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)

        # Step 3: Create widgets, place in layout, register with WidgetCore (INTERNAL)
        self._editor = QTextEdit()
        self._editor.setPlainText(str(initial_text))
        self._editor.setMinimumSize(int(min_width), int(min_height))
        self._editor.setAcceptRichText(False)
        self._editor.setPlaceholderText("One item per line…")

        form.addRow("Lines:", self._editor)

        self._widget_core.register_widget(
            "text", self._editor,
            role=PortRole.INTERNAL, datatype="str", default=str(initial_text),
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
            add_to_layout=False,
        )

        # Step 4: Wire signals
        self._widget_core.value_changed.connect(self._on_value_changed)
        self._widget_core.port_value_written.connect(self._on_port_value_written)

        # Step 5 + 6: Mount & patch proxy
        self.set_content_widget(self._widget_core)
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()

    @Slot(str)
    def _on_value_changed(self, port: str) -> None:
        try:
            # Mark dirty to trigger re-evaluation
            self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}._on_value_changed: {exc}")

    @Slot(str, object)
    def _on_port_value_written(self, port: str, value: Any) -> None:
        pass

    @staticmethod
    def _parse_lines(raw: str) -> List[str]:
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            val = inputs.get("text")
            result = self._parse_lines(str(val) if val is not None else "")
            return {"list": result}
        except Exception as exc:
            log.error(f"Exception in {self.__class__.__name__}.compute: {exc}")
            return {"list": []}

    def on_evaluate_finished(self) -> None:
        # Emit custom signal only after graph cache is updated (§12.1)
        val = self._get_cached_value("list")
        if val is not None:
            self.list_changed.emit(val)
        super().on_evaluate_finished()

    def cleanup(self) -> None:
        super().cleanup()
