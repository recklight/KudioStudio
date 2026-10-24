# -*- coding: utf-8 -*-
"""What the plots draw, computed in Python and sent as compact binary.

The page never receives the audio itself. For the waveform it gets, per
pixel column, the lowest and the highest sample in it; for the spectrogram,
the visible stretch of the image already scaled to bytes. Both are a few
kilobytes whatever the length of the clip, which is what keeps a ten-minute
file as quick to zoom as a ten-second one.

The spectrogram is computed by the `kudio` calls that `Settings.call` writes
out for the window, so what is drawn is exactly what a training script would
consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

import kudio

#: Analysis views, as the window offers them. The names are what the client
#: sends back.
MODES = ("stft", "mel", "mfcc")

#: The colour range is chosen from the image: a floor near the median keeps
#: the noise bed dark instead of washing everything out to mid-colour.
LEVEL_PERCENTILES = (40.0, 99.8)


def mono(y: np.ndarray) -> np.ndarray:
    """*y* as one channel, float32. A session holds mono already; a 2-D
    array is ``(frames, channels)``, as `kudio.file_load` gives it, and is
    mixed down across the channels."""
    y = np.asarray(y, dtype=np.float32)
    if y.ndim == 1:
        return y
    return y.mean(axis=1).astype(np.float32)


@dataclass(frozen=True)
class Peaks:
    """The waveform between *t0* and *t1* as the plot draws it.

    ``kind`` is ``"peaks"`` when there are more samples than columns -- then
    *values* holds a (min, max) pair per column -- and ``"samples"`` when
    zoomed in far enough to draw every sample, which *values* then holds.
    *start* is the time of the first column or sample, *step* the time
    between neighbouring ones.
    """

    kind: str
    start: float
    step: float
    values: np.ndarray


def peaks(y: np.ndarray, sr: int, t0: float, t1: float, columns: int) -> Peaks:
    y = mono(y)
    columns = max(1, int(columns))
    i0 = max(0, int(np.floor(t0 * sr)))
    i1 = min(len(y), int(np.ceil(t1 * sr)) + 1)
    if i1 <= i0:
        return Peaks("samples", i0 / sr, 1.0 / sr, np.zeros(0, np.float32))
    span = y[i0:i1]
    # two samples per column at least, or a column could be one sample and
    # a line drawn between neighbours says more
    if len(span) < 2 * columns:
        return Peaks("samples", i0 / sr, 1.0 / sr, span.astype(np.float32))
    edges = np.linspace(0, len(span), columns + 1).astype(np.int64)
    starts = edges[:-1]
    lows = np.minimum.reduceat(span, starts)
    highs = np.maximum.reduceat(span, starts)
    values = np.empty((columns, 2), np.float32)
    values[:, 0], values[:, 1] = lows, highs
    return Peaks("peaks", i0 / sr, (len(span) / sr) / columns, values)


@dataclass(frozen=True)
class Settings:
    """The analysis parameters, under kudio's own keyword names."""

    mode: str = "stft"
    n_fft: int = 512
    hop_length: int = 256
    n_mels: int = 64
    n_mfcc: int = 40

    def checked(self) -> "Settings":
        if self.mode not in MODES:
            raise ValueError(f"unknown view {self.mode!r}")
        for name, low, high in (("n_fft", 64, 8192), ("hop_length", 16, 4096),
                                ("n_mels", 8, 256), ("n_mfcc", 4, 128)):
            value = getattr(self, name)
            if not low <= value <= high:
                raise ValueError(f"{name} has to be between {low} and {high}")
        return self

    def call(self) -> str:
        """The kudio call behind the image, as the window shows it."""
        if self.mode == "stft":
            return (f"kudio.STFT(sr, n_fft={self.n_fft}, "
                    f"hop_length={self.hop_length}).forward(y)")
        if self.mode == "mel":
            return (f"kudio.melspectrogram(y, sr=sr, n_frames=1, "
                    f"n_mels={self.n_mels}, n_fft={self.n_fft}, "
                    f"hop_length={self.hop_length})")
        return f"kudio.mfcc(y, sr, n_mfcc={self.n_mfcc})"


@dataclass(frozen=True)
class Image:
    """A whole clip's time-frequency image, ``(frames, bins)``."""

    data: np.ndarray
    duration: float
    #: the top of the vertical axis: Hz for the STFT, a band or coefficient
    #: count otherwise
    y_max: float
    y_label: str
    y_units: Optional[str]
    levels: Tuple[float, float]


def image(y: np.ndarray, sr: int, settings: Settings) -> Image:
    """The view *settings* describes, for the whole of *y*."""
    y = mono(y)
    s = settings.checked()
    if s.mode == "stft":
        stft = kudio.STFT(sr=sr, n_fft=s.n_fft, hop_length=s.hop_length)
        data, y_max, label, units = stft.forward(y), sr / 2.0, "Frequency", "Hz"
    elif s.mode == "mel":
        # n_frames=1 asks for one vector per frame, i.e. the plain
        # (frames, n_mels) log-mel image rather than stacked context
        data = kudio.melspectrogram(y, sr=sr, n_frames=1, n_mels=s.n_mels,
                                    n_fft=s.n_fft, hop_length=s.hop_length)
        y_max, label, units = float(s.n_mels), "Mel band", None
    else:
        # (1, frames, n_mfcc), as kudio documents it. Not told apart by which
        # side is longer: a clip of fewer frames than coefficients would be
        # turned on its side
        data = np.reshape(kudio.mfcc(y, sr, n_mfcc=s.n_mfcc), (-1, s.n_mfcc))
        y_max, label, units = float(s.n_mfcc), "MFCC coefficient", None
    data = np.asarray(data, dtype=np.float32)
    if data.ndim != 2:
        data = data.reshape(len(data), -1)
    finite = data[np.isfinite(data)]
    lo, hi = (np.percentile(finite, LEVEL_PERCENTILES) if finite.size
              else (0.0, 1.0))
    if not hi > lo:
        hi = lo + 1.0
    return Image(data, len(y) / sr, y_max, label, units, (float(lo), float(hi)))


@dataclass(frozen=True)
class Slice:
    """Part of an `Image`, scaled to bytes for the client to colour.

    *pixels* is ``(rows, columns)`` uint8, row 0 at the top of the plot. The
    columns cover *t0* to *t1* and the rows *y0* to *y1* on the vertical
    axis; both can be a little wider than what was asked for, since frames
    and bins are whole.
    """

    pixels: np.ndarray
    t0: float
    t1: float
    y0: float = 0.0
    y1: float = 0.0


def image_slice(img: Image, t0: float, t1: float, columns: int, rows: int,
                y0: float = 0.0, y1: Optional[float] = None) -> Slice:
    """The stretch *t0*..*t1* of *img*, and of its vertical axis *y0*..*y1*
    (all of it when *y1* is None). Cut before it is pooled, so a zoom into
    the bottom of the axis gets the bins there at full resolution rather
    than the few rows they would have been at full range."""
    frames, bins = img.data.shape
    y1 = img.y_max if y1 is None else y1
    if frames == 0 or img.duration <= 0 or img.y_max <= 0:
        return Slice(np.zeros((0, 0), np.uint8), t0, t1, y0, y1)
    per_frame = img.duration / frames
    f0 = max(0, int(np.floor(t0 / per_frame)))
    f1 = min(frames, int(np.ceil(t1 / per_frame)))
    per_bin = img.y_max / bins
    b0 = max(0, int(np.floor(max(0.0, y0) / per_bin)))
    b1 = min(bins, int(np.ceil(min(y1, img.y_max) / per_bin)))
    if f1 <= f0 or b1 <= b0:
        return Slice(np.zeros((0, 0), np.uint8), t0, t1, y0, y1)
    part = img.data[f0:f1, b0:b1]
    part = _pool(part, max(1, int(columns)), axis=0)
    part = _pool(part, max(1, int(rows)), axis=1)
    lo, hi = img.levels
    scaled = np.clip((part - lo) / (hi - lo), 0.0, 1.0)
    scaled = np.nan_to_num(scaled, nan=0.0)
    pixels = np.round(scaled * 255).astype(np.uint8)
    # frames run along x and bins up y; the client wants rows from the top
    pixels = np.ascontiguousarray(pixels.T[::-1])
    return Slice(pixels, f0 * per_frame, f1 * per_frame, b0 * per_bin, b1 * per_bin)


def _pool(data: np.ndarray, size: int, axis: int) -> np.ndarray:
    """At most *size* entries along *axis*, each the largest of the ones it
    stands for -- a short click stays visible however far out the view is."""
    n = data.shape[axis]
    if n <= size:
        return data
    edges = np.linspace(0, n, size + 1).astype(np.int64)[:-1]
    return np.maximum.reduceat(data, edges, axis=axis)


__all__ = ["MODES", "Peaks", "Settings", "Image", "Slice", "mono", "peaks",
           "image", "image_slice"]
