# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Improved NodeDataFlow with robust state-aware data propagation.

Key improvements:
1. request_data() properly handles DISABLED nodes
2. Last valid values preserved before disabling
3. Configurable downstream behavior (use_cached, use_none, use_default)
4. Better passthrough that properly pulls from upstream
5. State change notifications propagate with reason
6. Atomic cache updates to prevent partial state
"""

import sys
import uuid
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Final, ClassVar, Callable
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal, QTimer, QObject
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView
from PySide6.QtGui import QColor, QPainter

# Assuming these exist in your project structure
from weave.node.node_core import Node
from weave.node.node_subcomponents import NodeState

from weave.logger import get_logger
log = get_logger("NodeDataFlow")


class DisabledBehavior(Enum):
    """Defines what downstream nodes receive when this node is disabled."""
    USE_LAST_VALID = auto()  # Return the last successfully computed value
    USE_NONE = auto()        # Return None for all outputs
    USE_DEFAULT = auto()     # Return port-specific default values
    PROPAGATE_DISABLED = auto()  # Signal downstream that data is unavailable


@dataclass
class CacheEntry:
    """Represents a cached value with metadata."""
    value: Any
    is_valid: bool = True
    timestamp: float = 0.0  # For potential staleness checks
    source_state: Optional[NodeState] = None  # State when this was computed


class NodeDataFlow:
    """Mixin to handle logic state, dirty-flag propagation, and lazy evaluation.

    Attributes:
        _is_dirty (bool): Indicates if the cache is invalid.
        _is_computing (bool): Lock to prevent infinite recursion during eval.
        _cached_values (Dict[str, CacheEntry]): Stores computed results with metadata.
        _last_valid_values (Dict[str, Any]): Preserved values from before DISABLED.
        _manual_mode (bool): If True, auto-propagation stops at this node.
        _disabled_behavior (DisabledBehavior): What to return when disabled.
        _port_defaults (Dict[str, Any]): Default values per output port.
    """

    def __init__(self) -> None:
        """Initialize the dataflow state."""
        self._is_dirty: bool = True
        self._is_computing: bool = False
        self._cached_values: Dict[str, CacheEntry] = {}
        self._last_valid_values: Dict[str, Any] = {}  # NEW: Preserved before disable
        self._manual_mode: bool = False
        self._disabled_behavior: DisabledBehavior = DisabledBehavior.USE_NONE
        self._port_defaults: Dict[str, Any] = {}  # Configurable defaults per port
        self._state: NodeState = NodeState.NORMAL  # Ensure state is always defined

    def set_port_default(self, port_name: str, default_value: Any) -> None:
        """Set a default value for an output port (used when disabled with USE_DEFAULT)."""
        self._port_defaults[port_name] = default_value

    def set_disabled_behavior(self, behavior: DisabledBehavior) -> None:
        """Configure what downstream nodes receive when this node is disabled."""
        self._disabled_behavior = behavior

    def set_dirty(self, reason: str = "value_change") -> None:
        """Recursively marks this node and all downstream nodes as dirty.

        Args:
            reason: Why the node is being marked dirty (for debugging/logging).
        
        Optimization:
            Stops recursion immediately if the node is already dirty.
        """
        if self._is_dirty:
            return

        self._is_dirty = True
        self._mark_downstream_dirty(reason)

    def _mark_downstream_dirty(self, reason: str = "upstream_change") -> None:
        """Mark all downstream nodes as dirty with a reason.
        
        Separated from set_dirty() so it can be called independently.
        """
        outputs = getattr(self, 'outputs', [])
        for port in outputs:
            traces = getattr(port, 'connected_traces', [])
            for trace in traces:
                if trace.target and trace.target.node:
                    downstream = trace.target.node
                    if hasattr(downstream, 'set_dirty'):
                        downstream.set_dirty(reason)
                    elif hasattr(downstream, '_is_dirty'):
                        downstream._is_dirty = True

    def _notify_downstream_state_change(self, new_state: NodeState) -> None:
        """Notify downstream nodes that our state changed (more specific than dirty)."""
        outputs = getattr(self, 'outputs', [])
        for port in outputs:
            traces = getattr(port, 'connected_traces', [])
            for trace in traces:
                if trace.target and trace.target.node:
                    downstream = trace.target.node
                    if hasattr(downstream, 'on_upstream_state_change'):
                        downstream.on_upstream_state_change(self, new_state)

    def on_upstream_state_change(self, upstream_node: 'NodeDataFlow', new_state: NodeState) -> None:
        """Hook called when an upstream node changes state. Override for custom behavior."""
        # Default: just mark dirty
        self.set_dirty(f"upstream_{new_state.name.lower()}")

    def request_data(self, port_name: str, visited: Optional[Set[int]] = None) -> Any:
        """Retrieves data for a specific port, evaluating upstream if necessary.

        IMPROVED: Now properly handles DISABLED and PASSTHROUGH states.

        Args:
            port_name: The name of the output port to retrieve.
            visited: A set of node IDs to detect dependency cycles.

        Returns:
            The computed value, cached value, default, or None depending on state.
        """
        if visited is None:
            visited = set()

        node_id = id(self)
        
        # --- CYCLE DETECTION ---
        if node_id in visited:
            return self._get_cached_value(port_name)

        # --- DISABLED STATE HANDLING ---
        if self._state == NodeState.DISABLED:
            return self._get_disabled_value(port_name)

        # --- PASSTHROUGH STATE: Forward request upstream ---
        if self._state == NodeState.PASSTHROUGH:
            # For passthrough, we still need to pull fresh data from upstream
            if self._is_dirty and not self._is_computing:
                visited.add(node_id)
                self.evaluate(visited)
            return self._get_cached_value(port_name)

        # --- NORMAL STATE: Compute if dirty ---
        if self._is_dirty and not self._manual_mode and not self._is_computing:
            visited.add(node_id)
            self.evaluate(visited)

        return self._get_cached_value(port_name)

    def _get_cached_value(self, port_name: str) -> Any:
        """Safely retrieve a cached value."""
        entry = self._cached_values.get(port_name)
        if entry is not None:
            return entry.value if isinstance(entry, CacheEntry) else entry
        return None

    def _get_disabled_value(self, port_name: str) -> Any:
        """Get the appropriate value to return when node is disabled."""
        if self._disabled_behavior == DisabledBehavior.USE_LAST_VALID:
            return self._last_valid_values.get(port_name)
        
        elif self._disabled_behavior == DisabledBehavior.USE_NONE:
            return None
        
        elif self._disabled_behavior == DisabledBehavior.USE_DEFAULT:
            return self._port_defaults.get(port_name)
        
        elif self._disabled_behavior == DisabledBehavior.PROPAGATE_DISABLED:
            # Return a sentinel that downstream can detect
            return DisabledMarker(source_node=self, port_name=port_name)
        
        return None

    def _preserve_valid_values(self) -> None:
        """Snapshot current cache as 'last valid' before disabling."""
        for port_name, entry in self._cached_values.items():
            if isinstance(entry, CacheEntry):
                if entry.is_valid:
                    self._last_valid_values[port_name] = entry.value
            else:
                self._last_valid_values[port_name] = entry

    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """Orchestrates upstream data gathering and local computation.

        IMPROVED: Better state handling and atomic cache updates.
        """
        # --- 1. STATE CHECK: DISABLED ---
        if self._state == NodeState.DISABLED:
            # Don't evaluate, don't clear dirty (will re-eval when enabled)
            return

        # --- 2. RECURSION GUARD ---
        if self._is_computing:
            return

        self._is_computing = True
        new_cache: Dict[str, CacheEntry] = {}
        
        try:
            # --- 3. GATHER INPUTS ---
            input_params = self._gather_inputs(visited)

            # --- 4. EXECUTE BASED ON STATE ---
            if self._state == NodeState.PASSTHROUGH:
                results = self._apply_passthrough(input_params)
            else:
                results = self.compute(input_params)

            # --- 5. NORMALIZE RESULTS ---
            results = self._normalize_results(results)

            # --- 6. BUILD NEW CACHE (atomic update) ---
            import time
            timestamp = time.time()
            for port_name, value in results.items():
                new_cache[port_name] = CacheEntry(
                    value=value,
                    is_valid=True,
                    timestamp=timestamp,
                    source_state=self._state
                )

            # Atomic swap
            self._cached_values = new_cache
            self._is_dirty = False

            # --- 7. UPDATE LAST VALID (for future disable) ---
            for port_name, entry in new_cache.items():
                self._last_valid_values[port_name] = entry.value

            # --- 8. UI HOOK ---
            if hasattr(self, 'on_evaluate_finished'):
                self.on_evaluate_finished()

        except Exception as e:
            log.info(f"Error computing node {self}: {e}")
            # Mark cache entries as invalid but preserve values
            for port_name, entry in self._cached_values.items():
                if isinstance(entry, CacheEntry):
                    entry.is_valid = False

        finally:
            self._is_computing = False

    def _gather_inputs(self, visited: Optional[Set[int]] = None) -> Dict[str, Any]:
        """Gather input values from connected upstream nodes.
        
        IMPROVED: Handles upstream DISABLED nodes gracefully.
        """
        input_params: Dict[str, Any] = {}
        inputs = getattr(self, 'inputs', [])
        
        for port in inputs:
            val = None
            traces = getattr(port, 'connected_traces', [])
            
            if traces:
                trace = traces[0]  # Single-source assumption
                src_port = trace.source
                
                if src_port and src_port.node:
                    # Request data - upstream will handle its own state
                    val = src_port.node.request_data(src_port.name, visited)
                    
                    # Handle DisabledMarker if upstream is propagating disabled state
                    if isinstance(val, DisabledMarker):
                        # Option: Convert to None, or handle specially
                        val = None
            
            input_params[port.name] = val
            
        return input_params

    def _normalize_results(self, results: Any) -> Dict[str, Any]:
        """Convert compute() return value to standard dict format."""
        outputs = getattr(self, 'outputs', [])
        
        if isinstance(results, dict):
            return results
        
        if results is None:
            return {}
        
        # Scalar return for single-output node
        if len(outputs) == 1:
            return {outputs[0].name: results}
        
        raise ValueError(
            f"Node returned scalar but has {len(outputs)} outputs. Return a dict."
        )

    def _apply_passthrough(self, input_values: Dict[str, Any]) -> Dict[str, Any]:
        """Maps inputs to outputs for passthrough mode.
        
        IMPROVED: Better matching strategies and type compatibility checks.
        """
        results = {}
        in_ports = getattr(self, 'inputs', [])
        out_ports = getattr(self, 'outputs', [])
        
        # Build lookup for faster access
        input_by_name = {p.name: input_values.get(p.name) for p in in_ports}
        
        for i, out_port in enumerate(out_ports):
            out_name = out_port.name
            out_type = getattr(out_port, 'data_type', None)
            val = None
            
            # Strategy 1: Exact name match
            if out_name in input_by_name:
                val = input_by_name[out_name]
            
            # Strategy 2: Index-based matching
            elif i < len(in_ports):
                in_port = in_ports[i]
                candidate = input_values.get(in_port.name)
                
                # Optional: Type compatibility check
                if out_type is not None:
                    in_type = getattr(in_port, 'data_type', None)
                    if in_type == out_type or self._types_compatible(in_type, out_type):
                        val = candidate
                else:
                    val = candidate
            
            # Strategy 3: First available input (fallback)
            if val is None and input_values:
                for in_name, in_val in input_values.items():
                    if in_val is not None:
                        val = in_val
                        break
            
            results[out_name] = val
            
        return results

    def _types_compatible(self, in_type: Any, out_type: Any) -> bool:
        """Check if input type can be passed through to output type."""
        if in_type is None or out_type is None:
            return True
        # Add custom type compatibility logic here
        return in_type == out_type

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """User-definable logic. Override in subclasses."""
        return {}

    def trigger(self) -> None:
        """Forces evaluation manually (bypassing manual mode)."""
        self._is_dirty = True
        self.evaluate()

    def invalidate_cache(self, preserve_last_valid: bool = True) -> None:
        """Explicitly invalidate the cache.
        
        Args:
            preserve_last_valid: If True, current values become 'last valid'.
        """
        if preserve_last_valid:
            self._preserve_valid_values()
        
        for entry in self._cached_values.values():
            if isinstance(entry, CacheEntry):
                entry.is_valid = False
        
        self._is_dirty = True


@dataclass
class DisabledMarker:
    """Sentinel value indicating upstream node is disabled."""
    source_node: Any
    port_name: str
    
    def __bool__(self) -> bool:
        return False  # Evaluates to False in boolean context


class BaseControlNode(Node, NodeDataFlow):
    """Hybrid Node: Combines UI (Node) with Logic (NodeDataFlow).
    
    IMPROVED: Comprehensive state change handling with proper cache management.
    
    Class-Level Metadata (override in subclasses):
        node_class:       Category string for registry tree (e.g. "Math").
        node_subclass:    Subcategory string (e.g. "Trigonometry").
        node_name:        Human-readable display name shown in menus.
                          Falls back to ``__name__`` if ``None``.
        node_description: Short one-liner describing what the node does.
                          Searched by the registry and shown in tooltips.
        node_tags:        List of keywords for search relevance.
        node_icon:        Path to an icon file (absolute, relative, or
                          Qt resource ``":/icons/…"``).  Cached by the
                          registry the first time it is resolved.
    
    Example::
    
        @register_node
        class ImageBlurNode(ThreadedNode):
            node_class       = "Image"
            node_subclass    = "Filter"
            node_name        = "Gaussian Blur"
            node_description = "Applies a gaussian blur to the input image."
            node_tags        = ["blur", "smooth", "filter", "gaussian"]
            node_icon        = ":/icons/blur.svg"
    """
    node_class:       ClassVar[str]            = "Basic"
    node_subclass:    ClassVar[str]            = "Basic"
    node_name:        ClassVar[Optional[str]]  = None
    node_description: ClassVar[Optional[str]]  = None
    node_tags:        ClassVar[Optional[List[str]]] = None
    node_icon:        ClassVar[Optional[str]]  = None
    
    data_updated = Signal()
    # NOTE: Do NOT redefine state_changed here - parent Node already defines it
    # as Signal(object, object) emitting (old_state, new_state)

    def __init__(self, title: str, **kwargs):
        """Initialize the node with UI and Logic components."""
        Node.__init__(self, title, **kwargs)
        NodeDataFlow.__init__(self)
        
        # Removed: self.unique_id: str = str(_uuid.uuid4())  # REMOVED
        # UUID is now handled at the Node level in node_core.py
        
        # Deferred initial evaluation
        QTimer.singleShot(0, self._post_init_eval)

    # ==================================================================
    # UUID METHODS - PROXY TO PARENT NODE'S UUID FUNCTIONALITY 
    # ==================================================================

    def get_uuid(self) -> uuid.UUID:
        """
        Get the unique identifier for this node.
        
        This method provides access to the underlying Node's UUID functionality,
        ensuring consistent identification across all node types in the system.
        
        Returns:
            A uuid.UUID object that uniquely identifies this node instance.
        """
        return super().get_uuid()
    
    def get_uuid_string(self) -> str:
        """
        Get the unique identifier for this node as a string representation.
        
        This is useful for serialization, logging, and other string-based operations
        where working with UUID objects directly might be cumbersome.
        
        Returns:
            A string representation of the node's UUID.
        """
        return super().get_uuid_string()

    def _post_init_eval(self) -> None:
        """Runs initial evaluation after constructor completes."""
        # Safety: Check if we're still in a valid scene
        if not self._is_scene_valid():
            return
        if not self._manual_mode:
            self.evaluate()

    def _is_scene_valid(self) -> bool:
        """Check if the node is still in a valid scene."""
        try:
            return self.scene() is not None
        except RuntimeError:
            return False

    def set_state(self, state: NodeState) -> None:
        """Override to integrate state changes with dataflow logic.
        
        IMPROVED: Comprehensive handling of all state transitions.
        """
        old_state = self._state
        
        if old_state == state:
            return  # No change
        
        # --- PRE-TRANSITION ACTIONS ---
        if state == NodeState.DISABLED:
            # Preserve current cache before disabling
            self._preserve_valid_values()
        
        # Call parent for UI updates
        super().set_state(state)
        
        # --- POST-TRANSITION ACTIONS ---
        self._handle_state_transition(old_state, state)
        
        # Notify downstream about state change
        self._notify_downstream_state_change(state)
        
        # NOTE: Parent Node.set_state() already emits state_changed signal

    def _handle_state_transition(self, old_state: NodeState, new_state: NodeState) -> None:
        """Handle specific state transition logic.
        
        State Transition Matrix:
        ┌──────────────┬────────────────┬────────────────┬────────────────┐
        │ FROM | TO    │ NORMAL         │ DISABLED       │ PASSTHROUGH    │
        ├──────────────┼────────────────┼────────────────┼────────────────┤
        │ NORMAL       │ -              │ preserve+notify│ clear+reeval   │
        │ DISABLED     │ restore+reeval │ -              │ restore+reeval │
        │ PASSTHROUGH  │ clear+reeval   │ preserve+notify│ -              │
        └──────────────┴────────────────┴────────────────┴────────────────┘
        """
        
        # DISABLED -> anything else: Restore and re-evaluate
        if old_state == NodeState.DISABLED:
            # Cache was preserved, now mark dirty to recompute
            self._is_dirty = True
            if not self._manual_mode:
                QTimer.singleShot(0, self._safe_evaluate)
        
        # anything -> DISABLED: Already preserved, notify downstream
        elif new_state == NodeState.DISABLED:
            # Downstream needs fresh data (which will be disabled values)
            self._mark_downstream_dirty("upstream_disabled")
        
        # NORMAL <-> PASSTHROUGH: Clear cache and re-evaluate
        elif (old_state == NodeState.NORMAL and new_state == NodeState.PASSTHROUGH) or \
             (old_state == NodeState.PASSTHROUGH and new_state == NodeState.NORMAL):
            # Clear the cache to avoid mixing compute() and passthrough results
            self._cached_values.clear()
            self._is_dirty = True
            if not self._manual_mode:
                QTimer.singleShot(0, self._safe_evaluate)

    def _safe_evaluate(self) -> None:
        """Evaluate with safety checks for Qt deletion."""
        if self._is_scene_valid():
            self.evaluate()

    def set_dirty(self, reason: str = "value_change") -> None:
        """Overrides Logic.set_dirty for Qt-specific behavior."""
        already_dirty = self._is_dirty
        
        NodeDataFlow.set_dirty(self, reason)

        if not self._manual_mode and not already_dirty:
            QTimer.singleShot(0, self._safe_evaluate)
        elif self._manual_mode:
            self.update()

    def on_evaluate_finished(self) -> None:
        """Callback after compute() completes."""
        if not self._is_scene_valid():
            return
        
        self.data_updated.emit()
        self.update()

    def on_upstream_state_change(self, upstream_node: 'NodeDataFlow', new_state: NodeState) -> None:
        """React to upstream node state changes.
        
        IMPROVED: More intelligent response to upstream changes.
        """
        if new_state == NodeState.DISABLED:
            # Upstream disabled - we need to re-evaluate with disabled values
            self.set_dirty("upstream_disabled")
        else:
            # Upstream re-enabled or changed mode - re-evaluate
            self.set_dirty(f"upstream_{new_state.name.lower()}")

    def on_ui_change(self) -> None:
        """Hook for internal widgets to request updates."""
        self.set_dirty("ui_change")

    def get_output_value(self, port_name: str) -> Any:
        """Public API to get current output value (for UI display, etc.)."""
        return self._get_cached_value(port_name)

    def is_output_valid(self, port_name: str) -> bool:
        """Check if an output port has a valid cached value."""
        entry = self._cached_values.get(port_name)
        if isinstance(entry, CacheEntry):
            return entry.is_valid
        return entry is not None

    # ══════════════════════════════════════════════════════════════════
    # Serialisation — extends Node with widget + dataflow state
    # ══════════════════════════════════════════════════════════════════

    def get_state(self) -> Dict[str, Any]:
        '''Full node state: GUI (super) + widget data + dataflow metadata.
        
        Serialisation boundary:
            Node.get_state()          → pos, size, colors, ports, minimized
            BaseControlNode.get_state() → above + widget_data + dataflow
        '''
        state = super().get_state()
        
        # Widget state — exclusively via WeaveWidgetCore
        if hasattr(self, '_weave_core') and self._weave_core is not None:
            state["widget_data"] = self._weave_core.get_state()
        else:
            state["widget_data"] = {}
        
        # Dataflow metadata
        state["dataflow"] = {
            "manual_mode": self._manual_mode,
            "disabled_behavior": self._disabled_behavior.name,
            "port_defaults": self._port_defaults.copy(),
        }
        
        return state

    def restore_state(self, state: Dict[str, Any]) -> None:
        '''Restore full node state: GUI (super) + widget data + dataflow metadata.'''
        def _t(msg):
            print(f"[BaseControlNode.restore_state] {msg}", flush=True)

        # 1. Restore GUI state (position, size, colors, ports, minimized)
        _t("Calling super().restore_state (Node) ...")
        super().restore_state(state)
        _t("super().restore_state OK")
        
        # 2. Restore widget state — exclusively via WeaveWidgetCore
        _t("Restoring widget_data ...")
        widget_data = state.get("widget_data")
        if widget_data and hasattr(self, '_weave_core') and self._weave_core is not None:
            self._weave_core.set_state(widget_data)
        _t("widget_data OK")
        
        # 3. Restore dataflow metadata
        _t("Restoring dataflow metadata ...")
        df = state.get("dataflow", {})
        if "manual_mode" in df:
            self._manual_mode = df["manual_mode"]
        if "disabled_behavior" in df:
            try:
                self._disabled_behavior = DisabledBehavior[df["disabled_behavior"]]
            except KeyError:
                pass
        if "port_defaults" in df:
            self._port_defaults = df["port_defaults"]
        _t("restore_state COMPLETE")


# ------------------------------------------------------------------------------
# Concrete Implementations
# ------------------------------------------------------------------------------

class ActiveNode(BaseControlNode):
    """Automatically updates and propagates changes immediately."""
    
    def __init__(self, title: str = "Active Node", **kwargs):
        super().__init__(title, **kwargs)
        self._manual_mode = False

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Example compute implementation."""
        in_val = inputs.get("in", 0)
        return {"out": (in_val or 0) * 2}


class ManualNode(BaseControlNode):
    """Requires explicit trigger to process."""
    
    def __init__(self, title: str = "Manual Node", **kwargs):
        super().__init__(title, **kwargs)
        self._manual_mode = True

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Example compute for manual node."""
        return {"out": "Manual Result"}
        
    def execute(self) -> None:
        """Public slot to trigger execution."""
        self.trigger()


class PassthroughTestNode(BaseControlNode):
    """Node specifically designed to test passthrough behavior."""
    
    def __init__(self, title: str = "Passthrough Test", **kwargs):
        super().__init__(title, **kwargs)
        self._manual_mode = False
        # Start in passthrough mode
        self.set_state(NodeState.PASSTHROUGH)

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """This should not be called in passthrough mode."""
        in_val = inputs.get("in", 0)
        return {"out": f"COMPUTED: {in_val}"}


# ------------------------------------------------------------------------------
# Test / Demo
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)

    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, 800, 600)
    scene.setBackgroundBrush(QColor(30, 30, 30))

    view = QGraphicsView(scene)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.resize(1000, 700)

    # Create test nodes
    node_active = ActiveNode("Auto Processor")
    node_active.setPos(100, 100)
    scene.addItem(node_active)

    node_manual = ManualNode("Heavy Task")
    node_manual.setPos(350, 100)
    scene.addItem(node_manual)

    # Test state changes
    print("Testing state transitions...")
    
    print(f"Initial state: {node_active._state}")
    node_active.set_state(NodeState.DISABLED)
    print(f"After disable: {node_active._state}")
    print(f"Last valid values preserved: {node_active._last_valid_values}")
    
    node_active.set_state(NodeState.NORMAL)
    print(f"After re-enable: {node_active._state}")
    
    node_active.set_state(NodeState.PASSTHROUGH)
    print(f"After passthrough: {node_active._state}")

    view.show()
    sys.exit(app.exec())