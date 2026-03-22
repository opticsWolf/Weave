# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

widgets._adapter — Strategy for reading/writing standard Qt widgets
and auto-detecting their change signals.

New widget types can be supported by updating ``_IO_REGISTRY`` and
``_SIGNAL_MAP`` — no changes to WidgetCore required.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Type

from PySide6.QtWidgets import (
    QWidget,
    QAbstractSpinBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QCheckBox, QAbstractSlider, QSlider,
    QLabel,
)

from weave.logger import get_logger
from weave.panel.mirror_factories import get_custom_signal_name as _get_custom_signal_name

from .widgetcore_port_models import WidgetBinding

log = get_logger("WidgetAdapter")


# ══════════════════════════════════════════════════════════════════════════════
# I/O Registry — maps widget types to (getter_method, setter_method) names
# ══════════════════════════════════════════════════════════════════════════════

# Each key is a tuple of types; value is (getter_attr, setter_attr).
# Order matters — first match wins.
_IO_REGISTRY: list[tuple[tuple[type, ...], str, str]] = [
    ((QDoubleSpinBox,),               "value",       "setValue"),
    ((QSpinBox, QAbstractSlider),     "value",       "setValue"),
    ((QLineEdit, QLabel),             "text",        "setText"),
    ((QCheckBox,),                    "isChecked",   "setChecked"),
    ((QTextEdit, QPlainTextEdit),     "toPlainText", "setPlainText"),
]


def generic_get(widget: QWidget, default: Any = None) -> Any:
    """Read a value from a standard Qt widget.

    Resolution order:
        1. QComboBox special handling (currentData → currentText).
        2. ``_IO_REGISTRY`` lookup.
        3. Duck-typed ``.value()`` fallback.
        4. *default*.
    """
    if isinstance(widget, QComboBox):
        data = widget.currentData()
        return data if data is not None else widget.currentText()

    for types, getter, _ in _IO_REGISTRY:
        if isinstance(widget, types):
            return getattr(widget, getter)()

    if hasattr(widget, "value") and callable(widget.value):
        return widget.value()
    return default


def generic_set(
    widget: QWidget,
    value: Any,
    block_signals: bool = True,
) -> None:
    """Write a value to a standard Qt widget.

    Parameters
    ----------
    widget : QWidget
        Target widget.
    value : Any
        Value to write.
    block_signals : bool
        If True (default), the widget's own signals are blocked during
        the write.  Pass False when the native signal must fire (e.g.
        undo/redo restoring a value whose side-effects rebuild ports).
    """
    was_blocked = widget.signalsBlocked()
    if block_signals:
        widget.blockSignals(True)
    try:
        if isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value) if value is not None else 0.0)
        elif isinstance(widget, (QSpinBox, QAbstractSlider)):
            widget.setValue(int(value) if value is not None else 0)
        elif isinstance(widget, QComboBox):
            if isinstance(value, int):
                widget.setCurrentIndex(value)
            else:
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
                elif widget.isEditable():
                    widget.setEditText(str(value))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, (QLineEdit, QLabel)):
            widget.setText(str(value) if value is not None else "")
        elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
            widget.setPlainText(str(value) if value is not None else "")
        elif hasattr(widget, "setValue") and callable(widget.setValue):
            widget.setValue(value)
    finally:
        if block_signals:
            widget.blockSignals(was_blocked)


# ══════════════════════════════════════════════════════════════════════════════
# Signal auto-detection
# ══════════════════════════════════════════════════════════════════════════════

_SIGNAL_MAP: Dict[type, str] = {
    QDoubleSpinBox: "valueChanged",
    QSpinBox:       "valueChanged",
    QComboBox:      "currentIndexChanged",
    QCheckBox:      "stateChanged",
    QSlider:        "valueChanged",
    QLineEdit:      "textChanged",
    QTextEdit:      "textChanged",
    QPlainTextEdit: "textChanged",
}


def resolve_signal_name(widget: QWidget, explicit: Optional[str] = None) -> Optional[str]:
    """Return the change-signal name for *widget*.

    Resolution order:
        1. *explicit* (caller override).
        2. Built-in ``_SIGNAL_MAP``.
        3. Custom-widget map from ``mirror_factories``.
    """
    if explicit is not None:
        return explicit

    for cls, name in _SIGNAL_MAP.items():
        if isinstance(widget, cls):
            return name

    return _get_custom_signal_name(type(widget))


def connect_change_signal(
    binding: WidgetBinding,
    callback: callable,
) -> None:
    """Auto-detect and connect the widget's change signal to *callback*.

    The resolved signal name and slot reference are stored on the binding
    for later disconnection.
    """
    sig_name = resolve_signal_name(binding.widget, binding.change_signal_name)
    if sig_name is None:
        return

    try:
        sig = getattr(binding.widget, sig_name)
        if not callable(sig):
            return

        binding._connected_signal = sig_name
        binding._slot_ref = callback
        sig.connect(callback)
    except Exception as e:
        log.warning(f"Failed to connect signal for widget '{binding.port_name}': {e}")


def disconnect_change_signal(binding: WidgetBinding) -> None:
    """Disconnect the previously connected change signal."""
    if binding._connected_signal is None:
        return
    try:
        sig = getattr(binding.widget, binding._connected_signal)
        if binding._slot_ref is not None:
            try:
                sig.disconnect(binding._slot_ref)
            except (RuntimeError, TypeError):
                pass
    except Exception as e:
        log.warning(f"Failed to disconnect signal for widget '{binding.port_name}': {e}")

    binding._connected_signal = None
    binding._slot_ref = None
