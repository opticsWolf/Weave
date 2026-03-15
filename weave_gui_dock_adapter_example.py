# -*- coding: utf-8 -*-
"""
Weave: NodeDockAdapter usage example
=====================================

Demonstrates both dock modes:

1. **Dynamic** — an inspector panel on the right that automatically shows
   whichever node is selected.  Close it → hides.  Reopen from the
   Window menu → resumes following selection.

2. **Static** — a pinned panel on the left permanently bound to one
   specific node.  Delete the node → the dock auto-closes.
   Close the dock manually → unlinks from the node.

Both panels have an "Unlink" button in the header to manually
disconnect from the node.
"""

import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    #app.setStyle("Fusion")

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
    win.setCentralWidget(view)
    win.resize(1400, 900)

    # ── 1) Dynamic dock — follows canvas selection (right side) ──────
    #
    # Click a node to inspect it.  Click empty space to clear.
    # Close the dock → it hides.  Reopen from View > Inspector.
    #inspector = NodeDockAdapter.create_dynamic(
    #    "Inspector", scene, parent=win
    #)
    #win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector)

    # ── 2) Static dock — pinned to a specific node (left side) ───────
    #
    # Create a concrete node and pin a dock to it.
    # If you delete the node on the canvas, the dock auto-closes.
    # If you close the dock manually, it unlinks from the node.
    #
    # Uncomment the block below to try it:
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

    print("\n=== Dock Adapter Demo ===")
    print("  Dynamic dock (right):")
    print("    • Select a node → mirror widgets appear in Inspector")
    print("    • Edit in dock ↔ syncs to node (and vice versa)")
    print("    • Click Unlink to disconnect from the node")
    print("    • Close the dock → hides; reopen from Window menu")
    print("")
    print("  Static dock (left, if enabled):")
    print("    • Always shows the pinned node's widgets")
    print("    • Delete the node → dock auto-closes")
    print("    • Close the dock → unlinks from the node")
    print("    • Click Unlink to disconnect manually")

    sys.exit(app.exec())


if __name__ == "__main__":
    # ── Weave imports ────────────────────────────────────────────────
    from weave.canvas import Canvas
    from weave.canvas.canvas_view import CanvasView
    from weave.canvas.canvas_minimap import CanvasMinimap
    from weave.dockadapter import NodeDockAdapter, DockMode

    from weave.example_nodes import *          # register nodes
    main()
