# -*- coding: utf-8 -*-
"""Clipboard editing and markers on `AudioSession`."""
import numpy as np
import pytest

from kudiostudio.session import (
    DEFAULT_CROSSFADE_MS,
    AudioSession,
    Marker,
)

SR = 16000
#: Every structural edit crossfades its seams, so each seam shortens the
#: result by this much. It is deliberately visible rather than absorbed.
FADE = DEFAULT_CROSSFADE_MS / 1000.0


# ------------------------------------------------------------------ clipboard

def test_copy_needs_a_selection(session):
    assert session.copy() is False
    session.set_selection(0.1, 0.3)
    assert session.copy() is True
    assert session.clipboard_duration() == pytest.approx(0.2, abs=1e-3)


def test_copy_leaves_the_audio_alone(session):
    before = np.array(session.y, copy=True)
    session.set_selection(0.1, 0.3)
    session.copy()
    assert np.array_equal(session.y, before)
    assert session.can_undo() is False


def test_cut_removes_the_selection_and_fills_the_clipboard(session):
    total = session.duration
    session.set_selection(0.2, 0.5)
    assert session.cut() is True
    # 0.3 s gone, plus one seam's crossfade
    assert session.duration == pytest.approx(total - 0.3 - FADE, abs=1e-3)
    assert session.can_paste() is True
    assert session.clipboard_duration() == pytest.approx(0.3, abs=1e-3)


def test_cut_is_undoable(session):
    before = np.array(session.y, copy=True)
    session.set_selection(0.2, 0.5)
    session.cut()
    assert session.undo() is True
    assert np.array_equal(session.y, before)


def test_delete_closes_the_gap(session):
    y = np.array(session.y, copy=True)
    session.set_selection(0.25, 0.75)
    assert session.delete() is True

    n = int(FADE * SR)
    kept_left = y[:int(0.25 * SR) - n]
    kept_right = y[int(0.75 * SR) + n:]
    # everything outside the seam is untouched; the seam itself is a blend
    assert np.allclose(session.y[:len(kept_left)], kept_left)
    assert np.allclose(session.y[-len(kept_right):], kept_right)


def test_delete_leaves_no_step_at_the_join(session):
    """A step in the waveform is a click; that is what the crossfade is for.

    Measured on two constant levels, where the step is unambiguous. The `tone`
    fixture is useless for this: 440 Hz cut half a second apart is a whole
    number of cycles, so the hard join is already perfectly continuous.
    """
    level = np.concatenate([np.full(SR // 2, 0.5, dtype=np.float32),
                            np.full(SR // 2, -0.5, dtype=np.float32)])
    session.set_audio(level, SR, name="two levels")

    # across the level change, so the join is the only step left in the clip
    session.set_crossfade_ms(0.0)
    session.set_selection(0.45, 0.55)
    session.delete()
    hard = float(np.max(np.abs(np.diff(session.y))))
    session.undo()

    session.set_crossfade_ms(5.0)
    session.set_selection(0.45, 0.55)
    session.delete()
    soft = float(np.max(np.abs(np.diff(session.y))))

    assert hard == pytest.approx(1.0, abs=0.01)      # +0.5 straight to -0.5
    assert soft < hard / 10.0                        # spread over 5 ms


def test_a_splice_does_not_raise_the_level_where_both_sides_are_alike(session):
    """Equal-power crossfade bumps correlated material by up to 3 dB; a splice
    joins two moments of one recording, so it uses the linear shape."""
    peak_before = float(np.max(np.abs(session.y)))
    session.set_selection(0.25, 0.75)                # 440 Hz, whole cycles
    session.delete()
    assert float(np.max(np.abs(session.y))) <= peak_before + 1e-6


def test_the_crossfade_can_be_turned_off(session):
    y = np.array(session.y, copy=True)
    total = session.duration
    session.set_crossfade_ms(0.0)
    session.set_selection(0.25, 0.75)
    session.delete()
    assert session.duration == pytest.approx(total - 0.5, abs=1e-4)
    expected = np.concatenate([y[:int(0.25 * SR)], y[int(0.75 * SR):]])
    assert np.allclose(session.y, expected)


def test_every_splice_says_what_it_did(session):
    """Quietly altering audio is worse than a click, so it is never silent."""
    messages = []
    session.message.connect(messages.append)

    session.set_selection(0.2, 0.4)
    session.delete()
    assert "5 ms crossfade" in messages[-1]

    session.set_crossfade_ms(0.0)
    session.set_selection(0.5, 0.6)
    session.delete()
    assert "no crossfade" in messages[-1]


def test_an_effect_on_a_selection_is_not_crossfaded(session):
    """It replaces the range with a processed version of *itself*; blending the
    edges back would leave 5 ms at each end un-effected."""
    total = session.duration
    session.set_selection(0.2, 0.6)
    quiet = np.asarray(session.selected_audio()) * 0.5
    session.replace_selection(quiet, undo_label="gain")

    assert session.duration == pytest.approx(total, abs=1e-6)
    edge = session.y[int(0.2 * SR):int(0.2 * SR) + 10]
    assert np.allclose(edge, quiet[:10], atol=1e-6)


def test_delete_without_a_selection_does_nothing(session):
    """One keystroke must not be able to delete the whole clip."""
    before = np.array(session.y, copy=True)
    assert session.delete() is False
    assert np.array_equal(session.y, before)


def test_paste_replaces_the_selection(session):
    session.set_selection(0.0, 0.1)
    session.copy()                                  # 0.1 s on the clipboard
    session.set_selection(0.5, 0.9)                 # 0.4 s to be replaced
    total = session.duration
    assert session.paste() is True
    # -0.4 +0.1, and two seams
    assert session.duration == pytest.approx(total - 0.3 - 2 * FADE, abs=1e-3)


def test_paste_inserts_at_a_position_when_nothing_is_selected(session):
    session.set_selection(0.0, 0.25)
    session.copy()
    session.clear_selection()
    total = session.duration
    assert session.paste(at=0.5) is True
    assert session.duration == pytest.approx(total + 0.25 - 2 * FADE, abs=1e-3)


def test_paste_appends_when_no_position_is_given(session):
    session.set_selection(0.0, 0.25)
    session.copy()
    session.clear_selection()
    tail = np.array(session.y[:int(0.25 * SR)], copy=True)
    session.paste()
    # the appended clip is intact past its leading seam
    n = int(FADE * SR)
    assert np.allclose(session.y[-(len(tail) - n):], tail[n:], atol=1e-6)


def test_paste_selects_what_it_pasted(session):
    session.set_selection(0.0, 0.2)
    session.copy()
    session.clear_selection()
    session.paste(at=0.4)
    start, end = session.selection()
    assert start == pytest.approx(0.4, abs=1e-3)
    assert end == pytest.approx(0.6, abs=1e-3)


def test_paste_resamples_audio_from_another_rate(session):
    """The clipboard keeps its own rate; assuming the session's changes speed."""
    session.set_selection(0.0, 0.5)
    session.copy()                                  # 8000 samples at 16 kHz
    clip, _ = session._clipboard
    session._clipboard = (clip, 8000)               # as if copied from an 8 k clip
    session.clear_selection()
    total = session.duration
    assert session.paste(at=0.0) is True
    # the same 8000 samples are 1.0 s of *sound* at 8 kHz, and stay 1.0 s
    # after resampling: what is preserved is duration, not sample count
    assert session.duration == pytest.approx(total + 1.0 - FADE, abs=1e-2)


def test_paste_without_a_clipboard_does_nothing(session):
    assert session.can_paste() is False
    assert session.paste() is False


def test_insert_silence(session):
    total = session.duration
    session.clear_selection()
    assert session.insert_silence(0.5, at=0.25) is True
    assert session.duration == pytest.approx(total + 0.5 - 2 * FADE, abs=1e-3)
    # silent between the two seams
    n = int(FADE * SR)
    quiet = session.y[int(0.25 * SR) + n:int(0.75 * SR) - 2 * n]
    assert np.max(np.abs(quiet)) == 0.0


def test_insert_silence_replaces_a_selection(session):
    total = session.duration
    session.set_selection(0.2, 0.4)
    assert session.insert_silence(0.2) is True
    assert session.duration == pytest.approx(total - 2 * FADE, abs=1e-3)


def test_crop_keeps_only_the_selection(session):
    session.set_selection(0.25, 0.75)
    assert session.crop_to_selection() is True
    assert session.duration == pytest.approx(0.5, abs=1e-3)
    assert session.can_undo() is True


def test_save_selection_writes_only_the_range(session, tmp_path):
    import kudio
    session.set_selection(0.2, 0.7)
    path = session.save_selection(tmp_path / "part.wav")
    y, sr = kudio.file_load(path)
    assert sr == SR
    assert len(y) == pytest.approx(0.5 * SR, abs=2)


def test_save_selection_needs_a_selection(session, tmp_path):
    with pytest.raises(RuntimeError, match="nothing selected"):
        session.save_selection(tmp_path / "part.wav")


# -------------------------------------------------------------------- markers

def test_add_and_list_markers(session):
    session.add_marker(1.0)
    session.add_marker(0.5, 0.8, "noise")
    assert [m.start for m in session.markers] == [0.5, 1.0]
    assert session.markers[0].label == "noise"
    assert session.markers[0].is_range is True
    assert session.markers[1].is_range is False


def test_mark_selection(session):
    assert session.mark_selection() is None
    session.set_selection(0.2, 0.4)
    marker = session.mark_selection("take 1")
    assert (marker.start, marker.end, marker.label) == (0.2, 0.4, "take 1")


def test_rename_and_remove_markers(session):
    session.add_marker(0.1, label="a")
    assert session.rename_marker(0, "b") is True
    assert session.markers[0].label == "b"
    assert session.remove_marker(0) is True
    assert session.markers == ()
    assert session.remove_marker(0) is False


def test_markers_move_with_a_deletion(session):
    """A marker is a position in time; an edit before it moves it."""
    session.add_marker(0.8, 0.9, "later")
    session.set_selection(0.2, 0.4)                 # delete 0.2 s before it
    session.delete()
    marker = session.markers[0]
    # the crossfade removes its own length too, and the marker follows that
    assert marker.start == pytest.approx(0.6 - FADE, abs=1e-3)
    assert marker.end == pytest.approx(0.7 - FADE, abs=1e-3)


def test_markers_before_an_edit_stay_put(session):
    session.add_marker(0.1, 0.15)
    session.set_selection(0.5, 0.7)
    session.delete()
    assert session.markers[0].start == pytest.approx(0.1, abs=1e-3)


def test_markers_move_with_an_insertion(session):
    session.add_marker(0.5)
    session.clear_selection()
    session.insert_silence(0.25, at=0.1)
    assert session.markers[0].start == pytest.approx(0.75 - 2 * FADE, abs=1e-3)


def test_undo_restores_the_markers_too(session):
    session.add_marker(0.8)
    session.set_selection(0.2, 0.4)
    session.delete()
    assert session.markers[0].start == pytest.approx(0.6 - FADE, abs=1e-3)
    session.undo()
    assert session.markers[0].start == pytest.approx(0.8, abs=1e-3)


def test_crop_drops_markers_outside_the_kept_range(session):
    session.add_marker(0.1, label="before")
    session.add_marker(0.5, 0.6, label="inside")
    session.add_marker(0.95, label="after")
    session.set_selection(0.25, 0.75)
    session.crop_to_selection()
    assert [m.label for m in session.markers] == ["inside"]
    assert session.markers[0].start == pytest.approx(0.25, abs=1e-3)


def test_new_audio_clears_the_markers(session):
    session.add_marker(0.5)
    session.set_audio(np.zeros(SR, dtype=np.float32), SR, name="new")
    assert session.markers == ()


def test_set_markers_sorts_them(session):
    session.set_markers([Marker(2.0, 2.0), Marker(0.5, 1.0)])
    assert [m.start for m in session.markers] == [0.5, 2.0]


def test_markers_changed_fires_once_per_change(session):
    seen = []
    session.markers_changed.connect(lambda: seen.append(1))
    session.add_marker(0.5)
    assert len(seen) == 1
    session.set_markers(list(session.markers))      # no actual change
    assert len(seen) == 1


# ----------------------------------------------------- crossfade, per edit

def flat(qapp, seconds: float = 1.0) -> AudioSession:
    """A constant signal: any length lost at a seam is the crossfade's doing
    and nothing else's."""
    session = AudioSession()
    session.set_audio(np.ones(int(seconds * SR), dtype=np.float32), SR)
    return session


def test_a_length_can_be_given_for_one_edit_without_changing_the_default(qapp):
    """A 5 ms splice inside a word and a 200 ms one between takes are
    different jobs; choosing the second must not make every later cut long."""
    session = flat(qapp)
    session.set_selection(0.2, 0.4)

    messages = []
    session.message.connect(messages.append)
    session.delete(crossfade_ms=50.0)

    assert "50 ms crossfade" in messages[-1]
    assert session.crossfade_ms == DEFAULT_CROSSFADE_MS      # default untouched


def test_the_override_actually_changes_how_much_audio_is_swallowed(qapp):
    """The note in the status bar has to describe the audio, not decorate it."""
    def length_after(crossfade_ms):
        session = flat(qapp)
        session.set_selection(0.2, 0.4)
        session.delete(crossfade_ms=crossfade_ms)
        return len(session.y)

    assert length_after(0.0) > length_after(5.0) > length_after(100.0)


def test_zero_for_one_edit_is_a_hard_cut_and_says_so(qapp):
    session = flat(qapp)
    session.set_selection(0.2, 0.4)

    messages = []
    session.message.connect(messages.append)
    session.delete(crossfade_ms=0.0)

    assert "no crossfade" in messages[-1]
    assert len(session.y) == SR - int(0.2 * SR)      # nothing else was taken


def test_a_negative_override_is_treated_as_off_not_as_an_error(qapp):
    session = flat(qapp)
    session.set_selection(0.2, 0.4)
    assert session.delete(crossfade_ms=-10.0) is True
    assert len(session.y) == SR - int(0.2 * SR)


def test_every_splicing_edit_takes_the_override(qapp):
    session = flat(qapp)
    messages = []
    session.message.connect(messages.append)

    session.set_selection(0.1, 0.3)
    session.cut(crossfade_ms=20.0)
    assert "20 ms" in messages[-1]

    session.clear_selection()
    session.paste(at=0.1, crossfade_ms=30.0)
    assert "30 ms" in messages[-1]

    session.clear_selection()
    session.insert_silence(0.1, at=0.2, crossfade_ms=40.0)
    assert "40 ms" in messages[-1]
    assert session.crossfade_ms == DEFAULT_CROSSFADE_MS
