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
                        └── **WidgetCore** ← set via set_content_widget()
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

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union
)

from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer, QPoint, QPointF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLayout, QGraphicsProxyWidget, QGraphicsItem, QStyleFactory,
    QApplication,
    # Supported auto-read/write widget types
    QAbstractSpinBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QCheckBox, QAbstractSlider, QSlider,
    QLabel, QPushButton,
)

from PySide6.QtGui import QPalette, QColor

if TYPE_CHECKING:
    from weave.node.node_core import Node
from weave.stylemanager import StyleManager, StyleCategory
from weave.themes.palette_bridge import (
    resolve_theme_colors, resolve_node_colors, build_theme_palette, ThemeColors,
)
from weave.panel.mirror_factories import get_custom_signal_name as _get_custom_signal_name
from weave.logger import get_logger

log = get_logger("WeaveWidgetCore")


# ══════════════════════════════════════════════════════════════════════════════
# Proxy-safe base style
# ══════════════════════════════════════════════════════════════════════════════
# QGraphicsProxyWidget creates an isolated rendering context: embedded
# widgets do NOT inherit QApplication.style().  Platform-native styles
# (Windows 11, macOS Aqua) ignore QPalette for most roles.
#
# Fusion is the only built-in Qt style that honours QPalette completely.
# We create a single shared instance and apply it to the WidgetCore
# container (children inherit via Qt's parent-chain lookup).

PROXY_WIDGET_STYLE: str = "Fusion"
"""Qt style name applied to widgets inside nodes.  Must be a style that
fully honours ``QPalette``.  Change this before creating any nodes if you
need a different style (e.g. ``"Windows"`` for testing)."""

_fusion_style = None


def _get_proxy_style():
    """Return a cached QStyle instance for use inside proxy widgets."""
    global _fusion_style
    if _fusion_style is None:
        _fusion_style = QStyleFactory.create(PROXY_WIDGET_STYLE)
        if _fusion_style is None:
            log.warning(
                f"QStyleFactory could not create '{PROXY_WIDGET_STYLE}' style. "
                f"Available styles: {QStyleFactory.keys()}. "
                f"Widget theming inside nodes may not work correctly."
            )
        else:
            # QStyleFactory.create() doesn't set objectName, which makes
            # debugging confusing (style().objectName() returns "").
            _fusion_style.setObjectName("fusion")
    return _fusion_style


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
    _slot_ref: Optional[Callable[..., None]] = field(default=None, repr=False)


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

    value_changed = Signal(str)  # port_name — user edits (suppressed during set_port_value)
    port_value_written = Signal(str)  # port_name — every programmatic write via set_port_value
    port_enabled_changed = Signal(str, bool)  # (port_name, enabled) — auto-disable on connect/disconnect

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
        self._suppress_depth: int = 0                    # reentrant signal-suppress counter
        self._node_ref: Optional["Node"] = None          # back-reference

        # ── Layout ───────────────────────────────────────────────────────
        if layout is None:
            layout = QVBoxLayout()
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)
        self.setLayout(layout)

        # ── Background matches the node body colour ─────────────────────
        self.setAutoFillBackground(True)

        # ── Subscribe to StyleManager for theme-driven palette updates ───
        sm = StyleManager.instance()
        sm.register(self, StyleCategory.NODE)

        # Apply Fusion style + node body palette immediately so the
        # container is painted correctly from the first frame.
        # _apply_container_background() handles both style and palette.
        self._apply_container_background()

        # ── Deferred proxy patching ──────────────────────────────────────
        QTimer.singleShot(0, self._patch_parent_proxy)

    # ══════════════════════════════════════════════════════════════════════
    # Proxy / Focus Fix
    # ══════════════════════════════════════════════════════════════════════

    def patch_proxy(self) -> bool:
        """
        Apply the correct flags, style, and palette to the hosting
        ``QGraphicsProxyWidget``.

        Called automatically (deferred) from ``__init__``.  Also callable
        explicitly by ``NodeBody`` or ``BaseControlNode`` once the proxy is
        known to exist — this removes the timing dependency entirely::

            core = WidgetCore()
            proxy.setWidget(container)     # proxy now exists
            core.patch_proxy()             # patch immediately, no timer needed

        Returns
        -------
        bool
            True if the proxy was found and patched; False if not yet
            available (caller may retry).
        """
        return self._patch_parent_proxy()

    def _patch_parent_proxy(self) -> bool:
        """
        Walk up the QWidget parent chain to find the QGraphicsProxyWidget
        that hosts us and ensure it has the right flags, style, and palette
        for interactive child widgets (combo-box popups, context menus …).

        This method is the **single authority** for proxy-level setup.  It
        must be called **after** ``set_content_widget()`` because that
        method (via ``NodeBody``) reparents ``WidgetCore`` into a container
        widget inside the proxy.  During that reparent, Qt and/or
        ``NodeBody`` may reset ``WA_SetStyle``, ``WA_SetPalette``, and
        ``autoFillBackground`` on ``WidgetCore`` and its children — wiping
        the Fusion style and body palette that ``__init__`` applied.

        Specifically, this method:

        1. Sets interactive flags on the proxy (focusable, hover, input).
        2. Applies the **Fusion style** to the proxy root *and* WidgetCore
           so that ``QPalette`` roles are honoured in the isolated context.
        3. Clears ``WA_SetPalette`` on all children so they are ready to
           inherit from WidgetCore.
        4. Applies the **node-body palette** to the proxy root (whose
           ``parentWidget()`` is ``None`` → falls back to app palette) and
           to WidgetCore via ``_apply_container_background()``.

        Individual child widgets below WidgetCore are never given explicit
        palettes or styles — they inherit through Qt's parent-chain lookup.

        Retry logic:
            ``_patch_parent_proxy`` is called via ``QTimer.singleShot(0)``
            from ``__init__``.  If the proxy has not been set up yet by
            ``NodeBody`` at that point, ``_find_proxy()`` returns None.
            We retry once after a short delay to handle deferred
            construction sequences.  For reliably deferred construction,
            prefer calling ``patch_proxy()`` explicitly once the proxy is
            attached.

        Returns
        -------
        bool
            True if patched successfully; False if proxy was not yet ready.
        """
        proxy = self._find_proxy()
        if proxy is None:
            if not getattr(self, '_proxy_patch_retried', False):
                self._proxy_patch_retried = True
                QTimer.singleShot(50, self._patch_parent_proxy)
            return False

        # ── 1. Interactive flags ─────────────────────────────────────
        proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        proxy.setAcceptHoverEvents(True)
        proxy.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemAcceptsInputMethod, True
        )
        proxy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ── 2. Fusion style on proxy root ────────────────────────────
        # QGraphicsProxyWidget does NOT inherit QApplication.style().
        # set_content_widget() / NodeBody may also wipe WA_SetStyle
        # during reparenting.
        style = _get_proxy_style()
        root = proxy.widget()

        if root is not None:
            if style is not None:
                root.setStyle(style)
            # The proxy root must NOT fill its background — the
            # QPainter-drawn node body should show through.
            root.setAutoFillBackground(False)

            # Proxy root's parentWidget() is None → palette resolution
            # falls back to QApplication::palette() (canvas colours).
            # Set the node-body palette explicitly.
            if root is not self:
                colors = resolve_theme_colors()
                root.setPalette(build_theme_palette(
                    window_color=colors.body_bg, colors=colors,
                ))

        # ── 3. Clear stale explicit palettes on children ─────────────
        # During reparenting, Qt or NodeBody may call setPalette() on
        # child widgets, setting WA_SetPalette and freezing them to a
        # stale palette (typically black/white defaults).
        #
        # ORDER MATTERS: clear BEFORE setting the palette on WidgetCore.
        # Qt propagates the parent palette to children during
        # setPalette() — but only to children without WA_SetPalette.
        for child in self.findChildren(QWidget):
            try:
                child.setAttribute(
                    Qt.WidgetAttribute.WA_SetPalette, False
                )
            except RuntimeError:
                pass

        # ── 4. Fusion + body palette on WidgetCore ───────────────────
        # Triggers Qt palette propagation to all children (now cleared).
        self._apply_container_background()

        log.debug(
            f"_patch_parent_proxy: proxy patched.  "
            f"root={type(root).__name__ if root else 'None'}, "
            f"root is self: {root is self}"
        )
        return True

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

        A widget is considered interactive when its ``focusPolicy`` is
        anything other than ``Qt.FocusPolicy.NoFocus``.
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
        return child.focusPolicy() != Qt.FocusPolicy.NoFocus

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

    def _widget_to_global(self, widget: QWidget,
                         local_pos: Union[QPoint, QPointF],
                         proxy: QGraphicsProxyWidget) -> QPoint:
        """
        Map *local_pos* in *widget*'s coordinate system to global screen
        coordinates via the full chain::

            widget-local → proxy root → scene → view viewport → screen
        """
        local_point: QPoint = (
            local_pos.toPoint() if isinstance(local_pos, QPointF) else local_pos
        )

        scene = proxy.scene()
        if scene is None or not scene.views():
            return widget.mapToGlobal(local_point)

        views = scene.views()
        view = next((v for v in views if v.isVisible()), views[0])

        proxy_root = proxy.widget()
        if proxy_root is None:
            return widget.mapToGlobal(local_point)

        proxy_pos = widget.mapTo(proxy_root, local_point)
        scene_pos = proxy.mapToScene(QPointF(proxy_pos))
        view_pos = view.mapFromScene(scene_pos)
        return view.viewport().mapToGlobal(view_pos)

    # ══════════════════════════════════════════════════════════════════════
    # Style Syncing (StyleManager → widget QPalettes)
    # ══════════════════════════════════════════════════════════════════════

    def on_style_changed(
        self, category: StyleCategory, changes: Dict[str, Any]
    ) -> None:
        """
        Callback from ``StyleManager`` when the active theme changes.

        Refreshes the palette on WidgetCore and the proxy root.
        Child widgets inherit automatically through Qt's parent-chain.
        """
        if category == StyleCategory.NODE:
            self._apply_container_background()

    def _apply_container_background(self) -> None:
        """
        Apply the Fusion style and node-body palette to WidgetCore.

        Also updates the proxy root widget palette if the proxy exists
        (proxy root's ``parentWidget()`` is ``None``, so it would
        otherwise fall back to ``QApplication::palette()``).

        When a back-reference to the owning node is available, the
        palette is derived from the node's *effective* header and body
        colours (which already include selection highlights and custom
        per-node overrides).  This ensures that deferred calls — e.g.
        from ``_patch_parent_proxy`` via ``QTimer.singleShot(0)`` —
        do not overwrite a node-specific palette with global defaults.

        Children inherit both style and palette via Qt's parent-chain
        lookup — no per-child calls are made.
        """
        # Ensure Fusion is (still) active — reparenting may wipe it.
        style = _get_proxy_style()
        if style is not None:
            self.setStyle(style)

        # Use the owning node's effective colours when available,
        # otherwise fall back to global theme defaults (no node context
        # yet during early __init__).
        node = self._node_ref
        if node is not None and hasattr(node, 'header') and hasattr(node, 'body'):
            colors = resolve_node_colors(
                node.header._bg_color, node.body._bg_color
            )
        else:
            colors = resolve_theme_colors()

        pal = build_theme_palette(
            window_color=colors.body_bg,
            base_palette=self.palette(),
            colors=colors,
        )
        self.setAutoFillBackground(True)
        self.setPalette(pal)

        # Update proxy root palette (parentWidget() is None → would
        # fall back to QApplication canvas palette without this).
        proxy = self._find_proxy()
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                root.setPalette(pal)

    def refresh_widget_palettes(self) -> None:
        """
        Force-refresh palettes on all child widgets.

        .. deprecated::
            Retained for backward compatibility.  Delegates to
            ``_apply_container_background()``; children inherit
            automatically.
        """
        self._apply_container_background()

    def apply_node_palette(
        self,
        header_bg: QColor,
        body_bg: Optional[QColor] = None,
    ) -> None:
        """
        Rebuild the widget palette using the owning node's *actual*
        header and body colours.

        This is called by the node's ``_update_colors()`` whenever the
        effective colours change — including selection highlights and
        custom per-node header colours.  The result is that:

        - ``QPalette.Highlight`` inside spinboxes, combos, line-edits
          etc. matches the node's (possibly custom) header colour rather
          than the global theme default.
        - When the node is selected the ``Window`` / ``Base`` roles
          shift to the highlighted body colour, giving embedded widgets
          a subtle visual cue that mirrors the QPainter-drawn body fill.

        Parameters
        ----------
        header_bg : QColor
            The node's effective header colour (already highlight-shifted
            when the node is selected).
        body_bg : QColor, optional
            The node's effective body colour.  ``None`` keeps the global
            theme ``body_bg``.
        """
        colors = resolve_node_colors(header_bg, body_bg)
        pal = build_theme_palette(
            window_color=colors.body_bg,
            base_palette=self.palette(),
            colors=colors,
        )
        self.setPalette(pal)

        # Proxy root's parentWidget() is None so it would fall back to
        # the application-level palette without an explicit update.
        proxy = self._find_proxy()
        if proxy is not None:
            root = proxy.widget()
            if root is not None and root is not self:
                root.setPalette(pal)

    def refresh_widget_stylesheets(self, *, extra_qss: str = "") -> None:
        """
        Apply a minimal scoped stylesheet for fine details that
        ``QPalette`` cannot control (borders, border-radius, custom
        slider tracks, combo-box drop-down arrows).

        .. note::

           Prefer ``refresh_widget_palettes()`` for 90 % of cases.
           QSS parsing is heavier and can cause micro-stutter when
           nodes are moved/resized at high frame-rates.

        Parameters
        ----------
        extra_qss : str
            Additional QSS rules appended after the auto-generated block.
        """
        c = resolve_theme_colors()

        accent_hex = c.header_bg.name()
        text_hex   = c.body_text.name()
        input_rgba = (
            f"rgba({c.input_bg.red()}, {c.input_bg.green()}, "
            f"{c.input_bg.blue()}, {c.input_bg.alpha()})"
        )

        qss = (
            f"QLineEdit, QSpinBox, QDoubleSpinBox {{\n"
            f"    background-color: {input_rgba};\n"
            f"    border: 1px solid {accent_hex};\n"
            f"    border-radius: 4px;\n"
            f"    color: {text_hex};\n"
            f"    padding: 2px;\n"
            f"}}\n"
            f"QComboBox::drop-down {{\n"
            f"    border-left: 1px solid {accent_hex};\n"
            f"}}\n"
        )
        if extra_qss:
            qss += extra_qss

        self.setStyleSheet(qss)

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
            Must be unique within this core.
        widget : QWidget
            The Qt widget (QSpinBox, QComboBox, QLineEdit, …).
        role : Union[str, PortRole]
            ``"input"``, ``"output"``, ``"bidirectional"``, ``"display"``
            or ``"internal"``.
        datatype : str
            Port datatype string (``"float"``, ``"int"``, ``"string"`` …).
        default : Any
            Default value when nothing is connected.
        description : str
            Port description / tooltip.
        getter : callable, optional
            ``() -> Any`` — custom value reader.
        setter : callable, optional
            ``(value) -> None`` — custom value writer.
        change_signal_name : str, optional
            Signal name on *widget* that fires on edit.  ``None`` = auto.
        add_to_layout : bool
            If True (default), widget is appended to this core's layout.

        Raises
        ------
        ValueError
            When port_name is already registered.
        """
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

        # Let the widget fill its background with the node body colour.
        widget.setAutoFillBackground(True)

        # Do NOT call widget.setStyle() or widget.setPalette() here.
        # The widget inherits both from WidgetCore via parent-chain.

        if add_to_layout and self.layout() is not None:
            self.layout().addWidget(widget)

        self._connect_change_signal(binding)
        widget.installEventFilter(self)

    def unregister_widget(self, port_name: str) -> Optional[QWidget]:
        """Remove a widget binding.  Returns the widget or None."""
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
        """Returns a list describing which ports the node should create."""
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
        """Read the current value of the widget registered to *port_name*."""
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
        """Read all registered widget values at once."""
        return {name: self.get_port_value(name) for name in self._bindings}

    def set_port_value(self, port_name: str, value: Any) -> None:
        """Push a value *into* the widget (signals blocked).

        After the widget is updated, the hosting ``QGraphicsProxyWidget``
        is asked to repaint so that the change is visible immediately —
        even if no other scene event triggers a redraw.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return

        try:
            self._suppress_depth += 1
            if binding.setter is not None:
                binding.setter(value)
            else:
                self._generic_set(binding.widget, value)
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to set value for port '{port_name}': {e}")
        finally:
            self._suppress_depth -= 1

        # Schedule a repaint on the proxy so the updated widget value is
        # composited into the scene immediately.  Without this, changes
        # pushed from upstream or from a mirror panel may only become
        # visible on the next unrelated scene redraw (hover, scroll …).
        proxy = self._find_proxy()
        if proxy is not None:
            proxy.update()

        # Notify external observers (dock panels, mirrors) that the
        # widget value was written.  This is emitted *outside* the
        # suppress guard so it always fires — unlike value_changed
        # which is suppressed during programmatic writes to prevent
        # compute feedback loops.
        self.port_value_written.emit(port_name)

    # ══════════════════════════════════════════════════════════════════════
    # Auto-disable (when an input port gets connected)
    # ══════════════════════════════════════════════════════════════════════

    def set_port_enabled(self, port_name: str, enabled: bool) -> None:
        """Enable or disable the widget for *port_name*."""
        binding = self._bindings.get(port_name)
        if binding is not None:
            binding.widget.setEnabled(enabled)
            self.port_enabled_changed.emit(port_name, enabled)

    def set_all_enabled(self, enabled: bool) -> None:
        """Bulk enable / disable every registered widget."""
        for port_name, binding in self._bindings.items():
            binding.widget.setEnabled(enabled)
            self.port_enabled_changed.emit(port_name, enabled)

    # ══════════════════════════════════════════════════════════════════════
    # Serialisation — THE sole source of widget state
    # ══════════════════════════════════════════════════════════════════════

    def get_state(self) -> Dict[str, Any]:
        """Persist every registered widget's value into a JSON-safe dict."""
        state: Dict[str, Any] = {}
        for name, binding in self._bindings.items():
            try:
                val = self.get_port_value(name)
                if val is None or isinstance(val, (int, float, str, bool, list, dict)):
                    state[name] = val
                else:
                    state[name] = str(val)
            except Exception as e:
                log.warning(f"Failed to serialize widget state for '{name}': {e}")
                state[name] = binding.default
        return state

    def set_state(self, data: Dict[str, Any]) -> None:
        """Restore widget values from a previously saved state dict."""
        self._suppress_depth += 1
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
                    log.warning(f"Failed to restore widget state for '{name}': {e}")
        finally:
            self._suppress_depth -= 1

    # ══════════════════════════════════════════════════════════════════════
    # Node back-reference
    # ══════════════════════════════════════════════════════════════════════

    def set_node(self, node: "Node") -> None:
        """Stores a back-reference to the owning node."""
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
        if isinstance(widget, (QDoubleSpinBox, QSpinBox, QAbstractSlider)):
            return widget.value()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, (QLineEdit, QLabel)):
            return widget.text()
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return widget.toPlainText()
        if hasattr(widget, 'value') and callable(widget.value):
            return widget.value()
        return default

    @staticmethod
    def _generic_set(widget: QWidget, value: Any) -> None:
        """Write a value to a standard Qt widget (signals blocked)."""
        was_blocked = widget.signalsBlocked()
        widget.blockSignals(True)
        try:
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value) if value is not None else 0.0)
            elif isinstance(widget, (QSpinBox, QAbstractSlider)):
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
            elif isinstance(widget, (QLineEdit, QLabel)):
                widget.setText(str(value) if value is not None else "")
            elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
                widget.setPlainText(str(value) if value is not None else "")
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
        """Auto-detect and connect the widget's change signal.

        Resolution order for auto-detection (when ``change_signal_name``
        is ``None``):
        1. Built-in ``_SIGNAL_MAP`` (standard Qt widgets).
        2. Global ``_CUSTOM_SIGNAL_MAP`` from ``mirror_factories``
           (custom widgets registered via ``register_mirror_factory``).
        """
        sig_name = binding.change_signal_name

        if sig_name is None:
            for cls, name in self._SIGNAL_MAP.items():
                if isinstance(binding.widget, cls):
                    sig_name = name
                    break

        # Fallback: consult the global custom-widget signal map.
        if sig_name is None:
            sig_name = _get_custom_signal_name(type(binding.widget))

        if sig_name is None:
            return

        try:
            sig = getattr(binding.widget, sig_name)
            if not callable(sig):
                return

            binding._connected_signal = sig_name
            port_name = binding.port_name

            def _on_change(*_args, _pn=port_name):
                if not self._suppress_depth:
                    self.value_changed.emit(_pn)

            binding._slot_ref = _on_change
            sig.connect(_on_change)
        except Exception as e:
            log.warning(f"Failed to connect signal for widget {binding.port_name}: {e}")

    def _disconnect_change_signal(self, binding: WidgetBinding) -> None:
        """Disconnect the previously connected change signal."""
        if binding._connected_signal is None:
            return
        try:
            sig = getattr(binding.widget, binding._connected_signal)
            if binding._slot_ref is not None:
                try:
                    sig.disconnect(binding._slot_ref)
                except (RuntimeError, TypeError):
                    pass
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
        """Check if a binding exists for the given port name."""
        return port_name in self._bindings

    def get_binding(self, port_name: str) -> Optional[WidgetBinding]:
        """Get a binding by port name."""
        return self._bindings.get(port_name)

    def get_widget(self, port_name: str) -> Optional[QWidget]:
        """Shortcut to retrieve the QWidget for a port name."""
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
        try:
            StyleManager.instance().unregister(self)
        except Exception:
            pass

        for binding in list(self._bindings.values()):
            self._disconnect_change_signal(binding)
            try:
                binding.widget.removeEventFilter(self)
            except Exception as e:
                log.warning(f"Failed to remove event filter: {e}")

        self._bindings.clear()
        self._widget_to_port.clear()
        self._node_ref = None