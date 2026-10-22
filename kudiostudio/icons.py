# -*- coding: utf-8 -*-
"""Line icons for the toolbar, the transport and the tool rail.

Drawn on a 24-unit grid with a 2-unit round stroke, the way most current line
icon sets are, and kept here as SVG text rather than as files: there are few
of them, and a colour is substituted at run time, so one drawing serves the
normal, the selected and the disabled state.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from kudiostudio.theme import DISABLED_COLOR, HINT_COLOR, TEXT_COLOR

#: SVG body per icon. ``fill="currentColor"`` marks the solid shapes; every
#: other element is stroked.
_SHAPES: Dict[str, str] = {
    # transport
    "play": '<path d="M7 4.8v14.4a1 1 0 0 0 1.5.86l11.6-7.2a1 1 0 0 0 0-1.72'
            'L8.5 3.94A1 1 0 0 0 7 4.8z" fill="currentColor" stroke="none"/>',
    "stop": '<rect x="5.5" y="5.5" width="13" height="13" rx="2" '
            'fill="currentColor" stroke="none"/>',
    # toolbar
    "open": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7'
            'v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "save": '<path d="M5 3.5h10.5L20.5 8.5V19a1.5 1.5 0 0 1-1.5 1.5H5'
            'A1.5 1.5 0 0 1 3.5 19V5A1.5 1.5 0 0 1 5 3.5z"/>'
            '<path d="M7.5 3.5v4.5h7V3.5M7.5 20.5v-6.5h9v6.5"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "revert": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3.5 4v5h5"/>',
    "zoom-in": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4'
               'M11 8.2v5.6M8.2 11h5.6"/>',
    "zoom-full": '<path d="M8 3.5H5.5a2 2 0 0 0-2 2V8M16 3.5h2.5a2 2 0 0 1 2 2V8'
                 'M3.5 16v2.5a2 2 0 0 0 2 2H8M20.5 16v2.5a2 2 0 0 1-2 2H16"/>'
                 '<path d="M7 12h10"/>',
    # the tool rail, one per panel
    "effects": '<path d="M6 4v5M6 13v7M12 4v9M12 17v3M18 4v2M18 10v10"/>'
               '<circle cx="6" cy="11" r="2"/><circle cx="12" cy="15" r="2"/>'
               '<circle cx="18" cy="8" r="2"/>',
    "enhance": '<path d="M11 3.5 12.9 9 18.5 11l-5.6 2L11 18.5 9.1 13 3.5 11'
               'l5.6-2z"/><path d="M18.5 15.5v5M16 18h5"/>',
    "compare": '<path d="M4 8h15M15.5 4.5 19 8l-3.5 3.5"/>'
               '<path d="M20 16H5M8.5 12.5 5 16l3.5 3.5"/>',
    "record": '<circle cx="12" cy="12" r="8"/>'
              '<circle cx="12" cy="12" r="3.6" fill="currentColor" stroke="none"/>',
    "synthesize": '<path d="M12 3.5 20.5 8 12 12.5 3.5 8z"/>'
                  '<path d="m3.5 12 8.5 4.5 8.5-4.5M3.5 16l8.5 4.5 8.5-4.5"/>',
    "manifest": '<path d="M9 6h11M9 12h11M9 18h11"/>'
                '<circle cx="4.8" cy="6" r="1" fill="currentColor" stroke="none"/>'
                '<circle cx="4.8" cy="12" r="1" fill="currentColor" stroke="none"/>'
                '<circle cx="4.8" cy="18" r="1" fill="currentColor" stroke="none"/>',
    "pitch": '<path d="M9 17.5V5.5l11-2v12"/>'
             '<circle cx="6.5" cy="17.5" r="2.5"/><circle cx="17.5" cy="15.5" r="2.5"/>',
    "metrics": '<path d="M4 20h16M7 16.5v-4M12 16.5V6M17 16.5v-7"/>',
    "batch": '<rect x="8" y="8" width="12.5" height="12.5" rx="2"/>'
             '<path d="M16 8V5.5a2 2 0 0 0-2-2H5.5a2 2 0 0 0-2 2V14a2 2 0 0 0 2 2H8"/>',
    # inside the controls: check boxes, combo boxes, spin boxes
    "check": '<path d="M5 12.5 10 17.5 19.5 7"/>',
    "chevron-down": '<path d="M5.5 9 12 15.5 18.5 9"/>',
    "chevron-up": '<path d="M5.5 15 12 8.5 18.5 15"/>',
}

#: rendered at each of these, so a 1.25x or 1.5x screen gets a pixmap drawn
#: for its size instead of a scaled one
_SIZES = (16, 20, 24, 28, 32, 40, 48)


def svg(name: str, color: str, stroke: float = 2.0) -> bytes:
    """The icon *name* as SVG text, drawn in *color*.

    A heavier *stroke* is for the marks drawn at 8-12 px inside a control,
    where the usual 2 units come out under a pixel wide.
    """
    body = _SHAPES[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="{stroke:g}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
            ).encode()


def _pixmap(name: str, color: str, size: int) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color)))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon(name: str, color: str = TEXT_COLOR, checked: Optional[str] = None,
         rest: Optional[str] = None) -> QIcon:
    """The icon *name* in *color*.

    *checked* is the colour for a checked button, *rest* the colour for one
    that is neither checked nor under the mouse; a tool rail shows the current
    tool brighter than the others. Disabled is always the theme's own grey,
    since Qt's automatic greying of a coloured pixmap is barely visible on a
    dark background.
    """
    result = QIcon()
    for size in _SIZES:
        result.addPixmap(_pixmap(name, rest or color, size), QIcon.Normal, QIcon.Off)
        result.addPixmap(_pixmap(name, color, size), QIcon.Active, QIcon.Off)
        result.addPixmap(_pixmap(name, checked or color, size), QIcon.Normal, QIcon.On)
        result.addPixmap(_pixmap(name, checked or color, size), QIcon.Active, QIcon.On)
        result.addPixmap(_pixmap(name, DISABLED_COLOR, size), QIcon.Disabled, QIcon.Off)
        result.addPixmap(_pixmap(name, DISABLED_COLOR, size), QIcon.Disabled, QIcon.On)
    return result


def rail_icon(name: str) -> QIcon:
    """A tool rail icon: dim at rest, full strength under the mouse or when
    it is the current tool."""
    return icon(name, TEXT_COLOR, checked=TEXT_COLOR, rest=HINT_COLOR)


NAMES = tuple(_SHAPES)

__all__ = ["icon", "rail_icon", "svg", "NAMES"]
