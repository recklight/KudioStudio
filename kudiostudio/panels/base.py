# -*- coding: utf-8 -*-
"""Shared plumbing for the tool panels."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from kudiostudio.session import AudioSession
from kudiostudio.theme import CALL_COLOR, HINT_COLOR
from kudiostudio.workers import WorkerHost

log = logging.getLogger(__name__)


class Panel(QWidget):
    """A tool panel bound to the shared :class:`AudioSession`.

    Panels talk to the session and to the status bar, never to each other.
    """

    #: one-line note for the status bar
    status = Signal(str)

    #: tab caption
    title = "Panel"

    def __init__(self, session: AudioSession, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.jobs = WorkerHost(self)

    # -------------------------------------------------------------- helpers

    def require_audio(self) -> bool:
        """Guard for actions that need a loaded clip."""
        if self.session.has_audio:
            return True
        self.status.emit("Load an audio file first.")
        return False

    def report(self, message: str) -> None:
        self.status.emit(message)

    def on_session_changed(self) -> None:
        """Called whenever the current audio changes. Override as needed."""

    @staticmethod
    def call_label(text: str = "") -> QLabel:
        """A monospace label showing the equivalent `kudio` call."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")
        return label

    @staticmethod
    def hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {HINT_COLOR};")
        return label


class DirPicker(QWidget):
    """A read/write path field with a folder browser next to it."""

    def __init__(self, placeholder: str, caption: str = "Select a folder",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._caption = caption
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.button = QPushButton("…")
        self.button.setFixedWidth(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        layout.addWidget(self.button)
        self.button.clicked.connect(self._browse)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self._caption,
                                                self.edit.text())
        if path:
            self.edit.setText(path)

    def path(self) -> str:
        return self.edit.text().strip()
