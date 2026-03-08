# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

proxycombobox.py
-----------------
Defines the shared ``_ProxyGlobalPosMixin`` coordinate helper and the
``ProxyComboBox`` drop-in ``QComboBox`` replacement, both of which are
used by all other proxy-safe widget modules in this package.

Exported names
--------------
``_ProxyGlobalPosMixin``
    Shared mixin that resolves the correct global screen position for any
    popup opened from a widget embedded inside a ``QGraphicsProxyWidget``.
    Import this into any new proxy-safe wrapper::

        from weave.proxycombobox import _ProxyGlobalPosMixin

``ProxyComboBox``
    Drop-in ``QComboBox`` replacement. See class docstring for details.

Usage
-----
::

    from weave.proxycombobox import ProxyComboBox

    combo = ProxyComboBox()
    combo.addItems(["Alpha", "Beta", "Gamma"])

Root cause
----------
Qt's ``QComboBox.showPopup()`` creates a ``QFrame`` sub-proxy for the
item list.  That sub-proxy is constrained to the bounding rect of the
host ``QGraphicsProxyWidget``, so the list is clipped, hidden, or
positioned incorrectly relative to the scene transform.

``ProxyComboBox`` overrides only ``showPopup()`` to open a ``QMenu``
via ``QMenu.exec()`` instead.  ``QMenu.exec()`` creates a fully
independent native top-level window through a separate code path that
Qt handles correctly regardless of proxy embedding, pan, or zoom.

Because ``ProxyComboBox`` is a true ``QComboBox`` subclass, all
standard Qt behaviour is preserved:

  • All signals fire normally (``currentIndexChanged``,
    ``currentTextChanged``, ``activated``, …).
  • ``WidgetCore`` handles it natively via its ``isinstance(w, QComboBox)``
    checks in ``_generic_get``, ``_generic_set``, and ``_SIGNAL_MAP`` —
    no custom getter / setter / change_signal_name overrides needed.
  • Item data (``setItemData`` / ``itemData`` / ``currentData``) works
    as normal; the ``QMenu`` action carries the item index so
    ``setCurrentIndex`` is used to commit the selection, keeping Qt's
    internal model in sync.
"""

from typing import Optional

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QComboBox, QMenu, QWidget


# ══════════════════════════════════════════════════════════════════════════════
# Shared coordinate-mapping mixin
# ══════════════════════════════════════════════════════════════════════════════

class _ProxyGlobalPosMixin:
    """
    Coordinate-mapping helper for widgets embedded in a QGraphicsProxyWidget.

    Mix this into any ``QWidget`` subclass that needs to open a popup
    (``QMenu``, ``QDialog`` …) at the correct global screen position,
    accounting for the scene's pan and zoom transforms.

    ``_ProxyGlobalPosMixin`` must appear before the Qt base class in the
    MRO so Python resolves ``_global_popup_pos`` on the mixin side::

        class MyWidget(_ProxyGlobalPosMixin, QSomeWidget):
            def showPopup(self):
                my_popup.exec(self._global_popup_pos())

    The mixin adds no ``__init__`` and carries no state, so cooperative
    multiple inheritance with any Qt class works without conflict.
    """

    def _global_popup_pos(self, local_point=None):
        """
        Map *local_point* to global screen coordinates through the full
        proxy / scene / view chain.

        Parameters
        ----------
        local_point : QPoint, optional
            A point in this widget's local coordinate system.
            Defaults to ``self.rect().bottomLeft()``.

        Returns
        -------
        QPoint
            The corresponding position in global screen coordinates.

        Notes
        -----
        Coordinate path::

            widget-local
              → proxy-root-local   (via QWidget.mapTo)
              → scene              (via QGraphicsProxyWidget.mapToScene)
              → view viewport      (via QGraphicsView.mapFromScene)
              → screen             (via QWidget.mapToGlobal on the viewport)

        Falls back to ``QWidget.mapToGlobal`` when this widget is not
        embedded inside a ``QGraphicsProxyWidget`` (e.g. during standalone
        testing).
        """
        if local_point is None:
            local_point = self.rect().bottomLeft()

        # Walk up the QWidget parent chain to find the hosting proxy
        proxy = None
        node = self
        while node is not None:
            proxy = node.graphicsProxyWidget()
            if proxy is not None:
                break
            node = node.parentWidget()

        if proxy is None:
            return self.mapToGlobal(local_point)

        scene = proxy.scene()
        if scene is None or not scene.views():
            return self.mapToGlobal(local_point)

        view = scene.views()[0]
        root = proxy.widget()
        if root is None:
            return self.mapToGlobal(local_point)

        # widget-local → proxy-root-local → scene → view-viewport → screen
        root_pos  = self.mapTo(root, local_point)
        scene_pos = proxy.mapToScene(QPointF(root_pos))
        view_pos  = view.mapFromScene(scene_pos)
        return view.viewport().mapToGlobal(view_pos)


# ══════════════════════════════════════════════════════════════════════════════
# ProxyComboBox
# ══════════════════════════════════════════════════════════════════════════════

class ProxyComboBox(_ProxyGlobalPosMixin, QComboBox):
    """
    QComboBox that works correctly inside a ``QGraphicsProxyWidget``.

    Identical to ``QComboBox`` in every way except ``showPopup()``,
    which is overridden to open a styled ``QMenu`` instead of Qt's
    broken sub-proxy dropdown.

    All native ``QComboBox`` signals (``currentIndexChanged``,
    ``currentTextChanged``, ``activated`` …) fire as usual, so
    ``WidgetCore`` handles this class without any custom
    getter / setter / signal overrides.

    Parameters
    ----------
    parent : QWidget, optional
        Standard Qt parent widget.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

    def showPopup(self) -> None:
        """
        Open a ``QMenu`` dropdown instead of Qt's broken sub-proxy popup.

        The menu is positioned at the bottom-left of the widget in global
        screen coordinates via ``_ProxyGlobalPosMixin._global_popup_pos()``.

        Selection commits via ``setCurrentIndex()``, which keeps Qt's
        internal item model fully in sync and fires all the expected
        signals (``currentIndexChanged``, ``currentTextChanged``, …).
        """
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #555;
                padding: 2px;
            }
            QMenu::item {
                padding: 4px 20px 4px 8px;
            }
            QMenu::item:selected {
                background-color: #4a90d9;
            }
        """)

        for i in range(self.count()):
            action = menu.addAction(self.itemText(i))
            action.setData(i)
            if i == self.currentIndex():
                font = action.font()
                font.setBold(True)
                action.setFont(font)

        chosen = menu.exec(self._global_popup_pos())
        if chosen is not None:
            self.setCurrentIndex(chosen.data())
