# -*- coding: utf-8 -*-
"""Fundamental frequency -- `kudio.f0`.

The spectrogram already shows the harmonic stack; what it does not show is
*which* line is the fundamental, and it stops showing anything useful the
moment the fundamental is weak -- a phone band, a high-pass, or simply a low
voice under the first few bins. Two things come back, and the second makes
the first readable: a contour, and a voiced/unvoiced decision. An estimator
asked "what is the pitch here" always answers, silence and an `s` included,
so the contour is drawn with gaps where there is no pitch.

pYIN runs a hidden Markov model over the whole clip, roughly a second per
ten seconds of audio, which is why the panel runs it as a job on a button.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

#: Named search ranges, in Hz. The range is the setting people get wrong, and
#: getting it wrong does not fail -- a voice an octave below `fmin` is
#: reported as something plausible and untrue inside the range. Naming the
#: usual ones makes the mistake harder to make than to avoid.
RANGES = (
    ("Speech (65–400 Hz)", 65.0, 400.0),
    ("Low voice (60–250 Hz)", 60.0, 250.0),
    ("High voice (150–600 Hz)", 150.0, 600.0),
    ("Singing / wide (50–1000 Hz)", 50.0, 1000.0),
    ("Custom", None, None),
)
CUSTOM_RANGE = len(RANGES) - 1

FRAME_LENGTHS = (1024, 2048, 4096, 8192)

#: the bounds the frequency boxes allow, in Hz
FMIN_RANGE = (20, 2000)
FMAX_RANGE = (40, 8000)
#: the hop box, in milliseconds
HOP_MS_RANGE = (1, 50)

FRAME_TIP = ("pYIN compares a frame against itself one period later, so the\n"
             "frame has to hold two periods of the lowest pitch you ask for.\n"
             "kudio refuses the combination rather than returning an empty\n"
             "track, which would read as 'this recording has no pitch'.")


def hop_samples(hop_ms: float, sr: int) -> int:
    """The hop in samples, which is what kudio takes."""
    return max(1, int(round(hop_ms * (sr or 16000) / 1000.0)))


def call_text(fmin: float, fmax: float, frame_length: int, hop_length: int) -> str:
    return (f"kudio.f0(y, sr, fmin={fmin:g}, fmax={fmax:g}, "
            f"frame_length={frame_length}, hop_length={hop_length})")


def scope(selection: Optional[Tuple[float, float]]) -> str:
    """What was measured, in the words the panel shows."""
    if selection is None:
        return "the whole clip"
    return f"the selection, {selection[0]:.3f}–{selection[1]:.3f} s"


NOTHING_VOICED = ("Nothing voiced in this range. A voice below fmin is not found — "
                  "it is reported as something wrong above it, so an empty answer "
                  "is the honest one. Try a wider range.")


def summary(track) -> str:
    """Three lines on a track with voiced frames in it."""
    low, high = track.range_hz()
    return (f"median {track.median_hz:.1f} Hz\n"
            f"5–95%  {low:.1f}–{high:.1f} Hz ({track.semitone_range:.1f} semitones)\n"
            f"{track.voiced_ratio:.0%} of frames voiced, "
            f"{len(track.voiced_segments())} span(s)")


def voiced_spans(track, offset: float) -> List[Tuple[float, float]]:
    """The voiced runs on the clip's own timeline: a selection's track is
    put back where the selection sits."""
    return [(start + offset, end + offset) for start, end in track.voiced_segments()]


__all__ = ["CUSTOM_RANGE", "FMAX_RANGE", "FMIN_RANGE", "FRAME_LENGTHS", "FRAME_TIP",
           "HOP_MS_RANGE", "NOTHING_VOICED", "RANGES", "call_text", "hop_samples",
           "scope", "summary", "voiced_spans"]
