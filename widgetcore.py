# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

WidgetCore — Centralised widget container for node body content.
=====================================================================

Purposes
--------
1. **Proxy-safe interactivity**
   Wraps all child widgets so that popups (QComboBox, context menus, date
   pickers …) work correctly inside a QGraphicsProxyWidget.  The core
   installs itself as an event-filter on every registered widget and
   makes sure focus, hover, and popup events are not swallowed by the
   scene's state machine.

2. **Declarative widget ↔ port mapping**
   Instead of every node manually reading widget values in ``compute()``
   and manually wiring signals, the node registers widgets once::

       core.register_widget("value", my_spinbox, role="output",
                            datatype="float", default=0.0)

   The core then exposes helpers so the node can auto-create ports and
   read/write values without touching individual widgets.

3. **Unified serialisation**
   ``get_state()`` / ``set_state()`` are the *only* mechanism for widget
   state persistence.  ``QtNode`` handles GUI-only state (position, size,
   colors, ports); ``BaseControlNode`` delegates widget state exclusively
   to this class.  There is no legacy ``get_widget_state`` path.

4. **Free layout**
   WeaveWidgetCore is a plain QWidget.  You can use *any* QLayout
   (VBox, HBox, Grid, Form, stacked …) or even ``setLayout(None)`` and
   place children with ``move()`` / ``resize()``.

5. **Auto-disable on connection**
   When an upstream connection is made to an input port whose name
   matches a registered widget, the widget is automatically disabled
   (greyed-out) and re-enabled when disconnected.

Architecture
------------
::

    BaseControlNode  (QtNode + NodeDataFlow)
      └── body: NodeBody
            └── _proxy: QGraphicsProxyWidget
                  └── _widget (container QWidget)
                        └── **WidgetCore**   ← set via set_content_widget()
                              ├── QDoubleSpinBox  (registered → "value" output)
                              ├── QComboBox       (registered → "mode" output)
                              └── QLabel          (unregistered, decorative)

Serialisation boundary
----------------------
::

    Node.get_state()            → GUI only: pos, size, colors, port defs, minimized
    BaseControlNode.get_state()   → super() + { "widget_data": core.get_state() }
                                           + { dataflow metadata }

Public API summary
------------------
- ``register_widget(port_name, widget, ...)``  — declare mapping
- ``get_port_definitions()``                   — node calls to auto-create ports
- ``get_port_value(port_name)``                — node calls in compute()
- ``set_port_value(port_name, value)``         — push upstream data back to UI
- ``set_port_enabled(port_name, enabled)``     — auto-disable hook
- ``get_state()`` / ``set_state(data)``        — serialisation
- ``value_changed``  Signal(str)               — emitted with port_name on change

Dropdown / Popup Fix
--------------------
The root cause is twofold:

1. ``NodeBody._proxy`` had no ``ItemIsFocusable`` flag, so clicks inside
   the proxy were not properly routed through Qt's focus system.
2. The canvas ``mousePressEvent`` delegates to its state machine *first*;
   if the state machine returns True the event is consumed before the
   ``QGraphicsProxyWidget`` can forward it to the embedded QComboBox.

WidgetCore fixes this by:

* Setting the correct flags on the proxy the moment it is parented
  (see ``_patch_parent_proxy``).
* Providing ``is_interactive_at(scene_pos)`` so the canvas state machine
  can detect "this click belongs to a node widget" and yield control.

Canvas-side patch (add to IdleState.on_mouse_press, BEFORE any other logic)::

    from PySide6.QtWidgets import QGraphicsProxyWidget
    from PySide6.QtGui import QTransform

    def on_mouse_press(self, event):
        item = self.canvas.itemAt(event.scenePos(), QTransform())
        # ── Let proxy widgets handle their own clicks ──
        if isinstance(item, QGraphicsProxyWidget):
            return False            # ← don't consume; Qt routes to widget
        # ... existing state machine logic ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING,
    Union
)

from PySide6.QtCore import Qt, Signal, Slot, QObject, QEvent, QTimer, QPointF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLayout, QGraphicsProxyWidget, QGraphicsItem,
    # Supported auto-read/write widget types
    QAbstractSpinBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QCheckBox, QAbstractSlider, QSlider,
    QLabel, QPushButton,
)

if TYPE_CHECKING:
    from weave.node.node_core import Node
from weave.logger import get_logger

log = get_logger("WeaveWidgetCore")


# ══════════════════════════════════════════════════════════════════════════════
# Data Structures
# ══════════════════════════════════════════════════════════════════════════════

class PortRole(Enum):
    """How a widget relates to node ports."""
    INPUT = auto()          # Widget provides a fallback for an *input* port
    OUTPUT = auto()         # Widget drives an *output* port value
    BIDIRECTIONAL = auto()  # Both: shows incoming data, provides default
    DISPLAY = auto()        # Read-only display (no port created)
    INTERNAL = auto()       # Not exposed as a port; node reads value manually


@dataclass
class WidgetBinding:
    """One entry in the registry that ties a widget to a port name."""
    port_name: str
    widget: QWidget
    role: PortRole = PortRole.OUTPUT
    datatype: str = "any"
    default: Any = None
    description: str = ""

    # Callables override the generic read/write helpers.
    # Signature: getter() -> Any,  setter(value) -> None
    getter: Optional[Callable[[], Any]] = None
    setter: Optional[Callable[[Any], None]] = None

    # The signal name on the widget that fires when the user edits it.
    # ``None`` = auto-detect (works for all standard Qt widgets).
    change_signal_name: Optional[str] = None

    # Internal bookkeeping (not for public use)
    _connected_signal: Optional[str] = field(default=None, repr=False)


@dataclass
class PortDefinition:
    """Returned by ``get_port_definitions()`` so the node can auto-create ports."""
    name: str
    datatype: str
    role: PortRole
    default: Any
    description: str


# ══════════════════════════════════════════════════════════════════════════════
# WeaveWidgetCore
# ══════════════════════════════════════════════════════════════════════════════

class WidgetCore(QWidget):
    """
    Central widget container placed inside a node's body.

    Every BaseControlNode must create and embed a WeaveWidgetCore.
    It is the sole owner of widget state serialisation — there is no
    fallback to ``get_widget_state`` / ``set_widget_state``.

    Signals
    -------
    value_changed(str)
        Emitted whenever a registered widget's value changes.
        The argument is the *port_name* that changed.
    """

    value_changed = Signal(str)  # port_name

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(
        self,
        layout: Optional[QLayout] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        # ── Internal registry ────────────────────────────────────────────
        self._bindings: Dict[str, WidgetBinding] = {}   # port_name → binding
        self._widget_to_port: Dict[int, str] = {}       # id(widget) → port_name
        self._suppress_signals: bool = False             # bulk-update guard
        self._node_ref: Optional["Node"] = None          # back-reference

        # ── Layout ───────────────────────────────────────────────────────
        if layout is None:
            layout = QVBoxLayout()
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)
        self.setLayout(layout)

        # ── Transparent background (the NodeBody paints behind us) ───────
        self.setStyleSheet("background: transparent; color: white;")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # ── Deferred proxy patching ──────────────────────────────────────
        QTimer.singleShot(0, self._patch_parent_proxy)

    # ══════════════════════════════════════════════════════════════════════
    # Proxy / Focus Fix
    # ══════════════════════════════════════════════════════════════════════

    def _patch_parent_proxy(self) -> None:
        """
        Walk up the QWidget parent chain to find the QGraphicsProxyWidget
        that hosts us and ensure it has the right flags for interactive
        child widgets (combo-box popups, context menus, tooltips …).

        Retry logic:
            ``_patch_parent_proxy`` is called via ``QTimer.singleShot(0)``
            from ``__init__``.  If the proxy has not been set up yet by
            ``NodeBody`` at that point, ``_find_proxy()`` returns None.
            We retry once after a short delay to handle deferred
            construction sequences.
        """
        proxy = self._find_proxy()
        if proxy is None:
            # Proxy not ready yet — retry once after a short delay
            if not getattr(self, '_proxy_patch_retried', False):
                self._proxy_patch_retried = True
                QTimer.singleShot(50, self._patch_parent_proxy)
            return

        proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        proxy.setAcceptHoverEvents(True)
        proxy.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemAcceptsInputMethod, True
        )
        proxy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _find_proxy(self) -> Optional[QGraphicsProxyWidget]:
        """Find the nearest QGraphicsProxyWidget ancestor."""
        widget: Optional[QWidget] = self
        while widget is not None:
            proxy = widget.graphicsProxyWidget()
            if proxy is not None:
                return proxy
            widget = widget.parentWidget()
        return None

    def is_interactive_at(self, scene_pos: QPointF) -> bool:
        """
        Returns True if *scene_pos* (QPointF in scene coordinates) lands
        on an interactive child widget (spin box, combo, line-edit …).

        Coordinate path:
            scene_pos  →  proxy.mapFromScene()  →  proxy-local coords
            (proxy-local coords ≡ root-widget coords for QGraphicsProxyWidget)
            root.childAt()  →  deepest child widget at that position
        """
        proxy = self._find_proxy()
        if proxy is None:
            return False

        # Scene → proxy-local coordinates (= root widget coordinates)
        root = proxy.widget()
        if root is None:
            return False
        
        local = proxy.mapFromScene(scene_pos).toPoint()

        # Find the deepest child in the entire embedded widget tree
        child = root.childAt(local.x(), local.y())
        if child is None:
            return False
        if isinstance(child, QLabel):
            return False
        if type(child) is QWidget:
            return False
        return True

    def get_proxy(self) -> Optional[QGraphicsProxyWidget]:
        """Public accessor for the hosting QGraphicsProxyWidget."""
        return self._find_proxy()

    # ══════════════════════════════════════════════════════════════════════
    # Direct widget activation (bypasses proxy event delivery)
    # ══════════════════════════════════════════════════════════════════════

    def activate_at(self, scene_pos: QPointF) -> bool:
        """
        Directly activate the widget under *scene_pos*.

        If the widget (or one of its ancestors inside the WidgetCore)
        exposes a ``show_popup(global_pos)`` method, the popup is opened
        immediately with correctly mapped global coordinates.

        This **bypasses Qt's event delivery chain entirely**, which is
        necessary because ``QGraphicsScene.mousePressEvent`` delivers to
        the parent node — not the proxy — so the embedded widget never
        receives the click through normal routing.

        Returns True if a widget was activated, False otherwise.
        Non-popup widgets (QSpinBox, QLineEdit …) return False so that
        the caller can fall back to the proxy focus path.
        """
        proxy = self._find_proxy()
        if proxy is None:
            return False

        root = proxy.widget()
        if root is None:
            return False

        local = proxy.mapFromScene(scene_pos).toPoint()
        child = root.childAt(local.x(), local.y())
        if child is None:
            return False

        # childAt() returns the deepest widget — which may be an internal
        # sub-widget of the button (e.g. a styled QFrame).  Walk up until
        # we find a widget with show_popup or we hit the WidgetCore.
        target = child
        while target is not None and target is not self:
            if hasattr(target, 'show_popup') and callable(target.show_popup):
                global_pos = self._widget_to_global(
                    target, target.rect().bottomLeft(), proxy
                )
                target.show_popup(global_pos)
                return True
            target = target.parentWidget()

        return False

    def _widget_to_global(self, widget: QWidget, local_pos: QPointF, 
                         proxy: QGraphicsProxyWidget) -> QPoint:
        """
        Map *local_pos* (QPoint in *widget*'s coordinate system) to
        global screen coordinates by walking the full coordinate chain::

            widget-local → proxy root → scene → view viewport → screen

        This avoids ``QWidget.mapToGlobal`` which is broken for widgets
        embedded inside ``QGraphicsProxyWidget`` (it ignores the view's
        pan and zoom transforms).
        """
        scene = proxy.scene()
        if scene is None or not scene.views():
            return widget.mapToGlobal(local_pos.toPoint())

        view = scene.views()[0]
        proxy_root = proxy.widget()
        if proxy_root is None:
            return widget.mapToGlobal(local_pos.toPoint())

        # widget-local → proxy root widget local (QPoint → QPoint)
        proxy_pos = widget.mapTo(proxy_root, local_pos)

        # proxy root local → scene (QPoint → QPointF)
        scene_pos = proxy.mapToScene(QPointF(proxy_pos))

        # scene → view viewport (QPointF → QPoint)
        view_pos = view.mapFromScene(scene_pos)

        # view viewport → global screen (QPoint → QPoint)
        return view.viewport().mapToGlobal(view_pos)

    # ══════════════════════════════════════════════════════════════════════
    # Widget Registration
    # ══════════════════════════════════════════════════════════════════════

    def register_widget(
        self,
        port_name: str,
        widget: QWidget,
        *,
        role: Union[str, PortRole] = PortRole.OUTPUT,
        datatype: str = "any",
        default: Any = None,
        description: str = "",
        getter: Optional[Callable[[], Any]] = None,
        setter: Optional[Callable[[Any], None]] = None,
        change_signal_name: Optional[str] = None,
        add_to_layout: bool = True,
    ) -> None:
        """
        Register *widget* as the UI element for *port_name*.

        Parameters
        ----------
        port_name : str
            Must be unique within this core.  Will become the port name on
            the parent node.
        widget : QWidget
            The Qt widget (QSpinBox, QComboBox, QLineEdit, …).
        role : Union[str, PortRole]
            ``"input"``, ``"output"``, ``"bidirectional"``, ``"display"``
            or ``"internal"``.  Determines whether and how a port is created.
        datatype : str
            Port datatype string (``"float"``, ``"int"``, ``"string"`` …).
        default : Any
            Default value when nothing is connected or the widget is empty.
        description : str
            Port description / tooltip.
        getter : callable, optional
            ``() -> Any`` — custom function to read the widget's value.
            If ``None``, a generic reader is used based on widget type.
        setter : callable, optional
            ``(value) -> None`` — custom function to write a value to the
            widget.  If ``None``, a generic writer is used.
        change_signal_name : str, optional
            Name of the signal on *widget* that fires when the user edits
            the value (e.g. ``"valueChanged"``).  If ``None``, auto-detected.
        add_to_layout : bool
            If True (default), ``widget`` is appended to this core's layout.
            Set False if you placed it manually in a nested sub-layout.

        Raises
        ------
        ValueError
            When port_name already registered.
        """
        # Ensure role is converted to enum value
        if isinstance(role, str):
            try:
                role = PortRole[role.upper()]
            except KeyError as e:
                raise ValueError(f"Invalid role '{role}'") from e

        if port_name in self._bindings:
            raise ValueError(
                f"Port name '{port_name}' is already registered in this "
                f"WeaveWidgetCore.  Use unregister_widget() first."
            )

        binding = WidgetBinding(
            port_name=port_name,
            widget=widget,
            role=role,
            datatype=datatype,
            default=default,
            description=description,
            getter=getter,
            setter=setter,
            change_signal_name=change_signal_name,
        )

        self._bindings[port_name] = binding
        self._widget_to_port[id(widget)] = port_name

        if add_to_layout and self.layout() is not None:
            self.layout().addWidget(widget)

        self._connect_change_signal(binding)
        widget.installEventFilter(self)

    def unregister_widget(self, port_name: str) -> Optional[QWidget]:
        """
        Remove a widget binding.  Returns the widget (still alive, not
        deleted) or None if the name was not registered.

        Parameters
        ----------
        port_name : str
            Port name to remove.

        Returns
        -------
        QWidget or None
            The unregistered widget or None if not found.
        """
        binding = self._bindings.pop(port_name, None)
        if binding is None:
            return None

        self._widget_to_port.pop(id(binding.widget), None)
        self._disconnect_change_signal(binding)
        try:
            binding.widget.removeEventFilter(self)
        except RuntimeError:
            pass
        return binding.widget

    # ══════════════════════════════════════════════════════════════════════
    # Port Definitions — what the node should expose
    # ══════════════════════════════════════════════════════════════════════

    def get_port_definitions(self) -> List[PortDefinition]:
        """
        Returns a list describing which ports the node should create based
        on registered widgets.

        Intended usage in the node constructor::

            for pd in core.get_port_definitions():
                if pd.role in (PortRole.OUTPUT, PortRole.BIDIRECTIONAL):
                    self.add_output(pd.name, pd.datatype, pd.description)
                if pd.role in (PortRole.INPUT, PortRole.BIDIRECTIONAL):
                    self.add_input(pd.name, pd.datatype, pd.description)

        Returns
        -------
        List[PortDefinition]
            Definitions of ports to be created.
        """
        defs: List[PortDefinition] = []
        for binding in self._bindings.values():
            if binding.role in (PortRole.DISPLAY, PortRole.INTERNAL):
                continue
            defs.append(PortDefinition(
                name=binding.port_name,
                datatype=binding.datatype,
                role=binding.role,
                default=binding.default,
                description=binding.description,
            ))
        return defs

    # ══════════════════════════════════════════════════════════════════════
    # Value Read / Write — called by node's compute()
    # ══════════════════════════════════════════════════════════════════════

    def get_port_value(self, port_name: str) -> Any:
        """
        Read the current value of the widget registered to *port_name*.

        Falls back to the binding's ``default`` if the widget is empty or
        the port name is unknown.

        Parameters
        ----------
        port_name : str
            Port name to read from.

        Returns
        -------
        Any
            Current widget value, or default if not found.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return None

        try:
            if binding.getter is not None:
                return binding.getter()
            return self._generic_get(binding.widget, binding.default)
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to get value for port '{port_name}': {e}")
            return binding.default

    def get_all_values(self) -> Dict[str, Any]:
        """
        Read all registered widget values at once.

        Returns
        -------
        Dict[str, Any]
            Port name mapped to current value.
        """
        result = {}
        for port_name in self._bindings:
            try:
                result[port_name] = self.get_port_value(port_name)
            except Exception as e:
                log.warning(f"Failed to get value for port '{port_name}': {e}")
                # Use default value
                binding = self._bindings[port_name]
                result[port_name] = binding.default
        return result

    def set_port_value(self, port_name: str, value: Any) -> None:
        """
        Push a value *into* the widget (e.g. when upstream data arrives).

        Signals are blocked during the write to prevent feedback loops.

        Parameters
        ----------
        port_name : str
            Port name to set.
        value : Any
            Value to assign to the widget.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return

        try:
            self._suppress_signals = True
            if binding.setter is not None:
                binding.setter(value)
            else:
                self._generic_set(binding.widget, value)
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to set value for port '{port_name}': {e}")
        finally:
            self._suppress_signals = False

    # ══════════════════════════════════════════════════════════════════════
    # Auto-disable (when an input port gets connected)
    # ══════════════════════════════════════════════════════════════════════

    def set_port_enabled(self, port_name: str, enabled: bool) -> None:
        """
        Enable or disable the widget for *port_name*.

        Called by the node when a trace connects / disconnects to the
        corresponding input port.

        Parameters
        ----------
        port_name : str
            Port name whose widget should be enabled/disabled.
        enabled : bool
            True to enable, False to disable.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return
        binding.widget.setEnabled(enabled)

    def set_all_enabled(self, enabled: bool) -> None:
        """
        Bulk enable / disable every registered widget.

        Parameters
        ----------
        enabled : bool
            True to enable all widgets, False to disable.
        """
        for binding in self._bindings.values():
            binding.widget.setEnabled(enabled)

    # ══════════════════════════════════════════════════════════════════════
    # Serialisation — THE sole source of widget state
    # ══════════════════════════════════════════════════════════════════════

    def get_state(self) -> Dict[str, Any]:
        """
        Persist every registered widget's value into a JSON-safe dict.

        Keys are port names; values are the widget values.
        This is the ONLY mechanism for widget state persistence.
        ``BaseControlNode.get_state()`` calls this and stores the result
        under the ``"widget_data"`` key.

        Returns
        -------
        Dict[str, Any]
            Serialised widget states as port_name → value mapping.
        """
        state: Dict[str, Any] = {}
        for name, binding in self._bindings.items():
            try:
                val = self.get_port_value(name)
                if isinstance(val, (int, float, str, bool, list, dict)):
                    state[name] = val
                elif val is None:
                    state[name] = None
                else:
                    # Convert non-JSON types to string representations
                    state[name] = str(val)
            except Exception as e:
                log.warning(f"Failed to serialize widget state for '{name}': {e}")
                state[name] = binding.default
        return state

    def set_state(self, data: Dict[str, Any]) -> None:
        """
        Restore widget values from a previously saved state dict.

        Signals are suppressed during the entire restore pass.
        ``BaseControlNode.restore_state()`` calls this with the dict
        stored under the ``"widget_data"`` key.

        Parameters
        ----------
        data : Dict[str, Any]
            State dictionary to restore widget values from.
        """
        self._suppress_signals = True
        try:
            for name, value in data.items():
                binding = self._bindings.get(name)
                if binding is None:
                    continue
                try:
                    if binding.setter is not None:
                        binding.setter(value)
                    else:
                        self._generic_set(binding.widget, value)
                except Exception as e:
                    log.warning(
                        f"Failed to restore widget state for '{name}': {e}"
                    )
        finally:
            self._suppress_signals = False

    # ══════════════════════════════════════════════════════════════════════
    # Node back-reference
    # ══════════════════════════════════════════════════════════════════════

    def set_node(self, node: "Node") -> None:
        """
        Stores a back-reference to the owning node.

        Parameters
        ----------
        node : Node
            The node that owns this widget core.
        """
        self._node_ref = node

    @property
    def node(self) -> Optional["Node"]:
        """Return the reference to the parent node."""
        return self._node_ref

    # ══════════════════════════════════════════════════════════════════════
    # Event filter — focus / popup propagation fix
    # ══════════════════════════════════════════════════════════════════════

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """
        Installed on every registered widget.

        On ``FocusIn``: ensures the QGraphicsProxyWidget also claims
        scene focus so the canvas state machine does not steal
        subsequent key / mouse events.

        Parameters
        ----------
        obj : QObject
            The object that received the event.
        event : QEvent
            The Qt event to process.

        Returns
        -------
        bool
            False always (let events propagate).
        """
        if event.type() == QEvent.Type.FocusIn:
            proxy = self._find_proxy()
            if proxy is not None:
                try:
                    proxy.setFocus(Qt.FocusReason.MouseFocusReason)
                    scene = proxy.scene()
                    if scene is not None:
                        scene.setFocusItem(proxy, Qt.FocusReason.MouseFocusReason)
                except Exception as e:
                    log.warning(f"Failed to set focus on proxy: {e}")
        
        return False  # never block — let events propagate

    # ══════════════════════════════════════════════════════════════════════
    # PRIVATE: generic widget readers / writers
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _generic_get(widget: QWidget, default: Any = None) -> Any:
        """Read a value from a standard Qt widget."""
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            if data is not None:
                return data
            return widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QAbstractSlider):
            return widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return widget.toPlainText()
        if isinstance(widget, QLabel):
            return widget.text()
        if hasattr(widget, 'value') and callable(widget.value):
            return widget.value()
        return default

    @staticmethod
    def _generic_set(widget: QWidget, value: Any) -> None:
        """Write a value to a standard Qt widget (signals blocked externally)."""
        was_blocked = widget.signalsBlocked()
        widget.blockSignals(True)
        try:
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value) if value is not None else 0.0)
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value) if value is not None else 0)
            elif isinstance(widget, QComboBox):
                if isinstance(value, int):
                    widget.setCurrentIndex(value)
                else:
                    idx = widget.findText(str(value))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                    elif widget.isEditable():
                        widget.setEditText(str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QAbstractSlider):
                widget.setValue(int(value) if value is not None else 0)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value) if value is not None else "")
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(value) if value is not None else "")
            elif isinstance(widget, QPlainTextEdit):
                widget.setPlainText(str(value) if value is not None else "")
            elif isinstance(widget, QLabel):
                widget.setText(str(value) if value is not None else "")
            elif hasattr(widget, 'setValue') and callable(widget.setValue):
                widget.setValue(value)
        finally:
            widget.blockSignals(was_blocked)

    # ── Signal auto-detection and wiring ─────────────────────────────────

    _SIGNAL_MAP: Dict[type, str] = {
        QDoubleSpinBox: "valueChanged",
        QSpinBox:       "valueChanged",
        QComboBox:      "currentIndexChanged",
        QCheckBox:      "stateChanged",
        QSlider:        "valueChanged",
        QLineEdit:      "textChanged",
        QTextEdit:      "textChanged",
        QPlainTextEdit: "textChanged",
    }

    def _connect_change_signal(self, binding: WidgetBinding) -> None:
        """Auto-detect and connect the widget's change signal."""
        sig_name = binding.change_signal_name

        if sig_name is None:
            for cls, name in self._SIGNAL_MAP.items():
                if isinstance(binding.widget, cls):
                    sig_name = name
                    break

        if sig_name is None:
            return

        try:
            sig = getattr(binding.widget, sig_name)
            if not callable(sig):
                return
                
            binding._connected_signal = sig_name
            port_name = binding.port_name

            def _on_change(*_args, _pn=port_name):
                if not self._suppress_signals:
                    self.value_changed.emit(_pn)

            binding._slot_ref = _on_change  # prevent GC
            sig.connect(_on_change)
        except Exception as e:
            log.warning(f"Failed to connect signal for widget {binding.port_name}: {e}")

    def _disconnect_change_signal(self, binding: WidgetBinding) -> None:
        """Disconnect the previously connected change signal."""
        if binding._connected_signal is None:
            return
        try:
            sig = getattr(binding.widget, binding._connected_signal)
            slot = getattr(binding, '_slot_ref', None)
            if slot is not None:
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass  # Already disconnected or doesn't exist
        except Exception as e:
            log.warning(f"Failed to disconnect signal for widget {binding.port_name}: {e}")
        binding._connected_signal = None

    # ══════════════════════════════════════════════════════════════════════
    # Convenience: iterate bindings
    # ══════════════════════════════════════════════════════════════════════

    def bindings(self) -> Dict[str, WidgetBinding]:
        """Return a *copy* of the internal bindings dict."""
        return dict(self._bindings)

    def has_binding(self, port_name: str) -> bool:
        """
        Check if a binding exists for the given port name.

        Parameters
        ----------
        port_name : str
            Port name to check.

        Returns
        -------
        bool
            True if bound, False otherwise.
        """
        return port_name in self._bindings

    def get_binding(self, port_name: str) -> Optional[WidgetBinding]:
        """
        Get a binding by port name.

        Parameters
        ----------
        port_name : str
            Port name to retrieve.

        Returns
        -------
        WidgetBinding or None
            The binding if found.
        """
        return self._bindings.get(port_name)

    def get_widget(self, port_name: str) -> Optional[QWidget]:
        """
        Shortcut to retrieve the QWidget for a port name.

        Parameters
        ----------
        port_name : str
            Port name whose widget to retrieve.

        Returns
        -------
        QWidget or None
            The widget if found.
        """
        binding = self._bindings.get(port_name)
        return binding.widget if binding is not None else None

    # ══════════════════════════════════════════════════════════════════════
    # Cleanup
    # ══════════════════════════════════════════════════════════════════════

    def cleanup(self) -> None:
        """
        Disconnect all signals, remove event filters, null out references.
        Call from the node's ``cleanup()`` method.
        """
        for binding in list(self._bindings.values()):
            self._disconnect_change_signal(binding)
            try:
                binding.widget.removeEventFilter(self)
            except Exception as e:
                log.warning(f"Failed to remove event filter: {e}")

        self._bindings.clear()
        self._widget_to_port.clear()
        self._node_ref = None
