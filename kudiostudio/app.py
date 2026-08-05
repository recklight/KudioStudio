# -*- coding: utf-8 -*-
"""The main window: file list, analysis views, transport and tool panels."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio import __version__
from kudiostudio.panels import PANEL_CLASSES, FileList
from kudiostudio.panels.browse import AUDIO_FILTER
from kudiostudio.session import AudioSession
from kudiostudio.widgets import AnalysisView, Player, TransportBar

log = logging.getLogger(__name__)


class KudioStudio(QMainWindow):
    """Desktop workbench for the `kudio` audio toolkit."""

    def __init__(self, reset: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"KudioStudio {__version__}")
        self.resize(1280, 800)

        self.settings = QSettings()
        if reset:
            self.settings.clear()

        self.session = AudioSession(self)
        self.player = Player(self)

        self._build_central()
        self._build_docks()
        self._build_actions()
        self._connect()

        self.statusBar().showMessage(
            f"KudioStudio {__version__} — kudio {kudio.__version__} ready.")
        self._restore_settings()
        self._sync_enabled()

    # -------------------------------------------------------------- assembly

    def _build_central(self) -> None:
        self.analysis = AnalysisView(self)
        self.transport = TransportBar(self)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.addWidget(self.analysis, stretch=1)
        layout.addWidget(self.transport)
        self.setCentralWidget(central)

    def _build_docks(self) -> None:
        self.files = FileList(self)
        files_dock = QDockWidget("Files", self)
        files_dock.setObjectName("filesDock")
        files_dock.setWidget(self.files)
        self.addDockWidget(Qt.LeftDockWidgetArea, files_dock)
        self.files_dock = files_dock

        self.tools = QTabWidget()
        self.panels = []
        for cls in PANEL_CLASSES:
            panel = cls(self.session, self)
            panel.status.connect(self.show_message)
            self.tools.addTab(panel, panel.title)
            self.panels.append(panel)

        tools_dock = QDockWidget("Tools", self)
        tools_dock.setObjectName("toolsDock")
        tools_dock.setWidget(self.tools)
        self.addDockWidget(Qt.RightDockWidgetArea, tools_dock)
        self.tools_dock = tools_dock
        self.resizeDocks([files_dock, tools_dock], [240, 380], Qt.Horizontal)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = self._action("&Open…", QKeySequence.Open, self.open_dialog)
        self.save_action = self._action("Save &As…", QKeySequence.SaveAs, self.save_dialog)
        quit_action = self._action("&Quit", QKeySequence.Quit, self.close)
        file_menu.addActions([self.open_action, self.save_action])
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        self.undo_action = self._action("&Undo", QKeySequence.Undo,
                                        lambda: self.session.undo())
        self.revert_action = self._action("&Revert to original", None,
                                          lambda: self.session.revert())
        self.select_all_action = self._action("Select &All", QKeySequence.SelectAll,
                                              self.analysis.waveform.select_all)
        self.clear_selection_action = self._action("&Clear selection", "Esc",
                                                   self.analysis.waveform.clear_selection)
        edit_menu.addActions([self.undo_action, self.revert_action])
        edit_menu.addSeparator()
        edit_menu.addActions([self.select_all_action, self.clear_selection_action])

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.files_dock.toggleViewAction())
        view_menu.addAction(self.tools_dock.toggleViewAction())

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("&About", None, self.about))

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("mainToolBar")
        toolbar.addActions([self.open_action, self.save_action])
        toolbar.addSeparator()
        toolbar.addActions([self.undo_action, self.revert_action])

    def _action(self, text: str, shortcut, slot) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _connect(self) -> None:
        self.session.changed.connect(self._on_session_changed)
        self.session.message.connect(self.show_message)
        self.session.selection_changed.connect(self.analysis.set_selection)
        self.analysis.selection_changed.connect(self.session.set_selection)

        self.files.file_activated.connect(self.load_file)
        self.files.status.connect(self.show_message)

        self.transport.play_requested.connect(self._play)
        self.transport.stop_requested.connect(self.player.stop)
        self.player.failed.connect(self.show_message)

    # ------------------------------------------------------------------ files

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open audio", self._last_dir(), AUDIO_FILTER)
        if paths:
            self.open_files(paths)

    def open_files(self, paths: Iterable) -> None:
        paths: List[str] = [str(p) for p in paths]
        self.files.add(paths)
        self.load_file(paths[0])

    def load_file(self, path: str) -> None:
        try:
            self.session.load_file(path)
        except Exception as e:
            self.show_message(f"Could not open {Path(path).name} — "
                              f"{type(e).__name__}: {e}")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))

    def save_dialog(self) -> None:
        if not self.session.has_audio:
            self.show_message("Nothing to save.")
            return
        suggested = Path(self._last_dir()) / (self.session.name or "audio.wav")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save audio as", str(Path(suggested).with_suffix(".wav")),
            "WAV (*.wav)")
        if not path:
            return
        try:
            self.session.save(path)
        except Exception as e:
            self.show_message(f"Save failed — {type(e).__name__}: {e}")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", str(Path.home())))

    # ------------------------------------------------------------- playback

    def _play(self, selection_only: bool) -> None:
        if not self.session.has_audio:
            return
        y = (self.session.selected_audio() if selection_only
             else self.session.y)
        self.player.play(y, self.session.sr, self.transport.gain_db)

    # --------------------------------------------------------------- session

    def _on_session_changed(self) -> None:
        self.analysis.set_audio(self.session.y, self.session.sr)
        for panel in self.panels:
            panel.on_session_changed()
        self._sync_enabled()
        if self.session.has_audio:
            self.transport.set_info(
                f"{self.session.name}  ·  {self.session.duration:.2f}s  ·  "
                f"{self.session.sr} Hz  ·  {len(self.session.y)} samples")
            self.setWindowTitle(f"{self.session.name} — KudioStudio {__version__}")
        else:
            self.transport.set_info("no audio loaded")
            self.setWindowTitle(f"KudioStudio {__version__}")

    def _sync_enabled(self) -> None:
        has_audio = self.session.has_audio
        self.transport.set_enabled_for(has_audio)
        self.save_action.setEnabled(has_audio)
        self.select_all_action.setEnabled(has_audio)
        self.clear_selection_action.setEnabled(has_audio)
        self.undo_action.setEnabled(self.session.can_undo())
        self.revert_action.setEnabled(self.session.original() is not None)

    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 10_000)
        log.info("%s", text)

    def about(self) -> None:
        QMessageBox.about(
            self, "About KudioStudio",
            f"<b>KudioStudio {__version__}</b><br>"
            f"A desktop workbench for the "
            f"<a href='https://pypi.org/project/kudio/'>kudio</a> audio toolkit."
            f"<br><br>kudio {kudio.__version__} · MIT licence")

    # -------------------------------------------------------------- settings

    def _restore_settings(self) -> None:
        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt hook
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self.player.shutdown()
        for panel in self.panels:
            panel.jobs.wait(2000)
        super().closeEvent(event)
