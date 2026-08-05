# -*- coding: utf-8 -*-
"""Capture from an input device — `kudio.record`.

Devices come from `kudio.list_devices`, which enumerates through sounddevice —
the same backend `kudio.record` uses. `CheckDevice` numbers devices through
PyAudio for the streaming classes, and the two are not interchangeable.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel

log = logging.getLogger(__name__)

COMMON_RATES = (48000, 44100, 32000, 22050, 16000, 8000)


def input_devices() -> List[Tuple[str, Optional[int]]]:
    """``[(label, device_index), ...]``; first entry is the system default."""
    devices: List[Tuple[str, Optional[int]]] = [("System default", None)]
    try:
        for d in kudio.list_devices("input"):
            devices.append((f"[{d['index']}] {d['name']}", d["index"]))
    except Exception as e:  # no audio backend on this machine
        log.warning("device enumeration failed: %s", e)
    return devices


class RecordPanel(Panel):
    """Record a fixed-length clip straight into the session."""

    title = "Record"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)

        self.device = QComboBox()
        self.rate = QComboBox()
        self.rate.setEditable(True)
        self.rate.addItems([str(r) for r in COMMON_RATES])
        self.rate.setCurrentText("16000")
        self.channels = QSpinBox()
        self.channels.setRange(1, 8)
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(0.1, 3600.0)
        self.seconds.setSingleStep(0.5)
        self.seconds.setValue(3.0)
        self.seconds.setSuffix(" s")

        self.record_button = QPushButton("● Record")
        self.refresh_button = QPushButton("Refresh devices")
        self.call = self.call_label()

        form = QFormLayout()
        form.addRow("Device", self.device)
        form.addRow("sr", self.rate)
        form.addRow("channels", self.channels)
        form.addRow("seconds", self.seconds)

        buttons = QHBoxLayout()
        buttons.addWidget(self.record_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)

        box = QGroupBox("kudio.record")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.call)
        box_layout.addLayout(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.hint(
            "The recording replaces the current audio and becomes the new "
            "baseline for Undo and Metrics. Needs kudio[audio]."))
        layout.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh_devices)
        self.record_button.clicked.connect(self._record)
        for w in (self.rate, self.channels, self.seconds, self.device):
            signal = getattr(w, "valueChanged", None) or w.currentTextChanged
            signal.connect(self._update_call)
        self.jobs.busy_changed.connect(self._set_busy)
        self.refresh_devices()

    # ------------------------------------------------------------------- ui

    def refresh_devices(self) -> None:
        current = self.device.currentData()
        self.device.clear()
        for label, index in input_devices():
            self.device.addItem(label, index)
        if current is not None:
            pos = self.device.findData(current)
            if pos >= 0:
                self.device.setCurrentIndex(pos)
        if self.device.count() <= 1:
            self.report("No input devices found — is kudio[audio] installed?")
        self._update_call()

    def _params(self) -> Tuple[float, int, int, Optional[int]]:
        try:
            sr = int(self.rate.currentText())
        except ValueError:
            sr = 16000
        return (self.seconds.value(), sr, self.channels.value(),
                self.device.currentData())

    def _update_call(self) -> None:
        seconds, sr, channels, device = self._params()
        self.call.setText(f"kudio.record(seconds={seconds}, sr={sr}, "
                          f"channels={channels}, device={device})")

    def _set_busy(self, busy: bool) -> None:
        self.record_button.setEnabled(not busy)
        self.record_button.setText("Recording…" if busy else "● Record")

    # ------------------------------------------------------------------ run

    def _record(self) -> None:
        seconds, sr, channels, device = self._params()
        self.report(f"Recording {seconds:g}s at {sr} Hz …")
        started = self.jobs.run(kudio.record, seconds=seconds, sr=sr,
                                channels=channels, device=device,
                                on_done=lambda y: self._finished(y, sr),
                                on_failed=self._failed)
        if not started:
            self.report("A recording is already in progress.")

    def _finished(self, y: np.ndarray, sr: int) -> None:
        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1:                      # keep the session mono
            y = y.mean(axis=1)
        self.session.set_audio(y, sr, name="recording")
        self.report(f"Recorded {len(y) / sr:.2f}s at {sr} Hz.")

    def _failed(self, message: str) -> None:
        self.report(f"Recording failed — {message}")
