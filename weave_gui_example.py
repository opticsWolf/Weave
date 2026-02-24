# -*- coding: utf-8 -*-
"""
Created on Tue Feb 24 22:00:35 2026

@author: Frank
"""

from PySide6.QtCore import Qt


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    
    try:
        #from weave.node import Node
        from weave.canvas import Canvas
        from weave.canvas.canvas_view import CanvasView
        from weave.canvas.canvas_minimap import CanvasMinimap
        HAS_UI_COMPONENTS = True
    except ImportError:
        HAS_UI_COMPONENTS = False
        print("[Warning] UI components missing.")
    
    from weave.example_nodes import *
    #from numpynodes import *
    #from dropdownnode import *
    
    app = QApplication.instance() or QApplication(sys.argv)
    
    scene = Canvas(config={
        "snapping_enabled": True,
        "connection_snap_radius": 25.0,
    })
    
    if HAS_UI_COMPONENTS:
        view = CanvasView(scene)
        view.setWindowTitle("Enhanced Node Canvas v12 (Performance Optimized)")
        view.resize(1200, 800)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

        minimap = CanvasMinimap(view, parent=view)
        minimap.set_config(width=220, height=160)
        minimap.show()
        
        view.centerOn(0, 0)
        view.show()
        
        print("\n=== Enhanced Node Canvas v12 (Performance Optimized) ===")
        print("Performance Improvements:")
        print("  • Cached style properties (50-100x faster access)")
        print("  • Optimized shake detection (delta-based, O(1))")
        print("  • Optimized drawBackground (no get_all() per frame)")
        print("")
        print("Architecture:")
        print("  • Grid snapping invoked by IdleState")
        print("  • Cloning goes directly to NodeManager")
        print("  • Item resolution via ItemResolver utility")
        print("")
        print("Features:")
        print("  • Connection dragging with port snapping")
        print("  • Double-click on title to edit node name")
        print("  • Drag input port beyond 2x snap radius to detach")
        print("  • Ctrl+Double-click to clone nodes")
        print("  • Shake selected nodes to disconnect")
        print("  • Right double-click on port to clear connections")
        print("  • Context menu with node registry")
        print("  • Grid snapping and infinite canvas")
        
        sys.exit(app.exec())
    else:
        print("[Info] Running in headless mode")
        sys.exit(0)