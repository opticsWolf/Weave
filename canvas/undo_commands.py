# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

UndoCommands — Granular, command-pattern undo/redo
===================================================

Each user action that mutates the graph produces a lightweight
``UndoCommand`` that stores only the delta — not a full graph
snapshot.  The command knows how to apply itself forward (``redo``)
and backward (``undo``).

Stored data per command type
----------------------------

==============================  =========================================
Command                         Data stored
==============================  =========================================
``MoveNodesCommand``            ``{uuid: (old_pos, new_pos)}`` dict
``ResizeNodeCommand``           node uuid, old_w, old_h, new_w, new_h
``WidgetValueCommand``          node uuid, port name, old value, new value
``NodePropertyCommand``         node uuid, getter/setter names, old, new
``AddNodeCommand``              class name, ``get_state()`` dict snapshot
``RemoveNodesCommand``          per-node state dicts + connection tuples
``AddConnectionCommand``        4-tuple: src/dst uuid + port index
``RemoveConnectionsCommand``    list of 4-tuples
``CompoundCommand``             list of sub-commands
==============================  =========================================

Node resolution
---------------
Commands store **UUID strings**, never live object references.  A
shared ``_find_node(canvas, uuid)`` helper resolves a UUID to the
current scene object at undo/redo time.  This is O(N) over scene
items but runs only on explicit user actions — never in hot paths.

Merging
-------
``WidgetValueCommand`` implements ``try_merge()`` so that rapid
edits to the same widget (typing, scrolling a spinbox) collapse
into a single undo step, replacing the old snapshot-based debounce.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from weave.logger import get_logger
log = get_logger("UndoCommands")


# ======================================================================
# Node resolution helpers
# ======================================================================

def get_node_uid(node) -> str:
    """Return a stable string UUID for *node*.

    Resolution order:
    1. ``node.unique_id`` — set by Canvas.add_node / serializer
    2. ``node.get_uuid_string()`` — Node-level UUID property
    3. Empty string (should never happen if add_node ran)
    """
    uid = getattr(node, 'unique_id', None)
    if uid:
        return uid
    if hasattr(node, 'get_uuid_string'):
        return node.get_uuid_string()
    return ''


def _find_node(canvas, node_uuid: str) -> Optional[QGraphicsItem]:
    """Resolve a UUID string to the live node in *canvas*.

    Checks ``_node_manager.nodes`` first (O(N) over managed nodes),
    then falls back to scanning all scene items.  Tries both
    ``unique_id`` and ``get_uuid_string()`` on each node.
    """
    if not node_uuid:
        return None

    # Fast path: managed nodes list
    if hasattr(canvas, '_node_manager'):
        for node in canvas._node_manager.nodes:
            if get_node_uid(node) == node_uuid:
                return node

    # Fallback: full scene scan
    for item in canvas.items():
        if get_node_uid(item) == node_uuid:
            return item

    return None


def _get_port_lists(node) -> Tuple[list, list]:
    """Return (inputs_list, outputs_list) normalised from dict or list."""
    inputs = getattr(node, 'inputs', [])
    outputs = getattr(node, 'outputs', [])
    in_list = list(inputs.values()) if isinstance(inputs, dict) else list(inputs)
    out_list = list(outputs.values()) if isinstance(outputs, dict) else list(outputs)
    return in_list, out_list


def _batch_trigger_compute(input_ports: list) -> None:
    """Fire set_dirty once per unique downstream node."""
    from weave.portutils import ConnectionFactory
    seen: set = set()
    for port in input_ports:
        node = getattr(port, 'node', None)
        if node is not None and id(node) not in seen:
            seen.add(id(node))
            ConnectionFactory._trigger_compute(port)


# ======================================================================
# Base class
# ======================================================================

class UndoCommand(ABC):
    """A reversible graph mutation."""

    @abstractmethod
    def undo(self, canvas) -> None: ...

    @abstractmethod
    def redo(self, canvas) -> None: ...

    def try_merge(self, other: "UndoCommand") -> bool:
        """Attempt to absorb *other* into this command.

        Returns ``True`` if the merge succeeded (the manager should
        discard *other*).  Default: no merging.
        """
        return False

    @property
    def description(self) -> str:
        return type(self).__name__


# ======================================================================
# Move
# ======================================================================

class MoveNodesCommand(UndoCommand):
    """One or more nodes moved from old positions to new positions.

    Stored data: ``{uuid_str: (QPointF_old, QPointF_new)}``
    """

    def __init__(self, moves: Dict[str, Tuple[QPointF, QPointF]]) -> None:
        self._moves = moves  # {uuid: (old_pos, new_pos)}

    def undo(self, canvas) -> None:
        for uid, (old_pos, _new_pos) in self._moves.items():
            node = _find_node(canvas, uid)
            if node is not None:
                node.setPos(old_pos)
                self._update_traces(node)

    def redo(self, canvas) -> None:
        for uid, (_old_pos, new_pos) in self._moves.items():
            node = _find_node(canvas, uid)
            if node is not None:
                node.setPos(new_pos)
                self._update_traces(node)

    @staticmethod
    def _update_traces(node) -> None:
        for port in list(getattr(node, 'inputs', [])) + list(getattr(node, 'outputs', [])):
            for trace in getattr(port, 'connected_traces', []):
                trace.update_path()

    @property
    def description(self) -> str:
        n = len(self._moves)
        return f"Move {n} node{'s' if n != 1 else ''}"


# ======================================================================
# Resize
# ======================================================================

class ResizeNodeCommand(UndoCommand):
    """A single node was resized from old dimensions to new dimensions.

    Stored data: ``(uuid_str, old_w, old_h, new_w, new_h)``
    """

    def __init__(
        self,
        node_uid: str,
        old_w: float,
        old_h: float,
        new_w: float,
        new_h: float,
    ) -> None:
        self._uid = node_uid
        self._old_w = old_w
        self._old_h = old_h
        self._new_w = new_w
        self._new_h = new_h

    def undo(self, canvas) -> None:
        self._apply(canvas, self._old_w, self._old_h)

    def redo(self, canvas) -> None:
        self._apply(canvas, self._new_w, self._new_h)

    def _apply(self, canvas, w: float, h: float) -> None:
        node = _find_node(canvas, self._uid)
        if node is None:
            return
        node.apply_resize(w, h)

    @property
    def description(self) -> str:
        return "Resize node"



# ======================================================================
# Widget value
# ======================================================================

class WidgetValueCommand(UndoCommand):
    """A widget value changed on a single node/port.

    Supports merging: consecutive edits to the same (node, port)
    collapse into one command, keeping the original ``old_value``
    and updating ``new_value``.
    """

    def __init__(
        self, node_uuid: str, port_name: str,
        old_value: Any, new_value: Any,
    ) -> None:
        self.node_uuid = node_uuid
        self.port_name = port_name
        self.old_value = old_value
        self.new_value = new_value

    def undo(self, canvas) -> None:
        self._apply(canvas, self.old_value)

    def redo(self, canvas) -> None:
        self._apply(canvas, self.new_value)

    def _apply(self, canvas, value: Any) -> None:
        node = _find_node(canvas, self.node_uuid)
        if node is None:
            return
        wc = getattr(node, '_widget_core', None) or getattr(node, '_weave_core', None)
        if wc is None:
            return

        # Use apply_port_value (NOT set_port_value) so the widget's
        # native signal fires.  This is critical for widgets whose
        # signal handlers drive side-effects (e.g. a count spinbox's
        # valueChanged → _on_count_changed → port creation/removal).
        #
        # set_port_value blocks ALL signals, which is correct for normal
        # upstream data pushes but breaks undo/redo because the node's
        # internal handlers never run.
        #
        # apply_port_value suppresses only WidgetCore's value_changed
        # (preventing the undo manager from re-recording), while letting
        # QSpinBox.valueChanged etc. propagate to the node's own slots.
        # The undo manager's _restoring flag is also True as a secondary
        # guard.
        if hasattr(wc, 'apply_port_value'):
            wc.apply_port_value(self.port_name, value)
        else:
            # Fallback for older WidgetCore without apply_port_value
            wc.set_port_value(self.port_name, value)

    def try_merge(self, other: UndoCommand) -> bool:
        if (
            isinstance(other, WidgetValueCommand)
            and other.node_uuid == self.node_uuid
            and other.port_name == self.port_name
        ):
            self.new_value = other.new_value
            return True
        return False

    @property
    def description(self) -> str:
        return f"Change {self.port_name}"


# ======================================================================
# Generic node property (state, header color, etc.)
# ======================================================================

class NodePropertyCommand(UndoCommand):
    """A property changed on one or more nodes via a value-accepting setter.

    *setter_name* must accept a single positional argument (the value).
    Examples: ``set_state(NodeState.DISABLED)``,
    ``set_header_color_by_index(2)``.
    """

    def __init__(
        self,
        changes: List[Tuple[str, Any, Any]],
        setter_name: str,
        label: str = "Change property",
    ) -> None:
        # [(node_uuid, old_value, new_value), ...]
        self._changes = changes
        self._setter = setter_name
        self._label = label

    def undo(self, canvas) -> None:
        for uid, old_val, _new_val in self._changes:
            node = _find_node(canvas, uid)
            if node is not None:
                getattr(node, self._setter)(old_val)

    def redo(self, canvas) -> None:
        for uid, _old_val, new_val in self._changes:
            node = _find_node(canvas, uid)
            if node is not None:
                getattr(node, self._setter)(new_val)

    @property
    def description(self) -> str:
        return self._label


# ======================================================================
# Toggle minimize
# ======================================================================

class ToggleMinimizeCommand(UndoCommand):
    """One or more nodes were minimized or restored.

    Stores the target ``is_minimized`` state for each node.  On undo /
    redo, only calls ``toggle_minimize()`` if the node's current state
    differs from the desired state — avoiding a double-toggle that
    would leave the node in the wrong state.
    """

    def __init__(self, nodes: List[Tuple[str, bool]]) -> None:
        # [(node_uuid, new_is_minimized), ...]
        self._nodes = nodes

    def undo(self, canvas) -> None:
        for uid, was_minimized_after in self._nodes:
            node = _find_node(canvas, uid)
            if node is not None and node.is_minimized == was_minimized_after:
                node.toggle_minimize()

    def redo(self, canvas) -> None:
        for uid, was_minimized_after in self._nodes:
            node = _find_node(canvas, uid)
            if node is not None and node.is_minimized != was_minimized_after:
                node.toggle_minimize()

    @property
    def description(self) -> str:
        return "Toggle minimize"


# ======================================================================
# Add node
# ======================================================================

class AddNodeCommand(UndoCommand):
    """A node was added to the canvas (spawn or paste).

    Stores the node class name and the full ``get_state()`` dict so
    the node can be re-created on redo after an undo removes it.
    """

    def __init__(
        self, class_name: str, state_dict: Dict[str, Any],
        node_uuid: str, pos: Tuple[float, float],
        registry_map: Dict[str, type],
    ) -> None:
        self._class_name = class_name
        self._state = state_dict
        self._uuid = node_uuid
        self._pos = pos
        self._registry = registry_map

    def undo(self, canvas) -> None:
        node = _find_node(canvas, self._uuid)
        if node is None:
            return
        # Remove connections first
        if hasattr(node, 'remove_all_connections'):
            node.remove_all_connections()
        canvas.remove_node(node)

    def redo(self, canvas) -> None:
        cls = self._registry.get(self._class_name)
        if cls is None:
            log.warning(f"AddNodeCommand.redo: class '{self._class_name}' not in registry")
            return
        node = cls()
        node.unique_id = self._uuid
        if hasattr(node, 'restore_state'):
            node.restore_state(self._state)
        canvas.add_node(node, self._pos)

    @property
    def description(self) -> str:
        return f"Add {self._class_name}"


# ======================================================================
# Remove nodes (with their connections)
# ======================================================================

# A connection tuple: (src_uuid, src_port_idx, dst_uuid, dst_port_idx)
ConnectionTuple = Tuple[str, int, str, int]


class RemoveNodesCommand(UndoCommand):
    """One or more nodes were removed from the canvas.

    Captures each node's full serialised state *and* every connection
    that touches any of the removed nodes.  Undo re-creates nodes
    first, then re-creates all captured connections.
    """

    def __init__(
        self,
        node_snapshots: List[Tuple[str, str, Dict[str, Any], Tuple[float, float]]],
        connections: List[ConnectionTuple],
        registry_map: Dict[str, type],
    ) -> None:
        # [(uuid, class_name, state_dict, (x, y)), ...]
        self._nodes = node_snapshots
        self._connections = connections
        self._registry = registry_map

    def undo(self, canvas) -> None:
        from weave.portutils import ConnectionFactory

        # 1. Re-create nodes
        for uid, cls_name, state, pos in self._nodes:
            cls = self._registry.get(cls_name)
            if cls is None:
                log.warning(f"RemoveNodesCommand.undo: class '{cls_name}' not in registry")
                continue
            node = cls()
            node.unique_id = uid
            if hasattr(node, 'restore_state'):
                node.restore_state(state)
            canvas.add_node(node, pos)

        # 2. Re-create connections (deferred compute)
        affected: list = []
        for src_uuid, src_idx, dst_uuid, dst_idx in self._connections:
            src_node = _find_node(canvas, src_uuid)
            dst_node = _find_node(canvas, dst_uuid)
            if src_node is None or dst_node is None:
                continue
            _in, out = _get_port_lists(src_node)
            in2, _out2 = _get_port_lists(dst_node)
            if src_idx < len(out) and dst_idx < len(in2):
                trace = ConnectionFactory.create(
                    canvas, out[src_idx], in2[dst_idx],
                    validate=False, trigger_compute=False,
                )
                if trace is not None:
                    affected.append(in2[dst_idx])
        _batch_trigger_compute(affected)

    def redo(self, canvas) -> None:
        # Remove in reverse order
        for uid, _cls, _state, _pos in reversed(self._nodes):
            node = _find_node(canvas, uid)
            if node is None:
                continue
            if hasattr(node, 'remove_all_connections'):
                node.remove_all_connections()
            canvas.remove_node(node)

    @property
    def description(self) -> str:
        n = len(self._nodes)
        return f"Delete {n} node{'s' if n != 1 else ''}"


# ======================================================================
# Add connection
# ======================================================================

class AddConnectionCommand(UndoCommand):
    """A connection was created between two ports."""

    def __init__(self, conn: ConnectionTuple) -> None:
        self._conn = conn  # (src_uuid, src_port_idx, dst_uuid, dst_port_idx)

    def undo(self, canvas) -> None:
        src_uuid, src_idx, dst_uuid, dst_idx = self._conn
        dst_node = _find_node(canvas, dst_uuid)
        if dst_node is None:
            return
        in_list, _ = _get_port_lists(dst_node)
        if dst_idx >= len(in_list):
            return
        dst_port = in_list[dst_idx]
        # Input ports have at most one connection — remove it
        from weave.portutils import ConnectionFactory
        for trace in list(getattr(dst_port, 'connected_traces', [])):
            ConnectionFactory.remove(trace, trigger_compute=True)

    def redo(self, canvas) -> None:
        from weave.portutils import ConnectionFactory
        src_uuid, src_idx, dst_uuid, dst_idx = self._conn
        src_node = _find_node(canvas, src_uuid)
        dst_node = _find_node(canvas, dst_uuid)
        if src_node is None or dst_node is None:
            return
        _, out = _get_port_lists(src_node)
        in_list, _ = _get_port_lists(dst_node)
        if src_idx < len(out) and dst_idx < len(in_list):
            ConnectionFactory.create(
                canvas, out[src_idx], in_list[dst_idx],
                validate=False, trigger_compute=True,
            )

    @property
    def description(self) -> str:
        return "Add connection"


# ======================================================================
# Remove connections
# ======================================================================

class RemoveConnectionsCommand(UndoCommand):
    """One or more connections were removed.

    Undo/redo defer per-trace compute triggers and batch once per
    downstream node.
    """

    def __init__(self, connections: List[ConnectionTuple]) -> None:
        self._connections = connections

    def undo(self, canvas) -> None:
        from weave.portutils import ConnectionFactory
        affected: list = []
        for src_uuid, src_idx, dst_uuid, dst_idx in self._connections:
            src_node = _find_node(canvas, src_uuid)
            dst_node = _find_node(canvas, dst_uuid)
            if src_node is None or dst_node is None:
                continue
            _, out = _get_port_lists(src_node)
            in_list, _ = _get_port_lists(dst_node)
            if src_idx < len(out) and dst_idx < len(in_list):
                trace = ConnectionFactory.create(
                    canvas, out[src_idx], in_list[dst_idx],
                    validate=False, trigger_compute=False,
                )
                if trace is not None:
                    affected.append(in_list[dst_idx])
        _batch_trigger_compute(affected)

    def redo(self, canvas) -> None:
        from weave.portutils import ConnectionFactory
        affected: list = []
        for src_uuid, _src_idx, dst_uuid, dst_idx in self._connections:
            dst_node = _find_node(canvas, dst_uuid)
            if dst_node is None:
                continue
            in_list, _ = _get_port_lists(dst_node)
            if dst_idx >= len(in_list):
                continue
            dst_port = in_list[dst_idx]
            for trace in list(getattr(dst_port, 'connected_traces', [])):
                ConnectionFactory.remove(trace, trigger_compute=False)
            affected.append(dst_port)
        _batch_trigger_compute(affected)

    @property
    def description(self) -> str:
        n = len(self._connections)
        return f"Remove {n} connection{'s' if n != 1 else ''}"


# ======================================================================
# Add port (dynamic)
# ======================================================================

class AddPortCommand(UndoCommand):
    """A port was dynamically added to a node at runtime.

    Stores the port definition so that undo can remove it and redo can
    re-create it.  Connected traces are NOT captured here — they are
    always empty at creation time.
    """

    def __init__(
        self,
        node_uuid: str,
        port_name: str,
        datatype: str,
        is_output: bool,
        description: str = "",
    ) -> None:
        self._node_uuid = node_uuid
        self._port_name = port_name
        self._datatype = datatype
        self._is_output = is_output
        self._port_desc = description

    def undo(self, canvas) -> None:
        node = _find_node(canvas, self._node_uuid)
        if node is None:
            return
        if hasattr(node, 'remove_port'):
            node.remove_port(self._port_name, is_output=self._is_output)

    def redo(self, canvas) -> None:
        node = _find_node(canvas, self._node_uuid)
        if node is None:
            return
        if self._is_output:
            node.add_output(self._port_name, self._datatype, self._port_desc)
        else:
            node.add_input(self._port_name, self._datatype, self._port_desc)

    @property
    def description(self) -> str:
        side = "output" if self._is_output else "input"
        return f"Add {side} port '{self._port_name}'"


# ======================================================================
# Remove port (dynamic)
# ======================================================================

class RemovePortCommand(UndoCommand):
    """A port was dynamically removed from a node at runtime.

    Captures the port definition and any connections that were attached
    to it so that undo can fully restore the port and its traces.
    """

    def __init__(
        self,
        node_uuid: str,
        port_name: str,
        datatype: str,
        is_output: bool,
        description: str = "",
        connections: Optional[List[ConnectionTuple]] = None,
    ) -> None:
        self._node_uuid = node_uuid
        self._port_name = port_name
        self._datatype = datatype
        self._is_output = is_output
        self._port_desc = description
        self._connections = connections or []

    def undo(self, canvas) -> None:
        from weave.portutils import ConnectionFactory

        # 1. Re-create port
        node = _find_node(canvas, self._node_uuid)
        if node is None:
            return
        if self._is_output:
            node.add_output(self._port_name, self._datatype, self._port_desc)
        else:
            node.add_input(self._port_name, self._datatype, self._port_desc)

        # 2. Re-create connections (deferred compute)
        affected: list = []
        for src_uuid, src_idx, dst_uuid, dst_idx in self._connections:
            src_node = _find_node(canvas, src_uuid)
            dst_node = _find_node(canvas, dst_uuid)
            if src_node is None or dst_node is None:
                continue
            _, out = _get_port_lists(src_node)
            in_list, _ = _get_port_lists(dst_node)
            if src_idx < len(out) and dst_idx < len(in_list):
                trace = ConnectionFactory.create(
                    canvas, out[src_idx], in_list[dst_idx],
                    validate=False, trigger_compute=False,
                )
                if trace is not None:
                    affected.append(in_list[dst_idx])
        _batch_trigger_compute(affected)

    def redo(self, canvas) -> None:
        node = _find_node(canvas, self._node_uuid)
        if node is None:
            return
        if hasattr(node, 'remove_port'):
            node.remove_port(self._port_name, is_output=self._is_output)

    @property
    def description(self) -> str:
        side = "output" if self._is_output else "input"
        return f"Remove {side} port '{self._port_name}'"


# ======================================================================
# Port visibility
# ======================================================================

class PortVisibilityCommand(UndoCommand):
    """One or more ports had their visibility toggled.

    Stores ``[(port_name, is_output, old_visible, new_visible), ...]``
    for a single node.
    """

    def __init__(
        self,
        node_uuid: str,
        changes: List[Tuple[str, bool, bool, bool]],
    ) -> None:
        # changes: [(port_name, is_output, was_visible, now_visible), ...]
        self._node_uuid = node_uuid
        self._changes = changes

    def undo(self, canvas) -> None:
        self._apply(canvas, revert=True)

    def redo(self, canvas) -> None:
        self._apply(canvas, revert=False)

    def _apply(self, canvas, revert: bool) -> None:
        node = _find_node(canvas, self._node_uuid)
        if node is None:
            return
        for port_name, is_output, was_visible, now_visible in self._changes:
            target_vis = was_visible if revert else now_visible
            port = None
            if hasattr(node, 'find_port'):
                port = node.find_port(port_name, is_output=is_output)
            if port is not None and hasattr(node, 'set_port_visible'):
                node.set_port_visible(port, target_vis)

    @property
    def description(self) -> str:
        n = len(self._changes)
        return f"Toggle {n} port visibility"


# ======================================================================
# Compound command
# ======================================================================

class CompoundCommand(UndoCommand):
    """Groups multiple sub-commands into a single undo step.

    Undo applies sub-commands in reverse order; redo in forward order.
    """

    def __init__(self, children: List[UndoCommand], label: str = "") -> None:
        self._children = children
        self._label = label

    def undo(self, canvas) -> None:
        for cmd in reversed(self._children):
            cmd.undo(canvas)

    def redo(self, canvas) -> None:
        for cmd in self._children:
            cmd.redo(canvas)

    @property
    def description(self) -> str:
        return self._label or f"{len(self._children)} actions"


# ======================================================================
# Snapshot helpers — used by callers to capture state BEFORE mutation
# ======================================================================

def capture_node_snapshot(
    node,
) -> Tuple[str, str, Dict[str, Any], Tuple[float, float]]:
    """Return ``(uuid, class_name, state_dict, (x, y))`` for a node.

    Call this **before** removing the node so that its full state is
    preserved for undo.
    """
    uid = get_node_uid(node)
    cls_name = type(node).__name__
    state = node.get_state() if hasattr(node, 'get_state') else {}
    pos = (node.pos().x(), node.pos().y())
    return uid, cls_name, state, pos


def capture_node_connections(
    canvas, node,
) -> List[ConnectionTuple]:
    """Return every connection touching *node* as 4-tuples.

    Call this **before** removing the node.
    """
    from weave.node.node_trace import NodeTrace
    seen: set = set()
    result: List[ConnectionTuple] = []

    for port_attr in ('inputs', 'outputs'):
        for port in getattr(node, port_attr, []):
            for trace in list(getattr(port, 'connected_traces', [])):
                if id(trace) in seen:
                    continue
                seen.add(id(trace))

                src = getattr(trace, 'source', None)
                dst = getattr(trace, 'target', None)
                if src is None or dst is None:
                    continue

                src_node = getattr(src, 'node', None)
                dst_node = getattr(dst, 'node', None)
                if src_node is None or dst_node is None:
                    continue

                _, out = _get_port_lists(src_node)
                in_list, _ = _get_port_lists(dst_node)
                try:
                    src_idx = out.index(src)
                    dst_idx = in_list.index(dst)
                except ValueError:
                    continue

                result.append((
                    get_node_uid(src_node),
                    src_idx,
                    get_node_uid(dst_node),
                    dst_idx,
                ))
    return result
