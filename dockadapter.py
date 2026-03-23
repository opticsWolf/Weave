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

    The dock title bar shows a generic label (e.g. "Inspector").
    The *panel header* shows the node name.

STATIC
    Permanently bound to one specific node.  If the linked node is
    deleted (removed from the scene or garbage-collected), the dock
    automatically closes and cleans up.  Closing the dock manually
    also unlinks from the node.

    The dock *title bar* shows the **node name** (updated if renamed).
    The panel header's title row is hidden to avoid redundancy.

    If the node class defines a ``dock_properties`` attribute
    (``DockProperties``), those hints are applied to the dock widget
    (allowed areas, size constraints, feature flags).

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

Or register globally so *all* panels can handle the type::

    from weave.panel.mirror_factories import register_mirror_factory
    register_mirror_factory(MyFancyWidget, my_factory, signal_name="valueChanged")

Serialisation
-------------
``get_dock_state()`` / ``restore_dock_state()`` capture and restore the
full geometry, position, floating/docked/minimised state, area, and
visibility so that a workspace serialiser can save and replay the exact
layout.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any, Dict, Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QByteArray, QPoint, QSize
from PySide6.QtWidgets import (
    QWidget, QDockWidget, QGraphicsScene, QMainWindow,
)

if TYPE_CHECKING:
    from weave.node.node_core import Node

from weave.logger import get_logger
from weave.panel.dock_properties import DockProperties
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

    # Emitted when the dock is actually closing (title-bar X on static,
    # or programmatic close).  Listeners should clean up references.
    dock_closed  = Signal()

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
        self._panel.node_unbound.connect(self._on_node_unbound)

        # When the static node is lost, close this dock
        self._panel.linked_node_lost.connect(self._on_linked_node_lost)

        # When the user unpins, re-sync to the current canvas selection
        self._panel.pin_changed.connect(self._on_pin_changed)

        # When the node is renamed in static mode, update the dock title.
        self._panel.dock_title_changed.connect(self._on_dock_title_changed)

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

        The dock's title bar is set to the *node name* (and kept in
        sync if the user renames it).  The *title* parameter is used
        only as a fallback if the node has no readable name.

        If the node class defines ``dock_properties``
        (``DockProperties``), those hints (allowed areas, size
        constraints, feature flags) are applied to the dock widget.

        Parameters
        ----------
        title : str
            Fallback dock title (used when node name is unavailable).
        node : Node
            The node to pin.
        parent : QWidget, optional
            Parent widget (usually the QMainWindow).
        """
        dock = cls(title, parent)
        dock._mode = DockMode.STATIC

        # Apply node-level dock hints *before* binding so size
        # constraints are in effect when the panel is first laid out.
        dock_props = getattr(node, "dock_properties", None)
        if isinstance(dock_props, DockProperties):
            dock._apply_dock_properties(dock_props)

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
            dock_props = getattr(node, "dock_properties", None)
            if isinstance(dock_props, DockProperties):
                self._apply_dock_properties(dock_props)

    def unbind(self) -> None:
        """Unbind from the current node."""
        self._panel.unbind()

    def register_mirror_factory(
        self, widget_type: type, factory: MirrorFactory
    ) -> None:
        """Forward to the inner ``NodePanel``."""
        self._panel.register_mirror_factory(widget_type, factory)

    # ──────────────────────────────────────────────────────────────────────
    # DockProperties application (change #3)
    # ──────────────────────────────────────────────────────────────────────

    def _apply_dock_properties(self, props: DockProperties) -> None:
        """Apply *props* hints to this QDockWidget."""
        # 1. Apply standard QDockWidget features (closable, movable, etc.)
        # Allowed areas
        if props.allowed_areas is not None:
            self.setAllowedAreas(props.allowed_areas)

        # Size constraints on the inner panel widget
        panel = self._panel
        if props.min_width is not None:
            panel.setMinimumWidth(props.min_width)
        if props.max_width is not None:
            panel.setMaximumWidth(props.max_width)
        if props.min_height is not None:
            panel.setMinimumHeight(props.min_height)
        if props.max_height is not None:
            panel.setMaximumHeight(props.max_height)

        # Preferred initial size
        w = props.preferred_width or 0
        h = props.preferred_height or 0
        if w or h:
            self.resize(max(w, self.width()), max(h, self.height()))

        # Feature flags → QDockWidget.DockWidgetFeature flags
        features = self.features()
        if props.closable is not None:
            if props.closable:
                features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
            else:
                features &= ~QDockWidget.DockWidgetFeature.DockWidgetClosable
        if props.movable is not None:
            if props.movable:
                features |= QDockWidget.DockWidgetFeature.DockWidgetMovable
            else:
                features &= ~QDockWidget.DockWidgetFeature.DockWidgetMovable
        if props.floatable is not None:
            if props.floatable:
                features |= QDockWidget.DockWidgetFeature.DockWidgetFloatable
            else:
                features &= ~QDockWidget.DockWidgetFeature.DockWidgetFloatable
        self.setFeatures(features)

        # Title bar visibility
        if props.title_bar_visible is False:
            self.setTitleBarWidget(QWidget())  # empty widget hides the bar
            
        # 2. Apply QMainWindow dock options (tabbed and nested)
        # We must get the top-level main window to set these flags
        main_window = self.window()
        
        if isinstance(main_window, QMainWindow):
            options = main_window.dockOptions()
            
            # Handle Tabbed Docks
            if props.tabbed_dock is False:
                options &= ~QMainWindow.AllowTabbedDocks
            elif props.tabbed_dock is True: 
                options |= QMainWindow.AllowTabbedDocks
                
            # Handle Nested Docks
            if props.nested_dock is False:
                options &= ~QMainWindow.AllowNestedDocks
            elif props.nested_dock is True: 
                options |= QMainWindow.AllowNestedDocks
                
            main_window.setDockOptions(options)

    # ──────────────────────────────────────────────────────────────────────
    # Exposed QDockWidget properties (change #4)
    # ──────────────────────────────────────────────────────────────────────

    def set_allowed_areas(self, areas: Qt.DockWidgetAreas) -> None:
        """Set the allowed dock areas for this dock widget."""
        self.setAllowedAreas(areas)

    def set_features(
        self,
        *,
        closable: Optional[bool] = None,
        movable: Optional[bool] = None,
        floatable: Optional[bool] = None,
    ) -> None:
        """Convenience method to toggle individual dock features.

        Pass only the features you want to change; others stay as-is.
        """
        features = self.features()
        flag = QDockWidget.DockWidgetFeature
        if closable is not None:
            if closable:
                features |= flag.DockWidgetClosable
            else:
                features &= ~flag.DockWidgetClosable
        if movable is not None:
            if movable:
                features |= flag.DockWidgetMovable
            else:
                features &= ~flag.DockWidgetMovable
        if floatable is not None:
            if floatable:
                features |= flag.DockWidgetFloatable
            else:
                features &= ~flag.DockWidgetFloatable
        self.setFeatures(features)

    def set_floating(self, floating: bool) -> None:
        """Programmatically float or dock the widget."""
        self.setFloating(floating)

    def set_title_bar_visible(self, visible: bool) -> None:
        """Show or hide the dock's title bar.

        Hiding the title bar also prevents moving, floating, and closing
        by the user (the dock can still be closed programmatically).
        """
        if visible:
            self.setTitleBarWidget(None)   # restore default title bar
        else:
            self.setTitleBarWidget(QWidget())

    def set_minimum_size(self, width: int, height: int) -> None:
        """Set the minimum size of the dock panel."""
        self._panel.setMinimumSize(width, height)

    def set_maximum_size(self, width: int, height: int) -> None:
        """Set the maximum size of the dock panel."""
        self._panel.setMaximumSize(width, height)

    # ──────────────────────────────────────────────────────────────────────
    # Serialisation (change #5)
    # ──────────────────────────────────────────────────────────────────────

    def get_dock_state(self) -> Dict[str, Any]:
        """Capture the full dock geometry and state for serialisation.

        The returned dict is JSON-safe and contains everything needed
        to recreate the dock in exactly the same position, size, and
        mode when ``restore_dock_state()`` is called after the dock
        has been re-created and added to a ``QMainWindow``.

        The ``"main_window_state"`` key contains the
        ``QMainWindow.saveState()`` bytes (hex-encoded) which stores
        the complete docking layout including tabification and
        split positions.  This should be saved *once per window*, not
        per dock — it is included here for convenience when a single
        dock is serialised in isolation.

        Returns
        -------
        dict
            JSON-safe state dict.
        """
        state: Dict[str, Any] = {
            "mode": self._mode.name,
            "title": self.windowTitle(),
            "visible": self.isVisible(),
            "floating": self.isFloating(),
            "area": self._current_area_name(),
            "features": int(self.features()),
            "allowed_areas": int(self.allowedAreas()),
        }

        # Geometry
        geo = self.geometry()
        state["geometry"] = {
            "x": geo.x(),
            "y": geo.y(),
            "width": geo.width(),
            "height": geo.height(),
        }

        # Size constraints
        state["min_size"] = {
            "width": self._panel.minimumWidth(),
            "height": self._panel.minimumHeight(),
        }
        state["max_size"] = {
            "width": self._panel.maximumWidth(),
            "height": self._panel.maximumHeight(),
        }

        # Floating window position (only meaningful when floating)
        if self.isFloating():
            fgeo = self.frameGeometry()
            state["floating_geometry"] = {
                "x": fgeo.x(),
                "y": fgeo.y(),
                "width": fgeo.width(),
                "height": fgeo.height(),
            }

        # Node UUID (so the deserialiser can rebind static docks)
        node = self._panel.node
        if node is not None and hasattr(node, "get_uuid_string"):
            state["node_uuid"] = node.get_uuid_string()
        else:
            state["node_uuid"] = None

        # Pin state (dynamic mode)
        state["pinned"] = self._panel.is_pinned

        return state

    def restore_dock_state(self, state: Dict[str, Any]) -> None:
        """Restore dock geometry and state from a previously saved dict.

        This method should be called **after** the dock has been added
        to a ``QMainWindow`` (via ``addDockWidget``) so that Qt's
        layout engine is available for area placement.

        Parameters
        ----------
        state : dict
            A dict previously returned by ``get_dock_state()``.
        """
        # Features & allowed areas
        features_int = state.get("features")
        if features_int is not None:
            self.setFeatures(QDockWidget.DockWidgetFeature(features_int))

        areas_int = state.get("allowed_areas")
        if areas_int is not None:
            self.setAllowedAreas(Qt.DockWidgetArea(areas_int))

        # Size constraints
        min_s = state.get("min_size", {})
        if min_s.get("width") or min_s.get("height"):
            self._panel.setMinimumSize(
                min_s.get("width", 0), min_s.get("height", 0)
            )
        max_s = state.get("max_size", {})
        if max_s.get("width") or max_s.get("height"):
            self._panel.setMaximumSize(
                max_s.get("width", 16777215), max_s.get("height", 16777215)
            )

        # Title
        title = state.get("title")
        if title:
            self.setWindowTitle(title)

        # Floating state & geometry
        is_floating = state.get("floating", False)
        self.setFloating(is_floating)

        if is_floating:
            fgeo = state.get("floating_geometry") or state.get("geometry")
            if fgeo:
                self.setGeometry(
                    fgeo["x"], fgeo["y"],
                    fgeo["width"], fgeo["height"],
                )
        else:
            geo = state.get("geometry")
            if geo:
                self.resize(geo["width"], geo["height"])

        # Visibility
        vis = state.get("visible", True)
        self.setVisible(vis)

    def _current_area_name(self) -> str:
        """Return the name of the dock area this widget currently occupies."""
        main_win = self._find_main_window()
        if main_win is not None:
            area = main_win.dockWidgetArea(self)
            return area.name if hasattr(area, "name") else str(int(area))
        return "unknown"

    def _find_main_window(self) -> Optional[QMainWindow]:
        """Walk up the parent chain to find the hosting QMainWindow."""
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QMainWindow):
                return parent
            parent = parent.parentWidget()
        return None

    @staticmethod
    def save_main_window_dock_layout(main_window: QMainWindow) -> str:
        """Save the full dock layout of *main_window* as a hex string.

        This captures *all* dock widgets' positions, sizes, tabification,
        and split ratios in a single blob that can be restored with
        ``restore_main_window_dock_layout()``.

        Usage::

            layout_hex = NodeDockAdapter.save_main_window_dock_layout(win)
            # persist layout_hex to file / database

        Returns
        -------
        str
            Hex-encoded ``QByteArray`` of ``QMainWindow.saveState()``.
        """
        return main_window.saveState().toHex().data().decode("ascii")

    @staticmethod
    def restore_main_window_dock_layout(
        main_window: QMainWindow, hex_state: str
    ) -> bool:
        """Restore a previously saved dock layout.

        Parameters
        ----------
        main_window : QMainWindow
            The window whose layout to restore.
        hex_state : str
            Hex-encoded state previously returned by
            ``save_main_window_dock_layout()``.

        Returns
        -------
        bool
            True if the state was restored successfully.
        """
        ba = QByteArray.fromHex(hex_state.encode("ascii"))
        return main_window.restoreState(ba)

    # ──────────────────────────────────────────────────────────────────────
    # Close event
    # ──────────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """
        Handle the dock being closed by the user or programmatically.

        Both modes unbind from the node / selection, emit ``dock_closed``
        so external managers can remove their references, and schedule
        the dock for deletion.

        DYNAMIC
            Mirrors are torn down, selection tracking is stopped, and
            the dock is destroyed.  A new inspector can be created via
            "Add Inspector".

        STATIC
            The static lock is released and the dock is destroyed.
        """
        if self._mode == DockMode.STATIC:
            self._panel.unbind()
        else:
            self._panel._unbind_internal()

        self._unbind_from_selection()
        self.dock_closed.emit()
        self.deleteLater()

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
        """Handle canvas ``selectionChanged`` signal.

        The sync is deferred to the next event-loop tick via
        ``QTimer.singleShot(0, ...)``.  ``selectionChanged`` fires
        inside ``QGraphicsScene.mousePressEvent``; if ``bind_node``
        runs synchronously it can call ``QWidget.setVisible`` which
        forces Qt to process pending events — delivering a queued
        ``mouseMoveEvent`` while the press handler is still on the
        stack.  Because the drag offset is not yet finalized at that
        point, the node jumps to an incorrect position.

        Deferring the sync breaks the re-entrant chain without
        affecting user-perceived responsiveness (the panel update
        appears on the very next tick).
        """
        if self._mode != DockMode.DYNAMIC:
            return
        if not self.isVisible():
            return
        QTimer.singleShot(0, self._sync_to_current_selection)

    def _sync_to_current_selection(self) -> None:
        """Read the current selection and bind/unbind accordingly.

        Skipped entirely when the panel is *pinned* — the user has
        explicitly locked it to a specific node via the pin toggle.
        """
        if self._selection_scene is None:
            return

        # A pinned panel ignores selection until the user unpins.
        if self._panel.is_pinned:
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
    # Pin toggle — re-sync on unpin
    # ──────────────────────────────────────────────────────────────────────

    @Slot(bool)
    def _on_pin_changed(self, pinned: bool) -> None:
        """Handle the panel's pin toggle.

        When the user *unpins*, immediately re-sync to whatever node is
        currently selected on the canvas.  This covers the common case
        where the user pinned Node A, selected Node B, then unpinned —
        the panel should switch to Node B without requiring a fresh
        click.  ``_sync_to_current_selection`` is safe to call here
        because ``is_pinned`` has already been cleared by the panel
        before the signal was emitted.
        """
        if not pinned and self._mode == DockMode.DYNAMIC:
            self._sync_to_current_selection()

    # ──────────────────────────────────────────────────────────────────────
    # Dynamic-mode: node unbound (e.g. pinned node deleted)
    # ──────────────────────────────────────────────────────────────────────

    @Slot()
    def _on_node_unbound(self) -> None:
        """The panel's node was unbound.
    
        For dynamic docks this can happen when a pinned node is deleted.
        The panel has already cleared itself and reset ``is_pinned`` to
        False, so we re-sync to whatever is currently selected on the
        canvas — the dock stays open and resumes following selection.
    
        Static docks never reach this path because node deletion
        triggers ``linked_node_lost`` → ``_on_linked_node_lost`` instead.
        """
        if self._mode == DockMode.DYNAMIC and self._selection_scene is not None:
            # Defer — just like _on_selection_changed does — so that this
            # slot cannot re-enter bind_node while a bind is already in
            # progress (e.g. the outer bind_node calls _unbind_internal
            # which emits node_unbound, which would otherwise trigger a
            # synchronous _sync_to_current_selection in the middle of the
            # outer bind, causing _build_mirrors to run twice).
            QTimer.singleShot(0, self._sync_to_current_selection)
            
    # ──────────────────────────────────────────────────────────────────────
    # Title sync — static dock title follows node name (change #1)
    # ──────────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_dock_title_changed(self, new_title: str) -> None:
        """The node was renamed — update the dock's title bar."""
        self.setWindowTitle(new_title)

    # ──────────────────────────────────────────────────────────────────────
    # Static-mode: linked node lost
    # ──────────────────────────────────────────────────────────────────────

    @Slot()
    def _on_linked_node_lost(self) -> None:
        """The statically bound (mirror) node has been deleted — close the dock.

        This only fires for **static** docks.  Dynamic (inspector) docks
        whose pinned node is deleted receive ``node_unbound`` instead
        and stay open — see ``_on_node_unbound``.
        """
        log.debug("Static dock: linked node deleted — closing dock.")
        self._unbind_from_selection()
        self.close()
