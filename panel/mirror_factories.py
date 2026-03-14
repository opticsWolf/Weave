# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

mirror_factories — Built-in widget cloners and signal map
==========================================================

Contains the ``MirrorFactory`` type alias, all built-in ``_clone_*``
functions, the ordered ``_DEFAULT_FACTORIES`` dispatch list, and the
``_MIRROR_SIGNAL_MAP`` used for bidirectional sync inside ``NodePanel``.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple, TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget,
    QAbstractSpinBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QCheckBox, QAbstractSlider, QSlider,
    QLabel, QPushButton,
)

if TYPE_CHECKING:
    from weave.widgetcore import WidgetBinding

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

MirrorFactory = Callable[["QWidget", "WidgetBinding"], QWidget]


# ══════════════════════════════════════════════════════════════════════════════
# Built-in cloner functions
# ══════════════════════════════════════════════════════════════════════════════

def _clone_spinbox(src: QSpinBox, _binding) -> QSpinBox:
    w = QSpinBox()
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setValue(src.value())
    w.setPrefix(src.prefix())
    w.setSuffix(src.suffix())
    w.setWrapping(src.wrapping())
    return w


def _clone_double_spinbox(src: QDoubleSpinBox, _binding) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setDecimals(src.decimals())
    w.setValue(src.value())
    w.setPrefix(src.prefix())
    w.setSuffix(src.suffix())
    w.setWrapping(src.wrapping())
    return w


def _clone_combobox(src: QComboBox, _binding) -> QComboBox:
    w = QComboBox()
    w.setEditable(src.isEditable())
    for i in range(src.count()):
        data = src.itemData(i)
        if data is not None:
            w.addItem(src.itemText(i), data)
        else:
            w.addItem(src.itemText(i))
    w.setCurrentIndex(src.currentIndex())
    return w


def _clone_checkbox(src: QCheckBox, _binding) -> QCheckBox:
    w = QCheckBox(src.text())
    w.setChecked(src.isChecked())
    w.setTristate(src.isTristate())
    return w


def _clone_slider(src: QSlider, _binding) -> QSlider:
    w = QSlider(src.orientation())
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setPageStep(src.pageStep())
    w.setValue(src.value())
    w.setTickPosition(src.tickPosition())
    w.setTickInterval(src.tickInterval())
    return w


def _clone_lineedit(src: QLineEdit, _binding) -> QLineEdit:
    w = QLineEdit(src.text())
    w.setPlaceholderText(src.placeholderText())
    w.setMaxLength(src.maxLength())
    w.setReadOnly(src.isReadOnly())
    return w


def _clone_textedit(src: QTextEdit, _binding) -> QTextEdit:
    w = QTextEdit()
    w.setPlainText(src.toPlainText())
    w.setReadOnly(src.isReadOnly())
    return w


def _clone_plaintextedit(src: QPlainTextEdit, _binding) -> QPlainTextEdit:
    w = QPlainTextEdit()
    w.setPlainText(src.toPlainText())
    w.setReadOnly(src.isReadOnly())
    return w


def _clone_label(src: QLabel, _binding) -> QLabel:
    w = QLabel(src.text())
    w.setWordWrap(src.wordWrap())
    w.setAlignment(src.alignment())
    return w


def _clone_pushbutton(src: QPushButton, _binding) -> QPushButton:
    w = QPushButton(src.text())
    w.setCheckable(src.isCheckable())
    w.setChecked(src.isChecked())
    return w


# ══════════════════════════════════════════════════════════════════════════════
# Dispatch tables
# ══════════════════════════════════════════════════════════════════════════════

# Ordered so subclasses are matched before base classes.
_DEFAULT_FACTORIES: List[Tuple[type, MirrorFactory]] = [
    (QDoubleSpinBox, _clone_double_spinbox),
    (QSpinBox,       _clone_spinbox),
    (QComboBox,      _clone_combobox),
    (QCheckBox,      _clone_checkbox),
    (QSlider,        _clone_slider),
    (QLineEdit,      _clone_lineedit),
    (QTextEdit,      _clone_textedit),
    (QPlainTextEdit, _clone_plaintextedit),
    (QLabel,         _clone_label),
    (QPushButton,    _clone_pushbutton),
]

# Signal names used to detect changes on mirror widgets.
_MIRROR_SIGNAL_MAP: Dict[type, str] = {
    QDoubleSpinBox: "valueChanged",
    QSpinBox:       "valueChanged",
    QComboBox:      "currentIndexChanged",
    QCheckBox:      "stateChanged",
    QSlider:        "valueChanged",
    QLineEdit:      "textChanged",
    QTextEdit:      "textChanged",
    QPlainTextEdit: "textChanged",
}
