# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

threadednodes.py — Threaded Node Base Classes
-------------------------------------------------
Provides background-compute node bases using QThreadPool for heavy workloads.

Architecture:
    BaseControlNode (existing, synchronous)
    ├── ActiveNode       — auto-eval, no threading (simple nodes)
    ├── ManualNode       — button-triggered, no threading (simple actions)
    ├── ThreadedNode     — auto-eval with QThreadPool (heavy compute)
    └── ThreadedManualNode — button-triggered with QThreadPool + cancel

Key Concepts:
    1. Only compute() runs off-thread.  Input gathering and result
       application always happen on the main thread.
    2. Cooperative cancellation via CancellationToken — check
       self.is_compute_cancelled() periodically inside compute().
    3. Widget values are snapshotted on the main thread BEFORE dispatch
       so compute() never touches Qt widgets.  Override
       snapshot_widget_inputs() to provide widget values.
    4. COMPUTING state is set while the worker is running.  The
       pre-compute state is restored when the worker finishes.

Prerequisites:
    Add COMPUTING to your NodeState enum in qt_nodecomponents.py:

        class NodeState(Enum):
            NORMAL      = auto()
            DISABLED    = auto()
            PASSTHROUGH = auto()
            COMPUTING   = auto()     # ← add this

Usage Examples:

    # ── Heavy auto-eval node ──────────────────────────────────────
    @register_node
    class ImageBlurNode(ThreadedNode):
        node_class  = "Image"
        node_subclass = "Filter"

        def __init__(self, **kw):
            super().__init__("Image Blur", **kw)
            self.add_input("image", "QImage")
            self.add_input("radius", "float")
            self.add_output("result", "QImage")

            self.spin = QDoubleSpinBox()
            self.spin.setValue(5.0)
            self.spin.valueChanged.connect(self.on_ui_change)
            self.set_content_widget(self.spin)

        def snapshot_widget_inputs(self):
            # Called on MAIN thread before dispatch
            return {"_radius_ui": self.spin.value()}

        def compute(self, inputs):
            image  = inputs.get("image")
            radius = inputs.get("radius") or inputs.get("_radius_ui", 5.0)
            if image is None:
                return {"result": None}

            # Long loop with cancellation check
            for y in range(image.height()):
                if self.is_compute_cancelled():
                    return {"result": None}
                blur_row(image, y, radius)

            return {"result": image}

    # ── Heavy manual node ─────────────────────────────────────────
    @register_node
    class ExportNode(ThreadedManualNode):
        node_class    = "IO"
        node_subclass = "Export"

        def __init__(self, **kw):
            super().__init__("Exporter", **kw)
            self.add_input("data", "any")
            self.add_output("status", "string")

            # execute() / cancel are already wired — just connect a button
            self.btn = QPushButton("Export")
            self.btn.clicked.connect(self.execute)
            self.set_content_widget(self.btn)

        def compute(self, inputs):
            data = inputs.get("data")
            for i, chunk in enumerate(data):
                if self.is_compute_cancelled():
                    return {"status": "Cancelled"}
                self.report_progress(int(100 * i / len(data)))
                write_chunk(chunk)
            return {"status": "Done"}
"""

from __future__ import annotations

import time
import threading
import traceback
from typing import Any, Dict, Optional, Set, ClassVar

from PySide6.QtCore import (
    Qt, QObject, Signal, Slot, QRunnable, QThreadPool, QTimer,
)

from weave.basenode import BaseControlNode, NodeDataFlow, CacheEntry
from weave.node.node_subcomponents import NodeState

from weave.logger import get_logger
log = get_logger("ThreadedNode")


# ═══════════════════════════════════════════════════════════════════════════════
# COMPUTING STATE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_HAS_COMPUTING_STATE: bool = hasattr(NodeState, "COMPUTING")

if not _HAS_COMPUTING_STATE:
    import warnings
    warnings.warn(
        "[qt_threadednodes] NodeState.COMPUTING not found. "
        "Add  COMPUTING = auto()  to your NodeState enum in qt_nodecomponents.py. "
        "Threading will work but the COMPUTING visual state will not be applied.",
        stacklevel=2,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CANCELLATION TOKEN
# ═══════════════════════════════════════════════════════════════════════════════

class CancellationToken:
    """
    Thread-safe cooperative cancellation flag.

    Nodes check ``is_cancelled()`` periodically inside ``compute()``
    to exit early when the user or system requests cancellation.

    Each new worker dispatch creates a fresh token; the old token
    is cancelled first so any lingering worker exits promptly.
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    # ── Public API ────────────────────────────────────────────────
    def cancel(self) -> None:
        """Signal cancellation.  Thread-safe."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Check the flag.  Thread-safe, O(1)."""
        return self._event.is_set()

    def reset(self) -> None:
        """Clear the flag for reuse (prefer creating a new token instead)."""
        self._event.clear()

    def __bool__(self) -> bool:
        """``if token:`` → True means *cancelled*."""
        return self._event.is_set()


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER SIGNALS  (QRunnable cannot emit signals — bridge via QObject)
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerSignals(QObject):
    """
    Signal bridge for :class:`ComputeWorker`.

    Signals:
        finished(dict)   — compute results dict
        error(str)       — formatted error message
        progress(int)    — optional 0-100 progress
        cancelled()      — worker exited due to cancellation
    """
    finished  = Signal(dict)
    error     = Signal(str)
    progress  = Signal(int)
    cancelled = Signal()


# ═══════════════════════════════════════════════════════════════════════════════
# COMPUTE WORKER  (QRunnable — executes on QThreadPool)
# ═══════════════════════════════════════════════════════════════════════════════

class ComputeWorker(QRunnable):
    """
    Wraps a node's ``compute(inputs)`` call in a :class:`QRunnable`
    for thread-pool execution.

    Lifecycle:
        1. Created on the **main thread** with pre-gathered inputs.
        2. ``run()`` executes on a **worker thread**.
        3. Results (or errors) are delivered back to the main thread
           via :attr:`signals`.

    The worker checks the *cancel_token* before starting and after
    ``compute()`` finishes.  For finer-grained cancellation the node's
    ``compute()`` should poll ``is_compute_cancelled()`` itself.
    """

    def __init__(
        self,
        compute_fn,
        inputs: Dict[str, Any],
        cancel_token: CancellationToken,
        progress_fn=None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)

        self._compute_fn   = compute_fn
        self._inputs       = inputs
        self._cancel_token = cancel_token
        self._progress_fn  = progress_fn
        self.signals       = WorkerSignals()

    # ── QRunnable interface (called on worker thread) ─────────────
    def run(self) -> None:  # noqa: D102
        if self._cancel_token.is_cancelled():
            self.signals.cancelled.emit()
            return

        try:
            results = self._compute_fn(self._inputs)

            if self._cancel_token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                self.signals.finished.emit(results if isinstance(results, dict) else {})

        except Exception as exc:
            if self._cancel_token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                tb = traceback.format_exc()
                self.signals.error.emit(f"{exc}\n{tb}")


# ═══════════════════════════════════════════════════════════════════════════════
# THREADED NODE  (auto-evaluate, background compute)
# ═══════════════════════════════════════════════════════════════════════════════

class ThreadedNode(BaseControlNode):
    """
    Auto-evaluating node whose ``compute()`` runs on :class:`QThreadPool`.

    Inherits everything from :class:`BaseControlNode` and only overrides
    the evaluate pipeline.  Simple rules:

    * **Never** touch Qt widgets inside ``compute()``.
    * **Override** ``snapshot_widget_inputs()`` to read widget values
      before dispatch.  The returned dict is merged into ``inputs``.
    * **Poll** ``self.is_compute_cancelled()`` in long loops.
    * Everything else (ports, state machine, caching, downstream
      propagation) works identically to synchronous nodes.

    Signals:
        compute_started()     — worker dispatched
        compute_finished()    — results applied to cache
        compute_cancelled()   — worker was cancelled before completion
        compute_error(str)    — worker raised an exception
        compute_progress(int) — optional 0-100 progress
    """

    # ── Signals ───────────────────────────────────────────────────
    compute_started   = Signal()
    compute_finished  = Signal()
    compute_cancelled = Signal()
    compute_error     = Signal(str)
    compute_progress  = Signal(int)

    def __init__(self, title: str = "Threaded Node", **kwargs: Any) -> None:
        super().__init__(title, **kwargs)
        self._manual_mode = False

        # ── Threading state ──
        self._cancel_token: CancellationToken = CancellationToken()
        self._current_worker: Optional[ComputeWorker] = None
        self._thread_pool: QThreadPool = QThreadPool.globalInstance()
        self._pending_dirty: bool = False
        self._pre_compute_state: Optional[NodeState] = None

    # ──────────────────────────────────────────────────────────────
    # CANCELLATION API  (usable from any thread)
    # ──────────────────────────────────────────────────────────────

    def is_compute_cancelled(self) -> bool:
        """
        Check whether the current compute has been cancelled.

        **Call this periodically inside** ``compute()`` and return
        early (with partial or empty results) when it returns ``True``.
        """
        return self._cancel_token.is_cancelled()

    def cancel_compute(self) -> None:
        """
        Request cancellation of the running worker.

        This sets the cancellation flag.  The worker will exit at the
        next ``is_compute_cancelled()`` check (cooperative cancellation).
        """
        if self._is_computing:
            self._cancel_token.cancel()

    # ──────────────────────────────────────────────────────────────
    # PROGRESS REPORTING  (call from inside compute())
    # ──────────────────────────────────────────────────────────────

    def report_progress(self, percent: int) -> None:
        """
        Emit progress from inside ``compute()``.

        This uses the worker's signal bridge so it is safe to call
        from the worker thread.  The ``compute_progress`` signal is
        emitted on the main thread.

        Args:
            percent: Progress value, 0 – 100.
        """
        worker = self._current_worker
        if worker is not None:
            worker.signals.progress.emit(max(0, min(100, percent)))

    # ──────────────────────────────────────────────────────────────
    # WIDGET SNAPSHOT HOOK  (override in subclass)
    # ──────────────────────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """
        Capture widget values on the **main thread** before the worker
        is dispatched.

        Override this in your subclass to return a dict of widget
        values.  The dict is merged into the ``inputs`` argument
        that ``compute()`` receives.

        Returns:
            A dict of key-value pairs (may be empty).

        Example::

            def snapshot_widget_inputs(self):
                return {"_blur_radius": self.spin.value()}
        """
        # Default: delegate to get_widget_state if available
        if hasattr(self, "get_widget_state"):
            try:
                return self.get_widget_state()
            except Exception:
                pass
        return {}

    # ──────────────────────────────────────────────────────────────
    # EVALUATE  (overrides synchronous BaseControlNode.evaluate)
    # ──────────────────────────────────────────────────────────────

    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """
        Threaded evaluate pipeline.

        Phase 1 (main thread):
            Gather upstream inputs, snapshot widget values.
        Phase 2 (worker thread):
            Run ``compute(inputs)`` in the thread pool.
        Phase 3 (main thread, via signal):
            Apply results to cache, propagate downstream.
        """
        # ── 1. State guard ────────────────────────────────────────
        if self._state == NodeState.DISABLED:
            return

        if _HAS_COMPUTING_STATE and self._state == NodeState.COMPUTING:
            # Already computing — queue a re-eval after the current one finishes
            self._pending_dirty = True
            return

        # ── 2. If already computing, cancel and re-dispatch later ─
        if self._is_computing:
            self._pending_dirty = True
            self.cancel_compute()
            return

        # ── 3. Passthrough runs synchronously (trivial) ──────────
        if self._state == NodeState.PASSTHROUGH:
            # Passthrough is a direct forwarding — no need for a thread
            super().evaluate(visited)
            return

        self._is_computing = True

        # ── 4. Phase 1: gather inputs (main thread) ──────────────
        try:
            input_params = self._gather_inputs(visited)
        except Exception as e:
            log.error(f"Input gathering failed: {e}")
            self._is_computing = False
            return

        # Snapshot widget values (main thread, safe)
        widget_snapshot = self.snapshot_widget_inputs()
        if widget_snapshot:
            input_params.update(widget_snapshot)

        # ── 5. Set COMPUTING state ────────────────────────────────
        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                # Direct attribute set to avoid triggering full state
                # transition logic (COMPUTING is transient, not user-set)
                super(BaseControlNode, self).set_state(NodeState.COMPUTING)
            except Exception:
                pass

        # ── 6. Create fresh cancellation token & worker ───────────
        self._cancel_token = CancellationToken()

        worker = ComputeWorker(
            compute_fn=self.compute,
            inputs=input_params,
            cancel_token=self._cancel_token,
        )

        # Wire signals (queued connection — callbacks run on main thread)
        worker.signals.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.cancelled.connect(self._on_worker_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.compute_progress.emit, Qt.ConnectionType.QueuedConnection)

        self._current_worker = worker

        # ── 7. Dispatch ───────────────────────────────────────────
        self.compute_started.emit()
        self._thread_pool.start(worker)

    # ──────────────────────────────────────────────────────────────
    # WORKER CALLBACKS  (main thread, via queued connection)
    # ──────────────────────────────────────────────────────────────

    @Slot(dict)
    def _on_worker_finished(self, results: Dict[str, Any]) -> None:
        """Phase 3: apply results on the main thread."""
        # Guard: node may have been deleted while worker was running
        if not self._is_scene_valid():
            self._is_computing = False
            return

        try:
            results = self._normalize_results(results)

            # Build new cache (atomic swap, same as sync path)
            timestamp = time.time()
            new_cache: Dict[str, CacheEntry] = {}
            for port_name, value in results.items():
                new_cache[port_name] = CacheEntry(
                    value=value,
                    is_valid=True,
                    timestamp=timestamp,
                    source_state=self._pre_compute_state or NodeState.NORMAL,
                )

            self._cached_values = new_cache
            self._is_dirty = False

            # Preserve last valid values
            for port_name, entry in new_cache.items():
                self._last_valid_values[port_name] = entry.value

            # Restore pre-compute state
            self._restore_pre_compute_state()

            # UI hook
            if hasattr(self, "on_evaluate_finished"):
                self.on_evaluate_finished()

        except Exception as e:
            log.error(f"Result application failed: {e}")
            self._restore_pre_compute_state()

        finally:
            self._is_computing = False
            self._current_worker = None
            self.compute_finished.emit()
            self._check_pending_dirty()

    @Slot(str)
    def _on_worker_error(self, error_msg: str) -> None:
        """Handle compute() exception."""
        log.info(f"Compute error in {self.__class__.__name__}: {error_msg}")

        # Mark cache entries as invalid
        for entry in self._cached_values.values():
            if isinstance(entry, CacheEntry):
                entry.is_valid = False

        self._restore_pre_compute_state()
        self._is_computing = False
        self._current_worker = None
        self.compute_error.emit(error_msg)
        self._check_pending_dirty()

    @Slot()
    def _on_worker_cancelled(self) -> None:
        """Handle cancellation completion."""
        self._restore_pre_compute_state()
        self._is_computing = False
        self._current_worker = None
        self.compute_cancelled.emit()
        self._check_pending_dirty()

    # ──────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────────────────────

    def _restore_pre_compute_state(self) -> None:
        """Revert from COMPUTING to whatever state we were in before."""
        target = self._pre_compute_state or NodeState.NORMAL
        self._pre_compute_state = None
        if _HAS_COMPUTING_STATE and self._state == NodeState.COMPUTING:
            try:
                super(BaseControlNode, self).set_state(target)
            except Exception:
                pass

    def _check_pending_dirty(self) -> None:
        """If the node was dirtied during compute, re-evaluate."""
        if self._pending_dirty:
            self._pending_dirty = False
            self._is_dirty = True
            QTimer.singleShot(0, self._safe_evaluate)

    def set_dirty(self, reason: str = "value_change") -> None:
        """
        Override: if currently computing, cancel and schedule re-eval
        instead of evaluating immediately.
        """
        already_dirty = self._is_dirty

        # Mark dirty via the mixin (propagates downstream)
        NodeDataFlow.set_dirty(self, reason)

        if self._is_computing:
            # Don't dispatch a new eval — just queue re-eval after cancel
            self._pending_dirty = True
            self.cancel_compute()
        elif not self._manual_mode and not already_dirty:
            QTimer.singleShot(0, self._safe_evaluate)

    # ── Cleanup ───────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Cancel any running worker before teardown."""
        self.cancel_compute()
        # Give the thread pool a moment to let the worker exit
        self._thread_pool.waitForDone(100)
        self._current_worker = None
        super().cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# THREADED MANUAL NODE  (button-triggered with cancel)
# ═══════════════════════════════════════════════════════════════════════════════

class ThreadedManualNode(BaseControlNode):
    """
    Button-triggered node whose ``compute()`` runs on :class:`QThreadPool`.

    Unlike :class:`ThreadedNode` this does **not** auto-evaluate when
    inputs change.  The user (or a connected trigger) must call
    ``execute()`` explicitly.  Calling ``execute()`` while already
    computing acts as a **cancel** — the running worker is stopped and
    the node returns to idle.

    Signals:
        compute_started()     — worker dispatched
        compute_finished()    — results applied
        compute_cancelled()   — cancelled (by user or upstream)
        compute_error(str)    — exception in compute()
        compute_progress(int) — 0-100 from report_progress()
    """

    # ── Signals ───────────────────────────────────────────────────
    compute_started   = Signal()
    compute_finished  = Signal()
    compute_cancelled = Signal()
    compute_error     = Signal(str)
    compute_progress  = Signal(int)

    def __init__(self, title: str = "Threaded Manual", **kwargs: Any) -> None:
        super().__init__(title, **kwargs)
        self._manual_mode = True

        # ── Threading state ──
        self._cancel_token: CancellationToken = CancellationToken()
        self._current_worker: Optional[ComputeWorker] = None
        self._thread_pool: QThreadPool = QThreadPool.globalInstance()
        self._pre_compute_state: Optional[NodeState] = None

    # ──────────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────────

    def execute(self) -> None:
        """
        Start or cancel the threaded compute.

        * **Idle → Computing**: gathers inputs, dispatches worker.
        * **Computing → Cancelling**: cancels the running worker.

        Wire this to a QPushButton::

            self.btn.clicked.connect(self.execute)
        """
        if self._is_computing:
            self.cancel_compute()
            return

        self._is_dirty = True
        self.evaluate()

    def cancel_compute(self) -> None:
        """Request cooperative cancellation of the running worker."""
        if self._is_computing:
            self._cancel_token.cancel()

    def is_compute_cancelled(self) -> bool:
        """Check cancellation flag (safe to call from worker thread)."""
        return self._cancel_token.is_cancelled()

    @property
    def is_computing(self) -> bool:
        """Whether a worker is currently running."""
        return self._is_computing

    # ──────────────────────────────────────────────────────────────
    # PROGRESS / WIDGET SNAPSHOT
    # ──────────────────────────────────────────────────────────────

    def report_progress(self, percent: int) -> None:
        """Emit progress from inside ``compute()`` (worker-thread safe)."""
        worker = self._current_worker
        if worker is not None:
            worker.signals.progress.emit(max(0, min(100, percent)))

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """
        Override to capture widget values before dispatch.
        Default delegates to get_widget_state().
        """
        if hasattr(self, "get_widget_state"):
            try:
                return self.get_widget_state()
            except Exception:
                pass
        return {}

    # ──────────────────────────────────────────────────────────────
    # EVALUATE  (threaded path)
    # ──────────────────────────────────────────────────────────────

    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """Dispatch compute() to the thread pool."""
        # ── Guards ────────────────────────────────────────────────
        if self._state == NodeState.DISABLED:
            return

        if self._is_computing:
            return

        # Passthrough runs synchronously
        if self._state == NodeState.PASSTHROUGH:
            super().evaluate(visited)
            return

        self._is_computing = True

        # ── Phase 1: gather inputs (main thread) ─────────────────
        try:
            input_params = self._gather_inputs(visited)
        except Exception as e:
            log.error(f"Input gathering failed: {e}")
            self._is_computing = False
            return

        widget_snapshot = self.snapshot_widget_inputs()
        if widget_snapshot:
            input_params.update(widget_snapshot)

        # ── Set COMPUTING state ──────────────────────────────────
        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                super(BaseControlNode, self).set_state(NodeState.COMPUTING)
            except Exception:
                pass

        # ── Create worker ─────────────────────────────────────────
        self._cancel_token = CancellationToken()

        worker = ComputeWorker(
            compute_fn=self.compute,
            inputs=input_params,
            cancel_token=self._cancel_token,
        )

        worker.signals.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.cancelled.connect(self._on_worker_cancelled, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.compute_progress.emit, Qt.ConnectionType.QueuedConnection)

        self._current_worker = worker

        # ── Dispatch ──────────────────────────────────────────────
        self.compute_started.emit()
        self._thread_pool.start(worker)

    # ──────────────────────────────────────────────────────────────
    # WORKER CALLBACKS  (main thread)
    # ──────────────────────────────────────────────────────────────

    @Slot(dict)
    def _on_worker_finished(self, results: Dict[str, Any]) -> None:
        """Apply results on the main thread."""
        if not self._is_scene_valid():
            self._is_computing = False
            return

        try:
            results = self._normalize_results(results)

            timestamp = time.time()
            new_cache: Dict[str, CacheEntry] = {}
            for port_name, value in results.items():
                new_cache[port_name] = CacheEntry(
                    value=value,
                    is_valid=True,
                    timestamp=timestamp,
                    source_state=self._pre_compute_state or NodeState.NORMAL,
                )

            self._cached_values = new_cache
            self._is_dirty = False

            for port_name, entry in new_cache.items():
                self._last_valid_values[port_name] = entry.value

            self._restore_pre_compute_state()

            if hasattr(self, "on_evaluate_finished"):
                self.on_evaluate_finished()

        except Exception as e:
            log.error(f"Result application failed: {e}")
            self._restore_pre_compute_state()

        finally:
            self._is_computing = False
            self._current_worker = None
            self.compute_finished.emit()

    @Slot(str)
    def _on_worker_error(self, error_msg: str) -> None:
        """Handle compute() exception."""
        log.info(f"Compute error in {self.__class__.__name__}: {error_msg}")

        for entry in self._cached_values.values():
            if isinstance(entry, CacheEntry):
                entry.is_valid = False

        self._restore_pre_compute_state()
        self._is_computing = False
        self._current_worker = None
        self.compute_error.emit(error_msg)

    @Slot()
    def _on_worker_cancelled(self) -> None:
        """Handle cancellation."""
        self._restore_pre_compute_state()
        self._is_computing = False
        self._current_worker = None
        self.compute_cancelled.emit()

    # ──────────────────────────────────────────────────────────────
    # INTERNAL
    # ──────────────────────────────────────────────────────────────

    def _restore_pre_compute_state(self) -> None:
        """Revert from COMPUTING to the prior state."""
        target = self._pre_compute_state or NodeState.NORMAL
        self._pre_compute_state = None
        if _HAS_COMPUTING_STATE and self._state == NodeState.COMPUTING:
            try:
                super(BaseControlNode, self).set_state(target)
            except Exception:
                pass

    def set_dirty(self, reason: str = "value_change") -> None:
        """Manual nodes just flag dirty — no auto dispatch."""
        already_dirty = self._is_dirty
        NodeDataFlow.set_dirty(self, reason)

        # Manual: only update the visual, never auto-evaluate
        if self._manual_mode:
            self.update()

    def cleanup(self) -> None:
        """Cancel any running worker before teardown."""
        self.cancel_compute()
        self._thread_pool.waitForDone(100)
        self._current_worker = None
        super().cleanup()
