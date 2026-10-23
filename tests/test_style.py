# -*- coding: utf-8 -*-
"""The dark theme: a stylesheet with nothing left unfilled, marks that load,
and labels that are as monospace as they claim."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from kudiostudio import icons, style


@pytest.fixture
def themed(qapp):
    """The theme on the shared application for one test, then taken off, so
    the rest of the suite runs as it always has."""
    before = (qapp.style().name(), qapp.palette(), qapp.font(), qapp.styleSheet())
    style.apply(qapp)
    yield qapp
    name, palette, font, sheet = before
    qapp.setStyleSheet(sheet)
    qapp.setFont(font)
    qapp.setPalette(palette)
    qapp.setStyle(name)


def test_every_placeholder_is_filled():
    assert "$" not in style.stylesheet()


def test_every_mark_the_stylesheet_names_is_a_drawable_svg(qapp):
    from PySide6.QtSvg import QSvgRenderer

    paths = re.findall(r'url\("([^"]+)"\)', style.stylesheet())
    assert paths, "the arrows and check marks are images"
    for path in set(paths):
        assert Path(path).is_file(), path
        assert QSvgRenderer(path).isValid(), path


def test_every_icon_draws(qapp):
    for name in icons.NAMES:
        assert not icons.icon(name).pixmap(24, 24).isNull(), name


def test_the_application_icon_ships_and_comes_in_every_taskbar_size(qapp):
    """kudio's own mark, so the two share one face."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    image = QImage(str(icons.LOGO))
    assert image.size() == QSize(256, 256)
    # a circle on a transparent ground, so the taskbar shows no square
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(128, 128).alpha() == 255

    icon = icons.app_icon()
    sizes = {s.width() for s in icon.availableSizes()}
    assert {16, 24, 32, 48, 256} <= sizes
    assert not icon.pixmap(16, 16).isNull()

    # and it goes into the wheel along with the code
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8")
    assert re.search(r'kudiostudio = \["assets/\*\.png"\]', pyproject)


def test_a_missing_icon_file_gives_an_empty_icon_not_an_error(monkeypatch, qapp,
                                                             tmp_path):
    monkeypatch.setattr(icons, "LOGO", tmp_path / "nowhere.png")
    assert icons.app_icon().isNull()


def test_every_panel_has_a_rail_icon():
    from kudiostudio.panels import PANEL_CLASSES
    for cls in PANEL_CLASSES:
        assert cls.icon in icons.NAMES, cls.title


def test_apply_sets_the_palette_and_keeps_a_font_to_fall_back_on(themed, qapp):
    from kudiostudio import theme
    assert themed.palette().window().color().name() == theme.BASE_COLOR
    families = themed.font().families()
    assert families and set(families) - set(style.UI_FONTS), \
        "the platform's own font stays last in the list"


def test_a_call_label_is_fixed_pitch(qapp):
    """``font-family: monospace`` alone is Tahoma on Windows."""
    from PySide6.QtGui import QFontDatabase, QFontInfo

    from kudiostudio.panels.base import Panel

    fixed = {"Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"}
    if not fixed & set(QFontDatabase.families()):
        pytest.skip("none of the fixed-pitch fonts the theme names is installed")
    label = Panel.call_label("kudio.gain(y, db=0.0)")
    label.ensurePolished()
    assert QFontInfo(label.font()).fixedPitch()
