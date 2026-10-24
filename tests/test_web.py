# -*- coding: utf-8 -*-
"""The window's back end: the plot data, the player, the Studio commands
and the local server. No browser; the page itself is checked by hand in a
browser and in the window."""
from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from fnmatch import fnmatch
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.shapes import SHAPES
from kudiostudio.web import analysis
from kudiostudio.web.player import Player
from kudiostudio.web.server import STATIC, LocalServer, colormap_bytes, theme_css
from kudiostudio.web.studio import Prefs, Studio

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def english_afterwards():
    """A Studio loads its language into `tr`; the tests that follow expect English."""
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def quiet(monkeypatch):
    """Playback that reports success and plays nothing."""
    played = []

    def play_audio(y, rate=None, **_):
        played.append((len(y), rate))

    monkeypatch.setattr(kudio, "play_audio", play_audio)
    return played


@pytest.fixture
def studio(tmp_path, quiet):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def server(studio):
    srv = LocalServer(studio, 0)
    srv.start()
    yield srv
    srv.close()


def _events(studio):
    seen = []
    q = studio.events.subscribe()

    def drain():
        while not q.empty():
            seen.append(q.get_nowait())
        return seen

    return drain


# ------------------------------------------------------------------ analysis

def test_peaks_hold_each_columns_lowest_and_highest_sample():
    sr = 1000
    y = np.zeros(1000, np.float32)
    y[10], y[20] = 0.9, -0.7          # both in the first of ten columns
    y[950] = 0.5                      # the last column
    p = analysis.peaks(y, sr, 0.0, 1.0, 10)
    assert p.kind == "peaks"
    assert p.values.shape == (10, 2)
    assert tuple(p.values[0]) == pytest.approx((-0.7, 0.9))
    assert tuple(p.values[9]) == pytest.approx((0.0, 0.5))
    assert p.step == pytest.approx(0.1, rel=0.01)


def test_zoomed_in_far_the_samples_themselves_are_sent():
    sr = 1000
    y = np.arange(1000, dtype=np.float32) / 1000
    p = analysis.peaks(y, sr, 0.5, 0.51, 400)
    assert p.kind == "samples"
    assert p.start == pytest.approx(0.5)
    assert p.step == pytest.approx(1 / sr)
    assert p.values[0] == pytest.approx(0.5)


def test_peaks_outside_the_clip_are_empty():
    p = analysis.peaks(np.ones(100, np.float32), 100, 5.0, 6.0, 10)
    assert p.kind == "samples" and len(p.values) == 0


def test_stereo_is_mixed_down_across_the_channels():
    """(frames, channels), as kudio.file_load gives it -- also for a clip of
    fewer frames than channels, where guessing by the shorter side mixes
    the frames together instead."""
    left_right = np.stack([np.ones(50), -np.ones(50) * 0.5], axis=1)
    assert analysis.mono(left_right).shape == (50,)
    assert analysis.mono(left_right)[0] == pytest.approx(0.25)
    one_frame = np.array([[0.2, 0.6]], np.float32)
    assert analysis.mono(one_frame).tolist() == pytest.approx([0.4])


def test_settings_refuse_what_kudio_would_choke_on():
    with pytest.raises(ValueError, match="unknown view"):
        analysis.Settings(mode="cqt").checked()
    with pytest.raises(ValueError, match="n_fft"):
        analysis.Settings(n_fft=16).checked()
    assert "n_mfcc=13" in analysis.Settings(mode="mfcc", n_mfcc=13).call()


@pytest.mark.parametrize("mode, top", [("stft", 4000.0), ("mel", 32.0), ("mfcc", 20.0)])
def test_each_view_spans_what_its_axis_says(mode, top):
    sr = 8000
    t = np.arange(sr) / sr
    y = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    img = analysis.image(y, sr, analysis.Settings(mode=mode, n_mels=32, n_mfcc=20))
    assert img.data.ndim == 2 and img.data.shape[0] > 10
    assert img.y_max == top
    assert img.duration == pytest.approx(1.0)
    assert img.levels[1] > img.levels[0]


def test_the_slice_puts_the_highest_bin_in_the_top_row():
    data = np.zeros((100, 8), np.float32)
    data[:, -1] = 1.0
    img = analysis.Image(data, 1.0, 8.0, "Frequency", "Hz", (0.0, 1.0))
    part = analysis.image_slice(img, 0.0, 1.0, columns=50, rows=8)
    assert part.pixels.shape == (8, 50)
    assert part.pixels.dtype == np.uint8
    assert (part.pixels[0] == 255).all() and (part.pixels[1:] == 0).all()


def test_a_one_frame_click_survives_the_zoomed_out_slice():
    data = np.zeros((10000, 4), np.float32)
    data[5003, 2] = 1.0
    img = analysis.Image(data, 10.0, 4.0, "Frequency", "Hz", (0.0, 1.0))
    part = analysis.image_slice(img, 0.0, 10.0, columns=100, rows=4)
    assert part.pixels.max() == 255


def test_a_zoom_into_the_axis_gets_its_bins_at_full_resolution():
    data = np.zeros((10, 1000), np.float32)
    data[:, 10] = 1.0                       # one bin near the bottom
    img = analysis.Image(data, 1.0, 1000.0, "Frequency", "Hz", (0.0, 1.0))
    whole = analysis.image_slice(img, 0.0, 1.0, columns=10, rows=50)
    assert whole.pixels.shape == (50, 10)   # pooled 20 bins to a row
    low = analysis.image_slice(img, 0.0, 1.0, columns=10, rows=50, y0=0.0, y1=50.0)
    assert low.pixels.shape == (50, 10)     # 50 bins, one each
    assert (low.y0, low.y1) == (0.0, 50.0)
    lit = np.flatnonzero(low.pixels[:, 0])
    assert lit.tolist() == [50 - 1 - 10]    # bin 10, counted from the top


def test_the_slice_reports_the_whole_frames_it_covers():
    img = analysis.Image(np.zeros((100, 4), np.float32), 1.0, 4.0, "", None, (0.0, 1.0))
    part = analysis.image_slice(img, 0.234, 0.5, columns=1000, rows=4)
    assert part.t0 == pytest.approx(0.23)
    assert part.t1 == pytest.approx(0.50)
    assert part.pixels.shape == (4, 27)


# -------------------------------------------------------------------- player

def test_the_player_says_when_it_ran_out(quiet):
    done = threading.Event()
    ends = []
    player = Player(on_finished=lambda end: (ends.append(end), done.set()))
    # short enough to wait for; whether it still plays when play() returns
    # depends on how long the first sounddevice import took
    playback = player.play(np.zeros(800, np.float32), 16000, t0=1.0)
    assert playback.start == 1.0 and playback.end == pytest.approx(1.05)
    assert done.wait(5.0)
    assert ends == [pytest.approx(1.05)]
    assert not player.playing
    assert player.position == pytest.approx(1.05)


def test_stopping_is_not_running_out(quiet):
    ends = []
    player = Player(on_finished=ends.append)
    player.play(np.zeros(16000, np.float32), 16000)
    time.sleep(0.05)
    where = player.stop()
    assert 0.0 < where < 1.0
    time.sleep(0.05)
    assert ends == [] and not player.playing


def test_a_backend_that_raises_is_reported_not_raised(monkeypatch):
    def broken(*_, **__):
        raise OSError("no output device")

    monkeypatch.setattr(kudio, "play_audio", broken)
    failed = threading.Event()
    messages = []
    player = Player(on_failed=lambda m: (messages.append(m), failed.set()))
    player.play(np.zeros(1600, np.float32), 16000)
    assert failed.wait(2.0)
    assert "no output device" in messages[0]
    assert not player.playing


def test_gain_is_applied_and_clipped(quiet, monkeypatch):
    got = []
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None: got.append(np.array(y)))
    player = Player()
    player.play(np.full(100, 0.5, np.float32), 1000, gain_db=12.0)
    player.stop()
    assert got[0].max() == 1.0


# -------------------------------------------------------------------- studio

def test_the_first_state_showing_new_audio_has_a_new_version(studio, tone):
    """Loading clears the selection before it announces the audio. Were the
    version a count of signals, a state pushed in between would carry the
    new audio under the old number, and the page would keep its view of the
    last clip's timeline."""
    drain = _events(studio)
    before = studio.version
    studio.call("open_files", [[str(tone)]])
    states = [e["state"] for e in drain() if e["type"] == "state"]
    with_audio = [s for s in states if s["has_audio"]]
    assert with_audio
    assert all(s["version"] > before for s in with_audio)
    assert len({s["version"] for s in with_audio}) == 1


def test_a_selection_is_not_new_audio_but_ab_is(studio, tone):
    studio.call("open_files", [[str(tone)]])
    first = studio.call("state")["version"]
    studio.call("set_selection", [0.1, 0.4])
    assert studio.call("state")["version"] == first
    y, sr = studio.session.y, studio.session.sr
    studio.session.set_audio(y * 0.5, sr, undo_label="halve", keep_original=True)
    edited = studio.call("state")["version"]
    assert edited > first
    state = studio.call("set_compare", [True])
    assert state["comparing"] and state["version"] > edited


def test_a_tiny_drag_clears_the_selection(studio, tone):
    studio.call("open_files", [[str(tone)]])
    assert studio.call("set_selection", [0.2, 0.5])["selection"] == [0.2, 0.5]
    assert studio.call("set_selection", [0.3, 0.30001])["selection"] is None


def test_the_file_list_keeps_each_file_once(studio, tone):
    studio.call("open_files", [[str(tone)]])
    state = studio.call("open_files", [[str(tone)]])
    assert [f["name"] for f in state["files"]] == ["tone.wav"]
    assert state["name"] == "tone.wav" and state["sr"] == 16000


def test_a_file_that_will_not_open_is_reported(studio, tmp_path):
    drain = _events(studio)
    bad = tmp_path / "not-audio.wav"
    bad.write_text("hello")
    state = studio.call("open_files", [[str(bad)]])
    assert not state["has_audio"]
    assert any(e["type"] == "message" and "not-audio.wav" in e["text"] for e in drain())


def test_analysis_settings_are_checked_and_kept(studio, tmp_path):
    state = studio.call("set_analysis", kwargs={"mode": "mel", "n_mels": "48"})
    assert state["analysis"]["mode"] == "mel" and state["analysis"]["n_mels"] == 48
    assert "n_mels=48" in state["call"]
    with pytest.raises(ValueError):
        studio.call("set_analysis", kwargs={"n_fft": 4})
    again = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    assert again.settings.mode == "mel" and again.settings.n_mels == 48


def test_playback_events_carry_the_timeline(studio, tone, quiet):
    studio.call("open_files", [[str(tone)]])
    studio.call("set_selection", [0.25, 0.5])
    drain = _events(studio)
    result = studio.call("play")
    assert result["playing"] and quiet
    stopped = studio.call("stop")
    assert not stopped["playing"]
    events = [e for e in drain() if e["type"] == "playback"]
    assert events[0] == {"type": "playback", "playing": True, "start": 0.25,
                         "end": pytest.approx(0.5)}
    assert events[-1]["playing"] is False
    assert 0.25 <= events[-1]["position"] <= 0.5


def test_seeking_moves_the_playhead_inside_the_clip(studio, tone):
    studio.call("open_files", [[str(tone)]])
    assert studio.call("seek", [0.4])["position"] == pytest.approx(0.4)
    assert studio.call("seek", [9.0])["position"] == pytest.approx(1.0)


def test_levels_are_measured_once_per_version(studio, tone):
    studio.call("open_files", [[str(tone)]])
    first = studio.call("levels")
    assert first["peak_dbfs"] == pytest.approx(20 * np.log10(0.4), abs=0.1)
    assert first["lufs"] is not None and not first["clipped"]
    assert studio.call("levels") is first
    # one second is shorter than the short-term window: no curve, not a bad one
    assert first["curve"] is None and first["short_term_s"] == kudio.SHORT_TERM


def test_the_loudness_curve_comes_with_the_levels(studio, tmp_path):
    sr = 16000
    t = np.arange(6 * sr) / sr
    y = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    path = tmp_path / "six.wav"
    kudio.save_wave(path, y, sr)
    studio.call("open_files", [[str(path)]])
    curve = studio.call("levels")["curve"]
    assert len(curve["times"]) == len(curve["lufs"]) > 10
    assert all(isinstance(v, float) or v is None for v in curve["lufs"])
    loud = [v for v in curve["lufs"] if v is not None]
    assert max(loud) == pytest.approx(-13.7, abs=1.5)


def test_switching_language_measures_the_problems_again(studio, tone):
    """The problem lines are translated as they are measured, so the cached
    ones would stay in the old language."""
    studio.call("open_files", [[str(tone)]])
    english = studio.call("levels")
    table = studio.call("set_language", ["zh_TW"])
    assert table["language"] == "zh_TW" and table["messages"]
    chinese = studio.call("levels")
    assert chinese is not english
    assert chinese["problems"] == [i18n.translate(p, "zh_TW") for p in english["problems"]]


def test_settings_survive_a_damaged_file(tmp_path):
    path = tmp_path / "window.json"
    path.write_text("{not json")
    prefs = Prefs(path)
    assert prefs.get("language") is None
    prefs.set("language", "zh_TW")
    assert Prefs(path).get("language") == "zh_TW"


# -------------------------------------------------------------------- server

def _get(server, path, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    if host is not None:
        conn.putrequest("GET", path, skip_host=True)
        conn.putheader("Host", host)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        conn.endheaders()
    else:
        conn.request("GET", path, headers=headers or {})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def _post(server, name, payload=None, token=True, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Kudio-Token"] = server.token
    body = raw if raw is not None else json.dumps(payload or {}).encode()
    conn.request("POST", f"/api/{name}", body=body, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response.status, data


def test_the_page_gets_the_token_only_from_a_request_that_had_it(server):
    _, without = _get(server, "/")
    assert server.token.encode() not in without
    assert b'name="kudio-token" content=""' in without
    _, with_token = _get(server, f"/?token={server.token}")
    assert server.token.encode() in with_token


def test_another_host_name_is_refused(server):
    response, _ = _get(server, f"/?token={server.token}", host="attacker.example:80")
    assert response.status == 403
    response, _ = _get(server, "/", host=f"localhost:{server.port}")
    assert response.status == 200


def test_commands_need_the_token(server):
    status, data = _post(server, "state", token=False)
    assert status == 403 and data == {"ok": False, "error": "token"}
    status, data = _post(server, "state")
    assert status == 200 and data["ok"] and data["result"]["has_audio"] is False


def test_a_refused_post_does_not_poison_the_next_request(server):
    """The body of a refused POST has to be read off the kept-alive
    connection, or it is taken for the next request line (a 501)."""
    body = b'{"args":[],"kwargs":{}}'
    refused = (f"POST /api/state HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
               ).encode() + body
    following = f"GET /theme.css HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n\r\n".encode()
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as s:
        s.sendall(refused + following)
        received = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                received += chunk
        except socket.timeout:
            pass
    assert received.startswith(b"HTTP/1.1 403")
    assert b"Connection: close" in received
    assert b" 501 " not in received


def test_unknown_commands_and_bad_bodies(server):
    assert _post(server, "no_such_thing")[0] == 404
    assert _post(server, "state", raw=b"{nope")[0] == 400
    status, data = _post(server, "set_selection", {"args": ["a", "b", "c"]})
    assert status == 400 and not data["ok"]


def test_a_failing_command_answers_instead_of_dropping(server, monkeypatch):
    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setitem(server.studio.commands, "revert", boom)
    status, data = _post(server, "revert")
    assert status == 200 and data == {"ok": False, "error": "RuntimeError: disk on fire"}


def test_plot_data_comes_as_bytes_with_its_description(server, tone):
    _post(server, "open_files", {"args": [[str(tone)]]})
    headers = {"X-Kudio-Token": server.token}
    response, body = _get(server, "/data/peaks?t0=0&t1=1&columns=100", headers)
    assert response.getheader("X-Kudio-Kind") == "peaks"
    values = np.frombuffer(body, "<f4").reshape(-1, 2)
    assert values.shape == (100, 2)
    assert values.max() == pytest.approx(0.4, abs=0.01)

    response, body = _get(server, "/data/image?t0=0&t1=1&columns=64&rows=32", headers)
    rows, columns = int(response.getheader("X-Kudio-Rows")), int(response.getheader("X-Kudio-Columns"))
    assert len(body) == rows * columns and rows <= 32 and columns <= 64
    assert response.getheader("X-Kudio-Units") == "Hz"
    assert float(response.getheader("X-Kudio-Ymax")) == 8000.0
    # 440 Hz sits low on the axis, so near the bottom row of the image
    pixels = np.frombuffer(body, np.uint8).reshape(rows, columns)
    assert pixels.mean(axis=1).argmax() > rows * 0.8

    # zoomed to 0-1000 Hz, the same tone is near the middle
    response, body = _get(server, "/data/image?t0=0&t1=1&columns=64&rows=32&y0=0&y1=1000",
                          headers)
    y1 = float(response.getheader("X-Kudio-Y1"))
    assert float(response.getheader("X-Kudio-Y0")) == 0.0 and 1000 <= y1 < 1032
    rows = int(response.getheader("X-Kudio-Rows"))
    pixels = np.frombuffer(body, np.uint8).reshape(rows, -1)
    assert 0.35 < pixels.mean(axis=1).argmax() / rows < 0.65

    response, _ = _get(server, "/data/peaks?t0=0&t1=1&columns=100")
    assert response.status == 403


def test_no_audio_is_an_empty_answer_not_an_error(server):
    headers = {"X-Kudio-Token": server.token}
    response, body = _get(server, "/data/image?t0=0&t1=1&columns=64&rows=32", headers)
    assert response.status == 200 and body == b""
    assert response.getheader("X-Kudio-Kind") == "empty"


def test_static_files_stay_inside_the_static_folder(server):
    response, body = _get(server, "/static/js/main.js")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/javascript")
    for sneaky in ("/static/../server.py", "/static/..%2fserver.py", "/static/%2e%2e/studio.py"):
        assert _get(server, sneaky)[0].status == 404


def test_theme_icons_and_colours_come_from_the_python_side(server):
    response, css = _get(server, "/theme.css")
    assert b"--base:" in css and b"--primary-soft:" in css
    assert css.decode() == theme_css()
    _, icons = _get(server, "/icons.json")
    assert json.loads(icons) == SHAPES
    _, lut = _get(server, "/colormap.bin")
    assert lut == colormap_bytes() and len(lut) == 768


def test_the_event_stream_starts_with_the_state(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("GET", f"/events?token={server.token}")
    response = conn.getresponse()
    assert response.getheader("Content-Type").startswith("text/event-stream")
    line = response.fp.readline()
    conn.close()
    event = json.loads(line.decode()[len("data: "):])
    assert event["type"] == "state" and "version" in event["state"]


# ---------------------------------------------------------------------- page

#: names the page shows as they are: kudio's own parameter names, Python's
#: None where a seed is left to chance (as the call reads), and the
#: language menu's title, which is in both languages on purpose
UNTRANSLATED = {"STFT", "Mel", "MFCC", "n_fft", "hop_length", "n_mels", "n_mfcc",
                "None", "Language / 語言"}

#: a keyword name, as the parameter lists label a parameter ("cutoff",
#: "db_range low"), a special value ("random"), or the kudio function a box
#: is for ("kudio.f0 (pYIN)", "kudio.record / kudio.StreamRecorder"): shown
#: as it is
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.]*( (low|high))?$"
                        r"|^kudio\.[\w.]+( \([^)]*\))?( / kudio\.[\w.]+)?$")

_JS_STRING = r'"((?:[^"\\]|\\.)*)"'
#: where the page modules hand English to `t`, directly or through a helper
_JS_CALLS = re.compile(
    r"(?:\bt|menuText|section|button|check|hint|\.row)\(\s*" + _JS_STRING)
_JS_KEYS = re.compile(
    r"\b(?:label|title|tip|text|placeholder|submit|special)\s*:\s*(?:t\()?" + _JS_STRING)
#: the TIPS table in main.js: id -> tooltip
_JS_TIPS = re.compile(r'^\s*"?[\w-]+"?\s*:\s*' + _JS_STRING + r",?\s*$", re.M)


def _js_strings(text):
    found = set()
    for pattern in (_JS_CALLS, _JS_KEYS):
        found |= {json.loads(f'"{s}"') for s in pattern.findall(text)}
    tips = text.split("const TIPS = {", 1)
    if len(tips) == 2:
        block = tips[1].split("};", 1)[0]
        found |= {json.loads(f'"{s}"') for s in _JS_TIPS.findall(block)}
    return found


def _page_strings():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    found = {m.group(1).strip()
             for m in re.finditer(r"<\w+[^>]*\bdata-t\b(?!-)[^>]*>([^<]*)<", html)}
    found |= set(re.findall(r'data-t-title="([^"]*)"', html))
    for js in (STATIC / "js").rglob("*.js"):
        found |= _js_strings(js.read_text(encoding="utf-8"))
    return {s for s in found if s and re.search(r"[A-Za-z]", s)}


def _needs_translation(text):
    return text not in UNTRANSLATED and not IDENTIFIER.match(text)


def _tool_strings():
    """What the panels' Python halves put on the page: the parameter lists'
    labels, tooltips, check box texts, notes, placeholders and dialog titles;
    the tool modules' tips, notes and choices; and the busy labels jobs run
    under, which the status bar shows."""
    import kudiostudio.tools as tools_package
    from kudiostudio.tools import effects, enhance, metrics, pitch
    found = set()
    for params in effects.PARAMS.values():
        for p in params:
            found |= {p.label, p.text, p.tip, p.placeholder, p.title, p.special}
    for module in (effects, enhance, metrics, pitch):
        for name, value in vars(module).items():
            if name.isupper() and isinstance(value, str) and \
                    re.search(r"(_TIP|_HINT|_NOTE|^NOTHING_)", name):
                found.add(value)
    found |= {label for label, _, _ in pitch.RANGES}
    found |= set(metrics.REFERENCES)
    found |= {text for text, _ in enhance.OTHER_METHODS}
    panels = Path(tools_package.__file__).parent.parent / "web" / "panels"
    for source in panels.glob("*.py"):
        found |= set(re.findall(r'start_job\(\s*"([^"]+)"', source.read_text(encoding="utf-8")))
    return {s for s in found if s}


def test_every_string_on_the_page_has_a_chinese_translation():
    strings = _page_strings() | _tool_strings()
    assert "Waveform" in strings and "Select all" in strings
    assert "Cu&t\tCtrl+X" in strings                     # a menu entry
    assert "zero-phase (no time shift)" in strings       # from tools.effects
    table = i18n.table("zh_TW")
    missing = sorted(s for s in strings if _needs_translation(s) and s not in table)
    assert missing == []


@pytest.mark.parametrize("module", sorted(p.relative_to(STATIC).as_posix()
                                          for p in (STATIC / "js").rglob("*.js")))
def test_every_page_module_parses(module):
    """A broken module loads as a blank panel, or a blank window, and says
    so only in the browser's console. Node reads them the way the page does."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "--input-type=module", "--check"],
                          input=(STATIC / module).read_text(encoding="utf-8"),
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr


def test_every_page_file_is_packaged():
    try:
        import tomllib
    except ImportError:
        tomllib = pytest.importorskip("tomli")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["kudiostudio"]
    package = ROOT / "kudiostudio"
    window_icon = package / "assets" / "logo.ico"
    def matches(relative, pattern):
        # as setuptools globs: a * stays inside one folder, which fnmatch's
        # does not -- "js/*.js" must not cover "js/panels/effects.js"
        parts, wanted = relative.split("/"), pattern.split("/")
        return len(parts) == len(wanted) and all(map(fnmatch, parts, wanted))

    for path in [window_icon, *STATIC.rglob("*")]:
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(package).as_posix()
            assert any(matches(relative, p) for p in patterns), relative
    # the window is the program, so what draws it is no extra
    needs = " ".join(config["project"]["dependencies"]).lower()
    assert "pywebview" in needs


# ----------------------------------------------------- the same clip, edited

def test_an_edit_of_the_same_clip_keeps_the_colour_scale(studio, tone):
    """A denoised clip drawn on its own levels looks as bright as the noisy
    one: the colour scale has to stay put while the clip is the same."""
    studio.call("open_files", [[str(tone)]])
    first = studio.image()[0].levels
    s = studio.session
    s.set_audio(s.y * 0.01, s.sr, undo_label="40 dB down", keep_original=True)
    assert studio.image()[0].levels == first
    # what frame-based processing gives back, 128 samples short, is the same clip
    s.set_audio(s.y[:-128], s.sr, undo_label="denoise", keep_original=True)
    assert studio.image()[0].levels == first
    # a trim is not, and neither is a view made another way
    s.set_audio(s.y[:len(s.y) // 4], s.sr, undo_label="trim", keep_original=True)
    trimmed = studio.image()[0].levels
    assert trimmed != first
    studio.call("set_analysis", kwargs={"n_fft": 1024})
    assert studio.image()[0].levels != trimmed


def test_another_file_of_the_same_length_gets_its_own_scale(studio, tone, tmp_path):
    studio.call("open_files", [[str(tone)]])
    first = studio.image()[0].levels
    y, sr = kudio.file_load(str(tone), sr=None)
    other = tmp_path / "quiet.wav"
    kudio.save_wave(other, (y * 0.01).astype(np.float32), sr)
    studio.call("open_files", [[str(other)]])
    assert studio.image()[0].levels != first


def test_a_pause_breaks_the_loudness_curve_rather_than_sinking_it(studio, tmp_path):
    """After sound, a pause measures the weighting filter's tail -- finite,
    and thousands of LU down -- not -inf. Drawn as a level it drags the
    curve's axis down with it."""
    from kudiostudio.web.studio import SILENCE_LUFS
    sr = 16000
    t = np.arange(12 * sr) / sr
    y = 0.3 * np.sin(2 * np.pi * 440 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.3 * t))
    y[4 * sr:8 * sr] = 0.0
    path = tmp_path / "pause.wav"
    kudio.save_wave(path, y.astype(np.float32), sr)
    studio.call("open_files", [[str(path)]])
    lufs = studio.call("levels")["curve"]["lufs"]
    assert None in lufs, "the pause is a gap"
    assert min(v for v in lufs if v is not None) >= SILENCE_LUFS


# ------------------------------------------------------------ the server, worn

def test_a_missing_logo_is_a_404_not_a_dropped_connection(server, monkeypatch, tmp_path):
    from kudiostudio.web import server as module
    monkeypatch.setattr(module, "LOGO", tmp_path / "logo.png")
    response, _ = _get(server, "/assets/logo.png")
    assert response.status == 404
    assert _get(server, "/theme.css")[0].status == 200


def test_a_request_the_page_gave_up_on_prints_no_traceback(server, capsys):
    """The page drops a request for peaks or an image whenever a newer one
    replaces it; the write into the closed socket is not worth a traceback.
    Anything else still is."""
    try:
        raise ConnectionAbortedError(10053, "the page went away")
    except ConnectionAbortedError:
        server.handle_error(None, ("127.0.0.1", 1))
    assert capsys.readouterr().err == ""
    try:
        raise ValueError("a real fault")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 1))
    assert "a real fault" in capsys.readouterr().err
