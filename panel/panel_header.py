# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

panel_header — Compact header widget for NodePanel
====================================================

Provides ``_PanelHeader``: a slim ``QWidget`` strip that displays the
bound node's title, an optional state badge, and an *Unlink* button
that lets the user disconnect the panel from its node at runtime.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


# ══════════════════════════════════════════════════════════════════════════════
# _PanelHeader
# ══════════════════════════════════════════════════════════════════════════════

class PanelHeader(QWidget):
    """Compact header showing the node title, state badge, and unlink button."""

    unlink_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self._title_label = QLabel()
        font = self._title_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self._title_label.setFont(font)
        layout.addWidget(self._title_label, stretch=1)

        self._state_label = QLabel()
        self._state_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        font_s = self._state_label.font()
        font_s.setPointSize(font_s.pointSize() - 1)
        self._state_label.setFont(font_s)
        layout.addWidget(self._state_label)

        self._unlink_btn = QPushButton("Unlink")
        self._unlink_btn.setFixedWidth(52)
        self._unlink_btn.setToolTip("Disconnect this panel from the node")
        self._unlink_btn.clicked.connect(self.unlink_clicked)
        self._unlink_btn.hide()
        layout.addWidget(self._unlink_btn)

    # ──────────────────────────────────────────────────────────────────────
    # Mutators
    # ──────────────────────────────────────────────────────────────────────

    def set_title(self, text: str) -> None:
        self._title_label.setText(text)

    def set_state_text(self, text: str) -> None:
        self._state_label.setText(text)

    def set_unlink_visible(self, visible: bool) -> None:
        self._unlink_btn.setVisible(visible)
