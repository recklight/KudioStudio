# -*- coding: utf-8 -*-
"""Level, loudness and "is this recording usable" -- `kudio.audio_report`.

The measuring half of the Levels readout, apart from any window;
`Studio.levels` turns it into what the page shows.
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

import kudio

log = logging.getLogger(__name__)

#: Above this, a converter or a lossy encoder is likely to clip on the
#: waveform between the samples even though no sample is at full scale. -1 dBTP
#: is the usual delivery ceiling; the warning fires at the ceiling itself.
TRUE_PEAK_WARN_DBTP = -1.0


class Measurement(NamedTuple):
    """One pass over the audio, answering everything the readout shows.

    All four come from one call because they walk the same signal: running
    them separately would filter it four times and, worse, let the Levels
    box show numbers measured from four different moments of an edit.
    """

    report: object                       # kudio.AudioReport
    true_peak_db: float
    curve: Optional[object]              # kudio.LoudnessCurve, or None
    range_lu: float


def measure(y, sr) -> Measurement:
    """Everything the Levels box reports. A long file takes half a second,
    so `Studio.levels` calls this with its lock released."""
    report = kudio.audio_report(y, sr)
    peak = kudio.true_peak(y, sr)
    try:
        curve = kudio.loudness_over_time(y, sr)
    except Exception as e:               # clip shorter than the 3 s window
        log.debug("no loudness curve: %s", e)
        return Measurement(report, peak, None, float("nan"))
    return Measurement(report, peak, curve, curve.range_lu)


__all__ = ["Measurement", "measure", "TRUE_PEAK_WARN_DBTP"]
