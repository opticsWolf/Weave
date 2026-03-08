# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

datetime_nodes.py
-----------------
Sample node implementations that use the proxy-safe date/time widgets
from ``proxydatetimewidgets``.

Provided nodes
--------------
``DateNode``
    Source node outputting an ISO date string (``"yyyy-MM-dd"``) via a
    ``ProxyDateEdit`` picker.

``TimeNode``
    Source node outputting a time string (``"HH:mm:ss"``) via a
    ``ProxyTimeEdit`` picker.

``DateTimeNode``
    Source node outputting an ISO-8601 datetime string via a
    ``ProxyDateTimeEdit`` picker (calendar + H/M/S spinboxes).

``DateTimeFormatterNode``
    Utility node that accepts an upstream ISO-8601 datetime string and
    outputs it re-formatted according to a user-chosen format string.

WidgetCore note
---------------
``QDateTimeEdit``, ``QDateEdit``, and ``QTimeEdit`` have no native
branch in ``WidgetCore._generic_get``.  Each registration below
therefore supplies explicit ``getter`` and ``setter`` lambdas so the
core can read, write, and serialise values as JSON-safe strings.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from PySide6.QtCore import QDate, QDateTime, QTime, Qt, Signal, Slot
from PySide6.QtWidgets import QFormLayout, QLabel

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import PortRole, WidgetCore

from weave.widgets.proxydatetimewidgets import (
    ProxyDateEdit,
    ProxyDateTimeEdit,
    ProxyTimeEdit,
)

from weave.widgets.proxycombobox import ProxyComboBox

from weave.logger import get_logger

log = get_logger("DateTimeNodes")


# ══════════════════════════════════════════════════════════════════════════════
# DateNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class DateNode(ActiveNode):
    """
    Source node that emits a date string via a ``ProxyDateEdit`` calendar
    picker.

    Type: Active (propagates downstream immediately on date change).

    Outputs
    -------
    date : string
        Selected date formatted as ``"yyyy-MM-dd"``.
    """

    date_changed = Signal(str)

    node_class: ClassVar[str] = "DateTime"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Date Picker"
    node_description: ClassVar[Optional[str]] = "Outputs a selected date string"
    node_tags: ClassVar[Optional[List[str]]] = ["date", "calendar", "input", "datetime"]

    _DATE_FMT = "yyyy-MM-dd"

    def __init__(
        self,
        title: str = "Date Picker",
        initial_date: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        title : str
            Node title shown in the graph view.
        initial_date : str, optional
            ISO date string (``"yyyy-MM-dd"``).  Defaults to today.
        """
        super().__init__(title=title, **kwargs)

        self._widget_core = WidgetCore()

        picker = ProxyDateEdit()
        if initial_date:
            parsed = QDate.fromString(initial_date, self._DATE_FMT)
            if parsed.isValid():
                picker.setDate(parsed)

        # Explicit getter/setter — WidgetCore has no native QDateEdit branch.
        self._widget_core.register_widget(
            "date", picker,
            role="output",
            datatype="string",
            default=QDate.currentDate().toString(self._DATE_FMT),
            getter=lambda: picker.date().toString(self._DATE_FMT),
            setter=lambda v: picker.setDate(
                QDate.fromString(str(v), self._DATE_FMT)
            ),
        )

        # Auto-create output ports from registered OUTPUT/BIDIRECTIONAL widgets.
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        self._widget_core.value_changed.connect(self._on_core_changed)

        # WidgetCore's hook doesn't fire after setDate() is called from inside
        # showPopup()'s QDialog event loop.  Connect dateChanged directly.
        picker.dateChanged.connect(lambda _: self._on_core_changed("date"))

        self.set_content_widget(self._widget_core)

        self._cached_values["date"] = picker.date().toString(self._DATE_FMT)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates picker changes to the graph."""
        try:
            if port_name == "date":
                self.on_ui_change()
                self.date_changed.emit(self._widget_core.get_port_value("date"))
        except Exception as exc:
            log.error(f"Exception in DateNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the currently selected date string."""
        try:
            return {"date": self._widget_core.get_port_value("date")}
        except Exception as exc:
            log.error(f"Exception in DateNode.compute: {exc}")
            return {"date": QDate.currentDate().toString(self._DATE_FMT)}

    def cleanup(self) -> None:
        """Safe teardown."""
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# TimeNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class TimeNode(ActiveNode):
    """
    Source node that emits a time string via a ``ProxyTimeEdit`` dialog.

    Type: Active (propagates downstream immediately on time change).

    Outputs
    -------
    time : string
        Selected time formatted as ``"HH:mm:ss"``.
    """

    time_changed = Signal(str)

    node_class: ClassVar[str] = "DateTime"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Time Picker"
    node_description: ClassVar[Optional[str]] = "Outputs a selected time string"
    node_tags: ClassVar[Optional[List[str]]] = ["time", "clock", "input", "datetime"]

    _TIME_FMT = "HH:mm:ss"

    def __init__(
        self,
        title: str = "Time Picker",
        initial_time: str = "00:00:00",
        show_seconds: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        title : str
            Node title shown in the graph view.
        initial_time : str
            Time string in ``"HH:mm:ss"`` format.
        show_seconds : bool
            Passed through to ``ProxyTimeEdit``; hides the seconds spinbox
            when ``False``.
        """
        super().__init__(title=title, **kwargs)

        self._widget_core = WidgetCore()

        picker = ProxyTimeEdit(show_seconds=show_seconds)
        parsed = QTime.fromString(initial_time, self._TIME_FMT)
        if parsed.isValid():
            picker.setTime(parsed)

        self._widget_core.register_widget(
            "time", picker,
            role="output",
            datatype="string",
            default="00:00:00",
            getter=lambda: picker.time().toString(self._TIME_FMT),
            setter=lambda v: picker.setTime(
                QTime.fromString(str(v), self._TIME_FMT)
            ),
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        self._widget_core.value_changed.connect(self._on_core_changed)

        # WidgetCore's hook doesn't fire after setTime() is called from inside
        # showPopup()'s QDialog event loop.  Connect timeChanged directly.
        picker.timeChanged.connect(lambda _: self._on_core_changed("time"))

        self.set_content_widget(self._widget_core)

        self._cached_values["time"] = picker.time().toString(self._TIME_FMT)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates picker changes to the graph."""
        try:
            if port_name == "time":
                self.on_ui_change()
                self.time_changed.emit(self._widget_core.get_port_value("time"))
        except Exception as exc:
            log.error(f"Exception in TimeNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the currently selected time string."""
        try:
            return {"time": self._widget_core.get_port_value("time")}
        except Exception as exc:
            log.error(f"Exception in TimeNode.compute: {exc}")
            return {"time": "00:00:00"}

    def cleanup(self) -> None:
        """Safe teardown."""
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# DateTimeNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class DateTimeNode(ActiveNode):
    """
    Source node that emits a full ISO-8601 datetime string via a
    ``ProxyDateTimeEdit`` picker (calendar + hour / minute / second).

    Type: Active (propagates downstream immediately on datetime change).

    Outputs
    -------
    datetime : string
        Selected datetime in ``Qt.DateFormat.ISODate`` format
        (e.g. ``"2026-03-08T14:30:00"``).
    """

    datetime_changed = Signal(str)

    node_class: ClassVar[str] = "DateTime"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "DateTime Picker"
    node_description: ClassVar[Optional[str]] = "Outputs a selected ISO datetime string"
    node_tags: ClassVar[Optional[List[str]]] = ["datetime", "calendar", "time", "input"]

    def __init__(
        self,
        title: str = "DateTime Picker",
        initial_datetime: Optional[str] = None,
        show_seconds: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        title : str
            Node title shown in the graph view.
        initial_datetime : str, optional
            ISO-8601 string used to seed the picker.  Defaults to *now*.
        show_seconds : bool
            Passed through to ``ProxyDateTimeEdit``; hides the seconds
            spinbox when ``False``.
        """
        super().__init__(title=title, **kwargs)

        self._widget_core = WidgetCore()

        picker = ProxyDateTimeEdit(show_seconds=show_seconds)
        if initial_datetime:
            parsed = QDateTime.fromString(initial_datetime, Qt.DateFormat.ISODate)
            if parsed.isValid():
                picker.setDateTime(parsed)

        _default_dt = QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate)

        self._widget_core.register_widget(
            "datetime", picker,
            role="output",
            datatype="string",
            default=_default_dt,
            getter=lambda: picker.dateTime().toString(Qt.DateFormat.ISODate),
            setter=lambda v: picker.setDateTime(
                QDateTime.fromString(str(v), Qt.DateFormat.ISODate)
            ),
        )

        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)

        self._widget_core.value_changed.connect(self._on_core_changed)

        # WidgetCore's hook doesn't fire after setDateTime() is called from
        # inside showPopup()'s QDialog event loop.  Connect directly.
        picker.dateTimeChanged.connect(lambda _: self._on_core_changed("datetime"))

        self.set_content_widget(self._widget_core)

        self._cached_values["datetime"] = picker.dateTime().toString(
            Qt.DateFormat.ISODate
        )

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates picker changes to the graph."""
        try:
            if port_name == "datetime":
                self.on_ui_change()
                self.datetime_changed.emit(
                    self._widget_core.get_port_value("datetime")
                )
        except Exception as exc:
            log.error(f"Exception in DateTimeNode._on_core_changed: {exc}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the currently selected ISO datetime string."""
        try:
            return {"datetime": self._widget_core.get_port_value("datetime")}
        except Exception as exc:
            log.error(f"Exception in DateTimeNode.compute: {exc}")
            return {
                "datetime": QDateTime.currentDateTime().toString(
                    Qt.DateFormat.ISODate
                )
            }

    def cleanup(self) -> None:
        """Safe teardown."""
        self._widget_core.cleanup()
        super().cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# DateTimeFormatterNode
# ══════════════════════════════════════════════════════════════════════════════

# Preset format strings shown in the dropdown.  Order matches the combo-box
# item indices; the format string doubles as the display label.
_FORMAT_PRESETS: List[str] = [
    "dd/MM/yyyy HH:mm:ss",
    "yyyy-MM-dd HH:mm:ss",
    "MM/dd/yyyy hh:mm AP",
    "dd.MM.yyyy HH:mm",
    "yyyy/MM/dd HH:mm",
    "dddd, MMMM d yyyy",
    "ddd MMM d yyyy",
    "dd-MMM-yyyy",
    "yyyy-MM-dd",
    "HH:mm:ss",
    "hh:mm AP",
    "yyyyMMddHHmmss",
    "d MMMM yyyy, HH:mm",
    "ddd, dd MMM yyyy HH:mm",
]


@register_node
class DateTimeFormatterNode(ActiveNode):
    """
    Utility node that re-formats an upstream ISO-8601 datetime string.

    Accepts a datetime string on its ``datetime`` input port.  The desired
    output format is chosen from a ``ProxyComboBox`` dropdown of common
    presets **or** typed by hand into the editable combo-box line-edit.
    The formatted result is shown in a label and emitted on the
    ``formatted`` output port.

    Type: Active (re-formats whenever either input or the format changes).

    Inputs
    ------
    datetime : string
        ISO-8601 datetime string produced by e.g. ``DateTimeNode``.

    Outputs
    -------
    formatted : string
        The datetime re-formatted according to the selected format string.

    Keyboard handling
    -----------------
    ``ProxyComboBox`` opens its popup via a ``QMenu`` so it works
    correctly inside a ``QGraphicsProxyWidget``.  Keyboard input
    (Backspace, Delete, arrows, Ctrl+C/V/X, etc.) is handled by the
    canvas-level widget-editing guard in
    :class:`~weave.canvas.canvas_core.Canvas`, which suppresses all
    scene shortcuts while the embedded line-edit has focus.
    """

    node_class: ClassVar[str] = "DateTime"
    node_subclass: ClassVar[str] = "Utility"
    node_name: ClassVar[Optional[str]] = "DateTime Formatter"
    node_description: ClassVar[Optional[str]] = "Re-formats a datetime string"
    node_tags: ClassVar[Optional[List[str]]] = ["datetime", "format", "utility", "string"]

    _DEFAULT_FMT = "dd/MM/yyyy HH:mm:ss"

    def __init__(
        self,
        title: str = "DateTime Formatter",
        fmt: str = _DEFAULT_FMT,
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        title : str
            Node title shown in the graph view.
        fmt : str
            Initial Qt format string.  If it matches a preset the
            combo-box highlights that entry; otherwise the text is
            shown as a custom value in the editable field.
        """
        super().__init__(title=title, **kwargs)

        self.add_input("datetime", "string")
        self.add_output("formatted", "string")

        # ── Layout ────────────────────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)

        # ── Editable ProxyComboBox with format presets ────────────────────────
        fmt_combo = ProxyComboBox()
        fmt_combo.setEditable(True)
        fmt_combo.setInsertPolicy(ProxyComboBox.InsertPolicy.NoInsert)
        fmt_combo.setMinimumWidth(180)

        # Populate presets
        for preset in _FORMAT_PRESETS:
            fmt_combo.addItem(preset)

        # Set initial value — match a preset or show custom text
        preset_idx = fmt_combo.findText(fmt)
        if preset_idx >= 0:
            fmt_combo.setCurrentIndex(preset_idx)
        else:
            fmt_combo.setEditText(fmt)

        form.addRow("Format:", fmt_combo)

        # Register with WidgetCore using explicit getter / setter so we
        # always read from the line-edit text (which reflects both preset
        # selection *and* freehand edits).  WidgetCore sees a QComboBox and
        # would normally use currentText / setCurrentText, but an explicit
        # getter/setter keeps behaviour unambiguous.
        self._fmt_combo = fmt_combo
        self._widget_core.register_widget(
            "format", fmt_combo,
            role="bidirectional",
            datatype="string",
            default=self._DEFAULT_FMT,
            getter=lambda: fmt_combo.currentText(),
            setter=lambda v: self._apply_format_value(str(v)),
            add_to_layout=False,   # already placed in the form above
        )

        # ── Display label for the formatted result ────────────────────────────
        result_label = QLabel("—")
        result_label.setStyleSheet("QLabel { color: #adf; }")
        form.addRow("Result:", result_label)

        self._widget_core.register_widget(
            "formatted_display", result_label,
            role="display",
            datatype="string",
            default="—",
            add_to_layout=False,
        )

        self._result_label = result_label

        # ── Signal wiring ─────────────────────────────────────────────────────
        # Dropdown selection → immediate re-evaluation.
        fmt_combo.currentIndexChanged.connect(
            lambda _idx: self._on_core_changed("format")
        )

        # Freehand typing → re-evaluate on every keystroke so the result
        # label updates instantly and the widget feels responsive.
        line_edit = fmt_combo.lineEdit()
        if line_edit is not None:
            line_edit.textChanged.connect(
                lambda _text: self._on_core_changed("format")
            )

        self.set_content_widget(self._widget_core)

        self._pending_formatted: str = "—"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _apply_format_value(self, value: str) -> None:
        """
        Setter used by WidgetCore during state restore.

        If *value* matches a preset, selects that index (so the dropdown
        highlights correctly).  Otherwise writes the text into the
        line-edit as a custom format.
        """
        idx = self._fmt_combo.findText(value)
        if idx >= 0:
            self._fmt_combo.setCurrentIndex(idx)
        else:
            self._fmt_combo.setEditText(value)

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Re-triggers graph evaluation when the format field changes."""
        try:
            if port_name == "format":
                self.on_ui_change()
        except Exception as exc:
            log.error(f"Exception in DateTimeFormatterNode._on_core_changed: {exc}")

    # ── Compute / display ─────────────────────────────────────────────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Parses the incoming datetime string and applies the format."""
        try:
            raw = inputs.get("datetime", "")
            fmt = self._widget_core.get_port_value("format") or self._DEFAULT_FMT

            dt = QDateTime.fromString(str(raw), Qt.DateFormat.ISODate)
            if dt.isValid():
                formatted = dt.toString(fmt)
            else:
                formatted = f"[invalid: {raw!r}]"

            self._pending_formatted = formatted
            return {"formatted": formatted}

        except Exception as exc:
            log.error(f"Exception in DateTimeFormatterNode.compute: {exc}")
            self._pending_formatted = "Error"
            return {"formatted": "Error"}

    def on_evaluate_finished(self) -> None:
        """Updates the display label once the evaluation cycle completes."""
        try:
            super().on_evaluate_finished()
            try:
                self._result_label.setText(self._pending_formatted)
            except RuntimeError:
                pass  # Widget already deleted
        except Exception as exc:
            log.error(f"Exception in DateTimeFormatterNode.on_evaluate_finished: {exc}")

    def cleanup(self) -> None:
        """Safe teardown."""
        self._widget_core.cleanup()
        super().cleanup()