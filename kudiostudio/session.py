# -*- coding: utf-8 -*-
"""The single piece of audio every panel reads from and writes to.

`AudioSession` owns the current waveform, its sample rate, an undo stack and
the time selection. Panels never talk to each other -- they mutate the session
and the views repaint on :attr:`AudioSession.changed`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QObject, Signal

import kudio

log = logging.getLogger(__name__)

#: Nothing deeper is worth keeping in RAM for an interactive editor.
UNDO_DEPTH = 20

#: Milliseconds of crossfade at every splice. A hard cut leaves a step in the
#: waveform, and a step is a click; 5 ms removes it without being heard as a
#: fade. It is **on by default and never silent** — every edit says how much
#: was applied, because quietly altering audio is worse than a click.
DEFAULT_CROSSFADE_MS = 5.0


@dataclass(frozen=True)
class Marker:
    """A named point or range in the clip, in seconds.

    ``end == start`` is a point. Markers are positions in *time*, so an edit
    that changes the length has to move them; see
    :meth:`AudioSession._shift_markers`.
    """
    start: float
    end: float
    label: str = ""

    @property
    def is_range(self) -> bool:
        return self.end > self.start

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class AudioSession(QObject):
    """Current audio + edit history, shared by the whole application."""

    #: waveform or sample rate replaced (panels should repaint)
    changed = Signal()
    #: time selection changed, in seconds; (0, 0) means "no selection"
    selection_changed = Signal(float, float)
    #: A/B preview toggled; True means the original is being monitored
    compare_changed = Signal(bool)
    #: clipboard gained or lost content
    clipboard_changed = Signal(bool)
    #: the marker list changed (added, removed, moved by an edit, undone)
    markers_changed = Signal()
    #: human-readable note for the status bar
    message = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._y: Optional[np.ndarray] = None
        self._sr: int = 0
        self._name: str = ""
        self._path: Optional[Path] = None
        self._undo: List[Tuple[np.ndarray, int, str, Tuple[Marker, ...]]] = []
        self._original: Optional[Tuple[np.ndarray, int]] = None
        self._sel: Tuple[float, float] = (0.0, 0.0)
        self._comparing: bool = False
        self._match_loudness: bool = True
        self._matched: Optional[np.ndarray] = None
        self._gap: Optional[float] = None
        self._gap_measured: bool = False
        self._clipboard: Optional[Tuple[np.ndarray, int]] = None
        self._markers: Tuple[Marker, ...] = ()
        self._crossfade_ms: float = DEFAULT_CROSSFADE_MS

    # ---------------------------------------------------------------- state

    @property
    def y(self) -> Optional[np.ndarray]:
        return self._y

    @property
    def sr(self) -> int:
        return self._sr

    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def duration(self) -> float:
        return len(self._y) / self._sr if self.has_audio else 0.0

    @property
    def has_audio(self) -> bool:
        return self._y is not None and self._sr > 0 and len(self._y) > 0

    def can_undo(self) -> bool:
        return bool(self._undo)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if not self.has_audio:
            return "<AudioSession empty>"
        return f"<AudioSession {self._name!r} {self.duration:.2f}s @ {self._sr} Hz>"

    # ----------------------------------------------------------------- load

    def load_file(self, path, sr: Optional[int] = None, mono: bool = True) -> None:
        """Load *path* through :func:`kudio.file_load` and make it current."""
        path = Path(path)
        y, rate = kudio.file_load(path, sr=sr, mono=mono)
        self._path = path
        self._original = (np.array(y, copy=True), int(rate))
        self._undo.clear()
        # a different file entirely -- the previous clip's markers point at
        # moments in audio that is no longer loaded. `set_audio` does this for
        # recordings; `_apply` does not, so opening a second file used to
        # inherit the first one's markers.
        self._set_markers(())
        self._apply(y, int(rate), path.name)
        found = self._load_sidecar(path)
        note = f" · {found} marker(s)" if found else ""
        self.message.emit(
            f"Loaded {path.name} — {self.duration:.2f}s @ {rate} Hz{note}")

    def set_audio(self, y: np.ndarray, sr: int, name: str = "",
                  *, undo_label: str = "", keep_original: bool = False) -> None:
        """Replace the current audio, pushing the previous state onto the undo stack.

        *keep_original* is for edits: the "original" used by A/B compare and
        :meth:`revert` stays untouched. New recordings pass ``False`` so they
        become the new baseline.
        """
        if undo_label and self.has_audio:
            self._push_undo(undo_label)
        if not keep_original:
            self._original = (np.array(y, copy=True), int(sr))
            self._undo.clear()
            self._path = None
            # different audio entirely: markers placed on the old clip would
            # point at moments that no longer exist
            self._set_markers(())
        self._apply(y, sr, name or self._name)

    def _apply(self, y: np.ndarray, sr: int, name: str) -> None:
        y = np.asarray(y)
        if y.dtype != np.float32:
            y = y.astype(np.float32)
        self._y, self._sr, self._name = y, int(sr), name
        self._matched, self._gap, self._gap_measured = None, None, False
        self.set_compare(False)     # a new edit ends any A/B preview
        self.clear_selection()
        self.changed.emit()

    # ----------------------------------------------------------------- undo

    def _push_undo(self, label: str) -> None:
        # markers ride along: an edit moves them, so undoing the edit has to
        # move them back, and recomputing the inverse shift is guesswork
        self._undo.append((np.array(self._y, copy=True), self._sr, label,
                           self._markers))
        del self._undo[:-UNDO_DEPTH]

    def undo(self) -> bool:
        if not self._undo:
            return False
        y, sr, label, markers = self._undo.pop()
        self._set_markers(markers)
        self._apply(y, sr, self._name)
        self.message.emit(f"Undo: {label}")
        return True

    def revert(self) -> bool:
        """Drop every edit and go back to the audio as it was loaded."""
        if self._original is None:
            return False
        y, sr = self._original
        self._undo.clear()
        self._set_markers(())
        self._apply(np.array(y, copy=True), sr, self._name)
        self.message.emit("Reverted to the original audio")
        return True

    def original(self) -> Optional[Tuple[np.ndarray, int]]:
        """The unedited audio, for A/B comparison and metrics."""
        return self._original

    # --------------------------------------------------------- A/B compare

    def can_compare(self) -> bool:
        """True when an original exists *and* the working audio has moved away
        from it. Offering A/B on two identical buffers is just a silent switch.
        """
        if self._original is None or not self.has_audio:
            return False
        y0, sr0 = self._original
        if sr0 != self._sr or len(y0) != len(self._y):
            return True
        return not np.array_equal(y0, self._y)

    @property
    def comparing(self) -> bool:
        return self._comparing

    def set_compare(self, on: bool) -> bool:
        """Monitor the original instead of the edit. Returns the state reached.

        Comparing is a *monitoring* mode: it changes what is played and drawn,
        never what the panels operate on. They keep reading :attr:`y`.
        """
        on = bool(on) and self.can_compare()
        if on != self._comparing:
            self._comparing = on
            self.compare_changed.emit(on)
        return self._comparing

    def preview(self) -> Tuple[Optional[np.ndarray], int]:
        """The audio to play and draw right now -- the original while comparing.

        Panels must not use this; an effect applied to the preview would write
        the original back over the edit.
        """
        if not (self._comparing and self._original is not None):
            return self._y, self._sr
        y0, sr0 = self._original
        if not self._match_loudness:
            return y0, sr0
        if self._matched is None:
            gap = self.loudness_gap()
            if gap is None:
                return y0, sr0
            self._matched = (np.asarray(y0) * 10 ** (-gap / 20.0)).astype(np.float32)
        return self._matched, sr0

    # ------------------------------------------------------- loudness match

    @property
    def loudness_matched(self) -> bool:
        return self._match_loudness

    def set_loudness_match(self, on: bool) -> bool:
        """Level the original to the edit before comparing them.

        Of two versions of a clip the louder one is reliably preferred, whatever
        its quality, so an unmatched A/B is partly a loudness test. On by
        default; turn it off to hear what an effect did to the level itself.
        """
        on = bool(on)
        if on != self._match_loudness:
            self._match_loudness = on
            self._matched = None
            self.compare_changed.emit(self._comparing)
        return self._match_loudness

    def loudness_gap(self) -> Optional[float]:
        """How many LU louder the original is than the edit, or ``None``.

        ``None`` means it could not be measured -- a clip shorter than
        BS.1770's 400 ms block, or silence on one side. Measured once per edit;
        the result also supplies the gain :meth:`preview` applies.
        """
        if self._gap_measured:
            return self._gap
        self._gap_measured = True
        self._gap = None
        if self._original is None or not self.has_audio:
            return None
        y0, sr0 = self._original
        try:
            gap = kudio.loudness(y0, sr0) - kudio.loudness(self._y, self._sr)
        except Exception as e:                      # too short, bad shape, ...
            log.debug("loudness gap unavailable: %s", e)
            return None
        self._gap = float(gap) if np.isfinite(gap) else None
        return self._gap

    # ------------------------------------------------------------ selection

    def selection(self) -> Optional[Tuple[float, float]]:
        """Selected range in seconds, or ``None`` when nothing is selected."""
        a, b = self._sel
        return (a, b) if b > a else None

    def set_selection(self, start: float, end: float) -> None:
        if end < start:
            start, end = end, start
        start = max(0.0, start)
        end = min(self.duration, end) if self.has_audio else end
        self._sel = (start, end)
        self.selection_changed.emit(start, end)

    def clear_selection(self) -> None:
        self._sel = (0.0, 0.0)
        self.selection_changed.emit(0.0, 0.0)

    def selected_slice(self) -> slice:
        """Sample range covered by the selection (the whole clip if none)."""
        sel = self.selection()
        if sel is None or not self.has_audio:
            return slice(0, len(self._y) if self.has_audio else 0)
        return slice(int(sel[0] * self._sr), int(sel[1] * self._sr))

    def selected_audio(self) -> Optional[np.ndarray]:
        return self._y[self.selected_slice()] if self.has_audio else None

    @property
    def crossfade_ms(self) -> float:
        """Crossfade applied at every splice, in milliseconds. 0 disables it."""
        return self._crossfade_ms

    def set_crossfade_ms(self, value: float) -> float:
        self._crossfade_ms = max(0.0, float(value))
        return self._crossfade_ms

    def _crossfade_for(self, override):
        """The length this edit will actually use, in milliseconds.

        A 5 ms splice in the middle of a word and a 200 ms one between two
        takes are different jobs, so every splicing operation takes an
        override. The session value stays where it was: choosing a long
        crossfade for one join must not quietly make every later cut long too.
        """
        if override is None:
            return self._crossfade_ms
        return max(0.0, float(override))

    def _crossfade_note(self, seams: int,
                        crossfade_ms: Optional[float] = None) -> str:
        """The clause every splice message ends with.

        An automatic edit nobody was told about is the thing to avoid here, so
        the amount goes in the status line every single time — including when
        it is off, so "no crossfade" is also visible.
        """
        if seams <= 0:
            return ""
        applied = self._crossfade_for(crossfade_ms)
        if applied <= 0:
            return " (no crossfade — hard cut)"
        return f" ({applied:g} ms crossfade)"

    def _splice(self, parts, crossfade_ms: Optional[float] = None
                ) -> Tuple[np.ndarray, float]:
        """Join *parts*, and say how many seconds the crossfades swallowed.

        Returns ``(audio, seconds_lost)``. The caller needs the second value:
        every seam shortens the result, and markers after the edit have to move
        by that much on top of whatever the edit itself changed.
        """
        usable = [np.asarray(p, dtype=np.float32) for p in parts
                  if np.asarray(p).size]
        seams = max(0, len(usable) - 1)
        applied = self._crossfade_for(crossfade_ms)
        if applied <= 0 or seams == 0:
            return (np.concatenate(usable) if usable
                    else np.zeros(0, dtype=np.float32)), 0.0

        duration = applied / 1000.0
        merged = kudio.splice(usable, self._sr, duration)
        lost = (sum(len(p) for p in usable) - len(merged)) / self._sr
        return merged, lost

    def replace_selection(self, y_new: np.ndarray, *, undo_label: str,
                          crossfade: bool = False,
                          crossfade_ms: Optional[float] = None) -> None:
        """Splice *y_new* over the selection (or replace everything if none).

        *crossfade* defaults to **off**, and the distinction matters:

        * An **effect** replaces the selection with a processed version of
          *itself*. Blending the edges back into the unprocessed neighbours
          would quietly leave 5 ms at each end un-effected — you asked for
          that range to be filtered, not most of it.
        * A **structural** edit — paste, insert silence — joins audio that has
          nothing to do with its neighbours, and the step at the join is a
          click. Those pass ``crossfade=True``.
        """
        if not self.has_audio:
            return
        sel = self.selection()
        if sel is None:
            self.set_audio(y_new, self._sr, undo_label=undo_label, keep_original=True)
            return
        sl = self.selected_slice()
        y_new = np.asarray(y_new, dtype=np.float32)
        parts = [self._y[:sl.start], y_new, self._y[sl.stop:]]
        merged, lost = self._splice(parts, crossfade_ms) if crossfade \
            else (np.concatenate([p for p in parts if len(p)]), 0.0)
        delta = (len(y_new) - (sl.stop - sl.start)) / self._sr - lost
        # set_audio first: it snapshots the markers for undo, and the snapshot
        # has to be the positions from *before* this edit moved them
        self.set_audio(merged, self._sr, undo_label=undo_label, keep_original=True)
        self._shift_markers(sel[1], delta)

    # ------------------------------------------------------------- clipboard

    def can_copy(self) -> bool:
        """Cut, copy and delete all need an explicit selection.

        Falling back to "the whole clip" is fine for an effect, which is
        reversible and visible. For *cut* it means one keystroke deletes
        everything, so the range has to be asked for. ``Ctrl+A`` is right there.
        """
        return self.has_audio and self.selection() is not None

    def can_paste(self) -> bool:
        return self.has_audio and self._clipboard is not None

    def clipboard_duration(self) -> float:
        if self._clipboard is None:
            return 0.0
        y, sr = self._clipboard
        return len(y) / sr if sr else 0.0

    def copy(self) -> bool:
        """Put the selection on the clipboard, leaving the audio alone."""
        if not self.can_copy():
            return False
        self._clipboard = (np.array(self.selected_audio(), copy=True), self._sr)
        self.clipboard_changed.emit(True)
        self.message.emit(f"Copied {self.clipboard_duration():.3f}s")
        return True

    def cut(self, *, crossfade_ms: Optional[float] = None) -> bool:
        """Copy the selection, then remove it."""
        if not self.copy():
            return False
        seconds = self.clipboard_duration()
        if not self.delete(undo_label="cut", crossfade_ms=crossfade_ms):
            return False
        self.message.emit(
            f"Cut {seconds:.3f}s{self._crossfade_note(1, crossfade_ms)}")
        return True

    def delete(self, *, undo_label: str = "delete",
               crossfade_ms: Optional[float] = None) -> bool:
        """Remove the selection; what follows moves left to close the gap."""
        if not self.can_copy():
            return False
        sel = self.selection()
        sl = self.selected_slice()
        removed = (sl.stop - sl.start) / self._sr
        merged, lost = self._splice([self._y[:sl.start], self._y[sl.stop:]],
                                    crossfade_ms)
        self.set_audio(merged, self._sr, undo_label=undo_label, keep_original=True)
        self._shift_markers(sel[1], -(removed + lost))
        self.message.emit(
            f"Deleted {removed:.3f}s{self._crossfade_note(1, crossfade_ms)}")
        return True

    def paste(self, at: Optional[float] = None, *,
              crossfade_ms: Optional[float] = None) -> bool:
        """Paste the clipboard.

        A selection is replaced; otherwise the clipboard is inserted at *at*
        seconds, or appended when no position is given. The pasted range ends
        up selected, so pasting twice does not stack invisibly.

        Clipboard audio recorded at another rate is resampled — the clipboard
        remembers its own rate rather than assuming the session's, which is how
        a pasted clip ends up at the wrong speed.
        """
        if not self.can_paste():
            return False
        clip, clip_sr = self._clipboard
        if clip_sr != self._sr:
            clip = kudio.resample(clip, clip_sr, self._sr)
        clip = np.asarray(clip, dtype=np.float32)

        sel = self.selection()
        if sel is not None:
            start = sel[0]
            self.replace_selection(clip, undo_label="paste", crossfade=True,
                                   crossfade_ms=crossfade_ms)
        else:
            position = self.duration if at is None else max(0.0, min(at, self.duration))
            index = int(position * self._sr)
            start = position
            merged, lost = self._splice(
                [self._y[:index], clip, self._y[index:]], crossfade_ms)
            self.set_audio(merged, self._sr, undo_label="paste", keep_original=True)
            self._shift_markers(position, len(clip) / self._sr - lost)
        self.set_selection(start, start + len(clip) / self._sr)
        self.message.emit(f"Pasted {len(clip) / self._sr:.3f}s at {start:.3f}s"
                          f"{self._crossfade_note(2, crossfade_ms)}")
        return True

    def insert_silence(self, seconds: float, at: Optional[float] = None, *,
                       crossfade_ms: Optional[float] = None) -> bool:
        """Insert *seconds* of silence, replacing the selection if there is one."""
        if not self.has_audio or seconds <= 0:
            return False
        silence = np.zeros(int(round(seconds * self._sr)), dtype=np.float32)
        sel = self.selection()
        if sel is not None:
            self.replace_selection(silence, undo_label="insert silence",
                                   crossfade=True, crossfade_ms=crossfade_ms)
        else:
            position = self.duration if at is None else max(0.0, min(at, self.duration))
            index = int(position * self._sr)
            merged, lost = self._splice(
                [self._y[:index], silence, self._y[index:]], crossfade_ms)
            self.set_audio(merged, self._sr, undo_label="insert silence",
                           keep_original=True)
            self._shift_markers(position, seconds - lost)
        self.message.emit(f"Inserted {seconds:.3f}s of silence"
                          f"{self._crossfade_note(2, crossfade_ms)}")
        return True

    def crop_to_selection(self) -> bool:
        """Throw away everything outside the selection."""
        if not self.can_copy():
            return False
        sel = self.selection()
        kept = np.array(self.selected_audio(), copy=True)
        self._shift_markers(0.0, -sel[0], window=(sel[0], sel[1]))
        self.set_audio(kept, self._sr, undo_label="crop", keep_original=True)
        self.message.emit(f"Cropped to {len(kept) / self._sr:.3f}s")
        return True

    # --------------------------------------------------------------- markers

    @property
    def markers(self) -> Tuple[Marker, ...]:
        return self._markers

    def add_marker(self, start: float, end: Optional[float] = None,
                   label: str = "") -> Marker:
        """Add a point (``end=None``) or a range, kept sorted by start time."""
        end = start if end is None else max(start, end)
        marker = Marker(start=float(start), end=float(end), label=label)
        self._set_markers(tuple(sorted(self._markers + (marker,),
                                       key=lambda m: (m.start, m.end))))
        return marker

    def mark_selection(self, label: str = "") -> Optional[Marker]:
        """Turn the current selection into a marker."""
        sel = self.selection()
        if sel is None:
            return None
        return self.add_marker(sel[0], sel[1], label)

    def remove_marker(self, index: int) -> bool:
        if not 0 <= index < len(self._markers):
            return False
        self._set_markers(self._markers[:index] + self._markers[index + 1:])
        return True

    def rename_marker(self, index: int, label: str) -> bool:
        if not 0 <= index < len(self._markers):
            return False
        markers = list(self._markers)
        markers[index] = replace(markers[index], label=label)
        self._set_markers(tuple(markers))
        return True

    def clear_markers(self) -> None:
        self._set_markers(())

    def set_markers(self, markers: Sequence[Marker]) -> None:
        """Replace the whole list — e.g. with the output of ``kudio.vad``."""
        self._set_markers(tuple(sorted(markers, key=lambda m: (m.start, m.end))))

    # ------------------------------------------------------ marker persistence

    @staticmethod
    def sidecar_path(path) -> Path:
        """Where a clip's markers live: ``clip.wav`` → ``clip.wav.labels.json``.

        Beside the audio and named after it, so moving the pair together is the
        default and a stray label file is obviously orphaned.
        """
        return Path(path).with_suffix(Path(path).suffix + ".labels.json")

    def save_markers(self, path=None) -> Optional[Path]:
        """Write the markers next to the audio, or to *path*.

        Both formats `kudio.save_labels` understands work: a ``.txt`` target
        gets an Audacity label track, which most of the audio world can read.
        Returns ``None`` when there is nothing to write and no file to write
        beside.
        """
        if path is None:
            if self._path is None:
                return None
            path = self.sidecar_path(self._path)
        labels = [kudio.Label(m.start, m.end, m.label) for m in self._markers]
        written = kudio.save_labels(path, labels)
        self.message.emit(f"Saved {len(labels)} marker(s) to {written.name}")
        return written

    def load_markers(self, path, *, replace: bool = True) -> int:
        """Read markers from *path*. Returns how many arrived."""
        labels = kudio.load_labels(path)
        markers = [Marker(start=x.start, end=x.end, label=x.text)
                   for x in labels]
        if replace:
            self.set_markers(markers)
        else:
            self.set_markers(list(self._markers) + markers)
        return len(markers)

    def _load_sidecar(self, path) -> int:
        """Pick up ``clip.wav.labels.json`` on open, if it is there.

        Silent when absent — that is the normal case. Not silent when present
        and broken: a label file that fails to parse is worth a sentence, since
        the alternative is markers quietly not appearing.
        """
        sidecar = self.sidecar_path(path)
        if not sidecar.is_file():
            return 0
        try:
            found = self.load_markers(sidecar)
        except Exception as e:                              # noqa: BLE001
            log.warning("could not read %s: %s", sidecar, e)
            self.message.emit(f"{sidecar.name} could not be read — "
                              f"{type(e).__name__}: {e}")
            return 0
        return found

    def _set_markers(self, markers: Tuple[Marker, ...]) -> None:
        if markers != self._markers:
            self._markers = markers
            self.markers_changed.emit()

    def _shift_markers(self, after: float, delta: float,
                       window: Optional[Tuple[float, float]] = None) -> None:
        """Move markers to follow an edit at *after* that changed length by *delta*.

        Markers before the edit stay put. Markers after it move. A marker that
        straddles a deletion is clamped rather than dropped — it was placed on
        something, and silently losing it is worse than showing it shortened.
        *window* additionally discards anything now outside ``(lo, hi)``, which
        is what cropping needs.
        """
        if not self._markers:
            return
        lo, hi = window if window is not None else (float('-inf'), float('inf'))

        def move(value: float) -> float:
            return value + delta if value > after else value

        moved = []
        for marker in self._markers:
            if window is not None and (marker.end < lo or marker.start > hi):
                continue
            start = move(max(marker.start, lo) if window else marker.start)
            end = move(min(marker.end, hi) if window else marker.end)
            moved.append(replace(marker, start=max(0.0, start),
                                 end=max(0.0, max(start, end))))
        self._set_markers(tuple(moved))

    # ----------------------------------------------------------------- save

    def save(self, path, subtype: str = "PCM_16") -> Path:
        if not self.has_audio:
            raise RuntimeError("nothing to save")
        path = Path(path)
        kudio.save_wave(path, self._y, self._sr, subtype=subtype)
        self.message.emit(f"Saved {path.name}")
        return path

    def save_selection(self, path, subtype: str = "PCM_16") -> Path:
        """Write only the selected range."""
        if not self.can_copy():
            raise RuntimeError("nothing selected")
        path = Path(path)
        selected = self.selected_audio()
        kudio.save_wave(path, selected, self._sr, subtype=subtype)
        self.message.emit(
            f"Saved {len(selected) / self._sr:.3f}s to {path.name}")
        return path
