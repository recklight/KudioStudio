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
from kudiostudio.theme import CALL_COLOR, HINT_COLOR, MONO_CSS
from kudiostudio.workers import WorkerHost

log = logging.getLogger(__name__)


class Panel(QWidget):
    """A tool panel bound to the shared :class:`AudioSession`.

    Panels talk to the session and to the status bar, never to each other.
    """

    #: one-line note for the status bar
    status = Signal(str)

    #: audio to audition -- ``(waveform, sample_rate)`` -- without touching the
    #: session. This is how a panel says "here is what it would sound like".
    preview_ready = Signal(object, int)

    #: the panel wants auditioning to stop
    preview_stopped = Signal()

    #: a live take has begun at this sample rate -- start a rolling display
    monitor_started = Signal(int)

    #: audio that has just arrived -- ``(waveform, sample_rate)`` -- with no
    #: playback and no session change. What a recording looks like while it
    #: happens.
    #:
    #: **Each sample is emitted exactly once.** The view accumulates them, so
    #: a panel must send `StreamRecorder.drain()` and never `tail()`: an
    #: overlapping window would draw the same audio twice, at two different
    #: times, and any per-column analysis downstream would compute it twice.
    monitor_chunk = Signal(object, int)

    #: the live take is over; the rolling display can be released
    monitor_stopped = Signal()

    #: a curve to draw over the time-frequency view -- ``(times, hz)``, in
    #: seconds on the clip's own timeline and in Hz. NaN means "nothing here",
    #: and the view leaves a gap rather than joining across it.
    overlay_ready = Signal(object, object)

    #: take the curve back down
    overlay_cleared = Signal()

    #: a file the panel wants opened the way File → Open opens one: into the
    #: file list and the session. A panel could call ``session.load_file``
    #: itself, but then the file would be missing from the list and the
    #: window would not remember its folder -- opening is the window's job.
    open_requested = Signal(str)

    #: tab caption
    title = "Panel"

    #: its button on the tool rail, a name from `kudiostudio.icons`,
    #: or empty for a text-only button
    icon = ""

    def __init__(self, session: AudioSession, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.jobs = WorkerHost(self)

    # -------------------------------------------------------------- helpers

    def cancel_preview(self) -> None:
        """The window stopped auditioning. Drop any live-preview state.

        Called when something else claims playback, so a panel's Preview button
        never claims to be running when it is not. Override if the panel has
        preview state; the default is for panels that have none.
        """

    def shutdown(self) -> None:
        """Release anything the panel is holding open. Override as needed.

        Called once when the window closes. A worker thread is already handled
        by ``jobs.wait``; this is for devices, which stay open until told.
        """

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
        label.setStyleSheet(f"color: {CALL_COLOR}; {MONO_CSS}")
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
