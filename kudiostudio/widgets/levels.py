# -*- coding: utf-8 -*-
"""Level, loudness and "is this recording usable" — `kudio.audio_report`.

Every metric in the Metrics panel needs a clean reference to compare against.
A real recording has none, so the questions that matter first — did it clip,
is it far too quiet, is this 16 kHz file really 8 kHz audio — had nowhere to
be answered. They are answered here, continuously, for whatever is loaded.
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

import kudio
from kudiostudio.theme import CALL_COLOR, HINT_COLOR, MONO_CSS, OK_COLOR, WARN_COLOR
from kudiostudio.workers import WorkerHost

log = logging.getLogger(__name__)

#: Above this, a converter or a lossy encoder is likely to clip on the
#: waveform between the samples even though no sample is at full scale. -1 dBTP
#: is the usual delivery ceiling; the warning fires at the ceiling itself.
TRUE_PEAK_WARN_DBTP = -1.0


class Measurement(NamedTuple):
    """One pass over the audio, answering everything the readout shows.

    All four come from the same worker job because they walk the same signal:
    running them separately would filter it four times and, worse, let the
    panel show numbers measured from four different moments of an edit.
    """

    report: object                       # kudio.AudioReport
    true_peak_db: float
    curve: Optional[object]              # kudio.LoudnessCurve, or None
    range_lu: float


def measure(y, sr) -> Measurement:
    """Everything the Levels box reports, off the GUI thread."""
    report = kudio.audio_report(y, sr)
    peak = kudio.true_peak(y, sr)
    try:
        curve = kudio.loudness_over_time(y, sr)
    except Exception as e:               # clip shorter than the 3 s window
        log.debug("no loudness curve: %s", e)
        return Measurement(report, peak, None, float("nan"))
    return Measurement(report, peak, curve, curve.range_lu)


class LevelReadout(QGroupBox):
    """Peak, loudness and warnings for the current audio, measured off-thread.

    A full report walks the signal twice and filters it once, which is
    milliseconds on a clip and half a second on a long file -- too slow to run
    inline on every edit. It is therefore debounced onto a worker, and a
    request arriving while one is running simply reschedules.
    """

    #: a fresh :class:`kudio.AudioReport` for the audio last handed in
    reported = Signal(object)

    #: the short-term loudness curve for the same audio, or ``None`` when the
    #: clip is shorter than the window. Emitted with every report, so whatever
    #: draws it is never showing a curve from a previous edit.
    curve_ready = Signal(object)

    #: long enough that dragging an effect parameter does not queue a report
    DEBOUNCE_MS = 250

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Levels", parent)
        self._pending: Optional[tuple] = None
        self._report: Optional["kudio.AudioReport"] = None
        self.jobs = WorkerHost(self)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._measure)

        self.peak = QLabel("—")
        self.true_peak = QLabel("—")
        self.true_peak.setToolTip(
            "The peak of the waveform between the samples (BS.1770-4 Annex 2).\n"
            "Samples are points on a curve that passes between them, and the\n"
            "curve overshoots — so a file with no sample at full scale can\n"
            "still clip in a converter or a lossy encoder.")
        self.loudness = QLabel("—")
        self.range = QLabel("—")
        self.range.setToolTip(
            "Loudness range, EBU Tech 3342: how far the loud parts sit above\n"
            "the quiet ones. A consistent delivery lands near 3-6 LU.")
        self.bandwidth = QLabel("—")
        self._readouts = (self.peak, self.true_peak, self.loudness,
                          self.range, self.bandwidth)
        for label in self._readouts:
            label.setStyleSheet(f"color: {CALL_COLOR}; {MONO_CSS}")

        self.problems = QLabel("")
        self.problems.setWordWrap(True)
        self.problems.setVisible(False)

        form = QFormLayout()
        form.addRow("peak", self.peak)
        form.addRow("true peak", self.true_peak)
        form.addRow("loudness", self.loudness)
        form.addRow("range", self.range)
        form.addRow("bandwidth", self.bandwidth)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.problems)
        self.setMaximumWidth(260)

    # ------------------------------------------------------------------ data

    @property
    def report(self) -> Optional["kudio.AudioReport"]:
        """The most recent report, or ``None`` before one has been made."""
        return self._report

    def set_audio(self, y: Optional[np.ndarray], sr: int) -> None:
        if y is None or sr <= 0 or len(y) == 0:
            self._pending = None
            self._timer.stop()
            self.clear()
            return
        self._pending = (np.asarray(y, dtype=np.float32), int(sr))
        self._timer.start()

    def clear(self) -> None:
        self._report = None
        for label in self._readouts:
            label.setText("—")
            label.setStyleSheet(f"color: {CALL_COLOR}; {MONO_CSS}")
        self.problems.setVisible(False)
        self.curve_ready.emit(None)

    # -------------------------------------------------------------- internal

    def _measure(self) -> None:
        if self._pending is None:
            return
        if self.jobs.busy:
            self._timer.start()             # try again once the last one lands
            return
        y, sr = self._pending
        self.jobs.run(measure, y, sr, on_done=self._show,
                      on_failed=self._failed)

    def _show(self, measured: Measurement) -> None:
        report = measured.report
        self._report = report
        problems = report.problems()
        clipped = report.clipped_samples > 0 or report.peak_dbfs > -0.5

        self.peak.setText("silence" if report.peak_dbfs == float("-inf")
                          else f"{report.peak_dbfs:+.1f} dBFS")
        self.peak.setStyleSheet(
            f"color: {WARN_COLOR if clipped else OK_COLOR}; "
            f"{MONO_CSS} font-weight: bold;")

        over = measured.true_peak_db >= TRUE_PEAK_WARN_DBTP
        self.true_peak.setText("silence"
                               if not np.isfinite(measured.true_peak_db)
                               else f"{measured.true_peak_db:+.1f} dBTP")
        self.true_peak.setStyleSheet(
            f"color: {WARN_COLOR if over else CALL_COLOR}; "
            f"{MONO_CSS}")

        self.loudness.setText("n/a" if not np.isfinite(report.lufs)
                              else f"{report.lufs:.1f} LUFS")
        self.range.setText("n/a" if not np.isfinite(measured.range_lu)
                           else f"{measured.range_lu:.1f} LU")
        self.bandwidth.setText(f"{report.bandwidth_hz / 1000:.1f} kHz")
        self.bandwidth.setStyleSheet(
            f"color: {WARN_COLOR if report.band_limited else CALL_COLOR}; "
            f"{MONO_CSS}")

        # A true-peak overshoot is a real finding that `audio_report` cannot
        # make: nothing in the samples is wrong, and the file still clips the
        # moment anything reconstructs the waveform. Skipped when the report
        # already says it clipped, since then it is the same news twice.
        if over and not any("clip" in p for p in problems):
            problems = problems + [
                f"true peak {measured.true_peak_db:+.1f} dBTP — the waveform "
                f"between the samples is at or above the usual -1 dBTP "
                f"ceiling, so this can clip on conversion"]

        if problems:
            self.problems.setText("· " + "\n· ".join(problems))
            self.problems.setStyleSheet(f"color: {WARN_COLOR};")
        else:
            self.problems.setText("nothing wrong with the recording itself")
            self.problems.setStyleSheet(f"color: {HINT_COLOR};")
        self.problems.setVisible(True)
        self.reported.emit(report)
        self.curve_ready.emit(measured.curve)

    def _failed(self, message: str) -> None:
        log.debug("level readout failed: %s", message)
        self.clear()
