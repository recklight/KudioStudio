# -*- coding: utf-8 -*-
"""The dark theme and the marks: stylesheets with nothing left undefined,
icons the page can draw, the application's own in every size the taskbar
asks for, one for every tool on the rail, and calls that are as monospace
as they claim."""
from __future__ import annotations

import http.client
import re
import struct
from pathlib import Path
from xml.etree import ElementTree

from kudiostudio import shapes, theme
from kudiostudio.web import app, server
from kudiostudio.web.studio import Prefs, Studio


def _page_files(*suffixes: str):
    for path in sorted(server.STATIC.rglob("*")):
        if path.suffix in suffixes:
            yield path.relative_to(server.STATIC).as_posix(), path.read_text(encoding="utf-8")


def _stylesheets() -> str:
    return "\n".join(text for _, text in _page_files(".css"))


def _font_list(css: str, prop: str) -> list:
    """The families a font custom property names, quotes kept: a quoted
    ``"monospace"`` would be a font of that name, not the generic family."""
    value = re.search(rf"{prop}\s*:\s*([^;]+);", css).group(1)
    return [family.strip() for family in value.split(",")]


def _as_the_page_draws_it(body: str) -> str:
    # what icons.js wraps every body in before it goes into the DOM
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2">{body}</svg>')


def test_every_placeholder_is_filled():
    """A var() that nothing defines is no error anywhere: the property quietly
    falls back to the browser's default, so a colour renamed in theme.py
    would leave part of the page unstyled."""
    defined = set(re.findall(r"(?<![\w-])(--[\w-]+)\s*:", server.theme_css()))
    for _, text in _page_files(".css", ".html"):
        defined |= set(re.findall(r"(?<![\w-])(--[\w-]+)\s*:", text))
    read = {}
    for name, text in _page_files(".css", ".html", ".js"):
        # var(--x, 66%) carries its own fallback, and is left out here
        for prop in re.findall(r"var\((--[\w-]+)\)", text):
            read.setdefault(prop, name)
        # the plots take their colours from the root element as they draw
        for prop in re.findall(r"getPropertyValue\(\s*[\"'](--[\w-]+)[\"']", text):
            read.setdefault(prop, name)
    assert "--base" in read and "--wave" in read
    assert {prop: name for prop, name in read.items() if prop not in defined} == {}


def test_every_mark_the_page_names_is_a_drawable_icon():
    """icons.js draws a name /icons.json does not have as an empty svg,
    without a word."""
    named = set()
    for _, text in _page_files(".html", ".js"):
        # a comment's example is not a mark
        text = re.sub(r"/\*.*?\*/|^\s*//[^\n]*", "", text, flags=re.S | re.M)
        named |= set(re.findall(r'data-icon="([\w-]+)"', text))
        named |= set(re.findall(r'\bsvg\(\s*"([\w-]+)"', text))
    assert {"play", "stop", "open", "close"} <= named
    assert sorted(named - set(shapes.SHAPES)) == []


def test_every_icon_draws():
    """A body that does not parse leaves a blank button, and says so only in
    the browser's console."""
    for name, body in shapes.SHAPES.items():
        root = ElementTree.fromstring(_as_the_page_draws_it(body))
        assert len(root), name


def test_the_application_icon_ships_and_comes_in_every_taskbar_size():
    """kudio's own mark, so the two share one face."""
    from PIL import Image          # matplotlib's, which kudio[viz] brings

    with Image.open(server.LOGO) as image:
        assert image.size == (256, 256)
        rgba = image.convert("RGBA")
    # a circle on a transparent ground, so the taskbar shows no square
    assert rgba.getpixel((0, 0))[3] == 0
    assert rgba.getpixel((128, 128))[3] == 255

    # the window's own icon holds an image per size, so Windows scales none
    data = app.ICON.read_bytes()
    _, kind, count = struct.unpack("<HHH", data[:6])
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}     # 0 is 256 there
    assert kind == 1 and {16, 24, 32, 48, 256} <= sizes
    with Image.open(app.ICON) as ico:
        ico.size = (16, 16)
        small = ico.convert("RGBA")
    assert small.size == (16, 16) and small.getbbox() is not None

    # and it goes into the wheel along with the code
    try:
        import tomllib
    except ImportError:            # before 3.11; pytest brings tomli there
        import tomli as tomllib
    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent
                               / "pyproject.toml").read_text(encoding="utf-8"))
    shipped = pyproject["tool"]["setuptools"]["package-data"]["kudiostudio"]
    assert "assets/*.png" in shipped and "assets/*.ico" in shipped


def test_a_missing_icon_file_gives_no_image_not_an_error(monkeypatch, tmp_path):
    """Without the file every mark on the page gets a 404 and draws nothing;
    the page itself still loads."""
    monkeypatch.setattr(server, "LOGO", tmp_path / "nowhere.png")
    studio = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    local = server.LocalServer(studio, 0)
    local.start()

    def status(path):
        conn = http.client.HTTPConnection("127.0.0.1", local.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            conn.close()

    try:
        assert status("/assets/logo.png") == 404
        assert status("/") == 200
    finally:
        local.close()
        studio.shutdown()


def test_every_panel_has_a_rail_icon(tmp_path):
    studio = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    try:
        for entry in studio.call("rail"):
            assert entry["icon"] in shapes.SHAPES, entry["id"]
    finally:
        studio.shutdown()


def test_the_page_sits_on_the_theme_s_base_and_keeps_a_font_to_fall_back_on():
    assert f"--base: {theme.BASE_COLOR};" in server.theme_css()
    css = _stylesheets()
    ground = re.search(r"html, body \{([^}]*)\}", css).group(1)
    assert "background: var(--base);" in ground
    families = _font_list(css, "--font-ui")
    assert len(families) > 1 and families[-1] == "sans-serif", \
        "the platform's own font stays last in the list"


def test_a_call_is_set_in_a_fixed_pitch_face():
    """``monospace`` alone is fixed pitch in a browser, so it ends the list;
    the names before it are the faces it should look like."""
    css = _stylesheets()
    assert _font_list(css, "--font-mono")[-1] == "monospace"
    assert re.search(r"\.mono \{[^}]*font-family: var\(--font-mono\)", css)
    # every panel's call comes from ui.callText, the status bar's is in the page
    ui = (server.STATIC / "js" / "ui.js").read_text(encoding="utf-8")
    page = (server.STATIC / "index.html").read_text(encoding="utf-8")
    assert 'el("code", "call-text mono")' in ui
    assert 'class="call mono" id="call-hint"' in page
    # and no rule of the calls' own sets another face over .mono
    for rule in re.findall(r"\.call(?:-text)?\s*\{([^}]*)\}", css):
        assert not re.search(r"\bfont(-family)?\s*:", rule), rule
