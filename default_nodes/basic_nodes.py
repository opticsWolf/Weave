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
* The ``SmartDisplayNode`` converter lives in a standalone
  :class:`ValueConverter` class so it can be unit-tested in isolation
  and re-used by other display nodes.
"""

from __future__ import annotations

import math
import numpy as np
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
from weave.widgetcore import PortRole, WidgetCore

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

    Inputs
    ------
    (none)

    Outputs
    -------
    value : int
        Current spinbox value.

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    initial_value : int
        Seed value for the spinbox.
    minimum : int
        Minimum allowed value (default ``-2_147_483_648``).
    maximum : int
        Maximum allowed value (default ``2_147_483_647``).
    step : int
        Single-step increment (default ``1``).
    """

    value_changed = Signal(int)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Integer Value"
    node_description: ClassVar[Optional[str]] = "Integer value source"
    node_tags: ClassVar[Optional[List[str]]] = ["int", "integer", "number", "input", "primitive"]

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
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(initial_value)
        spin.setMinimumWidth(100)

        self._widget_core.register_widget(
            "value", spin,
            role="output",
            datatype="int",
            default=0,
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._cached_values["value"] = initial_value

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
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# FloatInputNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class FloatInputNode(ActiveNode):
    """
    Source node that emits a float via an editable ``QDoubleSpinBox``.

    Unlike the legacy ``FloatNode`` in ``simple_nodes.py`` this node
    does **not** expose a *factor* input port — it is a pure, minimal
    float source.

    Type: Active (propagates downstream on every value change).

    Inputs
    ------
    (none)

    Outputs
    -------
    value : float
        Current spinbox value.

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    initial_value : float
        Seed value for the spinbox.
    minimum : float
        Minimum allowed value (default ``-1e9``).
    maximum : float
        Maximum allowed value (default ``1e9``).
    step : float
        Single-step increment (default ``0.1``).
    decimals : int
        Number of decimal places displayed (default ``4``).
    """

    value_changed = Signal(float)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Float Value"
    node_description: ClassVar[Optional[str]] = "Floating-point value source"
    node_tags: ClassVar[Optional[List[str]]] = ["float", "number", "input", "primitive"]

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
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(initial_value)
        spin.setMinimumWidth(110)

        self._widget_core.register_widget(
            "value", spin,
            role="output",
            datatype="float",
            default=0.0,
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._cached_values["value"] = initial_value

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
        self._widget_core.cleanup()
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

    Inputs
    ------
    (none)

    Outputs
    -------
    text : string
        Current text contents of the line-edit.

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    initial_text : str
        Seed value for the line-edit.
    placeholder : str
        Placeholder text shown when the field is empty.
    max_length : int
        Maximum number of characters allowed (0 = unlimited).
    """

    text_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "String Value"
    node_description: ClassVar[Optional[str]] = "Single-line string source"
    node_tags: ClassVar[Optional[List[str]]] = ["string", "text", "input", "primitive"]

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
        line_edit.setText(initial_text)
        line_edit.setPlaceholderText(placeholder)
        line_edit.setMinimumWidth(130)
        if max_length > 0:
            line_edit.setMaxLength(max_length)

        self._widget_core.register_widget(
            "text", line_edit,
            role="output",
            datatype="string",
            default="",
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._cached_values["text"] = initial_text

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(
                    self._widget_core.get_port_value("text")
                )
        except Exception as exc:
            log.error(f"Exception in StringInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current line-edit text."""
        try:
            return {"text": self._widget_core.get_port_value("text")}
        except Exception as exc:
            log.error(f"Exception in StringInputNode.compute: {exc}")
            return {"text": ""}

    def cleanup(self) -> None:
        self._widget_core.cleanup()
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

    Inputs
    ------
    (none)

    Outputs
    -------
    text : string
        Full plain-text contents of the editor.

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    initial_text : str
        Seed value for the editor.
    min_width : int
        Minimum widget width in pixels (default ``180``).
    min_height : int
        Minimum widget height in pixels (default ``90``).
    """

    text_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Text Box"
    node_description: ClassVar[Optional[str]] = "Multi-line text editor source"
    node_tags: ClassVar[Optional[List[str]]] = ["text", "multiline", "editor", "input", "primitive"]

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
        self._editor.setPlainText(initial_text)
        self._editor.setMinimumSize(min_width, min_height)
        self._editor.setAcceptRichText(False)

        # QTextEdit needs explicit getter/setter because WidgetCore's
        # generic path handles it but we want plain-text only.
        self._widget_core.register_widget(
            "text", self._editor,
            role="output",
            datatype="string",
            default="",
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        # textChanged has no arguments — use a lambda shim
        self._editor.textChanged.connect(lambda: self._on_core_changed("text"))
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._cached_values["text"] = initial_text

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        try:
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(
                    self._widget_core.get_port_value("text")
                )
        except Exception as exc:
            log.error(f"Exception in TextBoxInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current editor plain-text."""
        try:
            return {"text": self._widget_core.get_port_value("text")}
        except Exception as exc:
            log.error(f"Exception in TextBoxInputNode.compute: {exc}")
            return {"text": ""}

    def cleanup(self) -> None:
        self._widget_core.cleanup()
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

    Outputs
    -------
    list : list[int]
        The generated sequence ``list(range(start, stop, step))``.
    """
    list_changed = Signal(list)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[Optional[str]] = "Range List"
    node_description: ClassVar[Optional[str]] = "Creates numerical lists"
    node_tags: ClassVar[Optional[List[str]]] = ["list", "range", "generator"]

    def __init__(self, title: str = "Range List", **kwargs: Any) -> None:
        """Creates a list generator with start, stop, and step parameters."""
        super().__init__(title=title, **kwargs)

        self.add_output("list", "list")

        # Use a QFormLayout so labels and spinboxes sit on the same row, keeping
        # the node body compact.  Pass it directly to the WidgetCore constructor.
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
        spin_step.setRange(1, 9999)   # Step must be ≥ 1; prevents zero-step infinite loops
        spin_step.setValue(1)

        # Add labelled rows directly to the form layout before registration so
        # the WidgetCore doesn't try to add them a second time.
        form.addRow("Start:", spin_start)
        form.addRow("Stop:",  spin_stop)
        form.addRow("Step:",  spin_step)

        # Register each spinbox with add_to_layout=False because we already
        # placed them in the form layout above.
        self._widget_core.register_widget(
            "start", spin_start,
            role="output", datatype="float", default=0.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            "stop", spin_stop,
            role="output", datatype="float", default=10.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            "step", spin_step,
            role="output", datatype="float", default=1.0,
            add_to_layout=False,
        )

        # A single connection to WidgetCore.value_changed covers all three
        # spinboxes — no need for individual valueChanged slots.
        self._widget_core.value_changed.connect(self._on_core_changed)

        self.set_content_widget(self._widget_core)
        # refresh_widget_palettes() is called explicitly here because the QFormLayout
        # row labels ("Start:", "Stop:", "Step:") are created by Qt internally when
        # addRow() is called, after WidgetCore.__init__ has already run its initial
        # _apply_container_background().  Without this call those labels will not
        # receive the theme palette until the next StyleManager theme-change event.
        self._widget_core.patch_proxy()
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
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextListNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TextListNode(ActiveNode):
    """
    Source node that emits a ``list[str]`` built from a multi-line
    ``QTextEdit``.  Each non-empty line becomes one element.

    The editor trims leading/trailing whitespace from every line and
    silently drops blank lines, so spacing between items is irrelevant.

    Type: Active (propagates downstream on every text change).

    Inputs
    ------
    (none)

    Outputs
    -------
    list : list
        Items derived from the editor contents, one per non-empty line.

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    initial_text : str
        Seed text — newline-separated items pre-loaded into the editor.
    min_width : int
        Minimum widget width in pixels (default ``160``).
    min_height : int
        Minimum widget height in pixels (default ``90``).
    """

    list_changed = Signal(list)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[Optional[str]] = "Text List"
    node_description: ClassVar[Optional[str]] = "Builds a list from newline-separated editor text"
    node_tags: ClassVar[Optional[List[str]]] = [
        "list", "generator", "text", "input", "primitive",
    ]

    def __init__(
        self,
        title: str = "Text List",
        initial_text: str = "",
        min_width: int = 160,
        min_height: int = 90,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        self.add_output("list", "list")

        # ── WidgetCore + QTextEdit ────────────────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        self._editor = QTextEdit()
        self._editor.setPlainText(initial_text)
        self._editor.setMinimumSize(min_width, min_height)
        self._editor.setAcceptRichText(False)
        self._editor.setPlaceholderText("One item per line…")

        # Register as a display-role widget — the list output port is
        # created manually above; the editor itself is not a port widget.
        self._widget_core.register_widget(
            "text", self._editor,
            role="display",
            datatype="string",
            default="",
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
        )

        # textChanged carries no arguments — shim to named slot
        self._editor.textChanged.connect(lambda: self._on_editor_changed())
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._cached_values["list"] = self._parse_lines(initial_text)

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
            log.error(f"Exception in SimpleListNode._on_editor_changed: {exc}")

    # ── Computation ──────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Parses the editor text and returns the item list."""
        try:
            result = self._parse_lines(self._editor.toPlainText())
            return {"list": result}
        except Exception as exc:
            log.error(f"Exception in SimpleListNode.compute: {exc}")
            return {"list": []}

    def cleanup(self) -> None:
        self._widget_core.cleanup()
        super().cleanup()