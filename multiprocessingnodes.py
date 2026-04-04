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

from PySide6.QtCore import Qt, Slot

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
        
        This method collects all input parameters from connected nodes and 
        captures the current state of any widgets that are part of this node.
        It ensures that all required data is available to the subprocess worker
        when executing computations.
        
        Args:
            visited: Set of already-visited node IDs to prevent cycles
            
        Returns:
            Dict[str, Any]: Dictionary containing all input parameters for compute function
            
        Raises:
            Exception: If gathering inputs fails, re-raises caught exception with logging
        """
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
        """Create and start the multiprocessing worker.
        
        Initializes a new MultiprocessingWorker with the compute function,
        input parameters, and cancellation token. Connects all necessary signals
        for handling completion, errors, progress updates, and intermediate results.
        
        This method also sets up proper fence management by marking that the 
        worker owns its own fence token (inherited from ThreadedNode).
        
        Args:
            inputs: Dictionary of input parameters to pass to compute function
            
        Note:
            The _worker_has_fence flag is set to True after starting the worker
            ensuring proper synchronization with thread-based nodes.
            
        Raises:
            Exception: If worker creation or signal connection fails
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
        
        # Wire standard signals
        worker.signals.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.cancelled.connect(self._on_worker_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.compute_progress.emit, Qt.ConnectionType.QueuedConnection)
        
        # Wire intermediate results
        worker.signals.intermediate.connect(self._handle_intermediate_result, Qt.ConnectionType.QueuedConnection)
        
        self._current_worker = worker
        
        # FIXED: Mark that worker owns the fence (inherited from ThreadedNode)
        self._worker_has_fence = True
        
        self._thread_pool.start(worker)
    
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
        """Override to use MultiprocessingWorker with proper fencing.
        
        This method handles the evaluation lifecycle for auto-evaluating nodes,
        including checking cancellation status, preparing inputs, setting up 
        state tracking, and launching the subprocess worker.
        
        The implementation ensures proper fence management by:
            - Setting _worker_has_fence flag in _dispatch_worker
            - Using inherited ThreadedNode.evaluate() path when passthrough
            
        Args:
            visited: Set of already-visited node IDs to prevent cycles (optional)
            
        Note:
            If compute function is not picklable, logs error and exits early.
            Handles state transitions through COMPUTING if _HAS_COMPUTING_STATE enabled.
            
        Raises:
            Exception: If input preparation fails during computation
        """
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
            # Use ThreadedNode's parent (BaseControlNode) for sync path
            ThreadedNode.evaluate(self, visited)
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
        # _worker_has_fence set in _dispatch_worker


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
        
        This method handles the UI interaction for manual nodes. When called,
        if currently computing it cancels execution; otherwise it sets dirty
        flag and calls evaluate() to begin computation.
        
        Note:
            Uses self._is_computing to determine whether to cancel or start.
        """
        if self._is_computing:
            self.cancel_compute()
            return
        self._is_dirty = True
        self.evaluate()
    
    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """Override to use MultiprocessingWorker with proper fencing.
        
        This method handles the evaluation lifecycle for manual nodes,
        including checking if already computing, preparing inputs, and 
        launching the subprocess worker.
        
        The implementation ensures proper fence management by:
            - Setting _worker_has_fence flag in _dispatch_worker
            - Using inherited ThreadedManualNode.evaluate() path when passthrough
            
        Args:
            visited: Set of already-visited node IDs to prevent cycles (optional)
            
        Note:
            If compute function is not picklable, logs error and exits early.
            Handles state transitions through COMPUTING if _HAS_COMPUTING_STATE enabled.
            
        Raises:
            Exception: If input preparation fails during computation
        """
        if self._state == NodeState.DISABLED:
            return
        
        if self._is_computing:
            return
        
        if self._state == NodeState.PASSTHROUGH:
            ThreadedManualNode.evaluate(self, visited)
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
        # _worker_has_fence set in _dispatch_worker
