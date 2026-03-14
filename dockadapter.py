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
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QWidget, QDockWidget, QGraphicsScene

if TYPE_CHECKING:
    from weave.node.node_core import Node

from weave.logger import get_logger
from weave.panel.mirror_factories import MirrorFactory
from weave.panel.node_panel import NodePanel

log = get_logger("NodeDockAdapter")


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════

class DockMode(Enum):
    """Operating mode of a ``NodeDockAdapter``."""
    DYNAMIC = auto()   # Follows canvas selection
    STATIC  = auto()   # Pinned to a specific node


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
