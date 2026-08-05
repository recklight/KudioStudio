# -*- coding: utf-8 -*-
"""The main window assembles, loads a file and wires selection both ways."""
from __future__ import annotations

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


def test_undo_action_follows_the_session(window, tone):
    window.open_files([tone])
    assert window.undo_action.isEnabled() is False

    window.session.set_audio(window.session.y * 0.5, window.session.sr,
                             undo_label="gain", keep_original=True)
    assert window.undo_action.isEnabled() is True

    window.undo_action.trigger()
    assert window.undo_action.isEnabled() is False
