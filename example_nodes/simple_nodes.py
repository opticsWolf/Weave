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
from PySide6.QtCore import Signal, Slot, QObject
from PySide6.QtWidgets import (
    QSpinBox, QLineEdit, QLabel, QWidget, QVBoxLayout, QComboBox, QDoubleSpinBox, QPushButton, QTextEdit
)

# Import the WidgetCore for new node implementations
from weave.widgetcore import WidgetCore, PortRole

from weave.basenode import ActiveNode, ManualNode
from weave.noderegistry import register_node


from weave.logger import get_logger
log = get_logger("SimpleNodes")

# ------------------------------------------------------------------------------
# Mock Registry & Base Imports (Adapt if your file structure differs)
# ------------------------------------------------------------------------------

# Simple decorator if qt_noderegistry is missing


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
        
        self.add_input("factor", "float") # Optional modifier
        
        # Build core with WidgetCore 
        self._core = WidgetCore()
        
        spin = QDoubleSpinBox()
        spin.setRange(-9999.0, 9999.0)
        spin.setValue(val)
        
        # Register the widget with the core
        self._core.register_widget(
            "value", spin,
            role="output", datatype="float", default=0.0,
        )
        
        # Auto-create ports from registered widgets 
        for pd in self._core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's signal to our handler
        self._core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._core)
        
        self._cached_values["value"] = val

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            # Only handle value changes (since that's our only registered widget)
            if port_name == "value":
                self.on_ui_change()
                self.value_changed.emit(self._core.get_port_value("value"))
        except Exception as e:
            log.error(f"Exception in FloatNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Calculates output based on spinner value and optional upstream factor."""
        try:
            factor = inputs.get("factor", 1.0)
            
            # SAFETY CHECK: Widget existence
            base_val = self._core.get_port_value("value")
            
            # Safe float conversion
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
        # Cleanup the WidgetCore
        self._core.cleanup()
        
        # Call parent cleanup  
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
        self._core = WidgetCore()
        
        self.label = QLabel("Waiting...")
        self.label.setStyleSheet("QLabel { color: #ddd; font-weight: bold; }")
        self.label.setMinimumWidth(80)
        
        # Register the label as a DISPLAY role (no port created, but state persistence)
        self._core.register_widget(
            "display", self.label,
            role="display", datatype="string", default="No Data",
        )
        
        self.set_content_widget(self._core)
            
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
            
            # SAFETY CHECK: Ensure widget is alive before setText
            if hasattr(self, 'label') and self.label:
                try:
                    self._core.set_port_value("display", f"RX: {self._temp_display_data}")
                except RuntimeError:
                    # Wrapped C/C++ object has been deleted
                    pass
        except Exception as e:
            log.error(f"Exception in DisplayNode.on_evaluate_finished: {e}")

    def cleanup(self) -> None:
        """Teardown."""
        self._core.cleanup()
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
        self._core = WidgetCore()
        
        btn = QPushButton("Execute")
        btn.clicked.connect(self.execute)
        
        # Register the button (but don't create port since it's an action)
        self._core.register_widget(
            "trigger", btn,
            role="internal", datatype="string", default="",
        )
        
        self.set_content_widget(self._core)

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Performs the side effect."""
        try:
            data_val = inputs.get("data", "No Data")
            
            # Use getattr to avoid crash if 'title' attribute missing (edge case)
            title = getattr(self, 'title', 'ActionNode')
            
            log.info(f"ACTION TRIGGERED | Payload: {data_val}")
            return {"status": "Success"}
        except Exception as e:
            log.error(f"Exception in ActionNode.compute: {e}")
            return {"status": f"Error: {e}"}

    def cleanup(self) -> None:
        """Teardown."""
        self._core.cleanup()
        super().cleanup()


@register_node
class TextEditNode(ActiveNode):
    """
    Real-time logging node using efficient O(1) appends.
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Output"
    node_name: ClassVar[Optional[str]] = "Text Logger"
    node_description: ClassVar[Optional[str]] = "Logs text to editor"
    node_tags: ClassVar[Optional[List[str]]] = ["log", "text", "editor", "output"]

    def __init__(self, title: str = "Logger", **kwargs: Any) -> None:
        super().__init__(
            title=title, **kwargs
        )
        self.add_input("append", "string")
        
        # Create core with text editor
        self._core = WidgetCore()
        
        self.editor = QTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setMinimumSize(150, 100)
        
        # Register the editor widget
        self._core.register_widget(
            "text", self.editor,
            role="bidirectional", datatype="string", default="",
        )
        
        # Auto-create ports from registered widgets
        for pd in self._core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
            if pd.role in (PortRole.INPUT, PortRole.BIDIRECTIONAL):
                self.add_input(pd.name, pd.datatype, pd.description)
        
        # Connect core signal
        self._core.value_changed.connect(self.on_ui_change)
        
        self.set_content_widget(self._core)
            
        self._pending_append: Optional[str] = None

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Captures input string for the next UI update cycle."""
        try:
            # Get current text from core
            if hasattr(self, 'editor') and self.editor:
                try:
                    # Return current state for downstream nodes - get from core  
                    return {"text": self._core.get_port_value("text")}
                except RuntimeError:
                    return {"text": ""}
            else:
                return {"text": ""}
        except Exception as e:
            log.error(f"Exception in TextEditNode.compute: {e}")
            return {"text": ""}

    def on_evaluate_finished(self) -> None:
        """Updates the text editor efficiently."""
        try:
            super().on_evaluate_finished()
            
            # Check conditions:
            # 1. We have data to append
            # 2. The editor widget still exists
            # 3. The C++ object underlying the widget is valid
            if self._pending_append is not None:
                try:
                    self.editor.append(str(self._pending_append))
                    self._pending_append = None # Clear buffer
                    
                    # Update core with new text value 
                    self._core.set_port_value("text", self.editor.toPlainText())
                except RuntimeError:
                    pass # Object deleted
        except Exception as e:
            log.error(f"Exception in TextEditNode.on_evaluate_finished: {e}")

    def cleanup(self) -> None:
        """Teardown."""
        self._pending_append = None
        
        self._core.cleanup()
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
        self._core = WidgetCore()
        
        spin = QSpinBox()
        spin.setRange(-9999, 9999)
        spin.setValue(val)
        
        # Register the widget with the core
        self._core.register_widget(
            "value", spin,
            role="output", datatype="int", default=0,
        )
        
        # Auto-create ports from registered widgets 
        for pd in self._core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's signal to our handler
        self._core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._core)
        
        self._cached_values["value"] = val

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            # Only handle value changes (since that's our only registered widget)
            if port_name == "value":
                self.on_ui_change()
                self.value_changed.emit(self._core.get_port_value("value"))
        except Exception as e:
            log.error(f"Exception in IntNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Calculates output based on spinner value and optional upstream factor."""
        try:
            factor = inputs.get("factor", 1)
            
            base_val = self._core.get_port_value("value")
            
            # Safe int conversion
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
        # Cleanup the WidgetCore
        self._core.cleanup()
        
        # Call parent cleanup  
        super().cleanup()


@register_node
class TextInputNode(ActiveNode):
    """
    Text input node with QLineEdit.
    
    Type: Active (Updates downstream on text change or enter press).
    """
    text_changed = Signal(str)

    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Input"
    node_name: ClassVar[Optional[str]] = "Text Input"
    node_description: ClassVar[Optional[str]] = "Accepts string input"
    node_tags: ClassVar[Optional[List[str]]] = ["input", "text", "string"]

    def __init__(self, title: str = "Text Input", initial_text: str = "", **kwargs: Any) -> None:
        """
        Args:
            title (str): The node title.
            initial_text (str): Initial text value.
        """
        super().__init__(title=title, **kwargs)
        
        # Build core with WidgetCore 
        self._core = WidgetCore()
        
        line_edit = QLineEdit()
        line_edit.setText(initial_text)
        line_edit.setPlaceholderText("Enter text...")
        line_edit.setMinimumWidth(120)
        
        # Register the widget with the core
        self._core.register_widget(
            "text", line_edit,
            role="output", datatype="string", default="",
        )
        
        # Auto-create ports from registered widgets 
        for pd in self._core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's signal to our handler
        self._core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._core)
            
        self._cached_values["text"] = initial_text

    @Slot(str)
    def _on_core_changed(self, port_name: str) -> None:
        """Propagates text changes to the graph logic."""
        try:
            # Only handle text changes (since that's our only registered widget)
            if port_name == "text":
                self.on_ui_change()
                self.text_changed.emit(self._core.get_port_value("text"))
        except Exception as e:
            log.error(f"Exception in TextInputNode._on_core_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the current text value."""
        try:
            text = self._core.get_port_value("text")
            return {"text": text}
        except Exception as e:
            log.error(f"Exception in TextInputNode.compute: {e}")
            return {"text": ""}

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        # Cleanup the WidgetCore
        self._core.cleanup()
        
        # Call parent cleanup  
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
        """
        Creates a list generator with start, stop, and step parameters.
        """
        super().__init__(title=title, **kwargs)
        
        self.add_input("start", "float")
        self.add_input("stop", "float")  
        self.add_input("step", "float")
        self.add_output("list", "list")
        
        # Build core with WidgetCore 
        self._core = WidgetCore()
        
        # Container widget for layout
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # Start value spinbox
        label_start = QLabel("Start:")
        spin_start = QSpinBox()
        spin_start.setRange(-9999, 9999)
        spin_start.setValue(0)
        
        # Stop value spinbox  
        label_stop = QLabel("Stop:")
        spin_stop = QSpinBox()
        spin_stop.setRange(-9999, 9999)
        spin_stop.setValue(10)
        
        # Step value spinbox
        label_step = QLabel("Step:")
        spin_step = QSpinBox()
        spin_step.setRange(-9999, 9999)
        spin_step.setValue(1)
        spin_step.setMinimum(1)  # Prevent zero step
        
        # Add widgets to layout
        layout.addWidget(label_start)
        layout.addWidget(spin_start)
        layout.addWidget(label_stop)
        layout.addWidget(spin_stop)
        layout.addWidget(label_step)
        layout.addWidget(spin_step)
        
        # Register the container widget (will handle all spinboxes via core)
        self._core.register_widget(
            "range_controls", container,
            role="internal", datatype="string", default="",
        )
        
        # Also register individual spinbox widgets for direct access
        self._core.register_widget(
            "start", spin_start,
            role="bidirectional", datatype="float", default=0.0,
        )
        self._core.register_widget(
            "stop", spin_stop,
            role="bidirectional", datatype="float", default=10.0,
        )
        self._core.register_widget(
            "step", spin_step,
            role="bidirectional", datatype="float", default=1.0,
        )
        
        # Connect signals to our handler
        spin_start.valueChanged.connect(self._on_spin_changed)
        spin_stop.valueChanged.connect(self._on_spin_changed)  
        spin_step.valueChanged.connect(self._on_spin_changed)
        
        self.set_content_widget(self._core)

    @Slot(int)
    def _on_spin_changed(self, val: int) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in RangeListNode._on_spin_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a list using numpy.arange with input values or UI defaults."""
        try:
            # Use input values if connected, otherwise use UI spinboxes
            start = inputs.get("start")
            stop = inputs.get("stop") 
            step = inputs.get("step")
            
            # Fallback to core widget values
            if start is None:
                start = self._core.get_port_value("start")
            if stop is None:  
                stop = self._core.get_port_value("stop")
            if step is None:
                step = self._core.get_port_value("step")
            
            # Safety defaults
            start = float(start) if start is not None else 0.0
            stop = float(stop) if stop is not None else 10.0
            step = float(step) if step is not None else 1.0
            
            # Prevent infinite loops
            if step == 0:
                step = 1.0
            
            # Generate list using numpy
            result_array = np.arange(start, stop, step)
            result_list = result_array.tolist()
            
            self.list_changed.emit(result_list)
            return {"list": result_list}
            
        except Exception as e:
            log.error(f"Exception in RangeListNode.compute: {e}")
            return {"list": []}

    def cleanup(self) -> None:
        """Safe teardown of widgets and signals."""
        # Cleanup the WidgetCore
        self._core.cleanup()
        
        # Call parent cleanup  
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
        self._core = WidgetCore()
        
        label = QLabel("Length: 0")
        label.setStyleSheet("QLabel { color: #ddd; }")
        
        # Register the label as display-only (no port created)
        self._core.register_widget(
            "length_display", label,
            role="display", datatype="string", default="Length: 0",
        )
        
        self.set_content_widget(self._core)

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
            
            length = self._cached_values.get("length", 0)
            # Update via core
            self._core.set_port_value("length_display", f"Length: {length}")
        except Exception as e:
            log.error(f"Exception in ListLengthNode.on_evaluate_finished: {e}")

    def cleanup(self) -> None:
        """Teardown."""
        self._core.cleanup()
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
        # Enable auto-disable on the index input port
        self.inputs[-1]._auto_disable = True
        
        self.add_output("element", "any")
        
        # Build core with WidgetCore 
        self._core = WidgetCore()
        
        # Container widget for layout  
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        
        label_index = QLabel("Index:")
        spin_index = QSpinBox()
        spin_index.setRange(-9999, 9999)
        spin_index.setValue(0)
        
        # Add widgets to layout
        layout.addWidget(label_index)
        layout.addWidget(spin_index)
        
        # Register the container widget (will handle the spinbox via core)
        self._core.register_widget(
            "index_control", container,
            role="internal", datatype="string", default="",
        )
        
        # Also register the spinbox for direct access
        self._core.register_widget(
            "index", spin_index,
            role="bidirectional", datatype="int", default=0,
        )
        
        # Connect signal to handler  
        spin_index.valueChanged.connect(self._on_spin_changed)
        self.set_content_widget(self._core)

    @Slot(int)
    def _on_spin_changed(self, val: int) -> None:
        """Propagates UI changes to the graph logic."""
        try:
            self.on_ui_change()
        except Exception as e:
            log.error(f"Exception in ListIndexNode._on_spin_changed: {e}")

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts element at specified index."""
        try:
            input_list = inputs.get("list", [])
            
            # Get the index - use connected value if available
            index = inputs.get("index")
            if index is None:
                index = self._core.get_port_value("index") 
                
            index = int(index) if index is not None else 0
            
            # Safe indexing  
            if isinstance(input_list, (list, tuple, np.ndarray)) and len(input_list) > 0:
                # Handle negative indices
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
        # Cleanup the WidgetCore
        self._core.cleanup()
        
        # Call parent cleanup  
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
        self.inputs[-1]._auto_disable = True  # Enable auto-disabling
        
        # Regular input (no auto-disable)
        self.add_input("regular_input", "int")  
        
        self.add_output("result", "float")
        
        # Create core with label widget
        self._core = WidgetCore()
        
        label = QLabel("Auto-Disable Demo Node")
        label.setStyleSheet("QLabel { color: #ddd; font-weight: bold; }")
        
        # Register the label as display-only (no port created)
        self._core.register_widget(
            "demo_label", label,
            role="display", datatype="string", default="Auto-Disable Demo Node",
        )
        
        self.set_content_widget(self._core)

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Example computation that uses both input types."""
        try:
            auto_input = inputs.get("auto_disable_source", 0.0)
            regular_input = inputs.get("regular_input", 1)
            
            result = auto_input * regular_input
            
            return {"result": result}
        except Exception as e:
            log.error(f"Exception in AutoDisableDemoNode.compute: {e}")
            return {"result": 0.0}

    def cleanup(self) -> None:
        """Teardown."""
        self._core.cleanup()
        super().cleanup()


# Additional helper classes for demonstration purposes
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
        self._core = WidgetCore()
        
        spin = QDoubleSpinBox()
        spin.setValue(value)
        spin.setRange(-100.0, 100.0)
        
        # Register the widget with the core
        self._core.register_widget(
            "output_value", spin,
            role="output", datatype="float", default=5.0,
        )
        
        # Auto-create ports from registered widgets 
        for pd in self._core.get_port_definitions():
            if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                self.add_output(pd.name, pd.datatype, pd.description)
        
        # Connect the core's signal to our handler
        self._core.value_changed.connect(self._on_core_changed)
        self.set_content_widget(self._core)
            
        # Store the initial value
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
            return {"output_value": self._core.get_port_value("output_value")}
        except Exception as e:
            log.error(f"Exception in SimpleInputNode.compute: {e}")
            return {"output_value": 0.0}

    def cleanup(self) -> None:
        """Teardown."""
        # Cleanup the WidgetCore
        self._core.cleanup()
        
        # Call parent cleanup  
        super().cleanup()