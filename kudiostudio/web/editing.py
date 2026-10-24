# -*- coding: utf-8 -*-
"""The commands behind the window's Edit and File menus.

The edits are `AudioSession` calls, and their messages go to the status bar.
Anything that needs a path takes it as an argument: the page asks for it
first with `choose_path`, so a cancelled dialog never reaches here, and a
page without a window can type it.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Dict

from kudiostudio.web.commands import command

log = logging.getLogger(__name__)

#: longest silence the Insert silence box offers
MAX_SILENCE_S = 600.0
#: longest splice crossfade, as the transport's crossfade box allows
MAX_CROSSFADE_MS = 200.0


class Editing:
    """Cut, copy, paste and the rest, on the Studio's session."""

    def __init__(self, studio) -> None:
        self.studio = studio

    @property
    def session(self):
        return self.studio.session

    def _done(self) -> Dict:
        return self.studio.state()

    # ------------------------------------------------------------- clipboard

    @command
    def cut(self) -> Dict:
        if not self.session.cut():
            self.studio.say("Select a range to cut — Ctrl+A selects everything.")
        return self._done()

    @command
    def copy(self) -> Dict:
        if not self.session.copy():
            self.studio.say("Select a range to copy.")
        return self._done()

    @command
    def paste(self) -> Dict:
        """Paste at the playhead."""
        if not self.session.paste(self.studio.position()):
            self.studio.say("The clipboard is empty.")
        return self._done()

    @command
    def delete(self) -> Dict:
        if not self.session.delete():
            self.studio.say("Select a range to delete.")
        return self._done()

    @command
    def crop(self) -> Dict:
        if not self.session.crop_to_selection():
            self.studio.say("Select a range first — Ctrl+A selects everything.")
        return self._done()

    @command
    def insert_silence(self, seconds: float) -> Dict:
        if self.session.has_audio:
            seconds = min(max(float(seconds), 0.001), MAX_SILENCE_S)
            self.session.insert_silence(seconds, at=self.studio.position())
        return self._done()

    @command
    def set_crossfade(self, ms: float) -> Dict:
        applied = self.session.set_crossfade_ms(min(max(float(ms), 0.0),
                                                    MAX_CROSSFADE_MS))
        self.studio.say(f"Splices now crossfade over {applied:g} ms." if applied
                        else "Splices are now hard cuts — expect a click at every join.")
        return self._done()

    # ----------------------------------------------------------------- files

    @command
    def suggest_name(self, kind: str) -> str:
        """The file name the save dialog for *kind* starts with."""
        s = self.session
        stem = Path(s.name or "audio").stem
        if kind == "audio":
            return str(Path(s.name or "audio.wav").with_suffix(".wav"))
        if kind == "selection":
            sel = s.selection()
            if sel is None:
                return f"{stem}.wav"
            return f"{stem}_{sel[0]:.3f}-{sel[1]:.3f}.wav"
        if kind == "image":
            return f"{Path(s.name or 'analysis').stem}.png"
        if kind == "script":
            return f"{stem}_edits.py"
        if kind == "markers":
            return f"{Path(s.name or 'markers').stem}.txt"
        raise ValueError(f"no suggestion for {kind!r}")

    @command
    def save_as(self, path: str) -> Dict:
        if not self.session.has_audio:
            self.studio.say("Nothing to save.")
            return self._done()
        try:
            self.session.save(path)
        except Exception as e:  # noqa: BLE001 - reported to the person
            self.studio.say(f"Save failed — {type(e).__name__}: {e}")
            return self._done()
        self.studio.remember_dir(path)
        return self._done()

    @command
    def save_selection(self, path: str) -> Dict:
        if not self.session.can_copy():
            self.studio.say("Select a range first — Ctrl+A selects everything.")
            return self._done()
        try:
            self.session.save_selection(path)
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"Save failed — {type(e).__name__}: {e}")
            return self._done()
        self.studio.remember_dir(path)
        return self._done()

    @command
    def export_script(self, path: str) -> Dict:
        """Write the edit history as a Python script that redoes it.

        A step the script cannot reproduce is not left out quietly: the
        script stops at it, and the status line says so here, at export,
        rather than when somebody runs it in a batch job later.
        """
        s = self.session
        if not s.has_history():
            self.studio.say("No edits to export yet.")
            return self._done()
        path = Path(path)
        stem = Path(s.name or "audio").stem
        try:
            path.write_text(s.edit_script(f"{stem}_edited.wav"), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"Export failed — {type(e).__name__}: {e}")
            return self._done()
        self.studio.remember_dir(path)
        steps = len(s.history)
        warnings = s.edit_script_warnings()
        if warnings:
            more = (f" (and {len(warnings) - 1} more, listed at the top of the "
                    f"script)" if len(warnings) > 1 else "")
            self.studio.say(
                f"Exported {steps} edit(s) to {path.name}, but it will not "
                f"reproduce them as it stands — {warnings[0]}{more}")
        else:
            self.studio.say(f"Exported {steps} edit(s) to {path.name}")
        return self._done()

    @command
    def write_image(self, path: str, png_base64: str) -> Dict:
        """Write the plots as the page drew them, a PNG it hands over.

        What lands in the file is what is on screen, with the zoom and the
        selection that were being looked at.
        """
        path = Path(path)
        try:
            data = base64.b64decode(png_base64.split(",", 1)[-1], validate=True)
            if not data.startswith(b"\x89PNG"):
                raise ValueError("not a PNG")
            path.write_bytes(data)
        except Exception as e:  # noqa: BLE001
            log.warning("image not written: %s", e)
            self.studio.say(f"Could not write {path.name}")
            return self._done()
        self.studio.remember_dir(path)
        self.studio.say(f"Exported {path.name}")
        return self._done()


__all__ = ["Editing", "MAX_SILENCE_S", "MAX_CROSSFADE_MS"]
