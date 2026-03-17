# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

node_panel — Embeddable widget that mirrors a single node's WidgetCore
=======================================================================

``NodePanel`` is a plain ``QWidget`` that can be dropped into any layout
(dock sidebar, dialog, tab widget, …).  It reads a node's ``WidgetCore``
bindings on bind, creates lightweight *mirror* widgets, and keeps them
bidirectionally synchronised with the node for as long as the panel is
bound.

Sync paths
----------
User edits mirror widget
  → mirror signal fires
  → _on_mirror_changed()
  → widget_core.set_port_value(name, value)
  → node.compute()  (via existing signal chain)

Upstream data arrives / user edits node widget
  → widget_core.value_changed(port_name)
  → _on_node_value_changed(port_name)
  → update mirror widget  (signals blocked)
"""

from __future__ import annotations

from typing import (
    Any, Callable, Dict, Optional, TYPE_CHECKING,
)

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QScrollArea,
    QLabel, QFrame, QPushButton,
    QGraphicsItem,
)

if TYPE_CHECKING:
    from weave.node.node_core import Node
    from weave.widgetcore import WidgetBinding


from weave.panel.mirror_factories import (
    MirrorFactory,
    _DEFAULT_FACTORIES,
    _MIRROR_SIGNAL_MAP,
    get_custom_factory as _get_custom_factory,
    _CUSTOM_SIGNAL_MAP,
)
from weave.panel.panel_header import PanelHeader

from weave.logger import get_logger
log = get_logger("NodePanel")

# ---------------------------------------------------------------------------
# shiboken6 validity guard
# ---------------------------------------------------------------------------
# PySide6 emits a RuntimeWarning (not a RuntimeError) when you try to
# disconnect a signal on a C++ object that has already been destroyed.
# Using shiboken6.isValid() lets us skip the disconnect entirely in that
# situation rather than relying on catching the warning after the fact.
try:
    from shiboken6 import isValid as _cpp_is_valid
except ImportError:  # pragma: no cover
    def _cpp_is_valid(obj) -> bool:  # type: ignore[misc]
        """Fallback when shiboken6 is not importable; assume valid."""
        return True


# ══════════════════════════════════════════════════════════════════════════════
# NodePanel
# ══════════════════════════════════════════════════════════════════════════════

class NodePanel(QWidget):
    """
    A plain ``QWidget`` that mirrors the widgets of a single node's
    ``WidgetCore`` into standard Qt widgets suitable for embedding in
    dock panels, sidebars, dialogs, or tab widgets.

    Supports two binding modes:

    ``bind_node(node, static=False)``
        *Dynamic* — the panel can be re-bound to another node at any
        time (e.g. by the selection-following logic in
        ``NodeDockAdapter``).

    ``bind_node(node, static=True)``
        *Static* — the panel is permanently locked to this node.
        If the node is destroyed the panel emits ``linked_node_lost``
        so the parent can close/remove it.

    Signals
    -------
    node_bound(object)
        Emitted when a new node is bound (argument is the node).
    node_unbound()
        Emitted when the current node is unbound (manually or via
        selection change).
    linked_node_lost()
        Emitted **only in static mode** when the pinned node is
        destroyed.  The parent dock/container should close in response.
    """

    node_bound = Signal(object)
    node_unbound = Signal()
    linked_node_lost = Signal()
    pin_changed = Signal(bool)
    # Emitted in **static** mode when the node's title changes so the
    # parent ``NodeDockAdapter`` can update the dock's title bar.
    dock_title_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._node: Optional["Node"] = None
        self._static: bool = False
        self._pinned: bool = False
        # True only while _watch_node_lifetime() connections are live.
        # Guards all _unwatch_node_lifetime() disconnect calls so we never
        # attempt to disconnect signals that were never connected (dynamic
        # bindings never call _watch_node_lifetime, so the slots don't
        # exist — trying to disconnect them produces a RuntimeWarning).
        self._watching_lifetime: bool = False
        self._mirrors: Dict[str, QWidget] = {}
        self._mirror_slots: Dict[str, Callable] = {}
        self._custom_factories: Dict[type, MirrorFactory] = {}

        # ── Layout ───────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._header = PanelHeader(self)
        self._header.pin_toggled.connect(self._on_pin_toggled)
        root.addWidget(self._header)

        # Separator
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # Scrollable body
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, stretch=1)

        self._body = QWidget()
        self._form = QFormLayout(self._body)
        self._form.setContentsMargins(8, 8, 8, 8)
        self._form.setSpacing(6)
        self._scroll.setWidget(self._body)

        # Placeholder
        self._placeholder = QLabel("No node selected")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setEnabled(False)
        self._form.addRow(self._placeholder)

    # ──────────────────────────────────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────────────────────────────────

    @property
    def node(self) -> Optional["Node"]:
        """The currently bound node, or ``None``."""
        return self._node

    @property
    def is_static(self) -> bool:
        """``True`` if the panel is statically pinned to a node."""
        return self._static

    @property
    def is_pinned(self) -> bool:
        """``True`` if the panel is perma-linked to its current node.

        A pinned panel ignores canvas selection changes until the user
        unpins it or the node is deleted.  Only meaningful for dynamic
        panels — static panels are inherently pinned.
        """
        return self._pinned

    # ──────────────────────────────────────────────────────────────────────
    # Custom factory registration
    # ──────────────────────────────────────────────────────────────────────

    def register_mirror_factory(
        self, widget_type: type, factory: MirrorFactory
    ) -> None:
        """
        Register a custom mirror-widget factory for *widget_type*.

        The factory signature is ``(original_widget, binding) -> QWidget``.
        It will be preferred over the built-in cloners for exact type
        matches (``isinstance`` is **not** used — register the concrete
        class, not a base class).
        """
        self._custom_factories[widget_type] = factory

    # ──────────────────────────────────────────────────────────────────────
    # Pin toggle (dynamic panels only)
    # ──────────────────────────────────────────────────────────────────────

    @Slot(bool)
    def _on_pin_toggled(self, pinned: bool) -> None:
        """Handle the header pin button toggle.

        When *pinned* is True the panel is perma-linked to its current
        node: canvas selection changes are ignored, and the node's
        lifetime is watched so the panel cleans up if the node is
        deleted.  When *pinned* is False normal dynamic behaviour
        resumes.
        """
        if self._node is None or self._static:
            return

        self._pinned = pinned

        if pinned:
            # Start watching the node so deletion unpins automatically.
            if not self._watching_lifetime:
                self._watch_node_lifetime(self._node)
        else:
            # Stop watching — _unbind_internal or a future bind_node
            # will handle cleanup.
            self._unwatch_node_lifetime(self._node)

        self.pin_changed.emit(pinned)

    # ──────────────────────────────────────────────────────────────────────
    # Bind / Unbind
    # ──────────────────────────────────────────────────────────────────────

    def bind_node(self, node: "Node", *, static: bool = False) -> None:
        """
        Bind to *node* — populate the panel with mirror widgets.

        Parameters
        ----------
        node : Node
            The node whose ``_widget_core`` should be mirrored.
        static : bool
            If ``True`` the panel is permanently pinned to this node.
            The panel will emit ``linked_node_lost`` and refuse to
            rebind to a different node if the user calls ``bind_node``
            again.
        """
        if self._node is node:
            return

        # Static and pinned panels refuse to be rebound to a different node.
        if (self._static or self._pinned) and self._node is not None:
            log.debug(
                "Panel is locked (static=%s, pinned=%s) — ignoring "
                "bind_node() for '%s'.",
                self._static, self._pinned, self._node_title(node),
            )
            return

        if self._node is not None:
            self._unbind_internal()

        wc = getattr(node, "_widget_core", None)
        if wc is None:
            log.warning(
                f"Node '{self._node_title(node)}' has no _widget_core; "
                f"panel will be empty."
            )

        self._node = node
        self._static = static
        self._header.set_static_mode(static)

        title = self._node_title(node)
        if not static:
            # Dynamic panels show the node name in the header because the
            # dock title bar has a generic label (e.g. "Inspector").
            self._header.set_title(title)
        else:
            # Static panels use the dock title bar for the node name, so
            # the header title row is hidden.  Emit a signal so that the
            # parent dock adapter can update its QDockWidget title.
            self._header.set_title("")
            self.dock_title_changed.emit(title)

        self._build_mirrors()

        # Show the current node state in the header badge.
        state = getattr(node, "_state", None)
        if state is not None:
            name = state.name if hasattr(state, "name") else str(state)
            self._header.set_state_text(name)

        # Pin button: visible for dynamic panels, hidden for static.
        self._header.set_pin_visible(not static)
        self._header.set_pin_checked(False)
        self._pinned = False

        # Listen for value changes coming FROM the node.
        # value_changed fires on user edits (not suppressed).
        # port_value_written fires on every programmatic write via
        # set_port_value (upstream data, on_evaluate_finished, etc.)
        # which is suppressed for value_changed.  Between the two,
        # the mirror sees every change regardless of source.
        if wc is not None:
            wc.value_changed.connect(self._on_node_value_changed)
            wc.port_value_written.connect(self._on_node_value_changed)
            wc.port_enabled_changed.connect(self._on_port_enabled_changed)

        # Listen for node state changes to update the header badge.
        if hasattr(node, "state_changed"):
            try:
                node.state_changed.connect(self._on_node_state_changed)
            except (RuntimeError, TypeError):
                pass

        # Listen for title edits on the node's EditableTitle.
        if hasattr(node, "title_changed"):
            try:
                node.title_changed.connect(self._on_node_title_changed)
            except (RuntimeError, TypeError):
                pass

        # In static mode, watch for node destruction so we can auto-close.
        if static:
            self._watch_node_lifetime(node)

        self.node_bound.emit(node)

    def unbind(self) -> None:
        """
        Public unbind — disconnect from the currently bound node and
        clear all mirrors.

        In **static** mode this also releases the static lock, so the
        panel can be rebound or closed.
        """
        self._static = False
        self._unbind_internal()

    def _unbind_internal(self) -> None:
        """
        Core unbind logic shared by ``unbind()`` and internal callers.
        Does **not** clear ``_static`` — callers decide.
        """
        if self._node is None:
            return

        node = self._node
        wc = getattr(node, "_widget_core", None)

        # Disconnect WidgetCore signals
        if wc is not None:
            try:
                wc.value_changed.disconnect(self._on_node_value_changed)
            except (RuntimeError, TypeError):
                pass
            try:
                wc.port_value_written.disconnect(self._on_node_value_changed)
            except (RuntimeError, TypeError):
                pass
            try:
                wc.port_enabled_changed.disconnect(self._on_port_enabled_changed)
            except (RuntimeError, TypeError):
                pass

        # Disconnect state_changed
        if hasattr(node, "state_changed"):
            try:
                node.state_changed.disconnect(self._on_node_state_changed)
            except (RuntimeError, TypeError):
                pass

        # Disconnect title_changed
        if hasattr(node, "title_changed"):
            try:
                node.title_changed.disconnect(self._on_node_title_changed)
            except (RuntimeError, TypeError):
                pass

        # Stop watching lifetime
        self._unwatch_node_lifetime(node)

        self._tear_down_mirrors()
        self._node = None
        self._pinned = False
        self._header.set_title("")
        self._header.set_state_text("")
        self._header.set_pin_visible(False)
        self._header.set_pin_checked(False)

        # Re-add placeholder
        self._placeholder.setParent(self._body)
        self._form.addRow(self._placeholder)
        self._placeholder.show()

        self.node_unbound.emit()

    # ──────────────────────────────────────────────────────────────────────
    # Node lifetime watching (for static mode)
    # ──────────────────────────────────────────────────────────────────────

    def _watch_node_lifetime(self, node: "Node") -> None:
        """
        Connect to every available destruction signal so we learn about
        node removal regardless of how it happens (scene removeItem,
        ``del``, C++ destructor, …).

        Two independent hooks are used:

        1. ``QObject.destroyed`` — fires when the C++ side is deleted.
           This is the most reliable because it works regardless of
           whether the deletion came from Python ``del``, C++ parent
           cleanup, or ``QGraphicsScene.removeItem()``.

        2. ``Canvas.node_removed(QGraphicsItem)`` — fires when
           ``NodeManager.remove_node()`` is used.  This catches
           explicit graph-level removals (Delete key, context menu)
           that may happen *before* the C++ destructor runs.
        """
        # QObject.destroyed — reliable for any QObject-derived node.
        try:
            node.destroyed.connect(self._on_linked_node_destroyed)
        except (RuntimeError, TypeError):
            pass

        # Canvas.node_removed — emitted by some deletion paths.
        scene = None
        try:
            scene = node.scene()
        except RuntimeError:
            pass
        if scene is not None and hasattr(scene, "node_removed"):
            try:
                scene.node_removed.connect(self._on_scene_node_removed)
            except (RuntimeError, TypeError):
                pass

        # Mark that lifetime connections are live so _unwatch_node_lifetime
        # knows it is safe (and necessary) to disconnect them.
        self._watching_lifetime = True

    def _unwatch_node_lifetime(self, node: "Node") -> None:
        """Disconnect all lifetime signals for *node*.

        This method is a no-op when ``_watching_lifetime`` is ``False`` —
        i.e. when the panel was bound in **dynamic** mode and
        ``_watch_node_lifetime`` was never called.  Attempting to
        disconnect slots that were never connected causes PySide6 to emit
        a ``RuntimeWarning`` (not a ``RuntimeError``), which bypasses the
        usual ``except (RuntimeError, TypeError)`` guard.  The flag is the
        authoritative gate.

        ``_cpp_is_valid`` is checked as a secondary guard for the case
        where the C++ object was already destroyed before we got here,
        which would also produce a ``RuntimeWarning`` on disconnect.
        """
        if not self._watching_lifetime:
            return
        self._watching_lifetime = False

        # Only attempt to disconnect the destroyed signal when the C++ object
        # is still alive.  If it is already gone Qt has already cleaned up the
        # connection on its side, so there is nothing left to disconnect.
        if _cpp_is_valid(node):
            try:
                node.destroyed.disconnect(self._on_linked_node_destroyed)
            except (RuntimeError, TypeError):
                pass

        scene = None
        if _cpp_is_valid(node):
            try:
                scene = node.scene()
            except RuntimeError:
                pass
        if scene is not None and hasattr(scene, "node_removed"):
            try:
                scene.node_removed.disconnect(self._on_scene_node_removed)
            except (RuntimeError, TypeError):
                pass

    @Slot()
    def _on_linked_node_destroyed(self) -> None:
        """The pinned node's C++ side is being destroyed.

        We are called from inside the ``destroyed`` signal emission, which
        means we must **not** try to disconnect from ``destroyed`` ourselves —
        Qt is already tearing down that connection.  We *can* still reach the
        scene to clean up the ``node_removed`` connection, because the scene
        outlives its items.
        """
        log.debug("Static panel: linked node destroyed.")

        # Clear the flag first so that any later path through
        # _unwatch_node_lifetime is a safe no-op.  We handle the scene
        # disconnect inline below; the destroyed disconnect is handled by
        # Qt itself as part of the signal emission teardown.
        watching = self._watching_lifetime
        self._watching_lifetime = False

        if watching:
            # Disconnect the scene-level signal now, while we can still ask
            # the (partially-alive) node for its scene.  This prevents a
            # dangling connection that would produce a RuntimeWarning later.
            node = self._node
            if node is not None and _cpp_is_valid(node):
                scene = None
                try:
                    scene = node.scene()
                except RuntimeError:
                    pass
                if scene is not None and hasattr(scene, "node_removed"):
                    try:
                        scene.node_removed.disconnect(self._on_scene_node_removed)
                    except (RuntimeError, TypeError):
                        pass

        # Node is already gone — null out our reference directly without
        # trying to call methods on the dead object.
        self._tear_down_mirrors()
        self._node = None
        self._static = False
        self._pinned = False
        self._header.set_title("[deleted]")
        self._header.set_state_text("")
        self._header.set_pin_visible(False)
        self._header.set_pin_checked(False)

        self._placeholder.setParent(self._body)
        self._form.addRow(self._placeholder)
        self._placeholder.show()

        self.linked_node_lost.emit()

    @Slot(QGraphicsItem)
    def _on_scene_node_removed(self, item: QGraphicsItem) -> None:
        """Canvas emitted ``node_removed`` — check if it is our node."""
        if item is self._node:
            self._on_linked_node_destroyed()

    # ──────────────────────────────────────────────────────────────────────
    # Mirror construction / teardown
    # ──────────────────────────────────────────────────────────────────────

    def _build_mirrors(self) -> None:
        """Create mirror widgets for every binding in the node's WidgetCore."""
        wc = getattr(self._node, "_widget_core", None)
        if wc is None:
            return

        # Hide placeholder
        self._placeholder.hide()
        self._placeholder.setParent(None)

        bindings = wc.bindings()  # returns a copy
        for port_name, binding in bindings.items():
            mirror = self._create_mirror(binding)
            if mirror is None:
                continue

            self._mirrors[port_name] = mirror

            # Sync the initial enabled state from the source widget so
            # mirrors for auto-disabled ports start out greyed.
            mirror.setEnabled(binding.widget.isEnabled())

            label_text = port_name.replace("_", " ").title()
            self._form.addRow(f"{label_text}:", mirror)

            # Connect mirror → node
            self._connect_mirror_signal(port_name, mirror, binding)

    def _tear_down_mirrors(self) -> None:
        """Remove all mirror widgets and disconnect their signals."""
        for port_name in list(self._mirror_slots):
            mirror = self._mirrors.get(port_name)
            if mirror is not None:
                self._disconnect_mirror_signal(port_name, mirror)

        self._mirror_slots.clear()

        for mirror in self._mirrors.values():
            try:
                mirror.setParent(None)
                mirror.deleteLater()
            except RuntimeError:
                pass

        self._mirrors.clear()

        # Clear the form layout
        while self._form.count():
            item = self._form.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._placeholder:
                w.setParent(None)
                w.deleteLater()

    def _create_mirror(self, binding: "WidgetBinding") -> Optional[QWidget]:
        """
        Create a mirror widget for *binding*.

        Resolution order:
        1. Custom factory registered on *this panel* via
           ``register_mirror_factory()`` (exact type match).
        2. Global custom factory registered via
           ``mirror_factories.register_mirror_factory()`` (exact type).
        3. Built-in factory list (``isinstance`` check, subclass-first).
        4. ``None`` if no factory can handle the type.
        """
        original = binding.widget
        orig_type = type(original)

        # 1. Panel-local custom factory (exact match)
        factory = self._custom_factories.get(orig_type)
        if factory is not None:
            try:
                return factory(original, binding)
            except Exception as exc:
                log.warning(
                    f"Custom mirror factory for {orig_type.__name__} failed: {exc}"
                )

        # 2. Global custom factory (exact match)
        global_factory = _get_custom_factory(orig_type)
        if global_factory is not None:
            try:
                return global_factory(original, binding)
            except Exception as exc:
                log.warning(
                    f"Global mirror factory for {orig_type.__name__} failed: {exc}"
                )

        # 3. Built-in factories
        for cls, factory_fn in _DEFAULT_FACTORIES:
            if isinstance(original, cls):
                try:
                    return factory_fn(original, binding)
                except Exception as exc:
                    log.warning(
                        f"Built-in mirror factory for {cls.__name__} failed: {exc}"
                    )
                    return None

        log.debug(
            f"No mirror factory for widget type {orig_type.__name__} "
            f"(port '{binding.port_name}'). Skipping."
        )
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Bidirectional sync
    # ──────────────────────────────────────────────────────────────────────

    def _connect_mirror_signal(
        self, port_name: str, mirror: QWidget, binding: "WidgetBinding"
    ) -> None:
        """Connect the mirror widget's change signal → push value to node."""
        sig_name: Optional[str] = None

        # 1. Built-in signal map
        for cls, name in _MIRROR_SIGNAL_MAP.items():
            if isinstance(mirror, cls):
                sig_name = name
                break

        # 2. Global custom signal map (exact type)
        if sig_name is None:
            sig_name = _CUSTOM_SIGNAL_MAP.get(type(mirror))

        # QPushButton.clicked → forward to the original button
        if sig_name is None and isinstance(mirror, QPushButton):
            original = binding.widget

            def _forward_click(*_args, _orig=original):
                try:
                    _orig.click()
                except RuntimeError:
                    pass

            mirror.clicked.connect(_forward_click)
            self._mirror_slots[port_name] = _forward_click
            return

        if sig_name is None:
            return

        sig = getattr(mirror, sig_name, None)
        if sig is None:
            return

        wc = getattr(self._node, "_widget_core", None)
        if wc is None:
            return

        def _on_mirror_edited(*_args, _pn=port_name, _m=mirror, _wc=wc):
            from weave.widgetcore import WidgetCore
            value = WidgetCore._generic_get(_m)
            # Push the value into the node's widget (signals blocked so the
            # widget's own change signal doesn't fire).
            _wc.set_port_value(_pn, value)
            # Explicitly notify the node that the value changed.
            # set_port_value suppresses the widget's change signal to avoid
            # feedback loops, but this means the node's compute() pipeline
            # never triggers.  Emitting value_changed here closes the gap:
            # the node sees the change, reads the (already updated) widget,
            # and runs compute().
            # This also triggers _on_node_value_changed on this panel, which
            # writes the same value back into the mirror with signals blocked
            # — no infinite loop.
            _wc.value_changed.emit(_pn)

        sig.connect(_on_mirror_edited)
        self._mirror_slots[port_name] = _on_mirror_edited

    def _disconnect_mirror_signal(self, port_name: str, mirror: QWidget) -> None:
        """Disconnect a mirror widget's change signal."""
        slot = self._mirror_slots.get(port_name)
        if slot is None:
            return

        # Try built-in signal map first.
        for cls, sig_name in _MIRROR_SIGNAL_MAP.items():
            if isinstance(mirror, cls):
                sig = getattr(mirror, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
                return

        # Try global custom signal map (exact type).
        custom_sig_name = _CUSTOM_SIGNAL_MAP.get(type(mirror))
        if custom_sig_name is not None:
            sig = getattr(mirror, custom_sig_name, None)
            if sig is not None:
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            return

        if isinstance(mirror, QPushButton):
            try:
                mirror.clicked.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    @Slot(str)
    def _on_node_value_changed(self, port_name: str) -> None:
        """A value changed inside the node — update the corresponding mirror."""
        mirror = self._mirrors.get(port_name)
        if mirror is None:
            return

        wc = getattr(self._node, "_widget_core", None)
        if wc is None:
            return

        value = wc.get_port_value(port_name)
        self._set_mirror_value(mirror, value)

    @staticmethod
    def _set_mirror_value(mirror: QWidget, value: Any) -> None:
        """Write *value* into *mirror* with signals blocked."""
        from weave.widgetcore import WidgetCore
        was_blocked = mirror.signalsBlocked()
        mirror.blockSignals(True)
        try:
            WidgetCore._generic_set(mirror, value)
        except Exception as exc:
            log.debug(f"Failed to set mirror value: {exc}")
        finally:
            mirror.blockSignals(was_blocked)

    @Slot(str, bool)
    def _on_port_enabled_changed(self, port_name: str, enabled: bool) -> None:
        """A widget inside the node was enabled/disabled — sync the mirror."""
        mirror = self._mirrors.get(port_name)
        if mirror is not None:
            mirror.setEnabled(enabled)

    # ──────────────────────────────────────────────────────────────────────
    # Node state badge
    # ──────────────────────────────────────────────────────────────────────

    @Slot(object, object)
    def _on_node_state_changed(self, _old_state, new_state) -> None:
        name = new_state.name if hasattr(new_state, "name") else str(new_state)
        self._header.set_state_text(name)

    # ──────────────────────────────────────────────────────────────────────
    # Title sync
    # ──────────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_node_title_changed(self, new_title: str) -> None:
        """The node's EditableTitle was edited — update accordingly.

        Dynamic panels update the header label.  Static panels emit
        ``dock_title_changed`` so the parent ``NodeDockAdapter`` can
        update the ``QDockWidget`` title bar.
        """
        if self._static:
            self.dock_title_changed.emit(new_title)
        else:
            self._header.set_title(new_title)

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _node_title(node: "Node") -> str:
        """Best-effort extraction of a readable node title."""
        try:
            tip = node.header._title.toolTip()
            if tip:
                return tip
            return node.header._title.toPlainText()
        except Exception:
            pass
        name = getattr(node, "name", None)
        if name:
            return str(name)
        return type(node).__name__
