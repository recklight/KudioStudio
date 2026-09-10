# -*- coding: utf-8 -*-
"""What the Analysis pane draws that is not simply the clip: the rolling views
while a take is running, and the f0 contour over the spectrogram.

The interesting property of the first is not that a picture appears -- it is
that every column of it is computed once, as the audio lands, and that the
picture stops being live the moment real audio arrives. The interesting
property of the second is where it refuses to draw.
"""
from __future__ import annotations

import numpy as np
import pytest

from kudiostudio.widgets.views import (
    MONITOR_LEVELS,
    MONITOR_REDRAW_EVERY,
    MONITOR_SECONDS,
    AnalysisView,
)

SR = 16000


@pytest.fixture
def view(qapp):
    return AnalysisView()


def noise(n: int, seed: int = 0) -> np.ndarray:
    return (0.2 * np.random.default_rng(seed).standard_normal(n)).astype(np.float32)


# ------------------------------------------------------------------ lifecycle

def test_nothing_is_live_until_a_take_starts(view):
    assert view._monitor_sr == 0
    assert view._monitor_stream is None


def test_starting_a_take_builds_a_stream_at_the_take_rate(view):
    view.start_monitor(8000)
    assert view._monitor_sr == 8000
    assert view._monitor_stream.sr == 8000
    assert view._monitor_stream.window_seconds == pytest.approx(MONITOR_SECONDS,
                                                               abs=0.05)


def test_a_second_take_starts_from_an_empty_window(view):
    view.start_monitor(SR)
    view.feed_monitor(noise(SR), SR)
    assert view._monitor_stream.frames_seen > 0

    view.start_monitor(SR)
    assert view._monitor_stream.frames_seen == 0
    assert view._monitor_wave.size == 0


def test_a_chunk_arriving_without_a_start_is_believed(view):
    """A panel that forgets to announce itself should still draw, not crash."""
    view.feed_monitor(noise(4096), SR)
    assert view._monitor_sr == SR
    assert view._monitor_stream.frames_seen > 0


def test_a_rate_change_mid_stream_restarts_the_window(view):
    view.start_monitor(SR)
    view.feed_monitor(noise(4096), SR)
    view.feed_monitor(noise(4096), 8000)
    assert view._monitor_sr == 8000
    assert view._monitor_stream.sr == 8000


def test_stopping_leaves_the_last_picture_up(view):
    """Blanking it would hide the end of what was just recorded."""
    view.start_monitor(SR)
    for _ in range(MONITOR_REDRAW_EVERY):
        view.feed_monitor(noise(4096), SR)
    assert view.spectrogram.levels == MONITOR_LEVELS

    view.stop_monitor()
    assert view._monitor_sr == 0
    assert view._monitor_stream is None
    assert view.spectrogram.levels == MONITOR_LEVELS      # still on screen


# --------------------------------------------------------------- the audio

def test_every_column_is_computed_once_as_it_lands(view):
    """The whole reason this is affordable. Drawing is throttled; analysis is
    not -- a skipped feed would lose audio, a skipped redraw loses nothing."""
    view.start_monitor(SR)
    hop = view._monitor_stream.hop_length
    n_fft = view._monitor_stream.n_fft

    total = 0
    for _ in range(10):
        view.feed_monitor(noise(4096, seed=total), SR)
        total += 4096

    expected = (total - n_fft) // hop + 1
    assert view._monitor_stream.frames_seen == expected


def test_the_redraw_is_throttled_but_the_audio_is_not(view):
    view.start_monitor(SR)
    for _ in range(MONITOR_REDRAW_EVERY - 1):
        view.feed_monitor(noise(1600), SR)
    assert view._monitor_stream.frames_seen > 0
    assert view.spectrogram.levels is None               # not drawn yet

    view.feed_monitor(noise(1600), SR)
    assert view.spectrogram.levels == MONITOR_LEVELS


def test_the_waveform_keeps_only_the_window(view):
    view.start_monitor(SR)
    view.feed_monitor(noise(SR * 10), SR)
    assert view._monitor_wave.size == int(MONITOR_SECONDS * SR)


def test_an_empty_chunk_is_harmless(view):
    view.start_monitor(SR)
    view.feed_monitor(np.zeros(0, dtype=np.float32), SR)
    assert view._monitor_stream.frames_seen == 0


def test_multichannel_audio_is_mixed_down_not_flattened(view):
    """Flattening (n, channels) interleaves them into a signal nobody
    recorded -- `SpectrogramStream` refuses 2-D input for that reason, so the
    view has to mix down before it gets there."""
    left = noise(4096, seed=1)
    right = noise(4096, seed=2)

    view.start_monitor(SR)
    view.feed_monitor(np.stack([left, right], axis=1), SR)
    stereo = view._monitor_stream.columns()

    view.start_monitor(SR)
    view.feed_monitor((left + right) / 2.0, SR)
    assert np.array_equal(stereo, view._monitor_stream.columns())


# ------------------------------------------------------------------ settings

def test_the_mel_view_streams_mel_bands(view):
    view.mode.setCurrentIndex(view.MODE_MEL)
    view.n_mels.setValue(48)
    view.start_monitor(SR)
    view.feed_monitor(noise(8192), SR)

    assert view._monitor_stream.n_mels == 48
    assert view._monitor_stream.columns().shape[1] == 48


def test_mfcc_falls_back_to_the_stft_and_says_so(view):
    view.mode.setCurrentIndex(view.MODE_MFCC)
    view.start_monitor(SR)
    assert view._monitor_stream.n_mels == 0
    assert "MFCC has no live form" in view.call_hint.text()


def test_changing_the_geometry_mid_take_restarts_the_picture(view):
    view.start_monitor(SR)
    view.feed_monitor(noise(SR), SR)
    assert view._monitor_stream.frames_seen > 0

    view.n_fft.setValue(1024)
    assert view._monitor_stream.n_fft == 1024
    assert view._monitor_stream.frames_seen == 0


def test_the_caption_shows_the_live_call(view):
    view.start_monitor(SR)
    assert "SpectrogramStream" in view.call_hint.text()
    assert "push(chunk)" in view.call_hint.text()

    view.stop_monitor()
    assert "SpectrogramStream" not in view.call_hint.text()


# ------------------------------------------------------- real audio wins

def test_committing_a_take_ends_the_live_view(view):
    view.start_monitor(SR)
    for _ in range(MONITOR_REDRAW_EVERY):
        view.feed_monitor(noise(4096), SR)

    view.set_audio(noise(2 * SR), SR)
    assert view._monitor_sr == 0
    assert view._monitor_stream is None
    # the live dBFS range is not the offline log-power range; keeping it would
    # colour the committed take by a scale it was never measured on
    assert view.spectrogram.levels != MONITOR_LEVELS


def test_a_take_the_length_of_the_window_is_not_the_same_clip(view):
    """`_same_clip` compares durations, and a 3 s take next to a 3 s monitor
    window would otherwise look like an edit of it."""
    view.start_monitor(SR)
    for _ in range(MONITOR_REDRAW_EVERY):
        view.feed_monitor(noise(int(MONITOR_SECONDS * SR)), SR)

    view.set_audio(noise(int(MONITOR_SECONDS * SR)), SR)
    assert view.spectrogram.levels != MONITOR_LEVELS


# ------------------------------------------------------------------- overlay

def test_a_contour_is_drawn_over_the_stft(view):
    view.set_audio(noise(SR), SR)
    times = np.linspace(0.0, 1.0, 50)
    view.set_pitch_overlay(times, np.full(50, 180.0))
    assert view.spectrogram.overlay_visible


def test_the_contour_survives_a_redraw(view):
    """Every refresh replaces the image; a curve drawn once would vanish with
    the first parameter change."""
    view.set_audio(noise(SR), SR)
    view.set_pitch_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    view.refresh()
    assert view.spectrogram.overlay_visible


def test_the_contour_is_hidden_where_the_axis_is_not_hertz(view):
    """On a mel image "180" is band 180, which is a plausible-looking line in
    the wrong place."""
    view.set_audio(noise(SR), SR)
    view.set_pitch_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))

    view.mode.setCurrentIndex(view.MODE_MEL)
    assert not view.spectrogram.overlay_visible
    view.mode.setCurrentIndex(view.MODE_MFCC)
    assert not view.spectrogram.overlay_visible
    view.mode.setCurrentIndex(view.MODE_STFT)
    assert view.spectrogram.overlay_visible


def test_new_audio_drops_the_contour(view):
    view.set_audio(noise(SR), SR)
    view.set_pitch_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))

    view.set_audio(noise(2 * SR, seed=4), SR)
    assert not view.spectrogram.overlay_visible
    assert view._overlay is None


def test_a_live_take_hides_the_contour(view):
    view.set_audio(noise(SR), SR)
    view.set_pitch_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))

    view.start_monitor(SR)
    assert not view.spectrogram.overlay_visible


def test_an_empty_contour_is_simply_no_contour(view):
    view.set_audio(noise(SR), SR)
    view.set_pitch_overlay(np.zeros(0), np.zeros(0))
    assert not view.spectrogram.overlay_visible


def test_the_two_rolling_plots_agree_about_where_now_is(view):
    """They share a time axis. The spectrogram is always the full window wide
    with the newest column at the right; a waveform drawn from zero would put
    the same instant in two different places."""
    view.start_monitor(SR)
    for _ in range(MONITOR_REDRAW_EVERY):
        view.feed_monitor(noise(SR // 4), SR)          # 1 s into a 3 s window

    assert view.waveform.duration == pytest.approx(MONITOR_SECONDS, abs=0.05)
    x = view.waveform._curve.getData()[0]
    assert x[-1] == pytest.approx(MONITOR_SECONDS, abs=0.05)
    assert x[0] == pytest.approx(MONITOR_SECONDS - 1.0, abs=0.05)


def test_a_full_window_starts_at_zero_again(view):
    view.start_monitor(SR)
    view.feed_monitor(noise(int(2 * MONITOR_SECONDS * SR)), SR)
    view._draw_monitor()

    x = view.waveform._curve.getData()[0]
    assert x[0] == pytest.approx(0.0, abs=0.02)
    assert x[-1] == pytest.approx(MONITOR_SECONDS, abs=0.02)
