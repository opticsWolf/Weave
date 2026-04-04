# -*- coding: utf-8 -*-
"""
Node Drag Diagnostic Mixin
===========================

Temporary diagnostic tool for detecting unexpected node position jumps.

Usage
-----
Add ``NodeDragDiagnostic`` to your node's MRO *before* the QGraphicsItem base::

    class BaseControlNode(NodeDragDiagnostic, QGraphicsObject):
        ...

Or, if you prefer not to touch the base class, monkey-patch a single node::

    from weave.canvas.node_drag_diagnostic import patch_node
    patch_node(my_suspicious_node)

The mixin logs every ``setPos`` that moves the node by more than
``JUMP_THRESHOLD`` pixels in a single call.  A stack trace is included
so you can see exactly which code path caused the jump.

Remove this module once the issue is diagnosed.
"""

import traceback
from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QPointF

import logging
_log = logging.getLogger("NodeDragDiag")

# Any single-frame position delta larger than this (Manhattan distance)
# is logged as a potential jump.
JUMP_THRESHOLD = 40.0


class NodeDragDiagnostic:
    """
    Mixin that intercepts ``itemChange(ItemPositionChange, ...)`` to
    detect large sudden position deltas.

    Must appear **before** the QGraphicsItem-derived class in the MRO
    so that ``super().itemChange()`` resolves correctly.
    """

    _diag_last_pos: QPointF | None = None

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            new_pos = value  # QPointF proposed by Qt or setPos()
            if self._diag_last_pos is not None:
                delta = new_pos - self._diag_last_pos
                dist = abs(delta.x()) + abs(delta.y())  # Manhattan
                if dist > JUMP_THRESHOLD:
                    _log.warning(
                        f"JUMP DETECTED on {self!r}: "
                        f"delta=({delta.x():.1f}, {delta.y():.1f})  "
                        f"dist={dist:.1f}px\n"
                        f"  from {self._diag_last_pos} → {new_pos}\n"
                        f"{''.join(traceback.format_stack(limit=8))}"
                    )
            self._diag_last_pos = QPointF(new_pos)  # copy

        return super().itemChange(change, value)


def patch_node(node: QGraphicsItem, threshold: float = JUMP_THRESHOLD) -> None:
    """
    Monkey-patch a single node instance for jump diagnostics.

    This avoids changing the class hierarchy — useful for debugging a
    specific node at runtime::

        patch_node(canvas.selectedItems()[0])

    Args:
        node:      The QGraphicsItem to instrument.
        threshold: Manhattan-distance threshold for logging.
    """
    original = node.itemChange
    last_pos = [node.pos()]  # mutable container for closure

    def _patched_itemChange(change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            new_pos = value
            if last_pos[0] is not None:
                delta = new_pos - last_pos[0]
                dist = abs(delta.x()) + abs(delta.y())
                if dist > threshold:
                    _log.warning(
                        f"JUMP DETECTED on {node!r}: "
                        f"delta=({delta.x():.1f}, {delta.y():.1f})  "
                        f"dist={dist:.1f}px\n"
                        f"  from {last_pos[0]} → {new_pos}\n"
                        f"{''.join(traceback.format_stack(limit=8))}"
                    )
            last_pos[0] = QPointF(new_pos)
        return original(change, value)

    node.itemChange = _patched_itemChange
