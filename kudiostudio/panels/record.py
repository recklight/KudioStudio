# -*- coding: utf-8 -*-
"""Capture from an input device — `kudio.record` and `kudio.StreamRecorder`.

Two ways to record, because they answer different questions.

**Fixed length** (`kudio.record`, sounddevice) is one blocking call: you say
how long up front and nothing is observable until it returns. Right for a
scripted capture, useless for a person at a microphone.

**Until stopped** (`kudio.StreamRecorder`, PyAudio) opens the stream and hands
back frames as they arrive, so the level is visible *before* the take is
committed — which is the only moment at which a clipped recording can still be
prevented rather than diagnosed.

The two backends **number devices independently**: an index from
`kudio.list_devices` is a sounddevice index and means something else entirely
to PyAudio. The device list therefore changes with the mode, and says which
space it is showing.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel
from kudiostudio.theme import ACCENT_COLOR, HINT_COLOR, OK_COLOR, WARN_COLOR

log = logging.getLogger(__name__)

COMMON_RATES = (48000, 44100, 32000, 22050, 16000, 8000)

MODE_FIXED = "fixed length"
MODE_STREAM = "until stopped"
MODES = (MODE_FIXED, MODE_STREAM)

#: Meter floor. Below -60 dBFS the difference between "quiet" and "silent"
#: stops being useful to look at.
METER_FLOOR_DB = -60.0
#: How often the level is read and the newly-arrived audio is handed on, ms.
#: The *display* throttles itself -- see `AnalysisView.feed_monitor` -- but the
#: audio must be collected on every tick, because `drain()` hands each sample
#: over once and skipping a tick would lose it.
METER_INTERVAL_MS = 50


def _rms_db(block: np.ndarray, previous: float, smoothing: float = 0.7) -> float:
    """Smoothed RMS level in dBFS. A per-block reading alone jitters too much
    to read, and the number it feeds is a difference of two of them."""
    if block.size == 0:
        return previous
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    now = 20.0 * np.log10(rms) if rms > 0 else float("-inf")
    if not np.isfinite(previous) or not np.isfinite(now):
        return now
    return smoothing * previous + (1.0 - smoothing) * now


def input_devices() -> List[Tuple[str, Optional[int]]]:
    """sounddevice input devices — the index space `kudio.record` accepts."""
    devices: List[Tuple[str, Optional[int]]] = [("System default", None)]
    try:
        for d in kudio.list_devices("input"):
            devices.append((f"[{d['index']}] {d['name']}", d["index"]))
    except Exception as e:  # no audio backend on this machine
        log.warning("device enumeration failed: %s", e)
    return devices


def stream_devices() -> List[Tuple[str, Optional[int]]]:
    """PyAudio input devices — the index space `StreamRecorder` accepts."""
    devices: List[Tuple[str, Optional[int]]] = [("System default", None)]
    try:
        with kudio.CheckDevice() as check:
            for d in check.system_devices():
                if d.get("in", 0):
                    devices.append((f"[{d['index']}] {d['Device']}", d["index"]))
    except Exception as e:
        log.warning("PyAudio device enumeration failed: %s", e)
    return devices


class RecordPanel(Panel):
    """Record a fixed-length clip, or an open-ended take with a live meter."""

    title = "Record"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        #: the open take, while one is running
        self._recorder = None
        self._enhancer = None
        self._monitor_raw_db = float("-inf")
        self._monitor_clean_db = float("-inf")

        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.device = QComboBox()
        self.device_space = QLabel()
        self.device_space.setStyleSheet(f"color: {HINT_COLOR};")
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

        self.meter = QProgressBar()
        self.meter.setRange(int(METER_FLOOR_DB), 0)
        self.meter.setValue(int(METER_FLOOR_DB))
        self.meter.setFormat("%v dBFS")
        self.meter.setTextVisible(True)
        self.meter.setStyleSheet(
            f"QProgressBar::chunk {{ background: {ACCENT_COLOR}; }}")
        self.elapsed = QLabel("0.00 s")
        self.elapsed.setStyleSheet("font-family: monospace;")

        self.monitor_method = QComboBox()
        self.monitor_method.addItem("off — show what the microphone sends", None)
        for name in kudio.STREAMABLE_METHODS:
            self.monitor_method.addItem(kudio.METHODS[name].label, name)
            self.monitor_method.setItemData(self.monitor_method.count() - 1,
                                            kudio.METHODS[name].summary,
                                            Qt.ToolTipRole)
        self.monitor_method.setToolTip(
            "Denoise the rolling display as it arrives (kudio.StreamEnhancer).\n"
            "Monitoring only — the take is committed exactly as recorded.\n"
            "Only the per-frame methods can stream; the rest need the whole\n"
            "spectrogram and are in the Enhance panel.")
        self.monitor_result = QLabel("")
        self.monitor_result.setStyleSheet(f"color: {HINT_COLOR};")

        self.record_button = QPushButton("● Record")
        self.refresh_button = QPushButton("Refresh devices")
        self.call = self.call_label()

        self.take = QLabel("no take yet")
        self.take.setWordWrap(True)
        self.take.setStyleSheet(f"color: {HINT_COLOR};")

        meter_row = QHBoxLayout()
        meter_row.addWidget(self.meter, stretch=1)
        meter_row.addWidget(self.elapsed)

        form = QFormLayout()
        form.addRow("Mode", self.mode)
        form.addRow("Device", self.device)
        form.addRow("", self.device_space)
        form.addRow("sr", self.rate)
        form.addRow("channels", self.channels)
        form.addRow("seconds", self.seconds)
        form.addRow("level", meter_row)
        form.addRow("monitor through", self.monitor_method)
        form.addRow("", self.monitor_result)

        buttons = QHBoxLayout()
        buttons.addWidget(self.record_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)

        box = QGroupBox("kudio.record / kudio.StreamRecorder")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.call)
        box_layout.addLayout(buttons)
        box_layout.addWidget(self.take)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.hint(
            "The recording replaces the current audio and becomes the new "
            "baseline for Undo and Metrics. Needs kudio[audio]. Every take is "
            "checked for clipping and level as soon as it lands — a clipped "
            "take is best redone, since nothing downstream can undo it."))
        layout.addStretch(1)

        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(METER_INTERVAL_MS)
        self._meter_timer.timeout.connect(self._on_meter_tick)

        self.refresh_button.clicked.connect(self.refresh_devices)
        self.record_button.clicked.connect(self._record)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.monitor_method.currentIndexChanged.connect(self._update_call)
        for w in (self.rate, self.channels, self.seconds, self.device):
            signal = getattr(w, "valueChanged", None) or w.currentTextChanged
            signal.connect(self._update_call)
        self.jobs.busy_changed.connect(self._set_busy)
        self._on_mode_changed()

    # ------------------------------------------------------------------- ui

    @property
    def streaming(self) -> bool:
        return self.mode.currentText() == MODE_STREAM

    def _on_mode_changed(self) -> None:
        streaming = self.streaming
        self.seconds.setEnabled(not streaming)
        # only the open-ended path has a stream to monitor
        self.monitor_method.setEnabled(streaming)
        self.device_space.setText(
            "PyAudio device indices (CheckDevice)" if streaming
            else "sounddevice device indices (kudio.list_devices)")
        self.record_button.setText("● Start" if streaming else "● Record")
        self.refresh_devices()

    def refresh_devices(self) -> None:
        current = self.device.currentData()
        self.device.clear()
        for label, index in (stream_devices() if self.streaming
                             else input_devices()):
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
        if self.streaming:
            self.call.setText(f"kudio.StreamRecorder(device={device}, sr={sr}, "
                              f"channels={channels}).start() … .stop()")
        else:
            self.call.setText(f"kudio.record(seconds={seconds}, sr={sr}, "
                              f"channels={channels}, device={device})")

    def _set_busy(self, busy: bool) -> None:
        if self.streaming:
            return              # the stream owns the button while it is open
        self.record_button.setEnabled(not busy)
        self.record_button.setText("Recording…" if busy else "● Record")

    def _reset_meter(self) -> None:
        self.meter.setValue(int(METER_FLOOR_DB))
        self.elapsed.setText("0.00 s")

    # ------------------------------------------------------------------ run

    def _record(self) -> None:
        if self.streaming:
            self._stop_stream() if self._recorder is not None else self._start_stream()
            return
        seconds, sr, channels, device = self._params()
        self.report(f"Recording {seconds:g}s at {sr} Hz …")
        started = self.jobs.run(kudio.record, seconds=seconds, sr=sr,
                                channels=channels, device=device,
                                on_done=lambda y: self._finished(y, sr),
                                on_failed=self._failed)
        if not started:
            self.report("A recording is already in progress.")

    # ------------------------------------------------------- open-ended take

    def _start_stream(self) -> None:
        _, sr, channels, device = self._params()
        try:
            self._recorder = kudio.StreamRecorder(
                device=device, sr=sr, channels=channels).start()
        except Exception as e:
            self._recorder = None
            self._failed(f"{type(e).__name__}: {e}")
            return

        method = self.monitor_method.currentData()
        self._enhancer = None
        if method is not None:
            try:
                self._enhancer = kudio.StreamEnhancer(self._recorder.sr, method)
            except Exception as e:               # noqa: BLE001
                self.report(f"Live monitoring unavailable — "
                            f"{type(e).__name__}: {e}")

        self._monitor_raw_db = self._monitor_clean_db = float("-inf")
        self.monitor_result.setText("")
        self.monitor_started.emit(self._recorder.sr)
        self._reset_meter()
        self._meter_timer.start()
        self.record_button.setText("■ Stop")
        self.mode.setEnabled(False)
        self.monitor_method.setEnabled(False)
        self.report(f"Recording at {self._recorder.sr} Hz — press Stop when done.")

    def _stop_stream(self) -> None:
        recorder, self._recorder = self._recorder, None
        self._enhancer = None
        self._meter_timer.stop()
        self.monitor_stopped.emit()
        self.record_button.setText("● Start")
        self.mode.setEnabled(True)
        self.monitor_method.setEnabled(True)
        if recorder is None:
            return
        try:
            y = recorder.stop()
        except Exception as e:
            self._failed(f"{type(e).__name__}: {e}")
            return
        self._reset_meter()
        if y.size == 0:
            self.report("Nothing was captured — check the input device.")
            return
        self._finished(y, recorder.sr)

    def _on_meter_tick(self) -> None:
        recorder = self._recorder
        if recorder is None:
            self._meter_timer.stop()
            return
        level = recorder.level_db()
        self.meter.setValue(int(max(METER_FLOOR_DB, min(0.0, level))))
        self.elapsed.setText(f"{recorder.elapsed():.2f} s")

        # Exactly one drain() per tick. It hands each sample over once and
        # never again, so a second caller in the same tick would take half the
        # audio away from the first — the denoiser and the display have to be
        # fed from the same call, not from two of them.
        new = recorder.drain()
        if new.size:
            if new.ndim > 1:
                new = new.mean(axis=1)
            if self._enhancer is not None:
                new = self._through_monitor(new)
            if new.size:
                self.monitor_chunk.emit(new, recorder.sr)

        if recorder.capped:
            self.report("Reached the recorder's length cap — stopping.")
            self._stop_stream()

    def _through_monitor(self, new: np.ndarray) -> np.ndarray:
        """Denoise a block on its way to the display, and say how much came off.

        Returns the block unchanged if the denoiser gave up: a monitor that
        stops updating looks like a dead microphone, which is a worse thing to
        believe than "the denoising stopped".
        """
        try:
            clean = self._enhancer.process(new)
        except Exception as e:                          # noqa: BLE001
            log.warning("live monitoring failed: %s", e)
            self._enhancer = None
            self.monitor_result.setText("live monitoring stopped — see the log")
            self.monitor_result.setStyleSheet(f"color: {WARN_COLOR};")
            return new

        self._monitor_raw_db = _rms_db(new, self._monitor_raw_db)
        if clean.size:
            self._monitor_clean_db = _rms_db(clean, self._monitor_clean_db)

        if np.isfinite(self._monitor_raw_db) and np.isfinite(self._monitor_clean_db):
            removed = self._monitor_raw_db - self._monitor_clean_db
            self.monitor_result.setText(
                f"{self.monitor_method.currentText()} — removing "
                f"{removed:.1f} dB right now  ·  {self._enhancer.latency_seconds * 1000:.0f} ms behind")
            self.monitor_result.setStyleSheet(f"color: {HINT_COLOR};")
        return clean

    def shutdown(self) -> None:
        """Close the device if the window shuts while a take is running."""
        self._enhancer = None
        self._meter_timer.stop()
        self.monitor_stopped.emit()
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception as e:          # pragma: no cover - teardown only
                log.debug("recorder shutdown: %s", e)
            self._recorder = None

    def _finished(self, y: np.ndarray, sr: int) -> None:
        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1:                      # keep the session mono
            y = y.mean(axis=1)
        self.session.set_audio(y, sr, name="recording")
        self.report(f"Recorded {len(y) / sr:.2f}s at {sr} Hz.")
        self._check_take(y, sr)

    def _check_take(self, y: np.ndarray, sr: int) -> None:
        """Say what is wrong with the take now, not after the session ends.

        A clipped take cannot be repaired; the only fix is to record it again,
        which is only possible while the microphone is still set up.
        """
        try:
            report = kudio.audio_report(y, sr)
        except Exception as e:              # never let a check break a take
            log.debug("take report failed: %s", e)
            self.take.setText("")
            return

        summary = (f"peak {report.peak_dbfs:+.1f} dBFS"
                   if report.peak_dbfs != float("-inf") else "silent")
        if np.isfinite(report.lufs):
            summary += f"  ·  {report.lufs:.1f} LUFS"

        problems = report.problems()
        if problems:
            self.take.setText(summary + "\n· " + "\n· ".join(problems))
            self.take.setStyleSheet(f"color: {WARN_COLOR};")
            self.report(f"Recorded, but: {problems[0]}")
        else:
            self.take.setText(summary + "  ·  take looks clean")
            self.take.setStyleSheet(f"color: {OK_COLOR};")

    def _failed(self, message: str) -> None:
        self.report(f"Recording failed — {message}")
