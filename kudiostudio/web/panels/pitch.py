# -*- coding: utf-8 -*-
"""The Pitch panel: ``pitch.*`` on the wire.

`kudio.f0` runs as a job; the contour goes over the spectrogram through
`Studio.set_overlay`, put back on the clip's timeline when only a selection
was tracked. Any edit drops it, because a curve left on screen describing
audio that has been replaced is worse than no curve.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

import kudio
from kudiostudio.tools import pitch
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


class PitchPanel(WebPanel):
    id = "pitch"
    title = "Pitch"
    icon = "pitch"
    script = "js/panels/pitch.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._track = None
        #: where in the clip the track was measured from
        self._offset = 0.0
        self._scope = ""
        #: what the result line says: (text, ok)
        self._result: Optional[tuple] = None
        self._show_overlay = True

    def info(self) -> Dict:
        return {"busy": self.busy, "scope": self._scope,
                "result": self._result[0] if self._result else "",
                "ok": bool(self._result and self._result[1]),
                "has_track": self._track is not None,
                "overlay": self._show_overlay}

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info(),
                "ranges": [{"label": label, "fmin": lo, "fmax": hi}
                           for label, lo, hi in pitch.RANGES],
                "frame_lengths": list(pitch.FRAME_LENGTHS),
                "fmin_range": list(pitch.FMIN_RANGE),
                "fmax_range": list(pitch.FMAX_RANGE),
                "hop_ms_range": list(pitch.HOP_MS_RANGE),
                "frame_tip": pitch.FRAME_TIP}

    @staticmethod
    def _bounds(fmin, fmax, frame_length, hop_ms):
        fmin = float(int(np.clip(round(float(fmin)), *pitch.FMIN_RANGE)))
        fmax = float(int(np.clip(round(float(fmax)), *pitch.FMAX_RANGE)))
        frame = int(frame_length)
        if frame not in pitch.FRAME_LENGTHS:
            raise ValueError(f"frame_length has to be one of {pitch.FRAME_LENGTHS}")
        hop_ms = int(np.clip(round(float(hop_ms)), *pitch.HOP_MS_RANGE))
        return fmin, fmax, frame, hop_ms

    @command
    def call_text(self, fmin, fmax, frame_length, hop_ms) -> str:
        fmin, fmax, frame, hop_ms = self._bounds(fmin, fmax, frame_length, hop_ms)
        return pitch.call_text(fmin, fmax, frame, pitch.hop_samples(hop_ms, self.session.sr))

    @command
    def track(self, fmin, fmax, frame_length, hop_ms) -> Dict:
        if not self.require_audio():
            return self.info()
        fmin, fmax, frame, hop_ms = self._bounds(fmin, fmax, frame_length, hop_ms)
        # `selected_audio` is the whole clip when nothing is selected, so the
        # *selection* says which of the two this is, and the offset with it
        selection = self.session.selection()
        offset = 0.0 if selection is None else selection[0]
        scope = pitch.scope(selection)
        y = np.asarray(self.session.selected_audio(), dtype=np.float32)
        self.report("Tracking pitch — pYIN runs over the whole clip at once.")
        started = self._start(y, offset, scope, fmin, fmax, frame,
                              pitch.hop_samples(hop_ms, self.session.sr))
        if not started:
            self.report("Another job is still running.")
        return self.info()

    def _start(self, y, offset, scope, fmin, fmax, frame, hop) -> bool:
        version = self.studio.version

        def done(track) -> None:
            if self.studio.version != version:
                return                  # the audio moved on while it ran
            self._offset, self._scope = offset, scope
            self._finished(track)

        return self.start_job("Tracking…", kudio.f0, y, self.session.sr, fmin=fmin,
                              fmax=fmax, frame_length=frame, hop_length=hop,
                              on_done=done, on_failed=self._failed)

    def _finished(self, track) -> None:
        if not track.voiced_ratio:
            self._track = None
            self._result = (pitch.NOTHING_VOICED, False)
            self.studio.clear_overlay()
            self.report("No voiced frames found.")
        else:
            self._track = track
            self._result = (pitch.summary(track), True)
            self._draw()
            self.report(f"Pitch: {track}")
        self.publish(info=self.info())

    def _failed(self, message: str) -> None:
        self._track = None
        self._result = (message, False)
        self.studio.clear_overlay()
        self.report(f"Pitch tracking failed — {message}")
        self.publish(info=self.info())

    def _draw(self) -> None:
        if self._track is not None and self._show_overlay:
            # times are relative to what was analysed; the view draws on the
            # clip's own timeline
            self.studio.set_overlay(self._track.times + self._offset, self._track.f0)
        else:
            self.studio.clear_overlay()

    @command
    def set_overlay(self, on: bool) -> Dict:
        self._show_overlay = bool(on)
        self._draw()
        return self.info()

    @command
    def add_markers(self) -> Dict:
        if self._track is None:
            return self.info()
        added = 0
        for start, end in pitch.voiced_spans(self._track, self._offset):
            self.session.add_marker(start, end, f"voiced {added + 1}")
            added += 1
        self.report(f"Added {added} marker(s) for the voiced spans.")
        return self.info()

    def on_session_changed(self) -> None:
        """The audio moved; the contour describes what was there before."""
        if self._track is not None or self._result is not None:
            self._track = None
            self._result = None
            self._scope = ""
            self.studio.clear_overlay()
            self.publish(info=self.info())


__all__ = ["PitchPanel"]
