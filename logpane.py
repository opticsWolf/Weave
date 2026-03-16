# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

log_pane — Theme-aware dockable log-output widget
===================================================

Provides ``LogPane``, a read-only text panel that hooks into Weave's
centralised logging system via ``add_log_callback`` and derives its
colours from the active Weave theme through the ``StyleManager``
observer pattern.

When the theme changes, the pane rebuilds its palette and refreshes
per-severity text colours so the log output always matches the rest of
the application.

Usage::

    from weave.log_pane import LogPane

    pane = LogPane()                       # auto-registers with logger + style
    some_layout.addWidget(pane)            # embed anywhere

    # — or wrap in a QDockWidget —
    dock = QDockWidget("Log", parent=win)
    dock.setWidget(LogPane())
    win.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import QWidget, QPlainTextEdit, QVBoxLayout

from weave.logger import add_log_callback, remove_log_callback, get_logger
from weave.stylemanager import StyleManager, StyleCategory
from weave.themes.palette_bridge import (
    resolve_theme_colors, build_theme_palette, ThemeColors,
)

log = get_logger("LogPane")


class LogPane(QWidget):
    """
    Read-only log viewer that receives records from Weave's logger.

    Subscribes to ``StyleManager`` as an observer for the ``CANVAS``
    and ``NODE`` categories.  On every theme change the widget palette
    and per-severity text colours are rebuilt from the current theme.

    Parameters
    ----------
    parent : QWidget | None
        Parent widget.
    max_lines : int
        Maximum number of lines kept in the buffer (oldest are
        discarded).  Defaults to 2 000.
    """

    def __init__(self, parent: QWidget | None = None, max_lines: int = 2000):
        super().__init__(parent)

        self._max_lines = max_lines

        # ── Text widget ──────────────────────────────────────────────
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setMaximumBlockCount(self._max_lines)

        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._text.setFont(font)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text)

        # ── Per-severity colour cache (rebuilt on theme change) ──────
        self._level_colors: Dict[str, QColor] = {}

        # ── StyleManager integration ─────────────────────────────────
        sm = StyleManager.instance()
        sm.register(self, StyleCategory.CANVAS)
        sm.register(self, StyleCategory.NODE)

        # Apply the current theme immediately
        self._apply_theme()

        # ── Logger integration ───────────────────────────────────────
        add_log_callback(self._on_log_record)

    # ══════════════════════════════════════════════════════════════════
    # Theme / Style
    # ══════════════════════════════════════════════════════════════════

    def on_style_changed(
        self, category: StyleCategory, changes: Dict[str, Any]
    ) -> None:
        """StyleManager observer callback — refresh colours on any change."""
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Rebuild palette and per-severity colours from the active theme."""
        colors = resolve_theme_colors()

        # ── Widget palette (background, scrollbar, selection) ────────
        palette = build_theme_palette(
            window_color=colors.canvas_bg,
            colors=colors,
        )
        self._text.setPalette(palette)

        # QPlainTextEdit uses Base for its viewport background
        self._text.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  background-color: {colors.canvas_bg.darker(110).name()};"
            f"  color: {colors.body_text.name()};"
            f"  border: none;"
            f"}}"
        )

        # ── Per-severity text colours ────────────────────────────────
        self._level_colors = self._derive_level_colors(colors)

    @staticmethod
    def _derive_level_colors(colors: ThemeColors) -> Dict[str, QColor]:
        """
        Derive per-severity foreground colours from the theme.

        The mapping is intentionally simple:

        - **DEBUG** — body text dimmed to 60 % alpha (quiet)
        - **INFO** — body text at full strength (default)
        - **WARNING** — header/accent colour brightened (warm attention)
        - **ERROR** — red tinted, raised brightness (alarm)
        - **CRITICAL** — stronger red, bold (urgent)
        """
        dim = QColor(colors.body_text)
        dim.setAlpha(max(0, int(dim.alpha() * 0.6)))

        warn = QColor(colors.header_bg).lighter(160)
        # Push towards amber if the header colour is too cold
        if warn.hue() < 20 or warn.hue() > 300:
            warn = QColor(230, 180, 60)

        error = QColor(220, 70, 70)
        critical = QColor(255, 50, 50)

        return {
            "DEBUG":    dim,
            "INFO":     QColor(colors.body_text),
            "WARNING":  warn,
            "ERROR":    error,
            "CRITICAL": critical,
        }

    # ══════════════════════════════════════════════════════════════════
    # Log callback
    # ══════════════════════════════════════════════════════════════════

    def _on_log_record(self, level: str, module_tag: str, message: str):
        """Append a colour-coded line to the text area."""
        fmt = QTextCharFormat()
        fmt.setForeground(
            self._level_colors.get(level, self._level_colors.get("INFO", QColor(200, 200, 200)))
        )
        if level in ("ERROR", "CRITICAL"):
            fmt.setFontWeight(QFont.Weight.Bold)

        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(message + "\n", fmt)

        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    # ══════════════════════════════════════════════════════════════════
    # Cleanup
    # ══════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        """Unregister from logger and StyleManager."""
        remove_log_callback(self._on_log_record)

        sm = StyleManager.instance()
        sm.unregister(self)

        super().closeEvent(event)