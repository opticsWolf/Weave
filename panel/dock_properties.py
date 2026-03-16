# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

dock_properties — Declarative dock-layout hints for nodes
==========================================================

Provides ``DockProperties``, a plain dataclass that a ``BaseControlNode``
subclass can assign to its ``dock_properties`` class variable.  When a
``NodeDockAdapter`` creates a static dock for that node, it reads these
hints and applies them to the ``QDockWidget`` so the author of a custom
node can control *where* and *how* its panel may be docked without
writing any adapter code.

Usage
-----
::

    @register_node
    class MyNode(BaseControlNode):
        dock_properties = DockProperties(
            allowed_areas=Qt.DockWidgetArea.LeftDockWidgetArea
                        | Qt.DockWidgetArea.RightDockWidgetArea,
            min_width=250,
            max_width=500,
            preferred_area=Qt.DockWidgetArea.RightDockWidgetArea,
            closable=True,
            floatable=True,
            movable=True,
        )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import Qt


@dataclass
class DockProperties:
    """Declarative dock-layout hints attached to a node class.

    Every field is optional — ``None`` means "use the QDockWidget default".

    Attributes
    ----------
    allowed_areas : Qt.DockWidgetAreas | None
        Bitwise-OR combination of ``Qt.DockWidgetArea`` flags that define
        where the dock may be placed.  ``None`` → all areas.
    preferred_area : Qt.DockWidgetArea | None
        The area the dock should be placed in by default when first
        created.  ``None`` → the caller decides.
    min_width : int | None
        Minimum width in pixels for the dock panel.
    max_width : int | None
        Maximum width in pixels for the dock panel.
    min_height : int | None
        Minimum height in pixels for the dock panel.
    max_height : int | None
        Maximum height in pixels for the dock panel.
    preferred_width : int | None
        Initial / preferred width hint.  Applied via ``resize()`` before
        the dock is added to the main window.
    preferred_height : int | None
        Initial / preferred height hint.
    closable : bool | None
        Whether the dock's close button is shown.  ``None`` → True.
    movable : bool | None
        Whether the dock can be dragged to another area.  ``None`` → True.
    floatable : bool | None
        Whether the dock can be detached as a floating window.
        ``None`` → True.
    title_bar_visible : bool | None
        Whether to show the dock's title bar at all.  Setting to False
        also prevents moving/floating/closing.  ``None`` → True.
    """

    allowed_areas: Optional[Qt.DockWidgetAreas] = None
    preferred_area: Optional[Qt.DockWidgetArea] = None

    min_width: Optional[int] = None
    max_width: Optional[int] = None
    min_height: Optional[int] = None
    max_height: Optional[int] = None
    preferred_width: Optional[int] = None
    preferred_height: Optional[int] = None

    closable: Optional[bool] = None
    movable: Optional[bool] = None
    floatable: Optional[bool] = None
    title_bar_visible: Optional[bool] = None
