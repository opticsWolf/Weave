# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Weave - Public enumerations and leaf-level utilities for the node subsystem.

This module is intentionally dependency-free (stdlib + QtGui value types only)
so it can be imported from any layer without circular-import risk.
"""

from enum import Enum, IntEnum, auto
from PySide6.QtGui import QColor

# ── Enums ────────────────────────────────────────────────────────────

class NodeState(Enum):
    """Logical execution state of a node in the workflow."""
    NORMAL      = 0
    PASSTHROUGH = 1
    DISABLED    = 2
    COMPUTING   = 3


class VerticalSizePolicy(IntEnum):
    """Controls how a node's height responds to content changes.

    GROW_ONLY:  Height only increases; user-set height preserved.
    FIT:        Height always matches minimum required content.
    """
    GROW_ONLY = 0
    FIT       = 1


class DisabledBehavior(Enum):
    """Defines what downstream nodes receive when this node is disabled."""
    USE_LAST_VALID      = auto()
    USE_NONE            = auto()
    USE_DEFAULT         = auto()
    PROPAGATE_DISABLED  = auto()


# ── Utilities ────────────────────────────────────────────────────────

def highlight_colors(color: QColor, b_offset: int, s_offset: int = 0) -> QColor:
    """Adjusts brightness and saturation of a QColor.

    All values cast to int for PySide6 C++ signature safety.

    Args:
        color:    Base colour.
        b_offset: Lightness delta (positive = brighter).
        s_offset: Saturation delta (positive = more saturated).
    """
    h, s, l, a = color.getHsl()
    l = int(max(0, min(255, l + b_offset)))
    s = int(max(0, min(255, s + s_offset)))
    return QColor.fromHsl(int(h), s, l, int(a))