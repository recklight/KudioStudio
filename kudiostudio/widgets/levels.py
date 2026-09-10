# -*- coding: utf-8 -*-
"""Level, loudness and "is this recording usable" — `kudio.audio_report`.

Every metric in the Metrics panel needs a clean reference to compare against.
A real recording has none, so the questions that matter first — did it clip,
is it far too quiet, is this 16 kHz file really 8 kHz audio — had nowhere to
be answered. They are answered here, continuously, for whatever is loaded.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

import kudio
from kudiostudio.theme import CALL_COLOR, HINT_COLOR, OK_COLOR, WARN_COLOR
from kudiostudio.workers import WorkerHost

log = logging.getLogger(__name__)


class LevelReadout(QGroupBox):
    """Peak, loudness and warnings for the current audio, measured off-thread.

    A full report walks the signal twice and filters it once, which is
    milliseconds on a clip and half a second on a long file -- too slow to run
    inline on every edit. It is therefore debounced onto a worker, and a
    request arriving while one is running simply reschedules.
    """

    #: a fresh :class:`kudio.AudioReport` for the audio last handed in
    reported = Signal(object)

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
        self.loudness = QLabel("—")
        self.bandwidth = QLabel("—")
        for label in (self.peak, self.loudness, self.bandwidth):
            label.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")

        self.problems = QLabel("")
        self.problems.setWordWrap(True)
        self.problems.setVisible(False)

        form = QFormLayout()
        form.addRow("peak", self.peak)
        form.addRow("loudness", self.loudness)
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
        for label in (self.peak, self.loudness, self.bandwidth):
            label.setText("—")
            label.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")
        self.problems.setVisible(False)

    # -------------------------------------------------------------- internal

    def _measure(self) -> None:
        if self._pending is None:
            return
        if self.jobs.busy:
            self._timer.start()             # try again once the last one lands
            return
        y, sr = self._pending
        self.jobs.run(kudio.audio_report, y, sr,
                      on_done=self._show, on_failed=self._failed)

    def _show(self, report) -> None:
        self._report = report
        problems = report.problems()
        clipped = report.clipped_samples > 0 or report.peak_dbfs > -0.5

        self.peak.setText("silence" if report.peak_dbfs == float("-inf")
                          else f"{report.peak_dbfs:+.1f} dBFS")
        self.peak.setStyleSheet(
            f"color: {WARN_COLOR if clipped else OK_COLOR}; "
            f"font-family: monospace; font-weight: bold;")

        self.loudness.setText("n/a" if not np.isfinite(report.lufs)
                              else f"{report.lufs:.1f} LUFS")
        self.bandwidth.setText(f"{report.bandwidth_hz / 1000:.1f} kHz")
        self.bandwidth.setStyleSheet(
            f"color: {WARN_COLOR if report.band_limited else CALL_COLOR}; "
            f"font-family: monospace;")

        if problems:
            self.problems.setText("· " + "\n· ".join(problems))
            self.problems.setStyleSheet(f"color: {WARN_COLOR};")
        else:
            self.problems.setText("nothing wrong with the recording itself")
            self.problems.setStyleSheet(f"color: {HINT_COLOR};")
        self.problems.setVisible(True)
        self.reported.emit(report)

    def _failed(self, message: str) -> None:
        log.debug("level readout failed: %s", message)
        self.clear()
