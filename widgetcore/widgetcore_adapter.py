# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.

WidgetAdapter: PySide6 Type Firewall.
Ensures strict type casting before passing data to C++ Qt methods.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Type, List, Callable

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

# Rule 1 & 4: Order matters — first match wins.
# QAbstractSlider covers QSlider, QScrollBar, and QDial.
# QAbstractSpinBox is used as a fallback, but specific types handle specialized casting.
_IO_REGISTRY: List[Tuple[Tuple[Type[QWidget], ...], str, str]] = [
    ((QDoubleSpinBox,), 'value', 'setValue'),
    ((QSpinBox, QAbstractSlider, QSlider), 'value', 'setValue'),
    ((QLineEdit, QLabel), 'text', 'setText'),
    ((QTextEdit, QPlainTextEdit), 'toPlainText', 'setPlainText'),
    ((QComboBox,), 'currentText', 'setCurrentText'),
    ((QCheckBox,), 'isChecked', 'setChecked'),
    ((QAbstractSpinBox,), 'value', 'setValue'), 
]


def generic_get(widget: QWidget, default: Any = None) -> Any:
    """Read a value from a standard Qt widget with fallback support."""
    if isinstance(widget, QComboBox):
        data = widget.currentData()
        return data if data is not None else widget.currentText()

    for types, getter, _ in _IO_REGISTRY:
        if isinstance(widget, types):
            func = getattr(widget, getter, None)
            return func() if func else default

    if hasattr(widget, "value") and callable(widget.value):
        return widget.value()
    return default


def generic_set(
    widget: QWidget,
    value: Any,
    block_signals: bool = True,
) -> None:
    """
    Rule 3 & 4: Type Firewall for PySide6 C++ boundary safety.
    Explicitly casts loosely-typed values to C++ primitives to prevent crashes.
    """
    was_blocked = widget.signalsBlocked()
    if block_signals:
        widget.blockSignals(True)
        
    try:
        # Rule 0: Zero-tolerance for uncast floats to Int-based widgets
        if isinstance(widget, (QSpinBox, QAbstractSlider, QSlider)):
            # Double-cast (float then int) handles string versions of floats like "1.0"
            widget.setValue(int(float(value)) if value is not None else 0)
            
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value) if value is not None else 0.0)
            
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
            if isinstance(value, str):
                widget.setChecked(value.lower() in ('true', '1', 'yes', 'on'))
            else:
                widget.setChecked(bool(value))
                
        elif isinstance(widget, (QLineEdit, QLabel)):
            widget.setText(str(value) if value is not None else "")
            
        elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
            widget.setPlainText(str(value) if value is not None else "")
            
        elif hasattr(widget, "setValue") and callable(widget.setValue):
            widget.setValue(value)
            
    except Exception as e:
        log.error(f"PySide6 Type Cast Error on {type(widget).__name__}: {e}")
    finally:
        if block_signals:
            widget.blockSignals(was_blocked)


# ══════════════════════════════════════════════════════════════════════════════
# Signal auto-detection
# ══════════════════════════════════════════════════════════════════════════════

# Standardized signal mappings.
# QCheckBox uses 'toggled' to provide the boolean state directly to the callback.
_SIGNAL_MAP: Dict[Type[QWidget], str] = {
    QDoubleSpinBox: "valueChanged",
    QSpinBox:       "valueChanged",
    QAbstractSpinBox: "editingFinished", # Fallback for complex spinboxes
    QComboBox:      "currentTextChanged",
    QCheckBox:      "toggled",
    QSlider:        "valueChanged",
    QAbstractSlider: "valueChanged",
    QLineEdit:      "textChanged",
    QTextEdit:      "textChanged",
    QPlainTextEdit: "textChanged",
}


def resolve_signal_name(widget: QWidget, explicit: Optional[str] = None) -> Optional[str]:
    """Return the change-signal name for *widget*."""
    if explicit is not None:
        return explicit

    # Linear scan is safe for the small number of standard types; 
    # MRO order is respected by checking specific types before base types.
    for cls, name in _SIGNAL_MAP.items():
        if isinstance(widget, cls):
            return name

    return _get_custom_signal_name(type(widget))


def connect_change_signal(
    binding: WidgetBinding,
    callback: Callable,
) -> None:
    """Rule 7: Connect and track signal for explicit lifecycle management."""
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
    """Rule 7: Aggressively sever connection to allow GC reclamation."""
    if binding._connected_signal is None:
        return
    try:
        sig = getattr(binding.widget, binding._connected_signal)
        if binding._slot_ref is not None:
            try:
                sig.disconnect(binding._slot_ref)
            except (RuntimeError, TypeError):
                # Handle cases where C++ object or connection is already gone
                pass
    except Exception as e:
        log.warning(f"Failed to disconnect signal for widget '{binding.port_name}': {e}")

    binding._connected_signal = None
    binding._slot_ref = None