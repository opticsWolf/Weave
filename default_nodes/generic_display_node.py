# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

generic_display_node.py
------------------------
A ThreadedNode display sink that runs ValueConverter off the main thread.

``GenericDisplayNode`` is a drop-in replacement for
``SmartDisplayNode`` for cases where the incoming data is large (e.g. a
big NumPy array or a deep dict) and the conversion work would
noticeably stall the UI if it ran synchronously.

Key differences from ``SmartDisplayNode`` (ActiveNode)
-------------------------------------------------------
* ``compute()`` runs on ``QThreadPool`` — the ValueConverter call never
  blocks the Qt event loop.
* The COMPUTING pulse glow is active while conversion is in progress.
* Widget writes always happen on the main thread via
  ``on_evaluate_finished()``, identical to the synchronous node.

Thread-safety note on ``_pending_text``
---------------------------------------
``compute()`` writes ``self._pending_text`` on the worker thread.
``on_evaluate_finished()`` reads it on the main thread.  There is no
concurrent access because ``on_evaluate_finished`` is called via a
``Qt.QueuedConnection`` signal that only fires *after* the worker
function has returned and the thread has finished writing.  No lock is
required.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTextEdit

import math

from weave.threadednodes import ThreadedNode
from weave.noderegistry import register_node
from weave.widgetcore import WidgetCore

from weave.logger import get_logger

log = get_logger("DisplayNode")

# ══════════════════════════════════════════════════════════════════════════════
# ValueConverter  —  standalone, import-safe type renderer
# ══════════════════════════════════════════════════════════════════════════════

class ValueConverter:
    """
    Converts arbitrary Python values into a human-readable string suitable
    for display inside a node widget.
    """

    # ── Tuning constants ─────────────────────────────────────────────
    MAX_STR_LEN: int       = 4_096
    MAX_BYTES_PREVIEW: int = 64
    MAX_REPR_LEN: int      = 512
    SIG_FIGS: int          = 6

    DIVIDER: str = "─" * 17

    # ── Public entry point ───────────────────────────────────────────

    @classmethod
    def convert(cls, value: Any) -> str:
        """Return a formatted string representation of *value*."""
        try:
            return cls._dispatch(value, depth=0)
        except Exception as exc:  # pragma: no cover
            return f"<converter error: {exc}>"

    # ── Internal dispatcher ──────────────────────────────────────────

    @classmethod
    def _dispatch(cls, value: Any, depth: int) -> str:
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return cls._fmt_float(value)
        if isinstance(value, complex):
            return cls._fmt_complex(value.real, value.imag)
        if isinstance(value, str):
            if len(value) > cls.MAX_STR_LEN:
                return value[: cls.MAX_STR_LEN] + f"\n… ({len(value):,} chars total)"
            return value
        if isinstance(value, (bytes, bytearray)):
            return cls._fmt_bytes(value)

        np = cls._np()
        if np is not None and isinstance(value, np.ndarray):
            return cls._fmt_ndarray(value, np)

        if isinstance(value, (list, tuple)):
            return cls._fmt_sequence(value, depth)
        if isinstance(value, (set, frozenset)):
            return cls._fmt_set(value, depth)
        if isinstance(value, dict):
            return cls._fmt_dict(value, depth)

        return cls._fmt_generic(value)

    # ── Numeric helpers ──────────────────────────────────────────────

    @classmethod
    def _fmt_float(cls, v: float) -> str:
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        if v == 0.0:
            return "0.0"
        magnitude = abs(v)
        if 1e-4 <= magnitude < 1e7:
            digits = max(0, cls.SIG_FIGS - 1 - int(math.floor(math.log10(magnitude))))
            return f"{v:.{digits}f}"
        return f"{v:.{cls.SIG_FIGS - 1}e}"

    @classmethod
    def _fmt_complex(cls, re: float, im: float) -> str:
        re_s  = cls._fmt_float(re)
        im_s  = cls._fmt_float(abs(im))
        sign  = "+" if im >= 0 or math.isnan(im) else "-"
        if re == 0.0 and not math.isnan(re):
            return f"{'-' if im < 0 else ''}{im_s}j"
        if im == 0.0:
            return f"({re_s}+0.0j)"
        return f"({re_s}{sign}{im_s}j)"

    @classmethod
    def _fmt_bytes(cls, v: bytes | bytearray) -> str:
        n = len(v)
        preview = v[: cls.MAX_BYTES_PREVIEW]
        hex_str = " ".join(f"{b:02x}" for b in preview)
        suffix = f" …" if n > cls.MAX_BYTES_PREVIEW else ""
        type_name = type(v).__name__
        return f"{type_name}  ({n:,} B)\n{cls.DIVIDER}\n  {hex_str}{suffix}"

    @classmethod
    def _fmt_sequence(cls, v: list | tuple, depth: int) -> str:
        type_name = type(v).__name__
        n = len(v)
        if n == 0:
            return f"{type_name}  [empty]"

        elem_type = cls._common_type_label(v)
        header = f"{type_name}  [{n:,} item{'s' if n != 1 else ''}, {elem_type}]"

        if depth > 1:
            return header

        lines = [header, cls.DIVIDER]
        for i, item in enumerate(v):
            rendered = cls._dispatch(item, depth + 1)
            if "\n" in rendered:
                sub = rendered.replace("\n", "\n    ")
                lines.append(f"  [{i}]\n    {sub}")
            else:
                lines.append(f"  [{i}]  {rendered}")

        return "\n".join(lines)

    @classmethod
    def _fmt_set(cls, v: set | frozenset, depth: int) -> str:
        type_name = type(v).__name__
        n = len(v)
        if n == 0:
            return f"{type_name}  {{empty}}"

        items = list(v)
        elem_type = cls._common_type_label(items)
        header = f"{type_name}  {{{n:,} item{'s' if n != 1 else ''}, {elem_type}}}"

        if depth > 1:
            return header

        lines = [header, cls.DIVIDER]
        for item in items:
            rendered = cls._dispatch(item, depth + 1)
            if "\n" in rendered:
                sub = rendered.replace("\n", "\n    ")
                lines.append(f"  \n    {sub}")
            else:
                lines.append(f"  {rendered}")

        return "\n".join(lines)

    @classmethod
    def _fmt_dict(cls, v: dict, depth: int) -> str:
        n = len(v)
        if n == 0:
            return "dict  {empty}"

        header = f"dict  {{{n:,} key{'s' if n != 1 else ''}}}"

        if depth > 1:
            return header

        lines = [header, cls.DIVIDER]
        for k, val in v.items():
            key_str = repr(k)
            val_str = cls._dispatch(val, depth + 1)
            if "\n" in val_str:
                sub = val_str.replace("\n", "\n      ")
                lines.append(f"  {key_str}  →\n      {sub}")
            else:
                lines.append(f"  {key_str}  →  {val_str}")

        return "\n".join(lines)

    @classmethod
    def _fmt_scalar(cls, x: Any) -> str:
        try:
            import numpy as np
            if np.issubdtype(type(x), np.complexfloating):
                return cls._fmt_complex(float(x.real), float(x.imag))
            if np.issubdtype(type(x), np.floating):
                return cls._fmt_float(float(x))
            if np.issubdtype(type(x), np.integer):
                return f"{int(x):,}"
            if np.issubdtype(type(x), np.bool_):
                return "True" if x else "False"
        except Exception:
            pass
        return str(x)

    @classmethod
    def _fmt_ndarray(cls, v: Any, np: Any) -> str:
        ndim  = v.ndim
        shape = v.shape
        shape_str = "×".join(str(d) for d in shape)
        is_complex = np.issubdtype(v.dtype, np.complexfloating)

        lines = [f"ndarray  shape=({shape_str})  dtype={v.dtype}"]

        if v.size == 0:
            lines.append("  [empty]")
            return "\n".join(lines)

        lines.append(cls.DIVIDER)

        if not is_complex:
            try:
                mn = cls._fmt_scalar(np.nanmin(v))
                mx = cls._fmt_scalar(np.nanmax(v))
                lines.append(f"  min={mn}  max={mx}")
            except (TypeError, ValueError):
                pass

        if ndim <= 1:
            elems = "  ".join(cls._fmt_scalar(x) for x in v.flat)
            lines.append(f"  [{elems}]")
        elif ndim == 2:
            rows, cols = shape
            cells = [
                [cls._fmt_scalar(v[r, c]) for c in range(cols)]
                for r in range(rows)
            ]
            col_widths = [
                max(len(cells[r][c]) for r in range(rows))
                for c in range(cols)
            ]
            row_label_w = len(str(rows - 1))
            for r, row_cells in enumerate(cells):
                padded = "  ".join(
                    cell.rjust(col_widths[c]) for c, cell in enumerate(row_cells)
                )
                lines.append(f"  row {r:{row_label_w}d}:  [{padded}]")
        else:
            import itertools
            ranges = [range(d) for d in shape]
            for idx in itertools.product(*ranges):
                idx_str = ", ".join(str(i) for i in idx)
                lines.append(f"  [{idx_str}]  {cls._fmt_scalar(v[idx])}")

        return "\n".join(lines)

    @classmethod
    def _fmt_generic(cls, v: Any) -> str:
        qname = type(v).__qualname__
        r = repr(v)
        if len(r) > cls.MAX_REPR_LEN:
            r = r[: cls.MAX_REPR_LEN] + " …"
        return f"{qname}\n{cls.DIVIDER}\n  {r}"

    @staticmethod
    def _common_type_label(items: list) -> str:
        if not items:
            return "empty"
        types = {type(x).__name__ for x in items}
        if len(types) == 1:
            return next(iter(types))
        return "mixed"

    @staticmethod
    def _np() -> Any:
        import sys
        return sys.modules.get("numpy")


# ══════════════════════════════════════════════════════════════════════════════
# GenericDisplayNode
# ══════════════════════════════════════════════════════════════════════════════

@register_node
class GenericDisplayNode(ThreadedNode):
    """
    Flexible sink node that renders any incoming value inside a
    read-only ``QTextEdit`` using :class:`~weave.basic_nodes.ValueConverter`,
    with conversion running on ``QThreadPool`` so the UI stays responsive
    when processing large payloads.
    """

    display_updated = Signal(str)

    node_class:       ClassVar[str] = "Basic"
    node_subclass:    ClassVar[str] = "Output"
    node_name:        ClassVar[str] = "Generic Display"
    node_description: ClassVar[str] = "Displays any value with type-aware formatting; conversion runs off-thread"
    node_tags:        ClassVar[List[str]] = ["display", "output", "observer", "any", "smart", "threaded"]

    def __init__(
        self,
        title: str = "Generic Display",
        min_width: int = 200,
        min_height: int = 110,
        show_type_header: bool = False,
        placeholder: str = "Waiting for data…",
        **kwargs: Any,
    ) -> None:
        super().__init__(title=title, **kwargs)

        self._show_type_header: bool = show_type_header

        # ── Input port ───────────────────────────────────────────────
        self.add_input("data", datatype="any")

        # ── WidgetCore + read-only QTextEdit ─────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        self._display = QTextEdit()
        self._display.setReadOnly(True)
        self._display.setMinimumSize(min_width, min_height)
        self._display.setPlaceholderText(placeholder)

        # Register as DISPLAY role
        self._widget_core.register_widget(
            port_name="display",
            widget=self._display,
            role="DISPLAY",
            datatype="string",
            default="",
            getter=lambda: self._display.toPlainText(),
            setter=lambda v: self._display.setPlainText(str(v)),
        )

        self.set_content_widget(self._widget_core)
        
        if hasattr(self._widget_core, 'patch_proxy'):
            self._widget_core.patch_proxy()
        if hasattr(self._widget_core, 'refresh_widget_palettes'):
            self._widget_core.refresh_widget_palettes()

        self._pending_text: Optional[str] = None

    # ── Helpers (pure Python, worker-thread safe) ─────────────────────

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n //= 1024
        return f"{n:.1f} TB"

    @staticmethod
    def _size_hint(value: Any) -> str:
        import sys
        try:
            np = sys.modules.get("numpy")
            if np is not None and isinstance(value, np.ndarray):
                return GenericDisplayNode._fmt_bytes(value.nbytes)
            if isinstance(value, (list, tuple, set, frozenset, dict)):
                n = len(value)
                return f"{n:,} item{'s' if n != 1 else ''}"
            if isinstance(value, (bytes, bytearray)):
                return GenericDisplayNode._fmt_bytes(len(value))
            if isinstance(value, str):
                return f"{len(value):,} char{'s' if len(value) != 1 else ''}"
            return GenericDisplayNode._fmt_bytes(sys.getsizeof(value))
        except Exception:
            return ""

    def _build_display_text(self, value: Any) -> str:
        body = ValueConverter.convert(value)

        if not self._show_type_header:
            return body

        type_name  = type(value).__qualname__
        size_str   = self._size_hint(value)
        sep        = "  " if size_str else ""
        header     = f"[{type_name}]{sep}{size_str}"
        divider    = "─" * max(len(header), 17)
        return f"{header}\n{divider}\n{body}"

    # ── Computation (worker thread — no Qt widget access) ─────────────

    def compute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Convert the incoming value to a display string on the worker thread."""
        try:
            # Cooperative cancellation check
            if self.is_compute_cancelled():
                return {}

            value = inputs.get("data")
            self._pending_text = self._build_display_text(value)
            
        except Exception as exc:
            log.error(f"Exception in GenericDisplayNode.compute: {exc}")
            self._pending_text = f"<error: {exc}>"

        return {}

    # ── Post-compute UI flush (main thread) ───────────────────────────

    def on_evaluate_finished(self) -> None:
        """Flush ``_pending_text`` to the display widget on the main thread."""
        try:
            if self._pending_text is not None:
                try:
                    self._display.setPlainText(self._pending_text)
                    self.display_updated.emit(self._pending_text)
                    self._pending_text = None
                except RuntimeError:
                    pass  # Widget already deleted during scene teardown
        except Exception as exc:
            log.error(f"Exception in GenericDisplayNode.on_evaluate_finished: {exc}")
        finally:
            super().on_evaluate_finished()

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Release resources and break reference cycles safely."""
        self._pending_text = None
        
        # Cancel any running computation first
        if hasattr(self, 'cancel_compute'):
            self.cancel_compute()

        # ALWAYS call super().cleanup() last
        super().cleanup()