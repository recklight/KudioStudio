# -*- coding: utf-8 -*-
"""The Pitch panel and the contour it draws.

pYIN is slow enough that the panel runs it on a worker; these tests call the
handlers directly, which is what the rest of the suite does for the same
reason. The `recording` fixture is a 180 Hz harmonic tone with an envelope, so
the answer is known before the test runs.
"""
from __future__ import annotations

import numpy as np
import pytest

import kudio
from kudiostudio.panels.pitch import CUSTOM_RANGE, RANGES, PitchPanel
from kudiostudio.session import AudioSession


@pytest.fixture
def panel(qapp, recording):
    session = AudioSession()
    session.load_file(recording)
    return PitchPanel(session)


def track_of(panel):
    """Run the analysis synchronously -- no worker thread in the test."""
    fmin, fmax, frame, hop = panel._params()
    y = panel.session.selected_audio()
    if y is None:
        y = panel.session.y
    return kudio.f0(y, panel.session.sr, fmin=fmin, fmax=fmax,
                    frame_length=frame, hop_length=hop)


# ---------------------------------------------------------------- the range

def test_the_default_range_is_speech(panel):
    assert panel.range_choice.currentIndex() == 0
    assert (panel.fmin.value(), panel.fmax.value()) == (65, 400)
    assert panel.fmin.isEnabled() is False          # a preset owns the bounds


def test_choosing_a_preset_writes_both_bounds(panel):
    panel.range_choice.setCurrentIndex(2)           # high voice
    _, lo, hi = RANGES[2]
    assert (panel.fmin.value(), panel.fmax.value()) == (int(lo), int(hi))
    assert f"fmin={lo:g}" in panel.call.text()


def test_typing_a_bound_selects_custom_rather_than_fighting_it(panel):
    panel.range_choice.setCurrentIndex(CUSTOM_RANGE)
    panel.fmin.setValue(90)
    assert panel.range_choice.currentIndex() == CUSTOM_RANGE
    assert panel.fmin.isEnabled() is True
    assert "fmin=90" in panel.call.text()


def test_a_preset_does_not_read_back_as_a_custom_edit(panel):
    """Setting the spin boxes from a preset must not bounce to Custom."""
    panel.range_choice.setCurrentIndex(1)
    assert panel.range_choice.currentIndex() == 1


def test_the_hop_is_shown_in_samples_because_that_is_what_kudio_takes(panel):
    panel.hop_ms.setValue(20)
    assert f"hop_length={20 * panel.session.sr // 1000}" in panel.call.text()


# -------------------------------------------------------------- the result

def test_a_known_tone_is_reported(panel):
    track = track_of(panel)
    panel._finished(track)

    assert track.median_hz == pytest.approx(180.0, rel=0.02)
    # isVisibleTo, not isVisible: the panel itself is never shown in the suite
    assert panel.result.isVisibleTo(panel)
    text = panel.result.text()
    assert f"median {track.median_hz:.1f} Hz" in text
    assert "semitones" in text
    assert "voiced" in text
    assert panel.markers_button.isEnabled()


def test_the_contour_is_offered_to_the_view(panel):
    seen = []
    panel.overlay_ready.connect(lambda t, hz: seen.append((t, hz)))
    panel._finished(track_of(panel))

    assert len(seen) == 1
    times, hz = seen[0]
    assert len(times) == len(hz)
    assert np.nanmedian(hz) == pytest.approx(180.0, rel=0.05)


def test_unvoiced_frames_stay_nan_so_the_line_breaks(panel):
    """A contour joined across a pause claims a pitch through the silence."""
    sr = panel.session.sr
    y = np.array(panel.session.y, copy=True)
    y[int(0.8 * sr):int(1.4 * sr)] = 0.0            # a hole in the middle
    panel.session.set_audio(y, sr)

    seen = []
    panel.overlay_ready.connect(lambda t, hz: seen.append((t, hz)))
    panel._finished(track_of(panel))

    times, hz = seen[0]
    gap = np.isnan(hz)
    assert gap.any()
    assert 0.8 < float(np.median(times[gap])) < 1.4


def test_the_contour_can_be_turned_off_and_on(panel):
    cleared, drawn = [], []
    panel.overlay_cleared.connect(lambda: cleared.append(True))
    panel.overlay_ready.connect(lambda t, hz: drawn.append(True))
    panel._finished(track_of(panel))
    assert len(drawn) == 1

    panel.overlay.setChecked(False)
    assert cleared == [True]
    panel.overlay.setChecked(True)
    assert len(drawn) == 2


def test_nothing_voiced_says_so_instead_of_drawing_nothing(panel, qapp):
    silence = np.zeros(16000, dtype=np.float32)
    panel.session.set_audio(silence, 16000)
    panel._finished(kudio.f0(silence, 16000))

    assert "Nothing voiced" in panel.result.text()
    assert panel.markers_button.isEnabled() is False


def test_a_failure_is_shown_rather_than_swallowed(panel):
    panel._failed("FeatureError: frame_length=1024 is too short for fmin=20 Hz")
    assert "too short" in panel.result.text()
    assert panel.markers_button.isEnabled() is False


def test_an_impossible_frame_length_is_refused_by_kudio(panel):
    """The panel does not second-guess it -- kudio owns the rule."""
    panel.range_choice.setCurrentIndex(CUSTOM_RANGE)
    panel.fmin.setValue(30)
    panel.frame_length.setCurrentIndex(0)               # 1024
    with pytest.raises(Exception, match="too short for fmin"):
        track_of(panel)


# ------------------------------------------------------------- the timeline

def test_a_selection_is_tracked_where_it_actually_sits(panel):
    """A contour measured from 1.0-1.5 s belongs at 1.0 s, not at zero."""
    panel.session.set_selection(1.0, 1.5)
    seen = []
    panel.overlay_ready.connect(lambda t, hz: seen.append(t))
    panel._offset = 1.0
    panel._finished(track_of(panel))

    times = seen[0]
    assert times[0] == pytest.approx(1.0, abs=0.02)
    assert times[-1] == pytest.approx(1.5, abs=0.05)


def test_the_scope_says_what_was_measured(panel):
    panel._run()
    panel.jobs.wait()
    assert "whole clip" in panel.scope.text()

    panel.session.set_selection(0.5, 1.0)
    panel._run()
    panel.jobs.wait()
    assert "selection" in panel.scope.text()


def test_markers_land_on_the_voiced_spans(panel):
    panel._finished(track_of(panel))
    panel._add_markers()

    markers = panel.session.markers
    assert markers
    spans = track_of(panel).voiced_segments()
    assert len(markers) == len(spans)
    assert markers[0].start == pytest.approx(spans[0][0], abs=1e-6)
    assert markers[0].label == "voiced 1"


# ------------------------------------------------------- the audio changed

def test_an_edit_drops_the_contour(panel):
    cleared = []
    panel.overlay_cleared.connect(lambda: cleared.append(True))
    panel._finished(track_of(panel))
    assert panel.markers_button.isEnabled()

    panel.on_session_changed()
    assert cleared == [True]
    assert panel.markers_button.isEnabled() is False
    assert panel.result.isVisibleTo(panel) is False


def test_nothing_is_dropped_when_there_was_no_contour(panel):
    cleared = []
    panel.overlay_cleared.connect(lambda: cleared.append(True))
    panel.on_session_changed()
    assert cleared == []
