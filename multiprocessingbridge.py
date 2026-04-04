# -*- coding: utf-8 -*-
"""
Weave Multiprocessing Bridge — Hardened Edition
===============================================
Implements two-tier shutdown (Qt graceful + atexit emergency) and zombie
process prevention via maxtasksperchild and aggressive termination.

Usage:
    import multiprocessing
    multiprocessing.set_start_method('spawn')
    
    from weave.multiprocessing_interface import (
        setup_multiprocessing_cleanup,
        MultiprocessingNode,  # if importing nodes directly
    )
    
    app = QApplication(sys.argv)
    setup_multiprocessing_cleanup(app)  # Registers both Qt and atexit handlers
"""

import multiprocessing as mp
import queue as queue_module
import atexit
import signal
import os
import time
import traceback
import pickle
import sys
import weakref
from typing import Any, Dict, Optional, Callable, List

from PySide6.QtCore import QRunnable, Qt, QObject, Signal, Slot, QCoreApplication
from PySide6.QtWidgets import QApplication

from weave.threadednodes import WorkerSignals as BaseWorkerSignals
from weave.logger import get_logger

log = get_logger("MultiprocessingBridge")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Recycle workers after N tasks to prevent memory leaks (None = unlimited)
MAX_TASKS_PER_CHILD = 100  

# Timeout for graceful pool shutdown (seconds)
POOL_SHUTDOWN_TIMEOUT = 5.0

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNALS
# ═══════════════════════════════════════════════════════════════════════════════

class MPWorkerSignals(BaseWorkerSignals):
    """Extended signals with intermediate result channel."""
    intermediate = Signal(object)


# ═══════════════════════════════════════════════════════════════════════════════
# POOL MANAGER (with atexit safety net)
# ═══════════════════════════════════════════════════════════════════════════════

class _PoolManager:
    """
    Lazy-initialized singleton ProcessPool with emergency cleanup.
    Implements the Hybrid Shutdown Strategy:
      1. Qt aboutToQuit (graceful)
      2. atexit (emergency fallback)
    """
    _instance: Optional[mp.Pool] = None
    _context = None
    _shutdown_initiated: bool = False
    _emergency_cleanup_registered: bool = False
    _active_workers: weakref.WeakSet = weakref.WeakSet()  # Track live workers
    
    @classmethod
    def get_context(cls):
        """Get spawn context (cross-platform safe with Qt)."""
        if cls._context is None:
            cls._context = mp.get_context('spawn')
            # Prevent child processes from inheriting KeyboardInterrupt handlers
            # This is critical for clean subprocess behavior
            cls._context.set_start_method('spawn', force=True)
        return cls._context
    
    @classmethod
    def get_pool(cls, processes: Optional[int] = None) -> mp.Pool:
        """Return global process pool with maxtasksperchild for stability."""
        if cls._instance is None:
            ctx = cls.get_context()
            
            # Initialize pool with worker recycling to prevent memory bloat
            cls._instance = ctx.Pool(
                processes=processes,
                maxtasksperchild=MAX_TASKS_PER_CHILD,
                initializer=cls._init_worker_process,
                initargs=()
            )
            
            log.info(f"Started multiprocessing pool "
                    f"(workers={processes or 'auto'}, "
                    f"maxtasksperchild={MAX_TASKS_PER_CHILD})")
            
            # Register emergency cleanup if not already done
            cls._register_emergency_cleanup()
            
        return cls._instance
    
    @staticmethod
    def _init_worker_process():
        """
        Initialize each worker process.
        - Ignore SIGINT so parent controls shutdown
        - Set process name for easier debugging
        """
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            from setproctitle import setproctitle
            setproctitle("weave_worker")
        except ImportError:
            pass
        log.debug(f"Worker process started (PID: {os.getpid()})")
    
    @classmethod
    def _register_emergency_cleanup(cls):
        """Register atexit handler as safety net (Tier 2)."""
        if not cls._emergency_cleanup_registered:
            atexit.register(cls._emergency_atexit_cleanup)
            cls._emergency_cleanup_registered = True
            log.debug("Registered atexit emergency cleanup handler")
    
    @classmethod
    def shutdown(cls, graceful: bool = True, timeout: float = POOL_SHUTDOWN_TIMEOUT):
        """
        Tier 1: Graceful shutdown (call from Qt aboutToQuit).
        
        Args:
            graceful: If True, uses close()+join(); else terminate()
            timeout: Seconds to wait for workers to finish
        """
        if cls._shutdown_initiated:
            return
        
        cls._shutdown_initiated = True
        log.info(f"Initiating pool shutdown (graceful={graceful})...")
        
        if cls._instance is None:
            return
        
        try:
            # Cancel any running workers first (cooperative)
            for worker_ref in list(cls._active_workers):
                worker = worker_ref()
                if worker is not None and hasattr(worker, '_token'):
                    try:
                        worker._token.cancel()
                    except Exception:
                        pass
            
            if graceful:
                cls._instance.close()
                cls._instance.join(timeout=timeout)
                
                # If workers didn't finish gracefully, escalate to terminate
                if not cls._join_or_terminate(timeout=0.5):
                    log.warning("Graceful shutdown failed, escalating to terminate")
                    cls._instance.terminate()
                    cls._instance.join(timeout=2.0)
            else:
                cls._instance.terminate()
                cls._instance.join(timeout=timeout)
                
        except Exception as e:
            log.error(f"Error during pool shutdown: {e}")
        finally:
            cls._instance = None
            log.info("Pool shutdown complete")
    
    @classmethod
    def _join_or_terminate(cls, timeout: float) -> bool:
        """Helper to check if pool joined successfully."""
        # Poll with timeout
        cls._instance.join(timeout=timeout)
        # Check if any processes are still alive
        if hasattr(cls._instance, '_pool'):
            return not any(p.is_alive() for p in cls._instance._pool)
        return True
    
    @classmethod
    def _emergency_atexit_cleanup(cls):
        """
        Tier 2: Emergency cleanup (atexit fallback).
        Called if Qt didn't exit cleanly but Python is shutting down.
        Uses terminate() for immediate process kill (no patience for zombies).
        """
        if cls._instance is not None and not cls._shutdown_initiated:
            log.warning("EMERGENCY CLEANUP: Terminating orphaned pool via atexit")
            try:
                # Kill immediately - don't wait in atexit context
                cls._instance.terminate()
                # Short wait then force kill if needed
                cls._instance.join(timeout=1.0)
                
                # Force SIGKILL on Unix if still alive
                if sys.platform != 'win32' and hasattr(cls._instance, '_pool'):
                    for p in cls._instance._pool:
                        if p.is_alive():
                            try:
                                os.kill(p.pid, signal.SIGKILL)
                            except Exception:
                                pass
            except Exception as e:
                log.error(f"Emergency cleanup failed: {e}")
            finally:
                cls._instance = None


def get_multiprocessing_pool(processes: Optional[int] = None) -> mp.Pool:
    """Public accessor for the shared process pool."""
    return _PoolManager.get_pool(processes)

def setup_multiprocessing_cleanup(app: QCoreApplication):
    """
    Initialize the Hybrid Shutdown Strategy.
    Call this once after creating QApplication.
    
    Tier 1: Qt graceful shutdown
    Tier 2: atexit emergency fallback (already registered, but we ensure it)
    """
    # Primary: Qt graceful shutdown
    app.aboutToQuit.connect(lambda: _PoolManager.shutdown(graceful=True))
    
    # Verify atexit is registered (Tier 2 fallback)
    _PoolManager._register_emergency_cleanup()
    
    log.debug("Multiprocessing cleanup initialized (Hybrid Strategy)")


# ═══════════════════════════════════════════════════════════════════════════════
# CANCELLATION & INTERMEDIATE RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingCancellationToken:
    """Process-safe cancellation token using mp.Event."""
    __slots__ = ("_event",)
    
    def __init__(self):
        ctx = _PoolManager.get_context()
        self._event = ctx.Event()
    
    def cancel(self) -> None:
        self._event.set()
    
    def is_cancelled(self) -> bool:
        return self._event.is_set()

def is_cancelled_from_inputs(inputs: Dict[str, Any]) -> bool:
    """Check cancellation from subprocess."""
    evt = inputs.get('_cancel_event')
    return evt is not None and evt.is_set()

def emit_intermediate(inputs: Dict[str, Any], results: Dict[str, Any]) -> bool:
    """
    Emit intermediate results from subprocess.
    Non-blocking; returns False if queue is full.
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
    """Convenience wrapper for progress updates."""
    return emit_intermediate(inputs, {'_progress': max(0, min(100, percent))})


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER (with zombie tracking)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingWorker(QRunnable):
    """
    QRunnable wrapper for multiprocessing with intermediate result support.
    Registers itself with PoolManager for emergency tracking.
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
        
        # Register with manager for emergency cleanup
        _PoolManager._active_workers.add(self)
        
        # Setup IPC
        ctx = _PoolManager.get_context()
        self._intermediate_queue: mp.Queue = ctx.Queue(maxsize=100)
        
        # Validate pickling
        try:
            pickle.dumps(compute_fn)
        except (pickle.PicklingError, TypeError, AttributeError) as e:
            raise RuntimeError(f"compute_fn must be picklable: {e}")
    
    def run(self) -> None:
        """Execute in QThreadPool thread."""
        if self._token.is_cancelled():
            self.signals.cancelled.emit()
            self._cleanup()
            return
        
        pool = get_multiprocessing_pool()
        
        # Prepare IPC inputs
        inputs_with_ipc = dict(self._inputs)
        inputs_with_ipc['_cancel_event'] = self._token._event
        inputs_with_ipc['_intermediate_queue'] = self._intermediate_queue
        
        async_result = None
        try:
            async_result = pool.apply_async(self._compute_fn, (inputs_with_ipc,))
            
            # Poll loop: check completion, cancellation, and intermediate queue
            poll_interval = 0.005
            while not async_result.ready():
                # Drain intermediate queue
                self._drain_queue()
                
                if self._token.is_cancelled():
                    self.signals.cancelled.emit()
                    self._cleanup()
                    return
                
                time.sleep(poll_interval)
            
            # Final drain
            self._drain_queue(final=True)
            
            # Get result
            result = async_result.get()
            
            if self._token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                self.signals.finished.emit(result)
                
        except Exception as exc:
            if not self._token.is_cancelled():
                tb = traceback.format_exc()
                self.signals.error.emit(f"{exc}\n{tb}")
        finally:
            self._cleanup()
    
    def _drain_queue(self, final: bool = False):
        """Pull items from intermediate queue."""
        timeout = 0.1 if final else 0.001
        try:
            while True:
                try:
                    data = self._intermediate_queue.get(timeout=timeout if final else 0)
                    self.signals.intermediate.emit(data)
                    if not final:
                        break
                except queue_module.Empty:
                    break
        except Exception:
            pass
    
    def _cleanup(self):
        """Cleanup resources."""
        try:
            self._intermediate_queue.close()
            self._intermediate_queue.join_thread()
        except Exception:
            pass
        # Remove from tracking
        _PoolManager._active_workers.discard(self)


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED MEMORY (with atexit cleanup)
# ═══════════════════════════════════════════════════════════════════════════════

class SharedMemoryHandle:
    """
    Shared memory handle with automatic cleanup registration.
    Prevents shared memory leaks if the app crashes.
    """
    
    _global_handles: List['SharedMemoryHandle'] = []
    
    def __init__(self, shm: 'mp.shared_memory.SharedMemory', name: str, size: int):
        self.shm = shm
        self.name = name
        self.size = size
        self._closed = False
        self._unlink_on_exit = True
        
        # Register for emergency cleanup
        SharedMemoryHandle._global_handles.append(self)
    
    @classmethod
    def create(cls, size: int) -> 'SharedMemoryHandle':
        """Create new shared memory block."""
        ctx = _PoolManager.get_context()
        shm = ctx.shared_memory.SharedMemory(create=True, size=size)
        handle = cls(shm, shm.name, size)
        
        # Register atexit cleanup for this specific segment
        atexit.register(handle._emergency_unlink)
        return handle
    
    def copy_from(self, buffer):
        self.shm.buf[:len(buffer)] = buffer
    
    def to_numpy(self, dtype, shape):
        """View as numpy array (zero-copy)."""
        import numpy as np
        return np.ndarray(shape, dtype=dtype, buffer=self.shm.buf)
    
    def close(self):
        """Close this reference (call from subprocess)."""
        if not self._closed:
            self.shm.close()
            self._closed = True
    
    def unlink(self):
        """Permanently remove (call from main process when done)."""
        if self._unlink_on_exit:
            try:
                self.shm.unlink()
                self._unlink_on_exit = False
            except Exception:
                pass
            # Remove from tracking
            if self in SharedMemoryHandle._global_handles:
                SharedMemoryHandle._global_handles.remove(self)
    
    def _emergency_unlink(self):
        """Emergency cleanup via atexit."""
        if self._unlink_on_exit:
            try:
                self.shm.unlink()
            except Exception:
                pass
    
    def __getstate__(self):
        return {'name': self.name, 'size': self.size}
    
    def __setstate__(self, state):
        self.name = state['name']
        self.size = state['size']
        ctx = mp.get_context('spawn')
        self.shm = ctx.shared_memory.SharedMemory(name=self.name)
        self._closed = False
        self._unlink_on_exit = False  # Only creator unlinks


def check_picklable(obj: Any, name: str = "object") -> bool:
    """Debug helper."""
    try:
        pickle.dumps(obj)
        return True
    except Exception as e:
        log.warning(f"{name} is not picklable: {e}")
        return False