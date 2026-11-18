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
from typing import Dict, Optional

from kudiostudio.web.commands import command
from kudiostudio.web.options import subtype_for

log = logging.getLogger(__name__)

#: longest silence the Insert silence box offers
MAX_SILENCE_S = 600.0
#: longest splice crossfade, as the transport's crossfade box allows
MAX_CROSSFADE_MS = 200.0

#: what Save As writes, by the name's suffix: the samples are PCM or float
#: at the bit depth in Settings, which OGG Vorbis and MP3 cannot hold
SAVE_SUFFIXES = (".wav", ".flac", ".aiff", ".aif")


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
    def paste(self, at: Optional[float] = None) -> Dict:
        """Paste over the selection, or at the playhead; given *at* seconds,
        as the plots' "Paste here" gives it, at that point whatever is
        selected."""
        if at is not None:
            self.session.clear_selection()
        if not self.session.paste(self.studio.position(at)):
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
    def insert_silence(self, seconds: float, at: Optional[float] = None) -> Dict:
        """Silence over the selection, or at the playhead; at *at* seconds,
        whatever is selected, when that is given."""
        if at is not None:
            self.session.clear_selection()
        if self.session.has_audio:
            seconds = min(max(float(seconds), 0.001), MAX_SILENCE_S)
            self.session.insert_silence(seconds, at=self.studio.position(at))
        return self._done()

    @command
    def set_crossfade(self, ms: float) -> Dict:
        applied = self.session.set_crossfade_ms(min(max(float(ms), 0.0),
                                                    MAX_CROSSFADE_MS))
        # kept, so the next start splices the same way
        self.studio.options.put("crossfade_ms", applied)
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

    def _save_target(self, path: str, replace: bool):
        """``(path, None)`` to write to, or ``(None, answer)`` when it is not
        written yet: a name without a suffix gets .wav, one in a format Save
        As does not write is refused in words rather than in libsndfile's,
        and a file already there is written over only when the page has
        asked. The window's own Save dialog asks that itself; the path the
        page asks for in a browser does not."""
        target = Path(str(path).strip())
        if not target.suffix:
            # "take." would be "take..wav"; Windows drops a name's last dots
            target = target.with_name(target.name.rstrip(".") + ".wav")
        if target.suffix.lower() not in SAVE_SUFFIXES:
            self.studio.say(f"Saved as WAV, FLAC or AIFF only — {target.name} "
                            f"asks for {target.suffix}.")
            return None, self._done()
        if self.studio.dialogs is None and not replace and target.exists():
            source = (self.session.path is not None
                      and target.resolve() == Path(self.session.path).resolve())
            return None, {**self._done(),
                          "confirm_replace": {"path": str(target), "source": source}}
        return target, None

    @command
    def save_as(self, path: str, replace: bool = False) -> Dict:
        if not self.session.has_audio:
            self.studio.say("Nothing to save.")
            return self._done()
        target, answer = self._save_target(path, replace)
        if target is None:
            return answer
        target = target.resolve()
        subtype, note = subtype_for(target, self.studio.options["bit_depth"])
        try:
            self.session.save(target, subtype=subtype)
        except Exception as e:  # noqa: BLE001 - reported to the person
            self.studio.say(f"Save failed — {type(e).__name__}: {e}")
            return self._done()
        if note:
            self.studio.say(note)
        self.studio.remember_file(str(target))
        # the clip goes by the new file now, so the Files list has it, marked
        # as the open one, as it would be had the file been opened
        if str(target) not in self.studio.files:
            self.studio.files.append(str(target))
        self.studio.remember_dir(str(target))
        return self._done()

    @command
    def save_selection(self, path: str, replace: bool = False) -> Dict:
        if not self.session.can_copy():
            self.studio.say("Select a range first — Ctrl+A selects everything.")
            return self._done()
        target, answer = self._save_target(path, replace)
        if target is None:
            return answer
        subtype, note = subtype_for(target, self.studio.options["bit_depth"])
        try:
            self.session.save_selection(target, subtype=subtype)
        except Exception as e:  # noqa: BLE001
            self.studio.say(f"Save failed — {type(e).__name__}: {e}")
            return self._done()
        if note:
            self.studio.say(note)
        self.studio.remember_dir(str(target))
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
            path.write_text(s.edit_script(f"{stem}_edited.wav",
                                          subtype=self.studio.options["bit_depth"]),
                            encoding="utf-8")
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
