# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

NodeDockAdapter — Mirror node widgets into standard Qt dock panels
===================================================================

Provides a bridge between the node-graph world (where widgets live inside
``WidgetCore`` → ``QGraphicsProxyWidget``) and a traditional Qt GUI (where
widgets live in ``QDockWidget`` / ``QWidget`` hierarchies).

The adapter **does not reparent** the original widgets.  Instead it creates
lightweight *mirror* widgets that are bidirectionally synchronised with the
node's ``WidgetCore`` via its public ``get_port_value`` / ``set_port_value``
API and the ``value_changed`` signal.

Architecture
------------
::

    ┌─────────────────────────────────────┐
    │          NodeDockAdapter             │
    │  (QDockWidget)                      │
    │  ┌───────────────────────────────┐  │
    │  │         NodePanel              │  │
    │  │  ┌─────────────────────────┐  │  │
    │  │  │  _header  (title+state) │  │  │
    │  │  ├─────────────────────────┤  │  │
    │  │  │  mirror "value"  ↔ node │  │  │
    │  │  │  mirror "mode"   ↔ node │  │  │
    │  │  │  mirror "step"   ↔ node │  │  │
    │  │  └─────────────────────────┘  │  │
    │  └───────────────────────────────┘  │
    └─────────────────────────────────────┘

    Sync paths
    ──────────
    User edits mirror widget
      → mirror signal fires
      → _on_mirror_changed()
      → widget_core.set_port_value(name, value)
      → node.compute()  (via existing signal chain)

    Upstream data arrives / user edits node widget
      → widget_core.value_changed(port_name)
      → _on_node_value_changed(port_name)
      → update mirror widget  (signals blocked)

Dock modes
----------
DYNAMIC
    Follows canvas selection.  When the user clicks a node, the panel
    rebuilds its mirrors for that node.  Clicking empty space clears
    the panel.  Closing the dock only hides it — reopen from the
    Window menu or ``dock.show()`` and it resumes following selection.

STATIC
    Permanently bound to one specific node.  If the linked node is
    deleted (removed from the scene or garbage-collected), the dock
    automatically closes and cleans up.  Closing the dock manually
    also unlinks from the node.

Usage
-----
::

    # Dynamic: inspector that follows selection
    inspector = NodeDockAdapter.create_dynamic("Inspector", scene, parent=win)
    main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector)

    # Static: pinned to a specific node
    pinned = NodeDockAdapter.create_static("Float Controls", my_node, parent=win)
    main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, pinned)

    # Embed a panel anywhere (no dock)
    panel = NodePanel()
    panel.bind_node(my_node, static=True)
    some_layout.addWidget(panel)

Custom mirror factories
-----------------------
For complex custom widgets the default cloner cannot handle, register a
factory **before** binding the node::

    def my_factory(original: QWidget, binding: WidgetBinding) -> QWidget:
        mirror = MyFancyWidget()
        mirror.import_config(original.export_config())
        return mirror

    panel.register_mirror_factory(MyFancyWidget, my_factory)
    panel.bind_node(node_with_fancy_widgets)
"""

from __future__ import annotations

from enum import Enum, auto
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Type,
    TYPE_CHECKING, Union,
)

from PySide6.QtCore import Qt, Signal, Slot, QObject, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QScrollArea,
    QDockWidget, QLabel, QFrame, QPushButton,
    # Supported mirror types
    QAbstractSpinBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QCheckBox, QAbstractSlider, QSlider,
    QGraphicsScene, QGraphicsItem,
)

if TYPE_CHECKING:
    from weave.node.node_core import Node
    from weave.widgetcore import WidgetBinding

from weave.logger import get_logger

log = get_logger("NodeDockAdapter")

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
# Enums
# ══════════════════════════════════════════════════════════════════════════════

class DockMode(Enum):
    """Operating mode of a ``NodeDockAdapter``."""
    DYNAMIC = auto()   # Follows canvas selection
    STATIC  = auto()   # Pinned to a specific node


# ══════════════════════════════════════════════════════════════════════════════
# Mirror Widget Factory
# ══════════════════════════════════════════════════════════════════════════════

MirrorFactory = Callable[["QWidget", "WidgetBinding"], QWidget]


def _clone_spinbox(src: QSpinBox, _binding) -> QSpinBox:
    w = QSpinBox()
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setValue(src.value())
    w.setPrefix(src.prefix())
    w.setSuffix(src.suffix())
    w.setWrapping(src.wrapping())
    return w


def _clone_double_spinbox(src: QDoubleSpinBox, _binding) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setDecimals(src.decimals())
    w.setValue(src.value())
    w.setPrefix(src.prefix())
    w.setSuffix(src.suffix())
    w.setWrapping(src.wrapping())
    return w


def _clone_combobox(src: QComboBox, _binding) -> QComboBox:
    w = QComboBox()
    w.setEditable(src.isEditable())
    for i in range(src.count()):
        data = src.itemData(i)
        if data is not None:
            w.addItem(src.itemText(i), data)
        else:
            w.addItem(src.itemText(i))
    w.setCurrentIndex(src.currentIndex())
    return w


def _clone_checkbox(src: QCheckBox, _binding) -> QCheckBox:
    w = QCheckBox(src.text())
    w.setChecked(src.isChecked())
    w.setTristate(src.isTristate())
    return w


def _clone_slider(src: QSlider, _binding) -> QSlider:
    w = QSlider(src.orientation())
    w.setRange(src.minimum(), src.maximum())
    w.setSingleStep(src.singleStep())
    w.setPageStep(src.pageStep())
    w.setValue(src.value())
    w.setTickPosition(src.tickPosition())
    w.setTickInterval(src.tickInterval())
    return w


def _clone_lineedit(src: QLineEdit, _binding) -> QLineEdit:
    w = QLineEdit(src.text())
    w.setPlaceholderText(src.placeholderText())
    w.setMaxLength(src.maxLength())
    w.setReadOnly(src.isReadOnly())
    return w


def _clone_textedit(src: QTextEdit, _binding) -> QTextEdit:
    w = QTextEdit()
    w.setPlainText(src.toPlainText())
    w.setReadOnly(src.isReadOnly())
    return w


def _clone_plaintextedit(src: QPlainTextEdit, _binding) -> QPlainTextEdit:
    w = QPlainTextEdit()
    w.setPlainText(src.toPlainText())
    w.setReadOnly(src.isReadOnly())
    return w


def _clone_label(src: QLabel, _binding) -> QLabel:
    w = QLabel(src.text())
    w.setWordWrap(src.wordWrap())
    w.setAlignment(src.alignment())
    return w


def _clone_pushbutton(src: QPushButton, _binding) -> QPushButton:
    w = QPushButton(src.text())
    w.setCheckable(src.isCheckable())
    w.setChecked(src.isChecked())
    return w


# Ordered so subclasses are matched before base classes.
_DEFAULT_FACTORIES: List[Tuple[type, MirrorFactory]] = [
    (QDoubleSpinBox, _clone_double_spinbox),
    (QSpinBox,       _clone_spinbox),
    (QComboBox,      _clone_combobox),
    (QCheckBox,      _clone_checkbox),
    (QSlider,        _clone_slider),
    (QLineEdit,      _clone_lineedit),
    (QTextEdit,      _clone_textedit),
    (QPlainTextEdit, _clone_plaintextedit),
    (QLabel,         _clone_label),
    (QPushButton,    _clone_pushbutton),
]

# Signal names used to detect changes on mirror widgets.
_MIRROR_SIGNAL_MAP: Dict[type, str] = {
    QDoubleSpinBox: "valueChanged",
    QSpinBox:       "valueChanged",
    QComboBox:      "currentIndexChanged",
    QCheckBox:      "stateChanged",
    QSlider:        "valueChanged",
    QLineEdit:      "textChanged",
    QTextEdit:      "textChanged",
    QPlainTextEdit: "textChanged",
}


# ══════════════════════════════════════════════════════════════════════════════
# _PanelHeader
# ══════════════════════════════════════════════════════════════════════════════

class _PanelHeader(QWidget):
    """Compact header showing the node title, state badge, and unlink button."""

    unlink_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self._title_label = QLabel()
        font = self._title_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self._title_label.setFont(font)
        layout.addWidget(self._title_label, stretch=1)

        self._state_label = QLabel()
        self._state_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        font_s = self._state_label.font()
        font_s.setPointSize(font_s.pointSize() - 1)
        self._state_label.setFont(font_s)
        layout.addWidget(self._state_label)

        self._unlink_btn = QPushButton("Unlink")
        self._unlink_btn.setFixedWidth(52)
        self._unlink_btn.setToolTip("Disconnect this panel from the node")
        self._unlink_btn.clicked.connect(self.unlink_clicked)
        self._unlink_btn.hide()
        layout.addWidget(self._unlink_btn)

    def set_title(self, text: str) -> None:
        self._title_label.setText(text)

    def set_state_text(self, text: str) -> None:
        self._state_label.setText(text)

    def set_unlink_visible(self, visible: bool) -> None:
        self._unlink_btn.setVisible(visible)


# ══════════════════════════════════════════════════════════════════════════════
# NodePanel — embeddable widget that mirrors a single node
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._node: Optional["Node"] = None
        self._static: bool = False
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
        self._header = _PanelHeader(self)
        self._header.unlink_clicked.connect(self._on_unlink_clicked)
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

        # Static panels refuse to be rebound to a different node.
        if self._static and self._node is not None:
            log.debug(
                "Static panel already bound — ignoring bind_node() for "
                f"'{self._node_title(node)}'."
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
        self._header.set_title(self._node_title(node))
        self._header.set_unlink_visible(True)
        self._build_mirrors()

        # Listen for value changes coming FROM the node.
        if wc is not None:
            wc.value_changed.connect(self._on_node_value_changed)

        # Listen for node state changes to update the header badge.
        if hasattr(node, "state_changed"):
            try:
                node.state_changed.connect(self._on_node_state_changed)
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

        # Disconnect WidgetCore signal
        if wc is not None:
            try:
                wc.value_changed.disconnect(self._on_node_value_changed)
            except (RuntimeError, TypeError):
                pass

        # Disconnect state_changed
        if hasattr(node, "state_changed"):
            try:
                node.state_changed.disconnect(self._on_node_state_changed)
            except (RuntimeError, TypeError):
                pass

        # Stop watching lifetime
        self._unwatch_node_lifetime(node)

        self._tear_down_mirrors()
        self._node = None
        self._header.set_title("")
        self._header.set_state_text("")
        self._header.set_unlink_visible(False)

        # Re-add placeholder
        self._placeholder.setParent(self._body)
        self._form.addRow(self._placeholder)
        self._placeholder.show()

        self.node_unbound.emit()

    # ──────────────────────────────────────────────────────────────────────
    # Unlink button
    # ──────────────────────────────────────────────────────────────────────

    @Slot()
    def _on_unlink_clicked(self) -> None:
        """Handle the header Unlink button."""
        self.unbind()

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
        self._header.set_title("[deleted]")
        self._header.set_state_text("")
        self._header.set_unlink_visible(False)

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
        1. Custom factory registered via ``register_mirror_factory()``
           (exact type match).
        2. Built-in factory list (``isinstance`` check, subclass-first).
        3. ``None`` if no factory can handle the type.
        """
        original = binding.widget
        orig_type = type(original)

        # 1. Custom factory (exact match)
        factory = self._custom_factories.get(orig_type)
        if factory is not None:
            try:
                return factory(original, binding)
            except Exception as exc:
                log.warning(
                    f"Custom mirror factory for {orig_type.__name__} failed: {exc}"
                )

        # 2. Built-in factories
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
        for cls, name in _MIRROR_SIGNAL_MAP.items():
            if isinstance(mirror, cls):
                sig_name = name
                break

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
            _wc.set_port_value(_pn, value)

        sig.connect(_on_mirror_edited)
        self._mirror_slots[port_name] = _on_mirror_edited

    def _disconnect_mirror_signal(self, port_name: str, mirror: QWidget) -> None:
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

    # ──────────────────────────────────────────────────────────────────────
    # Node state badge
    # ──────────────────────────────────────────────────────────────────────

    @Slot(object, object)
    def _on_node_state_changed(self, _old_state, new_state) -> None:
        name = new_state.name if hasattr(new_state, "name") else str(new_state)
        self._header.set_state_text(name)

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


# ══════════════════════════════════════════════════════════════════════════════
# NodeDockAdapter — QDockWidget convenience wrapper
# ══════════════════════════════════════════════════════════════════════════════

class NodeDockAdapter(QDockWidget):
    """
    ``QDockWidget`` that wraps a ``NodePanel``.

    Supports two operating modes, exposed through the convenience
    constructors ``create_dynamic()`` and ``create_static()``.

    Parameters
    ----------
    title : str
        Dock widget title shown in the title bar and Window menu.
    parent : QWidget | None
        Parent widget (typically the ``QMainWindow``).
    """

    # Re-exported from the inner panel for convenience.
    node_bound   = Signal(object)
    node_unbound = Signal()

    def __init__(
        self,
        title: str = "Node Properties",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(title, parent)

        self._panel = NodePanel(self)
        self.setWidget(self._panel)

        self._mode: DockMode = DockMode.DYNAMIC
        self._selection_scene: Optional[QGraphicsScene] = None

        # Forward panel signals
        self._panel.node_bound.connect(self.node_bound)
        self._panel.node_unbound.connect(self.node_unbound)

        # When the static node is lost, close this dock
        self._panel.linked_node_lost.connect(self._on_linked_node_lost)

    # ──────────────────────────────────────────────────────────────────────
    # Factory constructors
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def create_dynamic(
        cls,
        title: str,
        scene: QGraphicsScene,
        *,
        parent: Optional[QWidget] = None,
    ) -> "NodeDockAdapter":
        """
        Create a **dynamic** dock that follows canvas selection.

        Selecting a single node on the canvas populates the panel
        with that node's mirrors.  Clearing the selection empties
        the panel.  Closing the dock hides it; reopening it via the
        Window menu or ``dock.show()`` resumes selection tracking.

        Parameters
        ----------
        title : str
            Dock title.
        scene : QGraphicsScene
            The ``Canvas`` whose selection to follow.
        parent : QWidget, optional
            Parent widget (usually the QMainWindow).
        """
        dock = cls(title, parent)
        dock._mode = DockMode.DYNAMIC
        dock._bind_to_selection(scene)
        return dock

    @classmethod
    def create_static(
        cls,
        title: str,
        node: "Node",
        *,
        parent: Optional[QWidget] = None,
    ) -> "NodeDockAdapter":
        """
        Create a **static** dock pinned to *node*.

        The dock shows this node's mirrors for its entire lifetime.
        If the node is deleted, the dock closes automatically.
        If the user closes the dock manually, the node is unlinked.

        Parameters
        ----------
        title : str
            Dock title.
        node : Node
            The node to pin.
        parent : QWidget, optional
            Parent widget (usually the QMainWindow).
        """
        dock = cls(title, parent)
        dock._mode = DockMode.STATIC
        dock._panel.bind_node(node, static=True)
        return dock

    # ──────────────────────────────────────────────────────────────────────
    # Forwarded API
    # ──────────────────────────────────────────────────────────────────────

    @property
    def panel(self) -> NodePanel:
        """The embedded ``NodePanel`` instance."""
        return self._panel

    @property
    def node(self) -> Optional["Node"]:
        return self._panel.node

    @property
    def mode(self) -> DockMode:
        return self._mode

    def bind_node(self, node: "Node", *, static: bool = False) -> None:
        """Bind the dock to *node*."""
        self._panel.bind_node(node, static=static)
        if static:
            self._mode = DockMode.STATIC

    def unbind(self) -> None:
        """Unbind from the current node."""
        self._panel.unbind()

    def register_mirror_factory(
        self, widget_type: type, factory: MirrorFactory
    ) -> None:
        """Forward to the inner ``NodePanel``."""
        self._panel.register_mirror_factory(widget_type, factory)

    # ──────────────────────────────────────────────────────────────────────
    # Close event
    # ──────────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """
        Handle the dock being closed by the user or programmatically.

        DYNAMIC
            The dock is merely hidden.  Mirrors are torn down to free
            resources, but the dock stays alive.  Reopening via the
            Window menu or ``show()`` resumes selection tracking.

        STATIC
            Unbind (releases the static lock) and fully close.
        """
        if self._mode == DockMode.STATIC:
            self._panel.unbind()
            self._unbind_from_selection()
        else:
            # Dynamic: tear down mirrors but keep the dock alive (hidden).
            self._panel._unbind_internal()

        super().closeEvent(event)

    # ──────────────────────────────────────────────────────────────────────
    # Visibility — re-sync on re-show for dynamic mode
    # ──────────────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        """When a hidden dynamic dock is shown again, sync to selection."""
        super().showEvent(event)
        if self._mode == DockMode.DYNAMIC and self._selection_scene is not None:
            self._sync_to_current_selection()

    # ──────────────────────────────────────────────────────────────────────
    # Selection following (dynamic mode)
    # ──────────────────────────────────────────────────────────────────────

    def _bind_to_selection(self, scene: QGraphicsScene) -> None:
        """Start following *scene* selection changes."""
        if self._selection_scene is not None:
            self._unbind_from_selection()

        self._selection_scene = scene
        scene.selectionChanged.connect(self._on_selection_changed)

    def _unbind_from_selection(self) -> None:
        """Stop following canvas selection."""
        if self._selection_scene is not None:
            try:
                self._selection_scene.selectionChanged.disconnect(
                    self._on_selection_changed
                )
            except (RuntimeError, TypeError):
                pass
            self._selection_scene = None

    @Slot()
    def _on_selection_changed(self) -> None:
        """Handle canvas ``selectionChanged`` signal."""
        if self._mode != DockMode.DYNAMIC:
            return
        if not self.isVisible():
            return
        self._sync_to_current_selection()

    def _sync_to_current_selection(self) -> None:
        """Read the current selection and bind/unbind accordingly."""
        if self._selection_scene is None:
            return

        selected = self._selection_scene.selectedItems()

        from weave.node.node_core import Node
        nodes = [item for item in selected if isinstance(item, Node)]

        if len(nodes) == 1:
            self._panel.bind_node(nodes[0], static=False)
        else:
            # Only unbind if not static (dynamic panels follow selection)
            if not self._panel.is_static:
                self._panel._unbind_internal()

    # ──────────────────────────────────────────────────────────────────────
    # Static-mode: linked node lost
    # ──────────────────────────────────────────────────────────────────────

    @Slot()
    def _on_linked_node_lost(self) -> None:
        """The statically pinned node has been deleted — close the dock."""
        log.debug("Static dock: linked node deleted — closing dock.")
        self._unbind_from_selection()
        self.close()