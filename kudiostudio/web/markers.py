# -*- coding: utf-8 -*-
"""The marker commands, ``markers.*`` on the wire.

They act on `AudioSession.markers`, which owns the state; the page only
draws it. Rows are addressed by their index in that list.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import kudio
from kudiostudio.session import Marker
from kudiostudio.web.commands import command

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
    def add(self) -> Dict:
        """A point marker at the playhead."""
        if self.session.has_audio:
            at = self.studio.position()
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
        try:
            spans = kudio.vad(s.y, s.sr)
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
            f"({total / max(s.duration, 1e-9) * 100:.0f}%)")
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
        self.studio.remember_dir(path)
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
