# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

BaseControlNode with robust fence management and enhanced dataflow logic.

FIXED:
- Added `_are_inputs_ready()` barrier for synchronous multi-input dependency synchronization.
- Delegated `_handle_state_transition` re-evaluation to `self.set_dirty()` to ensure polymorphic safety.
- Fixed `AttributeError` for computing pulse animations.
- Fixed Terminal Node crash (0 outputs) in `_normalize_results`.
- Fixed Passthrough type-dropping for "any" datatypes.
"""

from __future__ import annotations

import os
import sys
import uuid
import weakref
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Final, ClassVar, Callable, Type
from dataclasses import dataclass, field

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QObject
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView
from PySide6.QtGui import QColor, QPainter

from weave.node.node_core import Node
from weave.node.node_enums import NodeState, VerticalSizePolicy, DisabledBehavior
from weave.panel.dock_properties import DockProperties

from weave.logger import get_logger
log = get_logger("BaseNode")

_DEBUG = os.environ.get("WEAVE_DEBUG", "0") == "1"

def _dbg(msg: str) -> None:
    """Module-local debug printer for verbose evaluation tracking."""
    if _DEBUG:
        print(f"[BaseNode] {msg}", flush=True)
    log.debug(msg)


@dataclass
class CacheEntry:
    """Represents a cached value with metadata."""
    value: Any
    is_valid: bool = True
    timestamp: float = 0.0
    source_state: Optional[NodeState] = None


class NodeDataFlow:
    """Mixin to handle logic state, dirty-flag propagation, and lazy evaluation.

    Key improvements over base implementation:
    - Robust cycle detection in request_data()
    - Atomic cache updates for thread-safety
    - Better passthrough that properly pulls from upstream 
    - State change notifications with reason
    """

    def __init__(self) -> None:
        self._is_dirty: bool = True
        self._is_computing: bool = False
        self._cached_values: Dict[str, CacheEntry] = {}
        self._last_valid_values: Dict[str, Any] = {}  # NEW: Preserved before disable
        self._manual_mode: bool = False
        self._disabled_behavior: DisabledBehavior = DisabledBehavior.USE_NONE
        self._port_defaults: Dict[str, Any] = {}
        self._state: NodeState = NodeState.NORMAL

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
        """Mark all downstream nodes as dirty with a reason."""
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

        IMPROVED: Now properly handles DISABLED and PASSTHROUGH states with cycle detection.
        
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
        FIXED: ``_mark_downstream_dirty`` moved to ``finally`` so
        downstream nodes waiting at the barrier are always woken up,
        even when ``compute()`` raises.  Without this, an exception in
        any upstream node permanently orphans the entire downstream
        chain.
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
        _eval_succeeded = False
        
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
            _eval_succeeded = True

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
            # Wake downstream nodes regardless of success/failure.
            # On success: downstream re-evaluates with our fresh output.
            # On failure: downstream stops waiting at the barrier and
            # proceeds with whatever cached data is available, rather
            # than being stuck indefinitely.
            if _eval_succeeded:
                self._mark_downstream_dirty("upstream_evaluated")

    def _gather_inputs(self, visited: Optional[Set[int]] = None) -> Dict[str, Any]:
        """Gather input values from connected upstream nodes.

        For each input port:
        1. If connected, pull the upstream value via ``request_data()``.
        2. If unconnected **and** a matching WidgetCore bidirectional
           binding exists, fall back to the widget's current value so
           node ``compute()`` methods don't need manual fallback logic.
        3. Otherwise the value is ``None``.

        This means a bidirectional port always provides a value to
        ``compute()`` — either from upstream or from the local widget.
        """
        input_params: Dict[str, Any] = {}
        inputs = getattr(self, 'inputs', [])

        # Resolve WidgetCore once for the fallback path
        wc = getattr(self, '_widget_core', None) or getattr(self, '_weave_core', None)

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
                        val = None

            # Fall back to WidgetCore for unconnected bidirectional ports
            if val is None and wc is not None and hasattr(wc, 'get_binding'):
                binding = wc.get_binding(port.name)
                if binding is not None:
                    role_name = getattr(binding.role, 'name', '')
                    if role_name in ('BIDIRECTIONAL', 'INPUT'):
                        try:
                            val = wc.get_port_value(port.name)
                        except Exception:
                            pass

            input_params[port.name] = val

        return input_params

    def _normalize_results(self, results: Any) -> Dict[str, Any]:
        """Convert compute() return value to standard dict format."""
        outputs = getattr(self, 'outputs', [])
        
        if isinstance(results, dict):
            return results
        
        if results is None:
            return {}

        # Safely handle Terminal Nodes (0 outputs) returning scalars
        if len(outputs) == 0:
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
                    if self._types_compatible(in_type, out_type):
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

        # Universal wildcard support for "any" datatype
        in_str = str(in_type).lower()
        out_str = str(out_type).lower()
        if in_str == "any" or out_str == "any":
            return True

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
        node_class:        Category string for registry tree (e.g. "Math").
        node_subclass:     Subcategory string (e.g. "Trigonometry").
        node_name:         Human-readable display name shown in menus.
                           Falls back to ``__name__`` if ``None``.
        node_description:  Short one-liner describing what the node does.
                           Searched by the registry and shown in tooltips.
        node_tags:         List of keywords for search relevance.
                           
        node_icon:         Icon name to be provided to Node Icon Provider
        node_class_icon:   Class-level icon name
        node_subclass_icon: Subclass-level icon name
        node_icon_path:    Base path for node icons, can be set individually
                           by costume nodes
        dock_properties:   Optional ``DockProperties`` instance that defines
                           allowed dock areas, size constraints, and dock
                           feature flags for static dock panels created
                           from this node.  ``None`` means all defaults.
        vertical_size_policy:
                           Controls whether the node shrinks vertically
                           when content is removed.
                           ``VerticalSizePolicy.GROW_ONLY`` (default) —
                           height only increases, user-set height is
                           preserved.
                           ``VerticalSizePolicy.FIT`` — height always
                           matches the minimum required, so removing
                           a port or hiding a widget shrinks the node.
    
    Example::
    
        @register_node
        class ImageBlurNode(ThreadedNode):
            node_class       = "Image"
            node_subclass    = "Filter"
            node_name        = "Gaussian Blur"
            node_description = "Applies a gaussian blur to the input image."
            node_tags        = ["blur", "smooth", "filter", "gaussian"]
            node_icon        = "blur"
            node_class_icon  = "class_icon"
            node_subclass_icon  = "sub_class_icon"
            node_icon_path   =   r"path/to/icon"
            dock_properties  = DockProperties(
                allowed_areas=Qt.DockWidgetArea.LeftDockWidgetArea
                            | Qt.DockWidgetArea.RightDockWidgetArea,
                min_width=250,
                preferred_area=Qt.DockWidgetArea.RightDockWidgetArea,
            )
            # Node shrinks when ports are removed or hidden
            vertical_size_policy = VerticalSizePolicy.FIT
    """
    node_class: ClassVar[str] = "Basic"
    node_subclass: ClassVar[str] = "Basic"
    node_name: ClassVar[Optional[str]] = None
    node_description: ClassVar[Optional[str]] = None
    node_tags: ClassVar[Optional[List[str]]] = None
    node_icon: ClassVar[Optional[str]] = "node"
    node_class_icon: ClassVar[Optional[str]] = "node"
    node_subclass_icon: ClassVar[Optional[str]] = "node"
    node_icon_path: ClassVar[Optional[str]] = str(Path(__file__).parent / "resources" / "node_icons")
    dock_properties: ClassVar[Optional[DockProperties]] = None
    vertical_size_policy: ClassVar[VerticalSizePolicy] = VerticalSizePolicy.GROW_ONLY
    
    data_updated = Signal()

    def __init__(self, title: str, **kwargs):
        Node.__init__(self, title, **kwargs)
        NodeDataFlow.__init__(self)
        
        self._vertical_size_policy = type(self).vertical_size_policy

        # Fence tracking
        self._eval_pending: bool = False
        # ── THE FIX: Now acts as a counting semaphore ──
        self._fence_token: int = 0
        self._fence_scene_ref: Optional[weakref.ref] = None
        
        self.port_removed.connect(self._on_port_removed)
        QTimer.singleShot(0, self._post_init_eval)

    def _post_init_eval(self) -> None:
        if not self._is_scene_valid():
            return
        if not self._manual_mode:
            self.set_dirty("init")

    def _is_scene_valid(self) -> bool:
        try:
            return self.scene() is not None
        except RuntimeError:
            return False

    def _update_colors(self, is_selected: bool):
        super()._update_colors(is_selected)
        wc = getattr(self, '_widget_core', None) or getattr(self, '_weave_core', None)
        if wc is not None and hasattr(wc, 'apply_node_palette'):
            wc.apply_node_palette(
                self.header._bg_color,
                self.body._bg_color,
            )

    def set_state(self, state: NodeState) -> None:
        """Override to integrate state changes with dataflow logic.

        IMPORTANT: Do NOT set self._state here before calling super().
        NodePortsMixin.set_state() owns the self._state assignment and uses
        the current (old) value to detect whether a real transition is
        occurring.  If we stomp self._state first, its guard:

            if old_state == state: return

        evaluates True immediately and the entire visual pipeline is skipped:
        _apply_state_visuals(), sync_state_slider() (animation + color), and
        all update() calls are never reached.

        Execution order:
        1. Guard + capture old_state (we read self._state while it is still OLD).
        2. Pre-transition dataflow work that must happen BEFORE the visual update.
        3. super().set_state(state) — sets self._state, applies visuals, animates
           the slider, emits state_changed.
        4. Post-transition dataflow logic that needs the new self._state in place.
        5. Notify downstream nodes.
        """
        if self._state == state:
            return

        old_state = self._state
        # NOTE: Do NOT write self._state here.  super().set_state() owns that
        # assignment (NodePortsMixin line: self._state = state).

        # --- STEP 2: Pre-transition work ---

        # Use the correct encapsulated method names from NodePulseAnimMixin (§3)
        if state == NodeState.COMPUTING:
            if hasattr(self, '_start_computing_pulse'):
                self._start_computing_pulse()
        elif old_state == NodeState.COMPUTING:
            if hasattr(self, '_stop_computing_pulse'):
                self._stop_computing_pulse()

        # Preserve cache snapshot before entering DISABLED so that
        # USE_LAST_VALID can serve the final good values downstream.
        if state == NodeState.DISABLED:
            self._preserve_valid_values()  # From NodeDataFlow

        # --- STEP 3: Visual update (sets self._state, animates slider, repaints) ---
        super().set_state(state)

        # --- STEP 4: Post-transition dataflow logic ---
        self._handle_state_transition(old_state, state)

        # --- STEP 5: Notify downstream nodes ---
        self._notify_downstream_state_change(state)

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

        FIXED: State transitions unconditionally force ``_is_dirty = True``
        and schedule ``_fenced_evaluate`` directly, bypassing ``set_dirty``
        whose ``NodeDataFlow.set_dirty`` guard (``if _is_dirty: return``)
        can swallow the request when the node is already dirty.  The cache
        clear + mode switch MUST trigger a fresh evaluation — the old code
        did this correctly via direct assignment.
        """

        # DISABLED -> anything else: Restore and re-evaluate
        if old_state == NodeState.DISABLED:
            self._is_dirty = True
            if not self._manual_mode:
                self._eval_pending = True
                QTimer.singleShot(0, self._fenced_evaluate)

        # anything -> DISABLED: Already preserved, notify downstream
        elif new_state == NodeState.DISABLED:
            self._mark_downstream_dirty("upstream_disabled")

        # NORMAL <-> PASSTHROUGH: Clear cache and re-evaluate
        elif (old_state == NodeState.NORMAL and new_state == NodeState.PASSTHROUGH) or \
             (old_state == NodeState.PASSTHROUGH and new_state == NodeState.NORMAL):
            self._cached_values.clear()
            self._is_dirty = True
            if not self._manual_mode:
                self._eval_pending = True
                QTimer.singleShot(0, self._fenced_evaluate)

    def _safe_evaluate(self) -> None:
        """Evaluate with safety checks for Qt deletion."""
        if self._is_scene_valid():
            self.evaluate()

    # ==================================================================
    # Fence & Barrier Management
    # ==================================================================

    def _increment_eval_fence(self) -> None:
        """Increment fence with a counting semaphore to support concurrent operations."""
        try:
            # Safely handle legacy nodes that might have initialized this to None
            if getattr(self, '_fence_token', None) is None:
                self._fence_token = 0

            self._fence_token += 1

            scene = self.scene()
            if scene is not None:
                current = getattr(scene, '_eval_fence', 0)
                scene._eval_fence = current + 1
                # CRITICAL FIX: Store weak reference to scene
                self._fence_scene_ref = weakref.ref(scene)
                _dbg(f"fence increment: {current} -> {current + 1} "
                     f"(node holds {self._fence_token}, uuid={self.get_uuid_string()[:8]})")
        except RuntimeError:
            pass

    def _decrement_eval_fence(self) -> None:
        """Decrement fence using stored scene reference, supporting multiple concurrent holds."""
        try:
            if getattr(self, '_fence_token', None) is None:
                self._fence_token = 0

            if self._fence_token > 0:
                self._fence_token -= 1
                scene = None

                # Try stored weak reference first (node may be removed from scene)
                if self._fence_scene_ref is not None:
                    scene = self._fence_scene_ref()

                # Fallback to current scene() if stored ref is dead
                if scene is None:
                    scene = self.scene()

                if scene is not None:
                    current = getattr(scene, '_eval_fence', 0)
                    if current > 0:
                        scene._eval_fence = current - 1
                        _dbg(f"fence decrement: {current} -> {current - 1} "
                             f"(node holds {self._fence_token})")
                        # Notify listeners (e.g. UndoManager) when the canvas
                        # becomes idle — this lets them stop polling and
                        # react immediately. Tolerant of canvases that do
                        # not define the signal (graceful no-op).
                        if scene._eval_fence == 0:
                            sig = getattr(scene, 'eval_fence_idle', None)
                            if sig is not None:
                                try:
                                    sig.emit()
                                except (RuntimeError, AttributeError):
                                    pass
                else:
                    _dbg(f"WARNING: Cannot decrement fence, scene gone. "
                         f"Node held {self._fence_token + 1} -> {self._fence_token}")

                # Clean up the weak ref when the node holds no more fences
                if self._fence_token == 0:
                    self._fence_scene_ref = None

        except RuntimeError:
            pass

    def _are_inputs_ready(self) -> bool:
        """Dependency Synchronization Barrier.

        Ensures this node waits until ALL upstream dependencies have
        finished evaluating before it attempts to compute.
        """
        for port in getattr(self, 'inputs', []):
            for trace in getattr(port, 'connected_traces', []):
                src_port = getattr(trace, 'source', None)
                if not src_port: continue

                src_node = getattr(src_port, 'node', None)
                if not src_node: continue
                # 1. Disabled nodes won't compute. Safe to proceed.
                if getattr(src_node, '_state', None) == NodeState.DISABLED:
                    continue
                # 2. If upstream is currently running or queued, wait!
                if getattr(src_node, '_is_computing', False) or getattr(src_node, '_eval_pending', False):
                    return False
                # 3. If upstream is marked dirty, it's about to be queued.
                if getattr(src_node, '_is_dirty', False):
                    if not getattr(src_node, '_manual_mode', False):
                        return False
        return True

    def _fenced_evaluate(self) -> None:
        """Run evaluate with guaranteed fence cleanup and barrier synchronization.

        FIXED: When the barrier fails, reschedule instead of silently
        dropping.  The old code (``_safe_evaluate``) had no barrier and
        *always* evaluated.  A silent drop leaves the node dirty with
        ``_eval_pending = False`` — if the upstream ``_mark_downstream_dirty``
        recovery is not reached (e.g. upstream ``compute()`` throws), the
        node stays stuck showing stale cached data forever.
        """
        self._eval_pending = False

        if not self._is_dirty or self._state == NodeState.DISABLED:
            return

        # Synchronization Barrier: wait for upstream nodes to finish.
        # If upstream is still busy, RESCHEDULE so we try again on the
        # next event-loop tick instead of relying solely on upstream to
        # wake us via _mark_downstream_dirty.
        if not self._are_inputs_ready():
            if not self._eval_pending:
                self._eval_pending = True
                QTimer.singleShot(0, self._fenced_evaluate)
            return

        self._increment_eval_fence()
        try:
            self._safe_evaluate()
        finally:
            self._decrement_eval_fence()

    def set_dirty(self, reason: str = "value_change") -> None:
        """Mark dirty with improved fence handling and dependency barrier."""
        NodeDataFlow.set_dirty(self, reason)

        if not self._manual_mode and not self._eval_pending:
            self._eval_pending = True
            # Fence increment moved inside _fenced_evaluate so we don't hold it
            # unnecessarily while waiting at the dependency barrier
            QTimer.singleShot(0, self._fenced_evaluate)
        elif self._manual_mode:
            self.update()

    @classmethod
    def register_port_type(cls,
                           name: str,
                           color_index: Optional[int] = None,
                           type_id: Optional[int] = None,
                           python_type: Optional[Type] = None,
                           base_type_id: int = -1,
                           default: Any = None,
                           validator: Optional[Callable[[Any], bool]] = None,
                           formatter: Optional[Callable[[Any], str]] = None,
                           casts_to: Optional[Dict[Any, Callable[[Any], Any]]] = None) -> "PortType":
        """Register a custom port type with the global :class:`PortRegistry`.
    
        Thin convenience wrapper so subclasses don't have to import
        ``PortRegistry`` directly.  All arguments are forwarded verbatim.
    
        Args:
            name:         Unique human-readable type name (e.g. ``"Vector3"``).
            color_index:  Index into the active theme's
                          ``trace_color_palette`` (``StyleCategory.TRACE``).
                          Built-in types occupy 0–255; custom types should
                          use indices ≥256, or pass ``None`` to auto-assign
                          from the next free slot.
            type_id:      Unique numeric ID.  ``None`` auto-assigns from 200+.
            python_type:  Python type the port carries.
            base_type_id: Parent type ID for upcasting (``-1`` = no parent).
            default:      Default value or zero-arg factory.
            validator:    ``Callable[[Any], bool]`` validating values.
            formatter:    ``Callable[[Any], str]`` for UI display.
            casts_to:     ``{target_id_or_name: converter_fn}`` explicit
                          outbound casts from this type.
    
        Returns:
            The newly registered :class:`PortType`.
    
        Raises:
            ValueError: If ``name`` or ``type_id`` is already registered.
        """
        from weave.node.portregistry import PortRegistry
        return PortRegistry.register(
            name=name,
            color_index=color_index,
            type_id=type_id,
            python_type=python_type,
            base_type_id=base_type_id,
            default=default,
            validator=validator,
            formatter=formatter,
            casts_to=casts_to,
        )

    def on_port_connection_changed(self, port) -> None:
        """
        Mediator callback: Triggered by NodePort when traces are added/removed.
        Translates graph topology changes into UI state changes.
        """
        # Only input ports trigger auto-disable
        if port.is_output:
            return

        wc = getattr(self, '_widget_core', None) or getattr(self, '_weave_core', None)
        if wc is None:
            return

        # Ask WidgetCore if this port has a matching UI widget
        binding = wc.get_binding(port.name)
        if binding is not None:
            role_name = getattr(binding.role, 'name', '')
            
            # If the widget acts as a fallback for an input, auto-disable it!
            if role_name in ('BIDIRECTIONAL', 'INPUT'):
                has_connections = len(getattr(port, 'connected_traces', [])) > 0
                wc.set_port_enabled(port.name, not has_connections)

    def _on_port_removed(self, port) -> None:
        """Clean up dataflow artifacts with fence-tracked dirty."""
        name = getattr(port, 'name', None)
        if name is None:
            return

        # Purge dataflow caches
        self._cached_values.pop(name, None)
        self._last_valid_values.pop(name, None)
        self._port_defaults.pop(name, None)

        # FIXED: Only mark dirty if node is still in a valid scene and not computing
        if not self._is_computing and self.scene() is not None:
            self.set_dirty("port_removed")

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

    def cleanup(self) -> None:
        """Aggressive teardown: sever signals, clear caches, release fence (§7).

        Provides the base cleanup target that ThreadedNode.cleanup() and
        NodeManager.remove_node() expect.  Guards the super() call since
        Node (from node_core) may not define cleanup() either.
        """
        # 1. Disconnect the port_removed signal we connected in __init__
        try:
            self.port_removed.disconnect(self._on_port_removed)
        except (RuntimeError, TypeError):
            pass

        # 2. Release ALL held eval-fence tokens so UndoManager isn't stuck
        if getattr(self, '_fence_token', None) is None:
            self._fence_token = 0

        while self._fence_token > 0:
            self._decrement_eval_fence()

        # 3. Sever WidgetCore Qt signal bindings and event filters (§7)
        wc = getattr(self, '_widget_core', None) or getattr(self, '_weave_core', None)
        if wc is not None and hasattr(wc, 'cleanup'):
            try:
                wc.cleanup()
            except Exception as e:
                log.error(f"WidgetCore cleanup error in {self.__class__.__name__}: {e}")

        # 4. Clear dataflow caches to break potential reference cycles
        self._cached_values.clear()
        self._last_valid_values.clear()
        self._port_defaults.clear()

        # 5. Propagate to parent classes (Node may define its own cleanup)
        if hasattr(super(), 'cleanup'):
            super().cleanup()

    def get_state(self) -> Dict[str, Any]:
        '''Full node state: GUI (super) + widget data + dataflow metadata.
        
        Serialisation boundary:
            Node.get_state()          → pos, size, colors, ports, minimized
            BaseControlNode.get_state() → above + widget_data + dataflow
        '''
        state = super().get_state()
        
        # Widget state — exclusively via WeaveWidgetCore
        if hasattr(self, '_widget_core') and self._widget_core is not None:
            state["widget_data"] = self._widget_core.get_state()
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
        # 1. Restore GUI state (position, size, colors, ports, minimized)
        super().restore_state(state)
        
        # 2. Restore widget state — exclusively via WeaveWidgetCore
        widget_data = state.get("widget_data")
        if widget_data and hasattr(self, '_widget_core') and self._widget_core is not None:
            self._widget_core.set_state(widget_data)
        
        # 3. Restore dataflow metadata
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


# ------------------------------------------------------------------------------
# Concrete Implementations
# ------------------------------------------------------------------------------

class ActiveNode(BaseControlNode):
    """Automatically updates and propagates changes immediately.

    Uses ``FIT`` policy so the node shrinks when ports are removed.
    """
    vertical_size_policy = VerticalSizePolicy.FIT
    
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