# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

panel_header — Compact header widget for NodePanel
====================================================

Provides ``PanelHeader``: a slim ``QWidget`` strip that displays the
bound node's title, a persistent state badge beneath it, and an
optional *pin* toggle for dynamic panels.

Static vs Dynamic title
-----------------------
**Dynamic** panels display the node name inside the header because
the dock title bar shows a generic label (e.g. "Inspector").  The
header also shows a themed SVG node icon to the left of the title,
loaded via ``NodeIconProvider.for_header_pixmap()``.

**Static** panels do *not* display the node name or icon inside the
header because the dock title bar itself is set to the node name.
Showing it in both places would be redundant.  Call
``set_static_mode(True)`` to hide the title row (and the icon).

Pin button
----------
The pin button is a small checkable ``QPushButton`` shown only for
**dynamic** panels (hidden for static panels and when no node is
bound).  When checked the panel is "perma-linked" to the current
node — canvas selection changes are ignored until the user unchecks
the button or the node is deleted.

The button uses themed SVG icons (``lock.svg`` / ``lock-open.svg``)
from ``PanelIconProvider`` when available, falling back to Unicode
glyphs on all platforms.

Icon sizing and stroke weight
------------------------------
The node icon is sized to match the title label's ``QFontMetrics.height()``
so the icon and text are optically aligned.  The SVG stroke-width is
derived from the ``header_icon_default_width`` StyleManager property,
scaled by the title font weight (Bold = 700 → 1 × base; Normal = 400 →
~0.57 × base), mirroring the formula used by ``NodeHeader.paint()`` in
``node_components.py``.

Theme changes
-------------
The header subscribes to ``StyleManager.theme_changed`` and refreshes
both the node icon tint and the pin button icons automatically.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from weave.stylemanager import StyleManager, StyleCategory
from weave.logger import get_logger

log = get_logger("PanelHeader")


# ══════════════════════════════════════════════════════════════════════════════
# PanelHeader
# ══════════════════════════════════════════════════════════════════════════════

class PanelHeader(QWidget):
    """Compact header showing the node icon + title, state badge, and pin toggle."""

    # Emitted when the user clicks the pin button.  The argument is
    # True when the button is now *checked* (pinned).
    pin_toggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._static_mode: bool = False
        # Stored so _on_theme_changed can re-render after a palette switch.
        self._current_node_cls: Optional[type] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 4)
        outer.setSpacing(2)

        # ── Top row: [node icon] [title] [pin button] ────────────────
        self._top_row_widget = QWidget()
        top_row = QHBoxLayout(self._top_row_widget)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        # Node icon label — populated by update_node_icon(); hidden in
        # static mode and when the node has no icon.
        self._icon_label = QLabel()
        self._icon_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        self._icon_label.hide()
        top_row.addWidget(self._icon_label)

        self._title_label = QLabel()
        font = self._title_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self._title_label.setFont(font)
        top_row.addWidget(self._title_label, stretch=1)

        self._pin_btn = QPushButton()
        self._pin_btn.setCheckable(True)
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.setIconSize(QSize(14, 14))
        self._pin_btn.setToolTip("Pin this panel to the current node")
        self._pin_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 14px; }"
            "QPushButton:checked { background: rgba(255,255,255,30); "
            "border-radius: 4px; }"
        )
        self._update_pin_icon(False)
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        self._pin_btn.hide()
        top_row.addWidget(self._pin_btn)

        outer.addWidget(self._top_row_widget)

        # ── Bottom row: state badge ──────────────────────────────────
        self._state_label = QLabel()
        font_s = self._state_label.font()
        font_s.setPointSize(font_s.pointSize() - 1)
        self._state_label.setFont(font_s)
        self._state_label.setStyleSheet("color: grey;")
        outer.addWidget(self._state_label)

        # ── Subscribe to theme changes ────────────────────────────────
        StyleManager.instance().theme_changed.connect(self._on_theme_changed)

    # ──────────────────────────────────────────────────────────────────────
    # Mutators
    # ──────────────────────────────────────────────────────────────────────

    def set_title(self, text: str) -> None:
        self._title_label.setText(text)

    def set_state_text(self, text: str) -> None:
        self._state_label.setText(text)

    def set_static_mode(self, static: bool) -> None:
        """Enable or disable *static mode*.

        In static mode the entire title row (including node icon and pin
        button) is hidden because the dock title bar already shows the
        node name.  The state badge remains visible.
        """
        self._static_mode = static
        self._top_row_widget.setVisible(not static)
        if static:
            self._clear_icon_label()

    # ── Node icon ────────────────────────────────────────────────────────

    def update_node_icon(self, node_cls: type) -> None:
        """
        Render and display the SVG node icon for *node_cls*.

        Only effective for **dynamic** panels (``set_static_mode(False)``).
        Static panels never show a node icon — the dock title bar is
        already set to the node name; adding an icon would be redundant
        and confusing.

        The icon is:

        - Sized to ``QFontMetrics(title_font).height()`` so it is
          optically the same height as the title text.
        - Tinted to ``body_text_color`` from the active theme — the
          same colour that ``AppThemeBridge`` maps to
          ``QPalette.WindowText``.
        - Stroke-weight driven by ``header_icon_default_width`` ×
          (title font weight / 700), mirroring the formula used by
          ``NodeHeader.paint()`` in ``node_components.py``.

        If the node has no ``node_icon``, or the icon file cannot be
        resolved, the icon label is hidden silently.

        Parameters
        ----------
        node_cls : type
            The node class whose ``node_icon`` / ``node_icon_path``
            classvars are inspected.
        """
        if self._static_mode:
            return

        self._current_node_cls = node_cls

        # Only render when the node class declares a node_icon stem.
        if not getattr(node_cls, "node_icon", None):
            self._clear_icon_label()
            return

        pixmap = self._render_node_icon(node_cls)
        if pixmap is None or pixmap.isNull():
            self._clear_icon_label()
            return

        self._icon_label.setPixmap(pixmap)
        self._icon_label.setFixedSize(pixmap.width(), pixmap.height())
        self._icon_label.show()

    def clear_node_icon(self) -> None:
        """Hide and discard the current node icon."""
        self._current_node_cls = None
        self._clear_icon_label()

    # ── Pin button ───────────────────────────────────────────────────────

    def set_pin_visible(self, visible: bool) -> None:
        """Show or hide the pin button (hidden for static panels)."""
        self._pin_btn.setVisible(visible)

    def set_pin_checked(self, checked: bool) -> None:
        """Programmatically set the pin state without emitting pin_toggled."""
        was_blocked = self._pin_btn.signalsBlocked()
        self._pin_btn.blockSignals(True)
        self._pin_btn.setChecked(checked)
        self._update_pin_icon(checked)
        self._pin_btn.blockSignals(was_blocked)

    # ──────────────────────────────────────────────────────────────────────
    # Private — node icon rendering
    # ──────────────────────────────────────────────────────────────────────

    def _render_node_icon(self, node_cls: type) -> Optional[QPixmap]:
        """
        Delegate to ``NodeIconProvider.for_header_pixmap()`` with the
        panel's live colour, font-metric size, and stroke weight.

        Returns ``None`` on any failure so the caller can hide gracefully.
        """
        try:
            from weave.node.node_icon_provider import get_node_icon_provider
        except ImportError:
            return None

        color_hex = self._resolve_icon_color()
        fm        = QFontMetrics(self._title_label.font())
        size      = max(10, fm.height()-2)
        base_sw   = self._resolve_base_stroke_width()
        fw_int    = max(100, self._title_label.font().weight())
        stroke_w  = base_sw * (fw_int / 700.0)

        try:
            return get_node_icon_provider().for_header_pixmap(
                node_cls, color_hex, size, stroke_w
            )
        except Exception as exc:
            log.debug(f"PanelHeader: could not render node icon: {exc}")
            return None

    def _resolve_icon_color(self) -> str:
        """Resolve ``body_text_color`` from the active StyleManager theme."""
        sm = StyleManager.instance()
        for key in ("body_text_color", "title_text_color"):
            color = sm.get(StyleCategory.NODE, key)
            try:
                from PySide6.QtGui import QColor
                if isinstance(color, QColor) and color.isValid():
                    return color.name()
            except ImportError:
                pass
        return "#C8CDD7"

    def _resolve_base_stroke_width(self) -> float:
        """Read ``header_icon_default_width`` from StyleManager; fall back to 1.5."""
        val = StyleManager.instance().get(
            StyleCategory.NODE, "header_icon_default_width"
        )
        try:
            return float(val)
        except (TypeError, ValueError):
            return 1.5

    def _clear_icon_label(self) -> None:
        """Hide the icon label and release its pixmap."""
        self._icon_label.hide()
        self._icon_label.setPixmap(QPixmap())

    # ──────────────────────────────────────────────────────────────────────
    # Private — pin button
    # ──────────────────────────────────────────────────────────────────────

    def _on_pin_toggled(self, checked: bool) -> None:
        self._update_pin_icon(checked)
        self.pin_toggled.emit(checked)

    def _update_pin_icon(self, pinned: bool) -> None:
        """
        Set the pin button icon and tooltip.

        Uses themed SVG icons (``lock.svg`` / ``lock-open.svg``) from
        ``PanelIconProvider`` when available.  Falls back to Unicode
        glyphs so the button is always functional even without icon files.

        The stem mapping is:
            pinned=True  → ``lock.svg``   (panel locked to current node)
            pinned=False → ``lock-open.svg`` (panel follows selection)
        """
        ic = None
        try:
            from weave.panel.panel_icon_provider import get_panel_icon_provider
            stem = "lock" if pinned else "lock-open"
            ic   = get_panel_icon_provider().get_or_none(stem)
        except Exception:
            pass

        if ic is not None:
            self._pin_btn.setIcon(ic)
            self._pin_btn.setText("")
        else:
            # Clear any stale icon and fall back to a Unicode glyph.
            from PySide6.QtGui import QIcon
            self._pin_btn.setIcon(QIcon())
            self._pin_btn.setText(
                "\U0001F517" if pinned else "\U0001F513"  # 🔗 / 🔓
            )

        if pinned:
            self._pin_btn.setToolTip("Unpin — resume following selection")
        else:
            self._pin_btn.setToolTip("Pin this panel to the current node")

    # ──────────────────────────────────────────────────────────────────────
    # Theme refresh
    # ──────────────────────────────────────────────────────────────────────

    def _on_theme_changed(self, _theme_name: str) -> None:
        """
        Refresh all icons when the active theme changes.

        ``PanelIconProvider``'s own subscription to ``theme_changed``
        flushes its cache first, so by the time this slot runs the next
        ``get_or_none()`` call will produce freshly tinted icons.
        ``NodeIconProvider``'s ``_header_pixmap_cache`` is also flushed
        by its own subscription, so ``_render_node_icon()`` re-renders.
        """
        # Re-render the node icon with the new tint colour.
        if self._current_node_cls is not None and not self._static_mode:
            self.update_node_icon(self._current_node_cls)

        # Re-apply pin button icon with the new tint.
        self._update_pin_icon(self._pin_btn.isChecked())
