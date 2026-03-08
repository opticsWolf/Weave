# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

proxyfontwidgets.py
--------------------
Proxy-safe wrapper for ``QFontComboBox``, whose popup mechanism is
broken inside ``QGraphicsProxyWidget``.

Provided classes
----------------
``ProxyFontComboBox``
    ``QFontComboBox`` replacement.  ``showPopup()`` opens a resizable
    ``QDialog`` with a live-filter ``QLineEdit``, a ``QListWidget``
    of all available font families, and a live preview label.

Root cause
----------
Qt's ``QFontComboBox.showPopup()`` inherits from ``QComboBox`` and
creates a ``QFrame`` sub-proxy for the dropdown list, which is clipped
to the host proxy's bounding rect.  Overriding ``showPopup()`` with a
standalone ``QDialog`` bypasses the sub-proxy pipeline entirely.

WidgetCore compatibility
------------------------
``ProxyFontComboBox`` is a ``QFontComboBox`` which is a ``QComboBox``,
so ``WidgetCore._generic_get`` returns ``currentText()`` (the font
family name string) automatically.  No custom getter / setter needed.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QLabel, QDialogButtonBox,
    QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
    QFontComboBox,
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
    QLineEdit {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px 4px;
    }
    QListWidget {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555;
        outline: none;
    }
    QListWidget::item:selected {
        background-color: #4a90d9;
        color: white;
    }
    QListWidget::item:hover {
        background-color: #444;
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
# ProxyFontComboBox
# ══════════════════════════════════════════════════════════════════════════════

class ProxyFontComboBox(_ProxyGlobalPosMixin, QFontComboBox):
    """
    ``QFontComboBox`` that works correctly inside a ``QGraphicsProxyWidget``.

    Because a font database typically contains hundreds of families, a
    plain ``QMenu`` would be unwieldy.  ``showPopup()`` instead opens a
    resizable dialog with:

    - A ``QLineEdit`` filter that narrows the list as you type.
    - A ``QListWidget`` showing all matching font families.
    - A live preview label rendered in the selected font.

    All standard ``QComboBox`` / ``QFontComboBox`` signals fire normally
    after a confirmed selection because the commit is performed via
    ``setCurrentText()``, which keeps Qt's internal model in sync.

    WidgetCore note
    ---------------
    ``ProxyFontComboBox`` is a ``QFontComboBox`` → ``QComboBox`` subclass,
    so ``WidgetCore._generic_get`` returns ``currentText()`` (the font
    family name) automatically.  No custom getter / setter needed.

    Parameters
    ----------
    parent : QWidget, optional
        Standard Qt parent widget.
    preview_text : str
        Sample text shown in the live preview label.
    """

    _DEFAULT_PREVIEW = "The quick brown fox jumps over the lazy dog"

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        preview_text: str = _DEFAULT_PREVIEW,
    ) -> None:
        super().__init__(parent)
        self._preview_text = preview_text

    def showPopup(self) -> None:
        """Open a searchable, resizable font picker dialog."""
        all_families = QFontDatabase.families()
        current_family = self.currentText()

        dlg = QDialog(None, Qt.WindowType.Tool)
        dlg.setStyleSheet(_DIALOG_SS)
        dlg.setWindowTitle("Select font")
        dlg.resize(320, 440)

        root_layout = QVBoxLayout(dlg)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # ── Search field ──────────────────────────────────────────────
        search = QLineEdit()
        search.setPlaceholderText("Filter fonts…")
        search.setClearButtonEnabled(True)
        root_layout.addWidget(search)

        # ── Font list ─────────────────────────────────────────────────
        lst = QListWidget()
        lst.addItems(all_families)
        lst.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # Scroll to and pre-select the current font family
        matches = lst.findItems(current_family, Qt.MatchFlag.MatchExactly)
        if matches:
            lst.setCurrentItem(matches[0])
            lst.scrollToItem(matches[0], QListWidget.ScrollHint.PositionAtCenter)

        root_layout.addWidget(lst)

        # ── Live preview label ────────────────────────────────────────
        preview = QLabel(self._preview_text)
        preview.setWordWrap(True)
        preview.setMinimumHeight(36)
        preview.setStyleSheet(
            "QLabel { color: #ddd; background: #222; "
            "border: 1px solid #444; padding: 4px; }"
        )
        if current_family:
            preview.setFont(QFont(current_family, 11))
        root_layout.addWidget(preview)

        # ── Buttons ───────────────────────────────────────────────────
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        root_layout.addWidget(btn_box)

        # ── Live filter ───────────────────────────────────────────────
        def _on_filter(text: str) -> None:
            text = text.strip().lower()
            first_visible = None
            for i in range(lst.count()):
                item = lst.item(i)
                hidden = bool(text) and text not in item.text().lower()
                item.setHidden(hidden)
                if not hidden and first_visible is None:
                    first_visible = item
            # Auto-select the first visible match while filtering
            if text and first_visible is not None:
                lst.setCurrentItem(first_visible)

        search.textChanged.connect(_on_filter)

        # ── Live preview update ───────────────────────────────────────
        def _on_select(item: QListWidgetItem) -> None:
            if item:
                preview.setFont(QFont(item.text(), 11))

        lst.currentItemChanged.connect(_on_select)

        # Double-click confirms immediately
        lst.itemDoubleClicked.connect(lambda _: dlg.accept())

        dlg.move(self._global_popup_pos())

        if dlg.exec() == QDialog.DialogCode.Accepted:
            item = lst.currentItem()
            if item and not item.isHidden():
                # setCurrentText keeps the QFontComboBox model in sync
                # and fires currentTextChanged / currentIndexChanged normally
                self.setCurrentText(item.text())
