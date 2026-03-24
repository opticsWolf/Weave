# -*- coding: utf-8 -*-
"""
Weave: NodeDockAdapter usage example
=====================================

Demonstrates a **fully-docked** layout (Pattern 2):

- A minimal zero-size placeholder serves as QMainWindow's required
  central widget.
- The **canvas** lives inside a ``QDockWidget`` — non-closable, but
  floatable and movable.
- A **logger pane** at the bottom displays live log output.
  Non-closable, floatable, restricted to top / bottom dock areas.
- Additional inspector / pinned docks can be arranged freely.
"""

import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QDockWidget, QWidget
import os
os.environ["WEAVE_UNDO_DEBUG"] = "1"

def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # Optional: skin the whole app to match the active Weave theme.
    try:
        from weave.themes.theme_bridge import AppThemeBridge
        theme_bridge = AppThemeBridge()
    except ImportError:
        theme_bridge = None

    # ── Scene + View ─────────────────────────────────────────────────
    scene = Canvas()
    view = CanvasView(scene)

    minimap = CanvasMinimap(view, parent=view)
    minimap.set_config(width=220, height=160)
    minimap.show()

    # ── Main window ──────────────────────────────────────────────────
    win = QMainWindow()
    win.setWindowTitle("Weave — Dock Adapter Demo")

    # Pattern 2: zero-size hidden placeholder as central widget.
    _placeholder = QWidget()
    _placeholder.setFixedSize(0, 0)
    win.setCentralWidget(_placeholder)

    # ── Canvas dock — main view ──────────────────────────────────────
    canvas_dock = QDockWidget("Canvas", win)
    canvas_dock.setWidget(view)
    canvas_dock.setFeatures(
        QDockWidget.DockWidgetFeature.DockWidgetMovable
        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
    )
    canvas_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
    win.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, canvas_dock)

    # ── Logger dock — bottom ─────────────────────────────────────────
    log_dock = QDockWidget("Log", win)
    log_dock.setWidget(LogPane())
    log_dock.setFeatures(
        QDockWidget.DockWidgetFeature.DockWidgetMovable
        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
    )
    log_dock.setAllowedAreas(
        Qt.DockWidgetArea.TopDockWidgetArea
        | Qt.DockWidgetArea.BottomDockWidgetArea
    )
    win.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, log_dock)

    win.resize(1400, 900)

    # ── 1) Dynamic dock — follows canvas selection (right side) ──────
    #inspector = NodeDockAdapter.create_dynamic(
    #    "Inspector", scene, parent=win
    #)
    #win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector)

    # ── 2) Static dock — pinned to a specific node (left side) ───────
    #
    #   from weave.example_nodes.simple_nodes import FloatNode
    #
    #   pinned_node = FloatNode(title="Pinned Float")
    #   scene.addItem(pinned_node)
    #   pinned_node.setPos(-300, 0)
    #
    #   pinned_dock = NodeDockAdapter.create_static(
    #       "Pinned: Float", pinned_node, parent=win
    #   )
    #   win.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, pinned_dock)

    # ── Show ─────────────────────────────────────────────────────────
    view.centerOn(0, 0)
    win.show()

    from weave.logger import get_logger
    log = get_logger("DockDemo")
    log.info("Dock Adapter Demo started (Pattern 2: fully-docked)")
    log.info("Canvas dock: non-closable | floatable | all areas")
    log.info("Logger dock: non-closable | floatable | top/bottom only")

    sys.exit(app.exec())


if __name__ == "__main__":
    # ── Weave imports ────────────────────────────────────────────────
    from weave.canvas import Canvas
    from weave.canvas.canvas_view import CanvasView
    from weave.canvas.canvas_minimap import CanvasMinimap
    from weave.dockadapter import NodeDockAdapter, DockMode
    from weave.logpane import LogPane

    from weave.default_nodes import *          # register nodes
    main()