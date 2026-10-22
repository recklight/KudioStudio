# -*- coding: utf-8 -*-
"""Playback controls.

Audio goes out through :func:`kudio.play_audio`, which prefers sounddevice and
falls back to PyAudio. sounddevice returns immediately, so playback is already
asynchronous; PyAudio blocks, so that path is pushed onto a worker thread.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)
from PySide6.QtCore import QSize, Qt

import kudio
from kudiostudio import icons
from kudiostudio.theme import ACCENT_COLOR, HINT_COLOR, MONO_CSS
from kudiostudio.workers import WorkerHost

log = logging.getLogger(__name__)


def _sounddevice():
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


def format_time(seconds: float) -> str:
    """``12.5`` -> ``"0:12.500"``. Milliseconds matter when picking a splice."""
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(seconds, 60.0)
    return f"{int(minutes)}:{rest:06.3f}"


class Player(QObject):
    """Plays a numpy waveform and reports where the playhead is.

    Neither backend reports progress: sounddevice's ``play()`` is
    fire-and-forget and PyAudio's blocks until the end. The position is
    therefore *estimated* from a monotonic clock started when the samples were
    handed over -- accurate enough to drive a cursor, and the only option that
    works for both backends.
    """

    #: playhead moved, in seconds on the clip's timeline
    position_changed = Signal(float)
    #: playback reached the end on its own (never emitted by :meth:`stop`)
    finished = Signal()
    failed = Signal(str)

    #: 25 fps -- smooth, without repainting the waveform pointlessly often
    TICK_MS = 40

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._host = WorkerHost(self)
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._start = 0.0       # timeline position of the first sample played
        self._end = 0.0
        self._clock = 0.0       # monotonic reading when playback was handed over
        self._pos = 0.0

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    @property
    def position(self) -> float:
        """Where the playhead is now, in seconds on the clip's timeline."""
        if self._timer.isActive():
            return self._elapsed()
        return self._pos

    def play(self, y: np.ndarray, sr: int, gain_db: float = 0.0,
             *, t0: float = 0.0) -> None:
        """Play *y*, whose first sample sits at *t0* seconds on the timeline."""
        if y is None or sr <= 0 or len(y) == 0:
            return
        self.stop()
        out = np.asarray(y, dtype=np.float32)
        if gain_db:
            out = out * float(10 ** (gain_db / 20.0))
        out = np.clip(out, -1.0, 1.0)

        self._start = self._pos = float(t0)
        self._end = float(t0) + len(out) / float(sr)
        self._clock = time.monotonic()
        self._timer.start()
        self.position_changed.emit(self._start)

        if _sounddevice() is not None:
            # non-blocking backend: call straight through, keep stop() usable
            err = kudio.play_audio(out, rate=sr)
            if err:
                self._fail(err)
            return
        self._host.run(kudio.play_audio, out, rate=sr, on_failed=self._fail)

    def stop(self) -> None:
        if self._timer.isActive():
            self._pos = self._elapsed()
            self._timer.stop()
        sd = _sounddevice()
        if sd is not None:
            sd.stop()

    def shutdown(self) -> None:
        self.stop()
        self._host.wait(2000)

    # ---------------------------------------------------------------- internal

    def _elapsed(self) -> float:
        return min(self._start + (time.monotonic() - self._clock), self._end)

    def _tick(self) -> None:
        self._pos = self._elapsed()
        self.position_changed.emit(self._pos)
        if self._pos >= self._end:
            self._timer.stop()
            self.finished.emit()

    def _fail(self, message) -> None:
        self._timer.stop()
        self.failed.emit(str(message))


class TransportBar(QWidget):
    """Play / stop / A/B / gain, operating on the session's current selection."""

    play_requested = Signal(bool)   # True -> selection only
    stop_requested = Signal()
    compare_requested = Signal(bool)
    loudness_match_requested = Signal(bool)
    loop_requested = Signal(bool)
    crossfade_changed = Signal(float)

    _COMPARE_OFF = "A / B  ·  edited"
    _COMPARE_ON = "A / B  ·  original"

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.play_button = QPushButton(icons.icon("play", "#ffffff"), "Play")
        self.play_button.setIconSize(QSize(14, 14))
        # the one button drawn in the theme's blue: it is what the window is for
        self.play_button.setProperty("primary", True)
        self.play_button.setToolTip("Play from the cursor, or the selection (Space)")
        self.stop_button = QPushButton(icons.icon("stop"), "Stop")
        self.stop_button.setIconSize(QSize(14, 14))
        self.stop_button.setToolTip("Stop playback (Space)")
        self.selection_only = QCheckBox("Selection only")
        self.selection_only.setChecked(True)
        self.selection_only.setToolTip(
            "Play just the highlighted range when there is a selection.")
        self.loop = QCheckBox("Loop")
        self.loop.setToolTip(
            "Repeat what was last played (Ctrl+L).\n"
            "A panel's Preview always loops; this is for ordinary playback.")

        self.time_label = QLabel(format_time(0.0))
        self.time_label.setMinimumWidth(64)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setStyleSheet(MONO_CSS)

        self.compare_button = QPushButton(self._COMPARE_OFF)
        self.compare_button.setCheckable(True)
        self.compare_button.setEnabled(False)
        self.compare_button.setToolTip(
            "Hold the edit against the audio you started with (Ctrl+B).\n"
            "Views and playback follow; the tools keep working on the edit.")
        self.compare_button.setStyleSheet(
            f"QPushButton:checked {{ background: {ACCENT_COLOR}; color: #1b1d21;"
            f" font-weight: bold; }}")

        self.match_loudness = QCheckBox("Match loudness")
        self.match_loudness.setChecked(True)
        self.match_loudness.setEnabled(False)
        self.match_loudness.setToolTip(
            "Level the original to the edit (ITU-R BS.1770) before comparing.\n"
            "Of two versions of a clip the louder is preferred whatever its\n"
            "quality, so an unmatched A/B is partly a loudness test.")

        self.gain = QSlider(Qt.Horizontal)
        self.gain.setRange(-24, 12)
        self.gain.setValue(0)
        self.gain.setFixedWidth(120)
        self.gain_label = QLabel("0 dB")
        self.gain_label.setMinimumWidth(46)
        self.gain.valueChanged.connect(
            lambda v: self.gain_label.setText(f"{v:+d} dB" if v else "0 dB"))

        # The one edit setting that lives here rather than in a menu.
        # It changes what every cut and paste *does* to the audio, and the
        # right length is per edit -- 5 ms inside a word, 200 ms between two
        # takes -- so it has to be adjustable between two edits without
        # opening anything. This is the only strip that is always on screen.
        self.crossfade = QDoubleSpinBox()
        self.crossfade.setRange(0.0, 200.0)
        self.crossfade.setDecimals(1)
        self.crossfade.setSingleStep(1.0)
        self.crossfade.setSuffix(" ms")
        self.crossfade.setFixedWidth(84)
        self.crossfade.setToolTip(
            "Milliseconds blended at each cut, paste and insert.\n"
            "0 is a hard cut, at the cost of a click where two pieces meet.\n"
            "Every edit says in the status bar how much it applied.")

        self.info = QLabel("no audio loaded")
        self.info.setStyleSheet(f"color: {HINT_COLOR};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self.play_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.time_label)
        layout.addWidget(self.selection_only)
        layout.addWidget(self.loop)
        layout.addSpacing(12)
        layout.addWidget(self.compare_button)
        layout.addWidget(self.match_loudness)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Gain"))
        layout.addWidget(self.gain)
        layout.addWidget(self.gain_label)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Splice xf"))
        layout.addWidget(self.crossfade)
        layout.addStretch(1)
        layout.addWidget(self.info)

        self.play_button.clicked.connect(
            lambda: self.play_requested.emit(self.selection_only.isChecked()))
        self.stop_button.clicked.connect(self.stop_requested)
        self.compare_button.clicked.connect(self.compare_requested)
        self.match_loudness.toggled.connect(self.loudness_match_requested)
        self.loop.clicked.connect(self.loop_requested)
        self.crossfade.valueChanged.connect(self.crossfade_changed)

    @property
    def gain_db(self) -> float:
        return float(self.gain.value())

    @Slot(str)
    def set_info(self, text: str) -> None:
        self.info.setText(text)

    @Slot(float)
    def set_time(self, seconds: float) -> None:
        self.time_label.setText(format_time(seconds))

    def set_compare_state(self, on: bool) -> None:
        """Reflect the session's A/B state without emitting a request back."""
        self.compare_button.setChecked(on)
        self.compare_button.setText(self._COMPARE_ON if on else self._COMPARE_OFF)

    def set_compare_enabled(self, enabled: bool) -> None:
        self.compare_button.setEnabled(enabled)
        self.match_loudness.setEnabled(enabled)
        if not enabled:
            self.set_compare_state(False)

    def set_loop_state(self, on: bool) -> None:
        """Reflect the window's loop state without emitting a request back."""
        self.loop.setChecked(bool(on))

    def set_enabled_for(self, has_audio: bool) -> None:
        self.play_button.setEnabled(has_audio)
        self.stop_button.setEnabled(has_audio)
        self.loop.setEnabled(has_audio)
