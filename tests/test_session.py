# -*- coding: utf-8 -*-
"""AudioSession: loading, editing, undo and selection arithmetic."""
from __future__ import annotations

import numpy as np
import pytest


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


def test_compare_is_refused_until_something_has_changed(session):
    assert session.can_compare() is False
    assert session.set_compare(True) is False
    assert session.comparing is False

    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    assert session.can_compare() is True


def test_compare_swaps_what_preview_returns_but_not_y(session):
    edited = session.y * 0.5
    session.set_audio(edited, session.sr, undo_label="gain", keep_original=True)
    session.set_loudness_match(False)
    original = session.original()[0]

    seen = []
    session.compare_changed.connect(seen.append)

    assert session.set_compare(True) is True
    y, sr = session.preview()
    assert np.allclose(y, original)
    assert sr == session.sr
    assert np.allclose(session.y, edited)       # the panels' view is untouched
    assert seen == [True]

    session.set_compare(False)
    assert np.allclose(session.preview()[0], edited)
    assert seen == [True, False]


def test_loudness_match_levels_the_original_to_the_edit(session):
    """A -6 dB edit must not be compared against a 6 dB louder original."""
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    assert session.loudness_matched is True, "the fair comparison is the default"
    assert session.loudness_gap() == pytest.approx(6.02, abs=0.05)

    session.set_compare(True)
    matched = session.preview()[0]
    assert np.allclose(matched, session.y, atol=1e-6)   # gain-only edit cancels

    session.set_loudness_match(False)
    assert np.allclose(session.preview()[0], session.original()[0])


def test_turning_the_match_on_or_off_announces_the_change(session):
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    session.set_compare(True)

    seen = []
    session.compare_changed.connect(seen.append)
    session.set_loudness_match(False)
    session.set_loudness_match(False)        # no change, no signal
    session.set_loudness_match(True)
    assert seen == [True, True], "still comparing, but preview() changed"


def test_loudness_gap_is_none_when_it_cannot_be_measured(session, tone):
    """Shorter than BS.1770's 400 ms block: report honestly, do not guess."""
    short = session.y[:2000]                 # 0.125 s at 16 kHz
    session.load_file(tone)
    session.set_audio(short, session.sr, undo_label="trim", keep_original=True)

    assert session.loudness_gap() is None
    session.set_compare(True)
    assert np.allclose(session.preview()[0], session.original()[0]), \
        "unmatched rather than wrongly matched"


def test_the_loudness_gap_is_remeasured_after_a_new_edit(session):
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    assert session.loudness_gap() == pytest.approx(6.02, abs=0.05)

    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    assert session.loudness_gap() == pytest.approx(12.04, abs=0.05)


def test_compare_ends_when_the_audio_changes(session):
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    session.set_compare(True)

    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain again", keep_original=True)
    assert session.comparing is False


def test_compare_survives_a_length_changing_edit(session):
    """A trim leaves the two buffers different lengths; preview must still work."""
    session.set_audio(session.y[:4000], session.sr,
                      undo_label="trim", keep_original=True)
    assert session.can_compare() is True

    session.set_compare(True)
    y, sr = session.preview()
    assert len(y) == 16000
    assert len(session.y) == 4000


def test_save_round_trips(session, tmp_path):
    import kudio
    out = session.save(tmp_path / "out.wav")
    y, sr = kudio.file_load(out)
    assert sr == session.sr
    assert len(y) == len(session.y)
