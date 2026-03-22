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

Label mirroring
---------------
When building mirrors, the panel walks the source ``QFormLayout`` row
by row.  For each registered widget it reads the *actual* label from
``labelForField()`` (e.g. ``"Fill:"``, ``"Dim 0:"``).  Unregistered
decorative elements — section-header ``QLabel`` widgets like
``"── Axis 0 ──"`` and ``QFrame`` HLine separators — are cloned
into the panel at their correct position so the visual structure
matches the node body exactly.  Labels are never fabricated from
port names.

Visibility mirroring
--------------------
``WidgetCore`` emits ``widget_visibility_changed(port_name, visible)``
whenever a registered widget is directly shown or hidden (e.g.
``NumpyArrayNode._sync_fill_value_visibility``).  The panel hides or
shows the entire mirror row (label + widget) in response.  The initial
visibility is synced at bind time so widgets that start hidden (e.g. the
*Value* spinbox when Fill ≠ Full) appear correctly from the first frame.

Parent-propagated visibility changes (node body collapsed on the canvas)
are intentionally ignored — the dock panel stays fully visible.

Dynamic widget support
----------------------
When a node dynamically registers or unregisters widgets in its
``WidgetCore`` (e.g. ``MultiFloatOutputNode``, ``NumpyArrayNode``
adding/removing dim spinboxes), the panel reacts automatically via the
``widget_registered`` / ``widget_unregistered`` signals.  No full
rebuild is needed.

Sync paths
----------
User edits mirror widget
  → mirror signal fires
  → _on_mirror_edited()
  → widget_core.set_port_value(name, value)
  → widget_core.value_changed.emit(name)
  → node.compute()  (via existing signal chain)

Upstream data arrives / user edits node widget
  → widget_core.value_changed(port_name)  OR  port_value_written(port_name)
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
    from weave.widgetcore.widgetcore_port_models import WidgetBinding

from weave.widgetcore.widgetcore_adapter import generic_get, generic_set

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
try:
    from shiboken6 import isValid as _cpp_is_valid
except ImportError:  # pragma: no cover
    def _cpp_is_valid(obj) -> bool:  # type: ignore[misc]
        """Fallback when shiboken6 is not importable; assume valid."""
        return True


# Sentinel used to distinguish "caller did not pass label_text" from
# "caller explicitly passed None" in _add_mirror_for_binding.
_UNSET = object()


# ══════════════════════════════════════════════════════════════════════════════
# NodePanel
# ══════════════════════════════════════════════════════════════════════════════

class NodePanel(QWidget):
    """
    A plain ``QWidget`` that mirrors the widgets of a single node's
    ``WidgetCore`` into standard Qt widgets suitable for embedding in
    dock panels, sidebars, dialogs, or tab widgets.

    Both **inspector** (dynamic, follows canvas selection) and **mirror**
    (static, bound to one node) panels are fully **bidirectional** — edits
    in the panel push values to the node, and node changes update the panel.

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
    dock_title_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._node: Optional["Node"] = None
        self._static: bool = False
        self._pinned: bool = False
        self._watching_lifetime: bool = False
        self._mirrors: Dict[str, QWidget] = {}
        self._mirror_labels: Dict[str, QLabel] = {}
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
        return self._node

    @property
    def is_static(self) -> bool:
        return self._static

    @property
    def is_pinned(self) -> bool:
        return self._pinned

    # ──────────────────────────────────────────────────────────────────────
    # Custom factory registration
    # ──────────────────────────────────────────────────────────────────────

    def register_mirror_factory(
        self, widget_type: type, factory: MirrorFactory
    ) -> None:
        """Register a per-panel custom mirror-widget factory."""
        self._custom_factories[widget_type] = factory

    # ──────────────────────────────────────────────────────────────────────
    # Pin toggle (dynamic panels only)
    # ──────────────────────────────────────────────────────────────────────

    @Slot(bool)
    def _on_pin_toggled(self, pinned: bool) -> None:
        if self._node is None or self._static:
            return

        self._pinned = pinned

        if pinned:
            if not self._watching_lifetime:
                self._watch_node_lifetime(self._node)
        else:
            self._unwatch_node_lifetime(self._node)

        self.pin_changed.emit(pinned)

    # ──────────────────────────────────────────────────────────────────────
    # Bind / Unbind
    # ──────────────────────────────────────────────────────────────────────

    def bind_node(self, node: "Node", *, static: bool = False) -> None:
        """Bind to *node* — populate the panel with mirror widgets."""
        if self._node is node:
            return

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
            self._header.set_title(title)
        else:
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

        # Connect WidgetCore signals.
        if wc is not None:
            wc.value_changed.connect(self._on_node_value_changed)
            wc.port_value_written.connect(self._on_node_value_changed)
            wc.port_enabled_changed.connect(self._on_port_enabled_changed)
            wc.widget_registered.connect(self._on_widget_registered)
            wc.widget_unregistered.connect(self._on_widget_unregistered)
            wc.widget_visibility_changed.connect(
                self._on_widget_visibility_changed
            )

        # Node state changes → header badge.
        if hasattr(node, "state_changed"):
            try:
                node.state_changed.connect(self._on_node_state_changed)
            except (RuntimeError, TypeError):
                pass

        # Title edits.
        if hasattr(node, "title_changed"):
            try:
                node.title_changed.connect(self._on_node_title_changed)
            except (RuntimeError, TypeError):
                pass

        # In static mode, watch for node destruction.
        if static:
            self._watch_node_lifetime(node)

        self.node_bound.emit(node)

    def unbind(self) -> None:
        """Public unbind — releases the static lock too."""
        self._static = False
        self._unbind_internal()

    def _unbind_internal(self) -> None:
        """Core unbind logic."""
        if self._node is None:
            return

        node = self._node
        wc = getattr(node, "_widget_core", None)

        # Disconnect WidgetCore signals.
        if wc is not None:
            for sig_name, slot in [
                ("value_changed", self._on_node_value_changed),
                ("port_value_written", self._on_node_value_changed),
                ("port_enabled_changed", self._on_port_enabled_changed),
                ("widget_registered", self._on_widget_registered),
                ("widget_unregistered", self._on_widget_unregistered),
                ("widget_visibility_changed",
                 self._on_widget_visibility_changed),
            ]:
                try:
                    getattr(wc, sig_name).disconnect(slot)
                except (RuntimeError, TypeError):
                    pass

        if hasattr(node, "state_changed"):
            try:
                node.state_changed.disconnect(self._on_node_state_changed)
            except (RuntimeError, TypeError):
                pass

        if hasattr(node, "title_changed"):
            try:
                node.title_changed.disconnect(self._on_node_title_changed)
            except (RuntimeError, TypeError):
                pass

        self._unwatch_node_lifetime(node)

        self._tear_down_mirrors()
        self._node = None
        self._pinned = False
        self._header.set_title("")
        self._header.set_state_text("")
        self._header.set_pin_visible(False)
        self._header.set_pin_checked(False)

        self._placeholder.setParent(self._body)
        self._form.addRow(self._placeholder)
        self._placeholder.show()

        self.node_unbound.emit()

    # ──────────────────────────────────────────────────────────────────────
    # Node lifetime watching (for static mode)
    # ──────────────────────────────────────────────────────────────────────

    def _watch_node_lifetime(self, node: "Node") -> None:
        try:
            node.destroyed.connect(self._on_linked_node_destroyed)
        except (RuntimeError, TypeError):
            pass

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

        self._watching_lifetime = True

    def _unwatch_node_lifetime(self, node: "Node") -> None:
        if not self._watching_lifetime:
            return
        self._watching_lifetime = False

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
        """The watched node's C++ side is being destroyed.

        Behaviour depends on how the panel was bound:

        **Static (mirror) panel** — the panel's sole reason for existing
        is gone.  Emit ``linked_node_lost`` so the parent dock closes.

        **Dynamic (inspector) panel, pinned** — the pinned target is
        gone, but the inspector dock should stay open.  Clear the
        mirrors, reset to the "No node selected" state, and let the
        dock resume following canvas selection.  ``node_unbound`` is
        emitted (not ``linked_node_lost``) so the dock does *not* close.
        """
        log.debug(
            "Panel: linked node destroyed (static=%s, pinned=%s).",
            self._static, self._pinned,
        )

        # Snapshot before we clear state.
        was_static = self._static

        # ── Scene-level signal cleanup ────────────────────────────────
        watching = self._watching_lifetime
        self._watching_lifetime = False

        if watching:
            node = self._node
            if node is not None and _cpp_is_valid(node):
                scene = None
                try:
                    scene = node.scene()
                except RuntimeError:
                    pass
                if scene is not None and hasattr(scene, "node_removed"):
                    try:
                        scene.node_removed.disconnect(
                            self._on_scene_node_removed
                        )
                    except (RuntimeError, TypeError):
                        pass

        # ── WidgetCore signal cleanup ─────────────────────────────────
        # The node is being destroyed so we must disconnect our slots
        # from its WidgetCore *now*, before the C++ object is gone.
        node = self._node
        wc = getattr(node, "_widget_core", None) if node is not None else None
        if wc is not None and _cpp_is_valid(node):
            for sig_name, slot in [
                ("value_changed", self._on_node_value_changed),
                ("port_value_written", self._on_node_value_changed),
                ("port_enabled_changed", self._on_port_enabled_changed),
                ("widget_registered", self._on_widget_registered),
                ("widget_unregistered", self._on_widget_unregistered),
                ("widget_visibility_changed",
                 self._on_widget_visibility_changed),
            ]:
                try:
                    getattr(wc, sig_name).disconnect(slot)
                except (RuntimeError, TypeError):
                    pass

        if node is not None and _cpp_is_valid(node):
            if hasattr(node, "state_changed"):
                try:
                    node.state_changed.disconnect(self._on_node_state_changed)
                except (RuntimeError, TypeError):
                    pass
            if hasattr(node, "title_changed"):
                try:
                    node.title_changed.disconnect(self._on_node_title_changed)
                except (RuntimeError, TypeError):
                    pass

        # ── Tear down UI ──────────────────────────────────────────────
        self._tear_down_mirrors()
        self._node = None
        self._static = False
        self._pinned = False
        self._header.set_pin_visible(False)
        self._header.set_pin_checked(False)

        self._placeholder.setParent(self._body)
        self._form.addRow(self._placeholder)
        self._placeholder.show()

        if was_static:
            # ── Mirror panel: the dock should close ───────────────────
            self._header.set_title("[deleted]")
            self._header.set_state_text("")
            self.linked_node_lost.emit()
        else:
            # ── Inspector panel (was pinned): stay open, resume ───────
            self._header.set_title("")
            self._header.set_state_text("")
            self.node_unbound.emit()

    @Slot(QGraphicsItem)
    def _on_scene_node_removed(self, item: QGraphicsItem) -> None:
        if item is self._node:
            self._on_linked_node_destroyed()

    # ──────────────────────────────────────────────────────────────────────
    # Mirror construction / teardown
    # ──────────────────────────────────────────────────────────────────────

    def _build_mirrors(self) -> None:
        """Create mirror widgets by walking the source form row-by-row.

        Instead of iterating ``wc.bindings()`` (which loses decorative
        elements), we walk every row of the source ``QFormLayout`` and:

        - **Registered widget** in FieldRole → create a mirror clone,
          copy the LabelRole text if any, wire bidirectional sync.
        - **Spanning QLabel** (section header, e.g. "── Axis 0 ──") →
          clone it into the panel as a spanning row.
        - **Spanning QFrame** separator → clone it.
        - **Unregistered widget** → skip.

        This preserves the exact visual structure of the node body —
        section headers, separators, and labelled widgets all appear in
        the panel in the same order as in the node.

        If the source layout is not a ``QFormLayout`` (unusual), we fall
        back to iterating bindings in dict order.
        """
        wc = getattr(self._node, "_widget_core", None)
        if wc is None:
            return

        # Hide placeholder
        self._placeholder.hide()
        self._placeholder.setParent(None)

        src_layout = wc.layout()
        if not isinstance(src_layout, QFormLayout):
            # Fallback: iterate bindings in dict order (no decorative rows).
            for port_name, binding in wc.bindings().items():
                self._add_mirror_for_binding(port_name, binding)
            return

        # Build reverse map: id(source_widget) → port_name.
        bindings = wc.bindings()
        widget_to_port = {id(b.widget): pn for pn, b in bindings.items()}

        for row in range(src_layout.rowCount()):
            span_item = src_layout.itemAt(
                row, QFormLayout.ItemRole.SpanningRole,
            )
            lbl_item = src_layout.itemAt(
                row, QFormLayout.ItemRole.LabelRole,
            )
            fld_item = src_layout.itemAt(
                row, QFormLayout.ItemRole.FieldRole,
            )

            # ── Spanning row ──────────────────────────────────────────
            if span_item is not None:
                sw = span_item.widget()
                if sw is None:
                    continue

                port_name = widget_to_port.get(id(sw))
                if port_name is not None:
                    # Registered spanning widget → mirror without label.
                    binding = bindings.get(port_name)
                    if binding is not None:
                        self._add_mirror_for_binding(port_name, binding)
                    continue

                # Unregistered spanning QLabel → clone as section header.
                if isinstance(sw, QLabel):
                    clone = QLabel(sw.text())
                    clone.setAlignment(sw.alignment())
                    clone.setWordWrap(sw.wordWrap())
                    self._form.addRow(clone)
                    continue

                # Unregistered spanning QFrame separator → clone.
                if isinstance(sw, QFrame) and sw.frameShape() in (
                    QFrame.Shape.HLine, QFrame.Shape.VLine,
                ):
                    sep = QFrame()
                    sep.setFrameShape(sw.frameShape())
                    sep.setFrameShadow(sw.frameShadow())
                    self._form.addRow(sep)
                    continue

                # Other unregistered spanning widgets — skip.
                continue

            # ── Label + Field row ─────────────────────────────────────
            fld_w = fld_item.widget() if fld_item is not None else None
            if fld_w is None:
                continue

            port_name = widget_to_port.get(id(fld_w))
            if port_name is None:
                # Unregistered field widget — skip.
                continue

            binding = bindings.get(port_name)
            if binding is None:
                continue

            # Discover the label text from the LabelRole.
            label_text = None
            if lbl_item is not None:
                lbl_w = lbl_item.widget()
                if isinstance(lbl_w, QLabel):
                    label_text = lbl_w.text()

            self._add_mirror_for_binding(
                port_name, binding, label_text=label_text,
            )

    def _add_mirror_for_binding(
        self,
        port_name: str,
        binding: "WidgetBinding",
        label_text: Optional[str] = _UNSET,
    ) -> None:
        """Create a single mirror widget, add it to the form, wire signals.

        Parameters
        ----------
        port_name : str
            The port/binding name.
        binding : WidgetBinding
            The source binding from WidgetCore.
        label_text : str or None or _UNSET
            If ``_UNSET`` (the default), the label is discovered from
            the source form via ``labelForField()``.  If ``None``, no
            label is added (spanning row).  If a string, that text is
            used.
        """
        mirror = self._create_mirror(binding)
        if mirror is None:
            return

        self._mirrors[port_name] = mirror

        # Sync initial enabled state.
        mirror.setEnabled(binding.widget.isEnabled())

        # Sync initial value.
        wc = getattr(self._node, "_widget_core", None)
        if wc is not None:
            value = wc.get_port_value(port_name)
            if value is not None:
                self._set_mirror_value(mirror, value)

        # Resolve label text if not provided by the caller.
        if label_text is _UNSET:
            label_text = self._discover_label_text(binding.widget)

        if label_text is not None:
            label = QLabel(label_text)
            self._mirror_labels[port_name] = label
            self._form.addRow(label, mirror)
        else:
            self._form.addRow(mirror)

        # Sync initial visibility (the source widget may already be hidden,
        # e.g. the Value spinbox when Fill ≠ Full).
        visible = binding.widget.isVisible()
        if not visible:
            mirror.hide()
            label_w = self._mirror_labels.get(port_name)
            if label_w is not None:
                label_w.hide()

        # Connect mirror → node (bidirectional sync).
        self._connect_mirror_signal(port_name, mirror, binding)

    def _discover_label_text(self, widget: QWidget) -> Optional[str]:
        """Look up the label for *widget* from the source QFormLayout.

        Uses ``labelForField()`` so we get the *exact* text the node
        author wrote (e.g. ``"Dim 0:"``, ``"Fill:"``).  Returns
        ``None`` when the widget spans the full row or the layout is
        not a ``QFormLayout``.
        """
        wc = getattr(self._node, "_widget_core", None)
        if wc is None:
            return None
        layout = wc.layout()
        if not isinstance(layout, QFormLayout):
            return None
        lbl_w = layout.labelForField(widget)
        if lbl_w is None:
            return None
        if isinstance(lbl_w, QLabel):
            return lbl_w.text()
        text_fn = getattr(lbl_w, "text", None)
        return text_fn() if callable(text_fn) else None

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

        for label in self._mirror_labels.values():
            try:
                label.setParent(None)
                label.deleteLater()
            except RuntimeError:
                pass

        self._mirrors.clear()
        self._mirror_labels.clear()

        # Clear the form layout.
        while self._form.count():
            item = self._form.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._placeholder:
                w.setParent(None)
                w.deleteLater()

    # ──────────────────────────────────────────────────────────────────────
    # Dynamic widget add/remove (reacts to WidgetCore signals)
    # ──────────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_widget_registered(self, port_name: str) -> None:
        """WidgetCore registered a new widget — add a mirror for it."""
        if port_name in self._mirrors:
            return  # already mirrored

        wc = getattr(self._node, "_widget_core", None)
        if wc is None:
            return

        binding = wc.get_binding(port_name)
        if binding is None:
            return

        # Remove placeholder if it's showing.
        if self._placeholder.parent() is self._body:
            self._placeholder.hide()
            self._placeholder.setParent(None)

        self._add_mirror_for_binding(port_name, binding)

    @Slot(str)
    def _on_widget_unregistered(self, port_name: str) -> None:
        """WidgetCore unregistered a widget — remove its mirror."""
        mirror = self._mirrors.pop(port_name, None)
        if mirror is None:
            return

        # Disconnect the mirror's change signal.
        self._disconnect_mirror_signal(port_name, mirror)
        self._mirror_slots.pop(port_name, None)

        # Also discard the tracked label.
        label = self._mirror_labels.pop(port_name, None)

        # Remove the mirror's row from the form layout.
        for row in range(self._form.rowCount()):
            field_item = self._form.itemAt(
                row, QFormLayout.ItemRole.FieldRole,
            )
            if field_item is not None and field_item.widget() is mirror:
                self._form.removeRow(row)
                return

        # Fallback: if we couldn't find it in the form, just detach.
        try:
            mirror.setParent(None)
            mirror.deleteLater()
        except RuntimeError:
            pass
        if label is not None:
            try:
                label.setParent(None)
                label.deleteLater()
            except RuntimeError:
                pass

    # ──────────────────────────────────────────────────────────────────────
    # Visibility mirroring
    # ──────────────────────────────────────────────────────────────────────

    @Slot(str, bool)
    def _on_widget_visibility_changed(
        self, port_name: str, visible: bool
    ) -> None:
        """A registered widget in the node was shown/hidden — sync the
        mirror row (both label and widget)."""
        mirror = self._mirrors.get(port_name)
        if mirror is not None:
            mirror.setVisible(visible)

        label = self._mirror_labels.get(port_name)
        if label is not None:
            label.setVisible(visible)

    # ──────────────────────────────────────────────────────────────────────
    # Mirror factory chain
    # ──────────────────────────────────────────────────────────────────────

    def _create_mirror(self, binding: "WidgetBinding") -> Optional[QWidget]:
        """
        Create a mirror widget for *binding*.

        Resolution order:
        1. Panel-local custom factory (exact type match).
        2. Global custom factory (exact type match).
        3. Built-in factory list (isinstance check, subclass-first).
        4. ``None`` if no factory can handle the type.
        """
        original = binding.widget
        orig_type = type(original)

        # 1. Panel-local custom factory
        factory = self._custom_factories.get(orig_type)
        if factory is not None:
            try:
                return factory(original, binding)
            except Exception as exc:
                log.warning(
                    f"Custom mirror factory for {orig_type.__name__} "
                    f"failed: {exc}"
                )

        # 2. Global custom factory
        global_factory = _get_custom_factory(orig_type)
        if global_factory is not None:
            try:
                return global_factory(original, binding)
            except Exception as exc:
                log.warning(
                    f"Global mirror factory for {orig_type.__name__} "
                    f"failed: {exc}"
                )

        # 3. Built-in factories
        for cls, factory_fn in _DEFAULT_FACTORIES:
            if isinstance(original, cls):
                try:
                    return factory_fn(original, binding)
                except Exception as exc:
                    log.warning(
                        f"Built-in mirror factory for {cls.__name__} "
                        f"failed: {exc}"
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

        for cls, name in _MIRROR_SIGNAL_MAP.items():
            if isinstance(mirror, cls):
                sig_name = name
                break

        if sig_name is None:
            sig_name = _CUSTOM_SIGNAL_MAP.get(type(mirror))

        # QPushButton.clicked → forward to the original button.
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
            value = generic_get(_m)
            _wc.set_port_value(_pn, value)
            _wc.value_changed.emit(_pn)

        sig.connect(_on_mirror_edited)
        self._mirror_slots[port_name] = _on_mirror_edited

    def _disconnect_mirror_signal(
        self, port_name: str, mirror: QWidget
    ) -> None:
        """Disconnect a mirror widget's change signal."""
        slot = self._mirror_slots.get(port_name)
        if slot is None:
            return

        for cls, sig_name in _MIRROR_SIGNAL_MAP.items():
            if isinstance(mirror, cls):
                sig = getattr(mirror, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
                return

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
        """A value changed inside the node — update the mirror."""
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
        was_blocked = mirror.signalsBlocked()
        mirror.blockSignals(True)
        try:
            generic_set(mirror, value)
        except Exception as exc:
            log.debug(f"Failed to set mirror value: {exc}")
        finally:
            mirror.blockSignals(was_blocked)

    @Slot(str, bool)
    def _on_port_enabled_changed(
        self, port_name: str, enabled: bool
    ) -> None:
        """A widget inside the node was enabled/disabled — sync."""
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
        if self._static:
            self.dock_title_changed.emit(new_title)
        else:
            self._header.set_title(new_title)

    # ──────────────────────────────────────────────────────────────────────
    # Keyboard — forward undo/redo to canvas
    # ──────────────────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        """Intercept Ctrl+Z / Ctrl+Shift+Z and forward to the canvas
        undo manager.

        Panel widgets are standard ``QWidget`` trees, not
        ``QGraphicsProxyWidget``.  When a panel spinbox or combo has
        focus, keyboard events go to the panel — the canvas scene's
        ``keyPressEvent`` (in ``DefaultInteractionState``) never sees
        them.  We catch undo/redo here and delegate to the scene's
        ``CanvasCommandsMixin`` so the shortcuts work regardless of
        where focus is.

        All other keys are forwarded to the default handler so normal
        widget editing (typing in a spinbox, arrow keys, etc.) is not
        affected.
        """
        from PySide6.QtCore import Qt

        mod = event.modifiers()
        key = event.key()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mod & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mod & Qt.KeyboardModifier.AltModifier)

        if ctrl and not alt and key == Qt.Key.Key_Z:
            provider = self._find_command_provider()
            if provider is not None:
                if shift:
                    provider.cmd_redo()
                else:
                    provider.cmd_undo()
                event.accept()
                return

        super().keyPressEvent(event)

    def _find_command_provider(self):
        """Locate the ``CanvasCommandsMixin`` for undo/redo dispatch.

        Walks: bound node → scene → ``_context_menu_provider``.
        Returns ``None`` if any step fails.
        """
        node = self._node
        if node is None:
            return None
        try:
            scene = node.scene()
        except RuntimeError:
            return None
        if scene is None:
            return None
        return getattr(scene, "_context_menu_provider", None)

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
