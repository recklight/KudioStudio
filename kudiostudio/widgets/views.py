# -*- coding: utf-8 -*-
"""Waveform and time-frequency views, both backed by pyqtgraph.

The analysis image is produced by `kudio` itself
(:func:`kudio.waveform_to_spectrogram` / :func:`kudio.mfcc`) so what you see
here is exactly what a training script would consume.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
from kudiostudio.theme import (
    ACCENT_COLOR,
    CALL_COLOR,
    LOUDNESS_COLOR,
    MARKER_COLOR,
    PITCH_COLOR,
    WAVE_COLOR,
)
from kudiostudio.widgets.levels import LevelReadout

log = logging.getLogger(__name__)

pg.setConfigOptions(antialias=False, imageAxisOrder="col-major")

#: More points than this and we let pyqtgraph peak-decimate rather than
#: pushing every sample into the scene graph.
PEAK_DECIMATION_LIMIT = 50_000

#: Seconds of audio kept on screen while a take is running.
MONITOR_SECONDS = 3.0

#: Audio arrives about twenty times a second and every column of it is
#: computed as it lands, but redrawing that often gains nothing a person can
#: see. Only the *drawing* is throttled -- skipping a feed would lose audio.
MONITOR_REDRAW_EVERY = 4

#: Colour range for the rolling spectrogram, in dBFS **per bin**, held fixed.
#: The offline view picks its range from the image because that is the only
#: information it has; a live window has an absolute one, so use it -- colour
#: then means a level rather than "bright compared to the last three seconds",
#: and two takes are comparable. The top is well below 0 because a broadband
#: sound at -20 dBFS spreads that energy over hundreds of bins and no single
#: one of them ever gets near full scale.
MONITOR_LEVELS = (-75.0, -15.0)

#: Width reserved for the right-hand loudness axis. Fixed, and applied to the
#: spectrogram's empty right axis too: the two plots share a time axis, and a
#: gutter on one and not the other slides them apart horizontally -- which is
#: exactly the alignment the shared axis exists to guarantee.
RIGHT_AXIS_WIDTH = 62

#: Width pinned on both plots' left axes. The waveform's wants 71 px for
#: "Amplitude" plus labels like "-0.5"; the spectrogram's wants 35-59
#: depending on the view and the sample rate. Left to themselves they put the
#: same instant in two different places on two plots that share a time axis.
LEFT_AXIS_WIDTH = 72

#: Smallest loudness span the right axis will show, in LU. A steady take
#: varies by a fraction of a dB, and autoscaling to that magnifies its own
#: dither into a level that looks all over the place.
MIN_LOUDNESS_SPAN = 12.0

LOUDNESS_TOOLTIP = (
    "Short-term loudness (EBU Tech 3341) over the waveform, on its own LUFS\n"
    "axis. The integrated figure in the Levels box is one number for the whole\n"
    "clip; this is where it moved."
)


class TimePlot(pg.PlotWidget):
    """A plot whose x axis is time, with a selection region and a playhead.

    The waveform and the spectrogram show different things but behave the same
    along time: the same draggable range, the same cursor, the same
    click-to-seek, the same zoom. That behaviour lives here once, so the two
    cannot end up disagreeing about where "1.2 seconds" is.
    """

    selection_changed = Signal(float, float)
    #: the user clicked at this many seconds
    seek_requested = Signal(float)

    #: Selection fill, as RGBA. Subclasses drawing over a bright image turn the
    #: alpha down: a wash that reads as "highlighted" on a dark waveform reads
    #: as "tinted pink" over a colour map, and hides what you are looking at.
    REGION_FILL = (232, 163, 61, 55)
    REGION_HOVER_FILL = (232, 163, 61, 85)
    REGION_EDGE_WIDTH = 1
    #: marker ranges, fainter still than the selection for the same reason
    MARKER_FILL = (155, 142, 196, 40)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent, background=None)
        self.setLabel("bottom", "Time", units="s")
        # Both plots are x-linked, which makes them show the same time range --
        # and does nothing about where that range lands on screen. The left
        # axis is as wide as its tick labels need, so "Amplitude" (70 px) and
        # "Frequency" (35-59 px, depending on the rate and the view) put the
        # same instant in two different places, and the offset *changed* when
        # you switched modes. Pinning the width is what actually makes the
        # shared axis mean what this class promises.
        self.plotItem.getAxis("left").setWidth(LEFT_AXIS_WIDTH)

        self._region = pg.LinearRegionItem(
            brush=pg.mkBrush(*self.REGION_FILL),
            hoverBrush=pg.mkBrush(*self.REGION_HOVER_FILL),
            pen=pg.mkPen(ACCENT_COLOR, width=self.REGION_EDGE_WIDTH))
        self._region.setZValue(10)
        self._region.hide()
        self.addItem(self._region, ignoreBounds=True)
        self._region.sigRegionChangeFinished.connect(self._emit_region)

        self._cursor = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen(ACCENT_COLOR, width=1))
        self._cursor.setZValue(11)
        self._cursor.hide()
        self.addItem(self._cursor, ignoreBounds=True)

        #: marker overlay items, rebuilt wholesale on every change
        self._marker_items: List[pg.GraphicsObject] = []

        self._duration = 0.0
        self.scene().sigMouseClicked.connect(self._on_click)

    @property
    def duration(self) -> float:
        return self._duration

    # ---------------------------------------------------------------- zooming

    def zoom_to(self, start: float, end: float) -> None:
        if end > start:
            self.setXRange(start, end, padding=0.02)

    def zoom_full(self) -> None:
        if self._duration > 0:
            self.setXRange(0.0, self._duration, padding=0.0)

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

    # -------------------------------------------------------------- playhead

    def set_cursor(self, seconds: Optional[float]) -> None:
        if seconds is None:
            self._cursor.hide()
        else:
            self._cursor.setPos(seconds)
            self._cursor.show()

    # --------------------------------------------------------------- markers

    def set_markers(self, markers) -> None:
        """Draw the marker overlay. Not interactive — the list is the handle.

        Markers are drawn *behind* the selection and the playhead: they are
        reference points, and a marker that hides the range you are dragging
        has stopped being one.
        """
        for item in self._marker_items:
            self.removeItem(item)
        self._marker_items = []

        pen = pg.mkPen(MARKER_COLOR, width=1, style=Qt.DashLine)
        for marker in markers:
            if marker.is_range:
                item = pg.LinearRegionItem(
                    values=(marker.start, marker.end), movable=False,
                    brush=pg.mkBrush(self.MARKER_FILL), pen=pen)
            else:
                item = pg.InfiniteLine(pos=marker.start, angle=90,
                                       movable=False, pen=pen)
            item.setZValue(5)
            self.addItem(item, ignoreBounds=True)
            self._marker_items.append(item)

    def _on_click(self, ev) -> None:
        """Left-click on the plot moves the playhead.

        Clicks the region item has already taken (dragging an edge) arrive
        accepted, so they are left alone.
        """
        if self._duration <= 0 or ev.button() != Qt.LeftButton or ev.isAccepted():
            return
        view = self.plotItem.vb
        if not view.sceneBoundingRect().contains(ev.scenePos()):
            return          # click landed on an axis, not the plot area
        x = float(view.mapSceneToView(ev.scenePos()).x())
        self.seek_requested.emit(min(max(x, 0.0), self._duration))


class WaveformView(TimePlot):
    """Amplitude against time."""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setLabel("left", "Amplitude")
        self.showGrid(x=True, y=True, alpha=0.2)
        self.setMouseEnabled(x=True, y=False)
        self.setYRange(-1.0, 1.0, padding=0.02)

        self._curve = self.plot(pen=pg.mkPen(WAVE_COLOR, width=1))
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)

        # A second view box sharing the time axis, so the loudness curve can be
        # read in LUFS instead of being squashed into the amplitude scale --
        # "-31 LUFS here" is a number you can act on; "about a third as tall"
        # is not.
        self._loudness_vb = pg.ViewBox(enableMenu=False)
        self._loudness_vb.setMouseEnabled(x=False, y=False)
        self.plotItem.scene().addItem(self._loudness_vb)
        # NOT setXLink: the time axis is copied one way, in _sync_loudness_box.
        # A real link makes this box push its x range back onto the plot, which
        # re-fires the sync that set it -- the two then hand the same range to
        # each other until the process stops responding. Found by a test that
        # toggled the curve off and on again.

        right = self.plotItem.getAxis("right")
        right.linkToView(self._loudness_vb)
        right.setLabel("Loudness", units="LUFS")
        right.setWidth(RIGHT_AXIS_WIDTH)

        self._loudness = pg.PlotDataItem(
            pen=pg.mkPen(LOUDNESS_COLOR, width=2), connect="finite")
        self._loudness.setZValue(15)
        self._loudness_vb.addItem(self._loudness)
        # resize moves the box; a zoom or a scroll changes the range inside it
        self.plotItem.vb.sigResized.connect(self._sync_loudness_box)
        self.plotItem.vb.sigXRangeChanged.connect(self._sync_loudness_box)
        self.plotItem.hideAxis("right")

    # ------------------------------------------------------------- loudness

    def _sync_loudness_box(self) -> None:
        """Keep the second view box exactly on top of the first.

        pyqtgraph does not lay a second ViewBox out for you; without this it
        keeps whatever geometry it was created with and the curve drifts off
        the plot the first time the window is resized.
        """
        self._loudness_vb.setGeometry(self.plotItem.vb.sceneBoundingRect())
        self._loudness_vb.linkedViewChanged(self.plotItem.vb,
                                            self._loudness_vb.XAxis)

    def set_loudness(self, times, lufs) -> None:
        """Draw a loudness curve against the right-hand axis, in LUFS."""
        values = np.asarray(lufs, dtype=np.float64) if lufs is not None \
            else np.zeros(0)
        if times is None or values.size == 0:
            self.clear_loudness()
            return
        # -inf is digital silence; pyqtgraph breaks the line at non-finite
        # values, which is what silence should look like
        values = np.where(np.isfinite(values), values, np.nan)
        self._loudness.setData(np.asarray(times, dtype=np.float64), values)

        finite = values[np.isfinite(values)]
        if finite.size:
            lo, hi = float(np.min(finite)), float(np.max(finite))
            if hi - lo < MIN_LOUDNESS_SPAN:
                # a steady take would otherwise have its own dither magnified
                # to fill the plot, which reads as a level that is all over the
                # place
                middle = (hi + lo) / 2.0
                lo, hi = (middle - MIN_LOUDNESS_SPAN / 2,
                          middle + MIN_LOUDNESS_SPAN / 2)
            self._loudness_vb.setYRange(lo - 1.0, hi + 1.0, padding=0.0)

        self.plotItem.showAxis("right")
        self._loudness.show()
        self._sync_loudness_box()

    def clear_loudness(self) -> None:
        self._loudness.clear()
        self._loudness.hide()
        self.plotItem.hideAxis("right")

    @property
    def loudness_visible(self) -> bool:
        return bool(self._loudness.isVisible())

    # ------------------------------------------------------------------ data

    def set_waveform(self, y: Optional[np.ndarray], sr: int,
                     *, keep_view: bool = False, x0: float = 0.0,
                     span: Optional[float] = None) -> None:
        """Draw *y*. With *keep_view* the axes are left where the user put them.

        Rescaling the amplitude axis to each new peak makes every version of a
        clip fill the same height, which hides the one thing an A/B comparison
        is looking for. When the clip is a replacement for the same audio, the
        axis is kept -- and only ever grown, so nothing is drawn off-screen.

        *x0* and *span* place a short clip inside a wider axis. The rolling
        monitor needs that: its spectrogram is always the full window wide
        with the newest column at the right edge, and a waveform drawn from
        zero would put the same instant at two different places on two plots
        that share a time axis.
        """
        if y is None or sr <= 0 or len(y) == 0:
            self.clear_waveform()
            return
        self._duration = span if span is not None else len(y) / sr
        t = x0 + np.arange(len(y), dtype=np.float64) / sr
        self._curve.setData(t, np.asarray(y, dtype=np.float64))
        peak = float(np.max(np.abs(y))) or 1.0
        if keep_view:
            if peak > self.viewRange()[1][1]:
                self.setYRange(-peak, peak, padding=0.05)
            return
        self.setYRange(-peak, peak, padding=0.05)
        self.setXRange(0.0, self._duration, padding=0.0)

    def clear_waveform(self) -> None:
        self._duration = 0.0
        self._curve.setData([], [])
        self._region.hide()
        self._cursor.hide()
        self.clear_loudness()


class SpectrogramView(TimePlot):
    """Log-power STFT, mel or MFCC, rendered as an image."""

    # barely-there fill and stronger edges: the image underneath is the point
    REGION_FILL = (232, 163, 61, 18)
    REGION_HOVER_FILL = (232, 163, 61, 38)
    REGION_EDGE_WIDTH = 2
    MARKER_FILL = (155, 142, 196, 22)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setLabel("left", "Frequency", units="Hz")
        self.setMouseEnabled(x=True, y=True)

        self._image = pg.ImageItem()
        self.addItem(self._image)
        try:
            self._image.setColorMap(pg.colormap.get("viridis"))
        except Exception:  # pragma: no cover - very old pyqtgraph
            log.debug("viridis colormap unavailable; using default lookup table")
        self._levels: Optional[Tuple[float, float]] = None

        # A curve in Hz drawn over the image. `connect="finite"` is the whole
        # reason this is drawable: a pitch track is NaN wherever the frame is
        # unvoiced, and a line joined across those gaps would claim a pitch
        # through every pause in the recording.
        self._overlay = pg.PlotDataItem(
            pen=pg.mkPen(PITCH_COLOR, width=2), connect="finite")
        self._overlay.setZValue(20)
        self._overlay.hide()
        self.addItem(self._overlay)

    def set_overlay(self, times, values) -> None:
        """Draw *values* (Hz) against *times* (seconds) over the image.

        Only meaningful while the vertical axis is frequency; the caller is
        responsible for not asking on a mel or MFCC image, where the same
        number means a band index.
        """
        if times is None or values is None or len(times) == 0:
            self.clear_overlay()
            return
        self._overlay.setData(np.asarray(times, dtype=np.float64),
                              np.asarray(values, dtype=np.float64))
        self._overlay.show()

    def clear_overlay(self) -> None:
        self._overlay.clear()
        self._overlay.hide()

    @property
    def overlay_visible(self) -> bool:
        return bool(self._overlay.isVisible())

    @property
    def levels(self) -> Optional[Tuple[float, float]]:
        """The colour range in use, or ``None`` before the first image."""
        return self._levels

    def set_image(self, data: np.ndarray, duration: float, y_max: float,
                  y_label: str, y_units: Optional[str],
                  *, keep_levels: bool = False,
                  levels: Optional[Tuple[float, float]] = None) -> None:
        """*data* is ``(frames, bins)`` -- the layout kudio returns.

        *keep_levels* reuses the colour range from the previous image instead
        of choosing one from this data. Levels picked per image make two
        spectrograms incomparable by colour, which defeats an A/B: a denoiser
        that removed 20 dB of hiss would redraw at the same brightness and
        look like it had done nothing.

        *levels* pins the range to an absolute one instead. The rolling view
        uses it: a range recomputed from a window that scrolls twenty times a
        second is a picture whose brightness means nothing and changes anyway.
        """
        if data is None or data.size == 0:
            self.clear_image()
            return
        data = np.asarray(data, dtype=np.float32)
        if levels is not None:
            self._levels = (float(levels[0]), float(levels[1]))
        elif not (keep_levels and self._levels is not None):
            finite = data[np.isfinite(data)]
            if finite.size:
                # a floor near the median keeps the noise bed dark instead of
                # washing the whole image out to mid-colour
                lo, hi = np.percentile(finite, (40, 99.8))
                if hi > lo:
                    self._levels = (float(lo), float(hi))
        if self._levels is not None:
            self._image.setLevels(self._levels)
        self._image.setImage(data, autoLevels=False)
        self._image.setRect(0.0, 0.0, duration, y_max)
        self.setLabel("left", y_label, units=y_units)
        self.setXRange(0.0, duration, padding=0.0)
        self.setYRange(0.0, y_max, padding=0.0)
        self._duration = duration

    def reserve_right_gutter(self, on: bool) -> None:
        """Show or hide an empty right axis the same width as the waveform's.

        Nothing is drawn against it. It exists so that turning the loudness
        curve on does not shift this plot's time axis relative to the one
        above it.
        """
        axis = self.plotItem.getAxis("right")
        axis.setWidth(RIGHT_AXIS_WIDTH)
        axis.setStyle(showValues=False)
        self.plotItem.showAxis("right") if on else self.plotItem.hideAxis("right")

    def clear_image(self) -> None:
        self._image.clear()
        self._levels = None
        self._duration = 0.0
        self._cursor.hide()
        self._region.hide()
        self.clear_overlay()


class AnalysisView(QWidget):
    """Waveform above, time-frequency below, plus the analysis parameters.

    The parameter names are the `kudio` keyword arguments verbatim -- the
    caption under the controls shows the exact call being made.
    """

    selection_changed = Signal(float, float)
    seek_requested = Signal(float)

    MODES = ("STFT (log-power)", "Mel (log-power)", "MFCC")
    MODE_STFT, MODE_MEL, MODE_MFCC = range(3)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._y: Optional[np.ndarray] = None
        self._sr: int = 0
        self._sel: Optional[Tuple[float, float]] = None
        self._syncing = False       # guards the spin box <-> region round trip
        #: (times, Hz) drawn over the spectrogram, or None
        self._overlay: Optional[Tuple[np.ndarray, np.ndarray]] = None
        #: the short-term loudness curve for the current audio, or None. Held
        #: whether or not it is being drawn, so the checkbox is instant.
        self._loudness = None
        # live take state; _monitor_sr > 0 is what "a take is running" means
        self._monitor_sr = 0
        self._monitor_wave = np.zeros(0, dtype=np.float32)
        self._monitor_stream = None
        self._monitor_ticks = 0

        self.waveform = WaveformView(self)
        self.spectrogram = SpectrogramView(self)
        # both plots drive the selection and the playhead; set_selection writes
        # to both with their signals blocked, so this cannot feed back
        for plot in (self.waveform, self.spectrogram):
            plot.selection_changed.connect(self.selection_changed)
            plot.seek_requested.connect(self.seek_requested)
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
        self.n_mels = QSpinBox()
        self.n_mels.setRange(8, 256)
        self.n_mels.setValue(64)
        self.n_mfcc = QSpinBox()
        self.n_mfcc.setRange(4, 128)
        self.n_mfcc.setValue(40)

        self.show_loudness = QCheckBox("Loudness over time")
        self.show_loudness.setToolTip(LOUDNESS_TOOLTIP)
        self.show_loudness.setEnabled(False)
        self.show_loudness.toggled.connect(self._apply_loudness)

        controls = QFormLayout()
        controls.addRow("View", self.mode)
        controls.addRow("n_fft", self.n_fft)
        controls.addRow("hop_length", self.hop)
        controls.addRow("n_mels", self.n_mels)
        controls.addRow("n_mfcc", self.n_mfcc)
        controls.addRow("", self.show_loudness)

        self.call_hint = QLabel()
        self.call_hint.setWordWrap(True)
        self.call_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.call_hint.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")

        box = QGroupBox("Analysis")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(controls)
        box_layout.addWidget(self.call_hint)
        box.setMaximumWidth(260)

        selection_box = self._build_selection_box()
        self.levels = LevelReadout(self)
        # the readout already walks the signal for its report; the curve comes
        # out of the same pass rather than being measured a second time here
        self.levels.curve_ready.connect(self.set_loudness_curve)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(box)
        side.addWidget(selection_box)
        side.addWidget(self.levels)
        side.addStretch(1)

        plots = QVBoxLayout()
        plots.setContentsMargins(0, 0, 0, 0)
        plots.addWidget(self.waveform, stretch=1)
        plots.addWidget(self.spectrogram, stretch=2)

        layout = QHBoxLayout(self)
        layout.addLayout(plots, stretch=1)
        layout.addLayout(side)

        for w in (self.mode, self.n_fft, self.hop, self.n_mels, self.n_mfcc):
            signal = w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged
            # via a slot that takes no argument: currentIndexChanged would
            # otherwise hand its index straight into refresh()'s keyword
            signal.connect(self._on_controls_changed)
        self.mode.currentIndexChanged.connect(self._sync_controls)
        self._sync_controls()
        self._update_hint()

    def _build_selection_box(self) -> QGroupBox:
        """Type a range in when the mouse is not precise enough."""
        self.sel_start = QDoubleSpinBox()
        self.sel_end = QDoubleSpinBox()
        for spin in (self.sel_start, self.sel_end):
            spin.setDecimals(3)
            spin.setSingleStep(0.05)
            spin.setSuffix(" s")
            spin.setRange(0.0, 0.0)
            spin.valueChanged.connect(self._emit_typed_selection)

        self.sel_info = QLabel("no selection")
        self.sel_info.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")

        self.zoom_button = QPushButton("Zoom")
        self.zoom_button.setToolTip("Fit the selection to the width (Ctrl+E)")
        self.zoom_button.clicked.connect(self.zoom_to_selection)
        self.full_button = QPushButton("Full")
        self.full_button.setToolTip("Show the whole clip again (Ctrl+F)")
        self.full_button.clicked.connect(self.zoom_full)

        buttons = QHBoxLayout()
        buttons.addWidget(self.zoom_button)
        buttons.addWidget(self.full_button)

        form = QFormLayout()
        form.addRow("start", self.sel_start)
        form.addRow("end", self.sel_end)

        box = QGroupBox("Selection")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.sel_info)
        box_layout.addLayout(buttons)
        box.setMaximumWidth(260)
        return box

    def _sync_controls(self) -> None:
        """Grey out the parameters the current view does not read."""
        mode = self.mode.currentIndex()
        spectral = mode in (self.MODE_STFT, self.MODE_MEL)
        self.n_fft.setEnabled(spectral)
        self.hop.setEnabled(spectral)
        self.n_mels.setEnabled(mode == self.MODE_MEL)
        self.n_mfcc.setEnabled(mode == self.MODE_MFCC)

    # ------------------------------------------------------------------ data

    def set_audio(self, y: Optional[np.ndarray], sr: int) -> None:
        # Real audio always wins over a live view: a take that has been
        # committed, or a file opened mid-take, ends the rolling display
        # whether or not anyone remembered to say so.
        was_live = bool(self._monitor_sr)
        if was_live:
            self.stop_monitor()
        # The contour was measured from audio that may no longer be there. It
        # is cheap to draw and expensive to compute, which is exactly the shape
        # of thing that gets left on screen describing something else.
        self.clear_pitch_overlay()
        duration = len(y) / sr if (y is not None and sr > 0 and len(y)) else 0.0
        # An edit that keeps the length -- denoise, gain, an A/B swap -- is a
        # replacement for the same audio, so the axes and the colour scale stay
        # put: that is exactly when you are comparing. A real length change
        # (a trim) starts from the whole clip again.
        #
        # A take that happens to be about as long as the monitor window is not
        # "the same clip" as the monitor window, and the live colour range is
        # in dBFS while the offline image is kudio's log-power: keeping either
        # across that boundary would be comparing two different scales.
        span = (self.waveform.viewRange()[0]
                if duration > 0 and not was_live and self._same_clip(duration)
                else None)

        self._y, self._sr = y, sr
        self.waveform.set_waveform(y, sr, keep_view=span is not None)
        self.levels.set_audio(y, sr)
        self._syncing = True
        for spin in (self.sel_start, self.sel_end):
            spin.setMaximum(duration)
        self._syncing = False
        self.refresh(keep_levels=span is not None)

        if span is not None:
            self.waveform.setXRange(*span, padding=0.0)
            self.spectrogram.setXRange(*span, padding=0.0)

    #: Duration change below this counts as the same clip. Frame-based
    #: processing does not return exactly what it was given -- kudio's
    #: `trad_enhance` comes back 128 samples (8 ms) short -- and treating that
    #: as a new clip would reset the zoom, the amplitude axis and the colour
    #: scale after every denoise, which is precisely when you want them kept.
    SAME_CLIP_TOLERANCE = 0.05

    def _same_clip(self, duration: float) -> bool:
        previous = self.waveform.duration
        return previous > 0 and abs(duration - previous) <= self.SAME_CLIP_TOLERANCE

    def _on_controls_changed(self) -> None:
        """A parameter moved, so the data itself is different: rescale colour."""
        if self._monitor_sr:
            # A live stream cannot re-analyse what has already scrolled past,
            # so changing the geometry mid-take starts the picture again. That
            # is visible, which is better than a window whose left half was
            # computed with different settings from its right half.
            self._monitor_stream = self._build_monitor_stream()
            self._update_hint()
            self._draw_monitor()
            return

        self.refresh(keep_levels=False)

    def refresh(self, *, keep_levels: bool = False) -> None:
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
        self.spectrogram.set_image(data, duration, y_max, label, units,
                                   keep_levels=keep_levels)
        self._apply_overlay()

    def _analyse(self) -> Tuple[np.ndarray, float, str, Optional[str]]:
        mode = self.mode.currentIndex()
        if mode == self.MODE_STFT:
            stft = kudio.STFT(sr=self._sr, n_fft=self.n_fft.value(),
                              hop_length=self.hop.value())
            return stft.forward(self._y), self._sr / 2.0, "Frequency", "Hz"
        if mode == self.MODE_MEL:
            # n_frames=1 asks for one vector per frame, i.e. the plain
            # (frames, n_mels) log-mel image rather than stacked context
            feat = kudio.melspectrogram(self._y, sr=self._sr, n_frames=1,
                                        n_mels=self.n_mels.value(),
                                        n_fft=self.n_fft.value(),
                                        hop_length=self.hop.value())
            return feat, float(self.n_mels.value()), "Mel band", None
        feat = kudio.mfcc(self._y, self._sr, n_mfcc=self.n_mfcc.value())
        feat = np.squeeze(feat)               # (frames, n_mfcc)
        if feat.ndim == 2 and feat.shape[0] < feat.shape[1]:
            feat = feat.T                     # tolerate the transposed layout
        return feat, float(feat.shape[1]), "MFCC coefficient", None

    def _update_hint(self) -> None:
        mode = self.mode.currentIndex()
        if self._monitor_sr:
            mels = (f", n_mels={self.n_mels.value()}"
                    if mode == self.MODE_MEL else "")
            note = "  # MFCC has no live form; showing the STFT" \
                if mode == self.MODE_MFCC else ""
            self.call_hint.setText(
                f"kudio.SpectrogramStream(sr, n_fft={self.n_fft.value()}, "
                f"hop_length={self.hop.value()}, "
                f"seconds={MONITOR_SECONDS:g}{mels}).push(chunk){note}")
            return
        if mode == self.MODE_STFT:
            self.call_hint.setText(
                f"kudio.STFT(sr, n_fft={self.n_fft.value()}, "
                f"hop_length={self.hop.value()}).forward(y)")
        elif mode == self.MODE_MEL:
            self.call_hint.setText(
                f"kudio.melspectrogram(y, sr=sr, n_frames=1, "
                f"n_mels={self.n_mels.value()}, n_fft={self.n_fft.value()}, "
                f"hop_length={self.hop.value()})")
        else:
            self.call_hint.setText(
                f"kudio.mfcc(y, sr, n_mfcc={self.n_mfcc.value()})")

    # --------------------------------------------------------------- playhead

    def set_cursor(self, seconds: Optional[float]) -> None:
        """Move the playhead on both plots -- they share the time axis."""
        self.waveform.set_cursor(seconds)
        self.spectrogram.set_cursor(seconds)

    # ---------------------------------------------------------------- markers

    def set_markers(self, markers) -> None:
        """Draw the markers on both plots, for the same reason as the cursor."""
        self.waveform.set_markers(markers)
        self.spectrogram.set_markers(markers)

    # ---------------------------------------------------------------- monitor

    # --------------------------------------------------------------- overlay

    def set_loudness_curve(self, curve) -> None:
        """Take the short-term loudness for the current audio, or ``None``.

        Held rather than drawn: the checkbox then costs nothing, and a curve
        measured from audio that has since been edited is replaced rather than
        left on screen describing something else.
        """
        self._loudness = curve
        self._apply_loudness()

    def _apply_loudness(self) -> None:
        curve = self._loudness
        # A checkbox that ticks and does nothing is worse than one that says
        # why it cannot: short-term loudness needs three seconds, and a clip
        # that has not got them has no curve rather than a degraded one.
        self.show_loudness.setEnabled(curve is not None)
        self.show_loudness.setToolTip(
            LOUDNESS_TOOLTIP if curve is not None else
            f"The clip is shorter than the {kudio.SHORT_TERM:g} s short-term "
            f"window (EBU Tech 3341), so there is no curve to draw.")

        if curve is None or not self.show_loudness.isChecked():
            self.waveform.clear_loudness()
            self.spectrogram.reserve_right_gutter(False)
            return
        self.waveform.set_loudness(curve.times, curve.lufs)
        self.spectrogram.reserve_right_gutter(True)

    def set_pitch_overlay(self, times, hz) -> None:
        """Draw an f0 contour over the spectrogram.

        Held rather than drawn once, because every redraw replaces the image
        and the contour has to come back with it.
        """
        self._overlay = (np.asarray(times, dtype=np.float64),
                         np.asarray(hz, dtype=np.float64))
        self._apply_overlay()

    def clear_pitch_overlay(self) -> None:
        self._overlay = None
        self.spectrogram.clear_overlay()

    def _apply_overlay(self) -> None:
        """Show the contour only where the vertical axis is actually Hz.

        On a mel or MFCC image the same number is a band index, so a curve at
        "120" would land wherever band 120 happens to be -- a plausible-looking
        line in the wrong place, which is worse than no line.
        """
        if self._overlay is None or self._monitor_sr or \
                self.mode.currentIndex() != self.MODE_STFT:
            self.spectrogram.clear_overlay()
            return
        self.spectrogram.set_overlay(*self._overlay)

    # --------------------------------------------------------------- monitor

    def start_monitor(self, sr: int) -> None:
        """A take has begun: build the rolling views and clear what was there.

        The time axis now reads 0..window rather than the timeline, because
        while recording there is no timeline yet.
        """
        self._monitor_sr = int(sr)
        self._monitor_wave = np.zeros(0, dtype=np.float32)
        self._monitor_ticks = 0
        self._monitor_stream = self._build_monitor_stream()
        self._loudness = None
        self._apply_loudness()
        self._apply_overlay()
        self._update_hint()

    def _build_monitor_stream(self):
        """A `kudio.SpectrogramStream` matching the current view settings.

        MFCC has no live form worth watching -- it is a compressed feature for
        a model, not a picture of a room -- so that mode falls back to the STFT
        and the caption says so.
        """
        if self._monitor_sr <= 0:
            return None
        mel = self.n_mels.value() if self.mode.currentIndex() == self.MODE_MEL else 0
        try:
            return kudio.SpectrogramStream(
                self._monitor_sr, n_fft=self.n_fft.value(),
                hop_length=self.hop.value(), seconds=MONITOR_SECONDS,
                n_mels=mel)
        except Exception as e:                          # noqa: BLE001
            log.warning("live spectrogram unavailable: %s", e)
            return None

    def feed_monitor(self, y, sr: int) -> None:
        """Audio that has just arrived, and that this view has not seen before.

        Every sample is pushed through the column-wise STFT as it lands, so
        each column is computed exactly once; only the redraw is throttled.
        That is the whole reason a rolling spectrogram is affordable here and
        was not before.
        """
        if sr != self._monitor_sr:
            # a take that began without a start_monitor -- take its word for it
            self.start_monitor(sr)
        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1:
            # mix down rather than reshape: flattening (n, channels) interleaves
            # them, and `SpectrogramStream` would then analyse a signal nobody
            # recorded. It refuses 2-D input for exactly that reason.
            y = y.mean(axis=1)
        if not y.size:
            return

        keep = max(1, int(MONITOR_SECONDS * sr))
        self._monitor_wave = np.concatenate([self._monitor_wave, y])[-keep:]
        if self._monitor_stream is not None:
            self._monitor_stream.push(y)

        self._monitor_ticks += 1
        if self._monitor_ticks % MONITOR_REDRAW_EVERY == 0:
            self._draw_monitor()

    def _draw_monitor(self) -> None:
        if self._monitor_sr <= 0:
            return
        stream = self._monitor_stream
        # Both plots share a time axis and both are right-aligned on "now", so
        # while the window is still filling the waveform starts partway across
        # rather than at zero. Drawn from zero it would put the same instant in
        # two different places on two plots that are meant to line up.
        span = stream.window_seconds if stream is not None else MONITOR_SECONDS
        held = len(self._monitor_wave) / self._monitor_sr
        self.waveform.set_waveform(self._monitor_wave, self._monitor_sr,
                                   x0=max(0.0, span - held), span=span)
        if stream is None:
            return
        if stream.n_mels:
            y_max, label, units = float(stream.n_bins), "Mel band", None
        else:
            y_max, label, units = self._monitor_sr / 2.0, "Frequency", "Hz"
        self.spectrogram.set_image(stream.columns(), stream.window_seconds,
                                   y_max, label, units, levels=MONITOR_LEVELS)

    def stop_monitor(self) -> None:
        """The take is over. The last picture stays until real audio replaces
        it -- blanking it would hide the end of what was just recorded."""
        self._monitor_sr = 0
        self._monitor_stream = None
        self._monitor_wave = np.zeros(0, dtype=np.float32)
        self._update_hint()

    # ---------------------------------------------------------------- zooming

    def zoom_to_selection(self) -> None:
        if self._sel is None:
            return
        start, end = self._sel
        self.waveform.zoom_to(start, end)
        self.spectrogram.setXRange(start, end, padding=0.02)

    def zoom_full(self) -> None:
        self.waveform.zoom_full()
        if self.waveform.duration > 0:
            self.spectrogram.setXRange(0.0, self.waveform.duration, padding=0.0)

    # ------------------------------------------------------------- selection

    def set_selection(self, start: float, end: float) -> None:
        self._sel = (start, end) if end > start else None
        if self._sel is None:
            start = end = 0.0
        for plot in (self.waveform, self.spectrogram):
            plot.set_selection(start, end)

        self._syncing = True
        self.sel_start.setValue(start)
        self.sel_end.setValue(end)
        self._syncing = False

        self.sel_info.setText(f"{end - start:.3f} s selected" if self._sel
                              else "no selection")
        self.zoom_button.setEnabled(self._sel is not None)

    def _emit_typed_selection(self) -> None:
        """A spin box was edited. Half-typed ranges are ignored, not swapped."""
        if self._syncing:
            return
        start, end = self.sel_start.value(), self.sel_end.value()
        if end > start:
            self.selection_changed.emit(start, end)
