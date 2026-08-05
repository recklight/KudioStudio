# -*- coding: utf-8 -*-
"""AudioSession: loading, editing, undo and selection arithmetic."""
from __future__ import annotations

import numpy as np


def test_load_sets_audio_and_original(session, tone):
    assert session.has_audio
    assert session.sr == 16000
    assert session.name == tone.name
    assert abs(session.duration - 1.0) < 1e-3
    original = session.original()
    assert original is not None
    assert np.allclose(original[0], session.y)


def test_edit_pushes_undo_and_keeps_original(session):
    before = session.y.copy()
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)

    assert session.can_undo()
    assert np.allclose(session.y, before * 0.5)
    assert np.allclose(session.original()[0], before)

    assert session.undo() is True
    assert np.allclose(session.y, before)
    assert session.can_undo() is False


def test_revert_drops_every_edit(session):
    before = session.y.copy()
    for scale in (0.5, 0.25):
        session.set_audio(session.y * scale, session.sr,
                          undo_label="gain", keep_original=True)
    assert session.revert() is True
    assert np.allclose(session.y, before)
    assert session.can_undo() is False


def test_new_audio_becomes_the_new_baseline(session):
    fresh = np.zeros(8000, dtype=np.float32)
    session.set_audio(fresh, 8000, name="recording")

    assert session.sr == 8000
    assert session.can_undo() is False
    assert np.allclose(session.original()[0], fresh)


def test_selection_maps_to_samples(session):
    session.set_selection(0.25, 0.75)
    assert session.selection() == (0.25, 0.75)
    assert session.selected_slice() == slice(4000, 12000)
    assert len(session.selected_audio()) == 8000

    session.clear_selection()
    assert session.selection() is None
    assert len(session.selected_audio()) == len(session.y)


def test_replace_selection_splices_in_place(session):
    n = len(session.y)
    session.set_selection(0.25, 0.75)
    session.replace_selection(np.zeros(8000, dtype=np.float32),
                              undo_label="silence")

    assert len(session.y) == n
    assert np.allclose(session.y[4000:12000], 0.0)
    assert not np.allclose(session.y[:4000], 0.0)
    assert session.can_undo()


def test_replace_selection_handles_length_change(session):
    session.set_selection(0.25, 0.75)
    session.replace_selection(np.zeros(100, dtype=np.float32),
                              undo_label="stretch")
    assert len(session.y) == 4000 + 100 + 4000


def test_save_round_trips(session, tmp_path):
    import kudio
    out = session.save(tmp_path / "out.wav")
    y, sr = kudio.file_load(out)
    assert sr == session.sr
    assert len(y) == len(session.y)
