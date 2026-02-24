# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

logger.py — Centralized Logging for the Node Canvas System
--------------------------------------------------------------
Provides per-module loggers backed by Python's standard ``logging``
library, with an optional Qt signal bridge so log messages can be
displayed in a UI panel.

Quick Start::

    from logger import get_logger
    log = get_logger("Serializer")

    log.info("Saved to: %s", filepath)
    log.warning("Unknown node type: %s", cls_name)
    log.error("Connection restore error: %s", exc)
    log.debug("Cache hit for port %s", port_name)

All loggers are children of the root ``"WeaveCanvas"`` logger, so a
single handler attached at the root controls all output.

Log Levels (standard):
    DEBUG    — Verbose internal state (cache hits, port lookups)
    INFO     — Normal operations (save, load, node spawned, connection made)
    WARNING  — Recoverable issues (missing icon, unknown config key)
    ERROR    — Failures that skip an operation (compute error, load failed)
    CRITICAL — Unrecoverable (should almost never be used)
"""

from __future__ import annotations

import logging
import sys
from typing import Optional, List, Callable

# ==============================================================================
# ROOT LOGGER NAME
# ==============================================================================

ROOT_LOGGER_NAME = "WeaveCanvas"

# ==============================================================================
# CUSTOM FORMATTER
# ==============================================================================

class CanvasFormatter(logging.Formatter):
    """
    Compact formatter that mirrors the ``[Module] message`` style used by
    the existing print statements, with optional timestamp for file output.

    Console output::

        [Serializer] INFO  Saved to: my_graph.json
        [ThreadedNode] ERROR  Compute error in ImageBlurNode: ...

    File output (with timestamp)::

        2026-02-21 14:30:05 [Serializer] INFO  Saved to: my_graph.json
    """

    CONSOLE_FMT = "[%(module_tag)s] %(levelname)-5s %(message)s"
    FILE_FMT    = "%(asctime)s [%(module_tag)s] %(levelname)-5s %(message)s"

    def __init__(self, use_timestamp: bool = False) -> None:
        fmt = self.FILE_FMT if use_timestamp else self.CONSOLE_FMT
        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        # Inject module_tag if not already set (fallback to logger leaf name)
        if not hasattr(record, "module_tag"):
            # Logger name is "WeaveCanvas.Serializer" → extract "Serializer"
            record.module_tag = record.name.rsplit(".", 1)[-1]
        return super().format(record)


# ==============================================================================
# SIGNAL BRIDGE (optional Qt integration)
# ==============================================================================

# Lazy import — only used if connect_qt_handler() is called
_qt_handler: Optional[logging.Handler] = None


class _QtSignalHandler(logging.Handler):
    """
    Logging handler that forwards records to a list of callbacks.

    This avoids a hard dependency on PySide6 at import time.  The
    callbacks receive ``(level: str, module_tag: str, message: str)``.
    """

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: List[Callable] = []

    def add_callback(self, fn: Callable) -> None:
        if fn not in self._callbacks:
            self._callbacks.append(fn)

    def remove_callback(self, fn: Callable) -> None:
        try:
            self._callbacks.remove(fn)
        except ValueError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        if not self._callbacks:
            return
        try:
            msg = self.format(record)
            tag = getattr(record, "module_tag", record.name.rsplit(".", 1)[-1])
            level = record.levelname
            for cb in self._callbacks:
                try:
                    cb(level, tag, msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)


# ==============================================================================
# PUBLIC API
# ==============================================================================

def get_logger(module_tag: str) -> logging.Logger:
    """
    Get a named logger for a specific module / component.

    Args:
        module_tag: Short identifier (e.g. ``"Serializer"``, ``"Canvas"``,
                    ``"NodeManager"``).  Appears in log output as
                    ``[Serializer]``.

    Returns:
        A ``logging.Logger`` instance that is a child of the root
        ``WeaveCanvas`` logger.

    Example::

        log = get_logger("Serializer")
        log.info("Saved %d nodes", count)
    """
    logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.{module_tag}")
    return logger


def setup_logging(
    level: int = logging.DEBUG,
    stream=None,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Configure the root WeaveCanvas logger.

    Call this **once** at application startup (e.g. in your main.py).
    If never called, Python's default behaviour applies (WARNING+ to
    stderr), but you'll miss INFO/DEBUG messages.

    Args:
        level:    Minimum log level (``logging.DEBUG``, ``logging.INFO``, etc.).
        stream:   Output stream for console handler (default ``sys.stdout``).
        log_file: Optional path to a log file.  If provided, a file handler
                  with timestamps is added alongside the console handler.

    Returns:
        The root ``WeaveCanvas`` logger.

    Example::

        # In main.py, before creating any nodes or canvas:
        from qt_logger import setup_logging
        setup_logging(level=logging.DEBUG)
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        # Console handler
        console = logging.StreamHandler(stream or sys.stdout)
        console.setLevel(level)
        console.setFormatter(CanvasFormatter(use_timestamp=False))
        root.addHandler(console)

        # File handler (optional)
        if log_file:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(CanvasFormatter(use_timestamp=True))
            root.addHandler(fh)

    return root


def set_log_level(level: int) -> None:
    """
    Change the log level at runtime.

    Args:
        level: New level (e.g. ``logging.WARNING`` to silence info messages).
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)


def add_log_callback(fn: Callable) -> None:
    """
    Register a callback to receive all log messages.

    The callback signature is ``fn(level: str, module_tag: str, message: str)``.
    Useful for piping logs into a UI panel or network socket.

    Args:
        fn: Callable to invoke for each log record.
    """
    global _qt_handler
    root = logging.getLogger(ROOT_LOGGER_NAME)

    if _qt_handler is None:
        _qt_handler = _QtSignalHandler()
        _qt_handler.setFormatter(CanvasFormatter(use_timestamp=False))
        root.addHandler(_qt_handler)

    _qt_handler.add_callback(fn)


def remove_log_callback(fn: Callable) -> None:
    """Remove a previously registered log callback."""
    if _qt_handler is not None:
        _qt_handler.remove_callback(fn)
