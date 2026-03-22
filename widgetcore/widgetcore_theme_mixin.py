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

How state-overlay transparency works
-------------------------------------
``Node.paint()`` draws ``body_bg`` then alpha-composites the state overlay
(DISABLED, PASSTHROUGH, …) directly onto the ``QGraphicsItem`` canvas.
The ``QGraphicsProxyWidget`` is rendered on top of that canvas.

``WidgetCore`` itself has ``autoFillBackground=False`` — it does **not**
paint a background.  Every layout container beneath it (``QFrame``,
``QGroupBox``, etc.) is also made transparent by
``secure_node_transparency``.  This means the entire widget area is
composited over what the scene already painted, and the state overlay is
always visible underneath without any palette blending.

The palette is still applied (for ``QPalette.Text``, ``Base``,
``Button``, ``Highlight`` etc.) so that interactive leaf widgets
(spinboxes, combos, line-edits) are correctly styled.  Only the
``Window`` role (the widget fill colour) is irrelevant since
``autoFillBackground`` is off.
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
fully honours ``QPalette``."""

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
        """Apply Fusion style and palette to the widget.

        ``WidgetCore`` itself is transparent (``autoFillBackground=False``).
        The palette is still built and applied so that interactive leaf
        widgets (``QPalette.Base``, ``Text``, ``Button``, etc.) are
        correctly coloured.  The state overlay shows through from the
        QPainter canvas without any blending needed here.

        Also updates the proxy root widget palette (whose
        ``parentWidget()`` is None and would otherwise fall back to
        ``QApplication::palette()``).
        """
        from weave.themes.palette_bridge import (
            resolve_theme_colors, resolve_node_colors, build_theme_palette,
            secure_node_transparency,
        )

        style = get_proxy_style()
        if style is not None:
            self.setStyle(style)

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

        # WidgetCore does NOT fill its background — the scene canvas shows through.
        self.setAutoFillBackground(False)
        self.setPalette(pal)

        # Proxy root palette (needed because its parentWidget() is None).
        proxy = self._find_proxy()
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                root.setPalette(pal)
                # The proxy root must never paint a background — the
                # QPainter-drawn node body (incl. state overlay) must
                # show through.  Re-assert after every palette set.
                root.setAutoFillBackground(False)

        # Re-assert transparency on all container children after palette
        # propagation (which resets autoFillBackground on children).
        secure_node_transparency(self)
        # Also cover the proxy root and its subtree — secure_node_transparency
        # walks *downward* from its argument, and the proxy root is
        # WidgetCore's parent, so the self-rooted walk never reaches it.
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                secure_node_transparency(root)

    def _apply_full_proxy_theme(self: QWidget) -> None:
        """Post-patch theme setup: Fusion + palette on proxy root and children.

        Called by WidgetCore after ``_patch_parent_proxy`` succeeds.
        This is the primary installation point for ``secure_node_transparency``:
        by the time it runs, the proxy exists and all initial children are
        already in the tree.
        """
        from weave.themes.palette_bridge import (
            resolve_theme_colors, build_theme_palette,
            secure_node_transparency,
        )

        proxy = self._find_proxy()
        if proxy is None:
            return

        style = get_proxy_style()
        root = proxy.widget()

        if root is not None:
            if style is not None:
                root.setStyle(style)
            root.setAutoFillBackground(False)
            root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

            if root is not self:
                colors = resolve_theme_colors()
                root.setPalette(build_theme_palette(
                    window_color=colors.body_bg, colors=colors,
                ))

        # Clear stale explicit palettes on children so they inherit.
        for child in self.findChildren(QWidget):
            try:
                child.setAttribute(Qt.WidgetAttribute.WA_SetPalette, False)
            except RuntimeError:
                pass

        # Apply palette to WidgetCore (transparent background, correct roles).
        # _apply_container_background also calls secure_node_transparency.
        self._apply_container_background()

    # ── Public palette API ───────────────────────────────────────────────

    def apply_node_palette(
        self: QWidget,
        header_bg: QColor,
        body_bg: Optional[QColor] = None,
    ) -> None:
        """Rebuild the palette from the node's effective colours.

        Called by the node's ``_update_colors()`` whenever effective
        colours change (selection, custom header, state transition, …).

        ``WidgetCore`` is transparent, so the state overlay painted by
        ``Node.paint()`` is always visible beneath the widget area — no
        colour blending is needed here.  The palette update still matters
        for ``QPalette.Base`` / ``Text`` / ``Button`` / ``Highlight``
        (used by spinboxes, combos, etc.) and for the ``Highlight`` role
        which reflects per-node header colours.
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
                root.setAutoFillBackground(False)

        # Re-assert container transparency after palette propagation.
        secure_node_transparency(self)
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                secure_node_transparency(root)

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
