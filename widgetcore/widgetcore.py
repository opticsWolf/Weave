# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

widgets._core — WidgetCore, the central widget container for node bodies.
=====================================================================

This is the lean orchestrator that composes:
- ``ProxyMixin``  — QGraphicsProxyWidget interaction and focus fixes.
- ``ThemeMixin``  — Palette/style synchronisation with StyleManager.
- ``_adapter``    — Generic widget I/O and signal wiring (strategy).

See the package docstring (``widgets/__init__.py``) for architecture
and public API overview.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLayout

from weave.logger import get_logger
from weave.stylemanager import StyleManager, StyleCategory

from .widgetcore_port_models import PortRole, WidgetBinding, PortDefinition
from .widgetcore_adapter import (
    generic_get, generic_set,
    connect_change_signal, disconnect_change_signal,
)
from .widgetcore_proxy_mixin import ProxyMixin
from .widgetcore_theme_mixin import ThemeMixin

if TYPE_CHECKING:
    from weave.node.node_core import Node

log = get_logger("WidgetCore")


__all__ = [
    "WidgetCore",
    ]

class WidgetCore(QWidget, ProxyMixin, ThemeMixin):
    """Central widget container placed inside a node's body.

    Every BaseControlNode must create and embed a WidgetCore.
    It is the sole owner of widget state serialisation — there is no
    fallback to ``get_widget_state`` / ``set_widget_state``.

    Signals
    -------
    value_changed(str)
        Emitted when a registered widget's value changes (user edits).
        The argument is the *port_name* that changed.
    port_value_written(str)
        Emitted on every programmatic write via ``set_port_value``.
    port_enabled_changed(str, bool)
        Emitted when a widget is enabled/disabled (auto-disable on
        connect/disconnect).
    """

    value_changed = Signal(str)
    port_value_written = Signal(str)
    port_enabled_changed = Signal(str, bool)

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        layout: Optional[QLayout] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        # Registry
        self._bindings: Dict[str, WidgetBinding] = {}
        self._widget_to_port: Dict[int, str] = {}
        self._suppress_depth: int = 0
        self._node_ref: Optional["Node"] = None

        # Layout
        if layout is None:
            layout = QVBoxLayout()
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)
        self.setLayout(layout)

        # WidgetCore itself is transparent — the QPainter-drawn node body
        # (including any state overlay) shows through from the scene canvas.
        # autoFillBackground is explicitly kept False so Qt never paints a
        # solid QPalette.Window fill over the scene rendering.
        self.setAutoFillBackground(False)

        # StyleManager subscription
        sm = StyleManager.instance()
        sm.register(self, StyleCategory.NODE)

        # Initial Fusion + palette (before proxy exists)
        self._apply_container_background()

        # Deferred proxy patching
        QTimer.singleShot(0, self._deferred_proxy_setup)

        # Content-change coalescing
        self._layout_notify_enabled: bool = False
        self._content_change_pending: bool = False

    def _deferred_proxy_setup(self) -> None:
        """Called via QTimer.singleShot(0) — patches proxy then themes it."""
        if self._patch_parent_proxy():
            self._apply_full_proxy_theme()
            self._layout_notify_enabled = True

    # ── Signal suppression context manager ───────────────────────────────

    @contextmanager
    def suppress_signals(self):
        """Context manager to suppress ``value_changed`` emissions.

        Usage::

            with core.suppress_signals():
                core.set_port_value("x", 42)
                core.set_port_value("y", 99)
        """
        self._suppress_depth += 1
        try:
            yield
        finally:
            self._suppress_depth -= 1

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
        """Register *widget* as the UI element for *port_name*.

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
                f"WidgetCore.  Use unregister_widget() first."
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

        widget.setAutoFillBackground(True)

        if add_to_layout and self.layout() is not None:
            self.layout().addWidget(widget)

        # Delegate signal wiring to adapter
        pn = port_name  # capture for closure

        def _on_change(*_args, _pn=pn):
            if not self._suppress_depth:
                self.value_changed.emit(_pn)

        connect_change_signal(binding, _on_change)
        widget.installEventFilter(self)

    def unregister_widget(self, port_name: str) -> Optional[QWidget]:
        """Remove a widget binding.  Returns the widget or None."""
        binding = self._bindings.pop(port_name, None)
        if binding is None:
            return None

        self._widget_to_port.pop(id(binding.widget), None)
        disconnect_change_signal(binding)

        try:
            binding.widget.removeEventFilter(self)
        except RuntimeError:
            pass

        return binding.widget

    # ══════════════════════════════════════════════════════════════════════
    # Port Definitions
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
    # Value Read / Write
    # ══════════════════════════════════════════════════════════════════════

    def get_port_value(self, port_name: str) -> Any:
        """Read the current value of the widget registered to *port_name*.

        Resolution order:
            1. Custom ``getter`` (if provided at registration).
            2. ``generic_get`` auto-detection for standard Qt widgets.
            3. ``binding.default`` if the above returned ``None``.
            4. ``None`` only if the port name is not registered.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return None

        try:
            if binding.getter is not None:
                val = binding.getter()
            else:
                val = generic_get(binding.widget, binding.default)
            return val if val is not None else binding.default
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to get value for port '{port_name}': {e}")
            return binding.default

    def get_all_values(self) -> Dict[str, Any]:
        """Read all registered widget values at once."""
        return {name: self.get_port_value(name) for name in self._bindings}

    def set_port_value(self, port_name: str, value: Any) -> None:
        """Push a value *into* the widget (signals blocked).

        After the write, the proxy is asked to repaint so the change
        is visible immediately.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return

        try:
            self._suppress_depth += 1
            if binding.setter is not None:
                binding.setter(value)
            else:
                generic_set(binding.widget, value)
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to set value for port '{port_name}': {e}")
        finally:
            self._suppress_depth -= 1

        proxy = self._find_proxy()
        if proxy is not None:
            proxy.update()

        self.port_value_written.emit(port_name)

    def apply_port_value(self, port_name: str, value: Any) -> None:
        """Set a widget value, allowing the widget's native signal to fire.

        Unlike ``set_port_value`` (which blocks the widget's own signals),
        this lets the native signal propagate so node-internal handlers
        execute their side-effects.  WidgetCore's ``value_changed`` is
        still suppressed.

        Use from undo/redo command paths.
        """
        binding = self._bindings.get(port_name)
        if binding is None:
            return

        try:
            self._suppress_depth += 1
            if binding.setter is not None:
                binding.setter(value)
            else:
                generic_set(binding.widget, value, block_signals=False)
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to apply value for port '{port_name}': {e}")
        finally:
            self._suppress_depth -= 1

        proxy = self._find_proxy()
        if proxy is not None:
            proxy.update()

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
        with self.suppress_signals():
            for name, value in data.items():
                binding = self._bindings.get(name)
                if binding is None:
                    continue
                try:
                    if binding.setter is not None:
                        binding.setter(value)
                    else:
                        generic_set(binding.widget, value)
                except Exception as e:
                    log.warning(f"Failed to restore widget state for '{name}': {e}")

    # ══════════════════════════════════════════════════════════════════════
    # Node back-reference
    # ══════════════════════════════════════════════════════════════════════

    def set_node(self, node: "Node") -> None:
        """Store a back-reference to the owning node."""
        self._node_ref = node

    @property
    def node(self) -> Optional["Node"]:
        """Return the reference to the parent node."""
        return self._node_ref

    # ══════════════════════════════════════════════════════════════════════
    # Binding access
    # ══════════════════════════════════════════════════════════════════════

    def bindings(self) -> Dict[str, WidgetBinding]:
        """Return a *copy* of the internal bindings dict."""
        return dict(self._bindings)

    def has_binding(self, port_name: str) -> bool:
        return port_name in self._bindings

    def get_binding(self, port_name: str) -> Optional[WidgetBinding]:
        return self._bindings.get(port_name)

    def get_widget(self, port_name: str) -> Optional[QWidget]:
        """Shortcut to retrieve the QWidget for a port name."""
        binding = self._bindings.get(port_name)
        return binding.widget if binding is not None else None

    # ══════════════════════════════════════════════════════════════════════
    # Content-change coalescing
    # ══════════════════════════════════════════════════════════════════════

    def event(self, ev: QEvent) -> bool:
        """Intercept ``LayoutRequest`` to auto-resize the parent node.

        Qt fires LayoutRequest whenever a child widget is shown, hidden,
        added, removed, or changes its size hint.  We coalesce into one
        ``notify_content_changed()`` call per event-loop tick.
        """
        if (
            ev.type() == QEvent.Type.LayoutRequest
            and self._layout_notify_enabled
            and not self._content_change_pending
        ):
            node = self._node_ref
            if node is not None and hasattr(node, "notify_content_changed"):
                self._content_change_pending = True
                QTimer.singleShot(0, self._flush_content_change)
        return super().event(ev)

    def _flush_content_change(self) -> None:
        """Coalesced callback — notify the node exactly once per tick."""
        self._content_change_pending = False
        if not self._layout_notify_enabled:
            return
        node = self._node_ref
        if node is not None and hasattr(node, "notify_content_changed"):
            try:
                node.notify_content_changed()
            except RuntimeError:
                pass  # C++ object deleted

    def suppress_content_notify(self) -> None:
        """Temporarily disable automatic content-change notifications."""
        self._layout_notify_enabled = False

    def resume_content_notify(self, notify: bool = True) -> None:
        """Re-enable automatic content-change notifications.

        If *notify* is True, immediately fires one
        ``notify_content_changed`` to catch anything that happened
        while suppressed.
        """
        self._layout_notify_enabled = True
        self._content_change_pending = False
        if notify:
            self._flush_content_change()

    # ══════════════════════════════════════════════════════════════════════
    # Event filter — delegates to ProxyMixin
    # ══════════════════════════════════════════════════════════════════════

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Installed on every registered widget.

        Delegates focus and undo handling to ``ProxyMixin._proxy_event_filter``.
        """
        return self._proxy_event_filter(obj, event)

    # ══════════════════════════════════════════════════════════════════════
    # Cleanup
    # ══════════════════════════════════════════════════════════════════════

    def cleanup(self) -> None:
        """Disconnect all signals, remove event filters, null references.

        Call from the node's ``cleanup()`` method.
        """
        self._layout_notify_enabled = False
        self._content_change_pending = False

        try:
            StyleManager.instance().unregister(self)
        except Exception:
            pass

        for binding in list(self._bindings.values()):
            disconnect_change_signal(binding)
            try:
                binding.widget.removeEventFilter(self)
            except Exception as e:
                log.warning(f"Failed to remove event filter: {e}")

        self._bindings.clear()
        self._widget_to_port.clear()
        self._node_ref = None
