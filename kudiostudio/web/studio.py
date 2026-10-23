# -*- coding: utf-8 -*-
"""Everything the browser window can ask for, in one object.

`Studio` holds the session, the player and the analysis settings, and its
`command` methods are what the page calls, by name, through the local server.
Changes go back to the page as events: the session's signals and the
player's end are turned into small JSON messages on an `EventHub`, which the
server streams to every page that is listening.

All of it runs on the server's request threads, so one lock covers the
session and the player. The analysis image is computed outside it, from a
copy of the reference to the audio taken under it -- edits replace the array
rather than writing into it, so the copy cannot change underneath.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import weakref
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

import kudio
from kudiostudio import __version__, i18n
from kudiostudio.measure import TRUE_PEAK_WARN_DBTP, measure
from kudiostudio.session import AudioSession
from kudiostudio.web import analysis
from kudiostudio.web.player import Player

log = logging.getLogger(__name__)

#: what the open dialog lists; the same formats the Qt window offers
AUDIO_TYPES = ("Audio files (*.wav;*.flac;*.ogg;*.mp3;*.m4a;*.aiff;*.aif)",
               "All files (*.*)")

COMMANDS: Dict[str, Callable] = {}


def command(fn: Callable) -> Callable:
    """Mark *fn* as callable from the page. Nothing else is."""
    COMMANDS[fn.__name__] = fn
    return fn


class EventHub:
    """Fan-out of events to every listening page, one queue each."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: List[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def publish(self, kind: str, **data: Any) -> None:
        event = {"type": kind, **data}
        with self._lock:
            targets = list(self._queues)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                # a page that stopped reading; it will resync from `state`
                log.debug("event queue full, dropping %s", kind)


def config_dir() -> Path:
    """Where the window keeps its settings between runs."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
        or str(Path.home() / ".config")
    return Path(base) / "Kudio" / "KudioStudio"


class Prefs:
    """A small JSON file of settings that outlive a run."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (config_dir() / "window.json")
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2,
                                            ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            log.warning("settings not saved: %s", e)


class Studio:
    """The browser window's back end: one session, one player."""

    def __init__(self, prefs: Optional[Prefs] = None,
                 language: Optional[str] = None):
        self.prefs = prefs if prefs is not None else Prefs()
        self.language = i18n.normalize(language or self.prefs.get("language")
                                       or system_language())
        # this process has one window, so `tr` follows its language and
        # `translate` never has to swap tables
        i18n.load(self.language)
        self.events = EventHub()
        self.lock = threading.RLock()
        self.session = AudioSession()
        self.player = Player(on_finished=self._on_finished,
                             on_failed=self._on_play_failed)
        saved = self.prefs.get("analysis") or {}
        try:
            self.settings = analysis.Settings(**saved).checked()
        except (TypeError, ValueError):
            self.settings = analysis.Settings()
        self.files: List[str] = []
        #: bumped whenever the plots are to show another array, so the page
        #: can tell a reply about the audio it has from one about the audio
        #: before an edit. See `_sync_version`.
        self.version = 0
        self._shown: Optional[weakref.ref] = None
        self.playhead = 0.0
        self.loop = False
        self.selection_only = True
        self.gain_db = 0.0
        #: start and end of what was last played, for loop and for the
        #: playhead going back after playback runs out
        self.segment: Tuple[float, float] = (0.0, 0.0)
        #: open and save dialogs, when there is a window to show them
        self.dialogs = None
        self._image_lock = threading.Lock()
        self._image_key = None
        self._image: Optional[analysis.Image] = None
        self._levels: Dict[int, Dict] = {}

        s = self.session
        s.changed.connect(self._on_changed)
        s.selection_changed.connect(lambda *_: self._push_state())
        s.compare_changed.connect(self._on_compare)
        s.markers_changed.connect(self._push_state)
        s.clipboard_changed.connect(lambda _: self._push_state())
        s.message.connect(self.say)

    # ------------------------------------------------------------- dispatch

    def call(self, name: str, args=(), kwargs=None):
        """Run the command *name* for the page; raises KeyError if there is
        no such command."""
        fn = COMMANDS[name]
        with self.lock:
            return fn(self, *(args or ()), **(kwargs or {}))

    # ---------------------------------------------------------------- events

    def say(self, text: str) -> None:
        """A line for the status bar."""
        log.info("%s", text)
        self.events.publish("message", text=i18n.translate(text, self.language))

    def _push_state(self) -> None:
        self.events.publish("state", state=self.state())

    def _sync_version(self) -> None:
        """Bump the version if the plots now show a different array.

        Counting the session's `changed` signals is not enough: loading a
        file clears the selection, and announces that, before it announces
        the new audio, so a state pushed in between would carry the new
        audio under the old number. Edits, undo and A/B all swap the array
        rather than writing into it, so its identity is what changed. A weak
        reference, so the old array is not kept alive for this.
        """
        y, _ = self.session.preview() if self.session.has_audio else (None, 0)
        before = self._shown() if self._shown is not None else None
        if y is not before:
            self._shown = weakref.ref(y) if y is not None else None
            self.version += 1

    def _on_changed(self) -> None:
        self.player.stop()
        self.playhead = 0.0
        self._push_state()

    def _on_compare(self, on: bool) -> None:
        if self.player.playing:
            self._play_range(self.player.position, self.segment[1])
        self._push_state()

    def _on_finished(self, end: float) -> None:
        with self.lock:
            self.playhead = self.segment[0]
            again = self.loop and self.session.has_audio
            if again:
                self._play_range(*self.segment)
            else:
                self.events.publish("playback", playing=False,
                                    position=self.playhead)

    def _on_play_failed(self, message: str) -> None:
        self.events.publish("playback", playing=False, position=self.playhead)
        self.say(f"Playback failed — {message}")

    # ----------------------------------------------------------------- state

    @command
    def state(self) -> Dict:
        """Everything the page shows that is not the plots' data."""
        self._sync_version()
        s = self.session
        selection = s.selection()
        return {
            "version": self.version,
            "has_audio": s.has_audio,
            "name": s.name,
            "path": str(s.path) if s.path else "",
            "sr": s.sr,
            "duration": s.duration,
            "samples": len(s.y) if s.has_audio else 0,
            "selection": list(selection) if selection else None,
            "can_undo": s.can_undo(),
            "can_compare": s.can_compare(),
            "comparing": s.comparing,
            "match_loudness": s.loudness_matched,
            "files": [{"path": p, "name": Path(p).name} for p in self.files],
            "markers": [{"start": m.start, "end": m.end, "label": m.label}
                        for m in s.markers],
            "playing": self.player.playing,
            "playhead": self.playhead,
            "loop": self.loop,
            "selection_only": self.selection_only,
            "gain_db": self.gain_db,
            "analysis": dict(self.settings.__dict__),
            "call": self.settings.call(),
            "language": self.language,
            "version_info": {"kudiostudio": __version__, "kudio": kudio.__version__},
        }

    # ----------------------------------------------------------------- files

    @command
    def open_dialog(self) -> Dict:
        """Ask for files in the window's own dialog; None when there is no
        window, so the page asks for a path itself."""
        if self.dialogs is None:
            return {"opened": False, "needs_path": True}
        # the dialog waits for the person, not for this lock
        self.lock.release()
        try:
            paths = self.dialogs.open_files(self.prefs.get("last_dir", ""),
                                            AUDIO_TYPES)
        finally:
            self.lock.acquire()
        if not paths:
            return {"opened": False}
        self.open_files(list(paths))
        return {"opened": True}

    @command
    def open_files(self, paths: List[str]) -> Dict:
        """Add *paths* to the file list and open the first."""
        paths = [str(p) for p in paths if str(p).strip()]
        if not paths:
            return self.state()
        for p in paths:
            resolved = str(Path(p).resolve())
            if resolved not in self.files:
                self.files.append(resolved)
        self.load(str(Path(paths[0]).resolve()))
        return self.state()

    @command
    def load(self, path: str) -> Dict:
        try:
            self.session.load_file(path)
        except Exception as e:  # noqa: BLE001 - reported to the person
            self.say(f"Could not open {Path(path).name} — {type(e).__name__}: {e}")
            return self.state()
        self.prefs.set("last_dir", str(Path(path).parent))
        return self.state()

    @command
    def remove_file(self, path: str) -> Dict:
        if path in self.files:
            self.files.remove(path)
            self._push_state()
        return self.state()

    # ------------------------------------------------------------- selection

    @command
    def set_selection(self, start: float, end: float) -> Dict:
        if self.session.has_audio:
            if end - start < 1e-4:
                self.session.clear_selection()
            else:
                self.session.set_selection(float(start), float(end))
        return self.state()

    @command
    def clear_selection(self) -> Dict:
        self.session.clear_selection()
        return self.state()

    @command
    def select_all(self) -> Dict:
        if self.session.has_audio:
            self.session.set_selection(0.0, self.session.duration)
        return self.state()

    # -------------------------------------------------------------- playback

    @command
    def play(self, selection_only: Optional[bool] = None) -> Dict:
        """Play the selection if there is one, otherwise from the playhead."""
        if selection_only is not None:
            self.selection_only = bool(selection_only)
        if not self.session.has_audio:
            return self._playback_state()
        sel = self.session.selection() if self.selection_only else None
        if sel is not None:
            self._play_range(*sel)
        else:
            y, sr = self.session.preview()
            total = len(y) / sr
            start = self.playhead if self.playhead < total - 1e-3 else 0.0
            self._play_range(start, total)
        return self._playback_state()

    def _play_range(self, start: float, end: float) -> None:
        y, sr = self.session.preview()
        if y is None or sr <= 0 or len(y) == 0:
            return
        end = min(end, len(y) / sr)
        start = max(0.0, min(start, end))
        if end - start < 1e-3:
            return
        self.segment = (start, end)
        self.playhead = start
        playback = self.player.play(y[int(start * sr):int(end * sr)], sr,
                                    self.gain_db, t0=start)
        if playback is not None:
            self.events.publish("playback", playing=True, start=playback.start,
                                end=playback.end)

    @command
    def stop(self) -> Dict:
        if self.player.playing:
            self.playhead = self.player.stop()
            self.events.publish("playback", playing=False, position=self.playhead)
        return self._playback_state()

    @command
    def toggle_play(self) -> Dict:
        return self.stop() if self.player.playing else self.play()

    @command
    def seek(self, seconds: float) -> Dict:
        """Move the playhead; keep playing from there if it already was."""
        if not self.session.has_audio:
            return self._playback_state()
        self.playhead = max(0.0, min(float(seconds), self.session.duration))
        if self.player.playing:
            start, end = self.segment
            self._play_range(self.playhead, end if start <= self.playhead < end
                             else self.session.duration)
        else:
            self.player.seek(self.playhead)
            self.events.publish("playback", playing=False, position=self.playhead)
        return self._playback_state()

    @command
    def set_transport(self, loop: Optional[bool] = None,
                      selection_only: Optional[bool] = None,
                      gain_db: Optional[float] = None) -> Dict:
        if loop is not None:
            self.loop = bool(loop)
        if selection_only is not None:
            self.selection_only = bool(selection_only)
        if gain_db is not None:
            self.gain_db = float(np.clip(gain_db, -24.0, 12.0))
        return self.state()

    def _playback_state(self) -> Dict:
        return {"playing": self.player.playing, "position": self.player.position
                if self.player.playing else self.playhead,
                "segment": list(self.segment)}

    # ------------------------------------------------------------- the edit

    @command
    def undo(self) -> Dict:
        if not self.session.undo():
            self.say("Nothing to undo.")
        return self.state()

    @command
    def revert(self) -> Dict:
        self.session.revert()
        return self.state()

    @command
    def set_compare(self, on: bool) -> Dict:
        self.session.set_compare(bool(on))
        return self.state()

    @command
    def set_match_loudness(self, on: bool) -> Dict:
        self.session.set_loudness_match(bool(on))
        return self.state()

    # -------------------------------------------------------------- analysis

    @command
    def set_analysis(self, **changes) -> Dict:
        """Change the view and its parameters, e.g. ``mode="mel"``."""
        values = dict(self.settings.__dict__)
        values.update({k: (v if k == "mode" else int(v))
                       for k, v in changes.items() if k in values})
        self.settings = analysis.Settings(**values).checked()
        self.prefs.set("analysis", dict(self.settings.__dict__))
        return self.state()

    def audio(self) -> Tuple[Optional[np.ndarray], int, int]:
        """What the plots show -- the edit, or the original under A/B -- with
        the version it belongs to."""
        with self.lock:
            self._sync_version()
            if not self.session.has_audio:
                return None, 0, self.version
            y, sr = self.session.preview()
            return y, sr, self.version

    def peaks(self, t0: float, t1: float, columns: int):
        y, sr, version = self.audio()
        if y is None:
            return None, version
        return analysis.peaks(y, sr, t0, t1, columns), version

    def image(self) -> Tuple[Optional[analysis.Image], int]:
        y, sr, version = self.audio()
        if y is None:
            return None, version
        settings = self.settings
        key = (version, settings)
        with self._image_lock:
            if self._image_key != key:
                self._image = analysis.image(y, sr, settings)
                self._image_key = key
            return self._image, version

    @command
    def levels(self) -> Dict:
        """The Levels readout for what the plots show, measured once per
        version of the audio."""
        self._sync_version()
        version = self.version
        if version in self._levels:
            return self._levels[version]
        y, sr, _ = self.audio()
        if y is None:
            return {"version": version, "empty": True}
        self.lock.release()            # a long file takes half a second
        try:
            measured = measure(analysis.mono(y), sr)
        finally:
            self.lock.acquire()
        report = measured.report
        problems = list(report.problems())
        over = measured.true_peak_db >= TRUE_PEAK_WARN_DBTP
        if over and not any("clip" in p for p in problems):
            problems.append(
                f"true peak {measured.true_peak_db:+.1f} dBTP — the waveform "
                f"between the samples is at or above the usual -1 dBTP "
                f"ceiling, so this can clip on conversion")
        result = {
            "version": version,
            "peak_dbfs": _finite(report.peak_dbfs),
            "clipped": bool(report.clipped_samples > 0 or report.peak_dbfs > -0.5),
            "true_peak_db": _finite(measured.true_peak_db),
            "true_peak_over": bool(over),
            "lufs": _finite(report.lufs),
            "range_lu": _finite(measured.range_lu),
            "bandwidth_hz": _finite(report.bandwidth_hz),
            "band_limited": bool(report.band_limited),
            "problems": [i18n.translate(p, self.language) for p in problems],
        }
        self._levels = {version: result}
        return result

    # ---------------------------------------------------------------- locale

    @command
    def translations(self) -> Dict:
        return {"language": self.language,
                "languages": dict(i18n.LANGUAGES),
                "messages": i18n.table(self.language)}

    @command
    def set_language(self, code: str) -> Dict:
        """Takes effect at once: the page asks for its table again."""
        self.language = i18n.normalize(code)
        i18n.load(self.language)
        self.prefs.set("language", self.language)
        self._levels = {}              # its problem lines are in the old language
        return self.translations()

    def shutdown(self) -> None:
        self.player.shutdown()


def _finite(value) -> Optional[float]:
    value = float(value)
    return value if np.isfinite(value) else None


def system_language() -> str:
    """The operating system's interface language, when there is a
    translation for it, without asking Qt."""
    if os.name == "nt":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
                return i18n.normalize(buffer.value)
        except (AttributeError, OSError):
            pass
    import locale
    for name in (os.environ.get("LANG"), (locale.getlocale()[0] or "")):
        if name:
            return i18n.normalize(name.split(".")[0])
    return i18n.DEFAULT


__all__ = ["Studio", "EventHub", "Prefs", "COMMANDS", "command", "config_dir",
           "system_language"]
