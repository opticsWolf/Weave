# -*- coding: utf-8 -*-
"""
Weave Multiprocessing Nodes
============================
FIXED VERSION: Proper fence token tracking and inheritance.

Key fixes:
- Inherits fence management from ThreadedNode
- Proper token pairing in worker callbacks
- No custom _fenced_evaluate that bypasses base class

This module provides multiprocessing-capable node implementations
that support intermediate result handling, progress reporting,
and proper cancellation mechanisms for long-running computations.
"""

from typing import Optional, Set, Any, Dict

from PySide6.QtCore import Qt, Slot, QTimer

from weave.threadednodes import ThreadedNode, ThreadedManualNode, _HAS_COMPUTING_STATE
from weave.node.node_enums import NodeState
from weave.multiprocessingbridge import (
    MultiprocessingWorker,
    MultiprocessingCancellationToken,
    emit_intermediate,
    report_progress,
    setup_multiprocessing_cleanup,
)

from weave.logger import get_logger
log = get_logger("MultiprocessingNodes")


# ═══════════════════════════════════════════════════════════════════════════════
# BASE MIXIN (intermediate result handling)
# ═══════════════════════════════════════════════════════════════════════════════

class _MultiprocessingMixin:
    """Shared machinery for process-based nodes with intermediate results.
    
    This mixin provides the core functionality for running computations
    in separate processes while supporting intermediate result emission,
    progress reporting, and cancellation.
    
    The mixin handles communication between worker processes and the main thread,
    including managing intermediate results that can be displayed during long-running tasks.
    
    Attributes:
        _cancel_token (Optional[MultiprocessingCancellationToken]): 
            Token used to signal computation cancellation from main thread
        _current_worker (Optional[MultiprocessingWorker]): 
            Reference to current worker instance being managed
    
    Methods:
        _is_compute_picklable: Validates that compute function can be pickled for subprocess
        _prepare_inputs: Gathers all upstream inputs and widget snapshots  
        _dispatch_worker: Creates and starts multiprocessing worker with appropriate signals
        _handle_intermediate_result: Processes intermediate results from worker processes
        is_compute_cancelled: Checks if cancellation has been requested
        cancel_compute: Requests computation cancellation via token
    """
    
    def __init_subclass__(cls, **kwargs):
        """Initialize subclasses to ensure compute method is callable.
        
        This ensures that any class inheriting from _MultiprocessingMixin 
        must implement a callable compute() method. It's essential for the
        multiprocessing functionality to work correctly.
        
        Args:
            **kwargs: Additional keyword arguments passed during class definition
            
        Raises:
            TypeError: If compute attribute exists but is not callable
        """
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'compute') and not callable(cls.compute):
            raise TypeError(f"{cls.__name__}.compute must be callable")
    
    def _is_compute_picklable(self) -> bool:
        """Check if self.compute can be sent to subprocess.
        
        Determines whether the compute function is serializable using pickle,
        which is required for sending functions to separate processes. This
        validation ensures that any closure-based or complex function definitions
        are compatible with multiprocessing.
        
        Returns:
            bool: True if compute method can be pickled, False otherwise
            
        Note:
            Static methods must be unwrapped from their staticmethod wrapper
            before checking pickle compatibility.
        """
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
        """Gather upstream + widget snapshot (runs on main thread).
        
        FIXED: Uses merge-only-if-missing instead of ``dict.update()``
        to match ThreadedNode's behavior.  Connected port values must
        take priority over widget snapshot values.
        
        Args:
            visited: Set of already-visited node IDs to prevent cycles
            
        Returns:
            Dict[str, Any]: Dictionary containing all input parameters for compute function
        """
        try:
            input_params = self._gather_inputs(visited)
        except Exception as e:
            log.error(f"Input gathering failed: {e}")
            raise
        
        widget_snapshot = self.snapshot_widget_inputs()
        if widget_snapshot:
            for key, val in widget_snapshot.items():
                if key not in input_params or input_params[key] is None:
                    input_params[key] = val
        return input_params
    
    def _dispatch_worker(self, inputs: Dict[str, Any]) -> None:
        """Create and start the multiprocessing worker.
        
        FIXED: Properly acquires eval fence via ``_increment_eval_fence``
        and tracks it with ``_worker_fence_token`` so the inherited
        ``_release_worker_fence`` can release it when the worker finishes.
        
        Args:
            inputs: Dictionary of input parameters to pass to compute function
        """
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
        
        # Wire standard signals (inherited from ThreadedNode)
        worker.signals.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.cancelled.connect(self._on_worker_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.compute_progress.emit, Qt.ConnectionType.QueuedConnection)
        
        # Wire intermediate results
        worker.signals.intermediate.connect(self._handle_intermediate_result, Qt.ConnectionType.QueuedConnection)
        
        self._current_worker = worker
        self._thread_pool.start(worker)

    def _start_worker_evaluation(self) -> None:
        """Override ThreadedNode to dispatch via MultiprocessingWorker.

        ThreadedNode.set_dirty schedules ``_start_worker_evaluation``
        which creates a ``ComputeWorker`` (in-process threading).
        Multiprocessing nodes need a ``MultiprocessingWorker`` instead,
        so we override the dispatch here while reusing the same
        barrier, PASSTHROUGH, and fence logic.
        """
        from weave.basenode import BaseControlNode

        self._eval_pending = False

        if not self._is_dirty or self._state == NodeState.DISABLED:
            return

        # Barrier: reschedule if upstream is still busy
        if not self._are_inputs_ready():
            if not self._eval_pending:
                self._eval_pending = True
                QTimer.singleShot(0, self._start_worker_evaluation)
            return

        # PASSTHROUGH → synchronous (no subprocess needed)
        if self._state == NodeState.PASSTHROUGH:
            self._increment_eval_fence()
            try:
                BaseControlNode.evaluate(self, None)
            finally:
                self._decrement_eval_fence()
            return

        # NORMAL → multiprocessing path
        if self._is_computing:
            self._pending_dirty = True
            self.cancel_compute()
            return

        if not self._is_compute_picklable():
            log.error(f"{self}: compute() must be a @staticmethod for multiprocessing")
            return

        self._is_computing = True

        # Acquire fence — released by inherited _release_worker_fence
        self._increment_eval_fence()
        self._worker_fence_token = self._fence_token

        try:
            inputs = self._prepare_inputs(None)
        except Exception:
            self._is_computing = False
            self._release_worker_fence()
            return

        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                from weave.threadednodes import ThreadedNode as _TN
                super(_TN, self).set_state(NodeState.COMPUTING)
                if hasattr(self, '_start_computing_pulse'):
                    self._start_computing_pulse()
            except Exception:
                pass

        self.compute_started.emit()
        self._dispatch_worker(inputs)
    
    @Slot(object)
    def _handle_intermediate_result(self, results: object) -> None:
        """
        Handle intermediate results arriving from the subprocess.
        
        Processes intermediate results sent back by compute functions running
        in separate processes. This allows real-time updates during long-running 
        computations without blocking execution.
        
        Intermediate results can include progress indicators or partial computation
        outputs that should be displayed to users while the main task continues.
        
        Special handling is provided for:
            - '_progress' key: emits compute_progress signal and removes from cache
            - Other keys: Updates cached values and marks downstream nodes dirty
        
        Args:
            results: Dictionary of intermediate result data
            
        Note:
            This method follows the same pattern as ThreadedNode.emit_intermediate
            ensuring consistent behavior across node types.
            
        Warning:
            If results is not a dict or _is_computing is False, no action is taken.
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
        """Main-thread check for cancellation status.
        
        Checks whether a cancellation request has been made for the current computation.
        This method is thread-safe and can be called from any context to determine
        if processing should stop early.
        
        Returns:
            bool: True if computation has been cancelled, False otherwise
            
        Note:
            Uses the _cancel_token to check cancellation status. Returns False 
            if token doesn't exist or hasn't been cancelled.
        """
        return hasattr(self, '_cancel_token') and self._cancel_token.is_cancelled()
    
    def cancel_compute(self) -> None:
        """Request cancellation (sets the mp.Event).
        
        Sends a signal to terminate current computation by setting the 
        multiprocessing cancellation token. This will cause worker processes
        to check for cancellation status periodically in their compute functions.
        
        Note:
            Only attempts cancellation if currently computing and _cancel_token exists.
        """
        if self._is_computing and hasattr(self, '_cancel_token'):
            self._cancel_token.cancel()


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-EVALUATING NODE
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingNode(_MultiprocessingMixin, ThreadedNode):
    """Auto-evaluating node that runs compute() in a subprocess.
    
    This node automatically evaluates when upstream inputs change,
    running the computation function in a separate process to avoid
    blocking the main thread. It supports intermediate result emission 
    and progress reporting during execution.
    
    The node works by:
        1. Checking if the current computation needs cancellation or re-execution
        2. Preparing all input parameters including widget snapshots  
        3. Starting a MultiprocessingWorker with proper signal handling
        4. Managing fence tokens to ensure thread safety
    
    Example usage:
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
    
    Attributes:
        _cancel_token (Optional[MultiprocessingCancellationToken]): 
            Token used to signal computation cancellation from main thread
        _current_worker (Optional[MultiprocessingWorker]): 
            Reference to current worker instance being managed
        
    See Also:
        ThreadedNode: Base class for threaded nodes with automatic evaluation
        MultiprocessingManualNode: For manually-triggered multiprocessing tasks
    """
    
    def __init__(self, title: str = "Multiprocessing Node", **kwargs):
        """Initialize a MultiprocessingNode.
        
        Args:
            title (str): Display name for the node in UI
            **kwargs: Additional keyword arguments passed to base class
            
        Note:
            Initializes cancellation token and inherits fence management 
            from ThreadedNode parent class.
        """
        super().__init__(title, **kwargs)
        self._cancel_token: Optional[MultiprocessingCancellationToken] = None
    
    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """Evaluation entry point for pull model (request_data) and direct calls.

        PASSTHROUGH is handled synchronously via the base class.
        NORMAL mode delegates to ``_start_worker_evaluation`` which
        manages barrier, fence, and multiprocessing dispatch.
        """
        if self._state == NodeState.DISABLED:
            return

        if self._state == NodeState.PASSTHROUGH:
            # Synchronous passthrough via BaseControlNode
            ThreadedNode.evaluate(self, visited)
            return

        # For NORMAL mode, trigger the async multiprocessing path.
        # This is a no-op if already computing or pending.
        if not self._is_computing and self._is_dirty:
            self._start_worker_evaluation()


# ═══════════════════════════════════════════════════════════════════════════════
# MANUAL NODE
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingManualNode(_MultiprocessingMixin, ThreadedManualNode):
    """Button-triggered node with multiprocessing and intermediate results.
    
    This node requires manual triggering via a button click or similar UI action,
    running the computation function in a separate process. It supports
    intermediate result emission during execution for real-time updates.
    
    Unlike auto-evaluating nodes, this type only computes when explicitly 
    requested by the user (e.g., clicking an "Export" or "Process" button).
    
    Example usage:
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
    
    Attributes:
        _cancel_token (Optional[MultiprocessingCancellationToken]): 
            Token used to signal computation cancellation from main thread
        _current_worker (Optional[MultiprocessingWorker]): 
            Reference to current worker instance being managed
        
    See Also:
        ThreadedManualNode: Base class for manual triggering nodes
        MultiprocessingNode: For auto-evaluating multiprocessing tasks
    """
    
    def __init__(self, title: str = "Multiprocessing Manual", **kwargs):
        """Initialize a MultiprocessingManualNode.
        
        Args:
            title (str): Display name for the node in UI
            **kwargs: Additional keyword arguments passed to base class
            
        Note:
            Initializes cancellation token and inherits fence management 
            from ThreadedManualNode parent class.
        """
        super().__init__(title, **kwargs)
        self._cancel_token: Optional[MultiprocessingCancellationToken] = None
    
    def execute(self) -> None:
        """Toggle between start and cancel.
        
        Aligns with ThreadedManualNode.execute() — checks restoring
        state and routes through ``_start_worker_evaluation`` which
        handles multiprocessing dispatch via the mixin override.
        """
        if self._is_restoring():
            log.debug("execute: skipped, canvas is restoring")
            return

        if self._is_computing:
            self.cancel_compute()
            return

        self._is_dirty = True
        self._start_worker_evaluation()
    
    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """Evaluation entry point for manual multiprocessing nodes.

        PASSTHROUGH is handled synchronously via the base class.
        NORMAL mode delegates to ``_start_worker_evaluation`` which
        manages barrier, fence, and multiprocessing dispatch.
        """
        if self._state == NodeState.DISABLED:
            return

        if self._is_computing:
            return

        if self._state == NodeState.PASSTHROUGH:
            ThreadedManualNode.evaluate(self, visited)
            return

        # For NORMAL mode, trigger the async multiprocessing path.
        if self._is_dirty:
            self._start_worker_evaluation()
