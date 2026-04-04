# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

AppThemeBridge — Synchronises Weave's StyleManager with the Qt QPalette
========================================================================

Subscribes to NODE and CANVAS style categories and translates colour
values into a full ``QPalette`` that is applied to either:

- The global ``QApplication`` (default), so **all** standard Qt widgets
  (sidebars, property inspectors, toolbars) match the active theme, or
- A specific ``QWidget`` subtree, if you only want to skin one panel.

The bridge works best with the **Fusion** Qt style, which strictly
honours ``QPalette`` colours on every platform.  Other platform styles
(Windows, macOS) may partially ignore palette overrides.

Usage
-----
::

    from weave.app_theme_bridge import AppThemeBridge

    app = QApplication(sys.argv)
    bridge = AppThemeBridge()       # applies Fusion + skins the whole app

    # … later, from anywhere:
    apply_theme("warm")             # bridge auto-updates the palette

    # Skip automatic Fusion (use whatever style the app already has):
    bridge = AppThemeBridge(style_name="")

    # Use a different style:
    bridge = AppThemeBridge(style_name="Windows")

Architecture
------------
::

    StyleManager  ──style_changed──▶  AppThemeBridge.on_style_changed()
                                           │
                                           ▼
                                      refresh_app_palette()
                                           │
                                           ▼
                                   QApplication.setPalette()
                                           │
                                           ▼
                                   All standard Qt widgets repaint
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QStyleFactory

from weave.stylemanager import StyleManager, StyleCategory
from weave.themes.palette_bridge import resolve_theme_colors, build_theme_palette

from weave.logger import get_logger
log = get_logger("AppThemeBridge")

# ──────────────────────────────────────────────────────────────────────────────
# Default base style
# ──────────────────────────────────────────────────────────────────────────────

APP_WIDGET_STYLE: str = "Fusion"
"""Qt style name applied to the application (or target widget) by the bridge.

Fusion is the only built-in Qt style that strictly honours every
``QPalette`` role on all platforms.  Platform-native styles (Windows 11,
macOS Aqua) silently ignore many palette overrides, which prevents the
Weave theme from taking full effect on standard widgets such as sidebars,
property panels and docked inspectors.

Set this to ``""`` (empty string) before creating the bridge to skip
automatic style application and rely on whatever style the application
already uses.
"""


# ──────────────────────────────────────────────────────────────────────────────
# AppThemeBridge
# ──────────────────────────────────────────────────────────────────────────────

class AppThemeBridge(QObject):
    """
    Listens to Weave's ``StyleManager`` and pushes ``QPalette`` updates
    so that standard Qt widgets match the active node-canvas theme.

    On construction the bridge optionally applies a palette-friendly Qt
    style (default: **Fusion**) to the target.  This is necessary because
    platform-native styles (Windows 11, macOS Aqua) ignore most
    ``QPalette`` roles, making theme colours invisible.

    Parameters
    ----------
    target : QWidget | QApplication | None
        The widget (or application) whose palette should be updated.
        Pass ``None`` (default) to target the running ``QApplication``.
    style_name : str | None
        Qt style to apply to the target before palette work.
        ``None`` (default) uses the module-level ``APP_WIDGET_STYLE``
        constant (``"Fusion"``).  Pass ``""`` to skip automatic style
        application entirely.
    parent : QObject | None
        Optional QObject parent for preventing premature garbage-collection.
    """

    def __init__(
        self,
        target: Optional[Union[QWidget, QApplication]] = None,
        style_name: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        self._target: Union[QWidget, QApplication] = (
            target if target is not None else QApplication.instance()
        )
        if self._target is None:
            raise RuntimeError(
                "AppThemeBridge requires a running QApplication. "
                "Create one before instantiating the bridge."
            )

        # Debounce tracking flag
        self._refresh_scheduled = False

        # ── Apply a palette-friendly base style ──────────────────────
        # Must happen BEFORE the palette is set: the style determines
        # which QPalette roles the paint code actually consults.
        resolved_style = style_name if style_name is not None else APP_WIDGET_STYLE
        self._apply_base_style(resolved_style)

        # Subscribe to the categories that provide the colours we map.
        sm = StyleManager.instance()
        sm.register(self, StyleCategory.NODE)
        sm.register(self, StyleCategory.CANVAS)

        # Apply immediately so the palette matches the theme that is
        # already active when the bridge is created.
        self.refresh_app_palette()

    # ──────────────────────────────────────────────────────────────────────
    # Base style application
    # ──────────────────────────────────────────────────────────────────────

    def _apply_base_style(self, style_name: str) -> None:
        """
        Apply a Qt widget style to the target.

        For ``QApplication`` targets, ``app.setStyle()`` is used which
        affects every widget in the process.  For ``QWidget`` targets,
        ``widget.setStyle()`` is used which only affects that subtree.

        Parameters
        ----------
        style_name : str
            Name of the Qt style to create (e.g. ``"Fusion"``).
            If empty, no style is applied.
        """
        if not style_name:
            return

        style = QStyleFactory.create(style_name)
        if style is None:
            log.warning(
                f"QStyleFactory could not create '{style_name}' style. "
                f"Available: {QStyleFactory.keys()}.  "
                f"Theme colours may not render correctly on native widgets."
            )
            return

        if isinstance(self._target, QApplication):
            self._target.setStyle(style)
            log.debug(f"Applied '{style_name}' style to QApplication.")
        else:
            self._target.setStyle(style)
            log.debug(
                f"Applied '{style_name}' style to widget "
                f"'{self._target.objectName() or type(self._target).__name__}'."
            )

    # ──────────────────────────────────────────────────────────────────────
    # StyleManager callback
    # ──────────────────────────────────────────────────────────────────────

    def on_style_changed(
        self, category: StyleCategory, changes: Dict[str, Any]
    ) -> None:
        """Called by ``StyleManager`` when NODE or CANVAS styles change."""
        # Debounce the global palette refresh. If NODE and CANVAS both
        # update in the same frame, we only rebuild the palette once.
        if not self._refresh_scheduled:
            self._refresh_scheduled = True
            QTimer.singleShot(0, self._execute_refresh)

    def _execute_refresh(self) -> None:
        """Execute the deferred palette refresh."""
        self._refresh_scheduled = False
        self.refresh_app_palette()

    # ──────────────────────────────────────────────────────────────────────
    # Palette construction
    # ──────────────────────────────────────────────────────────────────────

    def refresh_app_palette(self) -> None:
        """
        Build a ``QPalette`` from the current Weave theme and apply it.

        Uses ``palette_bridge.build_theme_palette()`` — the same builder
        that ``WidgetCore`` uses — so that input widgets (spinboxes,
        combos, line-edits) look identical in dock panels and inside
        node proxies.

        The only difference is the ``Window`` role: here we pass
        ``canvas_bg`` (the scene background) so dock panels and
        toolbars match the canvas.  ``WidgetCore`` passes ``body_bg``
        (the node body fill) instead.
        """
        colors = resolve_theme_colors()
        palette = build_theme_palette(
            window_color=colors.canvas_bg,
            base_palette=self._target.palette(),
            colors=colors,
        )
        self._target.setPalette(palette)
        log.debug("App palette refreshed from Weave theme.")
