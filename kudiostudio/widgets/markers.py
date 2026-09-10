# -*- coding: utf-8 -*-
"""The marker list — named points and ranges you can jump back to.

A selection is a single, unnamed, transient thing: making a second one loses
the first. Anything that takes more than one pass over a recording — "the noise
starts here", "this take is the good one", "check this click" — needs somewhere
to put that knowledge, and re-finding it by dragging is how it gets lost.

The list is a view onto `AudioSession.markers`; it owns none of the state.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import kudio

log = logging.getLogger(__name__)


def format_span(start: float, end: float) -> str:
    """``0:12.500`` for a point, ``0:12.500 – 0:14.000`` for a range."""
    def one(seconds: float) -> str:
        minutes, rest = divmod(max(0.0, seconds), 60.0)
        return f"{int(minutes)}:{rest:06.3f}"
    return one(start) if end <= start else f"{one(start)} – {one(end)}"


class MarkerList(QWidget):
    """List of markers with jump / select / rename / remove."""

    #: move the playhead here
    seek_requested = Signal(float)
    #: select this range (ranges only)
    select_requested = Signal(float, float)
    #: something to put in the status bar
    status = Signal(str)

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setAlternatingRowColors(True)

        self.add_button = QPushButton("＋ At playhead")
        self.mark_button = QPushButton("＋ Selection")
        self.vad_button = QPushButton("Find speech")
        self.vad_button.setToolTip(
            "kudio.vad — mark every span that contains speech")
        self.rename_button = QPushButton("Rename")
        self.remove_button = QPushButton("Remove")
        self.clear_button = QPushButton("Clear")
        self.save_button = QPushButton("Save")
        self.save_button.setToolTip(
            "Write them beside the audio as clip.wav.labels.json, which is "
            "picked up automatically next time the file is opened.")
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip(
            "Write a .txt Audacity label track, or a .json file elsewhere.")
        self.import_button = QPushButton("Import…")

        top = QHBoxLayout()
        top.addWidget(self.add_button)
        top.addWidget(self.mark_button)
        middle = QHBoxLayout()
        middle.addWidget(self.rename_button)
        middle.addWidget(self.remove_button)
        middle.addWidget(self.clear_button)
        files = QHBoxLayout()
        files.addWidget(self.save_button)
        files.addWidget(self.export_button)
        files.addWidget(self.import_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.vad_button)
        layout.addWidget(self.list, stretch=1)
        layout.addLayout(middle)
        layout.addLayout(files)

        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        self.add_button.clicked.connect(self._add_at_playhead)
        self.mark_button.clicked.connect(self._mark_selection)
        self.vad_button.clicked.connect(self._from_vad)
        self.rename_button.clicked.connect(self._rename)
        self.remove_button.clicked.connect(self._remove)
        self.clear_button.clicked.connect(self._clear)
        self.save_button.clicked.connect(self._save)
        self.export_button.clicked.connect(self._export)
        self.import_button.clicked.connect(self._import)

        self.session.markers_changed.connect(self.refresh)
        self.session.changed.connect(self._sync_enabled)
        self.session.selection_changed.connect(lambda *_: self._sync_enabled())
        self._playhead = 0.0
        self.refresh()

    # -------------------------------------------------------------------- ui

    def set_playhead(self, seconds: float) -> None:
        """Where "add at playhead" will put the next marker."""
        self._playhead = float(seconds)

    def refresh(self) -> None:
        self.list.clear()
        for n, marker in enumerate(self.session.markers, 1):
            label = marker.label or f"marker {n}"
            item = QListWidgetItem(f"{format_span(marker.start, marker.end)}   {label}")
            item.setToolTip("Click to jump here, double-click to select the range"
                            if marker.is_range else "Click to jump here")
            self.list.addItem(item)
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        has_audio = self.session.has_audio
        has_markers = bool(self.session.markers)
        selected = self.list.currentRow() >= 0
        self.add_button.setEnabled(has_audio)
        self.mark_button.setEnabled(self.session.selection() is not None)
        self.vad_button.setEnabled(has_audio)
        self.clear_button.setEnabled(has_markers)
        self.rename_button.setEnabled(has_markers and selected)
        self.remove_button.setEnabled(has_markers and selected)
        self.save_button.setEnabled(has_markers and self.session.path is not None)
        self.export_button.setEnabled(has_markers)
        self.import_button.setEnabled(has_audio)

    # ---------------------------------------------------------------- actions

    def _current(self):
        row = self.list.currentRow()
        markers = self.session.markers
        return (row, markers[row]) if 0 <= row < len(markers) else (row, None)

    def _on_clicked(self, _item) -> None:
        _, marker = self._current()
        self._sync_enabled()
        if marker is not None:
            self.seek_requested.emit(marker.start)

    def _on_double_clicked(self, _item) -> None:
        _, marker = self._current()
        if marker is not None and marker.is_range:
            self.select_requested.emit(marker.start, marker.end)

    def _add_at_playhead(self) -> None:
        if not self.session.has_audio:
            return
        self.session.add_marker(self._playhead)
        self.status.emit(f"Marker at {format_span(self._playhead, self._playhead)}")

    def _mark_selection(self) -> None:
        marker = self.session.mark_selection()
        if marker is None:
            self.status.emit("Select a range first.")
            return
        self.status.emit(f"Marked {format_span(marker.start, marker.end)}")

    def _from_vad(self) -> None:
        """Replace the list with what kudio.vad finds.

        Replaces rather than appends: run twice with a different threshold and
        a merged list would be two answers laid on top of each other.
        """
        if not self.session.has_audio:
            return
        from kudiostudio.session import Marker
        try:
            spans = kudio.vad(self.session.y, self.session.sr)
        except Exception as e:
            self.status.emit(f"vad failed — {type(e).__name__}: {e}")
            return
        if not spans:
            self.status.emit("kudio.vad found no speech in this clip.")
            return
        self.session.set_markers([
            Marker(start=a, end=b, label=f"speech {n}")
            for n, (a, b) in enumerate(spans, 1)])
        total = sum(b - a for a, b in spans)
        self.status.emit(
            f"kudio.vad — {len(spans)} span(s), {total:.2f}s of speech "
            f"({total / max(self.session.duration, 1e-9) * 100:.0f}%)")

    def _rename(self) -> None:
        row, marker = self._current()
        if marker is None:
            return
        label, ok = QInputDialog.getText(self, "Rename marker", "Label:",
                                         text=marker.label)
        if ok:
            self.session.rename_marker(row, label)

    def _remove(self) -> None:
        row, marker = self._current()
        if marker is not None:
            self.session.remove_marker(row)

    def _clear(self) -> None:
        self.session.clear_markers()
        self.status.emit("Markers cleared.")

    # ------------------------------------------------------------ persistence

    def _save(self) -> None:
        """Write beside the audio, where opening the file will find them."""
        if self.session.path is None:
            self.status.emit(
                "This audio has no file to sit beside — use Export… instead.")
            return
        try:
            self.session.save_markers()
        except Exception as e:                              # noqa: BLE001
            self.status.emit(f"Could not save markers — {type(e).__name__}: {e}")

    def _export(self) -> None:
        stem = Path(self.session.name or "markers").stem
        path, _ = QFileDialog.getSaveFileName(
            self, "Export markers", f"{stem}.txt",
            "Audacity labels (*.txt);;JSON (*.json)")
        if not path:
            return
        try:
            self.session.save_markers(path)
        except Exception as e:                              # noqa: BLE001
            self.status.emit(f"Export failed — {type(e).__name__}: {e}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import markers", "",
            "Labels (*.txt *.tsv *.json);;All files (*)")
        if not path:
            return
        try:
            found = self.session.load_markers(path)
        except Exception as e:                              # noqa: BLE001
            self.status.emit(f"Import failed — {type(e).__name__}: {e}")
            return
        self.status.emit(f"Imported {found} marker(s) from {Path(path).name}")
