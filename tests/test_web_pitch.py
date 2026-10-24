# -*- coding: utf-8 -*-
"""Pitch in the window: the job, the contour over the spectrogram,
and the voiced spans as markers. The `recording` fixture is a 180 Hz
harmonic tone with an envelope, so the answer is known before the test runs."""
from __future__ import annotations

import threading

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import pitch
from kudiostudio.web.studio import Prefs, Studio

SPEECH = (65, 400, 2048, 10)


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path, recording, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    s.call("open_files", [[str(recording)]])
    yield s
    s.shutdown()


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


def _track(studio, *args):
    info = studio.call("pitch.track", list(args or SPEECH))
    assert studio.jobs.wait(60)
    return info


def test_the_call_names_the_hop_in_samples(studio):
    sr = studio.session.sr
    text = studio.call("pitch.call_text", list(SPEECH))
    assert text == pitch.call_text(65.0, 400.0, 2048, pitch.hop_samples(10, sr))
    assert f"hop_length={round(0.01 * sr)}" in text


def test_a_known_tone_is_drawn_over_the_spectrogram(studio):
    drain = _events(studio)
    _track(studio)
    info = studio.call("pitch.describe")
    assert info["ok"] and info["has_track"]
    median = float(info["result"].split()[1])
    assert median == pytest.approx(180.0, rel=0.02)
    curve = studio.call("overlay")
    hz = np.array([np.nan if v is None else v for v in curve["hz"]])
    assert np.nanmedian(hz) == pytest.approx(180.0, rel=0.05)
    assert drain("overlay")[-1]["curve"] == curve


def test_a_selection_is_drawn_where_it_sits(studio):
    studio.call("set_selection", [0.5, 1.5])
    _track(studio)
    assert studio.call("pitch.describe")["scope"] == "the selection, 0.500–1.500 s"
    times = studio.call("overlay")["times"]
    assert times[0] >= 0.5 and times[-1] <= 1.5 + 0.05


def test_a_pause_stays_a_gap(studio):
    sr = studio.session.sr
    y = np.array(studio.session.y, copy=True)
    y[int(0.8 * sr):int(1.4 * sr)] = 0.0
    studio.session.set_audio(y, sr)
    _track(studio)
    assert None in studio.call("overlay")["hz"]


def test_voiced_spans_become_markers_on_the_timeline(studio):
    studio.call("set_selection", [0.5, 1.5])
    _track(studio)
    state = studio.call("pitch.add_markers") and studio.call("state")
    spans = [(m["start"], m["end"]) for m in state["markers"]]
    assert spans and all(0.5 <= a < b <= 1.55 for a, b in spans)
    assert state["markers"][0]["label"] == "voiced 1"


def test_the_contour_can_be_taken_down_and_put_back(studio):
    _track(studio)
    studio.call("pitch.set_overlay", [False])
    assert studio.call("overlay") is None
    studio.call("pitch.set_overlay", [True])
    assert studio.call("overlay") is not None


def test_an_edit_drops_the_contour(studio):
    _track(studio)
    studio.call("set_selection", [0.0, 0.2])
    studio.call("delete")
    assert studio.call("overlay") is None
    assert studio.call("pitch.describe")["has_track"] is False


def test_nothing_voiced_says_so(studio):
    studio.session.set_audio(np.zeros(16000, np.float32), 16000, name="silence")
    _track(studio)
    info = studio.call("pitch.describe")
    assert info["result"] == pitch.NOTHING_VOICED and not info["ok"]
    assert studio.call("overlay") is None


def test_an_impossible_frame_length_is_reported(studio):
    drain = _events(studio)
    _track(studio, 30, 400, 1024, 10)
    info = studio.call("pitch.describe")
    assert "too short for fmin" in info["result"] and not info["ok"]
    assert any(e["text"].startswith("Pitch tracking failed")
               for e in drain("message"))


def test_a_result_for_audio_since_replaced_is_dropped(studio, monkeypatch):
    gate = threading.Event()
    real = kudio.f0

    def slow(*args, **kwargs):
        gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(kudio, "f0", slow)
    studio.call("pitch.track", list(SPEECH))
    studio.call("set_selection", [0.0, 0.2])
    studio.call("delete")                   # the audio moves on mid-job
    gate.set()
    assert studio.jobs.wait(60)
    assert studio.call("overlay") is None
    assert studio.call("pitch.describe")["has_track"] is False


def test_a_second_run_waits_for_the_first(studio, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(kudio, "f0", lambda *a, **k: gate.wait(10))
    drain = _events(studio)
    studio.call("pitch.track", list(SPEECH))
    studio.call("pitch.track", list(SPEECH))
    gate.set()
    studio.jobs.wait(10)
    assert "Another job is still running." in [e["text"] for e in drain("message")]


def test_bounds_outside_the_boxes_are_clamped(studio):
    text = studio.call("pitch.call_text", [5, 99999, 2048, 0])
    assert "fmin=20," in text and "fmax=8000," in text
    with pytest.raises(ValueError):
        studio.call("pitch.call_text", [65, 400, 1000, 10])
