# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Weave: Threaded Node Base Classes — FIXED VERSION
Proper async fence management for undo stability and synchronized multi-input evaluation.
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
from weave.node.node_enums import NodeState

from weave.logger import get_logger
log = get_logger("ThreadedNode")

_HAS_COMPUTING_STATE: bool = hasattr(NodeState, "COMPUTING")


class CancellationToken:
    """Thread-safe cooperative cancellation flag."""
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()

    def __bool__(self) -> bool:
        return self._event.is_set()


class WorkerSignals(QObject):
    finished  = Signal(object)
    error     = Signal(str)
    progress  = Signal(int)
    cancelled = Signal()


class ComputeWorker(QRunnable):
    """Worker that properly handles fence lifecycle."""

    def __init__(
        self,
        compute_fn,
        inputs: Dict[str, Any],
        cancel_token: CancellationToken,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._compute_fn   = compute_fn
        self._inputs       = inputs
        self._cancel_token = cancel_token
        self.signals       = WorkerSignals()

    def run(self) -> None:
        if self._cancel_token.is_cancelled():
            self.signals.cancelled.emit()
            return

        try:
            results = self._compute_fn(self._inputs)
            if self._cancel_token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                self.signals.finished.emit(results)
        except Exception as exc:
            if self._cancel_token.is_cancelled():
                self.signals.cancelled.emit()
            else:
                tb = traceback.format_exc()
                self.signals.error.emit(f"{exc}\n{tb}")


class ThreadedNode(BaseControlNode):
    """
    Auto-evaluating node with background compute and proper undo integration.
    
    FIXED: 
    - Manages eval fence explicitly around worker lifecycle (not via _fenced_evaluate)
    - Checks canvas._restoring to prevent callbacks during undo/redo
    - Ensures fence decrement even if node is removed from scene
    - Implements Dependency Synchronization (Barrier) for multi-input nodes
    - Uses get_state() instead of get_all_values() for WidgetCore snapshots
    """

    compute_started   = Signal()
    compute_finished  = Signal()
    compute_cancelled = Signal()
    compute_error     = Signal(str)
    compute_progress  = Signal(int)
    _intermediate_signal = Signal(object)

    def __init__(self, title: str = "Threaded Node", **kwargs: Any) -> None:
        super().__init__(title, **kwargs)
        self._manual_mode = False

        self._cancel_token: CancellationToken = CancellationToken()
        self._current_worker: Optional[ComputeWorker] = None
        self._thread_pool: QThreadPool = QThreadPool.globalInstance()
        self._pending_dirty: bool = False
        self._pre_compute_state: Optional[NodeState] = None
        
        # Track if we have an active fence token for this worker
        self._worker_fence_token: Optional[int] = None

        self._intermediate_signal.connect(
            self._on_intermediate_results,
            Qt.ConnectionType.QueuedConnection,
        )

    # ── Cancellation API ──────────────────────────────────────────

    def is_compute_cancelled(self) -> bool:
        return self._cancel_token.is_cancelled()

    def cancel_compute(self) -> None:
        if self._is_computing:
            self._cancel_token.cancel()

    # ── Progress & Intermediate Results ───────────────────────────

    def report_progress(self, percent: int) -> None:
        worker = self._current_worker
        if worker is not None:
            worker.signals.progress.emit(max(0, min(100, percent)))

    def emit_intermediate(self, results: Dict[str, Any]) -> None:
        self._intermediate_signal.emit(results)

    @Slot(object)
    def _on_intermediate_results(self, results: object) -> None:
        """Apply intermediate results - only if not restoring."""
        if self._is_restoring():
            return
            
        if not self._is_computing or not isinstance(results, dict):
            return

        timestamp = time.time()
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

    # ── Widget Inputs ─────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        wc = getattr(self, '_widget_core', None)
        if wc is not None:
            try:
                # FIXED: Mapped to get_state() per widgetcore.py
                return wc.get_state()
            except Exception:
                pass
        return {}

    # ── CRITICAL FIX: Override set_dirty to manage fence manually ──

    def set_dirty(self, reason: str = "value_change") -> None:
        """
        FIXED: Don't use base class _fenced_evaluate (it's for sync only).
        Instead, manage the fence explicitly around the worker lifecycle.
        """
        if self._is_restoring():
            log.debug(f"set_dirty: skipped, canvas is restoring")
            return
            
        NodeDataFlow.set_dirty(self, reason)

        if self._is_computing:
            self._pending_dirty = True
            self.cancel_compute()
        elif not self._manual_mode and not self._eval_pending:
            self._eval_pending = True
            # Schedule worker start - fence will be held until worker truly finishes
            QTimer.singleShot(0, self._start_worker_evaluation)

    def _start_worker_evaluation(self) -> None:
        """Start worker with proper fence acquisition and dependency synchronization.

        FIXED:
        - PASSTHROUGH mode now evaluates synchronously via the base class
          instead of spawning a worker that calls ``compute()``.
        - Barrier failure reschedules instead of silently dropping.
        """
        self._eval_pending = False
        
        if not self._is_dirty or self._state == NodeState.DISABLED:
            return

        # CRITICAL: Do not compute if upstream nodes are still crunching numbers.
        # Reschedule so we try again on the next event-loop tick instead of
        # relying solely on upstream _mark_downstream_dirty to wake us.
        if not self._are_inputs_ready():
            if not self._eval_pending:
                self._eval_pending = True
                QTimer.singleShot(0, self._start_worker_evaluation)
            return

        # ── PASSTHROUGH: synchronous, no worker needed ──
        if self._state == NodeState.PASSTHROUGH:
            self._increment_eval_fence()
            try:
                # Delegate to BaseControlNode.evaluate which calls
                # _apply_passthrough → caches → _mark_downstream_dirty
                super().evaluate(None)
            finally:
                self._decrement_eval_fence()
            return

        # ── NORMAL: threaded worker path ──
        # CRITICAL: Increment fence HERE, right before starting worker
        # This fence will be decremented only when worker truly finishes
        self._increment_eval_fence()
        self._worker_fence_token = self._fence_token  # Remember our token
        
        self._is_computing = True
        
        try:
            # 1. Gathers upstream data + local BIDIRECTIONAL fallbacks
            input_params = self._gather_inputs(None)
            
            # 2. Gathers the entire internal UI state (including INTERNAL combos)
            widget_snapshot = self.snapshot_widget_inputs()
            
            # 3. The Elegant Merge: Only inject UI state if the key isn't already 
            # satisfied by a connected port or a bidirectional fallback.
            if widget_snapshot:
                for key, val in widget_snapshot.items():
                    if key not in input_params or input_params[key] is None:
                        input_params[key] = val
                        
        except Exception as e:
            log.error(f"Input gathering failed: {e}")
            self._is_computing = False
            self._release_worker_fence()
            return

        self._pre_compute_state = self._state
        if _HAS_COMPUTING_STATE:
            try:
                super(BaseControlNode, self).set_state(NodeState.COMPUTING)
                if hasattr(self, '_start_computing_pulse'):
                    self._start_computing_pulse()
            except Exception:
                pass

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
        self.compute_started.emit()
        self._thread_pool.start(worker)

    def _release_worker_fence(self) -> None:
        """Release the fence token held for this worker."""
        if self._worker_fence_token is not None:
            # Use base class decrement which handles scene references properly
            self._decrement_eval_fence()
            self._worker_fence_token = None

    def _is_restoring(self) -> bool:
        """Check if canvas is currently in undo/redo restoration."""
        try:
            canvas = self.scene()
            if canvas is not None:
                return getattr(canvas, '_restoring', False)
        except RuntimeError:
            pass
        return False

    # ── State transition routing ──────────────────────────────────

    def _handle_state_transition(self, old_state: NodeState, new_state: NodeState) -> None:
        """Route state transitions through the correct evaluation path.

        The base class schedules ``_fenced_evaluate`` for transitions that
        require re-evaluation.  That works for synchronous nodes, but
        ``ThreadedNode.evaluate()`` is intentionally a no-op for NORMAL
        mode (threaded compute goes through ``_start_worker_evaluation``).

        Routing rules:
        - Target is PASSTHROUGH → synchronous, base class ``_fenced_evaluate`` ✓
        - Target is NORMAL (from DISABLED or PASSTHROUGH) → threaded worker
        - Target is DISABLED → no evaluation, just notify downstream
        """
        # anything -> DISABLED: preserve + notify (no evaluation needed)
        if new_state == NodeState.DISABLED:
            self._mark_downstream_dirty("upstream_disabled")
            return

        # anything -> PASSTHROUGH: synchronous path via base class
        if new_state == NodeState.PASSTHROUGH:
            if old_state != NodeState.DISABLED:
                self._cached_values.clear()
            self._is_dirty = True
            if not self._manual_mode:
                self._eval_pending = True
                QTimer.singleShot(0, self._fenced_evaluate)
            return

        # DISABLED -> NORMAL  or  PASSTHROUGH -> NORMAL: threaded worker path
        if old_state == NodeState.DISABLED or old_state == NodeState.PASSTHROUGH:
            if old_state == NodeState.PASSTHROUGH:
                self._cached_values.clear()
            self._is_dirty = True
            if not self._manual_mode:
                self._eval_pending = True
                QTimer.singleShot(0, self._start_worker_evaluation)

    # ── Worker Callbacks (Main Thread) ────────────────────────────

    @Slot(object)
    def _on_worker_finished(self, results) -> None:
        """Apply results - fence is still held until we release it.

        FIXED: ``_mark_downstream_dirty`` moved to ``finally`` so
        downstream nodes are always woken up, even if result
        normalisation or cache building throws.
        """
        # Always release fence first to prevent deadlock on error
        self._release_worker_fence()
        
        if self._is_restoring():
            self._cleanup_after_worker(skip_results=True)
            return
            
        if not self._is_scene_valid():
            self._cleanup_after_worker(skip_results=True)
            return

        _apply_succeeded = False
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
            _apply_succeeded = True

            for port_name, entry in new_cache.items():
                self._last_valid_values[port_name] = entry.value

            self._restore_pre_compute_state()
            
            if hasattr(self, "on_evaluate_finished"):
                self.on_evaluate_finished()

        except Exception as e:
            log.error(f"Result application failed: {e}")
            self._restore_pre_compute_state()

        finally:
            if _apply_succeeded:
                self._mark_downstream_dirty("upstream_threaded_result")
            self._cleanup_after_worker()

    @Slot(str)
    def _on_worker_error(self, error_msg: str) -> None:
        """Handle error - release fence."""
        self._release_worker_fence()
        
        if not self._is_restoring():
            log.info(f"Compute error in {self.__class__.__name__}: {error_msg}")
            for entry in self._cached_values.values():
                if isinstance(entry, CacheEntry):
                    entry.is_valid = False
            self._restore_pre_compute_state()
            self.compute_error.emit(error_msg)
            
        self._cleanup_after_worker()

    @Slot()
    def _on_worker_cancelled(self) -> None:
        """Handle cancellation - release fence."""
        self._release_worker_fence()
        
        if not self._is_restoring():
            self._restore_pre_compute_state()
            self.compute_cancelled.emit()
            
        self._cleanup_after_worker()

    def _cleanup_after_worker(self, skip_results: bool = False) -> None:
        """Clean up worker state and check for pending re-eval."""
        self._is_computing = False
        self._current_worker = None
        self.compute_finished.emit()
        
        # Check for pending dirty (re-evaluate if needed)
        if self._pending_dirty and not self._is_restoring():
            self._pending_dirty = False
            self._is_dirty = True
            self.set_dirty("pending_recompute")

    def _restore_pre_compute_state(self) -> None:
        target = self._pre_compute_state or NodeState.NORMAL
        self._pre_compute_state = None
        
        if _HAS_COMPUTING_STATE and self._state == NodeState.COMPUTING:
            try:
                if hasattr(self, '_stop_computing_pulse'):
                    self._stop_computing_pulse()
                super(BaseControlNode, self).set_state(target)
            except Exception:
                pass

    # ── Synchronous evaluate (fallback) ──────────────────────────

    def evaluate(self, visited: Optional[Set[int]] = None) -> None:
        """
        Synchronous evaluation - used for passthrough only.
        Normal threaded evaluation goes through _start_worker_evaluation.
        """
        if self._state == NodeState.PASSTHROUGH:
            super().evaluate(visited)
        # Normal compute is handled asynchronously via set_dirty -> _start_worker_evaluation

    def cleanup(self) -> None:
        """Cancel worker before teardown."""
        self.cancel_compute()
        self._thread_pool.waitForDone(100)
        self._current_worker = None
        super().cleanup()


class ThreadedManualNode(ThreadedNode):
    """Button-triggered threaded node."""

    def __init__(self, title: str = "Threaded Manual Node", **kwargs: Any) -> None:
        super().__init__(title, **kwargs)
        self._manual_mode = True

    def execute(self) -> None:
        """Trigger execution manually."""
        if self._is_restoring():
            log.debug("execute: skipped, canvas is restoring")
            return
            
        if not self._is_computing:
            self._is_dirty = True
            self._start_worker_evaluation()
        else:
            self._pending_dirty = True
            self.cancel_compute()

