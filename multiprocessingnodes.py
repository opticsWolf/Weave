# -*- coding: utf-8 -*-
"""
Weave Multiprocessing Nodes
============================
Auto-evaluating and manual nodes with full intermediate result support.

Key features:
    - emit_intermediate(inputs, results) from within compute()
    - report_progress(inputs, percent) helper for progress bars
    - Shared memory utilities for large data
"""

from typing import Optional, Set, Any, Dict

from PySide6.QtCore import Qt, Slot

from weave.threadednodes import ThreadedNode, ThreadedManualNode, _HAS_COMPUTING_STATE
from weave.node.node_enums import NodeState
from weave.multiprocessingbridge import (
    MultiprocessingWorker,
    MultiprocessingCancellationToken,
    emit_intermediate,  # Exported for user convenience
    report_progress,    # Exported for user convenience
    setup_multiprocessing_cleanup,
)

from weave.logger import get_logger
log = get_logger("MultiprocessingNodes")


# ═══════════════════════════════════════════════════════════════════════════════
# BASE MIXIN (intermediate result handling)
# ═══════════════════════════════════════════════════════════════════════════════

class _MultiprocessingMixin:
    """Shared machinery for process-based nodes with intermediate results."""
    
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'compute') and not callable(cls.compute):
            raise TypeError(f"{cls.__name__}.compute must be callable")
    
    def _is_compute_picklable(self) -> bool:
        """Check if self.compute can be sent to subprocess."""
        import pickle
        try:
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
        """Create and start the multiprocessing worker with intermediate support."""
        self._cancel_token = MultiprocessingCancellationToken()
        
        # Resolve static function
        compute_fn = self.compute
        if isinstance(compute_fn, staticmethod):
            compute_fn = compute_fn.__func__
        
        worker = MultiprocessingWorker(
            compute_fn=compute_fn,
            inputs=inputs,
            cancel_token=self._cancel_token,
        )
        
        # Wire standard signals
        worker.signals.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.cancelled.connect(self._on_worker_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.compute_progress.emit, Qt.ConnectionType.QueuedConnection)
        
        # Wire intermediate results (NEW)
        worker.signals.intermediate.connect(self._handle_intermediate_result, Qt.ConnectionType.QueuedConnection)
        
        self._current_worker = worker
        self._thread_pool.start(worker)
    
    @Slot(object)
    def _handle_intermediate_result(self, results: object) -> None:
        """
        Handle intermediate results arriving from the subprocess.
        Uses the same pathway as ThreadedNode.emit_intermediate.
        """
        if not self._is_computing or not isinstance(results, dict):
            return
        
        # Handle special _progress key by emitting compute_progress signal
        if '_progress' in results:
            self.compute_progress.emit(results['_progress'])
            # Don't store _progress in cache unless user has a port named '_progress'
            results = {k: v for k, v in results.items() if k != '_progress'}
            if not results:
                return
        
        # Use the inherited intermediate handling from ThreadedNode
        # which updates cache and marks downstream dirty
        if hasattr(self, '_on_intermediate_results'):
            self._on_intermediate_results(results)
        else:
            # Fallback: manual cache update
            import time as _time
            timestamp = _time.time()
            from weave.basenode import CacheEntry
            for port_name, value in results.items():
                self._cached_values[port_name] = CacheEntry(
                    value=value,
                    is_valid=True,
                    timestamp=timestamp,
                    source_state=NodeState.COMPUTING,
                )
            self._mark_downstream_dirty("intermediate_update")
            if hasattr(self, 'data_updated'):
                self.data_updated.emit()
    
    def is_compute_cancelled(self) -> bool:
        """Main-thread check for cancellation status."""
        return hasattr(self, '_cancel_token') and self._cancel_token.is_cancelled()
    
    def cancel_compute(self) -> None:
        """Request cancellation (sets the mp.Event)."""
        if self._is_computing and hasattr(self, '_cancel_token'):
            self._cancel_token.cancel()


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-EVALUATING NODE
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingNode(_MultiprocessingMixin, ThreadedNode):
    """
    Auto-evaluating node that runs compute() in a subprocess.
    
    Supports emit_intermediate() from within compute() to push partial results
    back to the main thread while the subprocess continues working.
    
    Example:
        @register_node
        class ImageProcessNode(MultiprocessingNode):
            def __init__(self, **kw):
                super().__init__("Image Process", **kw)
                self.add_input("image", "ndarray")
                self.add_output("result", "ndarray")
                self.add_output("progress", "float")
            
            @staticmethod
            def compute(inputs):
                img = inputs["image"]
                h, w = img.shape[:2]
                
                for y in range(h):
                    if is_cancelled_from_inputs(inputs):
                        return {"result": None, "status": "cancelled"}
                    
                    # Process row...
                    row_result = img[y] * 2
                    
                    # Emit progress every 10 rows
                    if y % 10 == 0:
                        report_progress(inputs, int(100 * y / h))
                        emit_intermediate(inputs, {"progress": y / h})
                
                return {"result": processed}
    """
    
    def __init__(self, title: str = "Multiprocessing Node", **kwargs):
        super().__init__(title, **kwargs)
        self._cancel_token: Optional[MultiprocessingCancellationToken] = None
    
    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """Override to use MultiprocessingWorker."""
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
            super(ThreadedNode, self).evaluate(visited)
            return
        
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
        
        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                super(ThreadedNode, self).set_state(NodeState.COMPUTING)
                if hasattr(self, '_start_computing_pulse'):
                    self._start_computing_pulse()
            except Exception:
                pass
        
        self.compute_started.emit()
        self._dispatch_worker(inputs)


# ═══════════════════════════════════════════════════════════════════════════════
# MANUAL NODE
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingManualNode(_MultiprocessingMixin, ThreadedManualNode):
    """
    Button-triggered node with multiprocessing and intermediate results.
    
    Example:
        class ExportNode(MultiprocessingManualNode):
            def __init__(self, **kw):
                super().__init__("Heavy Export", **kw)
                self.btn.clicked.connect(self.execute)
            
            @staticmethod
            def compute(inputs):
                frames = inputs["frames"]
                for i, frame in enumerate(frames):
                    if is_cancelled_from_inputs(inputs):
                        return {"status": "cancelled"}
                    
                    export_frame(frame)
                    emit_intermediate(inputs, {"exported_count": i + 1})
                
                return {"status": "complete"}
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
        """Override to use MultiprocessingWorker."""
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
        
        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                super(ThreadedManualNode, self).set_state(NodeState.COMPUTING)
                if hasattr(self, '_start_computing_pulse'):
                    self._start_computing_pulse()
            except Exception:
                pass
        
        self.compute_started.emit()
        self._dispatch_worker(inputs)
        self._fence_held = True