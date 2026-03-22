# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

widgets._models — Data structures for widget ↔ port bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional


class PortRole(Enum):
    """How a widget relates to node ports."""
    INPUT = auto()          # Widget provides a fallback for an *input* port
    OUTPUT = auto()         # Widget drives an *output* port value
    BIDIRECTIONAL = auto()  # Both: shows incoming data, provides default
    DISPLAY = auto()        # Read-only display (no port created)
    INTERNAL = auto()       # Not exposed as a port; node reads value manually


@dataclass
class WidgetBinding:
    """One entry in the registry that ties a widget to a port name."""
    port_name: str
    widget: "QWidget"  # type: ignore[name-defined]
    role: PortRole = PortRole.OUTPUT
    datatype: str = "any"
    default: Any = None
    description: str = ""

    # Callables override the generic read/write helpers.
    # Signature: getter() -> Any,  setter(value) -> None
    getter: Optional[Callable[[], Any]] = None
    setter: Optional[Callable[[Any], None]] = None

    # The signal name on the widget that fires when the user edits it.
    # ``None`` = auto-detect (works for all standard Qt widgets).
    change_signal_name: Optional[str] = None

    # Internal bookkeeping (not for public use)
    _connected_signal: Optional[str] = field(default=None, repr=False)
    _slot_ref: Optional[Callable[..., None]] = field(default=None, repr=False)


@dataclass
class PortDefinition:
    """Returned by ``get_port_definitions()`` so the node can auto-create ports."""
    name: str
    datatype: str
    role: PortRole
    default: Any
    description: str
