# -*- coding: utf-8 -*-
"""The single piece of audio every panel reads from and writes to.

`AudioSession` owns the current waveform, its sample rate, an undo stack and
the time selection. Panels never talk to each other -- they mutate the session
and the views repaint on :attr:`AudioSession.changed`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QObject, Signal

import kudio

log = logging.getLogger(__name__)

#: Nothing deeper is worth keeping in RAM for an interactive editor.
UNDO_DEPTH = 20


class AudioSession(QObject):
    """Current audio + edit history, shared by the whole application."""

    #: waveform or sample rate replaced (panels should repaint)
    changed = Signal()
    #: time selection changed, in seconds; (0, 0) means "no selection"
    selection_changed = Signal(float, float)
    #: human-readable note for the status bar
    message = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._y: Optional[np.ndarray] = None
        self._sr: int = 0
        self._name: str = ""
        self._path: Optional[Path] = None
        self._undo: List[Tuple[np.ndarray, int, str]] = []
        self._original: Optional[Tuple[np.ndarray, int]] = None
        self._sel: Tuple[float, float] = (0.0, 0.0)

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
        self._apply(y, int(rate), path.name)
        self.message.emit(f"Loaded {path.name} — {self.duration:.2f}s @ {rate} Hz")

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
        self._apply(y, sr, name or self._name)

    def _apply(self, y: np.ndarray, sr: int, name: str) -> None:
        y = np.asarray(y)
        if y.dtype != np.float32:
            y = y.astype(np.float32)
        self._y, self._sr, self._name = y, int(sr), name
        self.clear_selection()
        self.changed.emit()

    # ----------------------------------------------------------------- undo

    def _push_undo(self, label: str) -> None:
        self._undo.append((np.array(self._y, copy=True), self._sr, label))
        del self._undo[:-UNDO_DEPTH]

    def undo(self) -> bool:
        if not self._undo:
            return False
        y, sr, label = self._undo.pop()
        self._apply(y, sr, self._name)
        self.message.emit(f"Undo: {label}")
        return True

    def revert(self) -> bool:
        """Drop every edit and go back to the audio as it was loaded."""
        if self._original is None:
            return False
        y, sr = self._original
        self._undo.clear()
        self._apply(np.array(y, copy=True), sr, self._name)
        self.message.emit("Reverted to the original audio")
        return True

    def original(self) -> Optional[Tuple[np.ndarray, int]]:
        """The unedited audio, for A/B comparison and metrics."""
        return self._original

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

    def replace_selection(self, y_new: np.ndarray, *, undo_label: str) -> None:
        """Splice *y_new* over the selection (or replace everything if none)."""
        if not self.has_audio:
            return
        sel = self.selection()
        if sel is None:
            self.set_audio(y_new, self._sr, undo_label=undo_label, keep_original=True)
            return
        sl = self.selected_slice()
        merged = np.concatenate([self._y[:sl.start],
                                 np.asarray(y_new, dtype=np.float32),
                                 self._y[sl.stop:]])
        self.set_audio(merged, self._sr, undo_label=undo_label, keep_original=True)

    # ----------------------------------------------------------------- save

    def save(self, path, subtype: str = "PCM_16") -> Path:
        if not self.has_audio:
            raise RuntimeError("nothing to save")
        path = Path(path)
        kudio.save_wave(path, self._y, self._sr, subtype=subtype)
        self.message.emit(f"Saved {path.name}")
        return path
