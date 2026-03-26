# -*- coding: utf-8 -*-
"""
Weave Multiprocessing Bridge
============================
Embeds a multiprocessing.Pool inside the Qt event loop with support for
intermediate results and automatic cleanup.

Usage in main.py:
    import multiprocessing
    multiprocessing.set_start_method('spawn')
    
    from weave.multiprocessing_interface import setup_multiprocessing_cleanup
    app = QApplication(sys.argv)
    setup_multiprocessing_cleanup(app)  # Auto-shutdown on quit
"""

import multiprocessing as mp
import queue as queue_module
import time
import traceback
import pickle
import sys
from typing import Any, Dict, Optional, Callable

from PySide6.QtCore import QRunnable, Qt, QObject, Signal, Slot, QCoreApplication
from PySide6.QtWidgets import QApplication

# Extend base WorkerSignals to include intermediate results
from weave.threadednodes import WorkerSignals as BaseWorkerSignals

from weave.logger import get_logger

log = get_logger("MultiprocessingBridge")

# ═══════════════════════════════════════════════════════════════════════════════
# EXTENDED SIGNALS (adds intermediate result channel)
# ═══════════════════════════════════════════════════════════════════════════════

class MPWorkerSignals(BaseWorkerSignals):
    """
    Signals for multiprocessing worker, extending base WorkerSignals
    with intermediate result passing.
    """
    intermediate = Signal(object)  # Dict[str, Any] of partial results


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL POOL INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

class _PoolManager:
    """Lazy-initialized singleton ProcessPool."""
    _instance: Optional[mp.Pool] = None
    _context = None
    
    @classmethod
    def get_context(cls):
        """Get the spawn context (cross-platform safe with Qt)."""
        if cls._context is None:
            cls._context = mp.get_context('spawn')
        return cls._context
    
    @classmethod
    def get_pool(cls, processes: Optional[int] = None) -> mp.Pool:
        """Return the global process pool (creates on first call)."""
        if cls._instance is None:
            ctx = cls.get_context()
            cls._instance = ctx.Pool(processes=processes)
            log.info(f"Started multiprocessing pool with {processes or 'default'} workers")
        return cls._instance
    
    @classmethod
    def shutdown(cls):
        """Graceful shutdown – call on app exit."""
        if cls._instance:
            log.info("Shutting down multiprocessing pool...")
            cls._instance.close()
            cls._instance.join()
            cls._instance = None
            log.info("Multiprocessing pool shutdown complete")

def get_multiprocessing_pool(processes: Optional[int] = None) -> mp.Pool:
    """Public accessor for the shared process pool."""
    return _PoolManager.get_pool(processes)

def setup_multiprocessing_cleanup(app: QCoreApplication):
    """
    Connect pool shutdown to QApplication.aboutToQuit.
    Call this once after creating QApplication.
    """
    app.aboutToQuit.connect(_PoolManager.shutdown)
    log.debug("Multiprocessing cleanup hooked to aboutToQuit")


# ═══════════════════════════════════════════════════════════════════════════════
# CANCELLATION & INTERMEDIATE RESULTS API
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingCancellationToken:
    """
    Wraps a multiprocessing.Event for cross-process cancellation.
    """
    __slots__ = ("_event",)
    
    def __init__(self):
        ctx = _PoolManager.get_context()
        self._event = ctx.Event()
    
    def cancel(self) -> None:
        self._event.set()
    
    def is_cancelled(self) -> bool:
        return self._event.is_set()
    
    def reset(self) -> None:
        self._event.clear()

def is_cancelled_from_inputs(inputs: Dict[str, Any]) -> bool:
    """Check cancellation from inside the subprocess compute()."""
    evt = inputs.get('_cancel_event')
    return evt is not None and evt.is_set()

def emit_intermediate(inputs: Dict[str, Any], results: Dict[str, Any]) -> bool:
    """
    Emit intermediate results from inside the subprocess compute().
    
    Args:
        inputs: The inputs dict passed to compute()
        results: Dict mapping output port names to current intermediate values
    
    Returns:
        True if successfully queued, False if queue is full/closed
    
    Example (inside compute()):
        for i, chunk in enumerate(data):
            if is_cancelled_from_inputs(inputs):
                return None
            
            partial_result = process_chunk(chunk)
            emit_intermediate(inputs, {"preview": partial_result, "progress": i})
        
        return {"result": final_data}
    """
    q = inputs.get('_intermediate_queue')
    if q is not None:
        try:
            q.put_nowait(results)
            return True
        except (queue_module.Full, AttributeError, BrokenPipeError):
            pass
    return False

def report_progress(inputs: Dict[str, Any], percent: int) -> bool:
    """
    Convenience wrapper to emit progress as an intermediate result.
    Downstream nodes receive this as a normal output named '_progress'.
    """
    return emit_intermediate(inputs, {'_progress': max(0, min(100, percent))})


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER (QRunnable that monitors a Process + Queue)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingWorker(QRunnable):
    """
    QRunnable that submits work to the global multiprocessing.Pool.
    Monitors both the async result and the intermediate results queue.
    """
    
    def __init__(
        self,
        compute_fn: Callable[[Dict[str, Any]], Any],
        inputs: Dict[str, Any],
        cancel_token: MultiprocessingCancellationToken,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self._compute_fn = compute_fn
        self._inputs = inputs
        self._token = cancel_token
        self.signals = MPWorkerSignals()
        
        # Create intermediate results queue (process-safe)
        ctx = _PoolManager.get_context()
        self._intermediate_queue: mp.Queue = ctx.Queue(maxsize=100)  # Buffer up to 100 updates
        
        # Validate pickling early
        try:
            pickle.dumps(compute_fn)
        except (pickle.PicklingError, TypeError, AttributeError) as e:
            raise RuntimeError(
                f"compute_fn must be picklable (use @staticmethod or module function). "
                f"Original error: {e}"
            )
    
    def run(self) -> None:
        """Executes in QThreadPool thread."""
        if self._token.is_cancelled():
            self.signals.cancelled.emit()
            self._cleanup_queue()
            return
        
        pool = get_multiprocessing_pool()
        
        # Prepare inputs with IPC mechanisms
        inputs_with_ipc = dict(self._inputs)
        inputs_with_ipc['_cancel_event'] = self._token._event
        inputs_with_ipc['_intermediate_queue'] = self._intermediate_queue
        
        async_result = None
        try:
            async_result = pool.apply_async(
                self._compute_fn,
                (inputs_with_ipc,)
            )
            
            # Poll loop: check for completion, cancellation, and intermediate results
            while not async_result.ready():
                # 1. Drain intermediate queue (non-blocking)
                self._drain_intermediate_queue()
                
                # 2. Check cancellation
                if self._token.is_cancelled():
                    self.signals.cancelled.emit()
                    # Note: Subprocess continues running but results are discarded
                    self._cleanup_queue()
                    return
                
                # 3. Brief sleep to prevent CPU spin
                time.sleep(0.005)
            
            # Final drain of any remaining intermediate results
            self._drain_intermediate_queue(final=True)
            
            # Get final result (raises if compute() raised in subprocess)
            result = async_result.get()
            
            if self._token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                self.signals.finished.emit(result)
                
        except Exception as exc:
            if self._token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                tb = traceback.format_exc()
                self.signals.error.emit(f"{exc}\n{tb}")
        finally:
            self._cleanup_queue()
    
    def _drain_intermediate_queue(self, final: bool = False):
        """
        Pull items from the intermediate queue and emit signals.
        If final=True, tries harder to get all remaining items.
        """
        timeout = 0.1 if final else 0.001
        try:
            while True:
                try:
                    data = self._intermediate_queue.get(timeout=timeout if final else 0)
                    self.signals.intermediate.emit(data)
                    if not final:
                        break  # One item per check during main loop to stay responsive
                except queue_module.Empty:
                    break
        except Exception:
            pass
    
    def _cleanup_queue(self):
        """Close the queue to prevent resource warnings."""
        try:
            self._intermediate_queue.close()
            self._intermediate_queue.join_thread()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED MEMORY UTILITIES (for large data)
# ═══════════════════════════════════════════════════════════════════════════════

class SharedMemoryHandle:
    """
    Wrapper for multiprocessing.shared_memory to pass large arrays between processes
    without pickling overhead.
    
    Usage:
        # In main process:
        shm = SharedMemoryHandle.create(arr.nbytes)
        shm.copy_from(arr)
        inputs['image_shm'] = shm
        
        # In compute():
        shm = inputs['image_shm']
        arr = shm.to_numpy(dtype=np.float32, shape=(1024, 1024, 3))
        # ... process ...
        shm.close()  # Important!
    """
    
    def __init__(self, shm: 'mp.shared_memory.SharedMemory', name: str, size: int):
        self.shm = shm
        self.name = name
        self.size = size
    
    @classmethod
    def create(cls, size: int) -> 'SharedMemoryHandle':
        """Create a new shared memory block."""
        ctx = _PoolManager.get_context()
        shm = ctx.shared_memory.SharedMemory(create=True, size=size)
        return cls(shm, shm.name, size)
    
    def copy_from(self, buffer):
        """Copy data from buffer into shared memory."""
        self.shm.buf[:len(buffer)] = buffer
    
    def to_bytes(self) -> bytes:
        """Return contents as bytes."""
        return bytes(self.shm.buf)
    
    def close(self):
        """Release this reference (call from subprocess when done)."""
        self.shm.close()
    
    def unlink(self):
        """Permanently remove the shared memory (call from main process)."""
        self.shm.unlink()
    
    def __getstate__(self):
        """For pickling - only send name and size, not the buffer."""
        return {'name': self.name, 'size': self.size}
    
    def __setstate__(self, state):
        """Reattach to existing shared memory in subprocess."""
        self.name = state['name']
        self.size = state['size']
        ctx = mp.get_context('spawn')
        self.shm = ctx.shared_memory.SharedMemory(name=self.name)


def check_picklable(obj: Any, name: str = "object") -> bool:
    """Debug helper: returns True if obj can be pickled."""
    try:
        pickle.dumps(obj)
        return True
    except Exception as e:
        log.warning(f"{name} is not picklable: {e}")
        return False