# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

panel_icon_provider — Theme-aware SVG icon provider for dock panel widgets
===========================================================================

Loads pre-authored SVG files from ``../resources/dock_icons/``, tints them
to the active theme's ``body_text_color``, and caches the resulting
``QIcon`` objects for O(1) repeat lookups.

Expected icons
--------------
    lock.svg       — pin-button "pinned / locked" state
    lock-open.svg  — pin-button "unpinned / unlocked" state

Any additional SVG file dropped into the dock_icons directory is
automatically available via ``get()`` / ``get_or_none()`` — no code
changes required.

Integration with StyleManager
------------------------------
On construction the provider subscribes to ``StyleManager.theme_changed``
so the icon cache is flushed automatically when the active theme changes.
Subsequent ``get()`` calls re-tint icons with the new colour.

SVG source sharing
------------------
The underlying ``SvgIconLoader`` is obtained via the shared
``get_or_create_loader()`` registry, so if ``MenuIconProvider`` or
``NodeIconProvider`` already loaded the same directory, only one loader
and one ``_svg_cache`` are ever created for it.

Usage
-----
::

    from weave.panel.panel_icon_provider import get_panel_icon_provider

    icons = get_panel_icon_provider()

    # Theme-tinted icon for the pin button (locked state):
    if ic := icons.get_or_none("lock"):
        pin_button.setIcon(ic)

    # Explicit colour override:
    if ic := icons.get_or_none("lock-open", color="#3498db"):
        btn.setIcon(ic)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtGui import QColor, QIcon

from weave.themes.icon_loader import SvgIconLoader, get_or_create_loader
from weave.stylemanager import StyleManager, StyleCategory
from weave.logger import get_logger

log = get_logger("PanelIconProvider")


# ============================================================================
# PanelIconProvider
# ============================================================================

class PanelIconProvider:
    """
    Theme-aware SVG icon provider for dock panel widgets.

    Wraps ``SvgIconLoader`` and resolves the panel text colour from the
    active ``StyleManager`` theme automatically, so callers never
    hard-code colours::

        icons = get_panel_icon_provider()
        lock_icon = icons.get_or_none("lock")

    The tint colour is derived from ``body_text_color`` (falling back to
    ``title_text_color``) — the same source used by ``MenuIconProvider``
    and ``NodeIconProvider`` — so dock panel icons always match the
    surrounding text.

    Parameters
    ----------
    directory : str | Path | None
        Path to the SVG icon directory.  ``None`` uses the default
        ``../resources/dock_icons`` relative to this module.
    size : int
        Default logical icon size in pixels (can be overridden per-call).

    Raises
    ------
    FileNotFoundError
        Propagated from ``SvgIconLoader`` if *directory* does not exist.
    """

    #: Default directory — resolved at class definition time relative to
    #: this source file's location inside the ``weave/panel/`` package.
    _DEFAULT_DIR: Path = Path(__file__).parent.parent / "resources" / "dock_icons"

    #: Fallback hex colour when StyleManager cannot provide one.
    _FALLBACK_COLOR: str = "#C8CDD7"

    def __init__(
        self,
        directory: Optional[str | Path] = None,
        size: int = 24,
    ) -> None:
        path = Path(directory) if directory is not None else self._DEFAULT_DIR
        self._size   = size
        self._loader: SvgIconLoader = get_or_create_loader(path)

        StyleManager.instance().theme_changed.connect(self._on_theme_changed)

        log.debug(
            f"PanelIconProvider: ready, size={size}px, "
            f"directory='{self._loader._path}', "
            f"available={self._loader.available}"
        )

    # ------------------------------------------------------------------
    # Theme integration
    # ------------------------------------------------------------------

    def _resolve_color(self) -> str:
        """
        Resolve the panel text colour from the active theme.

        Uses ``body_text_color`` (same field ``build_theme_palette`` maps
        to ``QPalette.WindowText`` / ``QPalette.Text``) so panel icons
        always match the dock widget text colour.  Falls back to
        ``title_text_color`` then ``_FALLBACK_COLOR``.
        """
        sm = StyleManager.instance()
        for key in ("body_text_color", "title_text_color"):
            color = sm.get(StyleCategory.NODE, key)
            if isinstance(color, QColor) and color.isValid():
                return color.name()   # "#rrggbb"
        return self._FALLBACK_COLOR

    def _on_theme_changed(self, _theme_name: str) -> None:
        """Flush the icon cache so the next call re-tints with the new colour."""
        self._loader.clear_icon_cache()
        log.debug(
            f"PanelIconProvider: icon cache flushed for theme '{_theme_name}'."
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
            E.g. ``"lock"``, ``"lock-open"``.
        color : str | None
            Hex colour override.  ``None`` uses the active theme's
            ``body_text_color``.
        size : int | None
            Logical pixel size override.  ``None`` uses the constructor
            default.

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
        missing icon.  Safe to use everywhere icons are optional::

            if ic := icons.get_or_none("lock"):
                button.setIcon(ic)
        """
        return self._loader.get_or_none(
            name  = name,
            color = color if color is not None else self._resolve_color(),
            size  = size  if size  is not None else self._size,
        )

    def has(self, name: str) -> bool:
        """Return ``True`` if *name* was found in the icon directory."""
        return self._loader.has(name)

    @property
    def available(self) -> list[str]:
        """Sorted list of available icon stems."""
        return self._loader.available


# ============================================================================
# Module-level singleton
# ============================================================================

_provider: Optional[PanelIconProvider] = None


def get_panel_icon_provider(
    directory: Optional[str | Path] = None,
    size: int = 24,
) -> PanelIconProvider:
    """
    Return the process-wide ``PanelIconProvider`` singleton.

    Parameters are only applied on the **first** call; subsequent calls
    return the existing instance unchanged.

    Parameters
    ----------
    directory : str | Path | None
        Path to the SVG icon directory.  ``None`` uses the default
        ``../resources/dock_icons`` relative to this module.
    size : int
        Default logical icon size for the provider (first-call only).

    Returns
    -------
    PanelIconProvider

    Raises
    ------
    FileNotFoundError
        If *directory* does not exist and no provider has been created yet.
    """
    global _provider

    if _provider is None:
        try:
            _provider = PanelIconProvider(directory=directory, size=size)
        except FileNotFoundError as exc:
            log.warning(f"get_panel_icon_provider: {exc} — panel icons disabled.")
            # Return a no-op stub so callers don't need to guard against None.
            class _NoopProvider:
                def get(self, *a, **kw) -> QIcon:          return QIcon()
                def get_or_none(self, *a, **kw):           return None
                def has(self, *a) -> bool:                 return False
                available: list = []
                def _on_theme_changed(self, *a) -> None:   pass
            _provider = _NoopProvider()  # type: ignore[assignment]

    return _provider
