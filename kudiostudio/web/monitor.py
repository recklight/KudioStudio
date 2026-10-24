# -*- coding: utf-8 -*-
"""The plots while a take is running: the last few seconds, rolling.

Audio arrives about twenty times a second. Every sample goes through a
`kudio.SpectrogramStream` as it lands, so each column is computed exactly
once; only the picture is sent less often, as a ``monitor`` event the page
draws in place of the clip. The time axis then reads 0 to the window's
length, right-aligned on "now", because while recording there is no timeline
yet. When the take ends, the last picture stays until the recorded audio
replaces it -- blanking it would hide the end of what was just recorded.
"""
from __future__ import annotations

import base64
import logging

import numpy as np

import kudio
from kudiostudio.web import analysis

log = logging.getLogger(__name__)

#: Seconds of audio kept on screen while a take is running.
MONITOR_SECONDS = 3.0
#: Pictures sent: one every this many arrivals. Skipping a *push* would lose
#: audio; skipping a picture loses nothing anyone can see.
MONITOR_REDRAW_EVERY = 4
#: Colour range for the rolling spectrogram, in dBFS per bin, held fixed. The
#: offline view picks its range from the image because that is all it has; a
#: live window has an absolute one, so colour means a level rather than
#: "bright compared to the last three seconds", and two takes are comparable.
#: The top is well below 0: a broadband sound at -20 dBFS spreads its energy
#: over hundreds of bins and no single one gets near full scale.
MONITOR_LEVELS = (-75.0, -15.0)
#: min/max pairs across the rolling waveform
WAVE_COLUMNS = 800


class LiveMonitor:
    """The rolling waveform and spectrogram of one take."""

    def __init__(self, studio) -> None:
        self.studio = studio
        self.sr = 0
        self.stream = None
        self.wave = np.zeros(0, dtype=np.float32)
        self.ticks = 0

    @property
    def running(self) -> bool:
        return self.sr > 0

    def start(self, sr: int) -> None:
        """A take has begun at *sr*: build the rolling views to the window's
        analysis settings. MFCC has no live form worth watching -- it is a
        compressed feature for a model, not a picture of a room -- so that
        view falls back to the STFT."""
        self.sr = int(sr)
        self.wave = np.zeros(0, dtype=np.float32)
        self.ticks = 0
        s = self.studio.settings
        mel = s.n_mels if s.mode == "mel" else 0
        try:
            self.stream = kudio.SpectrogramStream(self.sr, n_fft=s.n_fft,
                                                  hop_length=s.hop_length,
                                                  seconds=MONITOR_SECONDS, n_mels=mel)
        except Exception as e:  # noqa: BLE001
            log.warning("live spectrogram unavailable: %s", e)
            self.stream = None
        self.studio.clear_overlay()
        self.studio.events.publish("monitor", on=True, sr=self.sr,
                                   span=self._span(), call=self.call())

    def call(self) -> str:
        s = self.studio.settings
        mels = f", n_mels={s.n_mels}" if s.mode == "mel" else ""
        note = "  # MFCC has no live form; showing the STFT" if s.mode == "mfcc" else ""
        return (f"kudio.SpectrogramStream(sr, n_fft={s.n_fft}, hop_length={s.hop_length}, "
                f"seconds={MONITOR_SECONDS:g}{mels}).push(chunk){note}")

    def _span(self) -> float:
        return self.stream.window_seconds if self.stream is not None else MONITOR_SECONDS

    def push(self, y: np.ndarray, sr: int) -> None:
        """Audio that has just arrived, and that no view has seen before.

        Only between `start` and `stop`. A block that lands after Stop is the
        recorder's last tick, and starting the view again for it would leave
        the window showing a take nobody is recording.
        """
        if not self.running:
            return
        if sr != self.sr:
            self.start(sr)       # the device changed rate mid-take
        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1:
            # mix down rather than reshape: flattening (n, channels) would
            # interleave them, and the stream would analyse a signal nobody
            # recorded
            y = y.mean(axis=1)
        if not y.size:
            return
        keep = max(1, int(MONITOR_SECONDS * self.sr))
        self.wave = np.concatenate([self.wave, y])[-keep:]
        if self.stream is not None:
            self.stream.push(y)
        self.ticks += 1
        if self.ticks % MONITOR_REDRAW_EVERY == 0:
            self.studio.events.publish("monitor", on=True, **self.snapshot())

    def snapshot(self) -> dict:
        """The picture now, as the page draws it: both plots right-aligned on
        "now", so while the window is still filling the waveform starts
        partway across rather than at zero -- drawn from zero it would put
        one instant in two places on two plots meant to line up."""
        span = self._span()
        held = len(self.wave) / self.sr if self.sr else 0.0
        x0 = max(0.0, span - held)
        out: dict = {"span": span, "wave": None, "image": None}
        if held > 0:
            p = analysis.peaks(self.wave, self.sr, 0.0, held, WAVE_COLUMNS)
            out["wave"] = {"kind": p.kind, "start": x0 + p.start, "step": p.step,
                           "values": [round(float(v), 4) for v in np.ravel(p.values)]}
        stream = self.stream
        if stream is not None:
            columns = stream.columns()               # (frames, bins), newest right
            lo, hi = MONITOR_LEVELS
            scaled = np.clip((columns - lo) / (hi - lo), 0.0, 1.0)
            pixels = np.ascontiguousarray(np.round(scaled * 255).astype(np.uint8).T[::-1])
            if stream.n_mels:
                y_max, label, units = float(stream.n_bins), "Mel band", ""
            else:
                y_max, label, units = self.sr / 2.0, "Frequency", "Hz"
            out["image"] = {"rows": int(pixels.shape[0]), "columns": int(pixels.shape[1]),
                            "data": base64.b64encode(pixels.tobytes()).decode("ascii"),
                            "y_max": y_max, "label": label, "units": units}
        return out

    def stop(self) -> None:
        """The take is over; the last picture stays on screen."""
        if not self.running:
            return
        self.sr = 0
        self.stream = None
        self.wave = np.zeros(0, dtype=np.float32)
        self.studio.events.publish("monitor", on=False)


__all__ = ["LiveMonitor", "MONITOR_LEVELS", "MONITOR_REDRAW_EVERY", "MONITOR_SECONDS"]
