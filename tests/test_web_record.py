# -*- coding: utf-8 -*-
"""Record in the window: a fixed-length take, an open-ended one with
its meter, and the live monitor the plots draw while it runs."""
from __future__ import annotations

import base64
import json

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import record as tools
from kudiostudio.web.monitor import MONITOR_REDRAW_EVERY
from kudiostudio.web.studio import Prefs, Studio

from test_recording import FakeRecorder, run_page


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    FakeRecorder.instances = []
    monkeypatch.setattr(kudio, "StreamRecorder", FakeRecorder)
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def panel(studio):
    p = studio.panel("record")
    # the tests tick by hand, so the ticker thread must not tick as well
    p._tick_loop = lambda: None
    return p


def _events(studio):
    q = studio.events.subscribe()

    def drain(kind=None):
        out = []
        while not q.empty():
            e = q.get_nowait()
            if kind is None or e["type"] == kind:
                out.append(e)
        return out

    return drain


def _start(studio, monitor=""):
    return studio.call("record.record", [tools.MODE_STREAM, 3, 16000, 1, None, monitor])


def _page(studio, steps, record=()):
    """record.js against this window's describe() and device lists. The
    Record button gets the answers in *record*, one per press; call_text is
    answered with "" and read back from what the page asked for."""
    answers = {"describe": studio.call("record.describe"),
               "devices [true]": studio.call("record.devices", [True]),
               "devices [false]": studio.call("record.devices", [False]),
               "call_text": "", "record": list(record)}
    return run_page("panels/record.js", answers,
                    "const modes = answers.describe.modes;\n" + steps)


def test_a_fixed_length_take_is_the_first_mode(studio):
    info = studio.call("record.describe")
    assert info["modes"][0] == tools.MODE_FIXED and not info["streaming"]
    assert "kudio.record(" in studio.call("record.call_text", [False, 3.0, 16000, 1, None])


def test_the_call_follows_the_mode(studio):
    assert studio.call("record.call_text", [False, 2.5, 16000, 1, None]) == \
        "kudio.record(seconds=2.5, sr=16000, channels=1, device=None)"
    assert studio.call("record.call_text", [True, 2.5, 44100, 2, 3]).startswith(
        "kudio.StreamRecorder(device=3, sr=44100, channels=2)")


def test_the_device_list_says_which_index_space_it_is_in(studio):
    # the two backends number devices independently; the label has to say which
    assert "PyAudio" in studio.call("record.devices", [True])["space"]
    assert "sounddevice" in studio.call("record.devices", [False])["space"]


def test_the_page_opens_on_a_fixed_length_take(studio):
    seen = _page(studio, """
        seen.mode = box("Mode").value;
        seen.seconds_locked = box("seconds").input.disabled;
        seen.monitor = box("monitor through").value;
        seen.streaming = lastCall()[0];
    """)
    assert seen == {"mode": tools.MODE_FIXED, "seconds_locked": False, "monitor": "",
                    "streaming": False}


def test_switching_to_stream_mode_changes_the_call_and_the_index_space(studio):
    seen = _page(studio, """
        await choose("Mode", modes[1]);
        seen.stream = { seconds_locked: box("seconds").input.disabled,
                        streaming: lastCall()[0], space: text(/device indices/) };
        await choose("Mode", modes[0]);
        seen.fixed = { seconds_locked: box("seconds").input.disabled,
                       streaming: lastCall()[0], space: text(/device indices/) };
    """)
    stream, fixed = seen["stream"], seen["fixed"]
    assert stream["seconds_locked"] is True and stream["streaming"] is True
    assert "PyAudio" in stream["space"]
    assert fixed["seconds_locked"] is False and fixed["streaming"] is False
    assert "sounddevice" in fixed["space"]


def test_the_record_button_becomes_stop_and_the_mode_locks_while_a_take_runs(studio, panel):
    opened = _start(studio)
    FakeRecorder.instances[-1].feed(np.full(8000, 0.2, np.float32))
    stopped = _start(studio)
    assert opened["streaming"] and not stopped["streaming"]
    assert studio.session.has_audio and len(studio.session.y) == 8000

    seen = _page(studio, """
        await choose("Mode", modes[1]);
        const record = primary();
        seen.ready = record.textContent;
        await click(record);
        seen.running = { label: record.textContent, mode_locked: box("Mode").disabled };
        await click(record);                            // the same button, now Stop
        seen.stopped = { label: record.textContent, mode_locked: box("Mode").disabled };
    """, record=[opened, stopped])
    assert seen["ready"] == "● Start"
    assert seen["running"] == {"label": "■ Stop", "mode_locked": True}
    assert seen["stopped"] == {"label": "● Start", "mode_locked": False}


def test_a_fixed_take_is_committed_and_checked(studio, monkeypatch):
    t = np.arange(16000) / 16000
    monkeypatch.setattr(kudio, "record", lambda seconds, sr, channels, device:
                        (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
    studio.call("record.record", [tools.MODE_FIXED, 1, 16000, 1, None])
    assert studio.jobs.wait(10)
    assert studio.session.name == "recording" and studio.session.sr == 16000
    text, tone = studio.call("record.describe")["take"]
    assert text.startswith("peak ") and tone in ("ok", "warn")


def test_the_take_is_described_in_the_windows_language(studio, monkeypatch):
    t = np.arange(16000) / 16000
    monkeypatch.setattr(kudio, "record", lambda seconds, sr, channels, device:
                        (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
    studio.call("record.record", [tools.MODE_FIXED, 1, 16000, 1, None])
    assert studio.jobs.wait(10)
    english = studio.call("record.describe")["take"][0]
    studio.call("set_language", ["zh_TW"])
    chinese = studio.call("record.describe")["take"][0]
    assert english.startswith("peak ") and not chinese.startswith("peak ")
    assert chinese.split("  ·  ")[1] == english.split("  ·  ")[1]    # "-13.3 LUFS"


def test_an_open_take_starts_the_monitor_and_stop_commits_it(studio, panel):
    drain = _events(studio)
    assert _start(studio)["streaming"]
    assert studio.call("state")["monitoring"]
    started = drain("monitor")[0]
    assert started["on"] and started["sr"] == 16000
    FakeRecorder.instances[-1].feed(np.full(8000, 0.2, np.float32))
    panel.tick()
    info = _start(studio)                    # the same button, now Stop
    assert not info["streaming"]
    assert len(studio.session.y) == 8000
    assert {"type": "monitor", "on": False} in drain("monitor")


def test_a_take_announces_its_rate_before_any_audio(studio, panel):
    drain = _events(studio)
    _start(studio)
    assert [(e["on"], e["sr"]) for e in drain("monitor")] == [(True, 16000)]
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    _start(studio)                            # stop
    assert drain("monitor") == [{"type": "monitor", "on": False}]


def test_an_empty_take_is_not_committed(studio, panel):
    drain = _events(studio)
    _start(studio)
    _start(studio)                            # stop, with nothing captured
    assert studio.session.has_audio is False
    assert "Nothing was captured" in drain("message")[-1]["text"]


def test_the_meter_reads_the_level_and_the_time(studio, panel):
    drain = _events(studio)
    _start(studio)
    FakeRecorder.instances[-1].feed(np.zeros(1600, np.float32), level=0.5)
    panel.tick()
    meter = [e for e in drain("panel") if "meter" in e][-1]
    assert meter["meter"] == pytest.approx(20 * np.log10(0.5))
    assert meter["elapsed"] == pytest.approx(0.1)


def test_the_meter_follows_the_level(studio, panel):
    drain = _events(studio)
    _start(studio)
    FakeRecorder.instances[-1].feed(np.zeros(1600, np.float32), level=0.5)
    panel.tick()
    reading = [e for e in drain("panel") if "meter" in e][-1]
    seen = _page(studio, f"""
        mounted.event({json.dumps(reading)});
        seen.level = box("level").querySelector(".meter-text").textContent;
        seen.elapsed = box("level").querySelector(".elapsed").textContent;
    """)
    assert seen == {"level": "-6 dBFS", "elapsed": "0.10 s"}


def test_silence_pins_the_meter_at_the_floor(studio, panel):
    drain = _events(studio)
    _start(studio)
    FakeRecorder.instances[-1].feed(np.zeros(160, np.float32), level=0.0)
    panel.tick()
    assert [e for e in drain("panel") if "meter" in e][-1]["meter"] == tools.METER_FLOOR_DB


def test_arriving_audio_reaches_the_monitor_once(studio, panel, monkeypatch):
    """The display accumulates what it is given, so a sample sent twice is
    drawn twice at two different times -- and anything doing per-column
    analysis downstream computes it twice as well."""
    _start(studio)
    seen = []
    push = studio.monitor.push

    def spy(y, sr):
        seen.append((len(y), sr))
        push(y, sr)

    monkeypatch.setattr(studio.monitor, "push", spy)
    recorder = FakeRecorder.instances[-1]
    recorder.feed(np.full(1600, 0.3, np.float32))
    panel.tick()
    recorder.feed(np.full(800, 0.3, np.float32))
    panel.tick()
    panel.tick()                              # nothing new arrived
    assert seen == [(1600, 16000), (800, 16000)]
    assert len(studio.monitor.wave) == 2400


def test_the_monitor_sends_a_picture_every_few_arrivals(studio, panel):
    drain = _events(studio)
    _start(studio)
    recorder = FakeRecorder.instances[-1]
    for _ in range(MONITOR_REDRAW_EVERY):
        recorder.feed(np.full(800, 0.3, np.float32))
        panel.tick()
    pictures = [e for e in drain("monitor") if e.get("image")]
    assert len(pictures) == 1
    image = pictures[0]["image"]
    assert len(base64.b64decode(image["data"])) == image["rows"] * image["columns"]
    assert image["units"] == "Hz" and image["y_max"] == 8000.0
    wave = pictures[0]["wave"]
    # right-aligned on "now": 0.2 s held in a 3 s window starts near 2.8 s
    assert wave["start"] == pytest.approx(pictures[0]["span"] - 0.2, abs=0.01)


def test_mfcc_watches_the_stft_live(studio, panel):
    studio.call("set_analysis", kwargs={"mode": "mfcc"})
    drain = _events(studio)
    _start(studio)
    assert "MFCC has no live form" in drain("monitor")[0]["call"]


def test_the_length_cap_stops_the_take(studio, panel):
    drain = _events(studio)
    _start(studio)
    recorder = FakeRecorder.instances[-1]
    recorder.feed(np.full(4000, 0.1, np.float32))
    recorder.capped = True
    panel.tick()
    assert not panel.streaming and len(studio.session.y) == 4000
    said = [e["text"] for e in drain("message")]
    assert "Reached the recorder's length cap — stopping." in said


def test_a_device_that_will_not_open_is_reported(studio, monkeypatch):
    def explode(**kwargs):
        raise OSError("no such device")

    monkeypatch.setattr(kudio, "StreamRecorder", explode)
    drain = _events(studio)
    assert not _start(studio)["streaming"]
    said = [e["text"] for e in drain("message")]
    assert said[-1] == "Recording failed — OSError: no such device"


def test_a_stereo_take_is_mixed_down(studio, panel):
    _start(studio)
    recorder = FakeRecorder.instances[-1]
    recorder.channels = 2
    recorder._audio = np.array([[1.0, 0.0], [0.0, 1.0]], np.float32)
    _start(studio)
    assert studio.session.y.tolist() == [0.5, 0.5]


def test_the_monitor_is_off_by_default(studio, panel):
    off = studio.call("record.describe")["monitor_methods"][0]
    assert off["name"] == "" and off["label"] == tools.MONITOR_OFF
    _start(studio)
    assert panel._enhancer is None


def test_only_streamable_methods_are_offered(studio):
    offered = {m["name"] for m in studio.call("record.describe")["monitor_methods"]} - {""}
    assert offered == set(kudio.STREAMABLE_METHODS)
    assert "specsub" not in offered              # cannot stream


def test_choosing_a_method_builds_a_stream_enhancer_at_the_takes_rate(studio, panel):
    _start(studio, monitor="logmmse")
    assert panel._enhancer is not None
    assert panel._enhancer.method == "logmmse"
    assert panel._enhancer.sr == FakeRecorder.instances[-1].sr


def test_an_open_take_is_monitored_through_the_method_chosen_on_the_page(studio, panel):
    seen = _page(studio, """
        await choose("Mode", modes[1]);
        const monitor = box("monitor through");
        seen.monitor = { value: monitor.value, locked: monitor.disabled };
        await choose("monitor through", "logmmse");
        await click(primary());
        seen.asked = lastCall("record");
    """)
    assert seen["monitor"] == {"value": "", "locked": False}
    assert seen["asked"] == [tools.MODE_STREAM, 3, 16000, 1, None, "logmmse"]
    studio.call("record.record", seen["asked"])
    assert panel._enhancer is not None and panel._enhancer.method == "logmmse"


def test_the_live_denoiser_changes_the_display_not_the_take(studio, panel):
    _start(studio, monitor="wiener")
    noise = (0.2 * np.random.default_rng(0).standard_normal(16000)).astype(np.float32)
    FakeRecorder.instances[-1].feed(noise)
    for _ in range(8):
        panel.tick()
    shown = studio.monitor.wave
    assert shown.size, "nothing was drawn"
    assert np.isfinite(shown).all()
    assert np.sqrt(np.mean(shown ** 2)) < np.sqrt(np.mean(noise ** 2))
    _start(studio)
    assert np.array_equal(studio.session.y, noise)


def test_the_take_is_committed_raw_even_while_monitoring(studio, panel):
    """Monitoring is monitoring — the same rule the A/B follows."""
    _start(studio, monitor="logmmse")
    assert panel._enhancer is not None
    raw = (0.2 * np.random.default_rng(2).standard_normal(8000)).astype(np.float32)
    FakeRecorder.instances[-1].feed(raw)
    panel.tick()
    _start(studio)                            # stop
    assert np.allclose(studio.session.y, raw, atol=1e-6)


def test_monitoring_reports_how_much_it_is_removing(studio, panel):
    drain = _events(studio)
    _start(studio, monitor="logmmse")
    rng = np.random.default_rng(1)
    FakeRecorder.instances[-1].feed((0.2 * rng.standard_normal(16000)).astype(np.float32))
    for _ in range(8):
        panel.tick()
    said = [e["monitor_text"] for e in drain("panel") if "monitor_text" in e]
    assert said and "dB" in said[-1]


def test_a_denoiser_that_gives_up_keeps_the_display_alive(studio, panel):
    """A monitor that stops updating looks like a dead microphone."""
    _start(studio, monitor="wiener")

    def explode(block):
        raise RuntimeError("nope")

    panel._enhancer.process = explode
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    panel.tick()

    assert panel._enhancer is None
    # the raw block got through
    assert len(studio.monitor.wave) == 1600 and np.allclose(studio.monitor.wave, 0.2)
    assert studio.call("record.describe")["monitor"] == \
        ["live monitoring stopped — see the log", "warn"]


def test_stopping_releases_the_enhancer(studio, panel):
    _start(studio, monitor="wiener")
    FakeRecorder.instances[-1].feed(np.zeros(4000, np.float32))
    _start(studio)                            # stop
    assert panel._enhancer is None


def test_the_ticker_reads_the_device_on_its_own(studio):
    """The other tests tick by hand; this one leaves it to the thread."""
    import time
    _start(studio)
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    deadline = time.monotonic() + 5
    while len(studio.monitor.wave) < 1600 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(studio.monitor.wave) == 1600
    _start(studio)                            # stop
    assert not studio.panel("record").streaming


def test_shutdown_closes_an_open_take(studio, panel):
    _start(studio)
    recorder = FakeRecorder.instances[-1]
    studio.shutdown()
    assert not recorder.recording and not studio.monitor.running
    assert not panel.streaming


def test_new_view_settings_mid_take_start_the_picture_over(studio, panel):
    """A rolling view cannot analyse again what has scrolled past, so its
    left half and its right half would otherwise be two different views."""
    _start(studio)
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    panel.tick()
    before = studio.monitor.stream
    assert before.frames_seen > 0
    studio.call("set_analysis", kwargs={"n_fft": 2 * before.n_fft})
    after = studio.monitor.stream
    assert after is not before and after.n_fft == 2 * before.n_fft
    assert after.frames_seen == 0 and len(studio.monitor.wave) == 0
    # the take itself is untouched, and stopping still commits it
    _start(studio)
    assert len(studio.session.y) == 1600


def test_the_same_settings_leave_a_take_alone(studio, panel):
    _start(studio)
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    panel.tick()
    before = studio.monitor.stream
    studio.call("set_analysis", kwargs={"n_fft": before.n_fft})
    assert studio.monitor.stream is before


def test_a_second_take_of_the_same_length_gets_a_scale_of_its_own(studio, panel):
    """Takes have no path, and two of the same length are still two
    recordings: a quieter one drawn on the louder one's scale would look like
    nothing at all."""
    from kudiostudio.web import analysis
    noise = (0.3 * np.random.default_rng(0).standard_normal(16000)).astype(np.float32)
    scales = []
    for take in (noise, noise * 0.05):
        _start(studio)
        FakeRecorder.instances[-1].feed(take)
        panel.tick()
        _start(studio)
        scales.append(studio.image()[0].levels)
    assert scales[1] != scales[0]
    assert scales[1] == analysis.image(studio.session.y, 16000, studio.settings).levels
