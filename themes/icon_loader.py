# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

icon_loader — Style-aware SVG icon loader
==========================================

Loads raw SVG files from disk once (O(n) preload) and returns
QIcon objects tinted to any hex colour, with results cached for
O(1) repeat lookups.

Integration with Weave's StyleManager
--------------------------------------
``MenuIconProvider`` wraps ``SvgIconLoader`` and resolves the
correct icon colour from the active theme automatically, so
callers never hard-code colours:

::

    icons = MenuIconProvider()

    # Always returns an icon tinted to the current theme's menu-text colour.
    action.setIcon(icons.get("save"))

    # Explicit colour override (e.g. a coloured swatch):
    action.setIcon(icons.get("open", color="#3498db"))

When the active theme changes, call ``icons.on_theme_changed()``
(or connect it to ``StyleManager.theme_changed``) to flush the
tint cache so subsequent lookups pick up the new colour.  The
SVG source cache is never flushed — files are only read once per
process.

DPI awareness
-------------
Pass ``size`` as a logical pixel value.  ``_render_svg`` creates
a high-DPI pixmap via ``QPixmap.setDevicePixelRatio`` so icons
are crisp on HiDPI / Retina displays.

Color replacement strategy
--------------------------
1. SVGs that use ``currentColor`` get a simple string replace — fast
   and reliable for well-authored icon sets.
2. Fallback: all ``fill="…"`` / ``stroke="…"`` attributes are
   rewritten, **except** ``fill="none"`` (transparent cutouts) and
   ``stroke="none"`` (no-stroke declarations) which are preserved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QStyle, QStyleOption

from weave.logger import get_logger

log = get_logger("IconLoader")


# ============================================================================
# SvgIconLoader — low-level cache
# ============================================================================

class SvgIconLoader:
    """
    Low-level, style-aware SVG icon loader.

    Responsibilities
    ----------------
    - Preload all ``*.svg`` files from a directory into a string cache.
    - Tint SVG source to any hex colour on demand.
    - Cache the resulting ``QIcon`` keyed by ``(name, color, size)``.
    - Expose ``clear_icon_cache()`` so callers can invalidate on
      theme change without re-reading files from disk.

    This class has no knowledge of StyleManager or Weave themes.
    Colour resolution is the responsibility of the caller
    (``MenuIconProvider`` handles this for the menu layer).

    Parameters
    ----------
    directory : str | Path
        Path to the directory that contains the SVG files.
        All ``*.svg`` files are loaded immediately on construction.

    Raises
    ------
    FileNotFoundError
        If *directory* does not exist.
    """

    # Matches fill="…" or stroke="…", capturing attribute name and value.
    # The look-ahead prevents matching fill="none" / stroke="none" so
    # transparent cutouts and explicit no-stroke markers are preserved.
    _COLOR_PATTERN = re.compile(
        r'(fill|stroke)="(?!none\b)([^"]*)"'
    )

    def __init__(self, directory: str | Path) -> None:
        self._path = Path(directory)
        # str cache: stem → raw SVG XML
        self._svg_cache:  Dict[str, str] = {}
        # icon cache: (stem, color_hex, size_px) → QIcon
        self._icon_cache: Dict[Tuple[str, str, int], QIcon] = {}

        if not self._path.exists():
            raise FileNotFoundError(
                f"Icon directory not found: {self._path}"
            )

        self._preload()
        log.debug(
            f"SvgIconLoader: preloaded {len(self._svg_cache)} icons "
            f"from '{self._path}'"
        )

    # ------------------------------------------------------------------
    # Preload
    # ------------------------------------------------------------------

    def _preload(self) -> None:
        """Read every *.svg in the directory into the string cache — O(n)."""
        for file in sorted(self._path.glob("*.svg")):
            try:
                self._svg_cache[file.stem.lower()] = file.read_text(
                    encoding="utf-8"
                )
            except OSError as exc:
                log.warning(f"Could not read icon '{file.name}': {exc}")

    # ------------------------------------------------------------------
    # Color replacement
    # ------------------------------------------------------------------

    @staticmethod
    def _tint_svg(svg: str, color: str) -> str:
        """
        Return a copy of *svg* with all colours replaced by *color*.

        Strategy
        --------
        1. If the SVG uses ``currentColor`` (preferred icon authoring
           style), do a targeted string replace — avoids regex entirely.
        2. Otherwise rewrite every ``fill="…"`` / ``stroke="…"``
           attribute except ``"none"`` values (transparent cutouts).

        Parameters
        ----------
        svg : str
            Raw SVG XML source.
        color : str
            Target colour as a hex string, e.g. ``"#C8CDD7"``.
        """
        if "currentColor" in svg:
            return svg.replace("currentColor", color)

        return SvgIconLoader._COLOR_PATTERN.sub(
            lambda m: f'{m.group(1)}="{color}"', svg
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render_svg(svg_data: bytes, size: int) -> QPixmap:
        """
        Render *svg_data* into a square ``QPixmap`` of exactly *size* × *size*
        physical pixels with ``devicePixelRatio`` = 1.

        HiDPI is handled at the ``QIcon`` level: ``scale_pixmap_to_menu``
        computes the correct physical target size from ``menu_icon_size() × dpr``
        and ``addPixmap`` registers the result under the right logical size.
        Baking a 2× DPR into the pixmap here caused double-sizing because the
        menu was already accounting for DPR, displaying a 16 px logical icon
        at 32 logical pixels.

        Parameters
        ----------
        svg_data : bytes
            UTF-8-encoded SVG XML.
        size : int
            Physical pixel size (width = height).
        """
        renderer = QSvgRenderer(QByteArray(svg_data))
        if not renderer.isValid():
            log.warning("SvgIconLoader: renderer could not parse SVG data.")
            fallback = QPixmap(QSize(size, size))
            fallback.fill(Qt.GlobalColor.transparent)
            return fallback

        pixmap = QPixmap(QSize(size, size))
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()

        return pixmap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        name:  str,
        color: str = "#FFFFFF",
        size:  int = 16,
    ) -> QIcon:
        """
        Return a tinted ``QIcon`` for *name*, using the cache.

        Parameters
        ----------
        name : str
            Icon stem without extension (case-insensitive).
        color : str
            Hex color string, e.g. ``"#C8CDD7"``.
        size : int
            Logical pixel size (used for both width and height).

        Returns
        -------
        QIcon
            Tinted, cached icon.

        Raises
        ------
        KeyError
            If no SVG file with the given *name* was found at preload time.
        """
        key       = name.lower()
        cache_key = (key, color, size)

        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]

        if key not in self._svg_cache:
            raise KeyError(
                f"Icon '{name}' not found in '{self._path}'. "
                f"Available: {sorted(self._svg_cache)}"
            )

        tinted  = self._tint_svg(self._svg_cache[key], color)
        pixmap  = self._render_svg(tinted.encode("utf-8"), size)
        pixmap  = scale_pixmap_to_menu(pixmap)

        # addPixmap() lets Qt inspect devicePixelRatio directly, so a
        # 32×32 pixmap with DPR=2 is correctly understood as a 16×16
        # logical icon.  The QIcon(pixmap) constructor bypasses this
        # bookkeeping on some platform styles and can cause double-scaling.
        icon = QIcon()
        icon.addPixmap(pixmap)

        self._icon_cache[cache_key] = icon
        return icon

    def get_or_none(
        self,
        name:  str,
        color: str = "#FFFFFF",
        size:  int = 16,
    ) -> Optional[QIcon]:
        """
        Like ``get()``, but returns ``None`` instead of raising on a
        missing icon.  Useful when icons are optional (e.g. node registry).
        """
        try:
            return self.get(name, color, size)
        except KeyError:
            return None

    def has(self, name: str) -> bool:
        """Return True if an SVG with the given *name* was preloaded."""
        return name.lower() in self._svg_cache

    def clear_icon_cache(self) -> None:
        """
        Discard all cached tinted icons.

        Call this whenever the active theme changes so that subsequent
        ``get()`` calls re-tint with the new colour.  The SVG source
        cache (``_svg_cache``) is unaffected — files are not re-read.
        """
        self._icon_cache.clear()
        log.debug("SvgIconLoader: icon cache cleared.")

    @property
    def available(self) -> list[str]:
        """Sorted list of preloaded icon names (stems, lower-cased)."""
        return sorted(self._svg_cache)


# ============================================================================
# Menu icon sizing helpers
# ============================================================================

def menu_icon_size() -> int:
    """
    Return the logical pixel size the active Qt style uses for menu icons.

    Queries ``QStyle.pixelMetric(PM_SmallIconSize)`` on the application
    style, which is the same metric Qt uses internally when it lays out
    ``QMenu`` rows.  Falls back to 16 px if no ``QApplication`` exists
    yet (e.g. during unit tests).

    Returns
    -------
    int
        Logical pixel size (equal width and height).
    """
    app = QApplication.instance()
    if app is None:
        return 16
    return app.style().pixelMetric(
        QStyle.PixelMetric.PM_SmallIconSize, None, None
    )

def scale_pixmap_to_menu(pixmap: QPixmap) -> QPixmap:
    """
    Scale *pixmap* to exactly ``menu_icon_size()`` physical pixels.

    ``_render_svg`` now produces pixmaps with DPR=1, so the physical
    target is simply ``menu_icon_size()`` with no DPR multiplication.
    ``QPixmap.scaled`` preserves the source DPR on the result, so no
    manual ``setDevicePixelRatio`` is needed afterwards.

    Parameters
    ----------
    pixmap : QPixmap
        Source pixmap from ``SvgIconLoader._render_svg``.

    Returns
    -------
    QPixmap
        Pixmap whose physical dimensions equal ``menu_icon_size()``.
    """
    target = menu_icon_size()

    if pixmap.width() == target:
        return pixmap

    return pixmap.scaled(
        QSize(target, target),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

# ============================================================================
# Shared loader registry
# ============================================================================

# Maps resolved directory path (str) → SvgIconLoader instance.
# All providers — MenuIconProvider, NodeIconProvider, any future consumer —
# obtain loaders through get_or_create_loader() so each directory is scanned
# and read from disk exactly once per process.
_loader_registry: Dict[str, SvgIconLoader] = {}


def get_or_create_loader(directory: str | Path) -> SvgIconLoader:
    """
    Return a ``SvgIconLoader`` for *directory*, creating it on first call.

    The registry is keyed by the **resolved absolute path** so that
    ``"icons/menu"``, ``"./icons/menu"``, and ``Path("icons/menu").resolve()``
    all map to the same loader instance.

    This is the single construction point for all ``SvgIconLoader``
    instances in the process.  Both ``MenuIconProvider`` and
    ``NodeIconProvider`` call this function, guaranteeing that icons from
    the same directory share one ``_svg_cache`` with no duplicate reads.

    Parameters
    ----------
    directory : str | Path
        Path to the SVG icon directory.

    Raises
    ------
    FileNotFoundError
        If *directory* does not exist (propagated from ``SvgIconLoader``).
    """
    key = str(Path(directory).resolve())
    if key not in _loader_registry:
        _loader_registry[key] = SvgIconLoader(key)
    return _loader_registry[key]

