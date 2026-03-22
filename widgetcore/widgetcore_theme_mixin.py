# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

widgets._theme — Mixin for theme/palette management inside proxy widgets.

Encapsulates:
- Fusion style caching and application.
- Palette construction from node colours or global theme.
- Proxy-root palette synchronisation.
- StyleManager callback (``on_style_changed``).
- QSS generation for fine-grained styling.
- Container transparency (via ``secure_node_transparency``).

Container transparency
----------------------
After every ``setPalette()`` call Qt propagates the new palette down the
widget tree, which resets ``autoFillBackground`` on children to ``True``
and makes layout containers (``QFrame``, ``QGroupBox``, ``QScrollArea``,
…) opaque again.  This blocks the QPainter-drawn node body fill and the
per-state overlay colour, so nodes in DISABLED or PASSTHROUGH state
appear unchanged inside embedded widgets.

The fix is to call ``secure_node_transparency(self)`` after every palette
application.  That function installs a persistent ``ContainerTransparencyFilter``
(a ``QObject`` event filter) on the root and all container children.  The
filter intercepts ``PaletteChange`` and ``ChildAdded`` events, re-asserting
transparency automatically for the lifetime of the node.  It is idempotent —
safe to call on every palette refresh.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QStyleFactory
from PySide6.QtGui import QColor

from weave.logger import get_logger

if TYPE_CHECKING:
    from weave.stylemanager import StyleCategory

log = get_logger("ThemeMixin")


# ══════════════════════════════════════════════════════════════════════════════
# Shared Fusion style instance
# ══════════════════════════════════════════════════════════════════════════════

PROXY_WIDGET_STYLE: str = "Fusion"
"""Qt style name applied to widgets inside nodes.  Must be a style that
fully honours ``QPalette``.  Change this before creating any nodes if you
need a different style (e.g. ``"Windows"`` for testing)."""

_fusion_style = None


def get_proxy_style():
    """Return a cached QStyle instance for use inside proxy widgets."""
    global _fusion_style
    if _fusion_style is None:
        _fusion_style = QStyleFactory.create(PROXY_WIDGET_STYLE)
        if _fusion_style is None:
            log.warning(
                f"QStyleFactory could not create '{PROXY_WIDGET_STYLE}' style. "
                f"Available styles: {QStyleFactory.keys()}. "
                f"Widget theming inside nodes may not work correctly."
            )
        else:
            _fusion_style.setObjectName("fusion")
    return _fusion_style


# ══════════════════════════════════════════════════════════════════════════════
# Mixin
# ══════════════════════════════════════════════════════════════════════════════

class ThemeMixin:
    """Mixin handling palette and style synchronisation.

    Expects the host class to also include ``ProxyMixin`` (for
    ``_find_proxy``) and to have a ``_node_ref`` attribute.
    """

    # ── StyleManager callback ────────────────────────────────────────────

    def on_style_changed(
        self: QWidget,
        category: "StyleCategory",
        changes: Dict[str, Any],
    ) -> None:
        """Called by ``StyleManager`` when the active theme changes."""
        from weave.stylemanager import StyleCategory
        if category == StyleCategory.NODE:
            self._apply_container_background()

    # ── Core palette application ─────────────────────────────────────────

    def _apply_container_background(self: QWidget) -> None:
        """Apply Fusion style and node-body palette to the widget.

        When a back-reference to the owning node is available, the
        palette is derived from the node's effective header/body colours
        (including selection highlights and per-node overrides).

        Also updates the proxy root widget palette (whose
        ``parentWidget()`` is None and would otherwise fall back to
        ``QApplication::palette()``).

        After ``setPalette()``, calls ``secure_node_transparency(self)``
        so that Qt's palette propagation — which resets
        ``autoFillBackground`` on children — cannot make containers
        opaque again.
        """
        from weave.themes.palette_bridge import (
            resolve_theme_colors, resolve_node_colors, build_theme_palette,
            secure_node_transparency,
        )

        # Ensure Fusion is (still) active — reparenting may wipe it.
        style = get_proxy_style()
        if style is not None:
            self.setStyle(style)

        # Use the owning node's effective colours when available.
        node = self._node_ref
        if node is not None and hasattr(node, "header") and hasattr(node, "body"):
            colors = resolve_node_colors(
                node.header._bg_color, node.body._bg_color,
            )
        else:
            colors = resolve_theme_colors()

        pal = build_theme_palette(
            window_color=colors.body_bg,
            base_palette=self.palette(),
            colors=colors,
        )
        self.setAutoFillBackground(True)
        self.setPalette(pal)

        # Proxy root — parentWidget() is None so needs explicit palette.
        proxy = self._find_proxy()
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                root.setPalette(pal)

        # Re-assert container transparency after palette propagation.
        # setPalette() resets autoFillBackground on children; the filter
        # installed here counters that for every subsequent palette change.
        secure_node_transparency(self)

    def _apply_full_proxy_theme(self: QWidget) -> None:
        """Post-patch theme setup: Fusion + palette on proxy root and children.

        Called by WidgetCore after _patch_parent_proxy succeeds.
        Handles the proxy-root styling, child palette clearing, and
        WidgetCore palette application as a single atomic step.

        After all palette work, calls ``secure_node_transparency(self)``
        to enroll the full widget tree in the reactive transparency filter.
        This is the primary installation point: by the time this runs the
        proxy exists and all initial child widgets are already in the tree,
        so the filter's recursive walk covers everything.
        """
        from weave.themes.palette_bridge import (
            resolve_theme_colors, build_theme_palette,
            secure_node_transparency,
        )

        proxy = self._find_proxy()
        if proxy is None:
            return

        # Fusion style on proxy root
        style = get_proxy_style()
        root = proxy.widget()

        if root is not None:
            if style is not None:
                root.setStyle(style)
            root.setAutoFillBackground(False)

            if root is not self:
                colors = resolve_theme_colors()
                root.setPalette(build_theme_palette(
                    window_color=colors.body_bg, colors=colors,
                ))

        # Clear stale explicit palettes on children so they inherit.
        for child in self.findChildren(QWidget):
            try:
                child.setAttribute(
                    Qt.WidgetAttribute.WA_SetPalette, False,
                )
            except RuntimeError:
                pass

        # Apply body palette to WidgetCore (propagates to children).
        # _apply_container_background already calls secure_node_transparency,
        # but we call it explicitly here too so the filter is installed even
        # before _apply_container_background's setPalette propagation fires.
        self._apply_container_background()

        # Enroll the full tree now that the proxy root and all initial
        # children are in place.  Idempotent — safe to call again.
        secure_node_transparency(self)

    # ── Public palette API ───────────────────────────────────────────────

    def apply_node_palette(
        self: QWidget,
        header_bg: QColor,
        body_bg: Optional[QColor] = None,
    ) -> None:
        """Rebuild the palette from the node's effective colours.

        Called by the node's ``_update_colors()`` whenever effective
        colours change (selection, custom header, state transition, …).

        After ``setPalette()``, calls ``secure_node_transparency(self)``
        to re-assert container transparency, which Qt's palette
        propagation would otherwise reset.
        """
        from weave.themes.palette_bridge import (
            resolve_node_colors, build_theme_palette,
            secure_node_transparency,
        )

        colors = resolve_node_colors(header_bg, body_bg)
        pal = build_theme_palette(
            window_color=colors.body_bg,
            base_palette=self.palette(),
            colors=colors,
        )
        self.setPalette(pal)

        proxy = self._find_proxy()
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                root.setPalette(pal)

        # Re-assert container transparency after every palette update.
        # This is the call site that fires on every state change (the node's
        # _update_colors → BaseControlNode._update_colors → apply_node_palette).
        # The filter is idempotent: widgets already managed are skipped in O(1).
        secure_node_transparency(self)

    def refresh_widget_palettes(self: QWidget) -> None:
        """Force-refresh palettes on all child widgets.

        .. deprecated::
            Retained for backward compatibility.  Delegates to
            ``_apply_container_background()``.
        """
        self._apply_container_background()

    def refresh_widget_stylesheets(self: QWidget, *, extra_qss: str = "") -> None:
        """Apply a scoped stylesheet for details QPalette cannot control.

        .. note::
            Prefer ``refresh_widget_palettes()`` for most cases.
            QSS parsing is heavier and can cause micro-stutter when
            nodes are moved/resized at high frame-rates.
        """
        from weave.themes.palette_bridge import resolve_theme_colors

        c = resolve_theme_colors()
        accent_hex = c.header_bg.name()
        text_hex = c.body_text.name()
        input_rgba = (
            f"rgba({c.input_bg.red()}, {c.input_bg.green()}, "
            f"{c.input_bg.blue()}, {c.input_bg.alpha()})"
        )

        qss = (
            f"QLineEdit, QSpinBox, QDoubleSpinBox {{\n"
            f"    background-color: {input_rgba};\n"
            f"    border: 1px solid {accent_hex};\n"
            f"    border-radius: 4px;\n"
            f"    color: {text_hex};\n"
            f"    padding: 2px;\n"
            f"}}\n"
            f"QComboBox::drop-down {{\n"
            f"    border-left: 1px solid {accent_hex};\n"
            f"}}\n"
        )
        if extra_qss:
            qss += extra_qss
        self.setStyleSheet(qss)
