# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

node_icon_provider — Context-aware node icon rendering
=======================================================

Renders node SVG icons in three distinct contexts:

- **Menu (node)** — ``for_menu(node_cls)``
  Tinted to the active theme's menu-text colour (``body_text_color``),
  sized to ``PM_SmallIconSize`` via ``scale_pixmap_to_menu()``.  Uses
  the ``node_icon`` + ``node_icon_path`` classvars.

- **Menu (category)** — ``for_menu_class(node_cls)``
  Same tint/size as ``for_menu()``, but uses ``node_class_icon``
  instead of ``node_icon``.  Intended for category-level ``QMenu``
  entries in the Browse Nodes hierarchy.

- **Menu (subcategory)** — ``for_menu_subclass(node_cls)``
  Same tint/size, uses ``node_subclass_icon``.  Intended for
  subcategory-level menu entries.

- **Header** — ``for_header(node_cls, header_bg)``
  Tinted to a colour contrasting against the node header background
  (WCAG luminance), sized larger for in-canvas display.  Uses
  ``node_icon``.

Icon stem resolution
--------------------
``node_icon``, ``node_class_icon``, and ``node_subclass_icon`` are all
**stem names** (e.g. ``"blur"``), *not* full paths.
``_resolve_full_path()`` combines the stem with the directory from
``node_icon_path`` to produce an absolute path:

1. If the stem starts with ``:/``, it is treated as a Qt resource path.
2. If ``node_icon_path`` is absolute, the full path is validated directly.
3. If ``node_icon_path`` is relative, it is resolved relative to the
   Python source file of the module that defines the node class.
4. Last resort: resolved relative to the current working directory.

SVG source sharing
------------------
All ``SvgIconLoader`` instances are obtained through the shared
``get_or_create_loader()`` registry in ``icon_loader``.  If a node
icon lives in the same directory as the menu icons, both providers
share a single loader — the directory is scanned and each file read
from disk exactly once per process.

Architecture
------------
::

    BaseControlNode.node_icon / node_class_icon / node_subclass_icon  (stem)
    BaseControlNode.node_icon_path                                     (directory)
          │
          ▼
    NodeIconProvider._resolve_full_path(node_cls, stem_attr, dir_attr)
          │
          ├── for_menu(node_cls)                    → QIcon  (text colour, menu size)
          ├── for_menu_class(node_cls)              → QIcon  (text colour, menu size)
          ├── for_menu_subclass(node_cls)           → QIcon  (text colour, menu size)
          └── for_header(node_cls, header_bg)       → QIcon  (contrast colour, header size)
                    │
                    └── get_or_create_loader(directory)  ← shared with MenuIconProvider
                              │
                              └── SvgIconLoader._svg_cache  (one copy per directory)

Usage
-----
::

    from weave.node_icon_provider import get_node_icon_provider

    provider = get_node_icon_provider()

    # Individual node icon in a menu:
    if ic := provider.for_menu(node_cls):
        action.setIcon(ic)

    # Category icon for a Browse-Nodes submenu:
    if ic := provider.for_menu_class(representative_node_cls):
        cat_menu.setIcon(ic)

    # Subcategory icon:
    if ic := provider.for_menu_subclass(representative_node_cls):
        sub_menu.setIcon(ic)

    # In a node header (pass the resolved header QColor):
    if ic := provider.for_header(node_cls, header_bg_color):
        painter.drawPixmap(rect, ic.pixmap(header_size, header_size))
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, TYPE_CHECKING

from PySide6.QtGui import QColor, QIcon

from weave.themes.icon_loader import (
    SvgIconLoader,
    get_or_create_loader,
    scale_pixmap_to_menu,
    menu_icon_size,
)
from weave.stylemanager import StyleManager, StyleCategory
from weave.logger import get_logger

if TYPE_CHECKING:
    from weave.noderegistry import NodeCls

log = get_logger("NodeIconProvider")


# ============================================================================
# Contrast colour helper
# ============================================================================

def _contrast_color(bg: QColor, light: str = "#E8ECF4", dark: str = "#1A1C22") -> str:
    """
    Return a hex colour that contrasts legibly against *bg*.

    Uses the WCAG relative-luminance formula.  Returns *light* for dark
    backgrounds and *dark* for light backgrounds.

    Parameters
    ----------
    bg : QColor
        Background colour to contrast against (typically the node header).
    light : str
        Hex colour to use when *bg* is dark.
    dark : str
        Hex colour to use when *bg* is light.
    """
    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    luminance = (
        0.2126 * _lin(bg.redF()) +
        0.7152 * _lin(bg.greenF()) +
        0.0722 * _lin(bg.blueF())
    )
    return light if luminance < 0.179 else dark


# ============================================================================
# NodeIconProvider
# ============================================================================

class NodeIconProvider:
    """
    Context-aware icon provider for node SVG icons.

    Produces tinted ``QIcon`` objects for two rendering contexts:

    ``for_menu(node_cls)``
        Tinted to the theme's ``body_text_color``, scaled to
        ``PM_SmallIconSize`` — consistent with all other menu icons.

    ``for_header(node_cls, header_bg)``
        Tinted to a colour that contrasts against *header_bg* (WCAG
        luminance), rendered at *header_size* logical pixels.

    SVG source is loaded via ``get_or_create_loader()`` from
    ``icon_loader``, which is the same shared registry used by
    ``MenuIconProvider``.  If a node icon lives in the same directory
    as the menu icons, no second directory scan or file read occurs.

    Tinting and rendering are fully delegated to ``SvgIconLoader``
    static methods (``_tint_svg`` / ``_render_svg``) so there is no
    duplicated colour-replacement or render logic anywhere.

    Both tint caches are flushed automatically on
    ``StyleManager.theme_changed`` — no manual wiring required.

    Parameters
    ----------
    header_size : int
        Logical pixel size for header icons (default: 14).
    menu_size : int
        Logical pixel size used when rendering menu icons before
        ``scale_pixmap_to_menu`` trims to the style metric (default: 16).
    """

    _FALLBACK_COLOR = "#C8CDD7"
    _HEADER_LIGHT   = "#E8ECF4"   # icon tint on dark headers
    _HEADER_DARK    = "#1A1C22"   # icon tint on light headers

    def __init__(
        self,
        header_size: int = 14,
        menu_size:   int = 16,
    ) -> None:
        self._header_size = header_size
        self._menu_size   = menu_size

        # Separate tint caches for each context.
        # menu cache key:   (icon_path, color_hex)
        # header cache key: (icon_path, color_hex, size_px)
        self._menu_cache:   Dict[Tuple[str, str],      QIcon] = {}
        self._header_cache: Dict[Tuple[str, str, int], QIcon] = {}

        # Paths that failed to load — sentinel avoids retrying.
        self._miss_paths: Dict[str, object] = {}
        self._MISS = object()

        StyleManager.instance().theme_changed.connect(self._on_theme_changed)

        log.debug(
            f"NodeIconProvider: ready "
            f"(header_size={header_size}px, menu_size={menu_size}px)"
        )

    # ------------------------------------------------------------------
    # Theme integration
    # ------------------------------------------------------------------

    def _on_theme_changed(self, _theme_name: str) -> None:
        """Flush both tint caches; SVG source loaders are preserved."""
        self._menu_cache.clear()
        self._header_cache.clear()
        log.debug(
            f"NodeIconProvider: tint caches flushed for theme '{_theme_name}'."
        )

    # ------------------------------------------------------------------
    # Colour resolution
    # ------------------------------------------------------------------

    def _menu_color(self) -> str:
        """
        Resolve menu-text colour from the active theme.

        Mirrors ``MenuIconProvider._resolve_color()`` exactly so node
        icons in menus are tinted identically to other menu icons.
        """
        sm = StyleManager.instance()
        for key in ("body_text_color", "title_text_color"):
            color = sm.get(StyleCategory.NODE, key)
            if isinstance(color, QColor) and color.isValid():
                return color.name()
        return self._FALLBACK_COLOR

    def _header_color(self, header_bg: QColor) -> str:
        """Derive a contrasting tint colour from *header_bg*."""
        return _contrast_color(
            header_bg,
            light=self._HEADER_LIGHT,
            dark=self._HEADER_DARK,
        )

    # ------------------------------------------------------------------
    # SVG source loading
    # ------------------------------------------------------------------

    def _load_raw(self, icon_path: str) -> Optional[str]:
        """
        Return the raw SVG XML for *icon_path*, or ``None`` on failure.

        Filesystem paths route through ``get_or_create_loader()`` so the
        SVG source is shared with every other provider pointing at the
        same directory.  Qt resource paths (``:/…``) are read via
        ``QFile`` since ``SvgIconLoader`` cannot scan resource trees.
        Failed paths are recorded in ``_miss_paths`` to avoid retrying.
        """
        if icon_path in self._miss_paths:
            return None

        # Qt resource path
        if icon_path.startswith(":/"):
            try:
                from PySide6.QtCore import QFile
                f = QFile(icon_path)
                if f.open(QFile.OpenModeFlag.ReadOnly):
                    data = bytes(f.readAll()).decode("utf-8")
                    f.close()
                    return data
            except Exception as exc:
                log.warning(
                    f"NodeIconProvider: could not read resource "
                    f"'{icon_path}': {exc}"
                )
            self._miss_paths[icon_path] = self._MISS
            return None

        # Filesystem path — shared loader registry
        directory = str(Path(icon_path).parent.resolve())
        try:
            loader = get_or_create_loader(directory)
        except FileNotFoundError as exc:
            log.warning(f"NodeIconProvider: {exc}")
            self._miss_paths[icon_path] = self._MISS
            return None

        stem = Path(icon_path).stem.lower()
        if not loader.has(stem):
            log.debug(
                f"NodeIconProvider: stem '{stem}' not found in "
                f"loader for '{directory}'."
            )
            self._miss_paths[icon_path] = self._MISS
            return None

        return loader._svg_cache.get(stem)

    # ------------------------------------------------------------------
    # Icon path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_full_path(
        node_cls: "NodeCls",
        stem_attr: str = "node_icon",
        dir_attr:  str = "node_icon_path",
    ) -> Optional[str]:
        """
        Resolve the absolute SVG file path for a given icon attribute pair.

        ``node_icon``, ``node_class_icon``, and ``node_subclass_icon`` are
        all *stem names* (e.g. ``"blur"``), **not** full paths.  This
        method combines the stem with the directory stored in
        ``node_icon_path`` (also a classvar) to produce an absolute path
        that ``_load_raw()`` and the shared loader registry can consume.

        Resolution order
        ----------------
        1. Qt resource path — if the stem itself starts with ``:/``, it is
           returned verbatim (no directory join needed).
        2. Absolute ``node_icon_path`` — joined with ``<stem>.svg`` and
           validated with ``Path.exists()``.
        3. Relative ``node_icon_path`` — resolved relative to the Python
           source file of the module that defines *node_cls*, then
           validated.
        4. Falls back to ``None`` if no valid path can be found.

        Parameters
        ----------
        node_cls : NodeCls
            The node class whose icon attribute should be resolved.
        stem_attr : str
            Name of the classvar holding the icon stem
            (``"node_icon"``, ``"node_class_icon"``, or
            ``"node_subclass_icon"``).
        dir_attr : str
            Name of the classvar holding the icon directory
            (always ``"node_icon_path"`` in practice).

        Returns
        -------
        str | None
            Resolved absolute filesystem path (or ``:/…`` resource path),
            or ``None`` if the icon cannot be located.
        """
        stem = getattr(node_cls, stem_attr, None)
        if not stem:
            return None
        stem = str(stem)

        # Qt resource path — returned as-is; _load_raw handles QFile access.
        if stem.startswith(":/"):
            return stem

        icon_dir = getattr(node_cls, dir_attr, None)
        if not icon_dir:
            return None
        icon_dir = str(icon_dir)

        filename = f"{stem}.svg"
        p = Path(icon_dir)

        # --- absolute path ---
        if p.is_absolute():
            full = p / filename
            if full.exists():
                return str(full.resolve())
            log.debug(
                f"NodeIconProvider: '{full}' not found "
                f"(stem_attr='{stem_attr}', node={node_cls.__name__})."
            )
            return None

        # --- relative path: resolve from the node class's module file ---
        try:
            module = inspect.getmodule(node_cls)
            if module and getattr(module, "__file__", None):
                base = Path(module.__file__).parent
                full = (base / p / filename).resolve()
                if full.exists():
                    return str(full)
        except Exception as exc:
            log.debug(
                f"NodeIconProvider: module-relative resolution failed "
                f"for '{stem_attr}' on {node_cls.__name__}: {exc}"
            )

        # --- last resort: relative to current working directory ---
        full = (Path.cwd() / p / filename).resolve()
        if full.exists():
            return str(full)

        log.debug(
            f"NodeIconProvider: could not locate '{filename}' via "
            f"icon_dir='{icon_dir}' for {node_cls.__name__} "
            f"(stem_attr='{stem_attr}')."
        )
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def for_menu(self, node_cls: "NodeCls") -> Optional[QIcon]:
        """
        Return a menu-context ``QIcon`` for *node_cls* (``node_icon``).

        Tinted to ``body_text_color``, scaled to ``PM_SmallIconSize``
        via ``scale_pixmap_to_menu()``.  Consistent with all other menu
        icons from ``MenuIconProvider``.

        Returns ``None`` if the node has no ``node_icon`` or the path
        cannot be resolved.
        """
        icon_path = self._resolve_full_path(node_cls, "node_icon", "node_icon_path")
        if icon_path is None:
            return None

        color     = self._menu_color()
        cache_key = (icon_path, color)

        if cache_key in self._menu_cache:
            return self._menu_cache[cache_key]

        svg = self._load_raw(icon_path)
        if svg is None:
            return None

        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), self._menu_size)
        pixmap = scale_pixmap_to_menu(pixmap)

        icon = QIcon()
        icon.addPixmap(pixmap)

        self._menu_cache[cache_key] = icon
        return icon

    def for_menu_class(self, node_cls: "NodeCls") -> Optional[QIcon]:
        """
        Return a menu-context ``QIcon`` for *node_cls*'s **category**
        (``node_class_icon``).

        Intended for use as the icon on category-level ``QMenu`` entries
        in the Browse Nodes hierarchy.  Tinted and scaled identically to
        ``for_menu()`` so all menu icons are visually consistent.

        Returns ``None`` if the node has no ``node_class_icon`` or the
        path cannot be resolved.
        """
        icon_path = self._resolve_full_path(node_cls, "node_class_icon", "node_icon_path")
        if icon_path is None:
            return None

        color     = self._menu_color()
        cache_key = (icon_path, color)

        if cache_key in self._menu_cache:
            return self._menu_cache[cache_key]

        svg = self._load_raw(icon_path)
        if svg is None:
            return None

        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), self._menu_size)
        pixmap = scale_pixmap_to_menu(pixmap)

        icon = QIcon()
        icon.addPixmap(pixmap)

        self._menu_cache[cache_key] = icon
        return icon

    def for_menu_subclass(self, node_cls: "NodeCls") -> Optional[QIcon]:
        """
        Return a menu-context ``QIcon`` for *node_cls*'s **subcategory**
        (``node_subclass_icon``).

        Intended for use as the icon on subcategory-level ``QMenu``
        entries in the Browse Nodes hierarchy.  Tinted and scaled
        identically to ``for_menu()`` so all menu icons are consistent.

        Returns ``None`` if the node has no ``node_subclass_icon`` or the
        path cannot be resolved.
        """
        icon_path = self._resolve_full_path(node_cls, "node_subclass_icon", "node_icon_path")
        if icon_path is None:
            return None

        color     = self._menu_color()
        cache_key = (icon_path, color)

        if cache_key in self._menu_cache:
            return self._menu_cache[cache_key]

        svg = self._load_raw(icon_path)
        if svg is None:
            return None

        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), self._menu_size)
        pixmap = scale_pixmap_to_menu(pixmap)

        icon = QIcon()
        icon.addPixmap(pixmap)

        self._menu_cache[cache_key] = icon
        return icon

    def for_header(
        self,
        node_cls:  "NodeCls",
        header_bg: QColor,
        size:      Optional[int] = None,
    ) -> Optional[QIcon]:
        """
        Return a header-context ``QIcon`` for *node_cls* (``node_icon``).

        Tinted to a colour contrasting against *header_bg* (WCAG
        luminance: dark header → light tint, light header → dark tint),
        rendered at *size* (or ``header_size`` from the constructor).

        Returns ``None`` if the node has no ``node_icon`` or the path
        cannot be resolved.
        """
        icon_path = self._resolve_full_path(node_cls, "node_icon", "node_icon_path")
        if icon_path is None:
            return None

        color     = self._header_color(header_bg)
        px_size   = size if size is not None else self._header_size
        cache_key = (icon_path, color, px_size)

        if cache_key in self._header_cache:
            return self._header_cache[cache_key]

        svg = self._load_raw(icon_path)
        if svg is None:
            return None

        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), px_size)

        icon = QIcon()
        icon.addPixmap(pixmap)

        self._header_cache[cache_key] = icon
        return icon


# ============================================================================
# Module-level singleton
# ============================================================================

_node_icon_provider: Optional[NodeIconProvider] = None


def get_node_icon_provider(
    header_size: int = 14,
    menu_size:   int = 16,
) -> NodeIconProvider:
    """
    Return the process-wide ``NodeIconProvider`` singleton.

    Parameters are only applied on the first call; subsequent calls
    return the existing instance unchanged.

    Parameters
    ----------
    header_size : int
        Logical pixel size for header-context icons.
    menu_size : int
        Logical pixel size used when rendering menu-context icons
        before ``scale_pixmap_to_menu`` trims to the style metric.
    """
    global _node_icon_provider

    if _node_icon_provider is None:
        _node_icon_provider = NodeIconProvider(
            header_size=header_size,
            menu_size=menu_size,
        )

    return _node_icon_provider