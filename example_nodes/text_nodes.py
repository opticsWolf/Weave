# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

text_nodes.py
-------------
Text-oriented node implementations for the Weave node graph.

Keyboard input (Backspace, Delete, arrows, Ctrl+C/V/X, etc.) works
correctly inside the ``QGraphicsProxyWidget`` because the canvas-level
widget-editing guard in :class:`~weave.canvas.canvas_core.Canvas`
suppresses all scene shortcuts and default ``QGraphicsScene`` key
handling while a proxy widget holds focus.  No per-widget event
filters are needed.

Provided nodes
--------------
``TextInputNode``
    Source node with an editable ``QLineEdit``.  Outputs the current
    text on every keystroke and responds immediately.

``TextEditNode``
    Source / utility node with an editable ``QTextEdit`` for multi-line
    text.  Outputs the full plain-text content whenever the text
    changes.

``TextLoggerNode``
    Sink node with a *read-only* ``QTextEdit`` that appends incoming
    strings.  Because it is read-only the user cannot type into the
    widget — included here for completeness so all text-oriented nodes
    live in one module.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QLineEdit, QTextEdit

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import PortRole, WidgetCore

from weave.logger import get_logger

log = get_logger("TextNodes")


# ══════════════════════════════════════════════════════════════════════════════
# TextInputNode  (QLineEdit)
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TextInputNode(ActiveNode):
    """
    Source node that emits a string via an editable ``QLineEdit``.

    Type: Active (propagates downstream on every keystroke).

    Outputs
    -------
    text : string
        Current contents of the line-edit.
    """

    text_changed = Signal(str)

    node_class: ClassVar[str] = "Text"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Text Input"
    node_description: ClassVar[Optional[str]] = "Single-line text input"
    node_tags: ClassVar[Optional[List[str]]] = ["input", "text", "string"]

    def __init__(
        self,
        title: str = "Text Input",
        initial_text: str = "",
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        title : str
            Node title shown in the graph view.
        initial_text : str
            Seed value for the line-edit.
        """
        super().__init__(title=title, **kwargs)

        # ── WidgetCore + QLineEdit ────────────────────────────────────────────
        self._widget_core = WidgetCore()

        line_edit = QLineEdit()
        line_edit.setText(initial_text)
        line_edit.setPlaceholderText("Enter text...")
        line_edit.setMinimumWidth(120)

        self._widget_core.register_widget(
            "text", line_edit,
            role="output",
            datatype="string",
            default="",
        )

        # Auto-create output ports from registered widgets.
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        # WidgetCore hooks QLineEdit to textChanged internally, so
        # value_changed fires on every keystroke — exactly what we want
        # for a responsive feel.
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)

        self._cached_values["text"] = initial_text

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates text changes to the graph."""
        try:
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(self._widget_core.get_port_value("text"))
        except Exception as exc:
            log.error(f"Exception in TextInputNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current line-edit text."""
        try:
            return {"text": self._widget_core.get_port_value("text")}
        except Exception as exc:
            log.error(f"Exception in TextInputNode.compute: {exc}")
            return {"text": ""}

    def cleanup(self) -> None:
        """Safe teardown."""
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextEditNode  (QTextEdit — editable, multi-line)
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TextEditNode(ActiveNode):
    """
    Source / utility node with an editable multi-line ``QTextEdit``.

    Type: Active (propagates downstream whenever the text changes).

    Inputs
    ------
    text_in : string, optional
        When connected, the incoming string *replaces* the editor
        contents (useful for chaining transforms).  When disconnected
        the user edits freely.

    Outputs
    -------
    text : string
        Full plain-text contents of the editor.
    """

    text_changed = Signal(str)

    node_class: ClassVar[str] = "Text"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Text Editor"
    node_description: ClassVar[Optional[str]] = "Multi-line text editor"
    node_tags: ClassVar[Optional[List[str]]] = ["input", "text", "editor", "multiline"]

    def __init__(
        self,
        title: str = "Text Editor",
        initial_text: str = "",
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        title : str
            Node title shown in the graph view.
        initial_text : str
            Seed value for the editor.
        """
        super().__init__(title=title, **kwargs)

        self.add_input("text_in", "string")

        # ── WidgetCore + QTextEdit ────────────────────────────────────────────
        self._widget_core = WidgetCore()

        editor = QTextEdit()
        editor.setPlainText(initial_text)
        editor.setMinimumSize(180, 100)
        editor.setAcceptRichText(False)

        # QTextEdit has no native WidgetCore branch — supply explicit
        # getter / setter.
        self._editor = editor
        self._widget_core.register_widget(
            "text", editor,
            role="output",
            datatype="string",
            default="",
            getter=lambda: editor.toPlainText(),
            setter=lambda v: editor.setPlainText(str(v)),
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        # textChanged fires on every modification — gives instant
        # downstream updates while the user types.
        editor.textChanged.connect(lambda: self._on_core_changed("text"))

        self.set_content_widget(self._widget_core)

        self._cached_values["text"] = initial_text

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates editor changes to the graph."""
        try:
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(self._widget_core.get_port_value("text"))
        except Exception as exc:
            log.error(f"Exception in TextEditNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        If *text_in* is connected, replaces the editor contents;
        otherwise returns the current editor text.
        """
        try:
            incoming = inputs.get("text_in")
            if incoming is not None:
                text = str(incoming)
                # Update editor to reflect the upstream value.  Block
                # signals so we don't re-trigger on_ui_change().
                self._editor.blockSignals(True)
                try:
                    self._editor.setPlainText(text)
                finally:
                    self._editor.blockSignals(False)
            else:
                text = self._widget_core.get_port_value("text")

            return {"text": text}
        except Exception as exc:
            log.error(f"Exception in TextEditNode.compute: {exc}")
            return {"text": ""}

    def cleanup(self) -> None:
        """Safe teardown."""
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TextLoggerNode  (QTextEdit — read-only sink)
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TextLoggerNode(ActiveNode):
    """
    Sink node that appends incoming strings to a read-only ``QTextEdit``.

    Type: Active (reacts to upstream pushes).

    Inputs
    ------
    append : string
        Each evaluation cycle appends this value as a new line.

    Display
    -------
    The ``QTextEdit`` shows the accumulated log.
    """

    node_class: ClassVar[str] = "Text"
    node_subclass: ClassVar[str] = "Output"
    node_name: ClassVar[Optional[str]] = "Text Logger"
    node_description: ClassVar[Optional[str]] = "Logs text to read-only editor"
    node_tags: ClassVar[Optional[List[str]]] = ["log", "text", "editor", "output"]

    def __init__(self, title: str = "Logger", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)

        self.add_input("append", "string")

        # ── WidgetCore + read-only QTextEdit ──────────────────────────────────
        self._widget_core = WidgetCore()

        self._editor = QTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setMinimumSize(150, 100)

        self._widget_core.register_widget(
            "text", self._editor,
            role="display",
            datatype="string",
            default="",
            getter=lambda: self._editor.toPlainText(),
            setter=lambda v: self._editor.setPlainText(str(v)),
        )

        self.set_content_widget(self._widget_core)

        self._pending_append: Optional[str] = None

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Captures the incoming string for the next UI update cycle."""
        try:
            append_val = inputs.get("append")
            if append_val is not None:
                self._pending_append = str(append_val)
        except Exception as exc:
            log.error(f"Exception in TextLoggerNode.compute: {exc}")
            self._pending_append = None

        try:
            return {"text": self._widget_core.get_port_value("text")}
        except RuntimeError:
            return {"text": ""}

    def on_evaluate_finished(self) -> None:
        """Appends buffered text after the evaluation cycle completes."""
        try:
            super().on_evaluate_finished()
            if self._pending_append is not None:
                try:
                    self._editor.append(self._pending_append)
                    self._pending_append = None
                    self._widget_core.set_port_value(
                        "text", self._editor.toPlainText()
                    )
                except RuntimeError:
                    pass  # Widget already deleted
        except Exception as exc:
            log.error(f"Exception in TextLoggerNode.on_evaluate_finished: {exc}")

    def cleanup(self) -> None:
        """Safe teardown."""
        self._pending_append = None
        self._widget_core.cleanup()
        super().cleanup()