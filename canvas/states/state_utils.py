# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

from typing import Optional, Type, TypeVar
from PySide6.QtWidgets import QGraphicsSceneMouseEvent, QGraphicsItem, QGraphicsProxyWidget, QGraphicsTextItem
from PySide6.QtCore import Qt, QPointF, QElapsedTimer
from PySide6.QtGui import QTransform

from weave.basenode import BaseControlNode
from weave.node.node_port import NodePort

from weave.logger import get_logger
log = get_logger("State Utils")

#--------------------------------------------------------
# Optimized Node SHAKE GESTURE RECOGNIZER  
#--------------------------------------------------------
class OptimizedShakeRecognizer:
    """
    High-performance shake detection with minimal branching.
    
    Key improvements over original:
    - Delta-based tracking (works with relative movements)
    - Reduced state complexity (no deque pruning overhead)
    - Better noise filtering for high-DPI mice
    - O(1) time complexity with minimal memory allocation
    """
    
    def __init__(
        self, 
        threshold: float = 50.0,
        min_changes: int = 4,
        timeout_ms: int = 500,
        debug: bool = False
    ):
        """
        Args:
            threshold: Minimum pixels per stroke to count as valid
            min_changes: Number of direction reversals needed
            timeout_ms: Maximum time window for gesture completion
            debug: Enable debug log
        """
        self.threshold = threshold
        self.min_changes = min_changes
        self.timeout_ms = timeout_ms
        self.debug = debug
        
        # Minimal state tracking
        self._direction_changes = 0
        self._last_dx = 0.0
        self._stroke_dist = 0.0
        self._timer = QElapsedTimer()
        self._timer.start()

    def reset(self):
        """Clear all tracking state."""
        self._direction_changes = 0
        self._stroke_dist = 0.0
        self._last_dx = 0.0
        self._timer.restart()
        
        if self.debug:
            log.debug("OptimizedShakeRecognizer: Reset")

    def update(self, delta: QPointF) -> bool:
        """
        Process relative movement delta. Returns True on gesture detection.
        
        Args:
            delta: Movement delta (current_pos - last_pos)
            
        Returns:
            True if shake gesture detected, False otherwise
        """
        # Check timeout
        if self._timer.elapsed() > self.timeout_ms:
            self.reset()
            return False

        dx = delta.x()
        
        # Filter micro-movements (reduces mouse jitter false positives)
        if abs(dx) < 2.0:
            return False

        # Detect direction reversal
        if (dx > 0 and self._last_dx < 0) or (dx < 0 and self._last_dx > 0):
            # Was previous stroke long enough?
            if self._stroke_dist >= self.threshold:
                self._direction_changes += 1
                self._stroke_dist = 0.0
                
                if self.debug:
                    log.debug(
                        f"OptimizedShake: Valid reversal #{self._direction_changes} "
                        f"(stroke: {self._stroke_dist:.1f}px)"
                    )
        
        # Accumulate stroke distance
        self._last_dx = dx
        self._stroke_dist += abs(dx)

        # Check for gesture completion
        if self._direction_changes >= self.min_changes:
            if self.debug:
                log.info(f"OptimizedShake: GESTURE DETECTED")
            self.reset()
            return True
            
        return False
    
#--------------------------------------------------------
#Item Resolution Utility Functions  
#--------------------------------------------------------

T = TypeVar('T', bound=QGraphicsItem)

class ItemResolver:
    """
    Consolidated utility for resolving items from scene positions.
    """
    
    @staticmethod
    def resolve_at(
        scene,
        scene_pos,
        target_type: Type[T],
        transform: Optional[QTransform] = None
    ) -> Optional[T]:
        """
        Find an item of the specified type at the given scene position.
        """
        if transform is None:
            transform = QTransform()
            
        item = scene.itemAt(scene_pos, transform)
        return ItemResolver._walk_up_to_type(item, target_type)
    
    @staticmethod
    def _walk_up_to_type(item: Optional[QGraphicsItem], target_type: Type[T]) -> Optional[T]:
        """Walk up the parent hierarchy to find an item of the specified type."""
        target = item
        while target is not None:
            if isinstance(target, target_type):
                return target
            target = target.parentItem()
        return None
    
    @staticmethod
    def resolve_port_at(scene, scene_pos) -> Optional[NodePort]:
        """Convenience method to find a port at a position."""
        return ItemResolver.resolve_at(scene, scene_pos, NodePort)
    
    @staticmethod
    def resolve_node_at(scene, scene_pos) -> Optional[BaseControlNode]:
        """Convenience method to find a node at a position."""
        return ItemResolver.resolve_at(scene, scene_pos, BaseControlNode)
