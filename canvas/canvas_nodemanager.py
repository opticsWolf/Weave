# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Node Management: Handles node addition, removal, cloning, and z-ordering.
Fixed implementation with proper node tracking to prevent garbage collection.

v11 Fix: Corrected attribute names in _reconstruct_connections:
- _connected_traces -> connected_traces
- _source -> source
- _target -> target

Removed duplicate code (now in qt_portutils):
- _create_connection -> ConnectionFactory.create
"""

from typing import Any, Tuple, Dict, List, Optional, Set
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

# Import consolidated port utilities
from weave.portutils import ConnectionFactory

from weave.logger import get_logger
log = get_logger("NodeManager")

# Graceful import handling for external node components
#try:
#from weave.node import NodeTrace, DragTrace
HAS_NODE_COMPONENTS = True
#except ImportError:
#    NodeTrace = Any
#    DragTrace = Any
#    HAS_NODE_COMPONENTS = False


class NodeManager:
    """
    Manages node lifecycle: creation, cloning, removal, and z-ordering.
    
    Maintains strong references to all managed nodes to prevent
    garbage collection issues.
    """

    def __init__(
        self, 
        scene: QGraphicsScene, 
        z_order_manager: Any, 
        grid_spacing: int = 20
    ) -> None:
        """
        Initializes the NodeManager.

        Args:
            scene: The QGraphicsScene to manage.
            z_order_manager: Object responsible for handling item stacking.
            grid_spacing: Offset distance used for placing cloned nodes.
        """
        self._scene = scene
        self._z_order_manager = z_order_manager
        self._grid_spacing = grid_spacing
        
        # CRITICAL: Keep strong references to all managed nodes
        # This prevents garbage collection from removing nodes unexpectedly
        self._managed_nodes: List[QGraphicsItem] = []

    @property
    def nodes(self) -> List[QGraphicsItem]:
        """Returns a copy of the managed nodes list."""
        return list(self._managed_nodes)

    def add_node(self, node: QGraphicsItem, pos: Tuple[float, float] = (0, 0)) -> None:
        """
        Add a node to the scene at the specified position.

        Args:
            node: The QGraphicsItem to add.
            pos: (x, y) coordinates for the node position.
        """
        # Add to scene if not already there
        if node.scene() != self._scene:
            self._scene.addItem(node)
        
        node.setPos(pos[0], pos[1])
        self._z_order_manager.bring_to_front(node)
        
        # Keep strong reference to prevent garbage collection
        if node not in self._managed_nodes:
            self._managed_nodes.append(node)

    def remove_node(self, node: QGraphicsItem) -> None:
        """
        Remove a node from the scene and properly clean up all associated traces.
        
        This method ensures that when nodes are removed, their connected port traces 
        are also cleaned up appropriately to prevent orphaned connection objects.
        """
        # First ensure we have access to NodePort components
        if hasattr(node, 'inputs') and hasattr(node, 'outputs'):
            # Clean up all ports' connections first (this ensures traces are properly disconnected)
            node.clear_ports()
        
        # Remove from tracking first  
        if node in self._managed_nodes:
            self._managed_nodes.remove(node)
        
        # Then remove from scene
        if node.scene() == self._scene:
            self._scene.removeItem(node)

    def bring_to_front(self, node: QGraphicsItem) -> None:
        """
        Bring a node to the front of the z-order.

        Args:
            node: The item to promote to the top layer.
        """
        self._z_order_manager.bring_to_front(node)

    def clone_nodes(self, nodes: List[QGraphicsItem]) -> List[QGraphicsItem]:
        """
        Clone a list of nodes using state serialization.
        Preserves visual style, port configuration, and internal topology.

        Args:
            nodes: List of nodes to be cloned.

        Returns:
            List of newly created and positioned node clones.
        """
        if not nodes:
            return []

        log.info(f"Cloning {len(nodes)} nodes...")

        # Mapping: Original Instance -> New Instance for connection reconstruction
        original_to_clone: Dict[QGraphicsItem, QGraphicsItem] = {}
        
        # Calculate offset (down and right)
        offset_val = self._grid_spacing * 2
        offset = QPointF(offset_val, offset_val)
        
        new_nodes: List[QGraphicsItem] = []

        # PASS 1: Instantiation & State Restoration
        for original in nodes:
            clone = self._clone_single_node(original, offset)
            if clone:
                original_to_clone[original] = clone
                new_nodes.append(clone)
                log.info(f"Cloned: {type(original).__name__}")

        # PASS 2: Connection (Trace) Reconstruction
        if len(new_nodes) > 1:
            log.debug(f"Reconstructing connections for {len(new_nodes)} nodes...")
            self._reconstruct_connections(original_to_clone)

        # Update UI selection to the new clones
        self._scene.clearSelection()
        for node in new_nodes:
            node.setSelected(True)
            # Ensure visibility by bringing to front
            self._z_order_manager.bring_to_front(node)

        log.debug(f"Clone complete: {len(new_nodes)} nodes created")
        return new_nodes

    def _clone_single_node(
        self, 
        original: QGraphicsItem, 
        offset: QPointF
    ) -> Optional[QGraphicsItem]:
        """
        Internal helper to clone a single node via its state protocol.
        
        Tries multiple cloning strategies in order of preference:
        1. get_state/restore_state protocol
        2. clone() method if available
        3. serialize/deserialize protocol
        4. Basic instantiation with property copying
        """
        clone = None
        
        # Strategy 1: State protocol (preferred)
        if hasattr(original, 'get_state') and hasattr(original, 'restore_state'):
            try:
                clone = type(original)()
                state = original.get_state()
                clone.restore_state(state)
                
                # Also restore widget state if available
                if hasattr(original, 'get_widget_state') and hasattr(clone, 'set_widget_state'):
                    widget_state = original.get_widget_state()
                    clone.set_widget_state(widget_state)
                    
            except Exception as e:
                log.error(f"State-based clone failed for {type(original).__name__}: {e}")
                clone = None
        
        # Strategy 2: Clone method
        if clone is None and hasattr(original, 'clone'):
            try:
                clone = original.clone()
            except Exception as e:
                log.error(f"clone() method failed for {type(original).__name__}: {e}")
                clone = None
        
        # Strategy 3: Serialization protocol
        if clone is None and hasattr(original, 'serialize') and hasattr(type(original), 'deserialize'):
            try:
                data = original.serialize()
                clone = type(original).deserialize(data)
            except Exception as e:
                log.error(f"Serialization clone failed for {type(original).__name__}: {e}")
                clone = None
        
        # Strategy 4: Basic instantiation (last resort)
        if clone is None:
            try:
                clone = type(original)()
                # Try to copy basic properties
                if hasattr(original, 'node_name') and hasattr(clone, 'set_name'):
                    clone.set_name(original.node_name)
            except Exception as e:
                log.error(f"Basic clone failed for {type(original).__name__}: {e}")
                return None
        
        if clone is None:
            return None
        
        # Position with offset
        new_pos = original.pos() + offset
        clone.setPos(new_pos)
        
        # Register in scene and tracking via add_node
        # This ensures the node is properly tracked
        self.add_node(clone, (new_pos.x(), new_pos.y()))
        
        return clone

    def _reconstruct_connections(
        self, 
        original_to_clone: Dict[QGraphicsItem, QGraphicsItem]
    ) -> None:
        """
        Reconstruct connections between cloned nodes using the map.
        
        Only creates connections where BOTH endpoints are in the clone set.
        This ensures cloned node groups are independent from originals.
        """
        if not HAS_NODE_COMPONENTS:
            log.debug("No node components available for trace reconstruction")
            return

        # Track which connections we've already created to avoid duplicates
        created_connections: Set[Tuple[int, int]] = set()
        connections_created = 0

        # Iterate through all original nodes that were cloned
        for original, clone in original_to_clone.items():
            # Check node has inputs/outputs
            original_inputs = getattr(original, 'inputs', [])
            clone_inputs = getattr(clone, 'inputs', [])
            
            if not original_inputs:
                continue

            # For each input port on the original node
            for i, orig_input in enumerate(original_inputs):
                if i >= len(clone_inputs):
                    continue
                
                clone_input = clone_inputs[i]
                
                # Get connected traces from the original input port
                # Try multiple attribute names for compatibility
                connected_traces = getattr(orig_input, 'connected_traces', None)
                if connected_traces is None:
                    connected_traces = getattr(orig_input, '_connected_traces', None)
                if connected_traces is None:
                    # Try as a property that returns empty list
                    connected_traces = []
                
                # Iterate through traces connected to this input
                for trace in list(connected_traces):
                    # Get source port of the trace
                    source_port = getattr(trace, 'source', None)
                    if source_port is None:
                        source_port = getattr(trace, '_source', None)
                    
                    if source_port is None:
                        continue

                    # Get the node that owns the source port
                    source_node = getattr(source_port, 'node', None)
                    if source_node is None:
                        source_node = getattr(source_port, '_node', None)
                    
                    if source_node is None:
                        continue
                    
                    # Only recreate connection if source node is ALSO being cloned
                    if source_node not in original_to_clone:
                        continue
                    
                    # Get the cloned version of the source node
                    cloned_source_node = original_to_clone[source_node]
                    
                    # Find the index of the source port in original's outputs
                    original_outputs = getattr(source_node, 'outputs', [])
                    cloned_outputs = getattr(cloned_source_node, 'outputs', [])
                    
                    try:
                        src_idx = list(original_outputs).index(source_port)
                    except (ValueError, AttributeError):
                        continue
                    
                    if src_idx >= len(cloned_outputs):
                        continue
                    
                    cloned_source_port = cloned_outputs[src_idx]

                    # Avoid duplicate connections
                    connection_key = (id(cloned_source_port), id(clone_input))
                    if connection_key in created_connections:
                        continue
                    created_connections.add(connection_key)

                    # Create the connection using ConnectionFactory
                    result = ConnectionFactory.create(
                        self._scene,
                        cloned_source_port,
                        clone_input,
                        validate=False,  # Skip validation for cloning
                        trigger_compute=False  # Don't trigger compute during cloning
                    )
                    
                    if result:
                        connections_created += 1

        if connections_created > 0:
            log.debug(f"Reconstructed {connections_created} connections")

    def clear_all(self) -> None:
        """
        Remove all managed nodes from the scene.
        """
        # Create a copy of the list since we're modifying it
        nodes_to_remove = list(self._managed_nodes)
        
        for node in nodes_to_remove:
            self.remove_node(node)
    
    def get_node_count(self) -> int:
        """Returns the number of managed nodes."""
        return len(self._managed_nodes)