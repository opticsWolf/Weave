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
  sized to ``PM_SmallIconSize`` via ``menu_icon_size()``.  Uses
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

Sizing architecture
-------------------
Menu methods (``for_menu``, ``for_menu_class``, ``for_menu_subclass``)
dynamically query ``menu_icon_size()`` and pass the exact target to
``SvgIconLoader._render_svg()``, so the SVG is rasterized at the
correct OS menu metric in a single pass — no bitmap resampling
(``scale_pixmap_to_menu``) is needed after rendering.

Architecture
------------
::

    BaseControlNode.node_icon / node_class_icon / node_subclass_icon  (stem)
    BaseControlNode.node_icon_path                                     (directory)
          │
          ▼
    NodeIconProvider._resolve_full_path(node_cls, stem_attr, dir_attr)
          │
          ├── for_menu(node_cls)                    → QIcon  (text colour, menu_icon_size())
          ├── for_menu_class(node_cls)              → QIcon  (text colour, menu_icon_size())
          ├── for_menu_subclass(node_cls)           → QIcon  (text colour, menu_icon_size())
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
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, TYPE_CHECKING

from PySide6.QtGui import QColor, QIcon, QPixmap

from weave.themes.icon_loader import (
    SvgIconLoader,
    get_or_create_loader,
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
        Tinted to the theme's ``body_text_color``, rendered at
        ``menu_icon_size()`` — consistent with all other menu icons.

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

    Sizing
    ------
    Menu methods dynamically query ``menu_icon_size()`` and pass the
    exact pixel count to ``SvgIconLoader._render_svg()``, so the SVG
    is rasterized at the correct resolution in a single pass — no
    post-render ``scale_pixmap_to_menu()`` bitmap resampling is needed.

    Parameters
    ----------
    header_size : int
        Logical pixel size for header icons (default: 14).
    menu_size : int
        Logical pixel size for menu icons (default: 16).  Retained for
        backwards compatibility but overridden at call time by
        ``menu_icon_size()`` in the ``for_menu*`` methods.
    """

    _FALLBACK_COLOR = "#C8CDD7"
    _HEADER_LIGHT   = "#E8ECF4"   # icon tint on dark headers
    _HEADER_DARK    = "#1A1C22"   # icon tint on light headers

    # Regex for SVG stroke-width injection — same patterns as MinimapIconProvider.
    # Attribute form:  stroke-width="1.5"
    _SW_ATTR  = re.compile(r'stroke-width\s*=\s*"[^"]*"')
    # CSS property form inside style="…" or <style> blocks: stroke-width: 1.5 / stroke-width:2px
    _SW_STYLE = re.compile(r'(stroke-width\s*:\s*)[0-9]*\.?[0-9]+(?:px)?')

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
        self._menu_cache:         Dict[Tuple[str, str],      QIcon]   = {}
        self._header_cache:       Dict[Tuple[str, str, int], QIcon]   = {}
        # Pixmap cache for direct header rendering (tinted + stroke-width injected).
        # key: (icon_path, color_hex, size_px, stroke_width_key)
        self._header_pixmap_cache: Dict[Tuple[str, str, int, str], QPixmap] = {}

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
        self._header_pixmap_cache.clear()
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

        Tinted to ``body_text_color``, rendered at ``menu_icon_size()``
        so the SVG is rasterized at the exact OS menu metric in a single
        pass — no bitmap resampling needed.  Consistent with all other
        menu icons from ``MenuIconProvider``.

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

        # Render at menu_icon_size() directly — no post-render scaling.
        target_size = menu_icon_size()
        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), target_size)

        icon = QIcon()
        icon.addPixmap(pixmap)

        self._menu_cache[cache_key] = icon
        return icon

    def for_menu_class(self, node_cls: "NodeCls") -> Optional[QIcon]:
        """
        Return a menu-context ``QIcon`` for *node_cls*'s **category**
        (``node_class_icon``).

        Intended for use as the icon on category-level ``QMenu`` entries
        in the Browse Nodes hierarchy.  Tinted and sized identically to
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

        # Render at menu_icon_size() directly — no post-render scaling.
        target_size = menu_icon_size()
        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), target_size)

        icon = QIcon()
        icon.addPixmap(pixmap)

        self._menu_cache[cache_key] = icon
        return icon

    def for_menu_subclass(self, node_cls: "NodeCls") -> Optional[QIcon]:
        """
        Return a menu-context ``QIcon`` for *node_cls*'s **subcategory**
        (``node_subclass_icon``).

        Intended for use as the icon on subcategory-level ``QMenu``
        entries in the Browse Nodes hierarchy.  Tinted and sized
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

        # Render at menu_icon_size() directly — no post-render scaling.
        target_size = menu_icon_size()
        tinted = SvgIconLoader._tint_svg(svg, color)
        pixmap = SvgIconLoader._render_svg(tinted.encode("utf-8"), target_size)

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

    # ------------------------------------------------------------------
    # Stroke-width injection
    # ------------------------------------------------------------------

    @classmethod
    def _inject_stroke_width(cls, svg: str, width: float) -> str:
        """
        Replace every ``stroke-width`` declaration in *svg* with *width*.

        Handles both the XML-attribute form (``stroke-width="…"``) and the
        CSS-property form (``stroke-width: …`` inside ``style`` attributes
        or ``<style>`` blocks), preserving units other than bare numbers
        and ``px`` unchanged.

        Parameters
        ----------
        svg : str
            Raw SVG XML source.
        width : float
            Desired stroke-width value in SVG user units.
        """
        w = f"{width:.4g}"
        svg = cls._SW_ATTR.sub(f'stroke-width="{w}"', svg)
        svg = cls._SW_STYLE.sub(lambda m: f"{m.group(1)}{w}", svg)
        return svg

    # ------------------------------------------------------------------
    # Header pixmap (tinted + stroke-width injected, for direct drawPixmap)
    # ------------------------------------------------------------------

    def for_header_pixmap(
        self,
        node_cls:     "NodeCls",
        color_hex:    str,
        size:         int,
        stroke_width: float,
    ) -> Optional[QPixmap]:
        """
        Return a tinted, stroke-weighted ``QPixmap`` for in-canvas node
        header rendering.

        Unlike ``for_header()`` which derives the tint automatically from
        the background luminance, this method accepts an explicit *color_hex*
        so the header can pass the exact title-text colour (already
        selection-state and highlight-aware) and guarantee the icon matches
        the text visually.

        The *stroke_width* parameter is injected into the SVG source before
        rendering, mirroring how ``header_icon_default_width`` scales with
        ``QFont.weight()`` in the caller.

        Results are cached by ``(icon_path, color_hex, size_px,
        stroke_width_key)`` in ``_header_pixmap_cache``, separate from
        ``_header_cache`` (which stores ``QIcon`` objects).  Both caches are
        flushed automatically on ``StyleManager.theme_changed``.

        Parameters
        ----------
        node_cls : NodeCls
            The node class whose ``node_icon`` / ``node_icon_path`` classvars
            are used to locate the SVG file.
        color_hex : str
            Hex tint colour, e.g. ``"#E0ECff"``.  Typically
            ``self._title.defaultTextColor().name()`` from ``NodeHeader``.
        size : int
            Square pixel size for the rendered pixmap.
        stroke_width : float
            SVG ``stroke-width`` value to inject.  Pass
            ``header_icon_default_width * (font_weight / 700.0)`` so the
            icon weight tracks the title font weight.

        Returns
        -------
        QPixmap | None
            Rendered pixmap, or ``None`` if the icon cannot be resolved.
        """
        icon_path = self._resolve_full_path(node_cls, "node_icon", "node_icon_path")
        if icon_path is None:
            return None

        sw_key    = f"{stroke_width:.3g}"
        cache_key = (icon_path, color_hex, size, sw_key)

        if cache_key in self._header_pixmap_cache:
            return self._header_pixmap_cache[cache_key]

        svg = self._load_raw(icon_path)
        if svg is None:
            return None

        svg    = SvgIconLoader._tint_svg(svg, color_hex)
        svg    = self._inject_stroke_width(svg, stroke_width)
        pixmap = SvgIconLoader._render_svg(svg.encode("utf-8"), size)

        self._header_pixmap_cache[cache_key] = pixmap
        return pixmap


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
        Logical pixel size for menu-context icons.  Retained for
        backwards compatibility; the ``for_menu*`` methods override
        this with ``menu_icon_size()`` at call time.
    """
    global _node_icon_provider

    if _node_icon_provider is None:
        _node_icon_provider = NodeIconProvider(
            header_size=header_size,
            menu_size=menu_size,
        )

    return _node_icon_provider
