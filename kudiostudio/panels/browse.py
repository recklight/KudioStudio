# -*- coding: utf-8 -*-
"""The working set of files, shown as a list you can load from."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import kudio

log = logging.getLogger(__name__)

AUDIO_FILTER = "Audio (*.wav *.flac *.mp3 *.ogg *.aiff);;All files (*)"


class FileList(QWidget):
    """Add files or a whole folder, then double-click to load one."""

    file_activated = Signal(str)
    status = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setAlternatingRowColors(True)

        add_files = QPushButton("Add files…")
        add_folder = QPushButton("Add folder…")
        remove = QPushButton("Remove")
        clear = QPushButton("Clear")

        buttons = QHBoxLayout()
        for b in (add_files, add_folder, remove, clear):
            buttons.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(buttons)
        layout.addWidget(self.list, stretch=1)

        add_files.clicked.connect(self._add_files)
        add_folder.clicked.connect(self._add_folder)
        remove.clicked.connect(self._remove_selected)
        clear.clicked.connect(self.list.clear)
        self.list.itemActivated.connect(self._activate)
        self.list.itemDoubleClicked.connect(self._activate)

    # ---------------------------------------------------------------- items

    def add(self, paths: Iterable) -> int:
        existing = {self.list.item(i).data(Qt.UserRole)
                    for i in range(self.list.count())}
        added = 0
        for path in paths:
            path = str(Path(path).resolve())
            if path in existing:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.list.addItem(item)
            existing.add(path)
            added += 1
        return added

    def paths(self) -> List[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add audio files", "",
                                                AUDIO_FILTER)
        if paths:
            self.status.emit(f"Added {self.add(paths)} file(s).")

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a folder of audio")
        if not folder:
            return
        try:
            files, _ = kudio.check_input(folder)
        except Exception as e:
            self.status.emit(f"Could not scan {folder} — {e}")
            return
        if not files:
            self.status.emit(f"No audio files found in {folder}.")
            return
        self.status.emit(f"Added {self.add(files)} file(s) from {Path(folder).name}.")

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _activate(self, item: QListWidgetItem) -> None:
        self.file_activated.emit(item.data(Qt.UserRole))
