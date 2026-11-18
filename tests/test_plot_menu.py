# -*- coding: utf-8 -*-
"""The plots' right-click menu, Shift+click on them, and the commands the
menu runs at a point rather than at the playhead."""
from __future__ import annotations

import json
import shutil
import subprocess
import time

import numpy as np
import pytest

import kudio
from kudiostudio.web.player import Playback
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture
def studio(tmp_path, monkeypatch, tone):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    s.call("open_files", [[str(tone)]])
    yield s
    s.shutdown()


@pytest.fixture
def played(studio, monkeypatch):
    heard = []

    def play(y, sr, gain_db=0.0, t0=0.0):
        heard.append((round(t0, 3), round(t0 + len(y) / sr, 3)))
        return Playback(float(t0), float(t0) + len(y) / sr, time.monotonic())

    monkeypatch.setattr(studio.player, "play", play)
    return heard


def _node(script: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------------------ the commands

def test_play_from_a_point_leaves_selection_only_as_it_was(studio, played):
    studio.call("set_selection", [0.2, 0.4])
    assert studio.selection_only is True
    studio.call("play_range", [0.5])
    studio.call("play_range", [0.2, 0.4])
    assert played == [(0.5, 1.0), (0.2, 0.4)]
    assert studio.selection_only is True


def test_a_marker_a_paste_and_silence_land_where_the_menu_was_opened(studio):
    """At the point, not at the playhead, and with a selection elsewhere
    left alone, where a plain paste or insert would replace it."""
    s = studio.session
    studio.call("markers.add", [0.75])
    assert [m.start for m in s.markers] == [0.75]
    s.set_selection(0.1, 0.2)
    studio.call("copy")
    before = s.duration
    studio.call("seek", [0.05])
    studio.call("paste", [0.5])
    # what was pasted comes back selected
    assert s.selection() == pytest.approx((0.5, 0.6), abs=1e-3)
    s.set_selection(0.1, 0.2)
    studio.call("seek", [0.05])
    studio.call("insert_silence", [0.25, 0.9])
    y, sr = s.preview()
    assert np.abs(y[int(0.92 * sr):int(1.13 * sr)]).max() < 1e-6
    assert np.abs(y[int(0.80 * sr):int(0.88 * sr)]).max() > 0.1
    # each join takes its crossfade off: 5 ms at each end of each insert
    assert s.duration == pytest.approx(before + 0.1 + 0.25 - 4 * 0.005, abs=1e-3)
    # and a time past the end is held to the clip
    assert studio.position(99.0) == pytest.approx(s.duration)


# ------------------------------------------------------------- the items

MENU = """
globalThis.document = { addEventListener() {}, documentElement: { style: {} } };
globalThis.window = { addEventListener() {} };
const { plotMenuItems } = await import(__MODULE__);
const act = new Proxy({}, { get: (_, name) => (...args) => calls.push([name, ...args]) });
const calls = [];
const labels = (items) => items.map((i) => (i === "-" ? "-" : i.label));
const base = { selection: null, playing: false, can_paste: false, mode: "stft", nFft: 512,
               hop: 256, yZoomed: false, units: "Hz", label: "Frequency" };
"""


def _menu(steps: str):
    return _node(MENU.replace("__MODULE__", json.dumps(
        (STATIC / "js" / "contextmenu.js").as_uri())) + steps)


def test_the_menu_offers_what_applies_where_it_was_opened():
    seen = _menu("""
const bare = plotMenuItems({ which: "wave", time: 1.25, value: -0.5, onAxis: false }, base, act);
const sel = plotMenuItems({ which: "wave", time: 1.25, value: -0.5, onAxis: false },
                          { ...base, selection: [0.5, 1], can_paste: true }, act);
const playing = plotMenuItems({ which: "spec", time: 2, value: 2400, onAxis: false },
                              { ...base, playing: true, yZoomed: true }, act);
console.log(JSON.stringify({ bare: labels(bare), sel: labels(sel), playing: labels(playing) }));
""")
    assert seen["bare"] == [
        "Play from here", "Move the playhead here", "-", "Insert silence here…", "-",
        "Add a marker here", "-", "Zoom &out to whole clip", "Select &All", "-",
        "Copy 1.250 s, -0.500"]
    assert seen["sel"][:6] == ["Play from here", "Play the selection", "Move the playhead here",
                               "-", "Cu&t\tCtrl+X", "&Copy\tCtrl+C"]
    assert "Paste here" in seen["sel"] and "Paste over the selection" in seen["sel"]
    assert "Mark the selection" in seen["sel"]
    assert "&Clear selection" in seen["sel"] and "Zoom to &selection" in seen["sel"]
    assert seen["playing"][0] == "Stop" and "Play from here" not in seen["playing"]
    assert seen["playing"][-2:] == ["Copy 2.000 s, 2.4kHz", "Reset the frequency range"]


def test_each_item_runs_its_command_at_the_point():
    seen = _menu("""
const items = plotMenuItems({ which: "wave", time: 1.25, value: 0, onAxis: false },
                            { ...base, selection: [0.5, 1], can_paste: true }, act);
for (const item of items) if (item !== "-") item.run();
console.log(JSON.stringify(calls));
""")
    assert ["play", 1.25] in seen and ["play", 0.5, 1] in seen and ["seek", 1.25] in seen
    assert ["pasteAt", 1.25] in seen and ["silenceAt", 1.25] in seen and ["markAt", 1.25] in seen
    assert ["edit", "cut"] in seen and ["edit", "crop"] in seen
    assert ["copy", "1.250 s, 0.000"] in seen


def test_the_frequency_axis_has_a_menu_of_its_own():
    seen = _menu("""
const stft = plotMenuItems({ which: "spec", time: null, value: 0, onAxis: true },
                           { ...base, yZoomed: true }, act);
const mfcc = plotMenuItems({ which: "spec", time: null, value: 0, onAxis: true },
                           { ...base, mode: "mfcc", nFft: 64 }, act);
stft[2].run(); stft[3].run(); stft[6].run();
console.log(JSON.stringify({ stft: labels(stft), mfcc: labels(mfcc), calls,
  checked: stft.filter((i) => i.checked).map((i) => i.checked()),
  enabled: [stft[0].enabled(), mfcc[0].enabled()] }));
""")
    assert seen["stft"] == ["Reset the frequency range", "-",
                            "Finer frequency detail (n_fft 1024)", "Finer time detail (n_fft 256)",
                            "-", "STFT", "Mel", "MFCC"]
    # MFCC has no n_fft to trade
    assert seen["mfcc"] == ["Reset the frequency range", "-", "-", "STFT", "Mel", "MFCC"]
    assert seen["calls"] == [["resolution", 2], ["resolution", 0.5], ["mode", "mel"]]
    assert seen["checked"] == [True, False, False]
    assert seen["enabled"] == [True, False]


# --------------------------------------------------------------- the plots

def test_shift_click_carries_the_selection_to_the_click():
    """From the edge further away; from the playhead with no selection."""
    module = (STATIC / "js" / "timeplot.js").as_uri()
    seen = _node(f"""
globalThis.document = {{ documentElement: {{}} }};
globalThis.getComputedStyle = () => ({{ getPropertyValue: () => "" }});
globalThis.ResizeObserver = class {{ observe() {{}} }};
globalThis.window = {{ devicePixelRatio: 1 }};
const {{ TimePlot }} = await import({json.dumps(module)});
const canvas = {{ clientWidth: 1064, clientHeight: 200, getContext: () => ({{}}),
  addEventListener() {{}}, setPointerCapture() {{}}, style: {{}},
  getBoundingClientRect: () => ({{ left: 0, top: 0, width: 1064, height: 200 }}) }};
const view = {{ t0: 0, t1: 10, span: 10, duration: 10, onChange() {{}} }};
const chosen = [];
const plot = new TimePlot(canvas, view, {{ onSelect: (a, b) => chosen.push([a, b]) }});
plot.width = 1064; plot.height = 200; plot.requestDraw = () => {{}};
const click = (time) => {{
  const x = plot.timeToX(time);
  plot.pointerDown({{ button: 0, shiftKey: true, clientX: x, clientY: 100, pointerId: 1 }});
  plot.pointerUp({{ clientX: x, clientY: 100 }});
}};
plot.playhead = 2;
click(5);
plot.selection = [3, 6];
click(7);
click(3.5);
console.log(JSON.stringify(chosen.map(([a, b]) => [+a.toFixed(3), +b.toFixed(3)])));
""")
    assert seen == [[2, 5], [3, 7], [3.5, 7]]


def test_a_right_click_is_the_windows_own_on_the_plots():
    page = (STATIC / "js" / "timeplot.js").read_text(encoding="utf-8")
    assert 'addEventListener("contextmenu"' in page and "e.preventDefault()" in page
