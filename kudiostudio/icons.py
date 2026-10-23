# -*- coding: utf-8 -*-
"""Line icons for the toolbar, the transport and the tool rail, and the
application's own icon.

The line icons are SVG text in `kudiostudio.shapes`, rather than files:
there are few of them, and a colour is substituted at run time, so one
drawing serves the normal, the selected and the disabled state. The
application icon is a picture, not a line drawing, and ships as a file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from kudiostudio.shapes import SHAPES as _SHAPES
from kudiostudio.shapes import svg
from kudiostudio.theme import DISABLED_COLOR, HINT_COLOR, PRIMARY_TEXT, TEXT_COLOR

#: rendered at each of these, so a 1.25x or 1.5x screen gets a pixmap drawn
#: for its size instead of a scaled one
_SIZES = (16, 20, 24, 28, 32, 40, 48)


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
    """A tool rail icon: dim at rest, full strength under the mouse, and in
    the accent when it is the current tool."""
    return icon(name, TEXT_COLOR, checked=PRIMARY_TEXT, rest=HINT_COLOR)


#: the application's own mark, shared with kudio's documentation: 256 px
#: with a transparent ground outside the circle
LOGO = Path(__file__).resolve().parent / "assets" / "logo.png"


def app_icon() -> QIcon:
    """The window and taskbar icon, empty if the file is missing.

    Each size is scaled down from the one above it rather than straight from
    256 px, which at 16 px would drop most of the K's outline between samples.
    """
    result = QIcon()
    pixmap = QPixmap(str(LOGO))
    if pixmap.isNull():
        return result
    result.addPixmap(pixmap)
    for size in (128, 64, 48, 32, 24, 16):
        while pixmap.width() >= 2 * size:
            pixmap = pixmap.scaled(pixmap.width() // 2, pixmap.height() // 2,
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        result.addPixmap(pixmap.scaled(size, size, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation))
    return result


#: the icons drawn here, which a plugin may not redraw
_BUILT_IN = frozenset(_SHAPES)

NAMES = tuple(_SHAPES)


def register(name: str, body: str) -> None:
    """Add a plugin panel's icon under *name*.

    *body* is SVG drawn on the same 24-unit grid as the icons above, with
    ``currentColor`` for anything solid. A name this module already draws is
    refused, so a plugin cannot change how a built-in tool looks.
    """
    global NAMES
    if name in _BUILT_IN:
        raise ValueError(f"icon {name!r} is a built-in one")
    data = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'{body}</svg>').encode()
    if not QSvgRenderer(QByteArray(data)).isValid():
        raise ValueError(f"icon {name!r} is not valid SVG")
    _SHAPES[name] = body
    NAMES = tuple(_SHAPES)


__all__ = ["icon", "rail_icon", "svg", "register", "app_icon", "LOGO", "NAMES"]
