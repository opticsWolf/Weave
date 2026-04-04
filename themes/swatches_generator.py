"""
Color Palette Generator
=======================
PySide6 application for generating sorted color palettes with
adjustable intensity, lightness, contrast, color spread, color count,
and color cast parameters.
Outputs JSON in [R, G, B, A] format sorted by grey tones then hue.
"""

import sys
import json
import colorsys
import math
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QSlider, QPushButton, QFileDialog,
    QGroupBox, QSpinBox, QScrollArea, QToolTip, QStatusBar,
    QSplitter, QTextEdit, QCheckBox, QComboBox, QFrame
)
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QSize
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontMetrics,
    QClipboard, QMouseEvent, QPaintEvent, QResizeEvent
)


# ---------------------------------------------------------------------------
# Swatch grid widget
# ---------------------------------------------------------------------------
class SwatchGrid(QWidget):
    """Draws the color palette as a grid of swatches."""

    color_hovered = Signal(int, list)   # index, [r,g,b,a]
    color_clicked = Signal(int, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors: list[list[int]] = []
        self.columns = 16
        self.swatch_size = 32
        self.gap = 2
        self.hovered_index = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(100)

    def set_colors(self, colors: list[list[int]], columns: int = 16):
        self.colors = colors
        self.columns = columns
        self._update_size()
        self.update()

    def _update_size(self):
        if not self.colors:
            return
        rows = math.ceil(len(self.colors) / self.columns)
        w = self.columns * (self.swatch_size + self.gap) + self.gap
        h = rows * (self.swatch_size + self.gap) + self.gap
        self.setFixedSize(max(w, 100), max(h, 100))

    def _index_at(self, pos: QPoint) -> int:
        col = (pos.x() - self.gap) // (self.swatch_size + self.gap)
        row = (pos.y() - self.gap) // (self.swatch_size + self.gap)
        if col < 0 or col >= self.columns or row < 0:
            return -1
        idx = row * self.columns + col
        if idx >= len(self.colors):
            return -1
        local_x = (pos.x() - self.gap) % (self.swatch_size + self.gap)
        local_y = (pos.y() - self.gap) % (self.swatch_size + self.gap)
        if local_x > self.swatch_size or local_y > self.swatch_size:
            return -1
        return idx

    def mouseMoveEvent(self, event: QMouseEvent):
        idx = self._index_at(event.pos())
        if idx != self.hovered_index:
            self.hovered_index = idx
            self.update()
            if idx >= 0:
                c = self.colors[idx]
                self.color_hovered.emit(idx, c)
                tip = f"#{idx}  [{c[0]}, {c[1]}, {c[2]}, {c[3]}]"
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            idx = self._index_at(event.pos())
            if idx >= 0:
                self.color_clicked.emit(idx, self.colors[idx])

    def leaveEvent(self, event):
        self.hovered_index = -1
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        bg = self.palette().window().color()
        painter.fillRect(self.rect(), bg)

        for i, c in enumerate(self.colors):
            col = i % self.columns
            row = i // self.columns
            x = self.gap + col * (self.swatch_size + self.gap)
            y = self.gap + row * (self.swatch_size + self.gap)
            rect = QRect(x, y, self.swatch_size, self.swatch_size)

            color = QColor(c[0], c[1], c[2], c[3])
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRect(rect)

            if i == self.hovered_index:
                painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect.adjusted(-1, -1, 1, 1))
                painter.setPen(QPen(QColor(0, 0, 0, 200), 1))
                painter.drawRect(rect.adjusted(-2, -2, 2, 2))

        painter.end()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
# Color cast preview swatch
# ---------------------------------------------------------------------------
class CastPreview(QWidget):
    """Small widget that shows the current cast color."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self._color = QColor(128, 128, 128)

    def set_color(self, r, g, b):
        self._color = QColor(r, g, b)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.setBrush(QBrush(self._color))
        painter.drawRoundedRect(1, 1, 26, 26, 4, 4)
        painter.end()


# ---------------------------------------------------------------------------
# Labeled slider helper
# ---------------------------------------------------------------------------
class ParamSlider(QWidget):
    """A labeled slider with value display and optional range."""

    value_changed = Signal(int)

    def __init__(self, label: str, min_val: int, max_val: int,
                 default: int, suffix: str = "", parent=None):
        super().__init__(parent)
        self.suffix = suffix

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.label = QLabel(label)
        self.label.setFixedWidth(110)
        self.label.setStyleSheet("font-weight: 500;")
        layout.addWidget(self.label)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(min_val)
        self.slider.setMaximum(max_val)
        self.slider.setValue(default)
        self.slider.setTickPosition(QSlider.NoTicks)
        layout.addWidget(self.slider, 1)

        self.val_label = QLabel()
        self.val_label.setFixedWidth(50)
        self.val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._update_label(default)
        layout.addWidget(self.val_label)

        self.slider.valueChanged.connect(self._on_change)

    def _update_label(self, val):
        self.val_label.setText(f"{val}{self.suffix}")

    def _on_change(self, val):
        self._update_label(val)
        self.value_changed.emit(val)

    def value(self) -> int:
        return self.slider.value()


# ---------------------------------------------------------------------------
# Palette generation logic (smooth roll-off with eased staircase + cast)
# ---------------------------------------------------------------------------
class PaletteGenerator:
    """Generates a color palette from adjustable parameters with smooth roll-offs."""

    @staticmethod
    def _apply_cast(colors: list[list[int]], cast_hue: int, cast_strength: int) -> list[list[int]]:
        """Apply color cast as a post-process, preserving sort order."""
        if cast_strength <= 0:
            return colors

        cast_h = cast_hue / 360.0
        cast_f = cast_strength / 100.0
        result = []

        for c in colors:
            r, g, b = c[0] / 255.0, c[1] / 255.0, c[2] / 255.0
            h, l, s = colorsys.rgb_to_hls(r, g, b)

            if s < 0.01:
                # Greys: inject cast hue, add saturation proportional to strength
                new_h = cast_h
                new_s = cast_f * 0.45
                new_l = l
            else:
                # Chromatic: blend hue toward cast via circular interpolation
                dh = cast_h - h
                if dh > 0.5:
                    dh -= 1.0
                elif dh < -0.5:
                    dh += 1.0
                new_h = (h + dh * cast_f * 0.5) % 1.0
                new_s = s + (1.0 - s) * cast_f * 0.15
                new_s = max(0.0, min(1.0, new_s))
                new_l = l

            nr, ng, nb = colorsys.hls_to_rgb(new_h, new_l, new_s)
            result.append([
                max(0, min(255, int(round(nr * 255)))),
                max(0, min(255, int(round(ng * 255)))),
                max(0, min(255, int(round(nb * 255)))),
                c[3],
            ])

        return result

    @staticmethod
    def generate(
        total_colors: int = 256,    # total number of colors to generate
        num_greys: int = 16,
        lightness: int = 70,        # 0-100, center lightness for hues
        saturation: int = 60,       # 0-100, color intensity
        contrast: int = 50,         # 0-100, lightness spread within each hue
        spread: int = 24,           # hue steps per cycle
        include_pure: bool = True,  # include fully saturated pure hues
        cast_hue: int = 0,          # 0-360, hue angle of the color cast
        cast_strength: int = 0,     # 0-100, how strongly the cast tints all colors
    ) -> list[list[int]]:
        seen = set()
        colors = []

        def add(r, g, b):
            r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
            key = (r, g, b)
            if key not in seen:
                seen.add(key)
                colors.append([r, g, b, 255])

        # --- Helper: Smooth Staircase Math ---
        def smooth_step(x):
            if x <= 0:
                return 0.0
            if x >= 1:
                return 1.0
            return x * x * (3.0 - 2.0 * x)

        def eased_staircase(t, steps):
            if steps <= 1:
                return t
            scaled = t * steps
            step_idx = math.floor(scaled)
            fraction = scaled - step_idx
            eased_fraction = smooth_step(fraction)
            return (step_idx + eased_fraction) / steps

        # --- Greys (linear gradient) ---
        num_greys = max(2, min(num_greys, total_colors))
        for i in range(num_greys):
            v = int(round(i * 255 / (num_greys - 1)))
            add(v, v, v)

        # --- Parameters as floats ---
        base_l = lightness / 100.0
        base_s = saturation / 100.0
        contrast_f = contrast / 100.0

        l_range = contrast_f * 0.65
        num_rings = max(1, int(2 + contrast_f * 8))

        # --- Optional pure hues ---
        if include_pure:
            num_hues = max(6, spread)
            for li in [0.35, 0.50, 0.65]:
                for i in range(num_hues):
                    h = i / num_hues
                    r, g, b = colorsys.hls_to_rgb(h, li, 0.95)
                    add(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))

        # --- Continuous smoothed spiral fill ---
        target_chromatic = total_colors - len(colors)
        cycles = target_chromatic / max(6, spread)

        i = 0
        while len(colors) < total_colors:
            t = i / (target_chromatic - 1) if target_chromatic > 1 else 0.5

            h = (t * cycles) % 1.0

            stepped_t = eased_staircase(t, num_rings)
            l = (base_l - l_range) + (stepped_t * 2 * l_range)
            l = max(0.15, min(0.92, l))

            l_distance = abs(stepped_t - 0.5) * 2.0
            s_modifier = 1.0 - (l_distance ** 2) * 0.4
            s = base_s * s_modifier
            s = max(0.08, min(0.95, s))

            r, g, b = colorsys.hls_to_rgb(h, l, s)
            add(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))

            i += 1
            if i > total_colors * 20:
                break

        # --- Sorting: greys by brightness, then chromatic by hue ---
        def sort_key(c):
            r, g, b = c[0] / 255.0, c[1] / 255.0, c[2] / 255.0
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            if s < 0.05:
                return (0, 0, l, 0)
            else:
                return (1, h, s, l)

        colors.sort(key=sort_key)
        colors = colors[:total_colors]

        # --- Apply color cast as post-process (preserves sort order) ---
        colors = PaletteGenerator._apply_cast(colors, cast_hue, cast_strength)

        return colors


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Color Palette Generator")
        self.setMinimumSize(900, 700)
        self.resize(1100, 820)

        self.setStyleSheet("""
            QMainWindow { background: #1e1e1e; }
            QWidget { color: #d4d4d4; font-family: 'Segoe UI', 'Ubuntu', sans-serif; font-size: 13px; }
            QGroupBox {
                border: 1px solid #3c3c3c;
                border-radius: 6px;
                margin-top: 12px;
                padding: 14px 10px 10px 10px;
                font-weight: bold;
                font-size: 13px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #3c3c3c;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #0078d4;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #1a8ae8;
            }
            QPushButton {
                background: #0078d4;
                border: none;
                border-radius: 5px;
                padding: 8px 18px;
                color: white;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover { background: #1a8ae8; }
            QPushButton:pressed { background: #005ba1; }
            QPushButton#secondary {
                background: #3c3c3c;
                color: #d4d4d4;
            }
            QPushButton#secondary:hover { background: #4a4a4a; }
            QTextEdit {
                background: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                font-family: 'Cascadia Code', 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                color: #ce9178;
                selection-background-color: #264f78;
            }
            QScrollArea { border: none; background: transparent; }
            QSpinBox {
                background: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 4px 8px;
                min-width: 60px;
            }
            QComboBox {
                background: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QStatusBar { background: #007acc; color: white; font-size: 12px; }
            QLabel#swatch_info {
                background: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 6px 12px;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 13px;
            }
        """)

        self._build_ui()
        self._generate()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # --- Left panel: controls (scrollable) ---
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFixedWidth(360)
        left_scroll.setStyleSheet("QScrollArea { border: none; }")

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        # Title
        title = QLabel("Palette Generator")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff; padding: 4px 0;")
        left_layout.addWidget(title)

        # -- Parameters group --
        params_group = QGroupBox("Generation Parameters")
        params_layout = QVBoxLayout(params_group)
        params_layout.setSpacing(6)

        self.sl_lightness = ParamSlider("Lightness", 10, 95, 70, "%")
        self.sl_saturation = ParamSlider("Saturation", 5, 100, 60, "%")
        self.sl_contrast = ParamSlider("Contrast", 0, 100, 50, "%")
        self.sl_spread = ParamSlider("Hue steps", 6, 48, 24)

        for sl in [self.sl_lightness, self.sl_saturation, self.sl_contrast, self.sl_spread]:
            params_layout.addWidget(sl)
            sl.value_changed.connect(self._on_param_change)

        left_layout.addWidget(params_group)

        # -- Color Cast group --
        cast_group = QGroupBox("Color Cast")
        cast_layout = QVBoxLayout(cast_group)
        cast_layout.setSpacing(6)

        # Cast hue slider with preview swatch
        cast_hue_row = QHBoxLayout()
        cast_hue_row.setContentsMargins(0, 0, 0, 0)
        self.sl_cast_hue = ParamSlider("Cast hue", 0, 360, 0, "\u00b0")
        self.sl_cast_hue.value_changed.connect(self._on_param_change)
        self.sl_cast_hue.value_changed.connect(self._update_cast_preview)
        cast_hue_row.addWidget(self.sl_cast_hue, 1)
        self.cast_preview = CastPreview()
        cast_hue_row.addWidget(self.cast_preview)
        cast_layout.addLayout(cast_hue_row)

        self.sl_cast_strength = ParamSlider("Cast strength", 0, 100, 0, "%")
        self.sl_cast_strength.value_changed.connect(self._on_param_change)
        self.sl_cast_strength.value_changed.connect(self._update_cast_preview)
        cast_layout.addWidget(self.sl_cast_strength)

        # Quick-pick cast buttons
        cast_btn_row = QHBoxLayout()
        cast_btn_row.setSpacing(4)
        cast_picks = [
            ("None", 0, 0),
            ("Warm", 25, 35),
            ("Cool", 210, 30),
            ("Sepia", 35, 45),
            ("Rose", 340, 30),
            ("Mint", 155, 25),
        ]
        for name, hue, strength in cast_picks:
            btn = QPushButton(name)
            btn.setObjectName("secondary")
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton { padding: 2px 8px; font-size: 11px; }"
            )
            btn.clicked.connect(
                lambda checked, _h=hue, _s=strength: self._apply_cast(_h, _s)
            )
            cast_btn_row.addWidget(btn)
        cast_layout.addLayout(cast_btn_row)

        left_layout.addWidget(cast_group)

        # -- Structure group --
        struct_group = QGroupBox("Structure")
        struct_layout = QVBoxLayout(struct_group)
        struct_layout.setSpacing(6)

        # Total colors
        total_row = QHBoxLayout()
        total_label = QLabel("Total colors")
        total_label.setFixedWidth(110)
        total_label.setStyleSheet("font-weight: 500;")
        self.sp_total = QSpinBox()
        self.sp_total.setRange(8, 1024)
        self.sp_total.setValue(256)
        self.sp_total.setSingleStep(8)
        self.sp_total.valueChanged.connect(self._on_param_change)
        total_row.addWidget(total_label)
        total_row.addWidget(self.sp_total, 1)
        struct_layout.addLayout(total_row)

        # Grey count
        grey_row = QHBoxLayout()
        grey_label = QLabel("Grey tones")
        grey_label.setFixedWidth(110)
        grey_label.setStyleSheet("font-weight: 500;")
        self.sp_greys = QSpinBox()
        self.sp_greys.setRange(2, 64)
        self.sp_greys.setValue(16)
        self.sp_greys.valueChanged.connect(self._on_param_change)
        grey_row.addWidget(grey_label)
        grey_row.addWidget(self.sp_greys, 1)
        struct_layout.addLayout(grey_row)

        # Grid columns
        col_row = QHBoxLayout()
        col_label = QLabel("Grid columns")
        col_label.setFixedWidth(110)
        col_label.setStyleSheet("font-weight: 500;")
        self.sp_columns = QSpinBox()
        self.sp_columns.setRange(8, 32)
        self.sp_columns.setValue(16)
        self.sp_columns.valueChanged.connect(self._on_columns_change)
        col_row.addWidget(col_label)
        col_row.addWidget(self.sp_columns, 1)
        struct_layout.addLayout(col_row)

        # Include pure hues
        self.cb_pure = QCheckBox("Include pure saturated hues")
        self.cb_pure.setChecked(True)
        self.cb_pure.stateChanged.connect(self._on_param_change)
        struct_layout.addWidget(self.cb_pure)

        left_layout.addWidget(struct_group)

        # -- Presets --
        presets_group = QGroupBox("Presets")
        presets_layout = QVBoxLayout(presets_group)

        # (name, lightness, saturation, contrast, spread, pure, total, cast_hue, cast_str)
        preset_names = [
            ("Pastel Soft",     78, 50, 30, 24, True,  256, 0,   0),
            ("Pastel Rich",     72, 65, 45, 24, True,  256, 0,   0),
            ("Full Spectrum",   50, 80, 80, 24, True,  256, 0,   0),
            ("Muted Earthy",    55, 40, 50, 24, False, 256, 35,  25),
            ("Vivid Neon",      55, 95, 60, 24, True,  256, 0,   0),
            ("Dark Moody",      30, 55, 45, 24, False, 256, 210, 20),
            ("Light Airy",      88, 35, 25, 24, False, 256, 0,   0),
            ("Warm Sepia",      60, 45, 50, 24, False, 256, 30,  50),
            ("Cool Frost",      75, 50, 40, 24, False, 256, 200, 40),
        ]

        for name, l, s, c, sp, pure, total, ch, cs in preset_names:
            btn = QPushButton(name)
            btn.setObjectName("secondary")
            btn.setFixedHeight(28)
            btn.clicked.connect(
                lambda checked, _l=l, _s=s, _c=c, _sp=sp, _p=pure,
                       _t=total, _ch=ch, _cs=cs:
                    self._apply_preset(_l, _s, _c, _sp, _p, _t, _ch, _cs)
            )
            presets_layout.addWidget(btn)

        left_layout.addWidget(presets_group)

        left_layout.addStretch()

        # -- Action buttons --
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(6)

        self.btn_generate = QPushButton("\u27f3  Regenerate")
        self.btn_generate.setFixedHeight(38)
        self.btn_generate.clicked.connect(self._generate)
        actions_layout.addWidget(self.btn_generate)

        btn_row = QHBoxLayout()
        self.btn_copy = QPushButton("Copy JSON")
        self.btn_copy.setObjectName("secondary")
        self.btn_copy.clicked.connect(self._copy_json)
        btn_row.addWidget(self.btn_copy)

        self.btn_save = QPushButton("Save JSON\u2026")
        self.btn_save.setObjectName("secondary")
        self.btn_save.clicked.connect(self._save_json)
        btn_row.addWidget(self.btn_save)
        actions_layout.addLayout(btn_row)

        left_layout.addLayout(actions_layout)

        left_scroll.setWidget(left_panel)
        main_layout.addWidget(left_scroll)

        # --- Right panel: swatch grid + JSON preview ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Swatch info bar
        self.info_label = QLabel("Hover over a swatch to see its color value")
        self.info_label.setObjectName("swatch_info")

        info_row = QHBoxLayout()
        self.color_preview = QWidget()
        self.color_preview.setFixedSize(38, 38)
        self.color_preview.setStyleSheet(
            "background: #333; border: 1px solid #555; border-radius: 4px;"
        )
        info_row.addWidget(self.info_label, 1)
        info_row.addWidget(self.color_preview)
        right_layout.addLayout(info_row)

        # Splitter for grid / json
        splitter = QSplitter(Qt.Vertical)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.swatch_grid = SwatchGrid()
        self.swatch_grid.color_hovered.connect(self._on_swatch_hover)
        self.swatch_grid.color_clicked.connect(self._on_swatch_click)
        scroll.setWidget(self.swatch_grid)
        splitter.addWidget(scroll)

        json_container = QWidget()
        json_layout = QVBoxLayout(json_container)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_label = QLabel("JSON Output")
        json_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #888;")
        json_layout.addWidget(json_label)
        self.json_preview = QTextEdit()
        self.json_preview.setReadOnly(True)
        json_layout.addWidget(self.json_preview)
        splitter.addWidget(json_container)

        splitter.setSizes([500, 250])
        right_layout.addWidget(splitter, 1)

        main_layout.addWidget(right_panel, 1)

        self.statusBar().showMessage("Ready")

        # Initial cast preview
        self._update_cast_preview()

    def _update_cast_preview(self, _=None):
        """Update the cast hue preview swatch."""
        h = self.sl_cast_hue.value() / 360.0
        strength = self.sl_cast_strength.value() / 100.0
        if strength < 0.01:
            self.cast_preview.set_color(128, 128, 128)
        else:
            r, g, b = colorsys.hls_to_rgb(h, 0.5, 0.9)
            grey = 128
            rr = int(grey + (r * 255 - grey) * strength)
            gg = int(grey + (g * 255 - grey) * strength)
            bb = int(grey + (b * 255 - grey) * strength)
            self.cast_preview.set_color(
                max(0, min(255, rr)),
                max(0, min(255, gg)),
                max(0, min(255, bb)),
            )

    def _apply_cast(self, hue, strength):
        """Quick-pick cast application."""
        self.sl_cast_hue.slider.blockSignals(True)
        self.sl_cast_strength.slider.blockSignals(True)

        self.sl_cast_hue.slider.setValue(hue)
        self.sl_cast_hue._update_label(hue)
        self.sl_cast_strength.slider.setValue(strength)
        self.sl_cast_strength._update_label(strength)

        self.sl_cast_hue.slider.blockSignals(False)
        self.sl_cast_strength.slider.blockSignals(False)

        self._update_cast_preview()
        self._generate()

    def _apply_preset(self, l, s, c, sp, pure, total, cast_hue, cast_str):
        widgets = [
            self.sl_lightness.slider, self.sl_saturation.slider,
            self.sl_contrast.slider, self.sl_spread.slider,
            self.sp_greys, self.cb_pure, self.sp_total,
            self.sl_cast_hue.slider, self.sl_cast_strength.slider,
        ]
        for w in widgets:
            w.blockSignals(True)

        self.sl_lightness.slider.setValue(l)
        self.sl_lightness._update_label(l)
        self.sl_saturation.slider.setValue(s)
        self.sl_saturation._update_label(s)
        self.sl_contrast.slider.setValue(c)
        self.sl_contrast._update_label(c)
        self.sl_spread.slider.setValue(sp)
        self.sl_spread._update_label(sp)
        self.cb_pure.setChecked(pure)
        self.sp_total.setValue(total)
        self.sl_cast_hue.slider.setValue(cast_hue)
        self.sl_cast_hue._update_label(cast_hue)
        self.sl_cast_strength.slider.setValue(cast_str)
        self.sl_cast_strength._update_label(cast_str)

        for w in widgets:
            w.blockSignals(False)

        self._update_cast_preview()
        self._generate()

    def _on_param_change(self, _=None):
        self._generate()

    def _on_columns_change(self, _=None):
        self.swatch_grid.set_colors(self.current_colors, self.sp_columns.value())

    def _generate(self):
        colors = PaletteGenerator.generate(
            total_colors=self.sp_total.value(),
            num_greys=self.sp_greys.value(),
            lightness=self.sl_lightness.value(),
            saturation=self.sl_saturation.value(),
            contrast=self.sl_contrast.value(),
            spread=self.sl_spread.value(),
            include_pure=self.cb_pure.isChecked(),
            cast_hue=self.sl_cast_hue.value(),
            cast_strength=self.sl_cast_strength.value(),
        )
        self.current_colors = colors
        self.swatch_grid.set_colors(colors, self.sp_columns.value())
        self._update_json()
        self.statusBar().showMessage(f"Generated {len(colors)} colors")

    def _format_json(self) -> str:
        """Format colors as JSON with 3 entries per line."""
        colors = self.current_colors
        lines = ["["]
        for i in range(0, len(colors), 3):
            row = colors[i:i + 3]
            parts = [f"[{c[0]}, {c[1]}, {c[2]}, {c[3]}]" for c in row]
            comma = "," if i + 3 < len(colors) else ""
            lines.append("    " + ", ".join(parts) + comma)
        lines.append("]")
        return "\n".join(lines)

    def _update_json(self):
        self.json_preview.setPlainText(self._format_json())

    def _on_swatch_hover(self, idx, color):
        r, g, b, a = color
        hex_col = f"#{r:02X}{g:02X}{b:02X}"
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        self.info_label.setText(
            f"#{idx:>3d}   [{r:>3d}, {g:>3d}, {b:>3d}, {a}]   "
            f"{hex_col}   H:{h:.2f}  S:{s:.2f}  L:{l:.2f}"
        )
        self.color_preview.setStyleSheet(
            f"background: rgb({r},{g},{b}); "
            f"border: 1px solid #555; border-radius: 4px;"
        )

    def _on_swatch_click(self, idx, color):
        r, g, b, a = color
        text = f"[{r}, {g}, {b}, {a}]"
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Copied: {text}", 3000)

    def _copy_json(self):
        QApplication.clipboard().setText(self._format_json())
        self.statusBar().showMessage("JSON copied to clipboard", 3000)

    def _save_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Palette JSON",
            f"palette_{self.sp_total.value()}.json",
            "JSON files (*.json);;Text files (*.txt);;All files (*)"
        )
        if path:
            Path(path).write_text(self._format_json(), encoding="utf-8")
            self.statusBar().showMessage(f"Saved to {path}", 5000)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    from PySide6.QtGui import QPalette
    dark = QPalette()
    dark.setColor(QPalette.Window, QColor(30, 30, 30))
    dark.setColor(QPalette.WindowText, QColor(212, 212, 212))
    dark.setColor(QPalette.Base, QColor(37, 37, 38))
    dark.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
    dark.setColor(QPalette.ToolTipBase, QColor(50, 50, 50))
    dark.setColor(QPalette.ToolTipText, QColor(212, 212, 212))
    dark.setColor(QPalette.Text, QColor(212, 212, 212))
    dark.setColor(QPalette.Button, QColor(60, 60, 60))
    dark.setColor(QPalette.ButtonText, QColor(212, 212, 212))
    dark.setColor(QPalette.BrightText, QColor(255, 255, 255))
    dark.setColor(QPalette.Link, QColor(0, 120, 212))
    dark.setColor(QPalette.Highlight, QColor(0, 120, 212))
    dark.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(dark)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()