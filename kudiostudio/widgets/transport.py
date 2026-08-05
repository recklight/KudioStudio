# -*- coding: utf-8 -*-
"""Playback controls.

Audio goes out through :func:`kudio.play_audio`, which prefers sounddevice and
falls back to PyAudio. sounddevice returns immediately, so playback is already
asynchronous; PyAudio blocks, so that path is pushed onto a worker thread.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)
from PySide6.QtCore import Qt

import kudio
from kudiostudio.theme import HINT_COLOR
from kudiostudio.workers import WorkerHost

log = logging.getLogger(__name__)


def _sounddevice():
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


class Player(QObject):
    """Plays a numpy waveform, with a stop that actually stops."""

    failed = Signal(str)

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._host = WorkerHost(self)

    def play(self, y: np.ndarray, sr: int, gain_db: float = 0.0) -> None:
        if y is None or sr <= 0 or len(y) == 0:
            return
        out = np.asarray(y, dtype=np.float32)
        if gain_db:
            out = out * float(10 ** (gain_db / 20.0))
        out = np.clip(out, -1.0, 1.0)

        if _sounddevice() is not None:
            # non-blocking backend: call straight through, keep stop() usable
            err = kudio.play_audio(out, rate=sr)
            if err:
                self.failed.emit(str(err))
            return
        self._host.run(kudio.play_audio, out, rate=sr,
                       on_failed=self.failed.emit)

    def stop(self) -> None:
        sd = _sounddevice()
        if sd is not None:
            sd.stop()

    def shutdown(self) -> None:
        self.stop()
        self._host.wait(2000)


class TransportBar(QWidget):
    """Play / stop / gain, operating on the session's current selection."""

    play_requested = Signal(bool)   # True -> selection only
    stop_requested = Signal()

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.play_button = QPushButton("▶  Play")
        self.stop_button = QPushButton("■  Stop")
        self.selection_only = QCheckBox("Selection only")
        self.selection_only.setChecked(True)
        self.selection_only.setToolTip(
            "Play just the highlighted range when there is a selection.")

        self.gain = QSlider(Qt.Horizontal)
        self.gain.setRange(-24, 12)
        self.gain.setValue(0)
        self.gain.setFixedWidth(120)
        self.gain_label = QLabel("0 dB")
        self.gain_label.setMinimumWidth(46)
        self.gain.valueChanged.connect(
            lambda v: self.gain_label.setText(f"{v:+d} dB" if v else "0 dB"))

        self.info = QLabel("no audio loaded")
        self.info.setStyleSheet(f"color: {HINT_COLOR};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self.play_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.selection_only)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Gain"))
        layout.addWidget(self.gain)
        layout.addWidget(self.gain_label)
        layout.addStretch(1)
        layout.addWidget(self.info)

        self.play_button.clicked.connect(
            lambda: self.play_requested.emit(self.selection_only.isChecked()))
        self.stop_button.clicked.connect(self.stop_requested)

    @property
    def gain_db(self) -> float:
        return float(self.gain.value())

    @Slot(str)
    def set_info(self, text: str) -> None:
        self.info.setText(text)

    def set_enabled_for(self, has_audio: bool) -> None:
        self.play_button.setEnabled(has_audio)
        self.stop_button.setEnabled(has_audio)
