# -*- coding: utf-8 -*-
"""Waveform and time-frequency views, both backed by pyqtgraph.

The analysis image is produced by `kudio` itself
(:func:`kudio.waveform_to_spectrogram` / :func:`kudio.mfcc`) so what you see
here is exactly what a training script would consume.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.theme import ACCENT_COLOR, CALL_COLOR, WAVE_COLOR

log = logging.getLogger(__name__)

pg.setConfigOptions(antialias=False, imageAxisOrder="col-major")

#: More points than this and we let pyqtgraph peak-decimate rather than
#: pushing every sample into the scene graph.
PEAK_DECIMATION_LIMIT = 50_000


class WaveformView(pg.PlotWidget):
    """Amplitude against time, with a draggable selection region."""

    selection_changed = Signal(float, float)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent, background=None)
        self.setLabel("bottom", "Time", units="s")
        self.setLabel("left", "Amplitude")
        self.showGrid(x=True, y=True, alpha=0.2)
        self.setMouseEnabled(x=True, y=False)
        self.setYRange(-1.0, 1.0, padding=0.02)

        self._curve = self.plot(pen=pg.mkPen(WAVE_COLOR, width=1))
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)

        self._region = pg.LinearRegionItem(
            brush=pg.mkBrush(232, 163, 61, 55),
            hoverBrush=pg.mkBrush(232, 163, 61, 85),
            pen=pg.mkPen(ACCENT_COLOR, width=1))
        self._region.setZValue(10)
        self._region.hide()
        self.addItem(self._region, ignoreBounds=True)
        self._region.sigRegionChangeFinished.connect(self._emit_region)

        self._cursor = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen(ACCENT_COLOR, width=1))
        self._cursor.hide()
        self.addItem(self._cursor, ignoreBounds=True)

        self._duration = 0.0

    # ------------------------------------------------------------------ data

    def set_waveform(self, y: Optional[np.ndarray], sr: int) -> None:
        if y is None or sr <= 0 or len(y) == 0:
            self.clear_waveform()
            return
        self._duration = len(y) / sr
        t = np.arange(len(y), dtype=np.float64) / sr
        self._curve.setData(t, np.asarray(y, dtype=np.float64))
        peak = float(np.max(np.abs(y))) or 1.0
        self.setYRange(-peak, peak, padding=0.05)
        self.setXRange(0.0, self._duration, padding=0.0)

    def clear_waveform(self) -> None:
        self._duration = 0.0
        self._curve.setData([], [])
        self._region.hide()
        self._cursor.hide()

    # ------------------------------------------------------------- selection

    def select_all(self) -> None:
        if self._duration > 0:
            self.set_selection(0.0, self._duration)

    def set_selection(self, start: float, end: float) -> None:
        if end <= start:
            self._region.hide()
            return
        self._region.blockSignals(True)
        self._region.setRegion((start, end))
        self._region.blockSignals(False)
        self._region.show()

    def clear_selection(self) -> None:
        self._region.hide()
        self.selection_changed.emit(0.0, 0.0)

    def _emit_region(self) -> None:
        start, end = self._region.getRegion()
        self.selection_changed.emit(float(start), float(end))

    def set_cursor(self, seconds: Optional[float]) -> None:
        if seconds is None:
            self._cursor.hide()
        else:
            self._cursor.setPos(seconds)
            self._cursor.show()


class SpectrogramView(pg.PlotWidget):
    """Log-power STFT or MFCC, rendered as an image."""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent, background=None)
        self.setLabel("bottom", "Time", units="s")
        self.setLabel("left", "Frequency", units="Hz")
        self.setMouseEnabled(x=True, y=True)

        self._image = pg.ImageItem()
        self.addItem(self._image)
        try:
            self._image.setColorMap(pg.colormap.get("viridis"))
        except Exception:  # pragma: no cover - very old pyqtgraph
            log.debug("viridis colormap unavailable; using default lookup table")

    def set_image(self, data: np.ndarray, duration: float, y_max: float,
                  y_label: str, y_units: Optional[str]) -> None:
        """*data* is ``(frames, bins)`` -- the layout kudio returns."""
        if data is None or data.size == 0:
            self.clear_image()
            return
        data = np.asarray(data, dtype=np.float32)
        finite = data[np.isfinite(data)]
        if finite.size:
            # a floor near the median keeps the noise bed dark instead of
            # washing the whole image out to mid-colour
            lo, hi = np.percentile(finite, (40, 99.8))
            if hi > lo:
                self._image.setLevels((float(lo), float(hi)))
        self._image.setImage(data, autoLevels=False)
        self._image.setRect(0.0, 0.0, duration, y_max)
        self.setLabel("left", y_label, units=y_units)
        self.setXRange(0.0, duration, padding=0.0)
        self.setYRange(0.0, y_max, padding=0.0)

    def clear_image(self) -> None:
        self._image.clear()


class AnalysisView(QWidget):
    """Waveform above, time-frequency below, plus the analysis parameters.

    The parameter names are the `kudio` keyword arguments verbatim -- the
    caption under the controls shows the exact call being made.
    """

    selection_changed = Signal(float, float)

    MODES = ("STFT (log-power)", "MFCC")

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._y: Optional[np.ndarray] = None
        self._sr: int = 0

        self.waveform = WaveformView(self)
        self.spectrogram = SpectrogramView(self)
        self.waveform.selection_changed.connect(self.selection_changed)
        self.waveform.setXLink(self.spectrogram)

        self.mode = QComboBox()
        self.mode.addItems(self.MODES)
        self.n_fft = QSpinBox()
        self.n_fft.setRange(64, 8192)
        self.n_fft.setSingleStep(64)
        self.n_fft.setValue(512)
        self.hop = QSpinBox()
        self.hop.setRange(16, 4096)
        self.hop.setSingleStep(16)
        self.hop.setValue(256)
        self.n_mfcc = QSpinBox()
        self.n_mfcc.setRange(4, 128)
        self.n_mfcc.setValue(40)

        controls = QFormLayout()
        controls.addRow("View", self.mode)
        controls.addRow("n_fft", self.n_fft)
        controls.addRow("hop_length", self.hop)
        controls.addRow("n_mfcc", self.n_mfcc)

        self.call_hint = QLabel()
        self.call_hint.setWordWrap(True)
        self.call_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.call_hint.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")

        box = QGroupBox("Analysis")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(controls)
        box_layout.addWidget(self.call_hint)
        box.setMaximumWidth(260)

        plots = QVBoxLayout()
        plots.setContentsMargins(0, 0, 0, 0)
        plots.addWidget(self.waveform, stretch=1)
        plots.addWidget(self.spectrogram, stretch=2)

        layout = QHBoxLayout(self)
        layout.addLayout(plots, stretch=1)
        layout.addWidget(box)

        for w in (self.mode, self.n_fft, self.hop, self.n_mfcc):
            signal = w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged
            signal.connect(self.refresh)
        self._update_hint()

    # ------------------------------------------------------------------ data

    def set_audio(self, y: Optional[np.ndarray], sr: int) -> None:
        self._y, self._sr = y, sr
        self.waveform.set_waveform(y, sr)
        self.refresh()

    def refresh(self) -> None:
        self._update_hint()
        if self._y is None or self._sr <= 0 or len(self._y) == 0:
            self.spectrogram.clear_image()
            return
        duration = len(self._y) / self._sr
        try:
            data, y_max, label, units = self._analyse()
        except Exception as e:
            log.warning("analysis failed: %s", e)
            self.spectrogram.clear_image()
            return
        self.spectrogram.set_image(data, duration, y_max, label, units)

    def _analyse(self) -> Tuple[np.ndarray, float, str, Optional[str]]:
        if self.mode.currentIndex() == 0:
            stft = kudio.STFT(sr=self._sr, n_fft=self.n_fft.value(),
                              hop_length=self.hop.value())
            return stft.forward(self._y), self._sr / 2.0, "Frequency", "Hz"
        feat = kudio.mfcc(self._y, self._sr, n_mfcc=self.n_mfcc.value())
        feat = np.squeeze(feat)               # (frames, n_mfcc)
        if feat.ndim == 2 and feat.shape[0] < feat.shape[1]:
            feat = feat.T                     # tolerate the transposed layout
        return feat, float(feat.shape[1]), "MFCC coefficient", None

    def _update_hint(self) -> None:
        if self.mode.currentIndex() == 0:
            self.call_hint.setText(
                f"kudio.STFT(sr, n_fft={self.n_fft.value()}, "
                f"hop_length={self.hop.value()}).forward(y)")
        else:
            self.call_hint.setText(
                f"kudio.mfcc(y, sr, n_mfcc={self.n_mfcc.value()})")

    # ------------------------------------------------------------- selection

    def set_selection(self, start: float, end: float) -> None:
        if end > start:
            self.waveform.set_selection(start, end)
        else:
            self.waveform.set_selection(0.0, 0.0)
