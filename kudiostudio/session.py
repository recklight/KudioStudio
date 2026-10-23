# -*- coding: utf-8 -*-
"""The single piece of audio every panel reads from and writes to.

`AudioSession` owns the current waveform, its sample rate, an undo stack and
the time selection. Panels never talk to each other -- they mutate the session
and the views repaint on :attr:`AudioSession.changed`.

Every undoable edit also records the Python that does it again, so the history
can be handed back as a script (:meth:`AudioSession.edit_script`): what was
worked out by ear here becomes something a batch job can run.
"""
from __future__ import annotations

import ast
import hashlib
import itertools
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

import kudio
from kudiostudio import __version__
from kudiostudio.events import Signal

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


@dataclass(frozen=True, eq=False)
class EditStep:
    """One entry of the edit history: what Undo calls it, and the code that
    does it again.

    *code* runs with ``y`` and ``sr`` bound to the audio and its rate and with
    ``np`` and ``kudio`` imported, and leaves the result in ``y``. It is
    ``None`` for an edit that arrived without any; the script export says so
    out loud instead of skipping it, because a script that quietly leaves out
    a step writes a different file and nothing about it looks wrong.

    Steps compare by identity, not by text: two identical gains are still two
    moments in the history, and a paste has to know which of them its
    clipboard was copied after.
    """
    label: str
    code: Optional[str] = None
    #: for a paste, where the clipboard came from -- see `_Copied`
    copied: Optional["_Copied"] = field(default=None, repr=False)


@dataclass(frozen=True)
class _Origin:
    """Where a history starts: the file, and how `kudio.file_load` read it.

    *path* is ``None`` for audio that never was a file -- a recording -- which
    a script has no way to read back.

    Two origins are the same audio when they compare equal, which is what lets
    a paste copy from earlier in the same history instead of rebuilding it.
    The same file opened twice is the same audio as long as its bytes are; two
    recordings are never the same audio, whatever their rate, so each one is
    a take of its own.
    """
    path: Optional[Path]
    rate: int
    #: the ``sr`` asked of file_load; ``None`` kept the file's own rate
    requested_sr: Optional[int] = None
    mono: bool = True
    #: which recording this is, counted per session; 0 for a file
    take: int = 0
    #: SHA-256 of the file as it was read. The script checks it, so it cannot
    #: run the edits on a later file at the same path -- the edited audio
    #: itself, after Save As wrote over the original.
    digest: Optional[str] = None
    #: size and modification time as read: a cheap first look at whether the
    #: file has changed since, before reading it all again to be sure
    stat: Optional[Tuple[int, int]] = field(default=None, compare=False)


@dataclass(frozen=True, eq=False)
class _Copied:
    """What the clipboard holds, described as code rather than as samples.

    It is samples *start:stop* of the audio that *origin* plus *steps*
    produced. A paste usually happens further down that same history, where
    one ``clip = y[start:stop].copy()`` line at the right moment is enough --
    but the clipboard outlives undo and survives opening another file, and
    then the script has to rebuild the audio it was copied from.
    """
    origin: Optional[_Origin]
    steps: Tuple[EditStep, ...]
    start: int
    stop: int


def _assigned_value(code: str) -> Optional[str]:
    """The expression *code* assigns to ``y``, when it does nothing else and
    does not read ``y`` while doing it; ``None`` otherwise.

    A paste's replacement is ``y = clip``: it does not depend on the selection,
    so it can be joined in directly rather than by binding the selection to
    ``y`` first and throwing it away.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return None
    node = tree.body[0]
    target = node.targets[0]
    if len(node.targets) != 1 or not (isinstance(target, ast.Name)
                                      and target.id == "y"):
        return None
    if any(isinstance(n, ast.Name) and n.id == "y" for n in ast.walk(node.value)):
        return None
    return ast.get_source_segment(code, node.value)


def _span(start: int, stop: int, sr: int) -> str:
    """``0.250–0.750 s``: the comment beside a range written in samples."""
    return f"{start / sr:.3f}–{stop / sr:.3f} s"


def _comment_safe(text) -> str:
    """*text* on one line of a comment.

    File names and undo labels go into the script's comments, and both can
    hold anything a name can: a newline or a carriage return in one ends the
    comment and makes the rest code -- code that ran when the script did. They
    are escaped the way Python writes them in a string; anything printable,
    accents and all, is kept as it is.
    """
    return "".join(repr(c)[1:-1] if (ord(c) < 0x20 and c != "\t") or c == "\x7f"
                   else c for c in str(text))


def file_digest(path) -> str:
    """The SHA-256 of the file at *path*, read in blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _stat(path) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


#: The function the script checks its sources with -- `file_digest`, written
#: out, since a script has to run where KudioStudio is not installed.
_DIGEST_CALL = "sha256("
_DIGEST_FUNCTION = [
    "def sha256(path):",
    '    """The file\'s SHA-256: which file this is, not just its name."""',
    "    digest = hashlib.sha256()",
    '    with open(path, "rb") as fh:',
    '        for block in iter(lambda: fh.read(1 << 20), b""):',
    "            digest.update(block)",
    "    return digest.hexdigest()",
]


class AudioSession:
    """Current audio + edit history, shared by the whole application.

    Its signals are `kudiostudio.events.Signal`, not Qt's, so the same session
    serves the Qt window and the browser one.
    """

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

    def __init__(self, parent: object = None):
        # *parent* is what Qt code passes to every object it builds; a
        # session has no use for it
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
        #: the edits since the baseline, as code. Not trimmed to UNDO_DEPTH
        #: like the snapshots: an edit too old to undo is still in the audio.
        self._history: List[EditStep] = []
        self._origin: Optional[_Origin] = None
        self._copied: Optional[_Copied] = None
        #: numbers each recording, so two takes are never the same origin
        self._takes = itertools.count(1)
        #: every file Save As has written in this session, resolved
        self._written: Set[str] = set()

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

    @property
    def history(self) -> Tuple[EditStep, ...]:
        """Every edit since the audio was loaded, recorded or reverted."""
        return tuple(self._history)

    def has_history(self) -> bool:
        return bool(self._history)

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
        self._history.clear()
        # what the script repeats to get the same samples back, so it records
        # the arguments that were passed rather than what they produced --
        # and which bytes they were applied to
        self._origin = _Origin(path.absolute(), int(rate), sr, mono,
                               digest=file_digest(path), stat=_stat(path))
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
                  *, undo_label: str = "", keep_original: bool = False,
                  code: Optional[str] = None) -> None:
        """Replace the current audio, pushing the previous state onto the undo stack.

        *keep_original* is for edits: the "original" used by A/B compare and
        :meth:`revert` stays untouched. New recordings pass ``False`` so they
        become the new baseline.

        *code* is the Python that turns the current ``y`` into the new one --
        see :class:`EditStep` -- and goes into the history beside
        *undo_label*. An edit without it is still undoable, but the script
        export can only say that it does not know how to repeat it.
        """
        if undo_label and self.has_audio:
            self._push_undo(undo_label, self._stored_as(code, y))
        if not keep_original:
            self._original = (np.array(y, copy=True), int(sr))
            self._undo.clear()
            self._history.clear()
            self._origin = _Origin(None, int(sr), take=next(self._takes))
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

    def _push_undo(self, label: str, code: Optional[str] = None) -> None:
        # markers ride along: an edit moves them, so undoing the edit has to
        # move them back, and recomputing the inverse shift is guesswork
        self._undo.append((np.array(self._y, copy=True), self._sr, label,
                           self._markers))
        del self._undo[:-UNDO_DEPTH]
        self._history.append(EditStep(label, code))

    @staticmethod
    def _stored_as(code: Optional[str], y) -> Optional[str]:
        """*code*, followed by the float32 conversion `_apply` is about to make.

        Only when it really makes one. Every kudio effect hands float32 back for
        float32 in, and a script full of conversions that do nothing is harder
        to read; but when one does happen it changes what the next step is fed,
        and a script that skips it drifts away from the session a little more
        with every step.
        """
        if code is None or np.asarray(y).dtype == np.float32:
            return code
        return f"{code}\ny = np.asarray(y, dtype=np.float32)"

    def undo(self) -> bool:
        if not self._undo:
            return False
        y, sr, label, markers = self._undo.pop()
        # every snapshot was pushed together with its step, and the two lists
        # are only ever cleared together, so the last step is this edit's
        self._history.pop()
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
        self._history.clear()
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

    def _splice_code(self, parts: Sequence[Tuple[str, np.ndarray]],
                     crossfade_ms: Optional[float] = None) -> str:
        """The expression that does what :meth:`_splice` does with *parts*.

        Each part is ``(code, audio)``. The audio is only asked whether it is
        empty: `_splice` drops empty parts before joining, a seam with nothing
        on one side is not a seam, and the script has to join exactly the same
        pieces to lose exactly the same samples to the crossfades.
        """
        usable = [code for code, audio in parts if np.asarray(audio).size]
        if not usable:
            return "np.zeros(0, dtype=np.float32)"
        if len(usable) == 1:
            return usable[0]
        applied = self._crossfade_for(crossfade_ms)
        if applied <= 0:
            return f"np.concatenate([{', '.join(usable)}])"
        return f"kudio.splice([{', '.join(usable)}], sr, {applied / 1000.0!r})"

    def replace_selection(self, y_new: np.ndarray, *, undo_label: str,
                          crossfade: bool = False,
                          crossfade_ms: Optional[float] = None,
                          code: Optional[str] = None) -> None:
        """Splice *y_new* over the selection (or replace everything if none).

        *crossfade* defaults to **off**, and the distinction matters:

        * An **effect** replaces the selection with a processed version of
          *itself*. Blending the edges back into the unprocessed neighbours
          would quietly leave 5 ms at each end un-effected — you asked for
          that range to be filtered, not most of it.
        * A **structural** edit — paste, insert silence — joins audio that has
          nothing to do with its neighbours, and the step at the join is a
          click. Those pass ``crossfade=True``.

        *code* computes *y_new* from ``y`` and ``sr``, where ``y`` is the audio
        being replaced: the selection, or the whole clip. It is written for
        the selected audio alone, the way an effect sees it; the session adds
        the slicing and the join, since only the session knows which samples
        and which crossfade it used.
        """
        if not self.has_audio:
            return
        sel = self.selection()
        if sel is None:
            self.set_audio(y_new, self._sr, undo_label=undo_label,
                           keep_original=True, code=code)
            return
        sl = self.selected_slice()
        converted = np.asarray(y_new).dtype != np.float32
        y_new = np.asarray(y_new, dtype=np.float32)
        parts = [self._y[:sl.start], y_new, self._y[sl.stop:]]
        merged, lost = self._splice(parts, crossfade_ms) if crossfade \
            else (np.concatenate([p for p in parts if len(p)]), 0.0)
        delta = (len(y_new) - (sl.stop - sl.start)) / self._sr - lost
        script = self._selection_code(code, sl, parts, converted,
                                      crossfade_ms if crossfade else 0.0)
        # set_audio first: it snapshots the markers for undo, and the snapshot
        # has to be the positions from *before* this edit moved them
        self.set_audio(merged, self._sr, undo_label=undo_label,
                       keep_original=True, code=script)
        self._shift_markers(sel[1], delta)

    def _selection_code(self, code: Optional[str], sl: slice, parts,
                        converted: bool, crossfade_ms: Optional[float]
                        ) -> Optional[str]:
        """Wrap *code*, written for the selected audio, into a whole-clip step.

        The selection is bound to ``y`` while *code* runs and the result is
        joined back between what came before and after it, the same way
        :meth:`replace_selection` joined it. A replacement that never reads the
        selection -- a paste, a silence -- is joined in directly instead.
        """
        if code is None:
            return None
        a, b = sl.start, sl.stop
        head, new, tail = parts
        span = _span(a, b, self._sr)
        value = _assigned_value(code)
        if value is not None:
            if converted:
                value = f"np.asarray({value}, dtype=np.float32)"
            joined = self._splice_code(
                [(f"y[:{a}]", head), (value, new), (f"y[{b}:]", tail)],
                crossfade_ms)
            return f"y = {joined}  # over {span}"
        lines = [f"head, y, tail = y[:{a}], y[{a}:{b}], y[{b}:]  # {span}", code]
        if converted:
            lines.append("y = np.asarray(y, dtype=np.float32)")
        joined = self._splice_code([("head", head), ("y", new), ("tail", tail)],
                                   crossfade_ms)
        lines.append(f"y = {joined}")
        return "\n".join(lines)

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
        sl = self.selected_slice()
        self._clipboard = (np.array(self.selected_audio(), copy=True), self._sr)
        # Not an edit, so not a step -- but a later paste depends on it, and
        # by then the audio it was copied from may have changed or be gone
        self._copied = _Copied(self._origin, tuple(self._history),
                               sl.start, sl.stop)
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
        parts = [(f"y[:{sl.start}]", self._y[:sl.start]),
                 (f"y[{sl.stop}:]", self._y[sl.stop:])]
        merged, lost = self._splice([audio for _, audio in parts], crossfade_ms)
        code = (f"y = {self._splice_code(parts, crossfade_ms)}"
                f"  # {_span(sl.start, sl.stop, self._sr)}")
        self.set_audio(merged, self._sr, undo_label=undo_label,
                       keep_original=True, code=code)
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
        # `clip` in the script is the clipboard as copied, at its own rate;
        # the resampling happens here at every paste, so it does in the script
        clip_code = "clip"
        if clip_sr != self._sr:
            clip = kudio.resample(clip, clip_sr, self._sr)
            clip_code = f"kudio.resample(clip, {clip_sr}, sr)"
        if np.asarray(clip).dtype != np.float32:
            clip_code = f"np.asarray({clip_code}, dtype=np.float32)"
        clip = np.asarray(clip, dtype=np.float32)

        sel = self.selection()
        if sel is not None:
            start = sel[0]
            self.replace_selection(clip, undo_label="paste", crossfade=True,
                                   crossfade_ms=crossfade_ms,
                                   code=f"y = {clip_code}")
        else:
            position = self.duration if at is None else max(0.0, min(at, self.duration))
            index = int(position * self._sr)
            start = position
            parts = [(f"y[:{index}]", self._y[:index]), (clip_code, clip),
                     (f"y[{index}:]", self._y[index:])]
            merged, lost = self._splice([audio for _, audio in parts],
                                        crossfade_ms)
            code = (f"y = {self._splice_code(parts, crossfade_ms)}"
                    f"  # at {position:.3f} s")
            self.set_audio(merged, self._sr, undo_label="paste",
                           keep_original=True, code=code)
            self._shift_markers(position, len(clip) / self._sr - lost)
        # the step was just pushed and nothing refers to it yet, so replacing
        # it with one that knows its clipboard is safe
        self._history[-1] = replace(self._history[-1], copied=self._copied)
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
        silence_code = f"np.zeros({len(silence)}, dtype=np.float32)"
        sel = self.selection()
        if sel is not None:
            self.replace_selection(silence, undo_label="insert silence",
                                   crossfade=True, crossfade_ms=crossfade_ms,
                                   code=f"y = {silence_code}")
        else:
            position = self.duration if at is None else max(0.0, min(at, self.duration))
            index = int(position * self._sr)
            parts = [(f"y[:{index}]", self._y[:index]), (silence_code, silence),
                     (f"y[{index}:]", self._y[index:])]
            merged, lost = self._splice([audio for _, audio in parts],
                                        crossfade_ms)
            code = (f"y = {self._splice_code(parts, crossfade_ms)}"
                    f"  # at {position:.3f} s")
            self.set_audio(merged, self._sr, undo_label="insert silence",
                           keep_original=True, code=code)
            self._shift_markers(position, seconds - lost)
        self.message.emit(f"Inserted {seconds:.3f}s of silence"
                          f"{self._crossfade_note(2, crossfade_ms)}")
        return True

    def crop_to_selection(self) -> bool:
        """Throw away everything outside the selection."""
        if not self.can_copy():
            return False
        sel = self.selection()
        sl = self.selected_slice()
        kept = np.array(self.selected_audio(), copy=True)
        # set_audio first: it snapshots the markers for undo, and the snapshot
        # has to be the positions from *before* the crop moved and dropped them
        self.set_audio(kept, self._sr, undo_label="crop", keep_original=True,
                       code=f"y = y[{sl.start}:{sl.stop}]  "
                            f"# keep {_span(sl.start, sl.stop, self._sr)}")
        self._shift_markers(0.0, -sel[0], window=(sel[0], sel[1]))
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
        self._wrote(path)
        self.message.emit(f"Saved {path.name}")
        return path

    def save_selection(self, path, subtype: str = "PCM_16") -> Path:
        """Write only the selected range."""
        if not self.can_copy():
            raise RuntimeError("nothing selected")
        path = Path(path)
        selected = self.selected_audio()
        kudio.save_wave(path, selected, self._sr, subtype=subtype)
        self._wrote(path)
        self.message.emit(
            f"Saved {len(selected) / self._sr:.3f}s to {path.name}")
        return path

    def _wrote(self, path: Path) -> None:
        """Remember that *path* was written, in case it is a script's source.

        Save As suggests the file that was opened, and saving over it is an
        ordinary thing to do -- after which the script's SOURCE holds the
        edited audio. A changed modification time usually gives that away;
        this is for the save that lands in the same tick at the same size.
        """
        self._written.add(os.path.normcase(str(path.resolve())))

    # --------------------------------------------------------------- script

    def edit_script(self, output: Optional[str] = None, *,
                    subtype: str = "PCM_16") -> str:
        """The edit history as a Python script that makes the same audio again.

        The script reads the original the way :meth:`load_file` read it, runs
        each step's code in order under its undo label, and writes *output* --
        or the path given as its first argument -- with *subtype*, which
        defaults to what :meth:`save` writes. Undo takes a step back out;
        Revert and opening another file start the history again.

        A step it cannot reproduce is written in as a comment and a ``raise``,
        so running the script stops there rather than writing a file that is
        not what was heard. :meth:`edit_script_warnings` says which.
        """
        return self._script(output, subtype)[0]

    def edit_script_warnings(self) -> List[str]:
        """Why :meth:`edit_script` would not reproduce the audio; empty when it would."""
        return self._script(None, "PCM_16")[1]

    def _script(self, output: Optional[str], subtype: str
                ) -> Tuple[str, List[str]]:
        if self._origin is None or not self.has_audio:
            raise RuntimeError("nothing loaded")
        origin = self._origin
        name = origin.path.name if origin.path is not None \
            else (self._name or "recording")
        stem = Path(name).stem
        body, problems = self._script_body(origin, tuple(self._history),
                                           "SOURCE")
        if origin.path is None:
            source = f"{stem}.wav"
            problems.insert(0, "this audio was recorded, not opened from a "
                               "file, so SOURCE is a placeholder: point it at "
                               "the take as it was recorded")
        else:
            source = str(origin.path)
        checks = any(line.lstrip().startswith(f"if {_DIGEST_CALL}")
                     for line in body)

        lines = [
            "# -*- coding: utf-8 -*-",
            f"# {_comment_safe(name)}, edited in KudioStudio {__version__} "
            f"(kudio {kudio.__version__}).",
            "#",
            "# Each numbered step is one entry of the undo history, oldest first,",
            "# written as the code that made it. Run this to make the same audio",
            "# again; the output path can be given as the first argument.",
            "#",
            f"# Sample positions are at {origin.rate} Hz, the rate the edits "
            f"were made at.",
        ]
        if problems:
            lines += ["#", "# WARNING: this script does not reproduce the edit "
                           "as it stands:"]
            lines += [f"#   - {_comment_safe(problem)}" for problem in problems]
        lines += [
            "",
            *(["import hashlib"] if checks else []),
            "import sys",
            "",
            "import numpy as np",
            "",
            "import kudio",
            "",
            f"SOURCE = {source!r}",
            f"OUTPUT = sys.argv[1] if len(sys.argv) > 1 "
            f"else {output or f'{stem}_edited.wav'!r}",
            "",
            *((_DIGEST_FUNCTION + ["", ""]) if checks else []),
            *body,
            "",
            f"kudio.save_wave(OUTPUT, y, sr, subtype={subtype!r})",
            'print(f"wrote {OUTPUT}: {len(y) / sr:.3f} s at {sr} Hz")',
        ]
        return "\n".join(lines) + "\n", problems

    def _script_body(self, origin: _Origin, steps: Tuple[EditStep, ...],
                     source: str) -> Tuple[List[str], List[str]]:
        """Lines that read *origin* from *source* and replay *steps*, and a
        sentence for everything they cannot reproduce.

        A copy that a paste needs is taken at the point it was made, when that
        point is on the way to this result. When it is not -- the copy was made
        before an edit that has since been undone, or in another file -- the
        audio it came from is rebuilt in a function of its own instead.
        """
        lines = self._load_lines(origin, source)
        problems: List[str] = []
        changed = self._changed_since_read(origin)
        if changed is not None:
            problems.append(changed)
        inline: Dict[int, _Copied] = {}       # by the number of steps before it
        for step in steps:
            copied = step.copied
            if (copied is not None and copied.origin == origin
                    and len(copied.steps) < len(steps)
                    and all(a is b for a, b in zip(copied.steps, steps))):
                inline.setdefault(len(copied.steps), copied)

        for number, step in enumerate(steps, 1):
            copied = inline.get(number - 1)
            if copied is not None:
                users = [str(n) for n, s in enumerate(steps, 1)
                         if s.copied is copied]
                lines += ["", f"clip = y[{copied.start}:{copied.stop}].copy()"
                              f"  # pasted in step{'s' if len(users) > 1 else ''}"
                              f" {', '.join(users)}"]
            needed = step.copied
            if needed is not None and inline.get(len(needed.steps)) is not needed:
                block, trouble = self._rebuild_clip(needed)
                lines += [""] + block
                problems += [f"step {number} ({step.label}): {t}" for t in trouble]

            lines += ["", f"# {number}. {_comment_safe(step.label)}"]
            if step.code is None:
                problem = f"step {number} ({step.label}) was not recorded as code"
                lines += ["# KudioStudio has no code for this step, so the "
                          "script stops here",
                          "# rather than write audio without it.",
                          f"raise NotImplementedError({problem!r})"]
                problems.append(problem)
            else:
                lines += step.code.splitlines()
        return lines, problems

    @staticmethod
    def _load_lines(origin: _Origin, source: str) -> List[str]:
        """Read *source* the way :meth:`load_file` read the original.

        The file is checked first: every edit below was made on those bytes,
        and run on a later file at the same path -- the edited audio itself,
        saved over the original -- they would be applied twice, without a
        word. With the file's own rate, the script also checks it is still
        the rate the edits were made at: every position in it is a sample
        index, and the same index at another rate is another moment.
        """
        lines: List[str] = []
        if origin.digest is not None:
            why = ("is not the file these edits were made on: it has changed "
                   "since. Point the script at a copy of the original, or "
                   "delete this check to run the edits on it anyway.")
            # the name as SOURCE says it, so a SOURCE pointed elsewhere is
            # the file the message names
            message = (f'f"{{{source}}} {why}"' if source.isidentifier()
                       else repr(f"{origin.path} {why}"))
            lines += [f"if {_DIGEST_CALL}{source}) != {origin.digest!r}:",
                      f"    sys.exit({message})"]
        if origin.requested_sr is not None:
            return lines + [f"y, sr = kudio.file_load({source}, "
                            f"sr={origin.requested_sr}, mono={origin.mono})"]
        rate = origin.rate
        return lines + [
            f"y, sr = kudio.file_load({source}, sr=None, mono={origin.mono})",
            f"if sr != {rate}:",
            f"    # every sample position below was counted at {rate} Hz",
            f"    y, sr = kudio.resample(y, sr, {rate}), {rate}"]

    def _changed_since_read(self, origin: _Origin) -> Optional[str]:
        """Why *origin*'s file no longer holds what the edits were made on.

        ``None`` when it still does -- or when there is no file to ask about.
        The size and time are looked at first; the bytes are read again only
        when those moved, or when Save As wrote to that path.
        """
        if origin.path is None or origin.digest is None:
            return None
        name = origin.path.name
        now = _stat(origin.path)
        if now is None:
            return (f"{name} is gone, and the script reads the edits' "
                    f"starting point from it")
        overwritten = os.path.normcase(str(origin.path.resolve())) \
            in self._written
        if now == origin.stat and not overwritten:
            return None
        try:
            if file_digest(origin.path) == origin.digest:
                return None
        except OSError as e:
            return f"{name} cannot be read back — {e}"
        if overwritten:
            return (f"{name} was saved over and now holds the edited audio, "
                    f"so the script would apply every edit a second time; "
                    f"it stops instead. Point it at a copy of the original")
        return (f"{name} has changed on disk since it was opened, so the "
                f"script stops rather than edit another file; point it at "
                f"a copy of the original")

    def _rebuild_clip(self, copied: _Copied) -> Tuple[List[str], List[str]]:
        """``clip = ...`` for a copy made from audio this history does not
        pass through: rebuild that audio in a function, then copy from it."""
        origin = copied.origin
        if origin is None or origin.path is None:
            return (["# The clipboard was copied from a recording, which was "
                     "never a file,",
                     "# so there is nothing to rebuild it from.",
                     "raise NotImplementedError('the clipboard pasted here "
                     "came from a recording')"],
                    ["the clipboard came from a recording, which the script "
                     "cannot read back"])
        body, problems = self._script_body(origin, copied.steps,
                                           repr(str(origin.path)))
        lines = ["# The clipboard was copied from audio this history no "
                 "longer passes through",
                 "# (another file, or edits since undone), so rebuild that "
                 "audio first.",
                 "def copied():"]
        lines += [f"    {line}" if line else "" for line in body]
        lines += [f"    return y[{copied.start}:{copied.stop}].copy()",
                  "", "", "clip = copied()"]
        return lines, problems
