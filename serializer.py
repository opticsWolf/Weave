# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Serializer - Graph serialization and deserialization.

This module handles saving and loading complete graph state to/from JSON,
ensuring all nodes, connections, and properties are preserved correctly.
"""

import uuid
from typing import Dict, List, Any, Optional, TYPE_CHECKING
from pathlib import Path

# Import required types conditionally to prevent circular imports
if TYPE_CHECKING:
    from weave.canvas import QtNodeCanvas  # noqa: F401
    from PySide6.QtWidgets import QGraphicsView  # noqa: F401

from weave.logger import get_logger
log = get_logger("Serializer")

# Import NodePort and Node to ensure compatibility
try:
    from weave.node.node_port import NodePort
except ImportError:
    NodePort = Any

try:
    from weave.node.node_core import Node
except ImportError:
    Node = Any


def _get_node_id(node) -> str:
    """
    Extract node ID using unified UUID interface.
    
    This function provides a consistent way to retrieve or generate 
    a unique identifier for nodes, prioritizing the new UUIDMixin approach.

    Args:
        node: The Node instance to get the ID from
        
    Returns:
        A string representation of the node's unique identifier
    """
    # Priority: get_uuid_string (UUIDMixin standard)
    if hasattr(node, 'get_uuid_string'):
        uid = node.get_uuid_string()
        if uid:
            return uid
    
    # Fallback: unique_id property 
    uid = getattr(node, 'unique_id', None)
    if uid:
        return str(uid)
    
    # Generate new UUID if none exists (this should not normally happen with proper Node construction)
    new_uid = str(uuid.uuid4())
    if hasattr(node, 'unique_id'):
        node.unique_id = new_uid
    return new_uid


class GraphSerializer:
    """
    Comprehensive serializer for the entire Node Canvas application state.
    """

    FORMAT_VERSION = "3.0"

    def __init__(self, registry_map: Dict[str, type]) -> None:
        """
        Args:
            registry_map: Map of class name strings to their Python types.
                          e.g. {'FloatNode': FloatNode, 'IntNode': IntNode}
        """
        self.node_registry = registry_map

    # =======================================================================
    # SERIALIZE (Save)
    # =======================================================================

    def serialize(
        self,
        canvas,
        view=None,
    ) -> str:
        """
        Serialize the graph state to a JSON string.

        Per-graph state only — workspace preferences (theme, grid type,
        trace style, snapping, minimap) are persisted separately via
        StyleManager / QSettings and are NOT included here.

        Args:
            canvas:  The QtNodeCanvas (QGraphicsScene subclass).
            view:    Optional QGraphicsView (QtCanvasView) for viewport state.

        Returns:
            A JSON string representing the graph state.
        """
        data: Dict[str, Any] = {
            "meta": self._serialize_meta(),
            "canvas": self._serialize_canvas(canvas),
            "nodes": [],
            "connections": [],
        }

        # Optional sections
        if view is not None:
            data["view"] = self._serialize_view(view)

        # Serialize nodes
        node_id_map: Dict[Any, str] = {}
        for item in canvas.items():
            if isinstance(item, Node) and hasattr(item, 'get_state'):
                serialized_node = self._serialize_node(item)
                data["nodes"].append(serialized_node)
                
                # Keep mapping of node instances to their IDs
                node_id_map[item] = serialized_node["id"]

        # Serialize connections using the unified map
        data["connections"] = self._serialize_connections(canvas, node_id_map)

        return self._format_output(data)

    def _serialize_meta(self) -> Dict[str, Any]:
        """Serialize metadata about the graph."""
        return {
            "version": self.FORMAT_VERSION,
            "exported_at": uuid.uuid4().hex,
        }

    def _serialize_canvas(self, canvas) -> Dict[str, Any]:
        """Serialize canvas-specific properties (if any)."""
        # For now, this is a placeholder. Add specific canvas state here if needed.
        return {}

    def _serialize_node(self, node) -> Dict[str, Any]:
        """Serialize a single node using unified UUID handling."""
        node_data = {
            "id": _get_node_id(node),
            "class": node.__class__.__name__,
        }
        
        # Get state from the Node's own serialization method
        try:
            if hasattr(node, 'get_state'):
                node_data["state"] = node.get_state()
            else:
                log.warning(f"Node {node} does not have get_state() method")
                node_data["state"] = {}
        except Exception as e:
            log.error(f"Error serializing node {node}: {e}")
            node_data["state"] = {}

        return node_data

    def _serialize_connections(
        self, canvas, node_id_map: Dict[Any, str]
        ) -> List[Dict[str, Any]]:
        """Serialize all NodeTrace connections in the scene."""
        connections: List[Dict[str, Any]] = []

        try:
            from weave.node.node_trace import NodeTrace
        except ImportError:
            log.warning("Could not import NodeTrace. Connections will not be saved.")
            return connections

        for item in canvas.items():
            if not isinstance(item, NodeTrace):
                continue

            src = getattr(item, "source", None)
            dst = getattr(item, "target", None)
            if not (src and dst):
                continue

            src_node = getattr(src, "node", None)
            dst_node = getattr(dst, "node", None)
            
            # Verify the nodes are in our node_id_map
            if src_node not in node_id_map or dst_node not in node_id_map:
                continue

            try:
                # Serialize by port name instead of array index for dynamic stability (§1)
                src_name = getattr(src, "name", "")
                dst_name = getattr(dst, "name", "")

                # Create connection data using the unified node ID mapping
                connections.append({
                    "source_node_id": node_id_map[src_node],
                    "target_node_id": node_id_map[dst_node],
                    "source_port_name": src_name,
                    "target_port_name": dst_name,
                    "type": type(item).__name__,
                })

            except Exception as e:
                log.warning(f"Could not serialize connection: {e}")
                continue

        return connections

    def _serialize_view(self, view) -> Dict[str, Any]:
        """Serialize viewport state."""
        # Placeholder for future implementation
        return {}

    def _format_output(self, data: Dict[str, Any]) -> str:
        """
        Format the final output string.
        
        In a real implementation, this would use json.dumps or similar.
        For now returning placeholder to satisfy interface.
        """
        import json
        return json.dumps(data, indent=2)

    # =======================================================================
    # DESERIALIZE (Load)
    # =======================================================================

    def deserialize(
        self,
        data: str,
        canvas,
        view=None,
    ) -> None:
        """
        Restore the graph state from a JSON string.
        
        Args:
            data: JSON string representing the graph state.
            canvas: The QtNodeCanvas (QGraphicsScene subclass) to restore into.  
            view: Optional QGraphicsView for viewport restoration.
        """
        import json
        try:
            parsed_data = json.loads(data)
        except Exception as e:
            log.error(f"Failed to parse JSON data: {e}")
            return

        # Deserialize nodes first (in order of dependencies if needed) 
        node_map: Dict[str, Any] = {}
        
        for n_data in parsed_data.get("nodes", []):
            node = self._restore_node(canvas, n_data)
            if node is not None:
                node_map[n_data["id"]] = node

        # Restore connections using the mapping
        self._restore_connections(parsed_data.get("connections", []), canvas, node_map)

        # Restore view state if provided
        if "view" in parsed_data and view is not None:
            self._restore_view(view, parsed_data["view"])

    def _restore_node(self, canvas, n_data: Dict[str, Any]) -> Optional[Any]:
        """Restore a single node ensuring UUID consistency."""
        cls_name = n_data.get("class")
        
        # Get the class from registry
        node_class = self.node_registry.get(cls_name)
        if not node_class:
            log.warning(f"Unknown node class '{cls_name}' in saved data")
            return None

        try:
            # Create instance (title restored via state)
            node = node_class()
            
            # Set the UUID using the unified setter
            node_id = n_data.get("id")
            if node_id and hasattr(node, 'unique_id'):
                node.unique_id = node_id

            # Suspend compute fence during restore to prevent load storms
            if hasattr(node, '_eval_pending'):
                node._eval_pending = True

            # Restore state from saved data
            state = n_data.get("state", {})
            
            if hasattr(node, 'restore_state') and callable(getattr(node, 'restore_state')):
                try:
                    node.restore_state(state)
                except Exception as e:
                    log.warning(f"Failed to restore state for {cls_name}: {e}")
            
        except Exception as e:
            log.error(f"Error creating/restoring node {cls_name}: {e}")
            return None
            
        if canvas is not None:
            try:
                # Route through Orchestrator/NodeManager for proper tracking
                if hasattr(canvas, 'add_node'):
                    pos = state.get("pos", (0, 0)) if isinstance(state, dict) else (0, 0)
                    canvas.add_node(node, pos)
                else:
                    canvas.addItem(node)
            except Exception as e:
                log.warning(f"Failed to add restored node to scene: {e}")

        # ── POST-SCENE GEOMETRY REFRESH ──────────────────────────────
        # restore_state() executed before the node had a scene, so every
        # prepareGeometryChange() call inside it was a no-op.  Qt's
        # scene manager therefore has no bounding-rect history for port
        # labels or sub-components.  Without this forced refresh, stale
        # label positions are never invalidated and appear as ghost
        # artefacts until the next canvas interaction.
        # The duplicate path does not suffer from this because the node
        # is already in a scene when its geometry is calculated.
        if node.scene() is not None:
            node.prepareGeometryChange()
            if hasattr(node, '_recalculate_paths'):
                node._recalculate_paths()
            if hasattr(node, 'update_geometry'):
                node.update_geometry()
            node.update()

        # Resume compute fence
        if hasattr(node, '_eval_pending'):
            node._eval_pending = False

        return node

    def _restore_connections(
        self, 
        connections_data: List[Dict[str, Any]], 
        canvas,
        node_map: Dict[str, Any]
    ) -> None:
        """Restore all connections using the node mapping."""
        try:
            from weave.node.node_trace import NodeTrace
        except ImportError:
            log.warning("Could not import NodeTrace. Connections will not be restored.")
            return

        for conn_data in connections_data:
            src_id = conn_data.get("source_node_id")
            tgt_id = conn_data.get("target_node_id")
            
            if src_id not in node_map or tgt_id not in node_map:
                continue
                
            src_node = node_map[src_id]
            tgt_node = node_map[tgt_id]

            try:
                src_port = None
                tgt_port = None

                # Name-based resolution (preferred — §1)
                if "source_port_name" in conn_data and "target_port_name" in conn_data:
                    src_name = conn_data["source_port_name"]
                    tgt_name = conn_data["target_port_name"]
                    src_port = next((p for p in getattr(src_node, 'outputs', []) if getattr(p, 'name', '') == src_name), None)
                    tgt_port = next((p for p in getattr(tgt_node, 'inputs', []) if getattr(p, 'name', '') == tgt_name), None)
                else:
                    # Legacy index fallback for old save files (§1 backwards compat)
                    src_idx = conn_data.get("source_port_index", 0)
                    tgt_idx = conn_data.get("target_port_index", 0)
                    
                    outputs = getattr(src_node, 'outputs', [])
                    inputs = getattr(tgt_node, 'inputs', [])
                    
                    out_list = list(outputs.values()) if isinstance(outputs, dict) else list(outputs)
                    in_list = list(inputs.values()) if isinstance(inputs, dict) else list(inputs)

                    if 0 <= src_idx < len(out_list):
                        src_port = out_list[src_idx]
                    if 0 <= tgt_idx < len(in_list):
                        tgt_port = in_list[tgt_idx]

                # Only create trace if both ports exist
                if src_port and tgt_port:
                    from weave.portutils import ConnectionFactory
                    # Prevent compute storms during load
                    trace = ConnectionFactory.create(
                        canvas, src_port, tgt_port,
                        validate=False, trigger_compute=False
                    )
                    
            except Exception as e:
                log.warning(f"Failed to restore connection: {e}")

    def _restore_view(self, view, view_data) -> None:
        """Restore viewport state."""
        # Placeholder for future implementation 
        pass

    # =======================================================================
    # FILE I/O METHODS (ADDED TO FIX THE MISSING METHODS)
    # =======================================================================

    def save_to_file(self, filepath: str, canvas, view=None) -> bool:
        """
        Save the graph state to a JSON file.
        
        Args:
            filepath: Path where the file should be saved
            canvas: The QtNodeCanvas (QGraphicsScene subclass)
            view: Optional QGraphicsView for viewport state
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Serialize the graph data
            json_string = self.serialize(canvas, view)
            
            # Write to file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(json_string)
                
            return True
            
        except Exception as e:
            log.error(f"Failed to save to file {filepath}: {e}")
            return False

    def load_from_file(self, filepath: str, canvas, view=None) -> bool:
        """
        Load graph state from a JSON file.
        
        Args:
            filepath: Path of the file to load
            canvas: The QtNodeCanvas (QGraphicsScene subclass)
            view: Optional QGraphicsView for viewport restoration
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Read from file
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Deserialize the data
            self.deserialize(content, canvas, view)
            
            # ── FULL CANVAS REFRESH ──
            if hasattr(canvas, 'update'):
                canvas.update()
            
            return True
            
        except Exception as e:
            log.error(f"Failed to load from file {filepath}: {e}")
            return False
