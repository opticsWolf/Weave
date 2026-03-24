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
* ``snapshot_widget_inputs()`` returns ``{}`` — this is a pure sink;
  no widget values are read inside ``compute()``.

Thread-safety note on ``_pending_text``
---------------------------------------
``compute()`` writes ``self._pending_text`` on the worker thread.
``on_evaluate_finished()`` reads it on the main thread.  There is no
concurrent access because ``on_evaluate_finished`` is called via a
``Qt.QueuedConnection`` signal that only fires *after* the worker
function has returned and the thread has finished writing.  No lock is
required.

Architecture
------------
::

    upstream node
         │  data (any)
         ▼
    GenericDisplayNode
      ├── compute()          [worker thread]
      │     ValueConverter.convert(value)  →  _pending_text
      │     return {}                      (no output ports)
      │
      └── on_evaluate_finished()   [main thread, via QueuedConnection]
            _display.setPlainText(_pending_text)
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

    The converter is intentionally import-safe: NumPy is only accessed if
    it is already present in ``sys.modules``.  No import-time side effects
    occur.

    Usage::

        text = ValueConverter.convert(some_value)

    Supported types and their output format
    ----------------------------------------
    ``None``
        ``"None"``

    ``bool``
        ``"True"`` / ``"False"``  (checked *before* ``int`` because
        ``bool`` is a subclass of ``int`` in Python)

    ``int``
        Plain decimal representation with thousands separator.
        Example: ``"1,234,567"``

    ``float``
        Auto-selects fixed or scientific notation based on magnitude.
        Rounds to 6 significant figures.
        Examples: ``"3.141593"``, ``"1.234568e+09"``, ``"inf"``, ``"nan"``

    ``complex``
        Formatted as ``"(re±imj)"`` with full sign handling.
        Examples: ``"(3.0+4.0j)"``, ``"(3.0-4.0j)"``, ``"4.0j"``

    ``str``
        Returned as-is.  Truncated with ``…`` when longer than
        ``MAX_STR_LEN`` (default 4 096 characters).

    ``bytes`` / ``bytearray``
        Hexadecimal dump, at most ``MAX_BYTES_PREVIEW`` bytes shown.
        Example: ``"bytes (12 B):\\n  48 65 6c 6c 6f …"``

    ``list`` / ``tuple``
        Type label, length, element type summary, and *all* items —
        no truncation.  Example::

            list  [5 items, int]
            ─────────────────
              [0]  1
              [1]  2
              [2]  3

    ``dict``
        Type label, key count, and *all* key→value pairs (values
        recursively converted at depth + 1).

    ``set`` / ``frozenset``
        Similar to ``list`` but items are shown unordered, all of them.

    ``numpy.ndarray``  (if NumPy is loaded)
        All elements are shown.

        * **1-D**: single comma-separated line.
        * **2-D**: one row per line, rows aligned.
        * **Complex dtype**: each element rendered as ``(re±imj)``.
        * **Higher rank**: shape header followed by the full flat listing.

        Example (2-D real)::

            ndarray  shape=(3×2)  dtype=float64
            ─────────────────
              min=-1.0  max=1.0
              row 0:  [0.1,  0.2]
              row 1:  [0.3,  0.4]
              row 2: [-1.0,  1.0]

    All other objects
        ``repr()`` output, prefixed with the qualified class name,
        truncated to ``MAX_REPR_LEN`` characters.
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
        # ── None ──────────────────────────────────────────────────────
        if value is None:
            return "None"

        # ── bool  (must come before int) ─────────────────────────────
        if isinstance(value, bool):
            return "True" if value else "False"

        # ── int ──────────────────────────────────────────────────────
        if isinstance(value, int):
            return f"{value:,}"

        # ── float ────────────────────────────────────────────────────
        if isinstance(value, float):
            return cls._fmt_float(value)

        # ── complex ──────────────────────────────────────────────────
        if isinstance(value, complex):
            return cls._fmt_complex(value.real, value.imag)

        # ── str ───────────────────────────────────────────────────────
        if isinstance(value, str):
            if len(value) > cls.MAX_STR_LEN:
                return value[: cls.MAX_STR_LEN] + f"\n… ({len(value):,} chars total)"
            return value

        # ── bytes / bytearray ────────────────────────────────────────
        if isinstance(value, (bytes, bytearray)):
            return cls._fmt_bytes(value)

        # ── NumPy ndarray ────────────────────────────────────────────
        np = cls._np()
        if np is not None and isinstance(value, np.ndarray):
            return cls._fmt_ndarray(value, np)

        # ── list / tuple ─────────────────────────────────────────────
        if isinstance(value, (list, tuple)):
            return cls._fmt_sequence(value, depth)

        # ── set / frozenset ──────────────────────────────────────────
        if isinstance(value, (set, frozenset)):
            return cls._fmt_set(value, depth)

        # ── dict ─────────────────────────────────────────────────────
        if isinstance(value, dict):
            return cls._fmt_dict(value, depth)

        # ── fallback ─────────────────────────────────────────────────
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
            # Fixed notation, 6 sig figs
            digits = max(0, cls.SIG_FIGS - 1 - int(math.floor(math.log10(magnitude))))
            return f"{v:.{digits}f}"
        return f"{v:.{cls.SIG_FIGS - 1}e}"

    # ── Complex ──────────────────────────────────────────────────────

    @classmethod
    def _fmt_complex(cls, re: float, im: float) -> str:
        """Format a complex number with correct sign and minimal redundancy."""
        re_s  = cls._fmt_float(re)
        im_s  = cls._fmt_float(abs(im))
        sign  = "+" if im >= 0 or math.isnan(im) else "-"
        # Pure imaginary — skip the zero real part
        if re == 0.0 and not math.isnan(re):
            return f"{'-' if im < 0 else ''}{im_s}j"
        # Pure real with zero imaginary — still show as complex for type clarity
        if im == 0.0:
            return f"({re_s}+0.0j)"
        return f"({re_s}{sign}{im_s}j)"

    # ── Bytes ────────────────────────────────────────────────────────

    @classmethod
    def _fmt_bytes(cls, v: bytes | bytearray) -> str:
        n = len(v)
        preview = v[: cls.MAX_BYTES_PREVIEW]
        hex_str = " ".join(f"{b:02x}" for b in preview)
        suffix = f" …" if n > cls.MAX_BYTES_PREVIEW else ""
        type_name = type(v).__name__
        return f"{type_name}  ({n:,} B)\n{cls.DIVIDER}\n  {hex_str}{suffix}"

    # ── Sequences ────────────────────────────────────────────────────

    @classmethod
    def _fmt_sequence(cls, v: list | tuple, depth: int) -> str:
        type_name = type(v).__name__
        n = len(v)
        if n == 0:
            return f"{type_name}  [empty]"

        elem_type = cls._common_type_label(v)
        header = f"{type_name}  [{n:,} item{'s' if n != 1 else ''}, {elem_type}]"

        if depth > 1:
            # Nested inside another container — brief summary only
            return header

        lines = [header, cls.DIVIDER]
        for i, item in enumerate(v):
            rendered = cls._dispatch(item, depth + 1)
            if "\n" in rendered:
                # Indent each sub-line so structure is clear
                sub = rendered.replace("\n", "\n    ")
                lines.append(f"  [{i}]\n    {sub}")
            else:
                lines.append(f"  [{i}]  {rendered}")

        return "\n".join(lines)

    # ── Sets ─────────────────────────────────────────────────────────

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

    # ── Dict ─────────────────────────────────────────────────────────

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

    # ── NumPy ────────────────────────────────────────────────────────

    @classmethod
    def _fmt_scalar(cls, x: Any) -> str:
        """Format a single numpy scalar, including complex types."""
        try:
            import numpy as np  # already loaded — no cost
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

        # ── min / max (real arrays only — complex has no total order) ─
        if not is_complex:
            try:
                mn = cls._fmt_scalar(np.nanmin(v))
                mx = cls._fmt_scalar(np.nanmax(v))
                lines.append(f"  min={mn}  max={mx}")
            except (TypeError, ValueError):
                pass

        # ── 1-D: single line of all elements ──────────────────────────
        if ndim <= 1:
            elems = "  ".join(cls._fmt_scalar(x) for x in v.flat)
            lines.append(f"  [{elems}]")

        # ── 2-D: one row per line, aligned ────────────────────────────
        elif ndim == 2:
            rows, cols = shape
            # Pre-render all cells so we can compute column widths
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

        # ── N-D (rank ≥ 3): flat listing with nd-index labels ─────────
        else:
            import itertools
            ranges = [range(d) for d in shape]
            for idx in itertools.product(*ranges):
                idx_str = ", ".join(str(i) for i in idx)
                lines.append(f"  [{idx_str}]  {cls._fmt_scalar(v[idx])}")

        return "\n".join(lines)

    # ── Generic ──────────────────────────────────────────────────────

    @classmethod
    def _fmt_generic(cls, v: Any) -> str:
        qname = type(v).__qualname__
        r = repr(v)
        if len(r) > cls.MAX_REPR_LEN:
            r = r[: cls.MAX_REPR_LEN] + " …"
        return f"{qname}\n{cls.DIVIDER}\n  {r}"

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _common_type_label(items: list) -> str:
        """Return a short label for the predominant element type."""
        if not items:
            return "empty"
        types = {type(x).__name__ for x in items}
        if len(types) == 1:
            return next(iter(types))
        return "mixed"

    @staticmethod
    def _np() -> Any:
        """Return the numpy module if already imported, else None."""
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

    The COMPUTING pulse glow is visible while the conversion is in
    progress.  For small values the worker completes near-instantly and
    the glow is barely perceptible; for large arrays or deep dicts the
    visual feedback is clear.

    Type: Threaded (compute runs on QThreadPool; main thread unblocked).

    Inputs
    ------
    data : any
        The value to display.  Accepts every Python type.

    Display
    -------
    ``[<type>]  <size>``    ← only when ``show_type_header=True``
    ``────────────────``
    ``<converted value>``

    Parameters
    ----------
    title : str
        Node title shown in the graph view.
    min_width : int
        Minimum display widget width in pixels (default ``200``).
    min_height : int
        Minimum display widget height in pixels (default ``110``).
    show_type_header : bool
        When ``True`` prepends a ``[type]  size`` header line above
        the converted value body.  Default ``False``.
    placeholder : str
        Placeholder text shown while no data has been received yet.
    """

    # Emitted on the main thread after the display has been updated.
    display_updated = Signal(str)

    node_class:       ClassVar[str]            = "Basic"
    node_subclass:    ClassVar[str]            = "Output"
    node_name:        ClassVar[Optional[str]]  = "Generic Display"
    node_description: ClassVar[Optional[str]]  = (
        "Displays any value with type-aware formatting; conversion runs off-thread"
    )
    node_tags: ClassVar[Optional[List[str]]] = [
        "display", "output", "observer", "any", "smart", "threaded",
    ]

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

        # Read-only config accessed by the worker thread — set once, never mutated.
        self._show_type_header: bool = show_type_header

        # ── Input port ───────────────────────────────────────────────
        self.add_input("data", "any")

        # ── WidgetCore + read-only QTextEdit ─────────────────────────
        self._widget_core = WidgetCore()
        self._widget_core.set_node(self)

        self._display = QTextEdit()
        self._display.setReadOnly(True)
        self._display.setMinimumSize(min_width, min_height)
        self._display.setPlaceholderText(placeholder)

        # Register as DISPLAY role — no port is created for this widget;
        # it is purely a visual output, written via set_port_value.
        self._widget_core.register_widget(
            "display", self._display,
            role="display",
            datatype="string",
            default="",
            getter=lambda: self._display.toPlainText(),
            setter=lambda v: self._display.setPlainText(str(v)),
        )

        self.set_content_widget(self._widget_core)
        self._widget_core.patch_proxy()
        self._widget_core.refresh_widget_palettes()

        # Pending text set by compute() (worker thread),
        # consumed by on_evaluate_finished() (main thread).
        self._pending_text: Optional[str] = None

    # ── Widget snapshot ──────────────────────────────────────────────

    def snapshot_widget_inputs(self) -> Dict[str, Any]:
        """Pure sink — no widget values feed into compute()."""
        return {}

    # ── Helpers (pure Python, worker-thread safe) ─────────────────────

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        """Format a byte count as a human-readable string."""
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n //= 1024
        return f"{n:.1f} TB"

    @staticmethod
    def _size_hint(value: Any) -> str:
        """Coarse human-readable size string for *value*."""
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
        """
        Assemble the full display string.

        Pure Python — safe to call from the worker thread.
        Reads ``self._show_type_header`` which is set once in ``__init__``
        and never mutated afterward.
        """
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
        """
        Convert the incoming value to a display string.

        Runs entirely on the worker thread.  The result is stored in
        ``_pending_text`` for the main thread to flush in
        ``on_evaluate_finished()``.  No Qt objects are touched here.
        """
        try:
            value = inputs.get("data")
            self._pending_text = self._build_display_text(value)
        except Exception as exc:
            log.error(f"Exception in GenericDisplayNode.compute: {exc}")
            self._pending_text = f"<error: {exc}>"

        # Sink node — no output ports to populate.
        return {}

    # ── Post-compute UI flush (main thread) ───────────────────────────

    def on_evaluate_finished(self) -> None:
        """
        Flush ``_pending_text`` to the display widget.

        Called on the **main thread** by ``ThreadedNode._on_worker_finished``
        via a ``Qt.QueuedConnection``, so Qt widget access is safe here.
        ``super()`` emits ``data_updated`` and repaints the node.
        """
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
        """Cancel any running worker then tear down widgets."""
        self._pending_text = None
        self._widget_core.cleanup()
        super().cleanup()
