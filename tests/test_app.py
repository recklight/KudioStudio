# -*- coding: utf-8 -*-
"""The main window assembles, loads a file and wires selection both ways."""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def window(qapp):
    from kudiostudio.app import KudioStudio
    w = KudioStudio(reset=True)
    yield w
    w.close()


def test_window_has_every_panel(window):
    from kudiostudio.panels import PANEL_CLASSES
    titles = [window.tools.tabText(i) for i in range(window.tools.count())]
    assert titles == [cls.title for cls in PANEL_CLASSES]


def test_actions_disabled_until_audio_is_loaded(window):
    assert window.save_action.isEnabled() is False
    assert window.undo_action.isEnabled() is False


def test_open_file_populates_views_and_list(window, tone):
    window.open_files([tone])

    assert window.session.has_audio
    assert window.files.paths() == [str(tone.resolve())]
    assert window.save_action.isEnabled() is True
    assert tone.name in window.windowTitle()
    assert "16000 Hz" in window.transport.info.text()


def test_bad_file_reports_instead_of_raising(window, tmp_path):
    broken = tmp_path / "not-audio.wav"
    broken.write_text("this is not a wav file")

    window.load_file(str(broken))
    assert window.session.has_audio is False
    assert "Could not open" in window.statusBar().currentMessage()


def test_selection_round_trips_between_view_and_session(window, tone):
    window.open_files([tone])

    window.analysis.selection_changed.emit(0.2, 0.6)
    assert window.session.selection() == pytest.approx((0.2, 0.6))

    window.session.set_selection(0.1, 0.3)
    start, end = window.analysis.waveform._region.getRegion()
    assert (start, end) == pytest.approx((0.1, 0.3))


def _edit(window):
    """Halve the gain, so the session has something to compare against."""
    window.session.set_audio(window.session.y * 0.5, window.session.sr,
                             undo_label="gain", keep_original=True)


def test_compare_button_is_dead_until_there_is_an_edit(window, tone):
    window.open_files([tone])
    assert window.transport.compare_button.isEnabled() is False

    _edit(window)
    assert window.transport.compare_button.isEnabled() is True


def test_compare_swaps_the_views_and_labels_which_side_you_hear(window, tone):
    window.open_files([tone])
    _edit(window)

    window.transport.match_loudness.setChecked(False)
    window.transport.compare_requested.emit(True)
    assert window.session.comparing is True
    assert window.transport.compare_button.isChecked() is True
    assert "original" in window.transport.compare_button.text()
    assert np.allclose(window.analysis._y, window.session.original()[0])

    window.toggle_compare()
    assert window.session.comparing is False
    assert np.allclose(window.analysis._y, window.session.y)


def test_editing_drops_out_of_compare(window, tone):
    window.open_files([tone])
    _edit(window)
    window.transport.compare_requested.emit(True)

    _edit(window)
    assert window.session.comparing is False
    assert window.transport.compare_button.isChecked() is False


def test_the_status_line_names_the_loudness_gap(window, tone):
    window.open_files([tone])
    _edit(window)

    window.toggle_compare()
    message = window.statusBar().currentMessage()
    assert "levelled to the edit" in message
    assert "-6.0 LU" in message, message

    window.transport.match_loudness.setChecked(False)
    message = window.statusBar().currentMessage()
    assert "Match loudness" in message and "+6.0 LU" in message, message


def test_the_match_checkbox_follows_whether_there_is_anything_to_compare(window, tone):
    window.open_files([tone])
    assert window.transport.match_loudness.isEnabled() is False
    _edit(window)
    assert window.transport.match_loudness.isEnabled() is True


def test_a_panel_preview_plays_without_touching_the_session(window, tone, monkeypatch):
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append((len(y), t0)))
    window.open_files([tone])
    window.session.set_selection(0.25, 0.75)
    before = window.session.y.copy()

    effects = next(p for p in window.panels if p.title == "Effects")
    effects.effect.setCurrentText("gain")
    effects.gain_db.setValue(-6.0)
    effects.preview_button.setChecked(True)

    assert played == [(8000, pytest.approx(0.25))]
    assert np.allclose(window.session.y, before), "preview must not edit"
    assert window.session.can_undo() is False
    assert window._preview is not None


def test_a_preview_loops_until_something_else_claims_playback(window, tone, monkeypatch):
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(len(y)))
    window.open_files([tone])

    effects = next(p for p in window.panels if p.title == "Effects")
    effects.effect.setCurrentText("gain")
    effects.preview_button.setChecked(True)
    assert len(played) == 1

    window.player.finished.emit()
    assert len(played) == 2, "auditioning should have restarted"

    window._stop()
    assert window._preview is None
    assert effects.preview_button.isChecked() is False

    window.player.finished.emit()
    assert len(played) == 2, "and stop must really stop it"


def test_editing_cancels_an_open_preview(window, tone, monkeypatch):
    monkeypatch.setattr(window.player, "play", lambda *a, **k: None)
    window.open_files([tone])

    effects = next(p for p in window.panels if p.title == "Effects")
    effects.preview_button.setChecked(True)
    assert window._preview is not None

    effects._apply()
    assert window._preview is None
    assert effects.preview_button.isChecked() is False


def test_clicking_the_waveform_moves_the_playhead(window, tone):
    window.open_files([tone])
    window.analysis.waveform.seek_requested.emit(0.4)

    assert window.session.has_audio
    assert window._playhead == pytest.approx(0.4)
    for plot in (window.analysis.waveform, window.analysis.spectrogram):
        assert plot._cursor.isVisible()
        assert plot._cursor.value() == pytest.approx(0.4)
    assert window.transport.time_label.text() == "0:00.400"


def test_seek_is_clamped_and_reset_by_a_new_file(window, tone):
    window.open_files([tone])
    window.seek(-5.0)
    assert window._playhead == 0.0

    window.seek(0.5)
    window.load_file(str(tone))
    assert window._playhead == 0.0


def test_play_starts_from_the_playhead_when_nothing_is_selected(window, tone, monkeypatch):
    played = {}
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.update(
                            n=len(y), sr=sr, t0=t0))
    window.open_files([tone])
    window.transport.selection_only.setChecked(False)

    window.seek(0.25)
    window._play(False)
    assert played["t0"] == pytest.approx(0.25)
    assert played["n"] == 12000          # 0.25 s in to the end of a 1 s clip


def test_play_uses_the_selection_when_there_is_one(window, tone, monkeypatch):
    played = {}
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.update(
                            n=len(y), t0=t0))
    window.open_files([tone])
    window.session.set_selection(0.25, 0.75)

    window._play(True)
    assert played["t0"] == pytest.approx(0.25)
    assert played["n"] == 8000


def test_playback_finishing_rewinds_to_where_it_started(window, tone):
    window.open_files([tone])
    window.session.set_selection(0.4, 0.6)
    window._play_range(0.4, 0.6)

    window._on_position(0.6)
    assert window._playhead == pytest.approx(0.6)

    window.player.finished.emit()
    assert window._playhead == pytest.approx(0.4)


def test_undo_action_follows_the_session(window, tone):
    window.open_files([tone])
    assert window.undo_action.isEnabled() is False

    window.session.set_audio(window.session.y * 0.5, window.session.sr,
                             undo_label="gain", keep_original=True)
    assert window.undo_action.isEnabled() is True

    window.undo_action.trigger()
    assert window.undo_action.isEnabled() is False


def test_enhance_preview_runs_off_thread_and_commits_nothing(window, tone, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer

    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append((len(y), t0)))
    window.open_files([tone])
    window.session.set_selection(0.2, 0.7)
    before = window.session.y.copy()

    enhance = next(p for p in window.panels if p.title == "Enhance")
    enhance.method.setCurrentIndex(0)               # trad_enhance
    enhance.preview_button.setChecked(True)

    loop = QEventLoop()
    QTimer.singleShot(15000, loop.quit)
    window.player.position_changed.connect(lambda _: loop.quit())
    enhance.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    loop.exec()
    enhance.jobs.wait(15000)

    assert played, "the preview never reached the player"
    assert played[0][1] == pytest.approx(0.2), "should play at the selection"
    assert np.allclose(window.session.y, before), "preview must not edit"
    assert window.session.can_undo() is False


def test_enhance_preview_is_released_when_denoise_commits(window, tone, monkeypatch):
    monkeypatch.setattr(window.player, "play", lambda *a, **k: None)
    window.open_files([tone])

    enhance = next(p for p in window.panels if p.title == "Enhance")
    enhance.preview_button.blockSignals(True)
    enhance.preview_button.setChecked(True)
    enhance.preview_button.blockSignals(False)

    enhance._run()
    assert enhance.preview_button.isChecked() is False
    enhance.jobs.wait(15000)


# ---------------------------------------------------------- editing & markers

def test_edit_actions_track_whether_there_is_a_selection(window, tone):
    window.open_files([tone])
    for action in (window.cut_action, window.copy_action, window.delete_action,
                   window.crop_action, window.save_selection_action):
        assert action.isEnabled() is False
    assert window.paste_action.isEnabled() is False

    window.session.set_selection(0.2, 0.6)
    for action in (window.cut_action, window.copy_action, window.delete_action,
                   window.crop_action, window.save_selection_action):
        assert action.isEnabled() is True

    window.session.copy()
    assert window.paste_action.isEnabled() is True


def test_the_clipboard_keys_are_not_window_shortcuts(window):
    """A window-level Ctrl+C would take copy away from every path field.

    Qt resolves shortcuts before the key reaches the focused widget, so these
    are handled in keyPressEvent instead — the same reason Space is.
    """
    for action in (window.cut_action, window.copy_action, window.paste_action,
                   window.delete_action):
        assert action.shortcut().isEmpty(), action.text()
        assert "\t" in action.text()        # ...but the menu still shows the key


def test_paste_lands_at_the_playhead(window, tone):
    window.open_files([tone])
    window.session.set_selection(0.0, 0.2)
    window.session.copy()
    window.session.clear_selection()
    window.seek(0.5)

    window._paste()
    start, end = window.session.selection()
    assert start == pytest.approx(0.5, abs=1e-3)
    assert end == pytest.approx(0.7, abs=1e-3)


def test_editing_without_a_selection_explains_itself(window, tone):
    window.open_files([tone])
    messages = []
    window.session.message.connect(messages.append)
    window.statusBar().messageChanged.connect(messages.append)
    window._cut()
    assert "Ctrl+A" in window.statusBar().currentMessage()


def test_markers_reach_the_plots(window, tone):
    window.open_files([tone])
    window.session.add_marker(0.3, 0.5, "here")
    assert len(window.analysis.waveform._marker_items) == 1
    assert len(window.analysis.spectrogram._marker_items) == 1


def test_clicking_a_marker_moves_the_playhead(window, tone):
    window.open_files([tone])
    window.session.add_marker(0.6)
    window.markers.list.setCurrentRow(0)
    window.markers._on_clicked(window.markers.list.item(0))
    assert window._playhead == pytest.approx(0.6)


def test_the_playhead_is_what_a_new_marker_uses(window, tone):
    window.open_files([tone])
    window.seek(0.42)
    window.markers._add_at_playhead()
    assert window.session.markers[0].start == pytest.approx(0.42)


# ------------------------------------------------------------------- playback

def test_loop_repeats_the_same_range(window, tone, monkeypatch):
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(t0))
    window.open_files([tone])
    window.session.set_selection(0.2, 0.4)
    window._play(True)
    assert played == [pytest.approx(0.2)]

    window.player.finished.emit()            # not looping: stays stopped
    assert len(played) == 1

    window.set_loop(True)
    window._play(True)
    window.player.finished.emit()
    assert len(played) == 3
    assert played[-1] == pytest.approx(0.2)


def test_loop_state_is_mirrored_in_both_places(window, tone):
    window.open_files([tone])
    window.transport.loop.click()
    assert window._loop is True
    assert window.loop_action.isChecked() is True

    window.toggle_loop()
    assert window._loop is False
    assert window.transport.loop.isChecked() is False


def test_a_looping_preview_is_not_affected_by_the_loop_switch(window, tone,
                                                              monkeypatch):
    """Preview always loops; the switch is for ordinary playback."""
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(t0))
    window.open_files([tone])
    window._start_preview(np.zeros(1600, dtype=np.float32), 16000)
    assert len(played) == 1
    window.player.finished.emit()
    assert len(played) == 2                  # looped without _loop being set
    assert window._loop is False


# --------------------------------------------------------------------- export

def test_export_image_writes_a_png(window, tone, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    window.open_files([tone])
    target = tmp_path / "analysis.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    window.export_image_dialog()
    assert target.is_file() and target.stat().st_size > 0


def test_save_selection_writes_only_the_range(window, tone, tmp_path, monkeypatch):
    import kudio
    from PySide6.QtWidgets import QFileDialog

    window.open_files([tone])
    window.session.set_selection(0.25, 0.75)
    target = tmp_path / "part.wav"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    window.save_selection_dialog()

    y, sr = kudio.file_load(target)
    assert len(y) == pytest.approx(0.5 * sr, abs=2)


def test_the_edits_export_as_a_script_from_the_file_menu(window, tone, tmp_path,
                                                         monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    # the action held while its menu is used: PySide6 6.10 deletes a menu
    # reached through a temporary action
    file_action = window.menuBar().actions()[0]
    file_menu = file_action.menu()
    assert window.export_script_action in file_menu.actions()

    window.open_files([tone])
    assert window.export_script_action.isEnabled() is False, "nothing to export"
    window.session.set_selection(0.2, 0.4)
    window._delete()
    assert window.export_script_action.isEnabled() is True

    target = tmp_path / "edits.py"
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(
        lambda *a, **k: asked.append(a[2]) or (str(target), "")))
    window.export_script_action.trigger()

    assert asked[0].endswith("tone_edits.py")
    script = target.read_text(encoding="utf-8")
    assert script == window.session.edit_script("tone_edited.wav")
    assert "# 1. delete" in script
    assert window.statusBar().currentMessage() == \
        "Exported 1 edit(s) to edits.py"

    window.undo_action.trigger()
    assert window.export_script_action.isEnabled() is False


def test_exporting_a_script_that_cannot_reproduce_the_edit_says_so(
        window, tone, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    window.open_files([tone])
    _edit(window)                           # an edit that brought no code
    target = tmp_path / "edits.py"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    window.export_script_dialog()

    assert target.is_file()
    message = window.statusBar().currentMessage()
    assert "will not reproduce" in message
    assert "step 1 (gain)" in message


def test_cancelling_the_script_export_writes_nothing(window, tone, tmp_path,
                                                     monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    window.open_files([tone])
    _edit(window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    window.export_script_dialog()
    assert list(tmp_path.glob("*.py")) == []


def test_a_live_monitor_draws_without_touching_the_session(window, tone):
    window.open_files([tone])
    before = np.array(window.session.y, copy=True)
    window.analysis.start_monitor(16000)
    window.analysis.feed_monitor(np.zeros(8000, dtype=np.float32), 16000)
    window.analysis._draw_monitor()
    assert np.array_equal(window.session.y, before)
    # the rolling axis is the window, not the take: half a second of audio
    # sits at the right-hand edge of it rather than filling it
    from kudiostudio.widgets.views import MONITOR_SECONDS
    # to the nearest whole column: the window is a column count, not a time
    assert window.analysis.waveform.duration == pytest.approx(MONITOR_SECONDS,
                                                              abs=0.05)


def test_the_record_panel_is_wired_to_the_rolling_views(window):
    """Three signals, one meaning each -- and the window has to connect all
    three or a take draws nothing, or never stops drawing."""
    from kudiostudio.panels.record import RecordPanel

    panel = next(p for p in window.panels if isinstance(p, RecordPanel))
    panel.monitor_started.emit(16000)
    assert window.analysis._monitor_sr == 16000

    panel.monitor_chunk.emit(np.full(4096, 0.2, dtype=np.float32), 16000)
    assert window.analysis._monitor_stream.frames_seen > 0

    panel.monitor_stopped.emit()
    assert window.analysis._monitor_sr == 0


def test_the_splice_crossfade_is_reachable_and_visible(window, tone, monkeypatch):
    """It alters the audio at every cut; a hidden constant would not do."""
    from PySide6.QtWidgets import QInputDialog

    window.open_files([tone])
    assert window.crossfade_action.isEnabled() is True
    assert window.session.crossfade_ms == 5.0

    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (0.0, True)))
    window.crossfade_dialog()
    assert window.session.crossfade_ms == 0.0
    assert "hard cuts" in window.statusBar().currentMessage()

    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (12.0, True)))
    window.crossfade_dialog()
    assert window.session.crossfade_ms == 12.0
    assert "12 ms" in window.statusBar().currentMessage()


def test_cancelling_the_crossfade_dialog_changes_nothing(window, tone, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    window.open_files([tone])
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (99.0, False)))
    window.crossfade_dialog()
    assert window.session.crossfade_ms == 5.0



# ------------------------------------------------------- the splice crossfade

def test_the_crossfade_is_on_the_bar_not_behind_a_dialog(window):
    """It changes what every cut and paste does to the audio, and the right
    length is per edit -- so it has to be adjustable without opening
    anything."""
    assert window.transport.crossfade.value() == window.session.crossfade_ms

    window.transport.crossfade.setValue(40.0)
    assert window.session.crossfade_ms == 40.0
    assert "40 ms" in window.crossfade_action.text()


def test_the_dialog_writes_through_the_same_control(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (12.5, True)))
    window.crossfade_dialog()

    assert window.transport.crossfade.value() == 12.5
    assert window.session.crossfade_ms == 12.5


def test_cancelling_the_dialog_changes_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    before = window.session.crossfade_ms
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (99.0, False)))
    window.crossfade_dialog()
    assert window.session.crossfade_ms == before


def test_turning_it_off_says_what_that_costs(window):
    messages = []
    window.statusBar().messageChanged.connect(messages.append)
    window.transport.crossfade.setValue(0.0)
    assert window.session.crossfade_ms == 0.0
    assert any("hard cut" in m for m in messages)


def test_quitting_waits_for_a_running_job(window, qapp):
    """A kudio call cannot be stopped half-way, and destroying the window
    around its thread aborted the process: a Batch run quit mid-way left half
    its output tree. The window stays until the job ends, says so, and then
    closes itself."""
    import time

    window.show()
    batch = next(p for p in window.panels if p.title == "Batch")
    assert batch.jobs.run(time.sleep, 1.0)
    window.close()

    assert window.isVisible(), "the close is put off while the job runs"
    assert window.statusBar().currentMessage().startswith(
        "Quitting when Batch finishes")
    deadline = time.monotonic() + 20
    while window.isVisible() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not window.isVisible()
    assert not batch.jobs.busy


def test_an_idle_window_closes_at_once(window):
    window.show()
    window.close()
    assert not window.isVisible()


def test_quitting_the_application_mid_job_exits_cleanly(tmp_path):
    """The whole program, as ``kudiostudio`` starts it: quit while a job runs
    for longer than the old two-second grace, and the process used to abort
    with "QThread: Destroyed while thread is still running"."""
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    script = tmp_path / "quit.py"
    script.write_text(textwrap.dedent(f'''
        import sys, time
        sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from kudiostudio import __main__ as entry

        def quit_mid_job():
            window = next(w for w in QApplication.topLevelWidgets()
                          if type(w).__name__ == "KudioStudio")
            batch = next(p for p in window.panels if p.title == "Batch")
            batch.jobs.run(time.sleep, 3.0)
            window.close()

        QTimer.singleShot(0, quit_mid_job)
        sys.exit(entry.main(["--no-theme", "--reset"]))
    '''), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / "config"))
    done = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, timeout=120, cwd=tmp_path, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "Destroyed while thread" not in done.stderr


def test_a_script_exported_after_saving_over_the_original_says_so(
        window, tmp_path, monkeypatch):
    """Save As suggests the opened file. Accepted, and the edits exported,
    the script read the edited audio back and edited it again -- and the
    status line said all was well."""
    from pathlib import Path

    import kudio
    from PySide6.QtWidgets import QFileDialog

    take = tmp_path / "take.wav"
    kudio.save_wave(take, (0.3 * np.sin(np.arange(32000) / 10)
                           ).astype(np.float32), 16000)
    window.open_files([take])
    window.session.set_selection(0.5, 1.0)
    window._delete()

    offered = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(
        lambda *a, **k: offered.append(a[2]) or (a[2], "")))
    window.save_dialog()
    assert Path(offered[0]) == take, "the opened file is what Save As offers"

    window.export_script_dialog()
    message = window.statusBar().currentMessage()
    assert "will not reproduce them as it stands" in message
    assert "take.wav was saved over" in message
