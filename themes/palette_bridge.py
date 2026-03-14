# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

palette_bridge — Single source of truth for Weave widget palette construction
=============================================================================

Resolves theme colours from ``StyleManager`` and builds a ``QPalette``
that both ``WidgetCore`` (inside node proxies) and ``AppThemeBridge``
(application-level docks / toolbars) share.

The **only** intended difference between the two consumers is the
``Window`` role:

- **WidgetCore** passes ``body_bg`` so the node body background
  matches the QPainter-drawn node fill.
- **AppThemeBridge** passes ``canvas_bg`` so dock panels, sidebars,
  and toolbars match the canvas background.

Every other role — ``Base``, ``Button``, ``Text``, ``Highlight``,
structural colours, disabled state — is identical.  Changing the
derivation here changes it everywhere.

Usage
-----
::

    from weave.themes.palette_bridge import resolve_theme_colors, build_theme_palette

    # Inside WidgetCore (node body):
    colors = resolve_theme_colors()
    pal = build_theme_palette(window_color=colors.body_bg)
    self.setPalette(pal)

    # Inside AppThemeBridge (application level):
    colors = resolve_theme_colors()
    pal = build_theme_palette(window_color=colors.canvas_bg)
    app.setPalette(pal)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor

from weave.stylemanager import StyleManager, StyleCategory


# ══════════════════════════════════════════════════════════════════════════════
# Resolved theme colours
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ThemeColors:
    """
    All resolved colours needed to build a widget palette.

    Consumers should treat this as read-only.  Call
    ``resolve_theme_colors()`` to get a fresh instance whenever the
    active theme may have changed.
    """
    canvas_bg:        QColor
    body_bg:          QColor
    body_text:        QColor
    header_bg:        QColor
    input_bg:         QColor
    disabled_text:    QColor
    placeholder_text: QColor


def resolve_theme_colors() -> ThemeColors:
    """
    Fetch and resolve the colours required for palette construction
    from the current ``StyleManager`` state.

    Colour resolution
    ~~~~~~~~~~~~~~~~~
    - ``canvas_bg``  — CANVAS → ``bg_color``
    - ``body_bg``    — NODE   → ``body_bg``
    - ``body_text``  — NODE   → ``body_text_color``, falling back to
      ``title_text_color`` for themes that predate the new property.
    - ``header_bg``  — NODE   → ``header_bg`` (used for selection
      highlight / accent)
    - ``input_bg``   — ``body_bg.darker(120)`` — background for input
      fields, spinner arrows, combo-box buttons.
    - ``disabled_text`` — ``body_text`` at ⅓ alpha.
    - ``placeholder_text`` — ``body_text`` at ½ alpha.
    """
    sm = StyleManager.instance()

    # ── Raw fetches ──────────────────────────────────────────────────
    canvas_bg = sm.get(StyleCategory.CANVAS, "bg_color")
    body_bg   = sm.get(StyleCategory.NODE,   "body_bg")
    header_bg = sm.get(StyleCategory.NODE,   "header_bg")

    # Prefer dedicated body_text_color; fall back to title_text_color
    # for backward compatibility with older themes.
    body_text = sm.get(StyleCategory.NODE, "body_text_color")
    if body_text is None or not isinstance(body_text, QColor):
        body_text = sm.get(StyleCategory.NODE, "title_text_color")

    # ── Defensive fallbacks ──────────────────────────────────────────
    if not isinstance(canvas_bg, QColor):
        canvas_bg = QColor(30, 33, 40)
    if not isinstance(body_bg, QColor):
        body_bg = QColor(38, 41, 46)
    if not isinstance(body_text, QColor):
        body_text = QColor(200, 205, 215)
    if not isinstance(header_bg, QColor):
        header_bg = QColor(32, 64, 128)

    # ── Derived colours ──────────────────────────────────────────────
    input_bg = QColor(body_bg).darker(120)

    disabled_text = QColor(body_text)
    disabled_text.setAlpha(max(0, body_text.alpha() // 3))

    placeholder_text = QColor(body_text)
    placeholder_text.setAlpha(max(0, body_text.alpha() // 2))

    return ThemeColors(
        canvas_bg=canvas_bg,
        body_bg=body_bg,
        body_text=body_text,
        header_bg=header_bg,
        input_bg=input_bg,
        disabled_text=disabled_text,
        placeholder_text=placeholder_text,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Palette construction
# ══════════════════════════════════════════════════════════════════════════════

def build_theme_palette(
    window_color: QColor,
    base_palette: Optional[QPalette] = None,
    colors: Optional[ThemeColors] = None,
) -> QPalette:
    """
    Construct a ``QPalette`` for Fusion-styled Weave widgets.

    Parameters
    ----------
    window_color : QColor
        The colour for ``QPalette.Window``.  This is the **only** role
        that differs between node-body and application-level usage:

        - ``colors.body_bg``   — for widgets inside node proxies
        - ``colors.canvas_bg`` — for dock panels, sidebars, toolbars

    base_palette : QPalette, optional
        Starting palette to modify.  If ``None``, a fresh default
        ``QPalette`` is created.

    colors : ThemeColors, optional
        Pre-resolved theme colours.  If ``None``,
        ``resolve_theme_colors()`` is called automatically.

    Returns
    -------
    QPalette
        A fully populated palette ready for ``setPalette()``.
    """
    if colors is None:
        colors = resolve_theme_colors()

    c = colors
    pal = QPalette(base_palette) if base_palette else QPalette()

    # ── Window / dock / node body surface ────────────────────────────
    pal.setColor(QPalette.ColorRole.Window,     window_color)
    pal.setColor(QPalette.ColorRole.WindowText, c.body_text)

    # ── Input fields / lists / trees ─────────────────────────────────
    pal.setColor(QPalette.ColorRole.Base,          c.input_bg)
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c.input_bg).darker(110))
    pal.setColor(QPalette.ColorRole.Text,          c.body_text)

    # ── Buttons / spinner arrows ─────────────────────────────────────
    pal.setColor(QPalette.ColorRole.Button,     c.input_bg)
    pal.setColor(QPalette.ColorRole.ButtonText, c.body_text)

    # ── Highlights / selections ──────────────────────────────────────
    pal.setColor(QPalette.ColorRole.Highlight,       c.header_bg)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    # ── Tooltips ─────────────────────────────────────────────────────
    pal.setColor(QPalette.ColorRole.ToolTipBase, c.body_bg)
    pal.setColor(QPalette.ColorRole.ToolTipText, c.body_text)

    # ── Placeholder text ─────────────────────────────────────────────
    pal.setColor(QPalette.ColorRole.PlaceholderText, c.placeholder_text)

    # ── Structural colours (Fusion sub-elements) ─────────────────────
    pal.setColor(QPalette.ColorRole.Mid,    QColor(c.body_bg).lighter(130))
    pal.setColor(QPalette.ColorRole.Light,  QColor(c.body_bg).lighter(150))
    pal.setColor(QPalette.ColorRole.Dark,   QColor(c.body_bg).darker(130))
    pal.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0, 80))

    # ── Disabled state ───────────────────────────────────────────────
    for role in (QPalette.ColorRole.Text,
                 QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c.disabled_text)

    return pal
