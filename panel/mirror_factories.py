# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

mirror_factories — Built-in widget cloners, signal map, and registry
=====================================================================

Contains the ``MirrorFactory`` type alias, all built-in ``_clone_*``
functions, the ordered ``_DEFAULT_FACTORIES`` dispatch list, and the
``_MIRROR_SIGNAL_MAP`` used for bidirectional sync inside ``NodePanel``.

Custom widget registration
--------------------------
Third-party or application-specific widget types can be registered
globally so that *every* ``NodePanel`` / ``NodeDockAdapter`` instance
can mirror them without per-panel factory registration::

    from weave.panel.mirror_factories import register_mirror_factory

    def clone_my_widget(src: MyWidget, binding: WidgetBinding) -> MyWidget:
        w = MyWidget()
        w.import_config(src.export_config())
        return w

    register_mirror_factory(
        MyWidget,
        clone_my_widget,
        signal_name="valueChanged",   # optional
    )

The global registry is consulted by ``NodePanel._create_mirror`` after
panel-local custom factories but *before* the built-in factories.

``WidgetCore`` also consults the global signal registry when auto-
detecting change signals for custom widgets (see ``_SIGNAL_MAP``
extension in ``widgetcore_adapter.py``).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget,
    QAbstractSpinBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QCheckBox, QAbstractSlider, QSlider,
    QLabel, QPushButton,
)

if TYPE_CHECKING:
    from weave.widgetcore_port_models import WidgetBinding

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
    w.setPrefix(src.prefix())
    w.setSuffix(src.suffix())
    w.setWrapping(src.wrapping())
    w.setValue(src.value())
    return w


def _clone_double_spinbox(src: QDoubleSpinBox, _binding) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setDecimals(src.decimals())
    w.setPrefix(src.prefix())
    w.setSuffix(src.suffix())
    w.setWrapping(src.wrapping())
    w.setValue(src.value())
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
    w.setTristate(src.isTristate())
    w.setChecked(src.isChecked())
    return w


def _clone_slider(src: QSlider, _binding) -> QSlider:
    w = QSlider(src.orientation())
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setPageStep(src.pageStep())
    w.setTickPosition(src.tickPosition())
    w.setTickInterval(src.tickInterval())
    w.setValue(src.value())
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
# Dispatch tables (built-in)
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


# ══════════════════════════════════════════════════════════════════════════════
# Global custom-widget registry
# ══════════════════════════════════════════════════════════════════════════════

_CUSTOM_FACTORIES: Dict[type, MirrorFactory] = {}
"""Exact-type → factory mapping for globally registered custom widgets."""

_CUSTOM_SIGNAL_MAP: Dict[type, str] = {}
"""Exact-type → signal-name mapping for globally registered custom widgets."""


def register_mirror_factory(
    widget_type: type,
    factory: MirrorFactory,
    *,
    signal_name: Optional[str] = None,
) -> None:
    """Register a global mirror-widget factory for *widget_type*.

    Parameters
    ----------
    widget_type : type
        The concrete QWidget subclass this factory handles.
    factory : MirrorFactory
        ``(original_widget, binding) -> QWidget``.  Must return a new
        widget whose value is initialised from *original_widget*.
    signal_name : str, optional
        The name of the Qt signal on the widget that fires when the
        user edits the value (e.g. ``"valueChanged"``).  If provided,
        it is added to the global signal map so that both ``WidgetCore``
        and ``NodePanel`` can auto-detect the change signal without the
        node author passing ``change_signal_name`` explicitly.
    """
    _CUSTOM_FACTORIES[widget_type] = factory
    if signal_name is not None:
        _CUSTOM_SIGNAL_MAP[widget_type] = signal_name


def unregister_mirror_factory(widget_type: type) -> None:
    """Remove a previously registered global factory."""
    _CUSTOM_FACTORIES.pop(widget_type, None)
    _CUSTOM_SIGNAL_MAP.pop(widget_type, None)


def get_custom_factory(widget_type: type) -> Optional[MirrorFactory]:
    """Look up a globally registered factory by exact type."""
    return _CUSTOM_FACTORIES.get(widget_type)


def get_custom_signal_name(widget_type: type) -> Optional[str]:
    """Look up a globally registered signal name by exact type."""
    return _CUSTOM_SIGNAL_MAP.get(widget_type)
