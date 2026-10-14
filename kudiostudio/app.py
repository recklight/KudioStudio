# -*- coding: utf-8 -*-
"""The main window: file list, analysis views, transport and tool panels."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio import __version__
from kudiostudio.panels import PANEL_CLASSES, FileList, ManifestPanel
from kudiostudio.panels.browse import AUDIO_FILTER
from kudiostudio.panels.manifest import MANIFEST_FILTER, is_manifest
from kudiostudio.session import AudioSession
from kudiostudio.widgets import AnalysisView, MarkerList, Player, TransportBar
from kudiostudio.workers import WorkerHost, collect_on_gui_thread

log = logging.getLogger(__name__)


class KudioStudio(QMainWindow):
    """Desktop workbench for the `kudio` audio toolkit."""

    def __init__(self, reset: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # before any job can run: a widget collected on a worker's thread
        # is destroyed there (see kudiostudio.workers)
        collect_on_gui_thread()
        self.setWindowTitle(f"KudioStudio {__version__}")
        self.resize(1280, 800)

        self.settings = QSettings()
        if reset:
            self.settings.clear()

        self.session = AudioSession(self)
        self.player = Player(self)
        #: where playback starts from when nothing is selected, in seconds
        self._playhead = 0.0
        #: timeline range handed to the player last, for A/B and click-to-seek
        self._segment = (0.0, 0.0)
        #: audio a panel asked us to audition, looping until something else
        #: claims playback; ``None`` when not previewing
        self._preview: Optional[Tuple[np.ndarray, int, float]] = None
        #: repeat ordinary playback (Preview always loops, regardless)
        self._loop = False
        #: Quit was asked for while a job ran; the window closes when they end
        self._quitting = False

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

        self.markers = MarkerList(self.session, self)
        markers_dock = QDockWidget("Markers", self)
        markers_dock.setObjectName("markersDock")
        markers_dock.setWidget(self.markers)
        self.addDockWidget(Qt.LeftDockWidgetArea, markers_dock)
        self.markers_dock = markers_dock

        self.tools = QTabWidget()
        self.panels = []
        for cls in PANEL_CLASSES:
            panel = cls(self.session, self)
            panel.status.connect(self.show_message)
            panel.preview_ready.connect(self._start_preview)
            panel.preview_stopped.connect(self._stop)
            panel.monitor_started.connect(self.analysis.start_monitor)
            panel.monitor_chunk.connect(self.analysis.feed_monitor)
            panel.monitor_stopped.connect(self.analysis.stop_monitor)
            panel.overlay_ready.connect(self.analysis.set_pitch_overlay)
            panel.overlay_cleared.connect(self.analysis.clear_pitch_overlay)
            panel.open_requested.connect(self._open_requested)
            self.tools.addTab(panel, panel.title)
            self.panels.append(panel)
        self.manifest_panel = next(p for p in self.panels
                                   if isinstance(p, ManifestPanel))

        tools_dock = QDockWidget("Tools", self)
        tools_dock.setObjectName("toolsDock")
        tools_dock.setWidget(self.tools)
        self.addDockWidget(Qt.RightDockWidgetArea, tools_dock)
        self.tools_dock = tools_dock
        self.resizeDocks([files_dock, tools_dock], [240, 380], Qt.Horizontal)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = self._action("&Open…", QKeySequence.Open, self.open_dialog)
        self.open_manifest_action = self._action(
            "Open &manifest…", None, self.open_manifest_dialog)
        self.save_action = self._action("Save &As…", QKeySequence.SaveAs, self.save_dialog)
        self.save_selection_action = self._action(
            "Save &selection as…", "Ctrl+Shift+E", self.save_selection_dialog)
        self.export_image_action = self._action(
            "&Export analysis image…", None, self.export_image_dialog)
        self.export_script_action = self._action(
            "Export edits as &Python script…", None, self.export_script_dialog)
        quit_action = self._action("&Quit", QKeySequence.Quit, self.close)
        file_menu.addActions([self.open_action, self.open_manifest_action,
                              self.save_action, self.save_selection_action])
        file_menu.addSeparator()
        file_menu.addActions([self.export_image_action,
                              self.export_script_action])
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        self.undo_action = self._action("&Undo", QKeySequence.Undo,
                                        lambda: self.session.undo())
        self.revert_action = self._action("&Revert to original", None,
                                          lambda: self.session.revert())
        # Cut/Copy/Paste/Delete are handled in keyPressEvent for the same
        # reason as Space and Home: as window shortcuts they would take Ctrl+C
        # away from every path field in the tool panels. The "\t" still shows
        # the key in the menu.
        self.cut_action = self._action("Cu&t\tCtrl+X", None, self._cut)
        self.copy_action = self._action("&Copy\tCtrl+C", None, self._copy)
        self.paste_action = self._action("&Paste\tCtrl+V", None, self._paste)
        self.delete_action = self._action("&Delete\tDel", None, self._delete)
        self.crop_action = self._action("Crop to selectio&n", "Ctrl+T",
                                        lambda: self.session.crop_to_selection())
        self.silence_action = self._action("&Insert silence…", None,
                                           self.insert_silence_dialog)
        self.crossfade_action = self._action("Splice cross&fade…", None,
                                             self.crossfade_dialog)
        self.select_all_action = self._action("Select &All", QKeySequence.SelectAll,
                                              self.analysis.waveform.select_all)
        self.clear_selection_action = self._action("&Clear selection", "Esc",
                                                   self.analysis.waveform.clear_selection)
        edit_menu.addActions([self.undo_action, self.revert_action])
        edit_menu.addSeparator()
        edit_menu.addActions([self.cut_action, self.copy_action,
                              self.paste_action, self.delete_action])
        edit_menu.addSeparator()
        edit_menu.addActions([self.crop_action, self.silence_action,
                              self.crossfade_action])
        edit_menu.addSeparator()
        edit_menu.addActions([self.select_all_action, self.clear_selection_action])

        play_menu = self.menuBar().addMenu("&Playback")
        # Space and Home are handled in keyPressEvent, not registered as
        # shortcuts: as window shortcuts they would swallow the space bar and
        # the Home key from every path field in the tool panels. The "\t" makes
        # the menu display them anyway.
        self.play_action = self._action("&Play / Stop\tSpace", None, self.toggle_play)
        self.home_action = self._action("Playhead to &start\tHome", None,
                                        lambda: self.seek(0.0))
        self.loop_action = self._action("&Loop", "Ctrl+L", self.toggle_loop)
        self.loop_action.setCheckable(True)
        self.compare_action = self._action("&A/B compare original", "Ctrl+B",
                                           self.toggle_compare)
        self.compare_action.setCheckable(True)
        play_menu.addActions([self.play_action, self.home_action])
        play_menu.addSeparator()
        play_menu.addActions([self.loop_action, self.compare_action])

        view_menu = self.menuBar().addMenu("&View")
        self.zoom_selection_action = self._action(
            "Zoom to &selection", "Ctrl+E", self.analysis.zoom_to_selection)
        self.zoom_full_action = self._action(
            "Zoom &out to whole clip", "Ctrl+F", self.analysis.zoom_full)
        view_menu.addActions([self.zoom_selection_action, self.zoom_full_action])
        view_menu.addSeparator()
        view_menu.addAction(self.files_dock.toggleViewAction())
        view_menu.addAction(self.markers_dock.toggleViewAction())
        view_menu.addAction(self.tools_dock.toggleViewAction())

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("&About", None, self.about))

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("mainToolBar")
        toolbar.addActions([self.open_action, self.save_action])
        toolbar.addSeparator()
        toolbar.addActions([self.undo_action, self.revert_action])
        toolbar.addSeparator()
        toolbar.addActions([self.zoom_selection_action, self.zoom_full_action])

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
        self.session.selection_changed.connect(
            lambda start, end: self.zoom_selection_action.setEnabled(end > start))
        self.session.compare_changed.connect(self._on_compare_changed)
        self.session.clipboard_changed.connect(lambda _: self._sync_enabled())
        self.session.markers_changed.connect(self._on_markers_changed)
        self.session.selection_changed.connect(lambda *_: self._sync_enabled())
        self.analysis.selection_changed.connect(self.session.set_selection)
        self.analysis.seek_requested.connect(self.seek)

        self.files.file_activated.connect(self.load_file)
        self.files.status.connect(self.show_message)
        self.markers.status.connect(self.show_message)
        self.markers.seek_requested.connect(self.seek)
        self.markers.select_requested.connect(self.session.set_selection)

        self.transport.play_requested.connect(self._play)
        self.transport.stop_requested.connect(self._stop)
        self.transport.compare_requested.connect(self.session.set_compare)
        self.transport.loudness_match_requested.connect(
            self.session.set_loudness_match)
        self.transport.loop_requested.connect(self.set_loop)
        self.transport.crossfade_changed.connect(self._set_crossfade)
        # seed the control from the session rather than the other way round:
        # the default lives in one place (session.DEFAULT_CROSSFADE_MS)
        self.transport.crossfade.setValue(self.session.crossfade_ms)
        self.player.position_changed.connect(self._on_position)
        self.player.finished.connect(self._on_playback_finished)
        self.player.failed.connect(self.show_message)

    # ------------------------------------------------------------------ files

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open audio", self._last_dir(), AUDIO_FILTER)
        if paths:
            self.open_files(paths)

    def open_files(self, paths: Iterable) -> None:
        """Open audio into the file list and the session -- and a corpus
        manifest among them in the Manifest panel.

        A manifest is not audio: given to `kudio.file_load` it only fails as
        a file that is not one, which is what ``kudiostudio manifest.json``
        used to say.
        """
        paths: List[str] = [str(p) for p in paths]
        manifests = [p for p in paths if is_manifest(p)]
        audio = [p for p in paths if not is_manifest(p)]
        if manifests:
            # one panel, one manifest: the last one named is the one shown
            self.open_manifest(manifests[-1])
        if audio:
            self.files.add(audio)
            self.load_file(audio[0])

    def _open_requested(self, path: str) -> None:
        """A panel asked for a file to be opened, as File → Open would."""
        self.open_files([path])

    def open_manifest_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a corpus manifest", self._last_dir(), MANIFEST_FILTER)
        if path:
            self.open_manifest(path)

    def open_manifest(self, path) -> None:
        """Show *path* in the Manifest panel and bring the panel forward.

        The panel reads it on a worker -- finding every file a corpus names is
        thousands of stats -- so the table fills in a moment after this
        returns, and the status line says when.
        """
        self.tools_dock.show()
        self.tools_dock.raise_()
        self.tools.setCurrentWidget(self.manifest_panel)
        if self.manifest_panel.load(path):
            self.settings.setValue("last_dir", str(Path(path).parent))

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

    def save_selection_dialog(self) -> None:
        if not self.session.can_copy():
            self.show_message("Select a range first — Ctrl+A selects everything.")
            return
        start, end = self.session.selection()
        stem = Path(self.session.name or "audio").stem
        suggested = Path(self._last_dir()) / f"{stem}_{start:.3f}-{end:.3f}.wav"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save selection as", str(suggested), "WAV (*.wav)")
        if not path:
            return
        try:
            self.session.save_selection(path)
        except Exception as e:
            self.show_message(f"Save failed — {type(e).__name__}: {e}")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))

    def export_image_dialog(self) -> None:
        """Write the analysis view — waveform, spectrogram, axes — to a PNG.

        A grab of the widget, not a re-render: what lands in the file is what
        is on screen, including the zoom and the selection you were looking at.
        """
        if not self.session.has_audio:
            self.show_message("Nothing to export.")
            return
        stem = Path(self.session.name or "analysis").stem
        suggested = Path(self._last_dir()) / f"{stem}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analysis image", str(suggested), "PNG (*.png)")
        if not path:
            return
        if not self.analysis.grab().save(path):
            self.show_message(f"Could not write {Path(path).name}")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.show_message(f"Exported {Path(path).name}")

    def export_script_dialog(self) -> None:
        """Write the edit history as a Python script that redoes it.

        A step the script cannot reproduce is not left out quietly: the script
        stops at it, and the status line says so here, at export, rather than
        when somebody runs it in a batch job later.
        """
        if not self.session.has_history():
            self.show_message("No edits to export yet.")
            return
        stem = Path(self.session.name or "audio").stem
        suggested = Path(self._last_dir()) / f"{stem}_edits.py"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export edits as Python script", str(suggested),
            "Python (*.py)")
        if not path:
            return
        path = Path(path)
        try:
            path.write_text(self.session.edit_script(f"{stem}_edited.wav"),
                            encoding="utf-8")
        except Exception as e:
            self.show_message(f"Export failed — {type(e).__name__}: {e}")
            return
        self.settings.setValue("last_dir", str(path.parent))
        steps = len(self.session.history)
        warnings = self.session.edit_script_warnings()
        if warnings:
            more = (f" (and {len(warnings) - 1} more, listed at the top of the "
                    f"script)" if len(warnings) > 1 else "")
            self.show_message(
                f"Exported {steps} edit(s) to {path.name}, but it will not "
                f"reproduce them as it stands — {warnings[0]}{more}")
            return
        self.show_message(f"Exported {steps} edit(s) to {path.name}")

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", str(Path.home())))

    # -------------------------------------------------------------- editing

    def _cut(self) -> None:
        if not self.session.cut():
            self.show_message("Select a range to cut — Ctrl+A selects everything.")

    def _copy(self) -> None:
        if not self.session.copy():
            self.show_message("Select a range to copy.")

    def _paste(self) -> None:
        if not self.session.paste(self._playhead):
            self.show_message("The clipboard is empty.")

    def _delete(self) -> None:
        if not self.session.delete():
            self.show_message("Select a range to delete.")

    def insert_silence_dialog(self) -> None:
        if not self.session.has_audio:
            return
        seconds, ok = QInputDialog.getDouble(
            self, "Insert silence", "Seconds:", 1.0, 0.001, 600.0, 3)
        if ok:
            self.session.insert_silence(seconds, at=self._playhead)

    def crossfade_dialog(self) -> None:
        """Type an exact splice crossfade.

        The transport bar carries the same value as a spin box, which is where
        it is normally changed; this is the keyboard route to it and the reason
        the setting appears in the Edit menu at all. Both write the transport
        control, so there is one place the value lives.
        """
        value, ok = QInputDialog.getDouble(
            self, "Splice crossfade",
            "Milliseconds blended at each cut and paste.\n"
            "0 disables it, at the cost of a click where two pieces meet.",
            self.session.crossfade_ms, 0.0, 200.0, 1)
        if ok:
            self.transport.crossfade.setValue(value)

    def _set_crossfade(self, value: float) -> None:
        applied = self.session.set_crossfade_ms(value)
        self.crossfade_action.setText(f"Splice cross&fade… ({applied:g} ms)")
        self.show_message(
            f"Splices now crossfade over {applied:g} ms." if applied
            else "Splices are now hard cuts — expect a click at every join.")

    # ------------------------------------------------------------- playback

    def _play(self, selection_only: bool) -> None:
        """Play the selection if there is one, otherwise from the playhead on."""
        if not self.session.has_audio:
            return
        self._end_preview()
        sel = self.session.selection() if selection_only else None
        if sel is not None:
            self._play_range(*sel)
            return
        y, sr = self.session.preview()
        total = len(y) / sr
        start = self._playhead if self._playhead < total - 1e-3 else 0.0
        self._play_range(start, total)

    def _play_range(self, start: float, end: float) -> None:
        """Play *start*..*end* seconds of whichever buffer A/B is showing."""
        y, sr = self.session.preview()
        if y is None or sr <= 0 or len(y) == 0:
            return
        end = min(end, len(y) / sr)
        start = max(0.0, min(start, end))
        if end - start < 1e-3:
            return
        self._segment = (start, end)
        self.player.play(y[int(start * sr):int(end * sr)], sr,
                         self.transport.gain_db, t0=start)

    def toggle_play(self) -> None:
        if self.player.playing:
            self._stop()
        else:
            self._play(self.transport.selection_only.isChecked())

    def _stop(self) -> None:
        self._end_preview()
        self.player.stop()

    # ---------------------------------------------------------------- preview

    def _start_preview(self, y, sr: int) -> None:
        """Audition a panel's rendered audio. Loops; never touches the session.

        Sits at the selection's place on the timeline so the playhead lands
        where the effect would. An effect that changes the length -- a time
        stretch -- will run the cursor past the selection; the audio is right,
        the cursor is approximate.
        """
        y = np.asarray(y, dtype=np.float32)
        if sr <= 0 or len(y) == 0:
            return
        sel = self.session.selection()
        self._preview = (y, int(sr), sel[0] if sel else 0.0)
        self._replay_preview()

    def _replay_preview(self) -> None:
        y, sr, t0 = self._preview
        self._segment = (t0, t0 + len(y) / sr)
        self.player.play(y, sr, self.transport.gain_db, t0=t0)

    def _end_preview(self) -> None:
        if self._preview is None:
            return
        self._preview = None
        for panel in self.panels:
            panel.cancel_preview()

    def seek(self, seconds: float) -> None:
        """Move the playhead; keep playing from there if we already were."""
        if not self.session.has_audio:
            return
        self._set_playhead(seconds)
        if self.player.playing:
            start, end = self._segment
            self._play_range(self._playhead,
                             end if start <= self._playhead < end
                             else self.session.duration)

    def toggle_compare(self) -> None:
        self.session.set_compare(not self.session.comparing)

    def toggle_loop(self) -> None:
        self.set_loop(not self._loop)

    def set_loop(self, on: bool) -> None:
        """Repeat whatever was last played, until something stops it."""
        self._loop = bool(on)
        self.loop_action.setChecked(self._loop)
        self.transport.set_loop_state(self._loop)

    def _set_playhead(self, seconds: float) -> None:
        self._playhead = max(0.0, float(seconds))
        self.analysis.set_cursor(
            self._playhead if self.session.has_audio else None)
        self.transport.set_time(self._playhead)
        self.markers.set_playhead(self._playhead)

    def _on_position(self, seconds: float) -> None:
        self._set_playhead(seconds)

    def _on_playback_finished(self) -> None:
        if self._preview is not None:
            self._replay_preview()      # auditioning loops until you stop it
            return
        # running to the end is not a choice of position; go back to the start
        # of what was played, so pressing play again repeats it
        self._set_playhead(self._segment[0])
        if self._loop:
            self._play_range(*self._segment)

    def _on_compare_changed(self, on: bool) -> None:
        """Swap the monitored buffer under the views, and under playback."""
        self.transport.set_compare_state(on)
        self.compare_action.setChecked(on)
        # Rebase playback first: redrawing the spectrogram takes long enough to
        # be audible as a lag between pressing the key and hearing the switch.
        if self.player.playing and self._preview is None:
            self._play_range(self.player.position, self._segment[1])
        y, sr = self.session.preview()
        self.analysis.set_audio(y, sr)
        self.show_message(self._compare_message(on))

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

    # --------------------------------------------------------------- session

    def _on_session_changed(self) -> None:
        self._end_preview()         # the audio it was rendered from is gone
        y, sr = self.session.preview()
        self.analysis.set_audio(y, sr)
        self._set_playhead(0.0)
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

    def _on_markers_changed(self) -> None:
        self.analysis.set_markers(self.session.markers)

    def _sync_enabled(self) -> None:
        has_audio = self.session.has_audio
        self.transport.set_enabled_for(has_audio)
        self.save_action.setEnabled(has_audio)
        self.export_image_action.setEnabled(has_audio)
        self.export_script_action.setEnabled(self.session.has_history())
        self.select_all_action.setEnabled(has_audio)
        self.clear_selection_action.setEnabled(has_audio)
        self.play_action.setEnabled(has_audio)
        self.home_action.setEnabled(has_audio)
        self.loop_action.setEnabled(has_audio)
        self.silence_action.setEnabled(has_audio)
        self.crossfade_action.setEnabled(True)   # a setting, not an edit
        self.zoom_full_action.setEnabled(has_audio)
        self.zoom_selection_action.setEnabled(self.session.selection() is not None)
        self.undo_action.setEnabled(self.session.can_undo())
        self.revert_action.setEnabled(self.session.original() is not None)

        can_copy = self.session.can_copy()
        for action in (self.cut_action, self.copy_action, self.delete_action,
                       self.crop_action, self.save_selection_action):
            action.setEnabled(can_copy)
        self.paste_action.setEnabled(self.session.can_paste())

        can_compare = self.session.can_compare()
        self.transport.set_compare_enabled(can_compare)
        self.compare_action.setEnabled(can_compare)
        if not can_compare:
            self.compare_action.setChecked(False)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt hook
        """Editing keys reach here only when no editor wanted them first.

        Space, Home and the clipboard keys are deliberately *not* window
        shortcuts. Qt resolves shortcuts before the key ever reaches the
        focused widget, so a window-level Ctrl+C would take copy away from
        every path field in the tool panels — and Space away from typing a
        filename with a space in it.
        """
        if self.session.has_audio and not event.isAutoRepeat():
            if event.key() == Qt.Key_Space:
                self.toggle_play()
                return
            if event.key() == Qt.Key_Home:
                self.seek(0.0)
                return
            if event.matches(QKeySequence.Cut):
                self._cut()
                return
            if event.matches(QKeySequence.Copy):
                self._copy()
                return
            if event.matches(QKeySequence.Paste):
                self._paste()
                return
            if event.matches(QKeySequence.Delete):
                self._delete()
                return
        super().keyPressEvent(event)

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

    def _job_hosts(self) -> List[Tuple[str, WorkerHost]]:
        """Every place a job can be running, by the name the window shows."""
        return ([("the level meters", self.analysis.levels.jobs)]
                + [(panel.title, panel.jobs) for panel in self.panels])

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt hook
        """Close -- but not while a job is running.

        A kudio call cannot be stopped half-way, and a window destroyed
        around a running thread aborts the whole process: a Batch run quit
        half-way left its output tree half-written. So Quit waits. The window
        stays open, says what it is waiting for, and closes itself when the
        last job ends.
        """
        running = [(name, host) for name, host in self._job_hosts()
                   if host.busy]
        if running:
            event.ignore()
            if not self._quitting:
                self._quitting = True
                for _, host in self._job_hosts():
                    host.busy_changed.connect(self._quit_when_idle)
            names = [name for name, _ in running]
            waiting = (names[0] + " finishes" if len(names) == 1 else
                       ", ".join(names[:-1]) + f" and {names[-1]} finish")
            self.show_message(f"Quitting when {waiting} — a running job "
                              f"cannot be stopped half-way.")
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self.player.shutdown()
        for panel in self.panels:
            panel.shutdown()        # devices stay open until told otherwise
        super().closeEvent(event)

    def _quit_when_idle(self, busy: bool) -> None:
        if busy or any(host.busy for _, host in self._job_hosts()):
            return
        for _, host in self._job_hosts():
            host.busy_changed.disconnect(self._quit_when_idle)
        self._quitting = False
        self.close()
