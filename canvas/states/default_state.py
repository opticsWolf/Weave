# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Default Interaction State — idle / selection / drag / shake.

Refactor highlights:
  § 1  Uses ``get_movable_nodes`` from state_utils (single source of truth).
  § 2  Legacy header paths (_min_btn_rect, _state_icon_rect) removed.
       Headers must expose ``get_minimize_btn_rect`` / ``get_state_slider_rect``.
  § 3  ``on_mouse_press`` delegates to a prioritised handler chain.
  § 4  State transitions go through ``self.request_transition(name, **kw)``
       — no direct imports of other state classes.
  § 5  Style caching handled by ``StylableStateMixin``.
  § 6  ``ItemResolver`` now multi-hit aware (scene.items).
"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsProxyWidget,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QKeyEvent

from weave.logger import get_logger

log = get_logger("DefaultState")

from weave.canvas.states.interaction_state import (
    CanvasInteractionState,
    InteractionHandler,
)
from weave.canvas.states.state_utils import (
    ItemResolver,
    OptimizedShakeRecognizer,
    StylableStateMixin,
    build_connection_tuples,
    get_movable_nodes,
)
from weave.canvas.commands_mixin import CanvasCommandsMixin
from weave.portutils import ConnectionFactory
from weave.node.node_port import NodePort
from weave.node.node_trace import DragTrace, NodeTrace
from weave.canvas.undo_commands import get_node_uid


# ============================================================================
# INTERACTION HANDLERS  (Review §3 — Chain of Responsibility)
# ============================================================================

class ProxyWidgetHandler:
    """Detect and yield to interactive widgets inside nodes."""

    def try_handle(
        self,
        event: QGraphicsSceneMouseEvent,
        state: "DefaultInteractionState",
    ) -> bool:
        scene_pos = event.scenePos()
        if not state._is_interactive_widget_click(scene_pos):
            return False
        state._ensure_node_selected(scene_pos, event)
        return state._yield_to_proxy(scene_pos)


class PortHandler:
    """Start a new connection drag or prepare detachment from a port."""

    def try_handle(
        self,
        event: QGraphicsSceneMouseEvent,
        state: "DefaultInteractionState",
    ) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        port = ItemResolver.resolve_port_at(state.canvas, event.scenePos())
        if port is None:
            return False

        # Summary ports on minimised nodes cannot start drags
        if getattr(port, "is_summary_port", False):
            return False

        # Input port with existing connection → prepare detachment
        if not port.is_output and port.connected_traces:
            trace = port.connected_traces[0]
            source = trace.source
            if source:
                state.request_transition(
                    "connection_drag",
                    start_port=source,
                    pending_detach_port=port,
                    pending_detach_trace=trace,
                )
                event.accept()
                return True

        # Start new connection drag
        state.request_transition("connection_drag", start_port=port)
        event.accept()
        return True


class NodeButtonHandler:
    """Handle presses on header buttons (minimise, state slider).

    Headers must expose ``get_minimize_btn_rect()`` and
    ``get_state_slider_rect()``.  Legacy ``_min_btn_rect`` /
    ``_state_icon_rect`` attributes are no longer supported.
    """

    _HIT_PAD_BTN = 5
    _HIT_PAD_SLIDER = 3

    def try_handle(
        self,
        event: QGraphicsSceneMouseEvent,
        state: "DefaultInteractionState",
    ) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        node = ItemResolver.resolve_node_at(state.canvas, event.scenePos())
        if node is None:
            return False

        local_pos = node.mapFromScene(event.scenePos())
        header = node.header
        uid = get_node_uid(node)

        # ── Minimise button ───────────────────────────────────────────
        if hasattr(header, "get_minimize_btn_rect"):
            btn = header.get_minimize_btn_rect()
            if not btn.isEmpty():
                hit = btn.adjusted(
                    -self._HIT_PAD_BTN, -self._HIT_PAD_BTN,
                    self._HIT_PAD_BTN, self._HIT_PAD_BTN,
                )
                if hit.contains(local_pos):
                    from weave.canvas.undo_commands import ToggleMinimizeCommand

                    node.toggle_minimize()
                    state._push_cmd(
                        ToggleMinimizeCommand([(uid, node.is_minimized)])
                    )
                    return True

        # ── State slider ──────────────────────────────────────────────
        if hasattr(header, "get_state_slider_rect"):
            slider = header.get_state_slider_rect()
            if not slider.isEmpty():
                hit = slider.adjusted(
                    -self._HIT_PAD_SLIDER, -self._HIT_PAD_SLIDER,
                    self._HIT_PAD_SLIDER, self._HIT_PAD_SLIDER,
                )
                if hit.contains(local_pos):
                    from weave.canvas.undo_commands import NodePropertyCommand

                    old = getattr(node, "_state", None)
                    node.cycle_state()
                    new = getattr(node, "_state", None)
                    state._push_cmd(
                        NodePropertyCommand(
                            [(uid, old, new)], "set_state", "Cycle state"
                        )
                    )
                    return True

        return False


# ============================================================================
# DEFAULT INTERACTION STATE
# ============================================================================

class DefaultInteractionState(StylableStateMixin, CanvasInteractionState):
    """Idle state: selection, movement, grid snapping, shake-to-disconnect.

    Mouse-press logic is decomposed into an ordered handler chain
    (``_press_handlers``).  Adding new interaction categories is a matter
    of inserting a new handler — not patching a monolithic method.
    """

    def __init__(self, canvas):
        super().__init__(canvas)

        # ── Style cache (via mixin) ───────────────────────────────────
        self._init_style_cache()

        # ── Shake detection ───────────────────────────────────────────
        self._shake_recognizer = OptimizedShakeRecognizer(
            threshold=self._shake_threshold,
            min_changes=self._shake_min_changes,
            timeout_ms=self._shake_timeout_ms,
        )

        # ── Drag state ────────────────────────────────────────────────
        self._is_dragging = False
        self._drag_started_pos: Optional[QPointF] = None
        self._last_mouse_pos: Optional[QPointF] = None
        self._drag_start_positions: dict = {}

        # ── Proxy widget interaction ──────────────────────────────────
        self._suppressed_movable_node: Optional[QGraphicsItem] = None

        # ── Handler chain (priority order) ────────────────────────────
        self._press_handlers: list[InteractionHandler] = [
            ProxyWidgetHandler(),
            PortHandler(),
            NodeButtonHandler(),
        ]

    # ------------------------------------------------------------------
    # Style-mixin hook
    # ------------------------------------------------------------------

    def _on_style_cache_updated(self) -> None:
        """Push refreshed values into the shake recogniser."""
        rec = getattr(self, "_shake_recognizer", None)
        if rec is not None:
            rec.threshold = self._shake_threshold
            rec.min_changes = self._shake_min_changes
            rec.timeout_ms = self._shake_timeout_ms

    # ==================================================================
    # WIDGET-EDITING API  (base class contract)
    # ==================================================================

    @property
    def suppressed_node(self) -> Optional[QGraphicsItem]:
        """The node whose ``ItemIsMovable`` was cleared for widget focus."""
        return self._suppressed_movable_node

    def exit_widget_editing(self) -> None:
        """Restore the suppressed node and clear widget-editing state."""
        self._restore_suppressed_node()

    # ==================================================================
    # PROXY-WIDGET HELPERS
    # ==================================================================

    def _is_interactive_widget_click(self, scene_pos: QPointF) -> bool:
        """True if *scene_pos* hits an interactive widget inside a node."""
        node = ItemResolver.resolve_node_at(self.canvas, scene_pos)
        if node is None:
            return False
        core = getattr(node, "_widget_core", None)
        if core is None:
            return False
        return core.is_interactive_at(scene_pos)

    def _yield_to_proxy(self, scene_pos: QPointF) -> bool:
        """Activate or focus the widget at *scene_pos*.

        Strategy 1 — direct activation (popup widgets): fully consumed.
        Strategy 2 — proxy focus (spinbox, line-edit): returns False so
        Qt's default routing delivers the event to the proxy.
        """
        node = ItemResolver.resolve_node_at(self.canvas, scene_pos)
        if node is None:
            return False

        core = getattr(node, "_widget_core", None)
        if core is None:
            return False

        if core.activate_at(scene_pos):
            return True  # popup opened — event consumed

        proxy = core.get_proxy()
        if proxy is not None:
            proxy.setFocus(Qt.FocusReason.MouseFocusReason)

        if node.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            self._suppressed_movable_node = node

        return False  # let Qt route to the proxy

    def _ensure_node_selected(
        self, scene_pos: QPointF, event: QGraphicsSceneMouseEvent
    ) -> None:
        node = ItemResolver.resolve_node_at(self.canvas, scene_pos)
        if node is None or node.isSelected():
            return
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.canvas.clearSelection()
        node.setSelected(True)

    def _restore_suppressed_node(self) -> None:
        """Restore ItemIsMovable on a node we suppressed for widget focus."""
        node = self._suppressed_movable_node
        if node is not None:
            try:
                node.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True
                )
            except RuntimeError:
                pass  # node already deleted
            self._suppressed_movable_node = None

    # ==================================================================
    # MOUSE PRESS  (handler chain — Review §3)
    # ==================================================================

    def on_mouse_press(self, event: QGraphicsSceneMouseEvent) -> bool:
        """Delegate to the handler chain; fall through to drag setup."""

        # ── Run handler chain ─────────────────────────────────────────
        for handler in self._press_handlers:
            if handler.try_handle(event, self):
                return True

        # ── No handler consumed → clean up widget-editing mode ────────
        if self._suppressed_movable_node is not None:
            self._restore_suppressed_node()

        focus_item = self.canvas.focusItem()
        if isinstance(focus_item, QGraphicsProxyWidget):
            focus_item.clearFocus()

        # ── Prepare drag tracking ─────────────────────────────────────
        self._shake_recognizer.reset()
        self._is_dragging = False
        self._drag_started_pos = None
        self._last_mouse_pos = None

        if event.button() != Qt.MouseButton.LeftButton:
            return False

        self._is_dragging = True

        # Snapshot positions for undo
        self._drag_start_positions = {
            id(item): item.pos()
            for item in get_movable_nodes(self.canvas.selectedItems())
        }

        # Also capture the node directly under the cursor (it may not
        # be in selectedItems yet because Qt hasn't processed the press).
        item_under = ItemResolver.resolve_node_at(
            self.canvas, event.scenePos()
        )
        if (
            item_under is not None
            and id(item_under) not in self._drag_start_positions
            and (item_under.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            and not isinstance(item_under, (NodeTrace, DragTrace))
        ):
            self._drag_start_positions[id(item_under)] = item_under.pos()

        return False  # let Qt handle selection / drag initiation

    # ==================================================================
    # MOUSE MOVE  (performance-critical)
    # ==================================================================

    def on_mouse_move(self, event: QGraphicsSceneMouseEvent) -> bool:
        if self._suppressed_movable_node is not None:
            return False  # let Qt route move to the proxy widget

        if not (self._is_dragging and (event.buttons() & Qt.MouseButton.LeftButton)):
            self._drag_started_pos = None
            self._last_mouse_pos = None
            return False

        if not self._shake_enabled:
            return False

        curr = event.scenePos()

        if self._drag_started_pos is None:
            self._drag_started_pos = curr
            self._last_mouse_pos = curr
            self._shake_recognizer.reset()
            return False

        if (curr - self._drag_started_pos).manhattanLength() <= 3.0:
            return False

        if self._last_mouse_pos is not None:
            delta = curr - self._last_mouse_pos
            if self._shake_recognizer.update(delta):
                log.info("Shake gesture detected!")
                self._trigger_shake_disconnect()
                return True

        self._last_mouse_pos = curr
        return False

    # ==================================================================
    # MOUSE RELEASE
    # ==================================================================

    def on_mouse_release(self, event: QGraphicsSceneMouseEvent) -> bool:
        self._restore_suppressed_node()

        # ── Undo command for moved nodes ──────────────────────────────
        if self._is_dragging and self._drag_start_positions:
            from weave.canvas.undo_commands import MoveNodesCommand

            moves = {}
            for item in self.canvas.selectedItems():
                old_pos = self._drag_start_positions.get(id(item))
                uid = get_node_uid(item)
                if uid and old_pos is not None and item.pos() != old_pos:
                    moves[uid] = (old_pos, item.pos())
            if moves:
                self._push_cmd(MoveNodesCommand(moves))

        self._shake_recognizer.reset()
        self._is_dragging = False
        self._drag_started_pos = None
        self._last_mouse_pos = None
        self._drag_start_positions = {}
        return False

    # ==================================================================
    # DOUBLE CLICK
    # ==================================================================

    def on_mouse_double_click(self, event: QGraphicsSceneMouseEvent) -> bool:
        # Interactive widget fast-path
        if self._is_interactive_widget_click(event.scenePos()):
            self._ensure_node_selected(event.scenePos(), event)
            return self._yield_to_proxy(event.scenePos())

        # Title editing
        node = ItemResolver.resolve_node_at(self.canvas, event.scenePos())
        if node is not None and hasattr(node, "header") and hasattr(node.header, "_title"):
            local_title = node.header._title.mapFromParent(
                node.header.mapFromParent(node.mapFromScene(event.scenePos()))
            )
            if node.header._title.contains(local_title):
                node.header._title.unlock_interaction()
                event.accept()
                return True

        return self._handle_double_click_interactions(event)

    def _handle_double_click_interactions(
        self, event: QGraphicsSceneMouseEvent
    ) -> bool:
        """Clone (Ctrl+dbl-click) or clear port connections (dbl-click)."""
        # Clone
        if (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and event.button() == Qt.MouseButton.LeftButton
        ):
            target = ItemResolver.resolve_node_at(self.canvas, event.scenePos())
            if target is not None:
                self._execute_clone(target)
                return True

        # Port clear
        port = ItemResolver.resolve_port_at(self.canvas, event.scenePos())
        if port is not None and event.button() == Qt.MouseButton.LeftButton:
            self._clear_port_connections(port)
            return True

        return False

    def _execute_clone(self, target_node: QGraphicsItem) -> None:
        provider = getattr(self.canvas, "_context_menu_provider", None)
        if provider is None:
            return
        if target_node.isSelected():
            provider.cmd_duplicate_selected()
        else:
            provider.cmd_duplicate(target_node)

    def _clear_port_connections(self, port: NodePort) -> None:
        """Remove connections from *port* and push an undo command."""
        from weave.canvas.undo_commands import RemoveConnectionsCommand

        traces = list(port.connected_traces) if port.is_output else (
            [port.connected_traces[0]] if port.connected_traces else []
        )
        tuples = build_connection_tuples(traces)
        for trace in traces:
            ConnectionFactory.remove(trace)
        if tuples:
            self._push_cmd(RemoveConnectionsCommand(tuples))

    # ==================================================================
    # GRID SNAPPING
    # ==================================================================

    def apply_grid_snapping(self, event: QGraphicsSceneMouseEvent) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return

        movable = [
            item
            for item in self.canvas.selectedItems()
            if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        ]
        if not movable:
            return

        effective = self.canvas.snapping_enabled
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            effective = not effective

        if effective:
            self.canvas._orchestrator.snap_items_to_grid(
                movable, self.canvas.grid_spacing
            )
        else:
            for item in movable:
                self._update_node_traces(item)

    @staticmethod
    def _update_node_traces(node: QGraphicsItem) -> None:
        for port in getattr(node, "inputs", []) + getattr(node, "outputs", []):
            for trace in getattr(port, "connected_traces", []):
                trace.update_path()

    # ==================================================================
    # SHAKE DISCONNECT
    # ==================================================================

    def _trigger_shake_disconnect(self) -> None:
        from weave.canvas.undo_commands import RemoveConnectionsCommand

        # Resolve target nodes
        nodes: list[QGraphicsItem] = []
        if self._drag_started_pos is not None:
            under = ItemResolver.resolve_node_at(
                self.canvas, self._drag_started_pos
            )
            if under is not None and under in get_movable_nodes([under]):
                nodes = [under]
        if not nodes:
            nodes = get_movable_nodes(self.canvas.selectedItems())
        if not nodes:
            return

        # Collect unique traces
        traces: set = set()
        for node in nodes:
            for attr in ("inputs", "outputs"):
                for port in getattr(node, attr, []):
                    for trace in list(getattr(port, "connected_traces", [])):
                        traces.add(trace)

        tuples = build_connection_tuples(traces)

        removed = 0
        for trace in traces:
            ConnectionFactory.remove(trace)
            removed += 1

        if removed:
            log.info(f"Shake disconnected {removed} traces")
            if tuples:
                self._push_cmd(RemoveConnectionsCommand(tuples))

    # ==================================================================
    # KEYBOARD
    # ==================================================================

    def keyPressEvent(self, event: QKeyEvent) -> bool:
        mod = event.modifiers()
        key = event.key()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mod & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mod & Qt.KeyboardModifier.AltModifier)

        # ── Undo / Redo: always fire, regardless of widget focus ──────
        # Must come before the focus guard so that Ctrl+Z / Ctrl+Shift+Z
        # work even when a proxy widget or editable text item has focus.
        if ctrl and not alt and key == Qt.Key.Key_Z:
            self._cmd("cmd_redo" if shift else "cmd_undo")
            return True

        # ── Focus guard: yield all other keys to embedded widgets ─────
        if self._suppressed_movable_node is not None:
            return False
        focus = self.canvas.focusItem()
        if isinstance(focus, QGraphicsProxyWidget):
            return False
        if isinstance(focus, QGraphicsTextItem):
            if focus.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable:
                return False

        # Shortcuts — compound modifiers checked first to avoid shadowing
        _SHORTCUTS: list[tuple] = [
            (Qt.Key.Key_Delete, False, False, False, "cmd_delete_selected"),
            (Qt.Key.Key_Backspace, False, False, False, "cmd_disconnect_selected"),
            (Qt.Key.Key_D, True, False, False, "cmd_duplicate_selected"),
            (Qt.Key.Key_A, True, False, False, "cmd_select_all"),
            (Qt.Key.Key_S, True, True, False, "cmd_save_as"),
            (Qt.Key.Key_S, True, False, False, "cmd_save"),
            (Qt.Key.Key_N, True, False, False, "cmd_new"),
            (Qt.Key.Key_O, True, False, False, "cmd_open"),
            (Qt.Key.Key_C, True, True, False, "cmd_clear_canvas"),
        ]

        for sc_key, sc_ctrl, sc_shift, sc_alt, cmd_name in _SHORTCUTS:
            if key == sc_key and ctrl == sc_ctrl and shift == sc_shift and alt == sc_alt:
                self._cmd(cmd_name)
                return True

        # Recent files: Alt+1 … Alt+9
        if alt and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            self._cmd("cmd_open_recent_by_index", key - Qt.Key.Key_1)
            return True

        return False

    # ==================================================================
    # COMMAND HELPERS
    # ==================================================================

    def _cmd(self, method_name: str, *args) -> None:
        provider: Optional[CanvasCommandsMixin] = getattr(
            self.canvas, "_context_menu_provider", None
        )
        if provider is None:
            log.debug(f"_cmd({method_name}): no provider")
            return
        fn = getattr(provider, method_name, None)
        if fn is None:
            log.warning(f"_cmd: provider missing '{method_name}'")
            return
        fn(*args)

    def _push_cmd(self, cmd) -> None:
        provider = getattr(self.canvas, "_context_menu_provider", None)
        if provider is not None and hasattr(provider, "_undo_manager"):
            provider._undo_manager.push(cmd)
