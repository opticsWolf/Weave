# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

simplenodes.py
-----------------
Concrete implementations of various node types, optimized for reliability and performance.
Includes lifecycle management and signal safety.

Example usage of auto-disable functionality:
To enable widget auto-disabling on input ports, set `_auto_disable = True` 
on the port object after creating it:

    self.add_input("my_input", "float")
    self.inputs[-1]._auto_disable = True  # Enable auto disable for this port

When another node connects to this input, and that source node has a content widget,
the widget in the source node will be automatically disabled.
"""

import numpy as np
from typing import Any, Dict, Optional, ClassVar, List
from PySide6.QtCore import Signal, Slot, QObject, QTimer
from PySide6.QtWidgets import (
    QSpinBox, QLineEdit, QLabel, QWidget, QVBoxLayout, QFormLayout,
    QComboBox, QDoubleSpinBox, QPushButton
)

# Import the WidgetCore for new node implementations
from weave.widgetcore import WidgetCore, PortRole

from weave.basenode import ActiveNode, ManualNode
from weave.noderegistry import register_node


from weave.logger import get_logger
log = get_logger("SimpleNodes")


# ------------------------------------------------------------------------------
# Concrete Implementations
# ------------------------------------------------------------------------------

@register_node
class FloatNode(ActiveNode):
    """
    A generic source node generating a float value.
    
    Type: Active (Updates downstream immediately on UI change).
    Complexity: O(1)
    """
    # Class-level signal for attribute safety in C++ bindings
    value_changed = Signal(float)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Float Generator"
    node_description: ClassVar[Optional[str]] = "Generates float values"
    node_tags: ClassVar[Optional[List[str]]] = ["float", "number", "input"]

    def __init__(self, title: str = "Float Gen", val: float = 0.0, **kwargs: Any) -> None:
        """
        Args:
            title (str): The node title.
            val (float): Initial value.
        """
        super().__init__(
            title=title, **kwargs
        )
        
        self.add_input("factor", "float")  # Optional modifier
        
        # Build core with WidgetCore
        # NOTE: Must be stored as _widget_core so BaseControlNode.get_state() /
        #       restore_state() can find it for serialisation.
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        spin = QDoubleSpinBox()
        
        # Register the widget with the core
        self._widget_core.register_widget(
            "value", spin,
            role="output", datatype="float", default=0.0,
        )
        
        # Auto-create ports from registered widgets
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's value_changed signal to our handler
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        self._cached_values["value"] = val

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            if port_name == "value":
                self.on_ui_change()
                self.value_changed.emit(self._widget_core.get_port_value("value"))
        except Exception as e:
            log.error(f"Exception in FloatNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Calculates output based on spinner value and optional upstream factor."""
        try:
            factor = inputs.get("factor", 1.0)
            base_val = self._widget_core.get_port_value("value")
            
            try:
                result = base_val * float(factor)
            except (ValueError, TypeError):
                result = base_val
            
            return {"value": result}
        except Exception as e:
            log.error(f"Exception in FloatNode.compute: {e}")
            return {"value": 0.0}

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class DisplayNode(ActiveNode):
    """
    Observer node that passively displays data.
    
    Type: Active (Reacts to upstream pushes).
    Optimization: Updates UI only when evaluation finalizes (on_evaluate_finished).
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Output"
    node_name: ClassVar[Optional[str]] = "Display Value"
    node_description: ClassVar[Optional[str]] = "Displays incoming data"
    node_tags: ClassVar[Optional[List[str]]] = ["display", "output", "observer"]

    def __init__(self, title: str = "Observer", **kwargs: Any) -> None:
        super().__init__(
            title=title, **kwargs
        )
        self.add_input("data", "any")
        
        # Create core with a display widget
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        label = QLabel("Waiting...")
        label.setMinimumWidth(80)
        
        # Register the label as DISPLAY role (no port created, but state is persisted)
        self._widget_core.register_widget(
            "display", label,
            role="display", datatype="string", default="No Data",
        )
        
        self.set_content_widget(self._widget_core)
        # Ensure all children (including unregistered ones) receive the initial
        # palette now that the widget tree is fully constructed.
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()
            
        self._temp_display_data: str = "No Data"

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Captures data without triggering immediate UI redraws."""
        try:
            raw_val = inputs.get("data")
            self._temp_display_data = str(raw_val) if raw_val is not None else "None"
        except Exception as e:
            log.error(f"Exception in DisplayNode.compute: {e}")
            self._temp_display_data = "Error"
        
        return {}

    def on_evaluate_finished(self) -> None:
        """Safe place to update UI after graph evaluation."""
        try:
            super().on_evaluate_finished()
            try:
                self._widget_core.set_port_value("display", f"RX: {self._temp_display_data}")
            except RuntimeError:
                # Wrapped C/C++ object has been deleted
                pass
        except Exception as e:
            log.error(f"Exception in DisplayNode.on_evaluate_finished: {e}")

    def cleanup(self) -> None:
        """Teardown."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class ActionNode(ManualNode):
    """
    Terminal node for side-effects.
    
    Type: Manual (Requires explicit 'Execute' trigger).
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Action"
    node_name: ClassVar[Optional[str]] = "Action Trigger"
    node_description: ClassVar[Optional[str]] = "Executes side effects"
    node_tags: ClassVar[Optional[List[str]]] = ["action", "execute", "command"]

    def __init__(self, title: str = "Action", **kwargs: Any) -> None:
        super().__init__(
            title=title, **kwargs
        )
        
        self.add_input("data", "any")
        self.add_output("status", "string")

        # Create core with button widget
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        btn = QPushButton("Execute")
        btn.clicked.connect(self.execute)
        
        # Register the button as INTERNAL — it's an action trigger, not a port value
        self._widget_core.register_widget(
            "trigger", btn,
            role="internal", datatype="string", default="",
        )
        
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Performs the side effect."""
        try:
            data_val = inputs.get("data", "No Data")
            log.info(f"ACTION TRIGGERED | Payload: {data_val}")
            return {"status": "Success"}
        except Exception as e:
            log.error(f"Exception in ActionNode.compute: {e}")
            return {"status": f"Error: {e}"}

    def cleanup(self) -> None:
        """Teardown."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class IntNode(ActiveNode):
    """
    Integer source node with spinbox.
    
    Type: Active (Updates downstream immediately on UI change).
    Complexity: O(1)
    """
    value_changed = Signal(int)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Integer Generator"
    node_description: ClassVar[Optional[str]] = "Generates integer values"
    node_tags: ClassVar[Optional[List[str]]] = ["int", "number", "input"]

    def __init__(self, title: str = "Int Gen", val: int = 0, **kwargs: Any) -> None:
        """
        Args:
            title (str): The node title.
            val (int): Initial value.
        """
        super().__init__(title=title, **kwargs)
        
        self.add_input("factor", "int")  # Optional modifier
        
        # Build core with WidgetCore
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        spin = QSpinBox()
        
        # Register the widget with the core
        self._widget_core.register_widget(
            "value", spin,
            role="output", datatype="int", default=0,
        )
        
        # Auto-create ports from registered widgets
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's value_changed signal to our handler
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()
        
        self._cached_values["value"] = val

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            if port_name == "value":
                self.on_ui_change()
                self.value_changed.emit(self._widget_core.get_port_value("value"))
        except Exception as e:
            log.error(f"Exception in IntNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Calculates output based on spinner value and optional upstream factor."""
        try:
            factor = inputs.get("factor", 1)
            base_val = self._widget_core.get_port_value("value")
            
            try:
                result = base_val * int(factor)
            except (ValueError, TypeError):
                result = base_val
            
            return {"value": result}
        except Exception as e:
            log.error(f"Exception in IntNode.compute: {e}")
            return {"value": 0}

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class RangeListNode(ActiveNode):
    """
    List generator node using numpy.arange.
    
    Generates numerical sequences with configurable start, stop, and step values.
    Type: Active (Updates downstream on parameter change).
    """
    list_changed = Signal(list)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Generator"
    node_name: ClassVar[Optional[str]] = "Range List Generator"
    node_description: ClassVar[Optional[str]] = "Creates numerical lists"
    node_tags: ClassVar[Optional[List[str]]] = ["list", "range", "generator"]

    def __init__(self, title: str = "Range List", **kwargs: Any) -> None:
        """Creates a list generator with start, stop, and step parameters."""
        super().__init__(title=title, **kwargs)
        
        self.add_input("start", "float")
        self.add_input("stop", "float")  
        self.add_input("step", "float")
        self.add_output("list", "list")
        
        # Use a QFormLayout so labels and spinboxes sit on the same row, keeping
        # the node body compact.  Pass it directly to the WidgetCore constructor.
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)
        
        spin_start = QSpinBox()
        spin_start.setRange(-9999, 9999)
        spin_start.setValue(0)
        
        spin_stop = QSpinBox()
        spin_stop.setRange(-9999, 9999)
        spin_stop.setValue(10)
        
        spin_step = QSpinBox()
        spin_step.setRange(1, 9999)   # Step must be ≥ 1; prevents zero-step infinite loops
        spin_step.setValue(1)
        
        # Add labelled rows directly to the form layout before registration so
        # the WidgetCore doesn't try to add them a second time.
        form.addRow("Start:", spin_start)
        form.addRow("Stop:", spin_stop)
        form.addRow("Step:", spin_step)
        
        # Register each spinbox with add_to_layout=False because we already
        # placed them in the form layout above.
        self._widget_core.register_widget(
            "start", spin_start,
            role="bidirectional", datatype="float", default=0.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            "stop", spin_stop,
            role="bidirectional", datatype="float", default=10.0,
            add_to_layout=False,
        )
        self._widget_core.register_widget(
            "step", spin_step,
            role="bidirectional", datatype="float", default=1.0,
            add_to_layout=False,
        )
        
        # A single connection to WidgetCore.value_changed covers all three
        # spinboxes — no need for individual valueChanged slots.
        self._widget_core.value_changed.connect(self._on_core_changed)
        
        self.set_content_widget(self._widget_core)
        # refresh_widget_palettes() is called explicitly here because the QFormLayout
        # row labels ("Start:", "Stop:", "Step:") are created by Qt internally when
        # addRow() is called, after WidgetCore.__init__ has already run its initial
        # _apply_container_background().  Without this call those labels will not
        # receive the theme palette until the next StyleManager theme-change event.
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in RangeListNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a list using numpy.arange; prefers connected inputs over UI values."""
        try:
            # Use upstream value if port is connected, else fall back to the widget
            start = inputs.get("start")
            stop  = inputs.get("stop")
            step  = inputs.get("step")
            
            if start is None:
                start = self._widget_core.get_port_value("start")
            if stop is None:
                stop  = self._widget_core.get_port_value("stop")
            if step is None:
                step  = self._widget_core.get_port_value("step")
            
            start = float(start) if start is not None else 0.0
            stop  = float(stop)  if stop  is not None else 10.0
            step  = float(step)  if step  is not None else 1.0
            
            if step == 0:
                step = 1.0
            
            result_list = np.arange(start, stop, step).tolist()
            self.list_changed.emit(result_list)
            return {"list": result_list}
            
        except Exception as e:
            log.error(f"Exception in RangeListNode.compute: {e}")
            return {"list": []}

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class ListLengthNode(ActiveNode):
    """
    Utility node that returns the length of a list.
    
    Type: Active (Updates when upstream list changes).
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Utility"
    node_name: ClassVar[Optional[str]] = "List Length Calculator"
    node_description: ClassVar[Optional[str]] = "Calculates list size"
    node_tags: ClassVar[Optional[List[str]]] = ["utility", "length", "list"]

    def __init__(self, title: str = "List Length", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
        
        self.add_input("list", "list")
        self.add_output("length", "int")
        
        # Create core with label widget
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        label = QLabel("Length: 0")
        
        # Register the label as display-only (no port created)
        self._widget_core.register_widget(
            "length_display", label,
            role="display", datatype="string", default="Length: 0",
        )
        
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Computes the length of the input list."""
        try:
            input_list = inputs.get("list", [])
            
            if isinstance(input_list, (list, tuple, np.ndarray)):
                length = len(input_list)
            else:
                length = 0
            
            return {"length": length}
        except Exception as e:
            log.error(f"Exception in ListLengthNode.compute: {e}")
            return {"length": 0}

    def on_evaluate_finished(self) -> None:
        """Updates the UI label with the computed length."""
        try:
            super().on_evaluate_finished()
            
            # Use get_output_value() so we unwrap the CacheEntry correctly
            length = self.get_output_value("length") or 0
            self._widget_core.set_port_value("length_display", f"Length: {length}")
        except Exception as e:
            log.error(f"Exception in ListLengthNode.on_evaluate_finished: {e}")

    def cleanup(self) -> None:
        """Teardown."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class ListIndexNode(ActiveNode):
    """
    Extracts an element from a list by index.
    
    Type: Active (Updates when upstream list or index changes).
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Utility"
    node_name: ClassVar[Optional[str]] = "List Index Extractor"
    node_description: ClassVar[Optional[str]] = "Extracts element by index"
    node_tags: ClassVar[Optional[List[str]]] = ["utility", "index", "list"]

    def __init__(self, title: str = "List Index", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
        
        self.add_input("list", "list")
        self.add_input("index", "int")
        # Enable auto-disable on the index input port so that when an upstream
        # node drives it, the spinbox is greyed out automatically.
        self.inputs[-1]._auto_disable = True
        
        self.add_output("element", "any")
        
        # Use a QFormLayout for a compact label + spinbox row
        form = QFormLayout()
        form.setContentsMargins(5, 5, 5, 5)
        form.setSpacing(4)
        self._widget_core = WidgetCore(layout=form)
        self._widget_core.set_node(self)
        
        spin_index = QSpinBox()
        spin_index.setRange(-9999, 9999)
        spin_index.setValue(0)
        
        form.addRow("Index:", spin_index)
        
        # Register with add_to_layout=False — already in the form above
        self._widget_core.register_widget(
            "index", spin_index,
            role="bidirectional", datatype="int", default=0,
            add_to_layout=False,
        )
        
        # WidgetCore.value_changed handles the signal; no manual connection needed
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        # QFormLayout row label ("Index:") is an unregistered child created after
        # WidgetCore.__init__ — refresh ensures it gets the theme palette immediately.
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in ListIndexNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts element at specified index."""
        try:
            input_list = inputs.get("list", [])
            
            # Prefer a connected upstream index; fall back to the spinbox
            index = inputs.get("index")
            if index is None:
                index = self._widget_core.get_port_value("index")
                
            index = int(index) if index is not None else 0
            
            if isinstance(input_list, (list, tuple, np.ndarray)) and len(input_list) > 0:
                if -len(input_list) <= index < len(input_list):
                    element = input_list[index]
                else:
                    element = None
            else:
                element = None
            
            return {"element": element}
        except Exception as e:
            log.error(f"Exception in ListIndexNode.compute: {e}")
            return {"element": None}

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        self._widget_core.cleanup()
        super().cleanup()


@register_node
class AutoDisableDemoNode(ActiveNode):
    """
    Demonstration node showing how auto-disable works.
    
    This node shows the auto-disable functionality in action:
    1. It creates an input port with `_auto_disable = True`
    2. When connected to another node, that source node's widget gets disabled
    
    Example usage:
        # In your custom nodes, enable auto-disabling like this:
        self.add_input("input_with_auto_disable", "float")
        self.inputs[-1]._auto_disable = True
    """
    
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Utility"
    node_name: ClassVar[Optional[str]] = "Auto-Disable Demo"
    node_description: ClassVar[Optional[str]] = "Demonstrates auto-disable feature"
    node_tags: ClassVar[Optional[List[str]]] = ["demo", "feature", "auto-disable"]

    def __init__(self, title: str = "Auto-Disable Demo", **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
        
        # This input port has auto-disable enabled
        self.add_input("auto_disable_source", "float")
        self.inputs[-1]._auto_disable = True
        
        # Regular input (no auto-disable)
        self.add_input("regular_input", "int")
        
        self.add_output("result", "float")
        
        # Create core with label widget
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        label = QLabel("Auto-Disable Demo Node")
        
        # Register the label as display-only (no port created)
        self._widget_core.register_widget(
            "demo_label", label,
            role="display", datatype="string", default="Auto-Disable Demo Node",
        )
        
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Example computation that uses both input types."""
        try:
            auto_input    = inputs.get("auto_disable_source", 0.0)
            regular_input = inputs.get("regular_input", 1)
            
            result = (auto_input or 0.0) * (regular_input or 1)
            return {"result": result}
        except Exception as e:
            log.error(f"Exception in AutoDisableDemoNode.compute: {e}")
            return {"result": 0.0}

    def cleanup(self) -> None:
        """Teardown."""
        self._widget_core.cleanup()
        super().cleanup()


class SimpleInputNode(ActiveNode):
    """
    Simple node that demonstrates the auto-disabling behavior.
    
    When connected to an input port with `_auto_disable = True`, 
    this widget will be automatically disabled during evaluation cycles.
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Simple Input Node"
    node_description: ClassVar[Optional[str]] = "Basic input node for demo"
    node_tags: ClassVar[Optional[List[str]]] = ["demo", "input", "example"]

    def __init__(self, title: str = "Demo Input Node", value: float = 5.0, **kwargs: Any) -> None:
        super().__init__(title=title, **kwargs)
        
        # Build core with WidgetCore
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)
        
        spin = QDoubleSpinBox()
        
        # Register the widget with the core
        self._widget_core.register_widget(
            "output_value", spin,
            role="output", datatype="float", default=5.0,
        )
        
        # Auto-create ports from registered widgets
        for pd in self._widget_core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's value_changed signal to our handler
        self._widget_core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()
            
        self._cached_values["output_value"] = value

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            if port_name == "output_value":
                self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in SimpleInputNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Simple computation that returns stored value."""
        try:
            return {"output_value": self._widget_core.get_port_value("output_value")}
        except Exception as e:
            log.error(f"Exception in SimpleInputNode.compute: {e}")
            return {"output_value": 0.0}

    def cleanup(self) -> None:
        """Teardown."""
        self._widget_core.cleanup()
        super().cleanup()