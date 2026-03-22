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
    from weave.themes.palette_bridge import secure_node_transparency

    # Inside WidgetCore (node body) — call once during init, stays permanent:
    secure_node_transparency(self)

    # Inside AppThemeBridge (application level):
    colors = resolve_theme_colors()
    pal = build_theme_palette(window_color=colors.canvas_bg)
    app.setPalette(pal)

Container transparency
----------------------
``WidgetCore`` itself is transparent (``autoFillBackground=False``), and
so are all layout containers beneath it (``QFrame``, ``QGroupBox``, etc.).
This means the widget area is simply composited over whatever the scene
has already painted — specifically, the QPainter-drawn node body fill
**plus** the state overlay for DISABLED / PASSTHROUGH / COMPUTING.  No
palette blending or overlay simulation is needed; the correct colours are
already on the canvas.

``secure_node_transparency(root)`` installs a persistent
``ContainerTransparencyFilter`` on the root and all container children.
The filter self-propagates via ``ChildAdded`` and counters Qt's palette
propagation (which resets ``autoFillBackground``) via ``PaletteChange``.

Opt-out
~~~~~~~
Set ``widget.setProperty("opaque_bg", True)`` on any container that must
keep its own solid background.  The filter skips such widgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QGroupBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QStackedWidget,
    QAbstractItemView,
    QTextEdit,
    QPlainTextEdit,
)

from weave.stylemanager import StyleManager, StyleCategory


# ══════════════════════════════════════════════════════════════════════════════
# Container-type registry
# ══════════════════════════════════════════════════════════════════════════════

# Widget types that are purely structural / layout containers.
# These have no meaningful visual content of their own and should let the
# QPainter-drawn node body (including state overlays) show through.
#
# ``QAbstractScrollArea`` is intentionally NOT listed here — it is too broad.
# ``QTextEdit``, ``QPlainTextEdit``, ``QListView``, ``QTreeView``,
# ``QTableView`` all inherit from it and would lose their backgrounds.
# ``QScrollArea`` (the specific non-interactive viewport wrapper) is listed
# explicitly instead.
_TRANSPARENT_CONTAINER_TYPES: tuple[type, ...] = (
    QFrame,              # generic divider / panel
    QGroupBox,           # labelled container
    QScrollArea,         # scroll-viewport wrapper
    QSplitter,           # resizable splitter handle container
    QTabWidget,          # tab bar + stacked pages
    QStackedWidget,      # page-switcher (no chrome of its own)
)

# Interactive widget types that happen to inherit from a container base class
# (typically QFrame) but must NEVER be made transparent because they paint
# their own meaningful background.  Also covers QComboBox popup internals:
# the dropdown list is a QListView → QAbstractItemView → QFrame.
_OPAQUE_INTERACTIVE_TYPES: tuple[type, ...] = (
    QTextEdit,           # QAbstractScrollArea → QFrame
    QPlainTextEdit,      # QAbstractScrollArea → QFrame
    QAbstractItemView,   # QListView, QTreeView, QTableView, QHeaderView, …
)

# Stylesheet applied to every matched container.
_CONTAINER_STYLESHEET: str = "background: transparent; border: none;"

# Property key used to flag widgets already managed by the filter.
_MANAGED_PROP: str = "_tw_transparency_managed"


def _should_make_transparent(widget: QWidget) -> bool:
    """Decide whether *widget* should be forced transparent.

    Returns True only when the widget:
    - matches a known container type,
    - is NOT an interactive widget that inherits from a container base
      (e.g. QTextEdit → QFrame, QListView → QFrame),
    - is NOT a top-level / popup window (e.g. QComboBox dropdown), and
    - has not opted out via the ``opaque_bg`` dynamic property.
    """
    if widget.property("opaque_bg"):
        return False
    if not isinstance(widget, _TRANSPARENT_CONTAINER_TYPES):
        return False
    if isinstance(widget, _OPAQUE_INTERACTIVE_TYPES):
        return False
    # Top-level windows (popups, tooltips, dialogs) must keep their
    # background — they are not part of the node body compositing.
    if widget.isWindow():
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# _force_transparent — shared primitive
# ══════════════════════════════════════════════════════════════════════════════

def _force_transparent(widget: QWidget) -> None:
    """Apply the three attributes that make a container truly transparent.

    Called only from ``_install_and_apply`` (the initial walk), never from
    inside the event filter — calling ``setStyleSheet`` inside a
    ``PaletteChange`` handler triggers another ``PaletteChange``,
    causing infinite recursion.
    """
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    widget.setAutoFillBackground(False)
    widget.setStyleSheet(_CONTAINER_STYLESHEET)


# ══════════════════════════════════════════════════════════════════════════════
# ContainerTransparencyFilter — reactive event filter
# ══════════════════════════════════════════════════════════════════════════════

class ContainerTransparencyFilter(QObject):
    """
    A singleton event filter installed on all container widgets inside a
    node's ``WidgetCore``.

    Handles two event types:

    ``QEvent.Type.ChildAdded``
        Fired on a watched widget when a new ``QWidget`` child is added
        to it at runtime.  The filter immediately installs itself on the
        new child and applies transparency if it is a container type.

    ``QEvent.Type.PaletteChange``
        Fired after Qt propagates a palette down the tree.  Qt's
        propagation resets ``autoFillBackground`` on children to ``True``,
        making containers opaque again.  The filter re-asserts
        ``autoFillBackground=False`` immediately.

        **Important:** only ``autoFillBackground`` is touched here.
        Calling ``setStyleSheet`` inside a ``PaletteChange`` handler
        fires ``StyleChange`` → ``PaletteChange`` on the same widget,
        causing infinite recursion.  The stylesheet set during the
        initial ``_install_and_apply`` walk persists across palette
        changes and does not need to be reapplied.

    Do not instantiate directly; use ``secure_node_transparency()``.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if not isinstance(obj, QWidget):
            return False

        etype = event.type()

        if etype == QEvent.Type.ChildAdded:
            child = event.child()  # type: ignore[attr-defined]
            if isinstance(child, QWidget):
                self._install_and_apply(child)

        elif etype == QEvent.Type.PaletteChange:
            # Re-assert autoFillBackground ONLY — no setStyleSheet here.
            if _should_make_transparent(obj):
                obj.setAutoFillBackground(False)

        return False  # never consume events

    def _install_and_apply(self, widget: QWidget) -> None:
        """Install the filter on *widget* and apply transparency if applicable.

        Skips already-managed widgets.  Recurses into existing children.
        """
        if widget.property(_MANAGED_PROP):
            return

        widget.setProperty(_MANAGED_PROP, True)
        widget.installEventFilter(self)

        if _should_make_transparent(widget):
            _force_transparent(widget)

        for child in widget.children():
            if isinstance(child, QWidget):
                self._install_and_apply(child)


# ══════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ══════════════════════════════════════════════════════════════════════════════

_TRANSPARENCY_FILTER = ContainerTransparencyFilter()


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def secure_node_transparency(root: QWidget) -> None:
    """
    Make all current and future container children of *root* permanently
    transparent so the QPainter-drawn node body (including state overlays)
    always shows through.

    Safe to call multiple times — already-managed widgets are skipped.

    Parameters
    ----------
    root : QWidget
        The top-level container to protect.  Typically ``WidgetCore``
        (``self``) inside a node body.
    """
    _TRANSPARENCY_FILTER._install_and_apply(root)


def make_containers_transparent(root: QWidget) -> None:
    """
    One-shot recursive walk: make every container in *root*'s subtree
    transparent right now.

    .. deprecated::
        Prefer ``secure_node_transparency()`` which installs a reactive
        event filter and is therefore permanent.
    """
    _walk_and_make_transparent(root, is_root=True)


def _walk_and_make_transparent(widget: QWidget, is_root: bool = False) -> None:
    """Recursive implementation for ``make_containers_transparent``."""
    if not is_root and _should_make_transparent(widget):
        _force_transparent(widget)
    for child in widget.children():
        if isinstance(child, QWidget):
            _walk_and_make_transparent(child, is_root=False)


# ══════════════════════════════════════════════════════════════════════════════
# Overlay blending
# ══════════════════════════════════════════════════════════════════════════════

def alpha_blend(base: QColor, overlay: QColor) -> QColor:
    """Alpha-composite *overlay* over *base*, returning an opaque QColor.

    Standard Porter-Duff "source over".  The result always has alpha 255
    so it can be used directly as a ``QPalette`` role colour.
    """
    oa = overlay.alphaF()
    if oa < 0.001:
        return QColor(base)
    if oa > 0.999:
        return QColor(overlay)

    ba = base.alphaF()
    inv_oa = 1.0 - oa

    r = overlay.redF()   * oa + base.redF()   * ba * inv_oa
    g = overlay.greenF() * oa + base.greenF() * ba * inv_oa
    b = overlay.blueF()  * oa + base.blueF()  * ba * inv_oa
    a = oa + ba * inv_oa

    if a < 0.001:
        return QColor(0, 0, 0, 255)

    return QColor.fromRgbF(
        min(1.0, r / a),
        min(1.0, g / a),
        min(1.0, b / a),
        1.0,
    )


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
    """
    sm = StyleManager.instance()

    canvas_bg = sm.get(StyleCategory.CANVAS, "bg_color")
    body_bg   = sm.get(StyleCategory.NODE,   "body_bg")
    header_bg = sm.get(StyleCategory.NODE,   "header_bg")

    body_text = sm.get(StyleCategory.NODE, "body_text_color")
    if body_text is None or not isinstance(body_text, QColor):
        body_text = sm.get(StyleCategory.NODE, "title_text_color")

    if not isinstance(canvas_bg, QColor):
        canvas_bg = QColor(30, 33, 40)
    if not isinstance(body_bg, QColor):
        body_bg = QColor(38, 41, 46)
    if not isinstance(body_text, QColor):
        body_text = QColor(200, 205, 215)
    if not isinstance(header_bg, QColor):
        header_bg = QColor(32, 64, 128)

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


def resolve_node_colors(
    header_bg: QColor,
    body_bg: Optional[QColor] = None,
) -> ThemeColors:
    """
    Build a ``ThemeColors`` with per-node overrides for header and body.
    """
    base = resolve_theme_colors()
    effective_body = body_bg if body_bg is not None else base.body_bg
    effective_input = QColor(effective_body).darker(120)
    return ThemeColors(
        canvas_bg=base.canvas_bg,
        body_bg=effective_body,
        body_text=base.body_text,
        header_bg=header_bg,
        input_bg=effective_input,
        disabled_text=base.disabled_text,
        placeholder_text=base.placeholder_text,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Palette construction
# ══════════════════════════════════════════════════════════════════════════════

def build_theme_palette(
    window_color: QColor,
    base_palette: Optional[QPalette] = None,
    colors: Optional[ThemeColors] = None,
    overlay: Optional[QColor] = None,
) -> QPalette:
    """
    Construct a ``QPalette`` for Fusion-styled Weave widgets.

    Parameters
    ----------
    window_color : QColor
        ``QPalette.Window`` colour.  Pass ``colors.body_bg`` for node
        proxies; ``colors.canvas_bg`` for dock panels / toolbars.
    base_palette : QPalette, optional
        Starting palette.  ``None`` creates a fresh default ``QPalette``.
    colors : ThemeColors, optional
        Pre-resolved colours.  ``None`` calls ``resolve_theme_colors()``.
    overlay : QColor, optional
        State overlay colour (from ``_apply_state_visuals``).  When its
        alpha is > 0, the interactive-widget palette roles (``Base``,
        ``AlternateBase``, ``Button``) are alpha-blended so leaf widgets
        (spinboxes, combos, line-edits) visually reflect the node state.

        The ``Window`` role is intentionally **not** blended: WidgetCore
        and its containers are transparent (``autoFillBackground=False``)
        so the QPainter-drawn node body — including the state overlay —
        shows through directly.  Blending ``Window`` would cause the
        proxy root widget to paint a second, redundant tinted fill.
    """
    if colors is None:
        colors = resolve_theme_colors()

    c = colors

    def _blend(color: QColor) -> QColor:
        """Blend *overlay* into an interactive-widget background colour."""
        if overlay is not None and overlay.alpha() > 0:
            return alpha_blend(color, overlay)
        return QColor(color)

    pal = QPalette(base_palette) if base_palette else QPalette()

    # Window role — NOT blended; containers are transparent.
    pal.setColor(QPalette.ColorRole.Window,     window_color)
    pal.setColor(QPalette.ColorRole.WindowText, c.body_text)

    # Interactive leaf-widget roles — blended with state overlay.
    pal.setColor(QPalette.ColorRole.Base,          _blend(c.input_bg))
    pal.setColor(QPalette.ColorRole.AlternateBase, _blend(QColor(c.input_bg).darker(110)))
    pal.setColor(QPalette.ColorRole.Text,          c.body_text)

    pal.setColor(QPalette.ColorRole.Button,     _blend(c.input_bg))
    pal.setColor(QPalette.ColorRole.ButtonText, c.body_text)

    pal.setColor(QPalette.ColorRole.Highlight,       c.header_bg)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    pal.setColor(QPalette.ColorRole.ToolTipBase, c.body_bg)
    pal.setColor(QPalette.ColorRole.ToolTipText, c.body_text)

    pal.setColor(QPalette.ColorRole.PlaceholderText, c.placeholder_text)

    # Structural roles — NOT blended; these are for frame edges and
    # sunken/raised borders that should stay consistent.
    pal.setColor(QPalette.ColorRole.Mid,    QColor(c.body_bg).lighter(130))
    pal.setColor(QPalette.ColorRole.Light,  QColor(c.body_bg).lighter(150))
    pal.setColor(QPalette.ColorRole.Dark,   QColor(c.body_bg).darker(130))
    pal.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0, 80))

    for role in (QPalette.ColorRole.Text,
                 QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c.disabled_text)

    return pal
