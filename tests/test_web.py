# -*- coding: utf-8 -*-
"""The browser window's back end: the plot data, the player, the Studio
commands and the local server. No Qt and no browser; the page itself is
checked by hand in a browser and in the window."""
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
    """A Studio loads its language into `tr`; the Qt tests expect English."""
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
    """Loading clears the selection before it announces the audio; a state
    pushed in between used to carry the new audio under the old version,
    and the page kept its old view of the timeline."""
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
    """The body of a refused POST used to stay unread on the kept-alive
    connection and be taken for the next request line (a 501)."""
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
    def boom(self):
        raise RuntimeError("disk on fire")

    from kudiostudio.web import studio as studio_module
    monkeypatch.setitem(studio_module.COMMANDS, "revert", boom)
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

#: names the page shows as they are: kudio's own parameter names, and the
#: language menu's title, which is in both languages on purpose
UNTRANSLATED = {"STFT", "Mel", "MFCC", "n_fft", "hop_length", "n_mels", "n_mfcc",
                "Language / 語言"}


def _page_strings():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    found = {m.group(1).strip()
             for m in re.finditer(r"<\w+[^>]*\bdata-t\b(?!-)[^>]*>([^<]*)<", html)}
    found |= set(re.findall(r'data-t-title="([^"]*)"', html))
    for js in (STATIC / "js").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        found |= set(re.findall(r'\bt\("((?:[^"\\]|\\.)*)"\)', text))
        found |= set(re.findall(r'title: "([^"]*)"', text))
    return {s for s in found if s}


def test_every_string_on_the_page_has_a_chinese_translation():
    strings = _page_strings()
    assert "Waveform" in strings and "Select all" in strings
    table = i18n.table("zh_TW")
    missing = sorted(s for s in strings - UNTRANSLATED if s not in table)
    assert missing == []


def test_every_page_file_is_packaged():
    try:
        import tomllib
    except ImportError:
        tomllib = pytest.importorskip("tomli")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["kudiostudio"]
    package = ROOT / "kudiostudio"
    window_icon = package / "assets" / "logo.ico"
    for path in [window_icon, *STATIC.rglob("*")]:
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(package).as_posix()
            assert any(fnmatch(relative, p) for p in patterns), relative
    assert "web" in config["project"]["optional-dependencies"]
