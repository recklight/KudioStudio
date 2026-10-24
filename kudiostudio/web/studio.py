# -*- coding: utf-8 -*-
"""Everything the page can ask for, in one object.

`Studio` holds the session, the player and the analysis settings, and its
`command` methods are what the page calls, by name, through the local server.
So are those of its parts -- the edit commands, the markers -- and of every
tool panel, under the panel's id. Changes go back to the page as events: the
session's signals and the player's end are turned into small JSON messages on
an `EventHub`, which the server streams to every page that is listening.

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
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

import kudio
from kudiostudio import __version__, i18n, plugins
from kudiostudio.i18n import system_language
from kudiostudio.measure import TRUE_PEAK_WARN_DBTP, measure
from kudiostudio.session import AudioSession
from kudiostudio.tools.manifest import is_manifest
from kudiostudio.web import analysis
from kudiostudio.web.commands import command, commands_of
from kudiostudio.web.editing import Editing
from kudiostudio.web.jobs import Jobs
from kudiostudio.web.markers import Markers
from kudiostudio.web.monitor import LiveMonitor
from kudiostudio.web.panels import PANEL_CLASSES, rail
from kudiostudio.web.player import Player

log = logging.getLogger(__name__)

#: A duration change below this is the same clip. Frame-based processing does
#: not return exactly what it was given -- kudio's `trad_enhance` comes back
#: 128 samples (8 ms) short -- and treating that as a new clip would reset the
#: colour scale after every denoise, which is when it is wanted kept. The page
#: holds its zoom and amplitude axis by the same rule (main.js, SAME_CLIP_S).
SAME_CLIP_S = 0.05

#: what the open dialog lists
AUDIO_TYPES = ("Audio files (*.wav;*.flac;*.ogg;*.mp3;*.m4a;*.aiff;*.aif)",
               "All files (*.*)")

#: the dialogs `choose_path` can show
PATH_KINDS = ("open", "open_many", "save", "folder")


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
        self._write()

    def clear(self) -> None:
        """Forget everything saved: ``kudiostudio --reset``."""
        self._data = {}
        self._write()

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2,
                                            ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            log.warning("settings not saved: %s", e)


class Studio:
    """The window's back end: one session, one player."""

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
        self.jobs = Jobs(self)
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
        #: a panel's rendered audio being auditioned: (y, sr, t0, panel id).
        #: Loops until something else claims playback.
        self._preview: Optional[Tuple[np.ndarray, int, float, str]] = None
        #: a curve a panel drew over the spectrogram, {"times", "hz"}, in
        #: seconds on the clip's timeline and Hz (None where there is none)
        self._overlay: Optional[Dict] = None
        #: open and save dialogs, when there is a window to show them
        self.dialogs = None
        #: closes that window; its closing event still waits for jobs
        self.close_window: Optional[Callable[[], None]] = None
        self._image_lock = threading.Lock()
        self._image_key = None
        self._image: Optional[analysis.Image] = None
        self._levels: Dict[int, Dict] = {}

        self.editing = Editing(self)
        self.markers = Markers(self)
        #: the plots while a take is running
        self.monitor = LiveMonitor(self)
        self.panels = [cls(self) for cls in PANEL_CLASSES]
        #: every plugin found, including the ones that were left out
        self.plugins = plugins.discover()
        for plugin in self.plugins:
            if plugin.panel is None:
                continue
            try:
                panel = plugin.panel(self)
                if not panel.id or any(p.id == panel.id for p in self.panels):
                    raise ValueError(f"its id {panel.id!r} is empty or taken")
            except Exception as e:  # noqa: BLE001 - one plugin must not stop the window
                plugin.error = f"{type(e).__name__}: {e}"
                log.warning("plugin %s left out -- %s", plugin.name, plugin.error)
                continue
            self.panels.append(panel)
        #: name -> bound method, for every command the page may call
        self.commands: Dict[str, Callable] = commands_of(self)
        self.commands.update(commands_of(self.editing))
        self.commands.update(commands_of(self.markers, "markers."))
        for panel in self.panels:
            self.commands.update(commands_of(panel, panel.id + "."))

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
        fn = self.commands[name]
        with self.lock:
            return fn(*(args or ()), **(kwargs or {}))

    def panel(self, panel_id: str):
        """The panel *panel_id*, or None."""
        return next((p for p in self.panels if p.id == panel_id), None)

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
        self.end_preview()          # the audio it was rendered from is gone
        self.player.stop()
        self.playhead = 0.0
        # whichever panel drew the curve, it was measured on other audio
        self.clear_overlay()
        for panel in self.panels:
            panel.on_session_changed()
        self._push_state()

    def _on_compare(self, on: bool) -> None:
        # rebase playback first: hearing the switch late is what makes an
        # A/B hard to judge
        if self.player.playing and self._preview is None:
            self._play_range(self.player.position, self.segment[1])
        self.clear_overlay()        # drawn on the version no longer shown
        self.say(self._compare_message(on))
        self._push_state()

    def _compare_message(self, on: bool) -> str:
        if not on:
            return "A/B — monitoring the edit"
        gap = self.session.loudness_gap()
        if gap is None:
            return ("A/B — the original, level as-is "
                    "(too short to measure loudness)")
        if self.session.loudness_matched:
            return (f"A/B — the original, levelled to the edit "
                    f"({-gap:+.1f} LU applied)")
        return (f"A/B — the original, {gap:+.1f} LU against the edit; "
                f"turn on Match loudness to compare fairly")

    def _on_finished(self, end: float) -> None:
        with self.lock:
            if self._preview is not None:
                self._replay_preview()      # auditioning loops until stopped
                return
            # running to the end is not a choice of position: go back to the
            # start of what was played, so pressing play again repeats it
            self.playhead = self.segment[0]
            if self.loop and self.session.has_audio:
                self._play_range(*self.segment)
            else:
                self.events.publish("playback", playing=False,
                                    position=self.playhead)

    def _on_play_failed(self, message: str) -> None:
        with self.lock:
            self.end_preview()
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
            "can_revert": s.original() is not None,
            "can_compare": s.can_compare(),
            "comparing": s.comparing,
            "match_loudness": s.loudness_matched,
            "can_copy": s.can_copy(),
            "can_paste": s.can_paste(),
            "has_history": s.has_history(),
            "crossfade_ms": s.crossfade_ms,
            "files": [{"path": p, "name": Path(p).name} for p in self.files],
            "markers": [{"start": m.start, "end": m.end, "label": m.label}
                        for m in s.markers],
            "playing": self.player.playing,
            "previewing": self._preview[3] if self._preview else "",
            "playhead": self.playhead,
            "loop": self.loop,
            "selection_only": self.selection_only,
            "gain_db": self.gain_db,
            "analysis": dict(self.settings.__dict__),
            "call": self.settings.call(),
            "language": self.language,
            "busy": [owner for owner, _ in self.jobs.running()],
            "monitoring": self.monitor.running,
            "version_info": {"kudiostudio": __version__, "kudio": kudio.__version__},
        }

    @command
    def rail(self) -> List[Dict]:
        """The tool rail's entries, in order."""
        return rail(self.panels)

    # --------------------------------------------------------------- dialogs

    def _last_dir(self) -> str:
        return self.prefs.get("last_dir", "") or str(Path.home())

    def remember_dir(self, path) -> None:
        """Start the next dialog in *path*'s folder."""
        self.prefs.set("last_dir", str(Path(path).parent))

    def _show(self, dialog: Callable, *args):
        # the dialog waits for the person, not for this lock
        self.lock.release()
        try:
            return dialog(*args)
        finally:
            self.lock.acquire()

    @command
    def choose_path(self, kind: str, title: str = "",
                    file_types: Sequence[str] = (), suggested: str = "") -> Dict:
        """Ask for a path in the window's own dialog.

        *kind* is one of `PATH_KINDS`. *suggested* is a file name, put in
        the last folder used, or a full path. Returns ``{"path": ...}`` --
        a list for ``open_many``, None when the person cancelled -- or
        ``{"needs_path": True, "suggested": ...}`` when there is no window,
        so the page asks for one itself.
        """
        if kind not in PATH_KINDS:
            raise ValueError(f"unknown dialog {kind!r}")
        if suggested and Path(suggested).is_absolute():
            start = Path(suggested)
        elif suggested:
            start = Path(self._last_dir()) / suggested
        else:
            start = Path(self._last_dir())
        if self.dialogs is None:
            return {"needs_path": True, "suggested": str(start)}
        types = tuple(file_types) or ("All files (*.*)",)
        if kind == "folder":
            path = self._show(self.dialogs.folder, str(start))
        elif kind == "save":
            path = self._show(self.dialogs.save_file, str(start.parent),
                              start.name, types)
        else:
            chosen = self._show(self.dialogs.open_files,
                                str(start if start.is_dir() else start.parent),
                                types, kind == "open_many")
            path = list(chosen) if kind == "open_many" else (chosen[0] if chosen else None)
        return {"path": path or None}

    # ----------------------------------------------------------------- files

    @command
    def open_dialog(self) -> Dict:
        """Ask for files in the window's own dialog; ``needs_path`` when
        there is no window, so the page asks for a path itself."""
        if self.dialogs is None:
            return {"opened": False, "needs_path": True}
        paths = self._show(self.dialogs.open_files, self._last_dir(),
                           AUDIO_TYPES, True)
        if not paths:
            return {"opened": False}
        self.open_files(list(paths))
        return {"opened": True}

    @command
    def open_files(self, paths: List[str]) -> Dict:
        """Add *paths* to the file list and open the first.

        A manifest among them goes to the Manifest panel instead: given to
        `kudio.file_load` it only fails as a file that is not audio.
        """
        paths = [str(p) for p in paths if str(p).strip()]
        manifests = [p for p in paths if is_manifest(p)]
        paths = [p for p in paths if not is_manifest(p)]
        if manifests:
            # one panel, one manifest: the last one named is the one shown
            self.open_manifest(manifests[-1])
        if not paths:
            return self.state()
        for p in paths:
            resolved = str(Path(p).resolve())
            if resolved not in self.files:
                self.files.append(resolved)
        self.load(str(Path(paths[0]).resolve()))
        return self.state()

    @command
    def open_manifest(self, path: str) -> Dict:
        """Read *path* in the Manifest panel and bring the panel forward.
        The table fills in a moment later; the status line says when."""
        self.panel("manifest").load(path)
        self.remember_dir(path)
        self.events.publish("tool", id="manifest")
        return {"opened": True}

    @command
    def load(self, path: str) -> Dict:
        try:
            self.session.load_file(path)
        except Exception as e:  # noqa: BLE001 - reported to the person
            self.say(f"Could not open {Path(path).name} — {type(e).__name__}: {e}")
            return self.state()
        self.remember_dir(path)
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
        self.end_preview()
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
        self.end_preview()
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
        self.end_preview()
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

    def position(self) -> float:
        """Where the playhead is now. While the clip plays that is where
        playback has got to, not where it started; a panel's preview loops
        audio of its own, so its clock is not the clip's playhead."""
        if self.player.playing and self._preview is None:
            return min(self.player.position, self.session.duration)
        return self.playhead

    def _playback_state(self) -> Dict:
        return {"playing": self.player.playing, "position": self.player.position
                if self.player.playing else self.playhead,
                "segment": list(self.segment)}

    # --------------------------------------------------------------- preview

    def start_preview(self, owner: str, y: np.ndarray, sr: int) -> bool:
        """Audition a panel's rendered audio. Loops; never touches the session.

        Sits at the selection's place on the timeline so the playhead lands
        where the effect would. An effect that changes the length -- a time
        stretch -- runs the playhead past the selection; the audio is right,
        the playhead is approximate.
        """
        y = np.asarray(y, dtype=np.float32)
        if sr <= 0 or y.size == 0:
            return False
        if self._preview is not None and self._preview[3] != owner:
            # another panel's preview is replaced, and that panel is told,
            # or its button stays down over audio it is no longer playing
            self.end_preview()
        sel = self.session.selection()
        self._preview = (y, int(sr), sel[0] if sel else 0.0, owner)
        self._replay_preview()
        return True

    def _replay_preview(self) -> None:
        y, sr, t0, owner = self._preview
        self.segment = (t0, t0 + len(y) / sr)
        self.playhead = t0
        playback = self.player.play(y, sr, self.gain_db, t0=t0)
        if playback is not None:
            self.events.publish("playback", playing=True, start=playback.start,
                                end=playback.end, preview=owner)

    def previewing(self, owner: str) -> bool:
        """Whether what is playing is *owner*'s preview."""
        return self._preview is not None and self._preview[3] == owner

    def end_preview(self) -> None:
        """Stop treating playback as a panel's preview, and tell the panel."""
        if self._preview is None:
            return
        owner = self._preview[3]
        self._preview = None
        for panel in self.panels:
            panel.cancel_preview()
        self.events.publish("preview", owner=owner, on=False)

    @command
    def stop_preview(self) -> Dict:
        """A panel's Preview switched off."""
        if self._preview is not None:
            self.end_preview()
            self.playhead = self.player.stop()
            self.events.publish("playback", playing=False, position=self.playhead)
        return self._playback_state()

    # --------------------------------------------------------------- overlay

    def set_overlay(self, times, hz) -> None:
        """Draw a curve over the spectrogram: *times* in seconds on the
        clip's timeline, *hz* with NaN where there is nothing, which the
        page leaves as a gap rather than joining across it."""
        times = np.asarray(times, dtype=np.float64)
        hz = np.asarray(hz, dtype=np.float64)
        self._overlay = {
            "times": [round(float(t), 5) for t in times],
            "hz": [round(float(v), 2) if np.isfinite(v) else None for v in hz],
        }
        self.events.publish("overlay", curve=self._overlay)

    def clear_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay = None
            self.events.publish("overlay", curve=None)

    @command
    def overlay(self) -> Optional[Dict]:
        """The curve over the spectrogram, for a page that has just loaded."""
        return self._overlay

    # ------------------------------------------------------------- the edit

    @command
    def undo(self) -> Dict:
        if not self.session.undo():
            self.say("Nothing to undo.")
        return self.state()

    @command
    def revert(self) -> Dict:
        if not self.session.revert():
            self.say("No original to revert to.")
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
        before = self.settings
        self.settings = analysis.Settings(**values).checked()
        self.prefs.set("analysis", dict(self.settings.__dict__))
        if self.monitor.running and self.settings != before:
            # a rolling view cannot analyse again what has already scrolled
            # past, so new settings start the picture over; better than a
            # window whose left half was computed one way and its right half
            # another
            self.monitor.start(self.monitor.sr)
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
        """The spectrogram of what the plots show, made once per version.

        An edit that leaves the clip the same length -- a denoise, a gain, an
        A/B swap -- keeps the colour scale of the version before it. That is
        exactly when two versions are being compared, and levels picked
        afresh from each image would draw a denoised clip as bright as the
        noisy one. Another file, another take, other analysis settings, or a
        trim start from the new image's own levels.
        """
        y, sr, version = self.audio()
        if y is None:
            return None, version
        settings = self.settings
        origin = self.session.origin
        key = (version, settings, origin)
        with self._image_lock:
            if self._image_key != key:
                fresh = analysis.image(y, sr, settings)
                last, last_key = self._image, self._image_key
                if last is not None and last_key is not None \
                        and last_key[1:] == (settings, origin) \
                        and abs(fresh.duration - last.duration) <= SAME_CLIP_S:
                    fresh = replace(fresh, levels=last.levels)
                self._image, self._image_key = fresh, key
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
        curve = measured.curve
        result = {
            "version": version,
            # short-term loudness over the clip, or None when it is shorter
            # than the window; silence as None, a gap in the line
            "curve": None if curve is None else {
                "times": [round(float(t), 4) for t in curve.times],
                "lufs": [_audible(v) for v in curve.lufs],
            },
            "short_term_s": float(kudio.SHORT_TERM),
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

    # ----------------------------------------------------------------- about

    @command
    def about(self) -> Dict:
        """The versions, the plugins on the rail, and the ones left off it,
        which the page names in the status bar when it opens."""
        return {"kudiostudio": __version__, "kudio": kudio.__version__,
                "plugins": [p.source for p in self.plugins if not p.error],
                "left_out": [self._say_left_out(p) for p in self.plugins if p.error]}

    def _say_left_out(self, plugin) -> str:
        return i18n.translate(f"Plugin {plugin.name} was left out — {plugin.error}",
                              self.language)

    @command
    def quit(self) -> Dict:
        """Close the window, through the same wait for running jobs as its
        close button. A page in a browser has no window to close."""
        if self.close_window is None:
            return {"closing": False}
        threading.Thread(target=self.close_window, name="quit", daemon=True).start()
        return {"closing": True}

    def shutdown(self) -> None:
        self.player.shutdown()
        for panel in self.panels:
            panel.shutdown()


def _finite(value) -> Optional[float]:
    value = float(value)
    return value if np.isfinite(value) else None


#: BS.1770's absolute gate: a block quieter than this counts as no level at
#: all, and the loudness curve shows it as a gap
SILENCE_LUFS = -70.0


def _audible(value) -> Optional[float]:
    """A point of the loudness curve, or None where there is silence. Digital
    silence measures -inf; a pause after sound measures the K-weighting
    filter's decaying tail, finite and absurd -- -2000 LUFS and lower -- and
    drawn as a level it drags the curve's axis down with it."""
    value = _finite(value)
    return value if value is not None and value >= SILENCE_LUFS else None


__all__ = ["Studio", "EventHub", "Prefs", "AUDIO_TYPES", "PATH_KINDS",
           "config_dir", "system_language"]
