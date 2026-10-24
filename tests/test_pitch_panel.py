# -*- coding: utf-8 -*-
"""The Pitch panel and the contour it draws.

The `recording` fixture is a 180 Hz harmonic tone with an envelope, so the
answer is known before the test runs. `track_of` runs the same analysis the
panel runs, here and now, for the numbers the panel's answer should match.
The range box, the two bounds and the check box are the page's own, and so
is what the result line and the markers button show for a given state;
those tests run pitch.js in node (`run_page`).
"""
from __future__ import annotations

import numpy as np
import pytest

import kudio
from kudiostudio.tools import pitch
from kudiostudio.web.studio import Prefs, Studio

from test_recording import run_page

SPEECH = (65, 400, 2048, 10)

#: for the page tests: the range with no bounds of its own, and what the
#: two bound boxes hold
PAGE_HELPERS = """
const custom = String(answers.describe.ranges.findIndex((r) => r.fmin === null));
const bounds = () => [box("fmin").value, box("fmax").value];
"""


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


def track_of(studio, fmin=65, fmax=400, frame_length=2048, hop_ms=10):
    s = studio.session
    y = np.asarray(s.selected_audio(), dtype=np.float32)
    return kudio.f0(y, s.sr, fmin=fmin, fmax=fmax, frame_length=frame_length,
                    hop_length=pitch.hop_samples(hop_ms, s.sr))


def _hz(curve):
    return np.array([np.nan if v is None else v for v in curve["hz"]])


def _page(studio, steps):
    """pitch.js against this window's describe(); call_text is answered
    with "" and read back from what the page asked for."""
    answers = {"describe": studio.call("pitch.describe"), "call_text": ""}
    return run_page("panels/pitch.js", answers, PAGE_HELPERS + steps)


def _shown(studio):
    """The result line as the page shows it, None while it is hidden, and
    whether the markers button can be pressed."""
    return _page(studio, """
        const nodes = [...walk(root)];
        const result = nodes.find((n) => n.className.includes("readout-line"));
        const markers = nodes.find((n) => n.textContent.startsWith("Voiced spans"));
        seen.result = result.hidden ? null : result.textContent;
        seen.markers = !markers.disabled;
    """)


# ---------------------------------------------------------------- the range

def test_the_default_range_is_speech(studio):
    first = studio.call("pitch.describe")["ranges"][0]
    assert first["label"] == pitch.RANGES[0][0]
    assert (first["fmin"], first["fmax"]) == (65, 400)


def test_the_page_opens_on_speech(studio):
    seen = _page(studio, """
        seen.range = box("range").value;
        seen.bounds = bounds();
    """)
    assert seen == {"range": "0", "bounds": [65, 400]}


def test_a_preset_carries_both_bounds_into_the_call(studio):
    high = studio.call("pitch.describe")["ranges"][2]           # high voice
    _, lo, hi = pitch.RANGES[2]
    assert (high["fmin"], high["fmax"]) == (int(lo), int(hi))
    text = studio.call("pitch.call_text", [high["fmin"], high["fmax"], 2048, 10])
    assert f"fmin={lo:g}" in text and f"fmax={hi:g}" in text


def test_choosing_a_preset_writes_both_bounds(studio):
    seen = _page(studio, """
        await choose("range", 2);                       // high voice
        seen.bounds = bounds();
        seen.call = lastCall();
    """)
    _, lo, hi = pitch.RANGES[2]
    assert seen["bounds"] == [int(lo), int(hi)]
    assert seen["call"] == [int(lo), int(hi), 2048, 10]


def test_a_typed_bound_goes_into_the_call_as_typed(studio):
    # Custom is the one range with no bounds of its own to write over the
    # boxes, so what was typed stays
    custom = studio.call("pitch.describe")["ranges"][pitch.CUSTOM_RANGE]
    assert (custom["fmin"], custom["fmax"]) == (None, None)
    assert "fmin=90" in studio.call("pitch.call_text", [90, 400, 2048, 10])


def test_typing_a_bound_selects_custom_rather_than_fighting_it(studio):
    seen = _page(studio, """
        await choose("range", custom);
        await type("fmin", 90);
        seen.custom = { range: box("range").value, locked: box("fmin").disabled,
                        call: lastCall() };
        await choose("range", 0);
        await type("fmin", 90);                         // over a preset this time
        seen.preset = box("range").value;
    """)
    custom = str(pitch.CUSTOM_RANGE)
    assert seen["custom"] == {"range": custom, "locked": False, "call": [90, 400, 2048, 10]}
    assert seen["preset"] == custom


def test_a_preset_does_not_read_back_as_a_custom_edit(studio):
    """Setting the boxes from a preset must not bounce to Custom."""
    seen = _page(studio, """
        await choose("range", 1);
        seen.range = box("range").value;
        seen.bounds = bounds();
    """)
    _, lo, hi = pitch.RANGES[1]
    assert seen == {"range": "1", "bounds": [int(lo), int(hi)]}


def test_the_hop_is_shown_in_samples_because_that_is_what_kudio_takes(studio):
    text = studio.call("pitch.call_text", [65, 400, 2048, 20])
    assert f"hop_length={20 * studio.session.sr // 1000}" in text


# -------------------------------------------------------------- the result

def test_a_known_tone_is_reported(studio):
    _track(studio)
    track = track_of(studio)
    assert track.median_hz == pytest.approx(180.0, rel=0.02)

    info = studio.call("pitch.describe")
    text = info["result"]
    assert f"median {track.median_hz:.1f} Hz" in text
    assert "semitones" in text
    assert "voiced" in text
    assert info["has_track"]
    assert _shown(studio) == {"result": text, "markers": True}


def test_the_contour_is_offered_to_the_view_once(studio):
    drain = _events(studio)
    _track(studio)
    curves = [e["curve"] for e in drain("overlay")]
    assert len(curves) == 1
    assert len(curves[0]["times"]) == len(curves[0]["hz"])
    assert np.nanmedian(_hz(curves[0])) == pytest.approx(180.0, rel=0.05)


def test_unvoiced_frames_stay_gaps_so_the_line_breaks(studio):
    """A contour joined across a pause claims a pitch through the silence."""
    sr = studio.session.sr
    y = np.array(studio.session.y, copy=True)
    y[int(0.8 * sr):int(1.4 * sr)] = 0.0            # a hole in the middle
    studio.session.set_audio(y, sr)
    _track(studio)

    curve = studio.call("overlay")
    times = np.array(curve["times"])
    gap = np.isnan(_hz(curve))
    assert gap.any()
    assert 0.8 < float(np.median(times[gap])) < 1.4


def test_the_contour_can_be_turned_off_and_on(studio):
    drain = _events(studio)
    _track(studio)
    assert [e["curve"] is not None for e in drain("overlay")] == [True]

    studio.call("pitch.set_overlay", [False])
    assert [e["curve"] for e in drain("overlay")] == [None]
    studio.call("pitch.set_overlay", [True])
    assert [e["curve"] is not None for e in drain("overlay")] == [True]


def test_the_check_box_takes_the_contour_down_and_puts_it_back(studio):
    seen = _page(studio, """
        const overlay = [...walk(root)].find((n) => n.className === "check").input;
        seen.checked = overlay.checked;
        for (const on of [false, true]) {
          overlay.checked = on;
          overlay.fire("change");
          await settle();
          seen[on ? "on" : "off"] = lastCall("set_overlay");
        }
    """)
    assert seen == {"checked": True, "off": [False], "on": [True]}


def test_nothing_voiced_says_so_and_offers_no_markers(studio):
    studio.session.set_audio(np.zeros(16000, dtype=np.float32), 16000)
    _track(studio)
    info = studio.call("pitch.describe")
    assert "Nothing voiced" in info["result"]
    assert info["has_track"] is False
    assert _shown(studio) == {"result": info["result"], "markers": False}


def test_a_failure_is_shown_rather_than_swallowed(studio):
    _track(studio)
    assert studio.call("pitch.describe")["has_track"]
    studio.panel("pitch")._failed(
        "FeatureError: frame_length=1024 is too short for fmin=20 Hz")
    info = studio.call("pitch.describe")
    assert "too short" in info["result"] and not info["ok"]
    assert info["has_track"] is False
    assert _shown(studio) == {"result": info["result"], "markers": False}


# ------------------------------------------------------------- the timeline

def test_a_selection_is_tracked_where_it_actually_sits(studio):
    """A contour measured from 1.0-1.5 s belongs at 1.0 s, not at zero."""
    studio.call("set_selection", [1.0, 1.5])
    _track(studio)
    times = studio.call("overlay")["times"]
    assert times[0] == pytest.approx(1.0, abs=0.02)
    assert times[-1] == pytest.approx(1.5, abs=0.05)


def test_the_scope_says_what_was_measured(studio):
    _track(studio)
    assert "whole clip" in studio.call("pitch.describe")["scope"]

    studio.call("set_selection", [0.5, 1.0])
    _track(studio)
    assert "selection" in studio.call("pitch.describe")["scope"]


def test_markers_land_on_the_voiced_spans(studio):
    _track(studio)
    studio.call("pitch.add_markers")

    markers = studio.session.markers
    assert markers
    spans = track_of(studio).voiced_segments()
    assert len(markers) == len(spans)
    assert markers[0].start == pytest.approx(spans[0][0], abs=1e-6)
    assert markers[0].label == "voiced 1"


# ------------------------------------------------------- the audio changed

def test_an_edit_drops_the_contour_and_the_result(studio):
    _track(studio)
    assert studio.call("pitch.describe")["has_track"]

    drain = _events(studio)
    studio.call("set_selection", [0.0, 0.2])
    studio.call("delete")
    assert [e["curve"] for e in drain("overlay")] == [None]
    info = studio.call("pitch.describe")
    assert info["has_track"] is False
    assert info["result"] == ""
    assert _shown(studio) == {"result": None, "markers": False}


def test_nothing_is_dropped_when_there_was_no_contour(studio):
    drain = _events(studio)
    studio.call("set_selection", [0.0, 0.2])
    studio.call("delete")
    events = drain()
    assert [e for e in events if e["type"] == "overlay"] == []
    assert [e for e in events if e["type"] == "panel" and e["panel"] == "pitch"] == []
