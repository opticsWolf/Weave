# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Weave Multiprocessing Bridge
============================
Embeds a multiprocessing.Pool inside the Qt event loop.

Usage in main.py (required for Windows/macOS spawn):
    if __name__ == '__main__':
        import multiprocessing
        multiprocessing.set_start_method('spawn')  # or 'fork' on Linux
        # ... then create QApplication and nodes
"""

import multiprocessing as mp
import time
import traceback
import pickle
from typing import Any, Dict, Optional, Callable

from PySide6.QtCore import QRunnable, Qt, QObject, Signal, Slot

from weave.threadednodes import WorkerSignals  # Re-use existing signal bridge
from weave.logger import get_logger

log = get_logger("MultiprocessingBridge")

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL POOL INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

class _PoolManager:
    """Lazy-initialized singleton ProcessPool."""
    _instance: Optional[mp.Pool] = None
    _context = None
    
    @classmethod
    def get_pool(cls, processes: Optional[int] = None) -> mp.Pool:
        """Return the global process pool (creates on first call)."""
        if cls._instance is None:
            # Use spawn context for cross-platform safety with Qt
            cls._context = mp.get_context('spawn')
            cls._instance = cls._context.Pool(processes=processes)
            log.info(f"Started multiprocessing pool with {processes or 'default'} workers")
        return cls._instance
    
    @classmethod
    def shutdown(cls):
        """Graceful shutdown – call on app exit."""
        if cls._instance:
            cls._instance.close()
            cls._instance.join()
            cls._instance = None
            log.info("Multiprocessing pool shutdown complete")

def get_multiprocessing_pool(processes: Optional[int] = None) -> mp.Pool:
    """Public accessor for the shared process pool."""
    return _PoolManager.get_pool(processes)

# ═══════════════════════════════════════════════════════════════════════════════
# CANCELLATION TOKEN (Process-safe)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingCancellationToken:
    """
    Wraps a multiprocessing.Event for cross-process cancellation.
    Lives in the main process; its internal `_event` is passed to subprocesses.
    """
    __slots__ = ("_event", "_lock")
    
    def __init__(self):
        ctx = mp.get_context('spawn')
        self._event = ctx.Event()
    
    def cancel(self) -> None:
        self._event.set()
    
    def is_cancelled(self) -> bool:
        return self._event.is_set()
    
    def reset(self) -> None:
        self._event.clear()

def is_cancelled_from_inputs(inputs: Dict[str, Any]) -> bool:
    """
    Helper for compute() functions running in the subprocess.
    Checks the injected cancellation event.
    """
    evt = inputs.get('_cancel_event')
    return evt is not None and evt.is_set()

# ═══════════════════════════════════════════════════════════════════════════════
# WORKER (QRunnable that monitors a Process)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiprocessingWorker(QRunnable):
    """
    QRunnable that submits work to the global multiprocessing.Pool
    and bridges signals back to the main thread.
    
    Runs in QThreadPool (so Qt tracks it), but the payload runs in mp.Pool.
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
        self.signals = WorkerSignals()
        
        # Validate that the function is picklable before dispatch
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
            return
        
        pool = get_multiprocessing_pool()
        
        # Inject the cancel event into inputs so subprocess can check it
        inputs_with_event = dict(self._inputs)
        inputs_with_event['_cancel_event'] = self._token._event
        
        try:
            # Submit to process pool
            async_result = pool.apply_async(
                self._compute_fn,
                (inputs_with_event,)  # Tuple args
            )
            
            # Poll with timeout so we remain responsive to Qt thread pool
            # and can honour cancellation requests from the main thread
            while not async_result.ready():
                if self._token.is_cancelled():
                    # Soft cancellation: we stop waiting; subprocess continues
                    # but its result will be discarded
                    self.signals.cancelled.emit()
                    return
                # Prevent CPU spin without blocking the QThread for too long
                time.sleep(0.005)
            
            # Retrieve result (raises if compute() raised in subprocess)
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

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def check_picklable(obj: Any, name: str = "object") -> bool:
    """Debug helper: returns True if obj can be pickled."""
    try:
        pickle.dumps(obj)
        return True
    except Exception as e:
        log.warning(f"{name} is not picklable: {e}")
        return False