# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Undo Commands - Command pattern implementation for undo/redo operations.
Architecturally corrected to enforce NodeManager routing, robust trace 
serialization, and accurate widget state restoration.
"""

import uuid
from typing import Optional, List, Dict, Set, Tuple, Union, Any
from enum import Enum

from PySide6.QtCore import QObject, Signal, QPointF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene
from PySide6.QtGui import QUndoCommand

from weave.portutils import PortUtils, ConnectionFactory
from weave.logger import get_logger

log = get_logger("UndoCommands")


# ======================================================================
# Universal UUID Helpers
# ======================================================================

def get_node_uid(node) -> str:
    """Return a stable string UUID for a node utilizing the UUIDMixin."""
    if node is None:
        return ""
    uid = PortUtils.get_node_uuid_string(node)
    return uid if uid else f"node_{id(node)}"


def get_port_uid(port) -> str:
    """Return stable UUID string for a port utilizing the UUIDMixin."""
    if port is None:
        return ""
    uid = PortUtils.get_port_uuid_string(port)
    return uid if uid else f"port_{id(port)}"


def resolve_node_by_uuid(canvas, node_uuid: Union[str, uuid.UUID]) -> Optional[QGraphicsItem]:
    """Safely resolve a UUID string to a live node instance."""
    if not node_uuid:
        return None
    
    # Fast path via NodeManager
    search_items = canvas._node_manager.nodes if hasattr(canvas, '_node_manager') else canvas.items()
    
    for item in search_items:
        if PortUtils.node_matches_uuid(item, node_uuid):
            return item
            
    return None

# ======================================================================
# Command Base Classes
# ======================================================================

class UndoCommand(QUndoCommand):
    """Abstract base class for all undo commands with unified hierarchical support."""

    def __init__(self, parent: Optional[QUndoCommand] = None) -> None:
        super().__init__(parent)
        self._child_count = 0

    def add_child(self, cmd: 'UndoCommand') -> None:
        """Add a sub-command to enable macro groupings."""
        if cmd is not None and isinstance(cmd, UndoCommand):
            self._child_count += 1
            super().add_child(cmd)

    def mergeWith(self, other: QUndoCommand) -> bool:
        return False
        
    def try_merge(self, other: 'UndoCommand') -> bool:
        """Alias for custom merge logic utilized by the UndoManager."""
        return self.mergeWith(other)

    @property
    def description(self) -> str:
        return type(self).__name__
    
    def get_affected_node_uuids(self) -> Set[str]:
        return set()


# ======================================================================
# Macros & Grouping
# ======================================================================

class CompoundCommand(UndoCommand):
    """A collection of commands executed atomically."""

    def __init__(self, commands: List[UndoCommand], description: str = "Compound Operation") -> None:
        super().__init__()
        self._description = description
        self._children: List[UndoCommand] = []
        for cmd in commands:
            self.add_child(cmd)
            self._children.append(cmd)

    def get_affected_node_uuids(self) -> Set[str]:
        uuids: Set[str] = set()
        for cmd in self._children:
            uuids.update(cmd.get_affected_node_uuids())
        return uuids

    @property
    def description(self) -> str:
        return self._description

    @property
    def children(self) -> List[UndoCommand]:
        return self._children


# ======================================================================
# Nodes: Add / Remove / Move / Resize / Properties
# ======================================================================

class AddNodeCommand(UndoCommand):
    """Spawn a node and ensure it is tracked via NodeManager."""

    def __init__(self, cls_name: str, state: Dict, uid: str, pos: Tuple[float, float], registry_map: Dict[str, type]) -> None:
        super().__init__()
        self._cls_name = cls_name
        self._state = state
        self._uid = uid
        self._pos = pos
        self._registry = registry_map
        self._created_node = None

    def undo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._uid)
        if node:
            if hasattr(canvas, 'remove_node'):
                canvas.remove_node(node)
            else:
                canvas.removeItem(node)
            self._created_node = None

    def redo(self, canvas) -> None:
        cls = self._registry.get(self._cls_name)
        if not cls:
            log.warning(f"AddNodeCommand: Class '{self._cls_name}' not in registry.")
            return
            
        node = cls()
        node.unique_id = self._uid
        
        if hasattr(node, '_eval_pending'):
            node._eval_pending = True
            
        if hasattr(node, 'restore_state'):
            node.restore_state(self._state)
            
        if hasattr(canvas, 'add_node'):
            canvas.add_node(node, self._pos)
        else:
            canvas.addItem(node)
            node.setPos(*self._pos)
            
        if hasattr(node, '_eval_pending'):
            node._eval_pending = False
            
        self._created_node = node

    def get_affected_node_uuids(self) -> Set[str]:
        return {self._uid}


class RemoveNodesCommand(UndoCommand):
    """Remove multiple nodes natively executing through the Canvas orchestrator."""

    def __init__(
        self,
        node_snapshots: List[Tuple[str, str, Dict[str, Any], Tuple[float, float]]],
        connections: List[Tuple[str, str, str, str]],
        registry_map: Dict[str, type],
    ) -> None:
        super().__init__()
        self._nodes = node_snapshots
        self._connections = connections
        self._registry = registry_map

    def undo(self, canvas) -> None:
        # 1. Re-create nodes
        for uid, cls_name, state, pos in self._nodes:
            cls = self._registry.get(cls_name)
            if cls is None:
                log.warning(f"RemoveNodesCommand: '{cls_name}' not in registry")
                continue
                
            node = cls()
            node.unique_id = uid
            
            if hasattr(node, '_eval_pending'):
                node._eval_pending = True
                
            if hasattr(node, 'restore_state'):
                node.restore_state(state)
            
            # CRITICAL FIX: Ensure Canvas tracks the newly recreated node
            if hasattr(canvas, 'add_node'):
                canvas.add_node(node, pos)
            else:
                canvas.addItem(node)
                node.setPos(pos[0], pos[1])
            
            if hasattr(node, '_eval_pending'):
                node._eval_pending = False

        # 2. Re-create connections via ConnectionFactory (name-based)
        for src_uid, src_name, dst_uid, dst_name in self._connections:
            src_node = resolve_node_by_uuid(canvas, src_uid)
            dst_node = resolve_node_by_uuid(canvas, dst_uid)
            if src_node is None or dst_node is None:
                continue

            src_port = next((p for p in getattr(src_node, 'outputs', []) if p.name == src_name), None)
            dst_port = next((p for p in getattr(dst_node, 'inputs', []) if p.name == dst_name), None)

            if src_port and dst_port:
                ConnectionFactory.create(canvas, src_port, dst_port, validate=False, trigger_compute=True)

    def redo(self, canvas) -> None:
        # Execute the deletion
        for uid, _, _, _ in self._nodes:
            node = resolve_node_by_uuid(canvas, uid)
            if node:
                if hasattr(canvas, 'remove_node'):
                    canvas.remove_node(node)
                else:
                    canvas.removeItem(node)

    def get_affected_node_uuids(self) -> Set[str]:
        return {uid for uid, _, _, _ in self._nodes}


class MoveNodesCommand(UndoCommand):
    """Translate nodes between old and new positions."""

    def __init__(self, moves: Dict[str, Tuple[QPointF, QPointF]]) -> None:
        super().__init__()
        self._moves = moves

    def undo(self, canvas) -> None:
        for uid, (old_pos, _new_pos) in self._moves.items():
            node = resolve_node_by_uuid(canvas, uid)
            if node is not None:
                node.setPos(old_pos)
                self._update_traces(node)

    def redo(self, canvas) -> None:
        for uid, (_old_pos, new_pos) in self._moves.items():
            node = resolve_node_by_uuid(canvas, uid)
            if node is not None:
                node.setPos(new_pos)
                self._update_traces(node)

    @staticmethod
    def _update_traces(node) -> None:
        for port in getattr(node, 'inputs', []) + getattr(node, 'outputs', []):
            for trace in getattr(port, 'connected_traces', []):
                if hasattr(trace, 'update_path'):
                    trace.update_path()

    def get_affected_node_uuids(self) -> Set[str]:
        return set(self._moves.keys())


class ResizeNodeCommand(UndoCommand):
    """Handle node scale adjustments."""

    def __init__(self, uid: str, old_w: float, old_h: float, new_w: float, new_h: float):
        super().__init__()
        self._uid = uid
        self._old_size = (old_w, old_h)
        self._new_size = (new_w, new_h)

    def undo(self, canvas):
        node = resolve_node_by_uuid(canvas, self._uid)
        if node: node.apply_resize(*self._old_size)

    def redo(self, canvas):
        node = resolve_node_by_uuid(canvas, self._uid)
        if node: node.apply_resize(*self._new_size)

    def get_affected_node_uuids(self) -> Set[str]:
        return {self._uid}


class ToggleMinimizeCommand(UndoCommand):
    """Toggle a node's minimized geometry state."""

    def __init__(self, toggles: List[Tuple[str, bool]]):
        super().__init__()
        self._toggles = toggles

    def undo(self, canvas):
        for uid, was_min in self._toggles:
            node = resolve_node_by_uuid(canvas, uid)
            if node: node.toggle_minimize()

    def redo(self, canvas):
        for uid, was_min in self._toggles:
            node = resolve_node_by_uuid(canvas, uid)
            if node: node.toggle_minimize()


class NodePropertyCommand(UndoCommand):
    """Atomic adjustment of a visual/architectural property on a Node."""

    def __init__(self, changes: List[Tuple[str, Any, Any]], method: str, desc: str):
        super().__init__()
        self._changes = changes
        self._method = method
        self._desc = desc

    @property
    def description(self): 
        return self._desc

    def undo(self, canvas):
        for uid, old_val, new_val in self._changes:
            node = resolve_node_by_uuid(canvas, uid)
            if node:
                if self._method == 'set_state':
                    node.set_state(old_val)
                elif self._method == 'set_config':
                    node.set_config(header_bg=old_val)

    def redo(self, canvas):
        for uid, old_val, new_val in self._changes:
            node = resolve_node_by_uuid(canvas, uid)
            if node:
                if self._method == 'set_state':
                    node.set_state(new_val)
                elif self._method == 'set_config':
                    node.set_config(header_bg=new_val)


class WidgetValueCommand(UndoCommand):
    """Push state changes safely back into the Node's underlying WidgetCore."""

    def __init__(self, node_uuid: str, port_name: str, old_value: Any, new_value: Any) -> None:
        super().__init__()
        self._node_uuid = node_uuid
        self._port_name = port_name
        self._old_value = old_value
        self._new_value = new_value

    def undo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node:
            wc = getattr(node, '_widget_core', None) or getattr(node, '_weave_core', None)
            if wc and hasattr(wc, 'apply_port_value'):
                wc.apply_port_value(self._port_name, self._old_value)

    def redo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node:
            wc = getattr(node, '_widget_core', None) or getattr(node, '_weave_core', None)
            if wc and hasattr(wc, 'apply_port_value'):
                wc.apply_port_value(self._port_name, self._new_value)

    def get_affected_node_uuids(self) -> Set[str]:
        return {self._node_uuid}

    @property
    def node_uuid(self) -> str:
        return self._node_uuid

    @property
    def port_name(self) -> str:
        return self._port_name

    @property
    def old_value(self) -> Any:
        return self._old_value

    @property
    def new_value(self) -> Any:
        return self._new_value

    @property
    def description(self) -> str:
        return f"Change '{self._port_name}' widget value"

    def mergeWith(self, other: 'UndoCommand') -> bool:
        """Coalesce rapid continuous changes (e.g., slider drags) on the same widget."""
        if not isinstance(other, WidgetValueCommand):
            return False

        if self._node_uuid == other.node_uuid and self._port_name == other.port_name:
            # Adopt the incoming command's new value, retaining our original old_value
            self._new_value = other.new_value
            return True

        return False


# ======================================================================
# Ports & Connections
# ======================================================================

class AddPortCommand(UndoCommand):
    """Add a port to an existing node."""

    def __init__(self, node, name: str, datatype: str = "flow", is_output: bool = False) -> None:
        super().__init__()
        self._node_uuid = get_node_uid(node)
        self._port_name = name
        self._datatype = datatype
        self._is_output = is_output

    def undo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node and hasattr(node, 'remove_port'):
            node.remove_port(self._port_name, is_output=self._is_output)

    def redo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node:
            func = getattr(node, 'add_output' if self._is_output else 'add_input')
            func(self._port_name, self._datatype, "")


class RemovePortCommand(UndoCommand):
    """Safely destroy a port, backing up and seamlessly restoring its traces."""

    def __init__(self, node, port) -> None:
        super().__init__()
        self._node_uuid = get_node_uid(node)
        self._port_name = getattr(port, 'name', None) or port if isinstance(port, str) else None
        self._is_output = getattr(port, 'is_output', False)
        
        self._port_state = {}
        if hasattr(port, 'get_state'):
            try: self._port_state = port.get_state()
            except Exception: pass
            
        # Store connections cleanly by Port Name (defends against index shifts!)
        self._saved_connections = []
        if not isinstance(port, str):
            for trace in getattr(port, 'connected_traces', []):
                src = trace.source
                dst = trace.target
                if not src or not dst: continue
                src_node = getattr(src, 'node', None)
                dst_node = getattr(dst, 'node', None)
                if not src_node or not dst_node: continue
                
                self._saved_connections.append((
                    get_node_uid(src_node), src.name,
                    get_node_uid(dst_node), dst.name
                ))

    def undo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if not node or not self._port_state: return
        
        is_output = self._port_state.get('is_output', self._is_output)
        func = getattr(node, 'add_output' if is_output else 'add_input')
        func(self._port_name, self._port_state.get('datatype', 'flow'), self._port_state.get('description', ''))
        
        for src_uid, src_name, dst_uid, dst_name in self._saved_connections:
            src_node = resolve_node_by_uuid(canvas, src_uid)
            dst_node = resolve_node_by_uuid(canvas, dst_uid)
            if not src_node or not dst_node: continue
            
            src_port = next((p for p in getattr(src_node, 'outputs', []) if p.name == src_name), None)
            dst_port = next((p for p in getattr(dst_node, 'inputs', []) if p.name == dst_name), None)
            if src_port and dst_port:
                ConnectionFactory.create(canvas, src_port, dst_port, validate=False, trigger_compute=True)

    def redo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node and self._port_name:
            node.remove_port(self._port_name, is_output=self._is_output)


class AddPortsCommand(UndoCommand):
    """Batch operation for adding arrays of dynamic ports."""

    def __init__(self, node, port_configs: List[Dict]) -> None:
        super().__init__()
        self._node_uuid = get_node_uid(node)
        self._port_configs = port_configs
        
    def undo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node:
            for config in reversed(self._port_configs):
                node.remove_port(config['name'], is_output=config.get('is_output', False))

    def redo(self, canvas) -> None:
        node = resolve_node_by_uuid(canvas, self._node_uuid)
        if node:
            for config in self._port_configs:
                func = getattr(node, 'add_output' if config.get('is_output', False) else 'add_input')
                func(config['name'], config.get('datatype', 'flow'), config.get('description', ''))


class AddConnectionCommand(UndoCommand):
    """Create a new trace link."""

    def __init__(self, connection: Tuple[str, str, str, str]):
        super().__init__()
        self._connection = connection

    def undo(self, canvas):
        src_uid, src_name, dst_uid, dst_name = self._connection
        src_node = resolve_node_by_uuid(canvas, src_uid)
        dst_node = resolve_node_by_uuid(canvas, dst_uid)
        if not src_node or not dst_node: return

        src_port = next((p for p in getattr(src_node, 'outputs', []) if p.name == src_name), None)
        dst_port = next((p for p in getattr(dst_node, 'inputs', []) if p.name == dst_name), None)

        if src_port and dst_port:
            for t in list(dst_port.connected_traces):
                if t.source == src_port:
                    ConnectionFactory.remove(t, trigger_compute=True)
                    break

    def redo(self, canvas):
        src_uid, src_name, dst_uid, dst_name = self._connection
        src_node = resolve_node_by_uuid(canvas, src_uid)
        dst_node = resolve_node_by_uuid(canvas, dst_uid)
        if not src_node or not dst_node: return

        src_port = next((p for p in getattr(src_node, 'outputs', []) if p.name == src_name), None)
        dst_port = next((p for p in getattr(dst_node, 'inputs', []) if p.name == dst_name), None)

        if src_port and dst_port:
            ConnectionFactory.create(canvas, src_port, dst_port, validate=False, trigger_compute=True)


class RemoveConnectionsCommand(UndoCommand):
    """Sever trace links across multiple items (e.g. Backspace / Shake actions)."""

    def __init__(self, connections: List[Tuple[str, str, str, str]]):
        super().__init__()
        self._connections = connections

    def undo(self, canvas):
        for src_uid, src_name, dst_uid, dst_name in self._connections:
            src_node = resolve_node_by_uuid(canvas, src_uid)
            dst_node = resolve_node_by_uuid(canvas, dst_uid)
            if not src_node or not dst_node: continue

            src_port = next((p for p in getattr(src_node, 'outputs', []) if p.name == src_name), None)
            dst_port = next((p for p in getattr(dst_node, 'inputs', []) if p.name == dst_name), None)

            if src_port and dst_port:
                ConnectionFactory.create(canvas, src_port, dst_port, validate=False, trigger_compute=True)

    def redo(self, canvas):
        for src_uid, src_name, dst_uid, dst_name in self._connections:
            src_node = resolve_node_by_uuid(canvas, src_uid)
            dst_node = resolve_node_by_uuid(canvas, dst_uid)
            if not src_node or not dst_node: continue

            src_port = next((p for p in getattr(src_node, 'outputs', []) if p.name == src_name), None)
            dst_port = next((p for p in getattr(dst_node, 'inputs', []) if p.name == dst_name), None)

            if src_port and dst_port:
                for t in list(dst_port.connected_traces):
                    if t.source == src_port:
                        ConnectionFactory.remove(t, trigger_compute=True)
                        break


# ======================================================================
# Snapshot Helpers
# ======================================================================

def capture_node_snapshot(node) -> Tuple[str, str, Dict[str, Any], Tuple[float, float]]:
    """Return (uuid, class_name, state_dict, (x, y)) for a node."""
    uid = get_node_uid(node)
    cls_name = type(node).__name__
    state = node.get_state() if hasattr(node, 'get_state') else {}
    pos = (node.pos().x(), node.pos().y())
    return uid, cls_name, state, pos


def capture_node_connections(canvas, node) -> List[Tuple[str, str, str, str]]:
    """Generates name-based connection tuples for recreating connections."""
    seen = set()
    result = []
    
    for port_attr in ('inputs', 'outputs'):
        for port in getattr(node, port_attr, []):
            for trace in list(getattr(port, 'connected_traces', [])):
                if id(trace) in seen: continue
                seen.add(id(trace))
                
                src = trace.source
                dst = trace.target
                if not src or not dst: continue
                
                src_node = getattr(src, 'node', None)
                dst_node = getattr(dst, 'node', None)
                if not src_node or not dst_node: continue
                
                # Use names instead of indices for total stability
                result.append((
                    get_node_uid(src_node), getattr(src, 'name', ''),
                    get_node_uid(dst_node), getattr(dst, 'name', '')
                ))
                
    return result