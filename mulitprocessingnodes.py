# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Weave Multiprocessing Nodes
============================
Auto-evaluating and manual nodes that execute compute() in a separate process.

Requirements:
    1. Subclass MultiprocessingNode or MultiprocessingManualNode.
    2. Override compute() as a @staticmethod (or assign a module function).
    3. Use is_cancelled_from_inputs(inputs) inside compute() for cooperative cancellation.
    
Example:
    @register_node
    class HeavyMathNode(MultiprocessingNode):
        def __init__(self, **kw):
            super().__init__("Heavy Math", **kw)
            self.add_input("values", "list")
            self.add_output("sum", "float")
        
        @staticmethod
        def compute(inputs):
            data = inputs.get("values", [])
            cancel_evt = inputs.get('_cancel_event')
            
            total = 0
            for i, v in enumerate(data):
                if cancel_evt and cancel_evt.is_set():
                    return {"sum": None}  # Cancelled
                total += v ** 2  # heavy-ish work
            return {"sum": total}
"""

from typing import Optional, Set, Any, Dict

from PySide6.QtCore import QTimer, Qt, Slot

from weave.threadednodes import ThreadedNode, ThreadedManualNode, _HAS_COMPUTING_STATE
from weave.node.node_enums import NodeState
from weave.multiprocessingbridge import (
    MultiprocessingWorker,
    MultiprocessingCancellationToken,
    get_multiprocessing_pool,
)

from weave.logger import get_logger
log = get_logger("MultiprocessingNodes")


# ═══════════════════════════════════════════════════════════════════════════════
# BASE MIXIN (shared logic)
# ═══════════════════════════════════════════════════════════════════════════════

class _MultiprocessingMixin:
    """Shared machinery for process-based nodes."""
    
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Validate that compute is likely picklable at class definition time
        if hasattr(cls, 'compute') and not callable(cls.compute):
            raise TypeError(f"{cls.__name__}.compute must be callable")
    
    def _is_compute_picklable(self) -> bool:
        """Check if self.compute can be sent to subprocess."""
        import pickle
        try:
            # Access via __func__ if it's a staticmethod descriptor
            fn = self.compute
            if isinstance(fn, staticmethod):
                fn = fn.__func__
            pickle.dumps(fn)
            return True
        except Exception:
            return False
    
    def _prepare_inputs(self, visited: Optional[Set[int]]) -> Dict[str, Any]:
        """Gather upstream + widget snapshot (runs on main thread)."""
        try:
            input_params = self._gather_inputs(visited)
        except Exception as e:
            log.error(f"Input gathering failed: {e}")
            raise
        
        widget_snapshot = self.snapshot_widget_inputs()
        if widget_snapshot:
            input_params.update(widget_snapshot)
        return input_params
    
    def _dispatch_worker(self, inputs: Dict[str, Any]) -> None:
        """Create and start the multiprocessing worker."""
        # Create process-safe cancellation token
        self._cancel_token = MultiprocessingCancellationToken()
        
        # Resolve the static function (handle both staticmethod and function)
        compute_fn = self.compute
        if isinstance(compute_fn, staticmethod):
            compute_fn = compute_fn.__func__
        
        worker = MultiprocessingWorker(
            compute_fn=compute_fn,
            inputs=inputs,
            cancel_token=self._cancel_token,
        )
        
        # Wire signals (queued connection ensures main thread reception)
        worker.signals.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.cancelled.connect(self._on_worker_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.compute_progress.emit, Qt.ConnectionType.QueuedConnection)
        
        self._current_worker = worker
        self._thread_pool.start(worker)  # Uses QThreadPool for the monitor thread
    
    # ------------------------------------------------------------------
    # API Compatibility (shadow ThreadedNode's threading token)
    # ------------------------------------------------------------------
    
    def is_compute_cancelled(self) -> bool:
        """Main-thread check for cancellation status."""
        return hasattr(self, '_cancel_token') and self._cancel_token.is_cancelled()
    
    def cancel_compute(self) -> None:
        """Request cancellation (sets the mp.Event)."""
        if self._is_computing and hasattr(self, '_cancel_token'):
            self._cancel_token.cancel()


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-EVALUATING MULTIPROCESSING NODE
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingNode(_MultiprocessingMixin, ThreadedNode):
    """
    Auto-evaluating node that runs compute() in a subprocess via multiprocessing.Pool.
    
    Inherits all signals from ThreadedNode (compute_started, compute_finished, etc.).
    
    **CRITICAL**: Override compute() as a @staticmethod.
    """
    
    def __init__(self, title: str = "Multiprocessing Node", **kwargs):
        # Initialize parent but we override the token type later
        super().__init__(title, **kwargs)
        # Replace the threading-based token with None initially; created per-eval
        self._cancel_token: Optional[MultiprocessingCancellationToken] = None
    
    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """
        Override ThreadedNode.evaluate to use MultiprocessingWorker instead of ComputeWorker.
        Logic mirrors ThreadedNode but swaps the execution backend.
        """
        # ── State guards (same as ThreadedNode) ─────────────────────
        if self._state == NodeState.DISABLED:
            return
        
        if _HAS_COMPUTING_STATE and self._state == NodeState.COMPUTING:
            self._pending_dirty = True
            return
        
        if self._is_computing:
            self._pending_dirty = True
            self.cancel_compute()
            return
        
        if self._state == NodeState.PASSTHROUGH:
            # Passthrough stays synchronous (fast path)
            super(ThreadedNode, self).evaluate(visited)  # Skip to BaseControlNode
            return
        
        # ── Prepare for compute ─────────────────────────────────────
        self._is_computing = True
        
        if not self._is_compute_picklable():
            log.error(f"{self}: compute() must be a @staticmethod for multiprocessing")
            self._is_computing = False
            return
        
        try:
            inputs = self._prepare_inputs(visited)
        except Exception:
            self._is_computing = False
            return
        
        # ── Set COMPUTING state visuals ─────────────────────────────
        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                super(ThreadedNode, self).set_state(NodeState.COMPUTING)
                if hasattr(self, '_start_computing_pulse'):
                    self._start_computing_pulse()
            except Exception:
                pass
        
        # ── Dispatch to process pool ────────────────────────────────
        self.compute_started.emit()
        self._dispatch_worker(inputs)


# ═══════════════════════════════════════════════════════════════════════════════
# MANUAL MULTIPROCESSING NODE
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingManualNode(_MultiprocessingMixin, ThreadedManualNode):
    """
    Button-triggered node that runs compute() in a subprocess.
    
    Inherits ThreadedManualNode behavior: execute() starts, execute() again cancels.
    """
    
    def __init__(self, title: str = "Multiprocessing Manual", **kwargs):
        super().__init__(title, **kwargs)
        self._cancel_token: Optional[MultiprocessingCancellationToken] = None
    
    def execute(self) -> None:
        """Toggle between start and cancel."""
        if self._is_computing:
            self.cancel_compute()
            return
        self._is_dirty = True
        self.evaluate()
    
    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """
        Override ThreadedManualNode.evaluate for process-based execution.
        """
        # ── Guards ──────────────────────────────────────────────────
        if self._state == NodeState.DISABLED:
            return
        
        if self._is_computing:
            return
        
        if self._state == NodeState.PASSTHROUGH:
            super(ThreadedManualNode, self).evaluate(visited)
            return
        
        if not self._is_compute_picklable():
            log.error(f"{self}: compute() must be a @staticmethod for multiprocessing")
            return
        
        self._is_computing = True
        
        try:
            inputs = self._prepare_inputs(visited)
        except Exception:
            self._is_computing = False
            return
        
        # ── COMPUTING state ─────────────────────────────────────────
        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                super(ThreadedManualNode, self).set_state(NodeState.COMPUTING)
                if hasattr(self, '_start_computing_pulse'):
                    self._start_computing_pulse()
            except Exception:
                pass
        
        # ── Dispatch ────────────────────────────────────────────────
        self.compute_started.emit()
        self._dispatch_worker(inputs)
        
        # Hold the eval fence (threaded path)
        self._fence_held = True