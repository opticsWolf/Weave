# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

dropdown_node.py
-------------------
Dropdown/ComboBox node for selecting from a list of items.

Uses ``ProxyComboBox`` — a thin QComboBox subclass that overrides only
``showPopup()`` to use ``QMenu.exec()`` instead of Qt's broken internal
sub-proxy mechanism.  This means:

  • WidgetCore handles it natively (QComboBox is in _generic_get /
    _generic_set / _SIGNAL_MAP) — no custom getter/setter needed.
  • All standard QComboBox signals (currentIndexChanged, currentTextChanged,
    activated …) fire normally.
  • The popup reliably appears above the scene regardless of pan/zoom.

Root cause of QComboBox inside QGraphicsProxyWidget:
    Qt's showPopup() creates a QFrame sub-proxy for the item list.
    That sub-proxy is constrained to the bounding rect of the host proxy,
    so the list is clipped, hidden, or rendered in the wrong position.
    Overriding showPopup() with QMenu.exec() uses a fully independent
    native window, which Qt handles correctly in all cases.

Important — one widget, one registration:
    Each QWidget instance must only be registered with WidgetCore once.
    Additional outputs derived from the same widget belong in compute().
"""

from typing import Any, Dict, List, ClassVar, Optional

from PySide6.QtCore import Signal, Slot, QPointF
from PySide6.QtWidgets import QComboBox, QFormLayout, QMenu

from weave.basenode import ActiveNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore, PortRole
from weave.widgets import ProxyComboBox

# ══════════════════════════════════════════════════════════════════════════════
# Dropdown Nodes
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class DropdownNode(ActiveNode):
    """
    Dropdown selection node.

    Allows selection from a predefined list of items.
    Type: Active (updates downstream on selection change).

    Outputs:
        selected (string): The text of the currently selected item.
        index    (int):    Index of the currently selected item
                           (derived in compute).
    """

    selection_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"

    def __init__(
        self,
        title: str = "Dropdown",
        items: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        if items is None:
            items = ["Apple", "Banana", "Cherry", "Date",
                     "Elderberry", "Fig", "Grape"]
        self._items = items

        # ── WidgetCore setup ─────────────────────────────────────────
        self._widget_core = WidgetCore()

        self.combo = ProxyComboBox()
        self.combo.addItems(items)
        self.combo.setMinimumWidth(120)

        # ProxyComboBox IS a QComboBox, so WidgetCore handles get/set/signal
        # natively — no custom getter/setter/change_signal_name needed.
        self._widget_core.register_widget(
            "selected",
            self.combo,
            role=PortRole.OUTPUT,
            datatype="string",
            default=items[0] if items else "",
            description="Currently selected item text",
        )

        self._widget_core.value_changed.connect(self._on_core_value_changed)

        # ── Port creation ────────────────────────────────────────────
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype)
        self.add_output("index", "int")

        # ── Embed in node body ───────────────────────────────────────
        self.set_content_widget(self._widget_core)
        self._widget_core.set_node(self)

    # ── Callbacks ─────────────────────────────────────────────────────

    @Slot(str)
    def _on_core_value_changed(self, port_name: str) -> None:
        try:
            self.on_ui_change()
            if port_name == "selected":
                self.selection_changed.emit(
                    self._widget_core.get_port_value("selected")
                )
        except Exception as e:
            print(f"[ERROR] DropdownNode._on_core_value_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            selected = self._widget_core.get_port_value("selected")
            index = self.combo.currentIndex() if self.combo else -1
            return {
                "selected": selected if selected is not None else "",
                "index": index,
            }
        except Exception as e:
            print(f"[ERROR] DropdownNode.compute: {e}")
            return {"selected": "", "index": -1}

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self) -> None:
        try:
            if hasattr(self, "_widget_core") and self._widget_core is not None:
                self._widget_core.value_changed.disconnect(
                    self._on_core_value_changed
                )
        except (RuntimeError, TypeError):
            pass
        try:
            self.selection_changed.disconnect()
        except (RuntimeError, AttributeError):
            pass

        if hasattr(self, "_widget_core") and self._widget_core is not None:
            self._widget_core.cleanup()
            self._widget_core.deleteLater()
            self._widget_core = None

        self.combo = None
        super().cleanup()


@register_node
class DropdownMultiOutputNode(ActiveNode):
    """
    Enhanced dropdown node with mapped values.

    Provides the selected text, a mapped value (for enum-like behavior),
    and the selection index.
    Type: Active (updates downstream on selection change).

    Outputs:
        selected (string): Currently selected item text.
        value    (any):    Mapped value from the active preset
                           (derived in compute).
        index    (int):    Index of the currently selected item
                           (derived in compute).
    """

    selection_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"

    PRESET_MAPPINGS: ClassVar[Dict[str, Dict[str, Any]]] = {
        "Fruits": {
            "Apple": 1.0, "Banana": 2.0, "Cherry": 3.0,
            "Date": 4.0, "Elderberry": 5.0,
        },
        "Colors": {
            "Red": "#FF0000", "Green": "#00FF00", "Blue": "#0000FF",
            "Yellow": "#FFFF00", "Magenta": "#FF00FF", "Cyan": "#00FFFF",
        },
        "Sizes": {
            "Small": 0.5, "Medium": 1.0,
            "Large": 2.0, "Extra Large": 3.0,
        },
        "Boolean": {
            "True": True, "False": False,
        },
    }

    def __init__(
        self,
        title: str = "Dropdown (Mapped)",
        preset: str = "Fruits",
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        self.mapping = self.PRESET_MAPPINGS.get(
            preset, self.PRESET_MAPPINGS["Fruits"]
        )

        # ── WidgetCore setup ─────────────────────────────────────────
        # QFormLayout keeps each label + combo on one row without needing
        # separate label widgets added outside of registration.
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)

        # --- Preset selector ---
        self.combo_preset = ProxyComboBox()
        self.combo_preset.addItems(list(self.PRESET_MAPPINGS.keys()))
        self.combo_preset.setCurrentText(preset)

        form.addRow("Preset:", self.combo_preset)
        self._widget_core.register_widget(
            "preset",
            self.combo_preset,
            role=PortRole.INTERNAL,
            datatype="string",
            default="Fruits",
            description="Active preset mapping",
            add_to_layout=False,  # already placed in the form above
        )

        # --- Item selector ---
        self.combo_item = ProxyComboBox()
        self.combo_item.addItems(list(self.mapping.keys()))

        form.addRow("Selection:", self.combo_item)
        self._widget_core.register_widget(
            "selected",
            self.combo_item,
            role=PortRole.OUTPUT,
            datatype="string",
            default="",
            description="Currently selected item text",
            add_to_layout=False,  # already placed in the form above
        )

        # ── Signals ──────────────────────────────────────────────────
        self._widget_core.value_changed.connect(self._on_core_value_changed)
        # Direct connection so the item list is rebuilt before value_changed
        # fires and triggers compute().
        self.combo_preset.currentTextChanged.connect(self._on_preset_changed)

        # ── Port creation ────────────────────────────────────────────
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype)
        self.add_output("value", "any")
        self.add_output("index", "int")

        # ── Embed in node body ───────────────────────────────────────
        self.set_content_widget(self._widget_core)
        self._widget_core.set_node(self)

    # ── Callbacks ─────────────────────────────────────────────────────

    @Slot(str)
    def _on_preset_changed(self, preset: str) -> None:
        """Rebuild the item combo when the preset selection changes."""
        try:
            self.mapping = self.PRESET_MAPPINGS.get(
                preset, self.PRESET_MAPPINGS["Fruits"]
            )
            self.combo_item.blockSignals(True)
            self.combo_item.clear()
            self.combo_item.addItems(list(self.mapping.keys()))
            self.combo_item.blockSignals(False)

            self.on_ui_change()
        except Exception as e:
            print(f"[ERROR] DropdownMultiOutputNode._on_preset_changed: {e}")

    @Slot(str)
    def _on_core_value_changed(self, port_name: str) -> None:
        try:
            self.on_ui_change()
            if port_name == "selected":
                self.selection_changed.emit(
                    self._widget_core.get_port_value("selected")
                )
        except Exception as e:
            print(f"[ERROR] DropdownMultiOutputNode._on_core_value_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            selected = self._widget_core.get_port_value("selected")
            selected = selected if selected is not None else ""
            value = self.mapping.get(selected)
            index = self.combo_item.currentIndex() if self.combo_item else -1
            return {
                "selected": selected,
                "value": value,
                "index": index,
            }
        except Exception as e:
            print(f"[ERROR] DropdownMultiOutputNode.compute: {e}")
            return {"selected": "", "value": None, "index": -1}

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self) -> None:
        try:
            if hasattr(self, "combo_preset") and self.combo_preset:
                self.combo_preset.currentTextChanged.disconnect(
                    self._on_preset_changed
                )
        except (RuntimeError, TypeError):
            pass
        try:
            if hasattr(self, "_widget_core") and self._widget_core is not None:
                self._widget_core.value_changed.disconnect(
                    self._on_core_value_changed
                )
        except (RuntimeError, TypeError):
            pass
        try:
            self.selection_changed.disconnect()
        except (RuntimeError, AttributeError):
            pass

        if hasattr(self, "_widget_core") and self._widget_core is not None:
            self._widget_core.cleanup()
            self._widget_core.deleteLater()
            self._widget_core = None

        self.combo_preset = None
        self.combo_item = None
        super().cleanup()