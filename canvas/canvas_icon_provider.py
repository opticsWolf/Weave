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
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QStyle, QStyleOption

from weave.themes.icon_loader import SvgIconLoader, get_or_create_loader, scale_pixmap_to_menu
from weave.stylemanager import StyleManager, StyleCategory
from weave.logger import get_logger

log = get_logger("MenuIconProvider")


# ==============================================================================
# Button enum (for minimap icons) - moved from canvas_minimap_icons.py 
# ==============================================================================

class MinimapButton(Enum):
    NONE  = -1
    RESET =  0
    FIT   =  1
    PIN   =  2
    SNAP  =  3


# ==============================================================================
# MenuIconProvider (original functionality)
# ==============================================================================

class MenuIconProvider:
    """
    Theme-aware icon provider for Weave menus.

    Wraps ``SvgIconLoader`` and resolves the menu-text colour from the
    active ``StyleManager`` theme automatically, so callers never
    hard-code colours::

        icons = MenuIconProvider()
        action.setIcon(icons.get("save"))

    The colour is derived from ``StyleCategory.NODE / body_text_color``
    (the same source that ``palette_bridge.resolve_theme_colors()`` uses)
    which maps directly to ``QPalette.WindowText`` / ``QPalette.Text``
    after ``AppThemeBridge`` applies it — guaranteeing icon tint and
    menu text are always the same hue.

    The underlying ``SvgIconLoader`` is obtained from the shared
    ``_loader_registry`` via ``get_or_create_loader()``, so if
    ``NodeIconProvider`` or any other consumer points at the same
    directory, only one loader (and one ``_svg_cache``) exists for it.

    On construction the provider subscribes to ``StyleManager.theme_changed``
    so the icon cache is flushed automatically whenever the theme switches.
    No manual wiring required by the caller.

    Parameters
    ----------
    directory : str | Path
        Path to the SVG icon directory.
    size : int
        Default logical icon size in pixels (can be overridden per-call).

    Raises
    ------
    FileNotFoundError
        Propagated from ``SvgIconLoader`` if the directory is missing.
    """

    #: Fallback colour used when StyleManager cannot provide one
    #: (e.g. during early init before any theme has been applied).
    _FALLBACK_COLOR = "#C8CDD7"

    def __init__(
        self,
        directory: str | Path,
        size:      int = 16,
    ) -> None:
        self._size   = size
        # Shared loader — never construct SvgIconLoader directly here.
        self._loader = get_or_create_loader(directory)

        # Subscribe to theme changes so the cache is flushed automatically.
        sm = StyleManager.instance()
        sm.theme_changed.connect(self._on_theme_changed)

        log.debug(
            f"MenuIconProvider: ready, default size={size}px, "
            f"directory='{self._loader._path}'"
        )

    # ------------------------------------------------------------------
    # Theme integration
    # ------------------------------------------------------------------

    def _resolve_color(self) -> str:
        """
        Resolve the current menu-text colour from the active theme.

        Uses ``StyleCategory.NODE / body_text_color`` — the same field
        that ``resolve_theme_colors()`` uses for ``QPalette.Text`` and
        ``QPalette.WindowText``.  This guarantees the icon tint always
        matches the menu label text.

        Falls back to ``_FALLBACK_COLOR`` if the value is absent or
        not yet a ``QColor`` (e.g. during early startup).
        """
        sm    = StyleManager.instance()
        color = sm.get(StyleCategory.NODE, "body_text_color")

        if isinstance(color, QColor) and color.isValid():
            return color.name()           # "#rrggbb" hex string

        # Try title_text_color as secondary fallback (same path as
        # resolve_theme_colors() in palette_bridge).
        color = sm.get(StyleCategory.NODE, "title_text_color")
        if isinstance(color, QColor) and color.isValid():
            return color.name()

        return self._FALLBACK_COLOR

    def _on_theme_changed(self, _theme_name: str) -> None:
        """
        Slot connected to ``StyleManager.theme_changed``.

        Flushes the tinted icon cache so the next ``get()`` call
        re-tints icons with the new theme's text colour.
        """
        self._loader.clear_icon_cache()
        log.debug(
            f"MenuIconProvider: cache flushed for theme '{_theme_name}'."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        name:  str,
        color: Optional[str] = None,
        size:  Optional[int] = None,
    ) -> QIcon:
        """
        Return a tinted ``QIcon`` for *name*.

        Parameters
        ----------
        name : str
            Icon stem without extension (case-insensitive).
        color : str | None
            Hex colour override.  ``None`` (default) uses the colour
            resolved from the active theme (menu-text colour).
        size : int | None
            Logical pixel size override.  ``None`` uses the default
            size passed to the constructor.

        Returns
        -------
        QIcon

        Raises
        ------
        KeyError
            If no SVG file with the given *name* was found.
        """
        return self._loader.get(
            name  = name,
            color = color if color is not None else self._resolve_color(),
            size  = size  if size  is not None else self._size,
        )

    def get_or_none(
        self,
        name:  str,
        color: Optional[str] = None,
        size:  Optional[int] = None,
    ) -> Optional[QIcon]:
        """
        Like ``get()``, but returns ``None`` instead of raising on a
        missing icon.  Safe to use everywhere icons are optional.
        """
        return self._loader.get_or_none(
            name  = name,
            color = color if color is not None else self._resolve_color(),
            size  = size  if size  is not None else self._size,
        )

    def has(self, name: str) -> bool:
        """Return True if *name* was found in the icon directory."""
        return self._loader.has(name)

    @property
    def available(self) -> list[str]:
        """Sorted list of available icon names."""
        return self._loader.available


# ==============================================================================
# MinimapIconProvider (merged from canvas_minimap_icons.py)
# ==============================================================================

class MinimapIconProvider:
    """
    Theme-aware SVG icon provider for CanvasMinimap buttons.

    Parameters
    ----------
    directory : str | Path | None
        Path to the SVG icon directory.  ``None`` uses the default
        ``../resources/minimap_icons`` relative to this module.

    Raises
    ------
    FileNotFoundError
        Propagated from ``SvgIconLoader`` if *directory* does not exist.
    """

    # Default icon directory, resolved relative to this source file.
    _DEFAULT_DIR: Path = Path(__file__).parent.parent / "resources" / "minimap_icons"

    # Fallback hex colour when StyleManager cannot provide one.
    _FALLBACK_COLOR = "#C8CDD7"

    # (button, state) → SVG file stem
    _ICON_STEMS: Dict[Tuple[MinimapButton, bool], str] = {
        (MinimapButton.RESET, False): "reset-zoom",
        (MinimapButton.RESET, True):  "reset-zoom",
        (MinimapButton.FIT,   False): "fit-to-content",
        (MinimapButton.FIT,   True):  "fit-to-content",
        (MinimapButton.PIN,   True):  "pin",      # pinned   = active
        (MinimapButton.PIN,   False): "unpin",    # unpinned = inactive
        (MinimapButton.SNAP,  True):  "snapping",
        (MinimapButton.SNAP,  False): "snapping",
    }

    # Regex patterns for SVG stroke-width injection.
    # Matches the attribute form: stroke-width="…"
    _SW_ATTR = re.compile(r'stroke-width\s*=\s*"[^"]*"')
    # Matches the CSS property form inside style="…" or <style> blocks:
    # stroke-width: 1.5  /  stroke-width:1.5;  /  stroke-width: 2px
    _SW_STYLE = re.compile(r'(stroke-width\s*:\s*)[0-9]*\.?[0-9]+(?:px)?')

    def __init__(self, directory: str | Path | None = None) -> None:
        path = Path(directory) if directory is not None else self._DEFAULT_DIR
        self._loader: SvgIconLoader = get_or_create_loader(path)

        # QPixmap cache: (stem, color_hex, size_px, stroke_width_key) → QPixmap
        # stroke_width_key is a rounded string so floating-point noise doesn't
        # proliferate cache entries (e.g. "1.5" covers 1.4999… and 1.5001…).
        self._pixmap_cache: Dict[Tuple[str, str, int, str], QPixmap] = {}

        # Paths that failed to load — sentinel avoids retrying.
        self._miss_stems: Dict[str, object] = {}
        _MISS = object()
        self._MISS = _MISS

        StyleManager.instance().theme_changed.connect(self._on_theme_changed)

        log.debug(
            f"MinimapIconProvider: ready, "
            f"directory='{self._loader._path}', "
            f"available={self._loader.available}"
        )

    # ------------------------------------------------------------------
    # Theme integration
    # ------------------------------------------------------------------

    def _on_theme_changed(self, _theme_name: str) -> None:
        """Flush pixmap cache on theme switch."""
        self._pixmap_cache.clear()
        log.debug(
            f"MinimapIconProvider: pixmap cache flushed for theme '{_theme_name}'."
        )

    # ------------------------------------------------------------------
    # Colour resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _qcolor_to_hex(color: Any, fallback: str) -> str:
        """Safely convert a QColor (or raw hex string) to a hex string."""
        if isinstance(color, QColor) and color.isValid():
            return color.name()          # "#rrggbb"
        if isinstance(color, str) and color.startswith("#"):
            return color
        return fallback

    def _resolve_color(
        self,
        btn_type: MinimapButton,
        state: bool,
        config: Dict[str, Any],
    ) -> str:
        """
        Return the hex tint colour for *btn_type* at *state*.

        Active toggle buttons (PIN pinned, SNAP enabled) use
        ``active_icon_color``; inactive toggles use ``border_color``
        (a faint tint, matching the original painter behaviour).
        Stateless buttons (RESET, FIT) use ``text_color``.
        """
        if btn_type in (MinimapButton.PIN, MinimapButton.SNAP):
            raw = (
                config.get("active_icon_color")
                if state
                else config.get("border_color")
            )
        else:
            raw = config.get("text_color") or config.get("icon_color")

        return self._qcolor_to_hex(raw, self._FALLBACK_COLOR)

    # ------------------------------------------------------------------
    # SVG manipulation helpers
    # ------------------------------------------------------------------

    @classmethod
    def _inject_stroke_width(cls, svg: str, width: float) -> str:
        """
        Replace every ``stroke-width`` declaration in *svg* with *width*.

        Handles both the XML-attribute form (``stroke-width="…"``) and the
        CSS-property form (``stroke-width: …`` inside ``style`` attributes
        or ``<style>`` blocks).

        This allows ``icon_symbol_width`` from the MINIMAP config to drive
        the visual stroke weight, mirroring how it previously controlled
        ``QPen.setWidthF()`` in the hand-drawn icon painters.

        Parameters
        ----------
        svg : str
            Raw SVG XML source.
        width : float
            Desired stroke-width value in SVG user units.  Icons should be
            authored in a 24 × 24 viewport so that the typical config range
            of 1.5–2.5 produces a visually consistent weight.
        """
        w = f"{width:.4g}"
        svg = cls._SW_ATTR.sub(f'stroke-width="{w}"', svg)
        svg = cls._SW_STYLE.sub(lambda m: f"{m.group(1)}{w}", svg)
        return svg

    # ------------------------------------------------------------------
    # Core rendering
    # ------------------------------------------------------------------

    def for_button(
        self,
        btn_type: MinimapButton,
        config: Dict[str, Any],
        state: bool = False,
    ) -> Optional[QPixmap]:
        """
        Return a tinted, stroke-weighted ``QPixmap`` for *btn_type*.

        The pixmap is square with side length ``config['icon_size']``
        (logical pixels, DPR = 1).  Results are cached by
        ``(stem, color_hex, size_px, stroke_width_key)`` so repeated
        calls are O(1).

        Parameters
        ----------
        btn_type : MinimapButton
            Which button to render.
        config : dict
            The live MINIMAP config dict from ``CanvasMinimap._config``.
            Must contain ``icon_size`` and ``icon_symbol_width``.
        state : bool
            Active / toggled state of the button (used for colour and
            stem selection for PIN / SNAP).

        Returns
        -------
        QPixmap | None
            Rendered pixmap, or ``None`` if the SVG file is missing.
        """
        stem = self._ICON_STEMS.get((btn_type, state))
        if stem is None or stem in self._miss_stems:
            return None

        color        = self._resolve_color(btn_type, state, config)
        size         = int(config.get("icon_size", 16))
        stroke_width = float(config.get("icon_symbol_width", 1.5))
        sw_key       = f"{stroke_width:.3g}"   # "1.5", "2", etc.

        cache_key = (stem, color, size, sw_key)
        if cache_key in self._pixmap_cache:
            return self._pixmap_cache[cache_key]

        # Load raw SVG from shared loader
        raw_svg = self._loader._svg_cache.get(stem.lower())
        if raw_svg is None:
            log.debug(
                f"MinimapIconProvider: stem '{stem}' not found in "
                f"'{self._loader._path}'."
            )
            self._miss_stems[stem] = self._MISS
            return None

        # Apply tint then stroke-width injection
        svg = SvgIconLoader._tint_svg(raw_svg, color)
        svg = self._inject_stroke_width(svg, stroke_width)

        # Render to pixmap
        pixmap = SvgIconLoader._render_svg(svg.encode("utf-8"), size)

        self._pixmap_cache[cache_key] = pixmap
        return pixmap

    # ------------------------------------------------------------------
    # Convenience paint method (drop-in replacement for the old painters)
    # ------------------------------------------------------------------

    def draw_button(
        self,
        painter:  QPainter,
        rect:     QRectF,
        config:   Dict[str, Any],
        btn_type: MinimapButton,
        state:    bool = False,
    ) -> None:
        """
        Draw the icon for *btn_type* centred inside *rect*.

        This is a drop-in replacement for the old
        ``IconPainter.paint(painter, rect, config, state)`` call.
        The painter's current transform and clipping are unaffected.

        Parameters
        ----------
        painter : QPainter
            Active painter (already translated / prepared by the caller).
        rect : QRectF
            Button bounding rectangle in viewport / screen coordinates.
        config : dict
            Live MINIMAP config dict (same dict passed to ``for_button``).
        btn_type : MinimapButton
            Which button to draw.
        state : bool
            Active / toggled state (PIN pinned, SNAP enabled, etc.).
        """
        pixmap = self.for_button(btn_type, config, state)
        if pixmap is None:
            return

        # Centre the pixmap inside the button rect
        x = rect.x() + (rect.width()  - pixmap.width())  / 2.0
        y = rect.y() + (rect.height() - pixmap.height()) / 2.0
        painter.drawPixmap(QPointF(x, y), pixmap)

    # ------------------------------------------------------------------
    # Minimized-state icon
    # ------------------------------------------------------------------

    #: SVG stem used when the minimap is in its minimized / icon-only state.
    _MINIMAP_STEM = "minimap"

    def draw_minimized(
        self,
        painter: QPainter,
        vp_rect,            # QRect or QRectF — viewport rect of the minimap widget
        config: Dict[str, Any],
    ) -> None:
        """
        Draw the ``minimap.svg`` icon centred in *vp_rect*.

        Replaces the hand-drawn map-symbol previously in
        ``CanvasMinimap._draw_minimized_icon()``.  The icon is sized to
        65 % of the smaller viewport dimension so it fills the minimized
        widget comfortably at any ``minimized_size`` config value.

        Tint colour is ``text_color`` (same as the zoom label and the
        hand-drawn symbol it replaces).  ``icon_symbol_width`` is
        injected as ``stroke-width`` in the SVG so the stroke weight
        follows the same config knob as the button icons.

        If ``minimap.svg`` is not found in the icon directory, the
        method is a silent no-op — the minimap simply shows nothing when
        minimized rather than crashing.

        Parameters
        ----------
        painter : QPainter
            Active painter (transform already reset to screen space by
            the caller, i.e. ``drawForeground`` after
            ``painter.resetTransform()``).
        vp_rect : QRect | QRectF
            The minimap viewport rectangle (``self.viewport().rect()``).
        config : dict
            Live MINIMAP config dict from ``CanvasMinimap._config``.
        """
        stem = self._MINIMAP_STEM
        if stem in self._miss_stems:
            return

        # Colour — same source as the old hand-drawn symbol
        raw   = config.get("text_color") or config.get("icon_color")
        color = self._qcolor_to_hex(raw, self._FALLBACK_COLOR)

        # Size — 65 % of the smaller viewport dimension, minimum 8 px
        size = max(8, int(min(vp_rect.width(), vp_rect.height()) * 0.65))

        stroke_width = float(config.get("icon_symbol_width", 1.5))
        sw_key       = f"{stroke_width:.3g}"

        cache_key = (stem, color, size, sw_key)
        pixmap = self._pixmap_cache.get(cache_key)

        if pixmap is None:
            raw_svg = self._loader._svg_cache.get(stem.lower())
            if raw_svg is None:
                log.debug(
                    f"MinimapIconProvider: stem '{stem}' not found in "
                    f"'{self._loader._path}' — minimized icon skipped."
                )
                self._miss_stems[stem] = self._MISS
                return

            svg    = SvgIconLoader._tint_svg(raw_svg, color)
            svg    = self._inject_stroke_width(svg, stroke_width)
            pixmap = SvgIconLoader._render_svg(svg.encode("utf-8"), size)
            self._pixmap_cache[cache_key] = pixmap

        # Centre the pixmap inside the viewport rect
        cx = vp_rect.x() + (vp_rect.width()  - pixmap.width())  / 2.0
        cy = vp_rect.y() + (vp_rect.height() - pixmap.height()) / 2.0
        painter.drawPixmap(QPointF(cx, cy), pixmap)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def available(self) -> list[str]:
        """Sorted list of preloaded icon stems."""
        return self._loader.available

    def missing_stems(self) -> list[str]:
        """
        Return the list of expected icon stems that were not found on disk.

        Useful for startup diagnostics / logging.
        """
        expected = set(self._ICON_STEMS.values()) | {self._MINIMAP_STEM}
        return sorted(s for s in expected if not self._loader.has(s))


# ==============================================================================
# Module-level singleton helpers (keeping both)
# ==============================================================================

_menu_provider: Optional[MenuIconProvider] = None
_minimap_provider: Optional[MinimapIconProvider] = None


def get_menu_icon_provider(
    directory: Optional[str | Path] = None,
    size:      int = 16,
) -> Optional[MenuIconProvider]:
    """
    Return the process-wide ``MenuIconProvider`` singleton.

    First call **must** supply *directory*.  Subsequent calls may omit
    it (the existing instance is returned unchanged).

    Returns ``None`` — rather than raising — if the directory does not
    exist or is not yet configured, so callers can treat icons as
    optional without wrapping every call in try/except.

    Parameters
    ----------
    directory : str | Path | None
        Path to the SVG icon directory.  Required on first call.
    size : int
        Default logical icon size for the provider (first-call only).
    """
    global _menu_provider

    if _menu_provider is not None:
        return _menu_provider

    if directory is None:
        return None

    try:
        _menu_provider = MenuIconProvider(directory=directory, size=size)
    except FileNotFoundError as exc:
        log.warning(f"get_menu_icon_provider: {exc} — icons disabled.")
        _menu_provider = None

    return _menu_provider


def get_minimap_icon_provider(
    directory: Optional[str | Path] = None,
) -> MinimapIconProvider:
    """
    Return the process-wide ``MinimapIconProvider`` singleton.

    Parameters are only applied on the first call; subsequent calls
    return the existing instance unchanged.

    Parameters
    ----------
    directory : str | Path | None
        Path to the SVG icon directory.  ``None`` uses the default
        ``../resources/minimap_icons`` relative to this module.

    Returns
    -------
    MinimapIconProvider
        The singleton instance.

    Raises
    ------
    FileNotFoundError
        If *directory* does not exist and no provider has been created yet.
    """
    global _minimap_provider

    if _minimap_provider is None:
        _minimap_provider = MinimapIconProvider(directory=directory)

    return _minimap_provider
