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

#: The largest STFT computed, frames times bins. Every view starts from the
#: full STFT, mel and MFCC included, so this counts n_fft // 2 + 1 bins
#: whatever the view keeps. At 64 million values the complex64 STFT of
#: float32 audio is 512 MB, the power taken from it 256 MB more, and an STFT
#: view holds another 256 MB of float32 after that: about 1 GB at the peak.
#: Ten minutes at 192 kHz with n_fft 65536 and a hop of 16384 is 7032 frames
#: of 32769 bins, 230 million, and is refused with what to change.
MAX_IMAGE_VALUES = 64_000_000

#: The mel scale kudio's mel filters are spaced on: 200/3 Hz a mel up to
#: 1000 Hz, which is 15 mel, and from there 6.4 times the frequency every 27
#: mel. M. Slaney, "Auditory Toolbox", Interval Research Corporation,
#: Technical Report 1998-010, 1998.
MEL_HZ = 200.0 / 3.0
KNEE_HZ, KNEE_MEL = 1000.0, 15.0
LOG_RATIO, LOG_MELS = 6.4, 27.0


def mel_to_hz(mel) -> np.ndarray:
    mel = np.asarray(mel, dtype=np.float64)
    above = KNEE_HZ * LOG_RATIO ** ((mel - KNEE_MEL) / LOG_MELS)
    return np.where(mel < KNEE_MEL, mel * MEL_HZ, above)


def hz_to_mel(hz) -> np.ndarray:
    hz = np.asarray(hz, dtype=np.float64)
    # where() works out both sides of the knee, and 0 Hz has no log
    ratio = np.maximum(hz, KNEE_HZ) / KNEE_HZ
    above = KNEE_MEL + LOG_MELS * np.log(ratio) / np.log(LOG_RATIO)
    return np.where(hz < KNEE_HZ, hz / MEL_HZ, above)


def mel_bands_hz(sr: int, n_mels: int) -> Tuple[float, ...]:
    """Where the mel bands from 0 Hz to Nyquist are centred, lowest first.

    A band's filter rises from the centre below its own and falls to the one
    above, so the points are n_mels + 2, evenly spaced in mel, and the two at
    the ends are no band's centre.
    """
    points = np.linspace(0.0, float(hz_to_mel(sr / 2.0)), n_mels + 2)
    return tuple(mel_to_hz(points[1:-1]).tolist())


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
    #: the STFT's frequency axis on a log scale. How it is drawn, not what is
    #: computed, so the kudio call is the same either way
    log_freq: bool = False

    def checked(self) -> "Settings":
        if self.mode not in MODES:
            raise ValueError(f"unknown view {self.mode!r}")
        # 65536 points is 2.9 Hz a bin at 192 kHz, fine enough to part the
        # lines of a 50 Hz hum
        for name, low, high in (("n_fft", 64, 65536), ("hop_length", 16, 16384),
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
        return (f"kudio.mfcc(y, sr, n_mfcc={self.n_mfcc}, n_fft={self.n_fft}, "
                f"hop_length={self.hop_length})")


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
    #: for a mel image, the centre frequency of each band, bottom first, so
    #: the axis can be labelled in Hz where the bands are
    bands_hz: Optional[Tuple[float, ...]] = None


def image(y: np.ndarray, sr: int, settings: Settings) -> Image:
    """The view *settings* describes, for the whole of *y*."""
    y = mono(y)
    s = settings.checked()
    frames, bins = 1 + len(y) // s.hop_length, s.n_fft // 2 + 1
    if frames * bins > MAX_IMAGE_VALUES:
        raise ValueError(f"{frames} frames of {bins} STFT bins is too large to hold; "
                         f"raise hop_length or lower n_fft, or open a shorter clip")
    bands = None
    if s.mode == "stft":
        stft = kudio.STFT(sr=sr, n_fft=s.n_fft, hop_length=s.hop_length)
        data, y_max, label, units = stft.forward(y), sr / 2.0, "Frequency", "Hz"
    elif s.mode == "mel":
        # n_frames=1 asks for one vector per frame, i.e. the plain
        # (frames, n_mels) log-mel image rather than stacked context
        data = kudio.melspectrogram(y, sr=sr, n_frames=1, n_mels=s.n_mels,
                                    n_fft=s.n_fft, hop_length=s.hop_length)
        y_max, label, units = float(s.n_mels), "Mel band", None
        bands = mel_bands_hz(sr, s.n_mels)
    else:
        # (1, frames, n_mfcc), as kudio documents it. Not told apart by which
        # side is longer: a clip of fewer frames than coefficients would be
        # turned on its side
        data = np.reshape(kudio.mfcc(y, sr, n_mfcc=s.n_mfcc, n_fft=s.n_fft,
                                     hop_length=s.hop_length), (-1, s.n_mfcc))
        y_max, label, units = float(s.n_mfcc), "MFCC coefficient", None
    data = np.asarray(data, dtype=np.float32)
    if data.ndim != 2:
        data = data.reshape(len(data), -1)
    finite = data[np.isfinite(data)]
    lo, hi = (np.percentile(finite, LEVEL_PERCENTILES) if finite.size
              else (0.0, 1.0))
    if not hi > lo:
        hi = lo + 1.0
    return Image(data, len(y) / sr, y_max, label, units, (float(lo), float(hi)), bands)


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
    #: the rows are spaced evenly in log frequency rather than in Hz
    log: bool = False


def lowest_log_hz(img: Image) -> float:
    """Where a log axis starts: the first bin above DC."""
    if img.data.ndim == 2 and img.data.shape[1] > 1:
        return img.y_max / (img.data.shape[1] - 1)
    return 1.0


def image_slice(img: Image, t0: float, t1: float, columns: int, rows: int,
                y0: float = 0.0, y1: Optional[float] = None, log: bool = False) -> Slice:
    """The stretch *t0*..*t1* of *img*, and of its vertical axis *y0*..*y1*
    (all of it when *y1* is None). Cut before it is pooled, so a zoom into
    the bottom of the axis gets the bins there at full resolution rather
    than the few rows they would have been at full range.

    With *log*, which only an axis in Hz takes, the rows are spaced evenly
    in log frequency from *y0*, or the first bin above DC, to *y1*. A row
    narrower than a bin repeats that bin; a wider one takes the largest of
    the bins it covers, as the linear rows do."""
    frames, bins = img.data.shape
    y1 = img.y_max if y1 is None else y1
    if log and img.y_units == "Hz":
        return _log_slice(img, t0, t1, columns, rows, y0, y1)
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


def _log_slice(img: Image, t0: float, t1: float, columns: int, rows: int,
               y0: float, y1: float) -> Slice:
    frames, bins = img.data.shape
    per_bin = img.y_max / bins
    lo = max(float(y0), lowest_log_hz(img))
    hi = min(float(y1), img.y_max)
    empty = Slice(np.zeros((0, 0), np.uint8), t0, t1, lo, hi, True)
    if frames == 0 or img.duration <= 0 or not hi > lo:
        return empty
    per_frame = img.duration / frames
    f0 = max(0, int(np.floor(t0 / per_frame)))
    f1 = min(frames, int(np.ceil(t1 / per_frame)))
    if f1 <= f0:
        return empty
    edges = np.geomspace(lo, hi, max(1, int(rows)) + 1)
    starts = np.minimum(np.floor(edges[:-1] / per_bin).astype(np.int64), bins - 1)
    stop = max(int(starts[-1]) + 1, min(bins, int(np.ceil(hi / per_bin))))
    part = _pool(img.data[f0:f1, :stop], max(1, int(columns)), axis=0)
    # reduceat takes the largest from one start to the next, and repeats the
    # bin where two rows start on the same one
    part = np.maximum.reduceat(part, starts, axis=1)
    level_lo, level_hi = img.levels
    scaled = np.nan_to_num(np.clip((part - level_lo) / (level_hi - level_lo), 0.0, 1.0),
                           nan=0.0)
    pixels = np.ascontiguousarray(np.round(scaled * 255).astype(np.uint8).T[::-1])
    return Slice(pixels, f0 * per_frame, f1 * per_frame, lo, hi, True)


def _pool(data: np.ndarray, size: int, axis: int) -> np.ndarray:
    """At most *size* entries along *axis*, each the largest of the ones it
    stands for -- a short click stays visible however far out the view is."""
    n = data.shape[axis]
    if n <= size:
        return data
    edges = np.linspace(0, n, size + 1).astype(np.int64)[:-1]
    return np.maximum.reduceat(data, edges, axis=axis)


__all__ = ["MODES", "MAX_IMAGE_VALUES", "Peaks", "Settings", "Image", "Slice", "mono",
           "peaks", "image", "image_slice", "lowest_log_hz", "mel_to_hz", "hz_to_mel",
           "mel_bands_hz"]
