# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Canvas Orchestrator - Cleaned version
Unified manager for QGraphicsScene spatial and depth organization.

Handles:
- Layout management (bounds calculation)
- Grid snapping
- Z-ordering (depth management)
"""
from typing import List
from PySide6.QtCore import QRectF, QPointF, QTimer
from PySide6.QtWidgets import QGraphicsScene, QGraphicsItem, QApplication


class CanvasOrchestrator:
    """
    Unified manager for QGraphicsScene spatial and depth organization.
    """

    def __init__(
        self,
        scene: QGraphicsScene,
        margin: int = 500,
        min_width: int = 3000,
        min_height: int = 2000,
        debounce_ms: int = 50,
        base_z: int = 100,
        snap_radius: float = 20.0
    ):
        self._scene = scene
        self._margin = margin
        self._min_width = min_width
        self._min_height = min_height
        self._snap_radius = snap_radius
        
        # Z-Order state
        self._base_z = base_z
        self._current_max_z = base_z

        if not QApplication.instance():
            raise RuntimeError("QApplication must be initialized before CanvasOrchestrator.")

        # Debounced resize timer
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(debounce_ms)
        self._resize_timer.timeout.connect(self.recalculate_bounds)

    # =========================================================================
    # LAYOUT & BOUNDS MANAGEMENT
    # =========================================================================

    def recalculate_bounds(self) -> None:
        """
        Calculate scene bounding box with minimum size and margin.
        Only updates if dimensions actually change.
        """
        content_rect = self._scene.itemsBoundingRect()

        final_rect = QRectF(
            -self._min_width / 2,
            -self._min_height / 2,
            self._min_width,
            self._min_height
        ).united(content_rect)

        final_rect.adjust(
            -self._margin,
            -self._margin,
            self._margin,
            self._margin
        )

        if self._scene.sceneRect() != final_rect:
            self._scene.setSceneRect(final_rect)

    def schedule_resize(self) -> None:
        """Schedule debounced bounds recalculation."""
        if not self._resize_timer.isActive():
            self._resize_timer.start()

    # =========================================================================
    # GRID SNAPPING
    # =========================================================================

    def snap_to_grid(self, pos: QPointF, grid_spacing: int) -> QPointF:
        """Snap coordinate to nearest grid intersection."""
        if grid_spacing <= 0:
            return pos

        return QPointF(
            round(pos.x() / grid_spacing) * grid_spacing,
            round(pos.y() / grid_spacing) * grid_spacing
        )

    def snap_items_to_grid(self, items: List[QGraphicsItem], grid_spacing: int) -> None:
        """
        Batch snap movable items to grid.
        Updates connected traces after snapping.
        """
        if grid_spacing <= 0:
            return

        snapped_items = []
        
        for item in items:
            if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
                old_pos = item.pos()
                new_pos = self.snap_to_grid(old_pos, grid_spacing)
                
                if old_pos != new_pos:
                    item.setPos(new_pos)
                    snapped_items.append(item)
        
        for item in snapped_items:
            self._update_node_traces(item)
            
    def _update_node_traces(self, node: QGraphicsItem) -> None:
        """
        Update all traces connected to a node's ports.
        Called after node position changes to keep traces aligned.
        """
        # Collect all ports from node (either may be absent)
        ports = getattr(node, 'inputs', []) + getattr(node, 'outputs', [])
        
        for port in ports:
            for trace in port.connected_traces:
                trace.update_path()

    # =========================================================================
    # Z-ORDERING
    # =========================================================================

    def bring_to_front(self, item: QGraphicsItem) -> None:
        """Lift item to top of visual stack."""
        if item.zValue() < self._current_max_z:
            self._current_max_z += 1
            item.setZValue(self._current_max_z)

    def send_to_back(self, item: QGraphicsItem, depth: int = 0) -> None:
        """Drop item to specific background depth."""
        item.setZValue(depth)

    def reset_z_order(self) -> None:
        """Reset depth counter to base value."""
        self._current_max_z = self._base_z

    # =========================================================================
    # ACCESSORS
    # =========================================================================

    def scene(self) -> QGraphicsScene:
        return self._scene
    
    @property
    def snap_radius(self) -> float:
        return self._snap_radius
    
    @snap_radius.setter
    def snap_radius(self, value: float) -> None:
        self._snap_radius = max(1.0, value)