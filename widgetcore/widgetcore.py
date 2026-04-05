# -*- coding: utf-8 -*-
"""
WidgetCore: Central UI state manager for Weave nodes.
Strictly adheres to PySide6 safety protocols and deterministic undo/redo state.
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

__all__ = ["WidgetCore"]


class WidgetCore(QWidget, ProxyMixin, ThemeMixin):
    """
    Central widget container placed inside a node's body.
    Mandates UUID-based serialization and PySide6 type safety.

    Signals
    -------
    value_changed(str)
        Emitted when a registered widget's value changes (user edits).
        The argument is the *port_name* that changed.
    port_value_written(str, object)
        Emitted on every programmatic write via ``set_port_value`` or 
        ``apply_port_value``.  The second argument is the new value.
    port_enabled_changed(str, bool)
        Emitted when a widget is enabled/disabled (auto-disable on
        connect/disconnect).
    widget_registered(str)
        Emitted after a new widget is registered via ``register_widget()``.
        Dock panels listen to this to add mirrors for dynamically
        created widgets.
    widget_unregistered(str)
        Emitted after a widget is removed via ``unregister_widget()``.
        Dock panels listen to this to remove mirrors for dynamically
        destroyed widgets.
    widget_visibility_changed(str, bool)
        Emitted when a registered widget is directly shown or hidden
        (via ``setVisible()`` / ``show()`` / ``hide()``).  Only *direct*
        calls trigger this — parent-propagated visibility changes
        (e.g. node body collapsed) are intentionally ignored so the
        dock panel stays fully visible regardless of the node's visual
        state on the canvas.
    """

    value_changed = Signal(str)            # Emitted on user interaction
    port_value_written = Signal(str, object) # Emitted on programmatic write (Rule 4)
    port_enabled_changed = Signal(str, bool)
    widget_registered = Signal(str)
    widget_unregistered = Signal(str)
    widget_visibility_changed = Signal(str, bool)

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

    @contextmanager
    def suppress_signals(self):
        """
        Context manager to suppress ``value_changed`` emissions.

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
    # Widget Registration & Lifecycle
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
            log.debug(
                f"register_widget: '{port_name}' already registered — "
                f"returning (idempotent)"
            )
            return

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
                log.debug(f"_on_change → value_changed.emit('{_pn}')"
                          f"  suppress_depth={self._suppress_depth}")
                self.value_changed.emit(_pn)
            else:
                log.debug(f"_on_change SUPPRESSED: '{_pn}'  "
                          f"suppress_depth={self._suppress_depth}")

        connect_change_signal(binding, _on_change)
        widget.installEventFilter(self)

        # Notify listeners (dock panels) about the new widget.
        self.widget_registered.emit(port_name)

    def unregister_widget(self, port_name: str) -> Optional[QWidget]:
        """Remove a widget binding.  Returns the widget or ``None``.

        The widget is disconnected from change-signal monitoring and
        its event filter is removed.  The widget is **not** removed
        from the layout — the caller is responsible for that (e.g.
        ``form.removeRow(widget)``).

        Emits ``widget_unregistered(port_name)`` so dock panels can
        tear down the corresponding mirror widget.
        """
        binding = self._bindings.pop(port_name, None)
        if binding is None:
            return None

        self._widget_to_port.pop(id(binding.widget), None)
        disconnect_change_signal(binding)

        try:
            binding.widget.removeEventFilter(self)
        except RuntimeError:
            pass

        # Notify listeners (dock panels) so they remove the mirror.
        self.widget_unregistered.emit(port_name)

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
    # Value Access (Rule 3 & 4)
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

    def apply_port_value(self, port_name: str, value: Any) -> bool:
        """
        Rule 4: Apply a value directly to a widget, blocking native signals.
        
        Used by UndoManager and Deserializer to set states without triggering
        a cascade of value_changed events and redundant graph evaluations.

        Parameters
        ----------
        port_name : str
            The name of the port whose associated widget will be updated.
        value : Any
            The new value to set on the widget.

        Returns
        -------
        bool
            True if successfully applied, False otherwise.
        """
        binding = self._bindings.get(port_name)
        if not binding: 
            log.debug(f"apply_port_value: '{port_name}' not bound -- skipped")
            return False
            
        # Store previous value for logging purposes only
        previous_value = None
        try:
            if binding.getter is not None:
                previous_value = binding.getter()
            else:
                previous_value = generic_get(binding.widget, binding.default)
        except Exception:
            pass

        log.debug(f"apply_port_value: '{port_name}' = {value!r} "
                  f"(suppress_depth will be {self._suppress_depth + 1})")
        
        # ── THE ARCHITECTURAL FIX: Hold the node's eval fence ──
        # We must hold the fence BEFORE deferring the QTimer to ensure the 
        # UndoManager's quiescence loop waits for all async layout shifts.
        node = self.node
        if node is not None and hasattr(node, '_increment_eval_fence'):
            node._increment_eval_fence()

        # Rule 4: Block ALL signals during the write to prevent race conditions.
        # The widget's native valueChanged signal must not fire during undo/redo
        # because it could create spurious undo commands.
        was_blocked = binding.widget.signalsBlocked()
        try:
            self._suppress_depth += 1
            binding.widget.blockSignals(True)  # BLOCK native widget signals
            if binding.setter is not None:
                binding.setter(value)
            else:
                generic_set(binding.widget, value, block_signals=True)
        except (RuntimeError, AttributeError) as e:
            log.warning(f"Failed to apply value for port '{port_name}': {e}")
            # Release fence on failure
            if node is not None and hasattr(node, '_decrement_eval_fence'):
                node._decrement_eval_fence()
            return False
        finally:
            binding.widget.blockSignals(was_blocked)  # Restore signal state
            self._suppress_depth -= 1

        proxy = self._find_proxy()
        if proxy is not None:
            proxy.update()

        # Rule 4: Defer port_value_written emission to ensure any pending
        # signals from other sources are processed first. This prevents
        # race conditions where a queued signal fires after we think
        # the restore is complete.
        def _emit_port_value_written():
            try:
                self.port_value_written.emit(port_name, value)
                # Log change detection after deferred emission
                try:
                    current_value = self.get_port_value(port_name)
                    if current_value != previous_value:
                        log.debug(f"apply_port_value: value changed {previous_value!r} -> {current_value!r}")
                except Exception:
                    pass
            finally:
                # ── THE ARCHITECTURAL FIX: Release the fence ──
                # Released ONLY after all connected slots (and layout updates) execute.
                if node is not None and hasattr(node, '_decrement_eval_fence'):
                    node._decrement_eval_fence()
        
        QTimer.singleShot(0, _emit_port_value_written)
        return True

    def set_port_value(self, port_name: str, value: Any) -> None:
        """Legacy wrapper; redirects to apply_port_value for safety."""
        self.apply_port_value(port_name, value)

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
    # Event filter — delegates to ProxyMixin + visibility tracking
    # ══════════════════════════════════════════════════════════════════════

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Installed on every registered widget.

        Handles:
        - Focus forwarding and undo interception (via ``ProxyMixin``).
        - Visibility tracking: direct ``Show`` / ``Hide`` events on
          registered widgets emit ``widget_visibility_changed`` so dock
          panels can follow.  ``ShowToParent`` / ``HideToParent`` are
          ignored — those fire when a *parent* changes visibility (e.g.
          node body collapsed on canvas) and the dock panel should remain
          visible in that case.
        """
        et = event.type()

        # ── Visibility tracking ──────────────────────────────────────
        if et == QEvent.Type.Show or et == QEvent.Type.Hide:
            port_name = self._widget_to_port.get(id(obj))
            if port_name is not None:
                self.widget_visibility_changed.emit(
                    port_name, et == QEvent.Type.Show,
                )

        # ── Proxy focus + undo ───────────────────────────────────────
        return self._proxy_event_filter(obj, event)

    # ══════════════════════════════════════════════════════════════════════
    # Cleanup (Rule 7)
    # ══════════════════════════════════════════════════════════════════════

    def cleanup(self) -> None:
        """
        Rule 7: Aggressive Cleanup to prevent memory leaks and phantom evals.
        
        Disconnect all signals, remove event filters, null references,
        and purge internal caches.
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
        log.debug("WidgetCore cleanup: Purged all bindings.")
