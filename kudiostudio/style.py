# -*- coding: utf-8 -*-
"""The dark theme: Qt's Fusion style, a palette, and one stylesheet.

Fusion draws the same on every platform and takes its colours from the
palette, so the parts the stylesheet leaves alone -- check marks, combo box
and spin box arrows, the dock buttons -- follow the theme too. The stylesheet
then sets what a palette cannot: corners, padding, and the cards the control
groups sit on.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from string import Template
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from kudiostudio import icons, theme

#: Tried in order, per character. Segoe UI for Latin text; the Chinese
#: interface needs a Traditional Chinese font named explicitly, since Windows
#: set to another locale falls back to Simplified glyph forms otherwise.
UI_FONTS = ("Segoe UI", "Microsoft JhengHei UI", "PingFang TC",
            "Noto Sans CJK TC", "Noto Sans TC")

#: The marks drawn inside controls, as (icon, colour, stroke). A stylesheet
#: can only take an image from a file, so these are written out once per run.
_MARKS = {
    "check": ("check", theme.INK_COLOR, 3.4),
    "check-disabled": ("check", theme.DISABLED_COLOR, 3.2),
    "down": ("chevron-down", theme.HINT_COLOR, 3.0),
    "down-disabled": ("chevron-down", theme.DISABLED_COLOR, 3.0),
    "up": ("chevron-up", theme.HINT_COLOR, 3.0),
    "up-disabled": ("chevron-up", theme.DISABLED_COLOR, 3.0),
}

#: the fixed-pitch families, for the places where numbers should line up
_MONO = "'Cascadia Mono', Consolas, Menlo, 'DejaVu Sans Mono', monospace"

_SHEET = Template("""
QWidget {
    color: $text;
}
QMainWindow, QDialog {
    background: $base;
}
QMainWindow::separator {
    background: $base;
    width: 5px;
    height: 5px;
}
QMainWindow::separator:hover {
    background: $primary_soft;
}

QDockWidget {
    color: $hint;
}
QDockWidget::title {
    background: $base;
    padding: 7px 10px 5px 12px;
    border-bottom: 1px solid $border;
    text-align: left;
}

QToolTip {
    background: $surface;
    color: $text;
    border: 1px solid $primary_dim;
    padding: 4px 6px;
}

QMenuBar {
    background: $base;
    padding: 2px 4px;
    border-bottom: 1px solid $border;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 3px;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background: $hover;
}
QMenu {
    background: $surface;
    border: 1px solid $edge;
    padding: 4px;
}
QMenu::item {
    padding: 5px 28px 5px 26px;
    border-left: 2px solid transparent;
}
QMenu::item:selected {
    background: $primary_soft;
    border-left-color: $primary;
}
QMenu::item:disabled {
    color: $disabled;
}
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 4px 8px;
}

QToolBar {
    background: $base;
    border: none;
    border-bottom: 1px solid $border;
    padding: 4px 6px;
    spacing: 2px;
}
QToolBar::separator {
    width: 1px;
    background: $border;
    margin: 4px 6px;
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 8px;
}
QToolButton:hover {
    background: $hover;
    border-color: $edge;
}
QToolButton:pressed {
    background: $raised;
}
QToolButton:checked {
    background: $primary_soft;
    border-color: $primary_dim;
}
QToolButton:disabled {
    color: $disabled;
}

QStatusBar {
    background: $base;
    color: $hint;
    border-top: 1px solid $border;
    font-family: $mono;
}
QStatusBar::item {
    border: none;
}

QPushButton {
    background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                stop: 0 $raised_top, stop: 1 $raised);
    border: 1px solid $edge;
    border-radius: 4px;
    padding: 3px 9px;
}
QPushButton:hover {
    background: $hover;
    border-color: $primary_dim;
}
QPushButton:pressed {
    background: $border;
}
QPushButton:checked {
    background: $primary_soft;
    border-color: $primary;
    color: $primary_text;
}
QPushButton:disabled {
    color: $disabled;
    background: $surface;
    border-color: $border;
}
QPushButton[primary="true"] {
    background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                stop: 0 $primary_top, stop: 1 $primary);
    border: 1px solid $primary_top;
    color: $ink;
    font-weight: 600;
}
QPushButton[primary="true"]:hover {
    background: $primary_top;
}
QPushButton[primary="true"]:disabled {
    background: $surface;
    border-color: $border;
    color: $disabled;
}

QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $canvas;
    border: 1px solid $edge;
    border-radius: 4px;
    padding: 2px 6px;
    selection-background-color: $primary;
    selection-color: $ink;
}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {
    border-color: $edge_hover;
}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {
    border-color: $primary;
}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled, QTextEdit:disabled {
    color: $disabled;
    background: $surface;
    border-color: $border;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: url("$down");
    width: 10px;
    height: 10px;
}
QComboBox::down-arrow:disabled {
    image: url("$down_disabled");
}
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $edge;
    selection-background-color: $primary_soft;
    selection-color: $text;
    outline: 0;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    border: none;
    background: transparent;
    width: 16px;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: $hover;
}
QAbstractSpinBox::up-arrow {
    image: url("$up");
    width: 8px;
    height: 8px;
}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {
    image: url("$up_disabled");
}
QAbstractSpinBox::down-arrow {
    image: url("$down");
    width: 8px;
    height: 8px;
}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {
    image: url("$down_disabled");
}

QCheckBox, QRadioButton {
    spacing: 6px;
}
QCheckBox::indicator, QGroupBox::indicator, QAbstractItemView::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid $edge_hover;
    border-radius: 3px;
    background: $canvas;
}
QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid $edge_hover;
    border-radius: 8px;
    background: $canvas;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QGroupBox::indicator:hover {
    border-color: $primary;
}
QCheckBox::indicator:checked, QGroupBox::indicator:checked,
QAbstractItemView::indicator:checked {
    background: $primary;
    border-color: $primary;
    image: url("$check");
}
QRadioButton::indicator:checked {
    background: $primary;
    border: 4px solid $canvas;
    width: 8px;
    height: 8px;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled,
QGroupBox::indicator:disabled {
    background: $surface;
    border-color: $border;
}
QCheckBox::indicator:checked:disabled, QGroupBox::indicator:checked:disabled {
    background: $raised;
    border-color: $border;
    image: url("$check_disabled");
}

QGroupBox {
    background: $surface;
    border: 1px solid $border;
    border-top-color: $edge;
    border-radius: 6px;
    margin-top: 1.5em;
    padding: 6px 4px 4px 4px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 6px;
    padding: 0 5px;
    border-left: 2px solid $primary;
    color: $hint;
}

QAbstractItemView {
    background: $surface;
    alternate-background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 4px;
    selection-background-color: $primary_soft;
    selection-color: $text;
    outline: 0;
}
QTableView {
    gridline-color: $border;
}
QHeaderView::section {
    background: $raised;
    color: $hint;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $edge;
    padding: 4px 8px;
}
QTableCornerButton::section {
    background: $raised;
    border: none;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $edge;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::handle:horizontal {
    background: $edge;
    border-radius: 3px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: $primary_dim;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

QSlider::groove:horizontal {
    height: 4px;
    background: $edge;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                stop: 0 $primary_dim, stop: 1 $primary);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: $canvas;
    border: 2px solid $primary;
    width: 10px;
    height: 10px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: $primary;
}
QSlider::handle:horizontal:disabled {
    border-color: $disabled;
}

QProgressBar {
    background: $canvas;
    border: 1px solid $edge;
    border-radius: 3px;
    text-align: center;
    font-family: $mono;
}
QProgressBar::chunk {
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                stop: 0 $primary_dim, stop: 1 $primary);
    border-radius: 2px;
}

QTabWidget::pane {
    border: none;
}
QTabBar::tab {
    background: transparent;
    color: $hint;
    padding: 6px 12px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: $primary_text;
    border-bottom-color: $primary;
}
QTabBar::tab:hover {
    color: $text;
}

/* the plots: pyqtgraph draws no background of its own here, so the view
   behind it provides the canvas */
QGraphicsView {
    background: $canvas;
    border: 1px solid $border;
    border-radius: 6px;
}

/* the playhead readout in the transport bar */
QLabel#timeReadout {
    background: $canvas;
    color: $primary_text;
    border: 1px solid $edge;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 10pt;
}

/* the column of tools at the side of the Tools dock */
#toolRail {
    background: $base;
    border-right: 1px solid $border;
}
#toolRail QToolButton {
    color: $hint;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 0;
    padding: 6px 2px 5px 2px;
}
#toolRail QToolButton:hover {
    background: $hover;
    color: $text;
}
#toolRail QToolButton:checked {
    background: $primary_soft;
    border-left-color: $primary;
    color: $primary_text;
}
#toolRail #railRule {
    color: $edge;
    margin: 4px 8px;
}
""")


_mark_files: Dict[str, str] = {}


def _marks() -> Dict[str, str]:
    """Mark name -> path of its SVG file, written on first use and removed
    when the process ends."""
    if not _mark_files:
        folder = Path(tempfile.mkdtemp(prefix="kudiostudio-"))
        atexit.register(shutil.rmtree, folder, ignore_errors=True)
        for key, (name, color, stroke) in _MARKS.items():
            path = folder / f"{key}.svg"
            path.write_bytes(icons.svg(name, color, stroke))
            # a stylesheet url() wants forward slashes, on Windows too
            _mark_files[key.replace("-", "_")] = path.as_posix()
    return _mark_files


def stylesheet() -> str:
    """The application stylesheet, with the theme's colours filled in."""
    return _SHEET.substitute(
        base=theme.BASE_COLOR, surface=theme.SURFACE_COLOR,
        surface_alt=theme.mix(theme.SURFACE_COLOR, theme.RAISED_COLOR, 0.5),
        raised=theme.RAISED_COLOR,
        raised_top=theme.mix(theme.RAISED_COLOR, theme.HOVER_COLOR, 0.6),
        hover=theme.HOVER_COLOR,
        border=theme.BORDER_COLOR, edge=theme.EDGE_COLOR,
        edge_hover=theme.mix(theme.EDGE_COLOR, theme.HINT_COLOR, 0.25),
        canvas=theme.CANVAS_COLOR, text=theme.TEXT_COLOR,
        hint=theme.HINT_COLOR, disabled=theme.DISABLED_COLOR,
        primary=theme.PRIMARY_COLOR, primary_soft=theme.PRIMARY_SOFT,
        primary_top=theme.mix(theme.PRIMARY_COLOR, "#ffffff", 0.22),
        primary_dim=theme.mix(theme.PRIMARY_COLOR, theme.BASE_COLOR, 0.55),
        primary_text=theme.PRIMARY_TEXT,
        ink=theme.INK_COLOR, mono=_MONO,
        **_marks())



def palette() -> QPalette:
    """Fusion's colours for the parts the stylesheet does not draw."""
    p = QPalette()
    roles = {
        QPalette.Window: theme.BASE_COLOR,
        QPalette.WindowText: theme.TEXT_COLOR,
        QPalette.Base: theme.CANVAS_COLOR,
        QPalette.AlternateBase: theme.SURFACE_COLOR,
        QPalette.Text: theme.TEXT_COLOR,
        QPalette.Button: theme.RAISED_COLOR,
        QPalette.ButtonText: theme.TEXT_COLOR,
        QPalette.ToolTipBase: theme.RAISED_COLOR,
        QPalette.ToolTipText: theme.TEXT_COLOR,
        QPalette.PlaceholderText: theme.DISABLED_COLOR,
        QPalette.Highlight: theme.PRIMARY_COLOR,
        QPalette.HighlightedText: theme.INK_COLOR,
        QPalette.Link: theme.CALL_COLOR,
        QPalette.BrightText: theme.WARN_COLOR,
        QPalette.Light: theme.EDGE_COLOR,
        QPalette.Midlight: theme.HOVER_COLOR,
        QPalette.Mid: theme.BORDER_COLOR,
        QPalette.Dark: theme.CANVAS_COLOR,
        QPalette.Shadow: "#000000",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(theme.DISABLED_COLOR))
    return p


def ui_font(base: QFont) -> QFont:
    """*base* with the theme's families, keeping only the ones this machine
    has, and *base*'s own family as the last resort."""
    installed = set(QFontDatabase.families())
    families = [f for f in UI_FONTS if f in installed]
    font = QFont(base)
    font.setFamilies(families + [base.family()])
    return font


def apply(app: QApplication) -> None:
    """Put the whole application on the dark theme. Call before any window
    is built, so every widget is polished with it from the start."""
    app.setStyle("Fusion")
    # Qt 6.8 and later: dark native title bars and dialogs to match
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark)
    app.setPalette(palette())
    app.setFont(ui_font(app.font()))
    app.setStyleSheet(stylesheet())


__all__ = ["apply", "stylesheet", "palette", "ui_font", "UI_FONTS"]
