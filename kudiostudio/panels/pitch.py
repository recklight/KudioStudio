# -*- coding: utf-8 -*-
"""Fundamental frequency — `kudio.f0`, drawn over the spectrogram.

The spectrogram already shows the harmonic stack; what it does not show is
*which* line is the fundamental, and it stops showing anything useful the
moment the fundamental is weak — a phone band, a high-pass, or simply a low
voice under the first few bins.

Two things come back, and the second is the one that makes the first
readable: a contour, and a voiced/unvoiced decision. An estimator asked "what
is the pitch here" always answers, including for silence and for an `s`. The
contour is drawn with gaps where there is genuinely no pitch, rather than
joined into a line through the pauses.

pYIN runs a hidden Markov model over the whole clip, so it costs roughly a
second per ten seconds of audio — which is why this is a button and not
something that recomputes as you drag.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel
from kudiostudio.theme import HINT_COLOR, OK_COLOR, WARN_COLOR

log = logging.getLogger(__name__)

#: Named search ranges, in Hz. The range is the setting people get wrong, and
#: getting it wrong does not fail — a voice an octave below `fmin` is reported
#: as something plausible and untrue inside the range. Naming the usual ones
#: makes the mistake harder to make than to avoid.
RANGES = (
    ("Speech (65–400 Hz)", 65.0, 400.0),
    ("Low voice (60–250 Hz)", 60.0, 250.0),
    ("High voice (150–600 Hz)", 150.0, 600.0),
    ("Singing / wide (50–1000 Hz)", 50.0, 1000.0),
    ("Custom", None, None),
)
CUSTOM_RANGE = len(RANGES) - 1

FRAME_LENGTHS = (1024, 2048, 4096, 8192)


class PitchPanel(Panel):
    """Track f0 over the clip (or the selection) and draw it."""

    title = "Pitch"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        #: the last result, and where in the clip it was measured from
        self._track = None
        self._offset = 0.0
        #: guards the preset -> spin box write, so setting a preset does not
        #: read back as somebody typing a custom bound
        self._syncing = False

        self.range_choice = QComboBox()
        for label, _, _ in RANGES:
            self.range_choice.addItem(label)
        self.fmin = QSpinBox()
        self.fmin.setRange(20, 2000)
        self.fmin.setSuffix(" Hz")
        self.fmax = QSpinBox()
        self.fmax.setRange(40, 8000)
        self.fmax.setSuffix(" Hz")
        self.frame_length = QComboBox()
        for n in FRAME_LENGTHS:
            self.frame_length.addItem(str(n), n)
        self.frame_length.setCurrentIndex(1)
        self.frame_length.setToolTip(
            "pYIN compares a frame against itself one period later, so the\n"
            "frame has to hold two periods of the lowest pitch you ask for.\n"
            "kudio refuses the combination rather than returning an empty\n"
            "track, which would read as 'this recording has no pitch'.")
        self.hop_ms = QSpinBox()
        self.hop_ms.setRange(1, 50)
        self.hop_ms.setValue(10)
        self.hop_ms.setSuffix(" ms")

        self.overlay = QCheckBox("Draw the contour over the spectrogram")
        self.overlay.setChecked(True)
        self.overlay.setToolTip(
            "Only on the STFT view — on a mel or MFCC image the vertical axis\n"
            "is a band index, so a curve in Hz would land in the wrong place.")

        self.run_button = QPushButton("Track pitch")
        self.markers_button = QPushButton("Voiced spans → markers")
        self.markers_button.setEnabled(False)
        self.markers_button.setToolTip(
            "Add each voiced run to the marker list, where it can be renamed,\n"
            "saved beside the file, or exported as a label track.")

        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setVisible(False)
        self.scope = QLabel("")
        self.scope.setStyleSheet(f"color: {HINT_COLOR};")

        self.call = self.call_label()

        form = QFormLayout()
        form.addRow("range", self.range_choice)
        form.addRow("fmin", self.fmin)
        form.addRow("fmax", self.fmax)
        form.addRow("frame_length", self.frame_length)
        form.addRow("hop", self.hop_ms)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.markers_button)
        buttons.addStretch(1)

        box = QGroupBox("kudio.f0 (pYIN)")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.overlay)
        box_layout.addWidget(self.call)
        box_layout.addLayout(buttons)
        box_layout.addWidget(self.scope)
        box_layout.addWidget(self.result)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.hint(
            "A selection is tracked on its own if there is one, so you can ask "
            "about one phrase rather than the whole take. The contour is "
            "measured from the audio as it is now — any edit drops it, because "
            "a curve left on screen describing audio that has been replaced is "
            "worse than no curve."))
        layout.addStretch(1)

        self.range_choice.currentIndexChanged.connect(self._on_range_changed)
        for w in (self.fmin, self.fmax, self.hop_ms):
            w.valueChanged.connect(self._on_custom_edit)
        self.frame_length.currentIndexChanged.connect(self._update_call)
        self.overlay.toggled.connect(self._on_overlay_toggled)
        self.run_button.clicked.connect(self._run)
        self.markers_button.clicked.connect(self._add_markers)
        self.jobs.busy_changed.connect(self._set_busy)

        self.range_choice.setCurrentIndex(0)
        self._on_range_changed()

    # ------------------------------------------------------------------- ui

    def _on_range_changed(self) -> None:
        index = self.range_choice.currentIndex()
        _, lo, hi = RANGES[index]
        custom = index == CUSTOM_RANGE
        self.fmin.setEnabled(custom)
        self.fmax.setEnabled(custom)
        if not custom:
            self._syncing = True
            self.fmin.setValue(int(lo))
            self.fmax.setValue(int(hi))
            self._syncing = False
        self._update_call()

    def _on_custom_edit(self) -> None:
        """Typing a bound is a way of choosing Custom, not of fighting it."""
        if not self._syncing and self.range_choice.currentIndex() != CUSTOM_RANGE:
            self.range_choice.setCurrentIndex(CUSTOM_RANGE)
        self._update_call()

    def _params(self) -> Tuple[float, float, int, int]:
        sr = self.session.sr or 16000
        hop = max(1, int(round(self.hop_ms.value() * sr / 1000.0)))
        return (float(self.fmin.value()), float(self.fmax.value()),
                int(self.frame_length.currentData()), hop)

    def _update_call(self) -> None:
        fmin, fmax, frame, hop = self._params()
        self.call.setText(
            f"kudio.f0(y, sr, fmin={fmin:g}, fmax={fmax:g}, "
            f"frame_length={frame}, hop_length={hop})")

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("Tracking…" if busy else "Track pitch")

    def _on_overlay_toggled(self, on: bool) -> None:
        if on and self._track is not None:
            self._emit_overlay()
        else:
            self.overlay_cleared.emit()

    def on_session_changed(self) -> None:
        """The audio moved; the contour describes what it used to be."""
        self._update_call()
        if self._track is not None:
            self._track = None
            self.markers_button.setEnabled(False)
            self.result.setVisible(False)
            self.overlay_cleared.emit()

    # ------------------------------------------------------------------ run

    def _run(self) -> None:
        if not self.require_audio():
            return
        # `selected_audio` returns the whole clip when nothing is selected, so
        # the *selection* is what says which of the two this is -- the audio
        # alone cannot tell you, and the offset depends on the answer.
        selection = self.session.selection()
        y = self.session.selected_audio()
        if selection is None:
            self._offset = 0.0
            self.scope.setText("the whole clip")
        else:
            self._offset = selection[0]
            self.scope.setText(
                f"the selection, {selection[0]:.3f}–{selection[1]:.3f} s")

        fmin, fmax, frame, hop = self._params()
        self.report("Tracking pitch — pYIN runs over the whole clip at once.")
        started = self.jobs.run(
            kudio.f0, np.asarray(y, dtype=np.float32), self.session.sr,
            fmin=fmin, fmax=fmax, frame_length=frame, hop_length=hop,
            on_done=self._finished, on_failed=self._failed)
        if not started:
            self.report("Another job is still running.")

    def _finished(self, track) -> None:
        self._track = track
        self.result.setVisible(True)
        if not track.voiced_ratio:
            self._track = None
            self.markers_button.setEnabled(False)
            self.result.setText(
                "Nothing voiced in this range. A voice below fmin is not "
                "found — it is reported as something wrong above it, so an "
                "empty answer is the honest one. Try a wider range.")
            self.result.setStyleSheet(f"color: {WARN_COLOR};")
            self.overlay_cleared.emit()
            self.report("No voiced frames found.")
            return

        low, high = track.range_hz()
        self.result.setText(
            f"median {track.median_hz:.1f} Hz\n"
            f"5–95%  {low:.1f}–{high:.1f} Hz "
            f"({track.semitone_range:.1f} semitones)\n"
            f"{track.voiced_ratio:.0%} of frames voiced, "
            f"{len(track.voiced_segments())} span(s)")
        self.result.setStyleSheet(f"color: {OK_COLOR};")
        self.markers_button.setEnabled(True)
        if self.overlay.isChecked():
            self._emit_overlay()
        self.report(f"Pitch: {track}")

    def _emit_overlay(self) -> None:
        track = self._track
        if track is None:
            return
        # times are relative to whatever was analysed; the view draws on the
        # clip's own timeline, so a selection has to be put back where it came
        # from or the contour lands at the start of the file
        self.overlay_ready.emit(track.times + self._offset, track.f0)

    def _add_markers(self) -> None:
        track = self._track
        if track is None:
            return
        added = 0
        for start, end in track.voiced_segments():
            self.session.add_marker(start + self._offset, end + self._offset,
                                    f"voiced {added + 1}")
            added += 1
        self.report(f"Added {added} marker(s) for the voiced spans.")

    def _failed(self, message: str) -> None:
        self._track = None
        self.markers_button.setEnabled(False)
        self.result.setVisible(True)
        self.result.setText(message)
        self.result.setStyleSheet(f"color: {WARN_COLOR};")
        self.report(f"Pitch tracking failed — {message}")
