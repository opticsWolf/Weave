# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Node Port - Fixed version with complete overlay propagation and rendering.

This file contains the fixed NodePort class that properly propagates state overlays 
to connected traces and renders them visually.
"""
import uuid
from typing import Optional, List
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPainterPath, QFont, QBrush, QFontMetrics

from weave.portregistry import PortRegistry
from weave.stylemanager import StyleManager, StyleCategory


class NodePort(QGraphicsItem):
    """
    Simplified node port with complete state overlay functionality.
    
    This version properly propagates state overlays to connected traces and 
    renders them visually.
    """

    __slots__ = (
        'node', 
        'name', 
        'is_output', 
        'color',
        'datatype',
        'port_type',
        'radius',
        'connected_traces', 
        '_is_highlighted',
        '_brush_default',
        '_brush_highlight',
        '_cached_path',
        '_inner_path',
        '_boundingRect',
        # Connection State Visuals
        '_temp_brush',
        '_temp_opacity', 
        '_is_connection_active',
        '_temp_inner_brush',
        # Port Area Extensions
        'port_description',
        '_label',
        'cfg',
        '_style_manager',
        '_state_overlay_color',  # New attribute for state overlay color
        'is_summary_port',       # True for dummy ports on minimized nodes
        '_port_uuid'             # Added for unique port identification
    )

    def __init__(self, parent: 'Node', name: str, datatype: str, is_output: bool, 
                 port_description: str = ""):
        super().__init__(parent)
        
        self.node = parent
        self.name = name
        self.datatype = datatype
        self.is_output = is_output
        self.port_description = port_description
        self.connected_traces: List['NodeTrace'] = []
        # Marks dummy ports used when a node is minimized.
        # Summary ports block connection dragging entirely.
        self.is_summary_port: bool = False
        self._is_highlighted = False
        
        # 1. Type & Color Lookup
        self.port_type = PortRegistry.get(datatype)
        self.color = self.port_type.color
        
        # Connection State (handled in state machine now)
        self._is_connection_active = False
        self._temp_brush: Optional[QBrush] = None
        self._temp_inner_brush: Optional[QBrush] = None
        self._temp_opacity: float = 1.0
        
        # Cache the style manager instance FIRST (needed by _get_port_config)
        self._style_manager = StyleManager.instance()
        
        # Add state overlay color attribute - NEW
        self._state_overlay_color = Qt.GlobalColor.transparent
        
        # ADD UUID GENERATION FOR PORT IDENTIFICATION
        self._port_uuid = uuid.uuid4()
        
        # 2. Geometry Config - Get from StyleManager instead of node config
        self.cfg = self._get_port_config()
        
        # Set radius from config (used by geometry calculations)
        # Uses 'radius' — the canonical name from PortStyleSchema
        self.radius = self.cfg.get('radius', 8)
        
        # 3. Pre-calculate Brushes (Performance: Avoid creating QBrush in paint loop)
        self._brush_default = QBrush(self.color)
        
        # Use helper function for highlight color
        hl_color = self._highlight_colors(self.color, self.cfg['highlight'], 20)
        self._brush_highlight = QBrush(hl_color)
        
        # 4. Pre-calculate Paths
        self._cached_path = QPainterPath()
        self._inner_path = QPainterPath()
        self._boundingRect = QRectF()
        self._rebuild_paths()
        
        # 5. Port Label (Create if port area is enabled)
        self._label: Optional[PortLabel] = None
        if self.cfg.get('enable_area', False):
            self._create_label()
        
        # 6. Set Tooltip
        self._update_tooltip()
        
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

        # Register for style changes to keep port appearance updated
        self._style_manager.register(self, StyleCategory.PORT)


    def get_uuid(self) -> uuid.UUID:
        """
        Get the unique identifier for this port.
        
        This UUID provides a persistent way to identify ports across their lifetime,
        even if other attributes like name or datatype change.
        
        Returns:
            A uuid.UUID object that uniquely identifies this port instance.
        """
        return self._port_uuid
    
    def get_uuid_string(self) -> str:
        """
        Get the unique identifier for this port as a string representation.
        
        This is useful for serialization, logging, and other string-based operations
        where working with UUID objects directly might be cumbersome.
        
        Returns:
            A string representation of the port's UUID.
        """
        return str(self._port_uuid)
    
    def _get_port_config(self) -> dict:
        """
        Get the current port configuration from StyleManager.

        Returns the raw style dictionary from StyleManager directly, using the
        canonical key names defined in PortStyleSchema (core_theme.py). No
        remapping is performed — callers must use the schema's own names
        (e.g. 'radius', not 'port_radius'; 'inner_color', not 'inner_port_color';
        'enable_area', not 'enable_port_area').

        Returns:
            A dictionary of port styling parameters keyed by PortStyleSchema field names.
        """
        return self._style_manager.get_all(StyleCategory.PORT)


    def _highlight_colors(self, color: QColor, b_offset: int, s_offset: int = 0) -> QColor:
        """Helper to highlight colors."""
        h, s, l, a = color.getHsl()
        l = max(0, min(255, l + b_offset))
        s = max(0, min(255, s + s_offset))
        return QColor.fromHsl(h, s, l, a)

    # ==========================================================================
    # PORT AREA & LABEL METHODS
    # ==========================================================================

    def _create_label(self):
        """Creates and configures the port label."""
        cfg = self.cfg
        
        type_display = getattr(self.port_type, 'name', self.datatype)
        
        if self.port_description:
            label_text = f"{self.name} - {self.port_description}"
        else:
            label_text = f"{self.name} ({type_display})"
        
        # Create label with configuration using PortStyleSchema key names
        self._label = PortLabel(self, label_text)
        self._label.set_config(
            font_family=cfg.get('label_font_family', 'Segoe UI'),
            font_size=cfg.get('label_font_size', 9),
            font_weight=cfg.get('label_font_weight', QFont.Weight.Normal),
            font_italic=cfg.get('label_font_italic', False),
            color=cfg.get('label_color', QColor(200, 200, 200)),
            max_width=cfg.get('label_max_width', 120)
        )
        
        self._position_label()

    def _rebuild_paths(self) -> None:
        r = self.radius
        inner_r = self.cfg.get('inner_radius', 4.0)
        
        # Outer Path (Half-Circle)
        self._cached_path = QPainterPath()
        if not self.is_output:
            self._cached_path.arcMoveTo(-r, -r, 2*r, 2*r, 270)
            self._cached_path.arcTo(-r, -r, 2*r, 2*r, 270, 180)
        else:
            self._cached_path.arcMoveTo(-r, -r, 2*r, 2*r, 90)
            self._cached_path.arcTo(-r, -r, 2*r, 2*r, 90, 180)
        self._cached_path.closeSubpath()
        
        # Inner Circle Path
        self._inner_path = QPainterPath()
        self._inner_path.addEllipse(QPointF(0, 0), inner_r, inner_r)
        
        # Bounding Rect (Slightly larger to avoid clipping)
        margin = 2.0
        self._boundingRect = QRectF(-r - margin, -r -margin, 2*r + 2*margin, 2*r + 2*margin)

    def _position_label(self) -> None:
        """Positions the label relative to the port."""
        if not self._label:
            return
        
        spacing = self.cfg.get('label_spacing', 8)
        r = self.radius
        
        if self.is_output:
            # Output Port (Right Side of Node): Label is to the LEFT of the port
            label_w = self._label.get_width()
            x_pos = -r - spacing - label_w
            self._label.setPos(x_pos, 0)
        else:
            # Input Port (Left Side of Node): Label is to the RIGHT of the port
            x_pos = r + spacing
            self._label.setPos(x_pos, 0)

    def _update_tooltip(self):
        """Updates the tooltip based on current configuration."""
        type_display = getattr(self.port_type, 'name', self.datatype)
        
        if self.port_description:
            tooltip = f"{type_display}\n{self.port_description}"
        else:
            tooltip = type_display
        
        self.setToolTip(tooltip)

    def refresh_label_style(self) -> None:
        """Triggers label refresh if present."""
        if self._label:
            self._label.refresh_style()

    def get_label_width(self) -> float:
        """Returns the total width occupied by the label."""
        if not self._label:
            return 0.0
        return self._label.get_width() + self.cfg.get('label_spacing', 8) + self.radius
    
    def get_label_height(self) -> float:
        """Returns the height of the label."""
        if not self._label:
            return 0.0
        return self._label.get_height()

    # ==========================================================================
    # VISUAL TARGET (For Port Area Redirection)
    # ==========================================================================

    def get_visual_target(self) -> 'NodePort':
        """
        Returns the port that should be used for visual connection endpoints.
        Enables port forwarding (e.g., summary ports in minimized nodes).
        """
        if hasattr(self.node, 'is_minimized') and self.node.is_minimized:
            if self.is_output:
                return getattr(self.node, '_summary_output', self)
            return getattr(self.node, '_summary_input', self)
        
        return self

    # ==========================================================================
    # CONNECTION MANAGEMENT
    # ==========================================================================

    def add_trace(self, trace: 'NodeTrace') -> None:
        """
        Registers a connection. 
        Enforces: Input ports can only have ONE connection.
        """
        if not self.is_output and self.connected_traces:
            for existing_trace in list(self.connected_traces):
                if existing_trace is not trace:
                    # Remove old traces via scene removal
                    if hasattr(existing_trace, 'remove_from_scene'):
                        existing_trace.remove_from_scene()
        
        if trace not in self.connected_traces:
            self.connected_traces.append(trace)
            
        self.refresh_label_style()
        self.update()

    def remove_trace(self, trace: 'NodeTrace') -> None:
        if trace in self.connected_traces:
            self.connected_traces.remove(trace)
            self.refresh_label_style()
            self.update()

    def get_connections(self) -> List['NodeTrace']:
        """Returns a copy of the connected traces list."""
        return self.connected_traces.copy()

    def disconnect_all(self) -> None:
        """
        Disconnects and removes all traces connected to this port.
        """
        for edge in list(self.connected_traces):
            if hasattr(edge, 'remove_from_scene'):
                edge.remove_from_scene()
            elif hasattr(edge, 'setParentItem'): 
                edge.setParentItem(None) 

    # Alias for backward compatibility
    def clear_connections(self) -> None:
        """Alias for disconnect_all() for backward compatibility."""
        self.disconnect_all()

    # ==========================================================================
    # CONNECTION STATE VISUALS
    # ==========================================================================

    def set_connection_state(self, is_compatible: bool) -> None:
        self._is_connection_active = True
        
        if is_compatible:
            sat_delta = self.cfg['compatible_saturation']
            val_delta = self.cfg['compatible_brightness']
            new_color = self._highlight_colors(self.color, val_delta, sat_delta)
            
            self._temp_opacity = 1.0
            self._temp_brush = QBrush(new_color)
            
            # Inner circle highlight logic
            if self.cfg['use_outline_color']:
                # Derive base from the node body's outline color, shifted by outline_bright
                base_inner = self._highlight_colors(
                    self.node.body._outline_color, self.cfg['outline_bright'], 0
                )
                inner_col = self._highlight_colors(base_inner, self.cfg['highlight'] * 1.33, 20)
                self._temp_inner_brush = QBrush(inner_col)
            else:
                base_inner = self.cfg['inner_color']
                self._temp_inner_brush = QBrush(base_inner)
        else:
            self._temp_opacity = self.cfg['incompatible_opacity']
            sat_delta = self.cfg['incompatible_saturation']
            val_delta = self.cfg['incompatible_brightness']
            
            new_color = self._highlight_colors(self.color, val_delta, sat_delta)
            self._temp_brush = QBrush(new_color)

            # Inner circle dimming logic
            if self.cfg['use_outline_color']:
                # Derive base from the node body's outline color, shifted by outline_bright
                base_inner = self._highlight_colors(
                    self.node.body._outline_color, self.cfg['outline_bright'], 0
                )
                inner_col = self._highlight_colors(base_inner, val_delta, sat_delta)
                self._temp_inner_brush = QBrush(inner_col)
            else:
                base_inner = self.cfg['inner_color']
                self._temp_inner_brush = QBrush(base_inner)

        self.update()

    def reset_connection_state(self) -> None:
        self._is_connection_active = False
        self._temp_brush = None
        self._temp_inner_brush = None
        self._temp_opacity = 1.0
        self.update()


    # ==========================================================================
    # INTERACTION (Only hover events for highlighting)
    # ==========================================================================

    def hoverEnterEvent(self, event) -> None:
        self.set_highlight(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.set_highlight(False)
        super().hoverLeaveEvent(event)

    def set_highlight(self, active: bool) -> None:
        if self._is_highlighted != active:
            self._is_highlighted = active
            self.update() 

    # ==========================================================================
    # STATE MANAGEMENT
    # ==========================================================================
    
    def get_state(self) -> dict:
        """
        Returns a dictionary representing the configuration of this port.
        Used for cloning and serialization.
        """
        return {
            "name": self.name,
            "datatype": self.datatype,
            "is_output": self.is_output,
            "description": self.port_description,
        }

    # ==========================================================================
    # RENDERING
    # ==========================================================================

    def boundingRect(self) -> QRectF:
        return self._boundingRect

    def get_scene_center(self) -> QPointF:
        """Returns the center of the port in Scene coordinates."""
        return self.scenePos()

    def set_state_overlay(self, overlay_color):
        """
        Sets the state overlay color for this port and forwards it to connected traces.
        
        Only outgoing ports forward the overlay color to prevent redundant propagation 
        through connections. This ensures efficient rendering while maintaining visual 
        consistency across the node graph.
    
        Args:
            overlay_color: QColor or Qt.GlobalColor.transparent for blending effect
                The color to be used for overlay rendering. This exact object will be
                forwarded to all connected traces without modification.
                
        Example:
            # Set an error red overlay on an output port
            output_port.set_state_overlay(QColor(255, 0, 0, 128))  # Semi-transparent red
            
            # Clear overlay by setting transparent
            output_port.set_state_overlay(Qt.GlobalColor.transparent)
            
            # Input ports do NOT forward colors (they receive them from connections)
            input_port.set_state_overlay(QColor(0, 255, 0, 128))  # Will be received but not forwarded
        """
        # 1. Update local state
        self._state_overlay_color = overlay_color
        
        # 2. Only forward to connected traces if this is an outgoing port
        # This prevents redundant propagation through connections
        if self.is_output:
            for trace in self.connected_traces:
                if hasattr(trace, 'set_state_overlay'):
                    try:
                        trace.set_state_overlay(overlay_color)  # Forward unmodified
                    except Exception:
                        pass
        
        # 3. Refresh trace colors to match the new port overlay color
        # This ensures traces update their base color when the port overlay changes
        for trace in self.connected_traces:
            if hasattr(trace, 'refresh_style'):
                try:
                    trace.refresh_style()
                except Exception:
                    pass
        
        # 4. Trigger repaint of this port
        self.update()

    
    def _blend_color_with_overlay(self, base_color: QColor, overlay_color) -> QColor:
        """
        Blend the overlay color with the base color, preserving the overlay's alpha
        as the blend factor.
        
        Args:
            base_color: The original port color
            overlay_color: The state overlay color (with alpha indicating blend strength)
        
        Returns:
            A new QColor blended between base and overlay
        """
        if overlay_color is None or overlay_color == Qt.GlobalColor.transparent:
            return base_color
        
        # Handle QColor objects
        if isinstance(overlay_color, QColor):
            if overlay_color.alpha() == 0:
                return base_color
            
            # Use overlay alpha as blend factor (0-255 -> 0.0-1.0)
            blend_factor = (overlay_color.alpha() / 255) / 1.5
            
            # Linear interpolation between base and overlay RGB values
            r = int(base_color.red() * (1 - blend_factor) + overlay_color.red() * blend_factor)
            g = int(base_color.green() * (1 - blend_factor) + overlay_color.green() * blend_factor)
            b = int(base_color.blue() * (1 - blend_factor) + overlay_color.blue() * blend_factor)
            
            # Preserve original base alpha
            return QColor(r, g, b, base_color.alpha())
        
        return base_color
    
    def _has_real_connections(self) -> bool:
        """Check whether the real ports this summary port represents have traces."""
        real_ports = getattr(self.node, 'outputs' if self.is_output else 'inputs', [])
        return any(
            p is not self and getattr(p, 'connected_traces', None)
            for p in real_ports
        )

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        """
        Renders the port with complete state overlay support.
        
        Draws both the main visual representation and an optional overlay layer
        when a state color is applied.
        """
        # Opacity handling
        if self._is_connection_active:
            painter.setOpacity(self._temp_opacity)
        else:
            painter.setOpacity(1.0)
    
        # Determine brush based on state and connection status
        if self._is_highlighted:
            own_brush = self._brush_highlight
        elif self._is_connection_active and self._temp_brush:
            own_brush = self._temp_brush
        else:
            own_brush = self._brush_default
    
        # === OVERLAY BLENDING ===
        # If we have a state overlay color, blend it with the brush color
        if (self._state_overlay_color is not None and 
            self._state_overlay_color != Qt.GlobalColor.transparent):
            
            base_color = own_brush.color()
            blended_color = self._blend_color_with_overlay(base_color, self._state_overlay_color)
            own_brush = QBrush(blended_color)
    
        painter.setPen(Qt.PenStyle.NoPen)
    
        # Draw outer shape based on connection state
        if not self.connected_traces:
            # Check if this is a summary (dummy) port on a minimised node
            # that stands in for real ports which DO have connections.
            # If so, draw a full circle in the port's own colour.
            if self.is_summary_port and self._has_real_connections():
                painter.setBrush(own_brush)
                painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)
            else:
                # Not connected -> Draw Standard Half-Circle
                painter.setBrush(own_brush)
                painter.drawPath(self._cached_path)
                
        else:
            draw_full_circle = False
            
            if self.is_output:
                draw_full_circle = True
            else:
                other_trace = self.connected_traces[0]
                other_port = getattr(other_trace, 'source', None)
    
                if other_port and other_port.datatype == self.datatype:
                    draw_full_circle = True
                else:
                    # Split circle logic - drawing both halves with different colors
                    painter.setBrush(own_brush)
                    painter.drawPath(self._cached_path)
                    
                    complementary = QPainterPath()
                    r = self.radius
                    
                    if not self.is_output: 
                        complementary.arcMoveTo(-r, -r, 2*r, 2*r, 90)
                        complementary.arcTo(-r, -r, 2*r, 2*r, 90, 180)
                    else:
                        complementary.arcMoveTo(-r, -r, 2*r, 2*r, 270)
                        complementary.arcTo(-r, -r, 2*r, 2*r, 270, 180)
                    
                    complementary.closeSubpath()
                    
                    if other_port:
                        other_col = other_port.color
                        if self._is_highlighted:
                            other_col = self._highlight_colors(other_col, self.cfg['highlight'], 20)
                        
                        # Also blend overlay with the complementary color
                        if (self._state_overlay_color is not None and 
                            self._state_overlay_color != Qt.GlobalColor.transparent):
                            other_col = self._blend_color_with_overlay(other_col, self._state_overlay_color)
                        
                        painter.setBrush(QBrush(other_col))
                    
                    painter.drawPath(complementary)
    
            if draw_full_circle:
                painter.setBrush(own_brush)
                painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)
    
        # Draw inner circle
        if self._temp_inner_brush:
            inner_brush = self._temp_inner_brush
        else:
            # Determine base inner color: derive from node body outline, or use inner_color directly
            if self.cfg['use_outline_color']:
                base_inner = self._highlight_colors(
                    self.node.body._outline_color, self.cfg['outline_bright'], 0
                )
            else:
                base_inner = self.cfg['inner_color']
            
            if self.connected_traces or (self.is_summary_port and self._has_real_connections()):
                final_inner = self._highlight_colors(base_inner, self.cfg['highlight'], 0)
            else:
                final_inner = base_inner
            
            # Blend overlay with inner circle color too
            if (self._state_overlay_color is not None and 
                self._state_overlay_color != Qt.GlobalColor.transparent):
                final_inner = self._blend_color_with_overlay(final_inner, self._state_overlay_color)
            
            inner_brush = QBrush(final_inner)
        
        painter.setBrush(inner_brush)
        painter.drawPath(self._inner_path)


# PortLabel remains unchanged for backward compatibility
class PortLabel(QGraphicsItem):
    """Simplified port label implementation"""
    __slots__ = (
        '_port', '_text', '_font', '_color', '_max_width',
        '_cached_rect', '_text_lines', '_line_height'
    )
    
    def __init__(self, parent: 'NodePort', text: str):
        super().__init__(parent)
        self._port = parent
        self._text = text
        self._max_width = 120.0
        self._color = QColor(200, 200, 200)
        self._font = QFont("Segoe UI", 9)
        self._cached_rect = QRectF()
        self._text_lines = []
        self._line_height = 0
        self._calculate_layout()
    
    def set_config(self, font_family: str, font_size: int, font_weight, 
                   font_italic: bool, color: QColor, max_width: float):
        """Initial configuration setup."""
        self._max_width = max_width
        self.refresh_style()
        self._calculate_layout()
    
    def refresh_style(self):
        """Updates font and color based on connection status.
        
        All keys use the canonical names from PortStyleSchema in core_theme.py.
        """
        cfg = self._port.cfg
        is_conn = len(self._port.connected_traces) > 0
        
        # 'label_color' — PortStyleSchema.label_color
        base_color = cfg['label_color']
        if is_conn:
            # 'label_connected_color_shift' — PortStyleSchema.label_connected_color_shift
            shift = cfg['label_connected_color_shift']
            self._color = self._port._highlight_colors(base_color, shift)
        else:
            self._color = base_color
            
        # 'label_font_family', 'label_font_size' — PortStyleSchema fields
        self._font = QFont(
            cfg['label_font_family'],
            cfg['label_font_size']
        )
        
        if is_conn:
            # 'label_connected_weight', 'label_connected_italic' — PortStyleSchema fields
            self._font.setWeight(cfg['label_connected_weight'])
            self._font.setItalic(cfg['label_connected_italic'])
        else:
            # 'label_font_weight', 'label_font_italic' — PortStyleSchema fields
            self._font.setWeight(cfg['label_font_weight'])
            self._font.setItalic(cfg['label_font_italic'])
            
        self.update()

    def _calculate_layout(self):
        """Calculates text wrapping and bounding rect."""
        fm = QFontMetrics(self._font)
        self._line_height = fm.height()
        
        words = self._text.split()
        self._text_lines = []
        current_line = ""
        
        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            if fm.horizontalAdvance(test_line) <= self._max_width:
                current_line = test_line
            else:
                if current_line:
                    self._text_lines.append(current_line)
                current_line = word
        
        if current_line:
            self._text_lines.append(current_line)
        
        if not self._text_lines:
            self._text_lines = [self._text]
        
        actual_width = max(fm.horizontalAdvance(line) for line in self._text_lines) if self._text_lines else 0
        total_height = len(self._text_lines) * self._line_height
        
        self._cached_rect = QRectF(0, -total_height / 2, actual_width, total_height)
    
    def get_width(self) -> float:
        return self._cached_rect.width()
    
    def get_height(self) -> float:
        return self._cached_rect.height()
    
    def boundingRect(self) -> QRectF:
        return self._cached_rect
    
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None):
        painter.setFont(self._font)
        painter.setPen(self._color)
        
        y_offset = self._cached_rect.top()
        for i, line in enumerate(self._text_lines):
            painter.drawText(QPointF(0, y_offset + (i + 1) * self._line_height - 3), line)