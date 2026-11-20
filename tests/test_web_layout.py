# -*- coding: utf-8 -*-
"""The window as a browser lays it out: sizes and scrolling, which the
stand-in document of test_web_views.py has no notion of.

Measured in a headless Chrome, Chromium or Edge, driven with the DevTools
client tools/screenshots.py makes the user guide's pictures with. Skipped
where none of them is installed or the one found will not start.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio.web.app import MIN_SIZE, WINDOW_SIZE
from kudiostudio.web.server import LocalServer
from kudiostudio.web.studio import Prefs, Studio

SCREENSHOTS = Path(__file__).resolve().parent.parent / "tools" / "screenshots.py"

#: Where the side panel's parts are, after the file list is scrolled to its
#: end: [top, bottom] in page pixels.
MEASURE = """(() => {
  const list = document.getElementById('file-list');
  list.scrollTop = list.scrollHeight;
  const box = (el) => { const r = el.getBoundingClientRect(); return [r.top, r.bottom]; };
  return {
    side: box(list.closest('aside')),
    list: box(list),
    last: box(list.lastElementChild),
    markers: box(document.getElementById('marker-list').closest('section')),
    marker_buttons: box(document.getElementById('marker-import')),
    scrolls: list.scrollHeight > list.clientHeight,
  };
})()"""


def _screenshots():
    spec = importlib.util.spec_from_file_location("_screenshots_for_layout", SCREENSHOTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    """A Studio, its server, and a headless browser showing the page at the
    window's own default size."""
    shots = _screenshots()
    try:
        shots.browser()
    except SystemExit:
        pytest.skip("no Chrome, Chromium or Edge to lay the page out")
    scratch = tmp_path_factory.mktemp("layout")
    studio = Studio(prefs=Prefs(scratch / "window.json"), language="en")
    server = LocalServer(studio, 0)
    server.start()
    try:
        process, tools = shots.open_browser(scratch / "profile")
    except SystemExit as e:
        server.close()
        studio.shutdown()
        pytest.skip(f"the browser did not start: {e}")
    try:
        page = shots.Page(tools, "", scratch, "en")
        page.size(*WINDOW_SIZE)
        page.open(server.url)
        yield studio, page, scratch
    finally:
        process.kill()
        server.close()
        studio.shutdown()


def _show(window, n: int, size=WINDOW_SIZE) -> dict:
    """*n* short takes in the file list of a window *size* big, and the side
    panel measured."""
    studio, page, scratch = window
    page.size(*size)
    folder = scratch / "takes"
    folder.mkdir(exist_ok=True)
    y = (0.1 * np.sin(2 * np.pi * 440 * np.arange(800) / 8000)).astype(np.float32)
    paths = []
    for i in range(n):
        path = folder / f"take_{i:03d}.wav"
        if not path.exists():
            kudio.save_wave(path, y, 8000)
        paths.append(str(path))
    with studio.lock:
        studio.files.clear()
    studio.call("open_files", [paths])
    page.wait(f"document.getElementById('file-list').children.length === {n}",
              what=f"{n} files in the list")
    page.settle(0.3)
    return page.js(MEASURE)


@pytest.mark.parametrize("size", [WINDOW_SIZE, MIN_SIZE], ids=["default", "smallest"])
def test_a_long_file_list_scrolls_inside_the_side_panel(window, size):
    """A folder of a few hundred takes grew the list past the bottom of the
    window: the files below it could not be reached, since nothing scrolled,
    and the markers went with them."""
    got = _show(window, 300, size)
    side_bottom = got["side"][1]
    assert got["scrolls"]
    assert got["list"][1] <= side_bottom + 0.5
    # scrolled to its end, the last file is inside the list's own box
    assert got["list"][0] - 0.5 <= got["last"][0] and got["last"][1] <= got["list"][1] + 0.5
    # and the markers, buttons and all, are still on screen
    assert got["markers"][1] <= side_bottom + 0.5
    assert got["marker_buttons"][1] <= side_bottom + 0.5


def test_a_short_file_list_leaves_the_rest_to_the_markers(window):
    """Three files take the room three files need; the markers have the
    remainder, as they always had."""
    got = _show(window, 3)
    side = got["side"][1] - got["side"][0]
    assert not got["scrolls"]
    assert got["markers"][1] - got["markers"][0] > side / 2
