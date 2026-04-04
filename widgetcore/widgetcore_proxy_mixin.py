# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

widgets._proxy — Mixin handling QGraphicsProxyWidget interaction.

Encapsulates:
- Proxy discovery (walking the parent chain).
- Interactive-flag patching (focus, hover, input method).
- Coordinate mapping (widget-local → scene → viewport → screen).
- Scene-focus forwarding (eventFilter on FocusIn).
- Undo/redo interception (eventFilter on KeyPress Ctrl+Z).
- ``is_interactive_at`` / ``activate_at`` hit-testing for the canvas
  state machine.
"""

from __future__ import annotations

from typing import Optional, Union

from PySide6.QtCore import Qt, QEvent, QObject, QPoint, QPointF, QTimer
from PySide6.QtWidgets import (
    QWidget, QGraphicsProxyWidget, QGraphicsItem,
)

from weave.logger import get_logger

log = get_logger("ProxyMixin")


class ProxyMixin:
    """Mixin for any QWidget that lives inside a QGraphicsProxyWidget.

    Expects ``self`` to be a QWidget subclass.  All methods are safe to
    call when no proxy exists (e.g. widget used outside a scene).
    """

    # Set by the subclass; proxy patching retries once on first miss.
    _proxy_patch_retried: bool = False

    # ── Discovery ────────────────────────────────────────────────────────

    def _find_proxy(self: QWidget) -> Optional[QGraphicsProxyWidget]:
        """Walk up the parent chain to find the hosting proxy."""
        widget: Optional[QWidget] = self
        while widget is not None:
            proxy = widget.graphicsProxyWidget()
            if proxy is not None:
                return proxy
            widget = widget.parentWidget()
        return None

    def get_proxy(self: QWidget) -> Optional[QGraphicsProxyWidget]:
        """Public accessor for the hosting QGraphicsProxyWidget."""
        return self._find_proxy()

    # ── Patching ─────────────────────────────────────────────────────────

    def patch_proxy(self: QWidget) -> bool:
        """Apply correct flags to the proxy.  Public entry point.

        Returns True if the proxy was found and patched.
        """
        return self._patch_parent_proxy()

    def _patch_parent_proxy(self: QWidget) -> bool:
        """Internal proxy-patching logic.

        Sets interactive flags (focusable, hover, input-method) on the
        QGraphicsProxyWidget.  Called via ``QTimer.singleShot(0)`` from
        ``__init__`` and retries once if the proxy is not yet available.

        The theming half (Fusion style + palette) is handled by
        ``ThemeMixin._apply_full_proxy_theme`` which the core calls
        after this method succeeds.

        Returns True if patched, False if proxy not yet ready.
        """
        proxy = self._find_proxy()
        if proxy is None:
            if not self._proxy_patch_retried:
                self._proxy_patch_retried = True
                QTimer.singleShot(50, self._patch_parent_proxy)
            return False

        # Interactive flags
        proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        proxy.setAcceptHoverEvents(True)
        proxy.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemAcceptsInputMethod, True,
        )
        proxy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        log.debug(
            f"_patch_parent_proxy: proxy patched.  "
            f"root={type(proxy.widget()).__name__ if proxy.widget() else 'None'}, "
            f"root is self: {proxy.widget() is self}"
        )
        return True

    # ── Hit-testing ──────────────────────────────────────────────────────

    def is_interactive_at(self: QWidget, scene_pos: QPointF) -> bool:
        """True if *scene_pos* lands on an interactive child widget.

        A widget is interactive when its ``focusPolicy`` is anything
        other than ``NoFocus``.
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

    def activate_at(self: QWidget, scene_pos: QPointF) -> bool:
        """Directly activate the widget under *scene_pos*.

        If the widget (or an ancestor inside the core) exposes a
        ``show_popup(global_pos)`` method, the popup is opened with
        correctly mapped coordinates.

        Returns True if a popup was triggered, False otherwise.
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
            if hasattr(target, "show_popup") and callable(target.show_popup):
                global_pos = self._widget_to_global(
                    target, target.rect().bottomLeft(), proxy,
                )
                target.show_popup(global_pos)
                return True
            target = target.parentWidget()

        return False

    # ── Coordinate mapping ───────────────────────────────────────────────

    def _widget_to_global(
        self: QWidget,
        widget: QWidget,
        local_pos: Union[QPoint, QPointF],
        proxy: QGraphicsProxyWidget,
    ) -> QPoint:
        """Map *local_pos* in *widget*'s space to global screen coords.

        Chain: widget-local → proxy root → scene → view viewport → screen.
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

    # ── Event filter (focus + undo interception) ─────────────────────────

    def _proxy_event_filter(self: QWidget, obj: QObject, event: QEvent) -> bool:
        """Handle FocusIn and Ctrl+Z interception for proxy widgets.

        Returns True if the event was consumed, False otherwise.
        Called from WidgetCore.eventFilter().
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

        elif event.type() == QEvent.Type.KeyPress:
            from PySide6.QtGui import QKeyEvent
            from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox

            ke: QKeyEvent = event  # type: ignore[assignment]
            mod = ke.modifiers()
            ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mod & Qt.KeyboardModifier.ShiftModifier)
            alt = bool(mod & Qt.KeyboardModifier.AltModifier)

            if ctrl and not alt and ke.key() == Qt.Key.Key_Z:
                # Do not hijack Ctrl+Z if the user is typing in a text field —
                # let the native Qt widget handle its own local undo/redo (§4).
                if (getattr(obj, 'hasFocus', lambda: False)()
                        and isinstance(obj, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))):
                    return False

                proxy = self._find_proxy()
                if proxy is not None:
                    scene = proxy.scene()
                    if scene is not None:
                        provider = getattr(scene, "_context_menu_provider", None)
                        if provider is not None:
                            try:
                                if shift:
                                    provider.cmd_redo()
                                else:
                                    provider.cmd_undo()
                            except Exception as e:
                                log.warning(f"Undo/redo via eventFilter failed: {e}")
                            return True  # consume

        return False  # never block other events
