# -*- coding: utf-8 -*-
"""Loudness over time: the measurement, the readout, and the curve on the
waveform -- plus the axis alignment the shared time axis has always promised.
"""
from __future__ import annotations

import numpy as np
import pytest

import kudio
from kudiostudio.widgets.levels import (
    TRUE_PEAK_WARN_DBTP,
    LevelReadout,
    measure,
)
from kudiostudio.widgets.views import (
    LEFT_AXIS_WIDTH,
    MIN_LOUDNESS_SPAN,
    RIGHT_AXIS_WIDTH,
    AnalysisView,
)

SR = 16000


def varying(seconds: float = 12.0, dip_db: float = 14.0,
            amp: float = 0.5) -> np.ndarray:
    """A take whose level moves, so a curve has something to show."""
    t = np.arange(int(SR * seconds), dtype=np.float64) / SR
    gate = np.where((t % 4.0) < 2.0, 1.0, 10 ** (-dip_db / 20.0))
    return (amp * np.sin(2 * np.pi * 220 * t) * gate).astype(np.float32)


@pytest.fixture
def view(qapp):
    v = AnalysisView()
    v.resize(900, 500)
    yield v
    # `set_audio` debounces a measurement onto a worker. Dropping the view
    # while that thread is still running takes the QThread down with it, and
    # the suite stops dead on the *next* test rather than this one. The window
    # does the same thing in closeEvent.
    v.levels._timer.stop()
    v.levels.jobs.wait(5000)


# ------------------------------------------------------------- measurement

def test_one_pass_answers_everything_the_readout_shows(qapp):
    """Four measurements from one walk over the signal: run separately they
    would filter it four times and, worse, could describe four different
    moments of an edit."""
    y = varying()
    m = measure(y, SR)

    assert m.report is not None
    assert m.true_peak_db == pytest.approx(kudio.true_peak(y, SR), abs=1e-9)
    assert len(m.curve) > 1
    assert m.range_lu == pytest.approx(m.curve.range_lu, abs=1e-9)


def test_a_clip_shorter_than_the_window_still_measures_what_it_can(qapp):
    """The integrated figure and the true peak do not need three seconds."""
    m = measure(varying(seconds=1.0), SR)
    assert m.report is not None
    assert np.isfinite(m.true_peak_db)
    assert m.curve is None
    assert np.isnan(m.range_lu)


# ---------------------------------------------------------------- readout

def _settle(qapp, readout, ms=2500):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    readout.reported.connect(lambda _: loop.quit())
    loop.exec()
    readout.jobs.wait(3000)


def test_the_readout_shows_true_peak_and_range(qapp):
    readout = LevelReadout()
    readout.set_audio(varying(), SR)
    _settle(qapp, readout)

    assert "dBTP" in readout.true_peak.text()
    assert "LU" in readout.range.text()


def test_an_inter_sample_overshoot_is_reported_as_a_problem(qapp):
    """`audio_report` cannot see this one: no sample is wrong, and the file
    still clips the moment anything reconstructs the waveform."""
    n = np.arange(SR * 4)
    y = (0.999 * np.sin(2 * np.pi * (SR / 4) * n / SR + np.pi / 4)).astype(np.float32)

    assert 20 * np.log10(np.max(np.abs(y))) < -2.5      # samples look safe
    assert kudio.true_peak(y, SR) > TRUE_PEAK_WARN_DBTP  # the waveform is not

    readout = LevelReadout()
    readout.set_audio(y, SR)
    _settle(qapp, readout)
    assert "dBTP" in readout.problems.text()
    assert "clip on conversion" in readout.problems.text()


def test_a_quiet_take_is_not_accused_of_clipping(qapp):
    readout = LevelReadout()
    readout.set_audio(varying(amp=0.05), SR)
    _settle(qapp, readout)
    assert "clip on conversion" not in readout.problems.text()


def test_clearing_takes_the_curve_down_with_it(qapp):
    readout = LevelReadout()
    seen = []
    readout.curve_ready.connect(seen.append)
    readout.set_audio(None, 0)
    assert seen == [None]
    assert readout.true_peak.text() == "—"


# ------------------------------------------------------------- the curve

def test_the_curve_is_held_but_not_drawn_until_asked(view):
    y = varying()
    view.set_audio(y, SR)
    view.set_loudness_curve(kudio.loudness_over_time(y, SR))

    assert view._loudness is not None
    assert not view.waveform.loudness_visible

    view.show_loudness.setChecked(True)
    assert view.waveform.loudness_visible


def test_turning_it_on_and_off_costs_no_measurement(view):
    y = varying()
    view.set_audio(y, SR)
    view.set_loudness_curve(kudio.loudness_over_time(y, SR))
    held = view._loudness

    view.show_loudness.setChecked(True)
    view.show_loudness.setChecked(False)
    view.show_loudness.setChecked(True)
    assert view._loudness is held
    assert view.waveform.loudness_visible


def test_silence_breaks_the_line_rather_than_dropping_to_a_floor(view):
    sr = SR
    y = varying(seconds=12.0)
    y[int(4 * sr):int(8 * sr)] = 0.0

    curve = kudio.loudness_over_time(y, sr, window=kudio.MOMENTARY)
    assert np.isneginf(curve.lufs).any()

    view.set_audio(y, sr)
    view.set_loudness_curve(curve)
    view.show_loudness.setChecked(True)

    drawn = view.waveform._loudness.getData()[1]
    assert np.isnan(drawn).any(), "-inf has to become a gap, not a value"
    assert not np.isneginf(drawn).any()


def test_a_steady_take_does_not_have_its_dither_magnified(view):
    """Autoscaling to a tenth of a dB draws a level that looks all over the
    place when it is in fact perfectly steady."""
    t = np.arange(SR * 10) / SR
    steady = (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    view.set_audio(steady, SR)
    view.set_loudness_curve(kudio.loudness_over_time(steady, SR))
    view.show_loudness.setChecked(True)

    lo, hi = view.waveform._loudness_vb.viewRange()[1]
    assert hi - lo >= MIN_LOUDNESS_SPAN


def test_new_audio_replaces_the_curve_rather_than_keeping_it(view):
    y = varying()
    view.set_audio(y, SR)
    view.set_loudness_curve(kudio.loudness_over_time(y, SR))
    view.show_loudness.setChecked(True)
    assert view.waveform.loudness_visible

    view.set_loudness_curve(None)
    assert view._loudness is None
    assert not view.waveform.loudness_visible


def test_a_live_take_drops_the_curve(view):
    y = varying()
    view.set_audio(y, SR)
    view.set_loudness_curve(kudio.loudness_over_time(y, SR))
    view.show_loudness.setChecked(True)

    view.start_monitor(SR)
    assert view._loudness is None
    assert not view.waveform.loudness_visible


# ----------------------------------------------------------- axis alignment

def test_the_two_plots_start_at_the_same_place(view):
    """They are x-linked, which makes them show the same time range and does
    nothing about where that range lands on screen. Before this was pinned the
    waveform's left axis was 70 px and the spectrogram's 35-59, so the same
    instant sat in two different places -- and the offset changed when you
    switched modes."""
    for plot in (view.waveform, view.spectrogram):
        assert plot.plotItem.getAxis("left").fixedWidth == LEFT_AXIS_WIDTH


@pytest.mark.parametrize("sr", [8000, 16000, 48000])
def test_the_rate_does_not_move_one_plot_and_not_the_other(view, sr):
    y = (0.4 * np.sin(2 * np.pi * 220 * np.arange(4 * sr) / sr)).astype(np.float32)
    view.set_audio(y, sr)
    for mode in (view.MODE_STFT, view.MODE_MEL, view.MODE_MFCC):
        view.mode.setCurrentIndex(mode)
        left = {p.plotItem.getAxis("left").fixedWidth
                for p in (view.waveform, view.spectrogram)}
        assert left == {LEFT_AXIS_WIDTH}, mode


def test_the_loudness_gutter_is_reserved_on_both_plots_or_neither(view):
    """A gutter on one and not the other slides them apart -- which is the
    alignment the shared axis exists to guarantee."""
    y = varying()
    view.set_audio(y, SR)
    view.set_loudness_curve(kudio.loudness_over_time(y, SR))

    def right_axes():
        return [p.plotItem.getAxis("right").isVisible()
                for p in (view.waveform, view.spectrogram)]

    assert right_axes() == [False, False]
    view.show_loudness.setChecked(True)
    assert right_axes() == [True, True]
    # fixedWidth, not width(): the laid-out width of an axis that has only
    # just been shown is still 0 until the widget is on screen. What has to
    # hold is that the same number was asked for on both.
    assert {p.plotItem.getAxis("right").fixedWidth
            for p in (view.waveform, view.spectrogram)} == {RIGHT_AXIS_WIDTH}

    view.show_loudness.setChecked(False)
    assert right_axes() == [False, False]
