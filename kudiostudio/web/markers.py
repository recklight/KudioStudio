# -*- coding: utf-8 -*-
"""The marker commands, ``markers.*`` on the wire.

They act on `AudioSession.markers`, which owns the state; the page only
draws it. Rows are addressed by their index in that list.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Dict, Optional

import kudio
from kudiostudio.session import Marker
from kudiostudio.web.commands import command

#: above this rate Find speech judges the frames on the speech band
SPEECH_BAND_ABOVE = 48000

#: Find events: 5 ms frames every 2.5 ms, short enough for a knock and long
#: enough to hold a few periods of anything above 600 Hz; two events closer
#: than 20 ms are one
EVENT_FRAME_S, EVENT_HOP_S, EVENT_JOIN_S = 0.005, 0.0025, 0.020


def find_events(y, sr: int, low: float, high: float, above_db: float):
    """The spans where the level in *low*..*high* Hz stands *above_db* over
    the median frame of the clip, in seconds. *high* 0 is Nyquist."""
    import numpy as np
    from kudio.effects.filters import bandpass, highpass, lowpass

    y = np.asarray(y, dtype=np.float64)
    nyquist = sr / 2.0
    high = nyquist if not high or high >= nyquist else float(high)
    low = max(0.0, float(low))
    if low >= high:
        raise ValueError(f"the band runs from low to high, got {low:g} to {high:g} Hz")
    if low > 0 and high < nyquist:
        y = bandpass(y, sr, low=low, high=high)
    elif low > 0:
        y = highpass(y, sr, cutoff=low)
    elif high < nyquist:
        y = lowpass(y, sr, cutoff=high)
    size = max(4, int(round(EVENT_FRAME_S * sr)))
    hop = max(1, int(round(EVENT_HOP_S * sr)))
    if len(y) < size:
        return []
    starts = np.arange(0, len(y) - size + 1, hop)
    running = np.concatenate(([0.0], np.cumsum(y ** 2)))
    power = (running[starts + size] - running[starts]) / size
    with np.errstate(divide="ignore"):
        level = 10.0 * np.log10(np.maximum(power, 1e-30))
    loud = level > np.median(level) + float(above_db)
    edges = np.diff(np.concatenate(([0], loud.view(np.int8), [0])))
    spans = [(starts[a] / sr, (starts[b - 1] + size) / sr)
             for a, b in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0])]
    joined = []
    for a, b in spans:
        if joined and a - joined[-1][1] < EVENT_JOIN_S:
            joined[-1] = (joined[-1][0], b)
        else:
            joined.append((a, b))
    return joined

log = logging.getLogger(__name__)


def format_span(start: float, end: float) -> str:
    """``0:12.500`` for a point, ``0:12.500 – 0:14.000`` for a range."""
    def one(seconds: float) -> str:
        minutes, rest = divmod(max(0.0, seconds), 60.0)
        return f"{int(minutes)}:{rest:06.3f}"
    return one(start) if end <= start else f"{one(start)} – {one(end)}"


class Markers:
    """Add, name, remove, find and file the session's markers."""

    def __init__(self, studio) -> None:
        self.studio = studio

    @property
    def session(self):
        return self.studio.session

    def _done(self) -> Dict:
        return self.studio.state()

    @command
    def add(self, at: Optional[float] = None) -> Dict:
        """A point marker at the playhead, or at *at* seconds."""
        if self.session.has_audio:
            at = self.studio.position(at)
            self.session.add_marker(at)
            self.studio.say(f"Marker at {format_span(at, at)}")
        return self._done()

    @command
    def mark_selection(self) -> Dict:
        marker = self.session.mark_selection()
        if marker is None:
            self.studio.say("Select a range first.")
        else:
            self.studio.say(f"Marked {format_span(marker.start, marker.end)}")
        return self._done()

    @command
    def find_speech(self) -> Dict:
        """Replace the list with what kudio.vad finds.

        Replaces rather than appends: run twice with a different threshold
        and a merged list would be two answers laid on top of each other.
        """
        s = self.session
        if not s.has_audio:
            return self._done()
        # above 48 kHz the floor over the speech band can outweigh a voice,
        # so the frames are judged on 100-4000 Hz where the kudio installed
        # can do that; below, the whole band, which keeps the fricatives
        band = {}
        if s.sr > SPEECH_BAND_ABOVE and "band" in inspect.signature(kudio.vad).parameters:
            band = {"band": (100.0, 4000.0)}
        try:
            spans = kudio.vad(s.y, s.sr, **band)
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"vad failed — {type(e).__name__}: {e}")
            return self._done()
        if not spans:
            self.studio.say("kudio.vad found no speech in this clip.")
            return self._done()
        s.set_markers([Marker(start=a, end=b, label=f"speech {n}")
                       for n, (a, b) in enumerate(spans, 1)])
        total = sum(b - a for a, b in spans)
        self.studio.say(
            f"kudio.vad — {len(spans)} span(s), {total:.2f}s of speech "
            f"({total / max(s.duration, 1e-9) * 100:.0f}%)"
            + (", judged on 100-4000 Hz" if band else ""))
        return self._done()

    @command
    def find_events(self, low: float = 0.0, high: float = 0.0, above_db: float = 12.0) -> Dict:
        """Replace the list with the spans where the level in *low*..*high*
        Hz stands *above_db* over the clip's median frame: a knock, a
        transient, a burst in one band. As Find speech does, it replaces
        rather than appends."""
        s = self.session
        if not s.has_audio:
            return self._done()
        try:
            spans = find_events(s.y, s.sr, low, high, above_db)
        except ValueError as e:
            self.studio.say(f"Not searched — {e}.")
            return self._done()
        if high:
            band = f"{low:g}–{high:g} Hz"
        else:
            band = f"{low:g} Hz and up" if low else "the whole band"
        if not spans:
            self.studio.say(f"No event stands {above_db:g} dB over the median in {band}.")
            return self._done()
        s.set_markers([Marker(start=a, end=b, label=f"event {n}")
                       for n, (a, b) in enumerate(spans, 1)])
        self.studio.say(f"{len(spans)} event(s) {above_db:g} dB over the median in {band}.")
        return self._done()

    @command
    def rename(self, index: int, label: str) -> Dict:
        self.session.rename_marker(int(index), str(label))
        return self._done()

    @command
    def remove(self, index: int) -> Dict:
        self.session.remove_marker(int(index))
        return self._done()

    @command
    def clear(self) -> Dict:
        self.session.clear_markers()
        self.studio.say("Markers cleared.")
        return self._done()

    @command
    def save(self) -> Dict:
        """Beside the audio, where opening the file will find them."""
        if self.session.path is None:
            self.studio.say("This audio has no file to sit beside — use Export… instead.")
            return self._done()
        try:
            self.session.save_markers()
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"Could not save markers — {type(e).__name__}: {e}")
        return self._done()

    @command
    def export(self, path: str) -> Dict:
        try:
            self.session.save_markers(path)
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"Export failed — {type(e).__name__}: {e}")
            return self._done()
        self.studio.remember_save_dir(path)
        return self._done()

    @command
    def load(self, path: str) -> Dict:
        try:
            found = self.session.load_markers(path)
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"Import failed — {type(e).__name__}: {e}")
            return self._done()
        self.studio.remember_dir(path)
        self.studio.say(f"Imported {found} marker(s) from {Path(path).name}")
        return self._done()


__all__ = ["Markers", "format_span"]
