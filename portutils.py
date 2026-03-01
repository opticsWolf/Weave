# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Port Utilities - Consolidated port-related logic.

This module eliminates duplication across the canvas system by providing
a single source of truth for:
- Port direction detection
- Connection compatibility (leveraging PortRegistry)
- Port finding within snap radius
- Connection creation

REPLACES duplicated code in:
- canvasorchestrator.py (_check_port_compatibility, _find_snap_port, _create_connection)
- canvasstates.py (_is_compatible, _find_snap_port)
- canvas.py (_validate_connection, _create_connection)
- canvasnodemanager.py (_create_connection)
- nodetrace.py (DragTrace._detect_port_direction)
"""

from typing import Optional, Any, Tuple, List, Set, TYPE_CHECKING
import uuid  # Added for UUID support
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QGraphicsScene

# Import the existing type system
from weave.portregistry import PortRegistry, PortType

# Import NodePort (doesn't create circular import)
#try:
#    from nodeport import NodePort

#except ImportError:
#    NodePort = Any
#    HAS_NODE_PORT = False

# NodeTrace is imported lazily in ConnectionFactory to avoid circular import
# (qt_nodetrace imports qt_portutils, so we can't import qt_nodetrace here)

from weave.node.node_port import NodePort
HAS_NODE_PORT = True

from weave.logger import get_logger
log = get_logger("PortUtils")

class PortUtils:
    """
    Utility class for port-related operations.
    
    Provides a single source of truth for logic that was previously
    duplicated across 7+ locations in the codebase.
    """
    
    # ==========================================================================
    # PORT DIRECTION
    # ==========================================================================
    
    @staticmethod
    def is_output(port: 'NodePort') -> bool:
        """
        Determine if a port is an output port.
        
        This replaces the defensive getattr pattern found in 7 locations.
        NodePort always has `is_output` defined, but this handles edge cases.
        
        Args:
            port: The port to check.
            
        Returns:
            True if output port, False if input port.
        """
        # Primary: Direct attribute (NodePort always has this)
        if hasattr(port, 'is_output'):
            return bool(port.is_output)
        
        # Fallback 1: Inverse of is_input
        if hasattr(port, 'is_input'):
            return not port.is_input
        
        # Fallback 2: String direction attribute
        if hasattr(port, 'direction'):
            return str(port.direction).lower() in ('out', 'output')
        
        # Default: Assume output
        return True
    
    @staticmethod
    def get_direction_sign(port: 'NodePort') -> float:
        """
        Get the direction sign for bezier curve control points.
        
        This replaces DragTrace._detect_port_direction().
        
        Args:
            port: The port to get direction for.
            
        Returns:
            +1.0 for output ports, -1.0 for input ports.
        """
        return 1.0 if PortUtils.is_output(port) else -1.0
    
    # ==========================================================================
    # NODE ACCESS
    # ==========================================================================
    
    @staticmethod
    def get_node(port: 'NodePort') -> Optional[Any]:
        """
        Get the parent node of a port.
        
        NodePort uses `self.node` (public attribute).
        
        Args:
            port: The port to get the node from.
            
        Returns:
            The parent node or None.
        """
        return getattr(port, 'node', None)
    
    # ==========================================================================
    # POSITION
    # ==========================================================================
    
    @staticmethod
    def get_scene_center(port: 'NodePort') -> QPointF:
        """
        Get the center position of a port in scene coordinates.
        
        Args:
            port: The port to get position from.
            
        Returns:
            Scene position of port center.
        """
        if hasattr(port, 'get_scene_center'):
            return port.get_scene_center()
        return port.scenePos()
    
    # ==========================================================================
    # COMPATIBILITY CHECKING
    # ==========================================================================
    
    @staticmethod
    def are_compatible(
        port_a: 'NodePort', 
        port_b: 'NodePort',
        check_existing: bool = True
    ) -> bool:
        """
        Check if two ports can be connected.
        
        This is the SINGLE SOURCE OF TRUTH for connection validation.
        Replaces _check_port_compatibility, _is_compatible, _validate_connection.
        
        Uses PortRegistry.get_converter() for datatype compatibility,
        which handles:
        - Type identity
        - Explicit casts (Int → Float)
        - Type inheritance (Int inherits Number)
        - Generic/wildcard types
        
        Args:
            port_a: First port.
            port_b: Second port.
            check_existing: If True, also checks for existing connections.
            
        Returns:
            True if ports can be connected.
        """
        # Rule 1: Can't connect to self
        if port_a is port_b:
            return False
        
        # Rule 2: Can't connect ports on the same node
        node_a = PortUtils.get_node(port_a)
        node_b = PortUtils.get_node(port_b)
        if node_a is not None and node_b is not None and node_a is node_b:
            return False
        
        # Rule 3: Must be opposite types (output → input)
        a_is_output = PortUtils.is_output(port_a)
        b_is_output = PortUtils.is_output(port_b)
        if a_is_output == b_is_output:
            return False
        
        # Rule 4: Check for existing connection (optional)
        if check_existing:
            if PortUtils._has_existing_connection(port_a, port_b):
                return False
        
        # Rule 5: Datatype compatibility via PortRegistry
        return PortUtils.check_datatype_compatibility(port_a, port_b)
    
    @staticmethod
    def check_datatype_compatibility(
        port_a: 'NodePort', 
        port_b: 'NodePort'
    ) -> bool:
        """
        Check if port datatypes are compatible.
        
        LEVERAGES PortRegistry instead of duplicating the logic!
        
        Args:
            port_a: First port (can be input or output).
            port_b: Second port.
            
        Returns:
            True if datatypes are compatible.
        """
        # Get port types (PortType objects from registry)
        type_a = getattr(port_a, 'port_type', None)
        type_b = getattr(port_b, 'port_type', None)
        
        # If we have PortType objects, use the registry's converter system
        if isinstance(type_a, PortType) and isinstance(type_b, PortType):
            # Determine which is source (output) and which is target (input)
            if PortUtils.is_output(port_a):
                source_type, target_type = type_a, type_b
            else:
                source_type, target_type = type_b, type_a
            
            # Check if either type is generic/wildcard (allow in both directions)
            # PortRegistry.get_converter only checks if TARGET is generic,
            # but we also need to allow connections FROM generic types
            source_name = source_type.name.lower() if source_type.name else ''
            target_name = target_type.name.lower() if target_type.name else ''
            
            if source_name in ('generic', 'any', 'object') or target_name in ('generic', 'any', 'object'):
                return True
            
            # Use PortRegistry's sophisticated compatibility check
            is_valid, _ = PortRegistry.get_converter(source_type, target_type)
            return is_valid
        
        # Fallback: Use PortType.can_connect_from if available
        if type_b is not None and hasattr(type_b, 'can_connect_from') and type_a is not None:
            if PortUtils.is_output(port_a):
                return type_b.can_connect_from(type_a)
            else:
                return type_a.can_connect_from(type_b)
        
        # Legacy fallback: string-based datatype comparison
        return PortUtils._legacy_datatype_check(port_a, port_b)
    
    @staticmethod
    def _legacy_datatype_check(port_a: 'NodePort', port_b: 'NodePort') -> bool:
        """
        Legacy fallback for ports without PortType objects.
        
        Handles string-based datatype attributes.
        """
        WILDCARD_TYPES = frozenset({'any', 'object', '*', 'generic'})
        
        a_dtype = getattr(port_a, 'datatype', None)
        b_dtype = getattr(port_b, 'datatype', None)
        
        # If either is unspecified, allow connection
        if a_dtype is None or b_dtype is None:
            return True
        
        # Exact match
        if a_dtype == b_dtype:
            return True
        
        # Check for wildcard types
        a_str = str(a_dtype).lower()
        b_str = str(b_dtype).lower()
        
        if a_str in WILDCARD_TYPES or b_str in WILDCARD_TYPES:
            return True
        
        return False
    
    @staticmethod
    def _has_existing_connection(port_a: 'NodePort', port_b: 'NodePort') -> bool:
        """Check if a connection already exists between two ports."""
        traces_a = getattr(port_a, 'connected_traces', [])
        
        for trace in traces_a:
            trace_source = getattr(trace, 'source', None)
            trace_target = getattr(trace, 'target', None)
            
            # Enhanced check using UUID comparison
            if trace_source is port_b or trace_target is port_b:
                return True
            
            # If we have UUIDs available for both ports, use that instead of identity 
            trace_source_uuid = getattr(trace_source, '_port_uuid', None)  
            trace_target_uuid = getattr(trace_target, '_port_uuid', None)
            port_b_uuid = getattr(port_b, '_port_uuid', None)
            
            if (trace_source_uuid is not None and 
                trace_target_uuid is not None and
                port_b_uuid is not None):
                if (trace_source_uuid == port_b_uuid or 
                    trace_target_uuid == port_b_uuid):
                    return True
        
        return False
    
    # ==========================================================================
    # PORT ORDERING
    # ==========================================================================
    
    @staticmethod
    def order_ports(
        port_a: 'NodePort', 
        port_b: 'NodePort'
    ) -> Tuple['NodePort', 'NodePort']:
        """
        Order ports as (output_port, input_port).
        
        NodeTrace expects (source=output, target=input).
        
        Args:
            port_a: First port.
            port_b: Second port.
            
        Returns:
            Tuple of (output_port, input_port).
        """
        if PortUtils.is_output(port_a):
            return (port_a, port_b)
        return (port_b, port_a)

    # ==========================================================================
    # UUID-AWARE FUNCTIONS
    # ==========================================================================

    @staticmethod
    def get_port_uuid(port: 'NodePort') -> Optional[uuid.UUID]:
        """
        Get the UUID of a port.
        
        Args:
            port: The port to get UUID for
            
        Returns:
            A uuid.UUID object or None if not available
        """
        return getattr(port, '_port_uuid', None)

    @staticmethod
    def port_matches_uuid(port: 'NodePort', target_uuid: uuid.UUID) -> bool:
        """
        Check if a port matches the given UUID.
        
        Args:
            port: The port to check
            target_uuid: The UUID to match against
            
        Returns:
            True if the port's UUID matches, False otherwise
        """
        return getattr(port, '_port_uuid', None) == target_uuid


class PortFinder:
    """
    Utility for finding ports in a scene within a snap radius.
    
    Replaces _find_snap_port in orchestrator and states.
    """
    
    DEFAULT_SNAP_RADIUS = 20.0
    
    @staticmethod
    def find_nearest_compatible(
        scene: QGraphicsScene,
        position: QPointF,
        source_port: 'NodePort',
        snap_radius: Optional[float] = None,
        check_existing: bool = True
    ) -> Optional['NodePort']:
        """
        Find the nearest compatible port within snap radius.
        
        Args:
            scene: The scene to search in.
            position: Scene position to search around.
            source_port: The port we're connecting from.
            snap_radius: Search radius (defaults to DEFAULT_SNAP_RADIUS).
            check_existing: Whether to exclude existing connections.
            
        Returns:
            Nearest compatible NodePort or None.
        """
        if scene is None or not HAS_NODE_PORT:
            return None
        
        radius = snap_radius if snap_radius is not None else PortFinder.DEFAULT_SNAP_RADIUS
        
        # Create search rectangle
        search_rect = QRectF(
            position.x() - radius,
            position.y() - radius,
            radius * 2,
            radius * 2
        )
        
        # Get all items in search area
        items = scene.items(search_rect)
        
        best_port = None
        best_distance_sq = float('inf')
        radius_sq = radius * radius
        
        for item in items:
            # Skip non-ports
            if not isinstance(item, NodePort):
                continue
            
            # Skip source port itself
            if item is source_port:
                continue

            # Skip summary (dummy) ports on minimized nodes - they are never
            # valid connection targets regardless of type compatibility.
            if getattr(item, 'is_summary_port', False):
                continue

            # Check compatibility using the unified method
            if not PortUtils.are_compatible(source_port, item, check_existing=check_existing):
                continue
            
            # Calculate squared distance (faster than sqrt)
            port_center = PortUtils.get_scene_center(item)
            dx = port_center.x() - position.x()
            dy = port_center.y() - position.y()
            distance_sq = dx * dx + dy * dy
            
            if distance_sq < radius_sq and distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_port = item
        
        return best_port


class ConnectionFactory:
    """
    Factory for creating connections between ports.
    
    Replaces _create_connection in orchestrator, node manager, and canvas.
    """
    
    @staticmethod
    def create(
        scene: QGraphicsScene,
        port_a: 'NodePort',
        port_b: 'NodePort',
        validate: bool = True,
        trigger_compute: bool = True
    ) -> Optional['NodeTrace']:
        """
        Create a connection between two ports.
        
        Args:
            scene: The scene to add the connection to.
            port_a: One of the ports to connect.
            port_b: The other port to connect.
            validate: Whether to validate compatibility first.
            trigger_compute: Whether to trigger downstream computation.
            
        Returns:
            The created NodeTrace or None on failure.
        """
        # Lazy import to avoid circular dependency (qt_nodetrace imports qt_portutils)
        try:
            from weave.node.node_trace import NodeTrace
        except ImportError:
            return None
        
        # Optional validation
        if validate and not PortUtils.are_compatible(port_a, port_b):
            return None
        
        # Order correctly: output → input
        output_port, input_port = PortUtils.order_ports(port_a, port_b)
        
        try:
            # Create the trace
            trace = NodeTrace(output_port, input_port)
            scene.addItem(trace)
            
            # Trigger downstream computation if requested
            if trigger_compute:
                ConnectionFactory._trigger_compute(input_port)
            
            return trace
            
        except Exception as e:
            log.warning(f"Failed to create connection: {e}")
            return None
    
    @staticmethod
    def _trigger_compute(input_port: 'NodePort') -> None:
        """Trigger computation on the receiving node."""
        node = PortUtils.get_node(input_port)
        if node is None:
            return
        
        # Try standard interfaces in order of preference
        if hasattr(node, 'set_dirty'):
            node.set_dirty()
        elif hasattr(node, 'evaluate'):
            node.evaluate()

    @staticmethod
    def remove(trace, trigger_compute: bool = True) -> None:
        """
        Remove a connection and optionally trigger recomputation.
        
        This is the disconnect counterpart to create(). Keeps all compute
        triggering at the operation level inside ConnectionFactory.
        
        Args:
            trace: The NodeTrace to remove.
            trigger_compute: Whether to trigger downstream recomputation.
                             Pass False during mid-drag detachment so compute
                             only fires when the user finalises the disconnect.
        """
        # Capture target port BEFORE unregistering from ports
        target_port = getattr(trace, 'target', None)

        # Unregister from ports
        source = getattr(trace, 'source', None)
        if source is not None and hasattr(source, 'remove_trace'):
            source.remove_trace(trace)
        if target_port is not None and hasattr(target_port, 'remove_trace'):
            target_port.remove_trace(trace)

        # Remove from scene
        scene = trace.scene() if hasattr(trace, 'scene') else None
        if scene:
            scene.removeItem(trace)

        # Trigger recomputation (mirrors _trigger_compute in create)
        if trigger_compute and target_port is not None:
            ConnectionFactory._trigger_compute(target_port)