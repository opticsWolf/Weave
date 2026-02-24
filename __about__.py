# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Project metadata for the opticsWolf Weave.
"""

from typing import Final

# Metadata Definitions
__title__: Final[str] = "Weave"
__description__: Final[str] = (
    "A modular PySide6 framework for the visual synthesis and "
    "execution of high-concurrency simulation workflows."
)
__version__: Final[str] = "0.1.0"
__author__: Final[str] = "opticsWolf"
__license__: Final[str] = "Apache-2.0"
__copyright__: Final[str] = "Copyright (c) 2026 opticsWolf"

def metadata_summary() -> dict[str, str]:
    """Returns a dictionary of project metadata for introspection."""
    return {
        "title": __title__,
        "version": __version__,
        "license": __license__,
        "description": __description__,
    }