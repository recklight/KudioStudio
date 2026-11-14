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

from test_recording import FakeCheckDevice, FakeRecorder, run_page


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    FakeRecorder.instances = []
    monkeypatch.setattr(kudio, "StreamRecorder", FakeRecorder)
    # no inputs unless a test lists some, as on CI, so the page tests read
    # the same lists on a machine with a microphone
    monkeypatch.setattr(kudio, "list_devices", lambda kind=None: [])
    monkeypatch.setattr(kudio, "CheckDevice", FakeCheckDevice)
    monkeypatch.setattr(FakeCheckDevice, "inputs", [])
    monkeypatch.setattr(FakeCheckDevice, "default", None)
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


def test_system_default_opens_the_input_pyaudio_calls_its_default(studio, panel,
                                                                   monkeypatch):
    """Given no index, StreamRecorder opens the last input PyAudio lists --
    here the line input, not the microphone the system records from."""
    monkeypatch.setattr(FakeCheckDevice, "inputs", [(1, "Microphone Array"), (4, "Line In")])
    monkeypatch.setattr(FakeCheckDevice, "default", 1)
    devices = studio.call("record.devices", [True])["devices"]
    assert devices == [["System default", 1], ["[1] Microphone Array", 1],
                       ["[4] Line In", 4]]
    studio.call("record.record", [tools.MODE_STREAM, 3, 16000, 1, devices[0][1], ""])
    assert FakeRecorder.instances[-1].device == 1


def test_with_no_default_input_the_first_choice_says_what_it_opens(studio, monkeypatch):
    monkeypatch.setattr(FakeCheckDevice, "inputs", [(1, "Microphone Array"), (4, "Line In")])
    label, index = studio.call("record.devices", [True])["devices"][0]
    assert index is None and label == tools.NO_DEFAULT_INPUT


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


def test_each_mode_remembers_its_own_device(studio, monkeypatch):
    """The two backends number devices independently: here 3 is the USB
    microphone to sounddevice and the line input to PyAudio. A choice must
    not follow the mode across just because its number exists on the other
    side."""
    monkeypatch.setattr(kudio, "list_devices",
                        lambda kind=None: [{"index": 3, "name": "USB microphone"}])
    monkeypatch.setattr(FakeCheckDevice, "inputs", [(3, "Line In"), (5, "USB microphone")])
    monkeypatch.setattr(FakeCheckDevice, "default", 5)
    seen = _page(studio, """
        await choose("Device", "3");
        await choose("Mode", modes[1]);
        seen.stream = { device: box("Device").value, call: lastCall()[4] };
        await choose("Device", "3");
        await choose("Mode", modes[0]);
        seen.fixed = box("Device").value;
        await choose("Mode", modes[1]);
        seen.stream_again = box("Device").value;
    """)
    assert seen["stream"] == {"device": "5", "call": 5}       # the system default
    assert seen["fixed"] == "3" and seen["stream_again"] == "3"


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
    figures_en, figures_zh = english.split("\n")[0], chinese.split("\n")[0]
    assert figures_zh.split("  ·  ")[1] == figures_en.split("  ·  ")[1]    # "-13.3 LUFS"
    # and kudio's findings under the figures, one line each
    assert english.count("\n") == chinese.count("\n") > 0
    assert "content stops" in english and "content stops" not in chinese


def test_a_clipped_take_is_named_in_the_status_bar_in_the_windows_language(studio,
                                                                           monkeypatch):
    """kudio's finding starts with a figure. Looked up together with the
    "Recorded, but:" in front of it, its own template took that prefix for
    the figure, and the line read "有 已錄音，但：8600 個樣本削波…"."""
    t = np.arange(16000) / 16000
    hot = np.clip(1.5 * np.sin(2 * np.pi * 220 * t), -1.0, 1.0).astype(np.float32)
    monkeypatch.setattr(kudio, "record", lambda seconds, sr, channels, device: hot)
    studio.call("set_language", ["zh_TW"])
    drain = _events(studio)
    studio.call("record.record", [tools.MODE_FIXED, 1, 16000, 1, None])
    assert studio.jobs.wait(10)
    first = kudio.audio_report(hot, 16000).problems()[0]
    assert "samples clipped" in first
    said = [e["text"] for e in drain("message")]
    assert said[-1] == "已錄音，但：" + i18n.translate(first, "zh_TW")
    assert said[-1].startswith("已錄音，但：有 ")


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
    assert {"type": "monitor", "on": False, "landed": True} in drain("monitor")


def test_a_take_announces_its_rate_before_any_audio(studio, panel):
    drain = _events(studio)
    _start(studio)
    assert [(e["on"], e["sr"]) for e in drain("monitor")] == [(True, 16000)]
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    _start(studio)                            # stop
    assert drain("monitor") == [{"type": "monitor", "on": False, "landed": True}]


def test_an_empty_take_is_not_committed(studio, panel):
    drain = _events(studio)
    _start(studio)
    _start(studio)                            # stop, with nothing captured
    assert studio.session.has_audio is False
    assert "Nothing was captured" in drain("message")[-1]["text"]


def test_the_page_is_told_whether_a_take_is_about_to_land(studio, panel, monkeypatch):
    """It keeps the last live picture for a take that lands, and goes back
    to the clip for one that does not."""
    drain = _events(studio)
    _start(studio)
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    _start(studio)
    _start(studio)
    _start(studio)                            # nothing this time

    def broken(self):
        raise OSError("the device went away")

    monkeypatch.setattr(FakeRecorder, "stop", broken)
    _start(studio)
    FakeRecorder.instances[-1].feed(np.full(1600, 0.2, np.float32))
    _start(studio)
    ended = [e["landed"] for e in drain("monitor") if not e["on"]]
    assert ended == [True, False, False]


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


def test_the_window_says_when_replacing_the_clip_would_lose_audio(studio, tone):
    studio.call("open_files", [[str(tone)]])
    assert studio.call("state")["unsaved"] is False
    studio.session.set_audio(studio.session.y * 0.5, 16000, undo_label="gain",
                             keep_original=True)
    assert studio.call("state")["unsaved"] is True


def test_a_take_asks_before_it_replaces_audio_no_file_holds(studio):
    """A take replaces the open clip, and edits not saved since, or a take
    never written, would go with it. Nothing to lose, nothing to ask."""
    info = studio.call("record.describe")
    seen = _page(studio, """
        let answer = false;
        const titles = [];
        ctx.dialogs = { confirmReplace: async (q) => { titles.push(q.title); return answer; } };
        const record = primary();
        mounted.state({ unsaved: false });
        await click(record);
        mounted.state({ unsaved: true });
        await click(record);                            // declined
        answer = true;
        await click(record);                            // agreed
        seen.asked = titles.length;
        seen.records = asked.filter(([name]) => name === "record").length;
    """, record=[info, info])
    assert seen == {"asked": 2, "records": 2}
