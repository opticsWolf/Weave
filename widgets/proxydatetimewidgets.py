# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

proxydatetimewidgets.py
------------------------
Proxy-safe wrappers for Qt date and time widgets whose popup mechanisms
are broken inside ``QGraphicsProxyWidget``.

Provided classes
----------------
``ProxyDateTimeEdit``
    ``QDateTimeEdit`` replacement.  ``showPopup()`` opens a frameless
    ``QDialog`` containing a ``QCalendarWidget`` and H / M / S spinboxes.

``ProxyDateEdit``
    ``QDateEdit`` replacement.  Calendar-only picker; double-clicking a
    date or pressing Enter accepts immediately.

``ProxyTimeEdit``
    ``QTimeEdit`` replacement.  Compact H / M / S form dialog.

Root cause
----------
Qt's popup mechanism creates a child ``QFrame`` or ``QWidget`` and wraps
it in a second ``QGraphicsProxyWidget`` (a "sub-proxy").  That sub-proxy
is clipped to the bounding rect of the host proxy and is often positioned
incorrectly when the scene is panned or zoomed.  Each wrapper replaces
``showPopup()`` with a ``QDialog`` that is a fully independent native
top-level window, bypassing the sub-proxy pipeline entirely.

WidgetCore registration
-----------------------
``WidgetCore`` has no native branch for ``QDateTimeEdit``, ``QDateEdit``
or ``QTimeEdit``.  Always supply explicit ``getter`` and ``setter`` so
the core can read / write values and serialise them as JSON-safe strings.

Examples::

    from PySide6.QtCore import QDate, QTime, QDateTime, Qt

    # QDateEdit
    core.register_widget(
        "date", my_date_edit,
        role="output", datatype="string", default="2000-01-01",
        getter=lambda: my_date_edit.date().toString("yyyy-MM-dd"),
        setter=lambda v: my_date_edit.setDate(
            QDate.fromString(str(v), "yyyy-MM-dd")
        ),
    )

    # QTimeEdit
    core.register_widget(
        "time", my_time_edit,
        role="output", datatype="string", default="00:00:00",
        getter=lambda: my_time_edit.time().toString("HH:mm:ss"),
        setter=lambda v: my_time_edit.setTime(
            QTime.fromString(str(v), "HH:mm:ss")
        ),
    )

    # QDateTimeEdit
    core.register_widget(
        "datetime", my_datetime_edit,
        role="output", datatype="string",
        default=QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate),
        getter=lambda: my_datetime_edit.dateTime().toString(
            Qt.DateFormat.ISODate
        ),
        setter=lambda v: my_datetime_edit.setDateTime(
            QDateTime.fromString(str(v), Qt.DateFormat.ISODate)
        ),
    )
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QDate, QTime, QDateTime
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QCalendarWidget, QSpinBox, QLabel, QDialogButtonBox, QSizePolicy,
    QDateEdit, QDateTimeEdit, QTimeEdit,
)

from weave.widgets.proxycombobox import _ProxyGlobalPosMixin


# ══════════════════════════════════════════════════════════════════════════════
# Shared dark stylesheet
# ══════════════════════════════════════════════════════════════════════════════

_DIALOG_SS = """
    QDialog {
        background-color: #2d2d2d;
        color: white;
        border: 1px solid #555;
    }
    QLabel {
        color: #ccc;
        background: transparent;
    }
    QSpinBox {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px 4px;
    }
    QSpinBox::up-button, QSpinBox::down-button {
        background-color: #4a4a4a;
        border: none;
    }
    QPushButton {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 4px 12px;
        min-width: 60px;
    }
    QPushButton:hover   { background-color: #4a4a4a; border-color: #888; }
    QPushButton:pressed { background-color: #333; }
    QCalendarWidget QAbstractItemView {
        background-color: #2d2d2d;
        color: white;
        selection-background-color: #4a90d9;
        selection-color: white;
    }
    QCalendarWidget QWidget#qt_calendar_navigationbar {
        background-color: #3a3a3a;
    }
    QCalendarWidget QToolButton {
        color: white;
        background-color: transparent;
    }
    QCalendarWidget QMenu {
        background-color: #2d2d2d;
        color: white;
    }
    QDialogButtonBox QPushButton {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 4px 12px;
        min-width: 60px;
    }
    QDialogButtonBox QPushButton:hover { background-color: #4a4a4a; }
"""


# ══════════════════════════════════════════════════════════════════════════════
# ProxyDateTimeEdit
# ══════════════════════════════════════════════════════════════════════════════

class ProxyDateTimeEdit(_ProxyGlobalPosMixin, QDateTimeEdit):
    """
    ``QDateTimeEdit`` that works correctly inside a ``QGraphicsProxyWidget``.

    ``showPopup()`` opens a frameless dialog containing a
    ``QCalendarWidget`` for the date and three ``QSpinBox`` widgets for
    hours, minutes, and seconds.  The dialog is dismissed with OK /
    Cancel buttons or by pressing Escape.

    All ``QDateTimeEdit`` signals (``dateTimeChanged``, ``dateChanged``,
    ``timeChanged`` …) fire normally after a confirmed selection.

    Parameters
    ----------
    parent : QWidget, optional
        Standard Qt parent widget.
    show_seconds : bool
        Whether to include the seconds spinbox (default ``True``).
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        show_seconds: bool = True,
    ) -> None:
        super().__init__(parent)
        self._show_seconds = show_seconds
        self.setCalendarPopup(True)

    def showPopup(self) -> None:
        """Open a native date + time picker dialog."""
        dlg = QDialog(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        dlg.setStyleSheet(_DIALOG_SS)
        dlg.setWindowTitle("Pick date & time")
        dlg.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        root_layout = QVBoxLayout(dlg)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # ── Calendar ──────────────────────────────────────────────────
        cal = QCalendarWidget()
        cal.setGridVisible(True)
        cal.setSelectedDate(self.date())
        cal.setMinimumDate(self.minimumDate())
        cal.setMaximumDate(self.maximumDate())
        root_layout.addWidget(cal)

        # ── Time row ──────────────────────────────────────────────────
        form = QFormLayout()
        form.setSpacing(4)

        h_spin = QSpinBox()
        h_spin.setRange(0, 23)
        h_spin.setValue(self.time().hour())
        h_spin.setFixedWidth(52)

        m_spin = QSpinBox()
        m_spin.setRange(0, 59)
        m_spin.setValue(self.time().minute())
        m_spin.setFixedWidth(52)

        time_row = QHBoxLayout()
        time_row.addWidget(h_spin)
        time_row.addWidget(QLabel(":"))
        time_row.addWidget(m_spin)

        if self._show_seconds:
            s_spin = QSpinBox()
            s_spin.setRange(0, 59)
            s_spin.setValue(self.time().second())
            s_spin.setFixedWidth(52)
            time_row.addWidget(QLabel(":"))
            time_row.addWidget(s_spin)
        else:
            s_spin = None

        time_row.addStretch()
        label = "Time (H : M : S)" if self._show_seconds else "Time (H : M)"
        form.addRow(label, time_row)
        root_layout.addLayout(form)

        # ── Buttons ───────────────────────────────────────────────────
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        root_layout.addWidget(btn_box)

        dlg.adjustSize()
        dlg.move(self._global_popup_pos())

        if dlg.exec() == QDialog.DialogCode.Accepted:
            sec = s_spin.value() if s_spin else 0
            new_dt = QDateTime(
                cal.selectedDate(),
                QTime(h_spin.value(), m_spin.value(), sec),
            )
            new_dt = max(
                self.minimumDateTime(),
                min(self.maximumDateTime(), new_dt),
            )
            self.setDateTime(new_dt)


# ══════════════════════════════════════════════════════════════════════════════
# ProxyDateEdit
# ══════════════════════════════════════════════════════════════════════════════

class ProxyDateEdit(_ProxyGlobalPosMixin, QDateEdit):
    """
    ``QDateEdit`` that works correctly inside a ``QGraphicsProxyWidget``.

    ``showPopup()`` opens a frameless dialog with a ``QCalendarWidget``
    only — no time row.  Double-clicking a date or pressing Enter on the
    calendar accepts immediately without needing the OK button.

    All ``QDateEdit`` signals (``dateChanged`` …) fire normally after
    a confirmed selection.

    Parameters
    ----------
    parent : QWidget, optional
        Standard Qt parent widget.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCalendarPopup(True)

    def showPopup(self) -> None:
        """Open a native date picker dialog."""
        dlg = QDialog(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        dlg.setStyleSheet(_DIALOG_SS)
        dlg.setWindowTitle("Pick date")
        dlg.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        root_layout = QVBoxLayout(dlg)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # ── Calendar ──────────────────────────────────────────────────
        cal = QCalendarWidget()
        cal.setGridVisible(True)
        cal.setSelectedDate(self.date())
        cal.setMinimumDate(self.minimumDate())
        cal.setMaximumDate(self.maximumDate())
        # Double-click or Enter on a date accepts immediately
        cal.activated.connect(lambda _: dlg.accept())
        root_layout.addWidget(cal)

        # ── Buttons ───────────────────────────────────────────────────
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        root_layout.addWidget(btn_box)

        dlg.adjustSize()
        dlg.move(self._global_popup_pos())

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.setDate(cal.selectedDate())


# ══════════════════════════════════════════════════════════════════════════════
# ProxyTimeEdit
# ══════════════════════════════════════════════════════════════════════════════

class ProxyTimeEdit(_ProxyGlobalPosMixin, QTimeEdit):
    """
    ``QTimeEdit`` that works correctly inside a ``QGraphicsProxyWidget``.

    ``QTimeEdit`` does not normally call ``showPopup()`` — it uses arrow
    buttons like a spinbox — but overriding it provides a clean dialog
    if ``setCalendarPopup(True)`` is called, and guards against any
    future Qt version that routes clicks differently.

    All ``QTimeEdit`` signals (``timeChanged`` …) fire normally after
    a confirmed selection.

    Parameters
    ----------
    parent : QWidget, optional
        Standard Qt parent widget.
    show_seconds : bool
        Whether to include the seconds spinbox (default ``True``).
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        show_seconds: bool = True,
    ) -> None:
        super().__init__(parent)
        self._show_seconds = show_seconds

    def showPopup(self) -> None:
        """Open a native time picker dialog."""
        dlg = QDialog(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        dlg.setStyleSheet(_DIALOG_SS)
        dlg.setWindowTitle("Pick time")
        dlg.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        root_layout = QVBoxLayout(dlg)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        # ── Time spinboxes ────────────────────────────────────────────
        form = QFormLayout()
        form.setSpacing(6)

        h_spin = QSpinBox()
        h_spin.setRange(0, 23)
        h_spin.setValue(self.time().hour())
        h_spin.setFixedWidth(60)
        form.addRow("Hour:", h_spin)

        m_spin = QSpinBox()
        m_spin.setRange(0, 59)
        m_spin.setValue(self.time().minute())
        m_spin.setFixedWidth(60)
        form.addRow("Minute:", m_spin)

        if self._show_seconds:
            s_spin = QSpinBox()
            s_spin.setRange(0, 59)
            s_spin.setValue(self.time().second())
            s_spin.setFixedWidth(60)
            form.addRow("Second:", s_spin)
        else:
            s_spin = None

        root_layout.addLayout(form)

        # ── Buttons ───────────────────────────────────────────────────
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        root_layout.addWidget(btn_box)

        dlg.adjustSize()
        dlg.move(self._global_popup_pos())

        if dlg.exec() == QDialog.DialogCode.Accepted:
            sec = s_spin.value() if s_spin else 0
            self.setTime(QTime(h_spin.value(), m_spin.value(), sec))
