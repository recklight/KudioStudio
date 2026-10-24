# -*- coding: utf-8 -*-
"""The Effects panel: ``effects.*`` on the wire.

The page holds the controls' values and sends them with every call; this
side holds what outlives a call -- the measured room that was read, the
segments of the last split -- and turns values into kudio calls through
`kudiostudio.tools.effects`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

import kudio
from kudiostudio.tools import effects
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel

log = logging.getLogger(__name__)


class EffectsPanel(WebPanel):
    id = "effects"
    title = "Effects"
    icon = "effects"
    script = "js/panels/effects.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        #: the measured room, read at the clip's rate (see `load_ir`)
        self._response: Optional[effects.Response] = None
        #: what the last split_on_silence produced, so it can be written out
        self._segments: List[np.ndarray] = []

    # ----------------------------------------------------------------- info

    def info(self, align: bool = True) -> Dict:
        """What the page shows beside the controls."""
        out = {"busy": self.busy, "segments": len(self._segments),
               "segment_seconds": (sum(len(s) for s in self._segments)
                                   / self.session.sr if self._segments else 0.0),
               "ir": None}
        if self._response is not None:
            text, doubtful = self._response.describe(align)
            out["ir"] = {"path": str(self._response.path),
                         "name": self._response.path.name,
                         "measured": text, "doubtful": doubtful}
        return out

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **effects.schema(), **self.info()}

    @command
    def call_text(self, name: str, values: Dict) -> str:
        """The call the controls describe, as the panel shows it."""
        return self._operation(name, values)[0]

    def _operation(self, name: str, values: Dict, exact: bool = False):
        noise = values.get("noise") or None
        return effects.operation(name, values, exact=exact,
                                 noise=Path(noise) if noise else None,
                                 response=self._response)

    def _render(self, name: str, values: Dict, exact: bool = False):
        """Run the effect over the selection, or the whole clip. Raises on
        failure. The one place it is computed, so Preview and Apply cannot
        drift apart: what is heard is what Apply commits."""
        call_text, fn = self._operation(name, values, exact)
        y = np.asarray(self.session.selected_audio(), dtype=np.float32)
        return call_text, fn(y, self.session.sr)

    # --------------------------------------------------------- measured room

    @command
    def load_ir(self, path: str, align: bool = True) -> Dict:
        """Read a measured impulse response, the way the recorded call reads
        it: at the clip's rate, in mono. With no clip open it keeps the
        file's own rate until one arrives. A file that cannot be used leaves
        the one already loaded in place."""
        rate = self.session.sr if self.session.has_audio else None
        response, why = effects.read_response(path, rate)
        if response is None:
            self.report(why)
        else:
            self._response = response
            self.studio.remember_dir(path)
        return self.info(align)

    @command
    def measure_ir(self, align: bool = True) -> Dict:
        return self.info(align)

    # ---------------------------------------------------------------- apply

    @command
    def apply(self, name: str, values: Dict) -> Dict:
        if not self.require_audio():
            return self.info()
        try:
            call_text, out = self._render(name, values, exact=True)
        except Exception as e:  # noqa: BLE001 - said, not raised
            self.report(f"{name} failed — {type(e).__name__}: {e}")
            return self.info()
        if name == "split_on_silence":
            self._segments = list(out)
            total = sum(len(seg) for seg in out) / self.session.sr
            self.report(f"split_on_silence → {len(out)} segment(s), {total:.2f}s of "
                        f"non-silent audio (nothing modified) — 'Save segments…' "
                        f"writes them out")
            return self.info()
        self.session.replace_selection(out, undo_label=name, code=f"y = {call_text}")
        self.report(f"Applied {call_text}")
        return self.info()

    @command
    def save_segments(self, folder: str) -> Dict:
        """Write the last split's segments as numbered files in *folder*."""
        if not self._segments:
            self.report("Run split_on_silence first.")
            return self.info()
        stem = Path(self.session.name or "audio").stem
        written = 0
        try:
            for n, segment in enumerate(self._segments, 1):
                kudio.save_wave(Path(folder) / f"{stem}_{n:03d}.wav",
                                segment, self.session.sr)
                written += 1
        except Exception as e:  # noqa: BLE001
            self.report(f"Saved {written} of {len(self._segments)} — "
                        f"{type(e).__name__}: {e}")
            return self.info()
        self.report(f"Wrote {written} segment(s) to {folder}")
        return self.info()

    # -------------------------------------------------------------- preview

    @command
    def preview(self, name: str, values: Dict) -> Dict:
        """Audition the effect on the selection; loops, changes nothing.
        Called again with new values, it renders again."""
        if not self.session.has_audio:
            self.report("Load an audio file first.")
            return {"on": False}
        if name in effects.NOT_PREVIEWABLE:
            self.report(f"{name} does not change the audio, so there is nothing "
                        f"to preview.")
            return {"on": False}
        try:
            _, out = self._render(name, values)
        except Exception as e:  # noqa: BLE001
            # a half-typed parameter should not spam the status bar, but a
            # preview that silently stops is worse
            self.report(f"Preview failed — {type(e).__name__}: {e}")
            self.studio.stop_preview()
            return {"on": False}
        return {"on": self.studio.start_preview(self.id, out, self.session.sr)}

    # ---------------------------------------------------------------- hooks

    def on_session_changed(self) -> None:
        # The response is held at the clip's rate. A clip at another rate --
        # or the first clip, after one picked with nothing open -- needs it
        # read again from the file: resampling the held copy would be a
        # second resampling, and not the read the recorded call makes.
        r = self._response
        if r is not None and self.session.has_audio and self.session.sr != r.sr:
            again, why = effects.read_response(r.path, self.session.sr)
            if again is None:
                self.report(why)
            self._response = again
        # cut out of audio that is no longer current; saving them under the
        # new clip's name would file them under the wrong take
        self._segments = []
        self.publish(info=self.info())


__all__ = ["EffectsPanel"]
