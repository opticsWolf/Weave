# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

panel_header — Compact header widget for NodePanel
====================================================

Provides ``PanelHeader``: a slim ``QWidget`` strip that displays the
bound node's title, a persistent state badge beneath it, and an
optional *pin* toggle for dynamic panels.

Pin button
----------
The pin button is a small checkable ``QPushButton`` shown only for
**dynamic** panels (hidden for static panels and when no node is
bound).  When checked the panel is "perma-linked" to the current
node — canvas selection changes are ignored until the user unchecks
the button or the node is deleted.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)


# ══════════════════════════════════════════════════════════════════════════════
# PanelHeader
# ══════════════════════════════════════════════════════════════════════════════

class PanelHeader(QWidget):
    """Compact header showing the node title, state badge, and pin toggle."""

    # Emitted when the user clicks the pin button.  The argument is
    # True when the button is now *checked* (pinned).
    pin_toggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 4)
        outer.setSpacing(2)

        # ── Top row: title + pin button ──────────────────────────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self._title_label = QLabel()
        font = self._title_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self._title_label.setFont(font)
        top_row.addWidget(self._title_label, stretch=1)

        self._pin_btn = QPushButton()
        self._pin_btn.setCheckable(True)
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.setToolTip("Pin this panel to the current node")
        self._pin_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 14px; }"
            "QPushButton:checked { background: rgba(255,255,255,30); "
            "border-radius: 4px; }"
        )
        self._update_pin_icon(False)
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        self._pin_btn.hide()
        top_row.addWidget(self._pin_btn)

        outer.addLayout(top_row)

        # ── Bottom row: state badge ──────────────────────────────────
        self._state_label = QLabel()
        font_s = self._state_label.font()
        font_s.setPointSize(font_s.pointSize() - 1)
        self._state_label.setFont(font_s)
        self._state_label.setStyleSheet("color: grey;")
        outer.addWidget(self._state_label)

    # ──────────────────────────────────────────────────────────────────────
    # Mutators
    # ──────────────────────────────────────────────────────────────────────

    def set_title(self, text: str) -> None:
        self._title_label.setText(text)

    def set_state_text(self, text: str) -> None:
        self._state_label.setText(text)

    # ── Pin button ───────────────────────────────────────────────────────

    def set_pin_visible(self, visible: bool) -> None:
        """Show or hide the pin button (hidden for static panels)."""
        self._pin_btn.setVisible(visible)

    def set_pin_checked(self, checked: bool) -> None:
        """Programmatically set the pin state without emitting pin_toggled."""
        was_blocked = self._pin_btn.signalsBlocked()
        self._pin_btn.blockSignals(True)
        self._pin_btn.setChecked(checked)
        self._update_pin_icon(checked)
        self._pin_btn.blockSignals(was_blocked)

    # ── Private ──────────────────────────────────────────────────────────

    def _on_pin_toggled(self, checked: bool) -> None:
        self._update_pin_icon(checked)
        self.pin_toggled.emit(checked)

    def _update_pin_icon(self, pinned: bool) -> None:
        # Using simple unicode glyphs — works on all platforms.
        if pinned:
            self._pin_btn.setText("\U0001F517")   # 🔗
            self._pin_btn.setToolTip("Unpin — resume following selection")
        else:
            self._pin_btn.setText("\U0001F513")   # 🔓
            self._pin_btn.setToolTip("Pin this panel to the current node")
