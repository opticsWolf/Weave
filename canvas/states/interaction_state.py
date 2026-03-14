# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Type, TypeVar, Sequence
from PySide6.QtWidgets import QGraphicsSceneMouseEvent, QGraphicsItem, QGraphicsProxyWidget, QGraphicsTextItem
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QTransform, QKeyEvent

class CanvasInteractionState(ABC):
    """Base class for canvas interaction states."""
    
    def __init__(self, canvas):
        self.canvas = canvas

    @abstractmethod
    def on_mouse_press(self, event: QGraphicsSceneMouseEvent) -> bool: 
        """Handle mouse press. Return True to consume event."""  
        pass
    
    @abstractmethod
    def on_mouse_move(self, event: QGraphicsSceneMouseEvent) -> bool: 
        """Handle mouse move. Return True to consume event."""
        pass
    
    @abstractmethod
    def on_mouse_release(self, event: QGraphicsSceneMouseEvent) -> bool: 
        """Handle mouse release. Return True to consume event."""  
        pass

    @abstractmethod
    def on_mouse_double_click(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Handle mouse double click. Return True to consume event."""
        pass
    
    def on_enter(self): 
        """Called when entering this state."""
        pass
    
    def on_exit(self): 
        """Called when exiting this state."""  
        pass
    
    def on_selection_changed(self, selected_items: list) -> None:
        """Called when scene selection changes."""
        pass
    
    def apply_grid_snapping(self, event: QGraphicsSceneMouseEvent) -> None:
        """Apply grid snapping after default mouse move behavior."""
        pass
    
    def keyPressEvent(self, event: QKeyEvent) -> bool:
        """
        Handle keyboard shortcuts for canvas operations.
        
        This method can be overridden in subclasses to add state-specific shortcuts.
        Returns True if the event was handled and should not propagate further.
        """
        return False
