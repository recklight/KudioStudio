# -*- coding: utf-8 -*-
"""The edit history as a script.

Every undoable step records the code that repeats it, and the exported script
run on the original file has to land on the audio the session holds — not
roughly, but sample for sample, since a script that drifts is a script nobody
can trust in a batch job.
"""
from __future__ import annotations

import runpy
import subprocess
import sys

import numpy as np
import pytest

import kudio
from kudiostudio.panels.compare import ComparePanel
from kudiostudio.panels.effects import EffectsPanel
from kudiostudio.panels.enhance import LEARNED, TRAD, WAVELET, EnhancePanel
from kudiostudio.session import UNDO_DEPTH, AudioSession

SR = 16000

#: skipped without kudio-enhance and PyTorch -- see conftest.py
requires_enhance = pytest.mark.learned


def run_script(session, tmp_path, name: str = "edits"):
    """Export the history, run it in a fresh interpreter, read what it wrote.

    Written as FLOAT, so what is compared is the arithmetic and not the
    rounding to 16 bits that Save As would add on both sides.
    """
    script = tmp_path / f"{name}.py"
    script.write_text(session.edit_script(subtype="FLOAT"), encoding="utf-8")
    out = tmp_path / f"{name}.wav"
    done = subprocess.run([sys.executable, str(script), str(out)],
                          capture_output=True, text=True, timeout=600,
                          cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    return kudio.file_load(out)


def assert_reproduces(session, tmp_path, name: str = "edits") -> None:
    y, sr = run_script(session, tmp_path, name)
    assert sr == session.sr
    assert len(y) == len(session.y)
    assert np.allclose(y, session.y, rtol=0.0, atol=1e-6)


def replay(step, before: np.ndarray, sr: int) -> np.ndarray:
    """Run one step's code on *before*, the way the script runs it."""
    namespace = {"np": np, "kudio": kudio, "y": before, "sr": sr}
    exec(step.code, namespace)
    return namespace["y"]


def run_and_wait(panel) -> None:
    """Denoising and comparing run on a worker; drive the loop until it lands."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    panel._run()
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)


def gain(session: AudioSession, db: float) -> None:
    """A whole-clip or selected-range edit that says what it did."""
    session.replace_selection(kudio.gain(session.selected_audio(), db),
                              undo_label="gain", code=f"y = kudio.gain(y, db={db})")


# ------------------------------------------------------------- the property

def test_the_exported_script_makes_the_audio_the_session_holds(real_session,
                                                               tmp_path):
    session = real_session
    effects = EffectsPanel(session)

    # an effect on the whole clip
    effects.effect.setCurrentText("highpass")
    effects.hp_cutoff.setValue(120.0)
    effects._apply()

    # an effect on a selection, one that changes the selection's length
    session.set_selection(0.3, 0.9)
    effects.effect.setCurrentText("time_stretch")
    effects.stretch_rate.setValue(1.25)
    effects._apply()

    # a replacement joined back with a crossfade of its own length
    session.set_selection(1.0, 1.2)
    session.replace_selection(kudio.reverse(session.selected_audio()),
                              undo_label="reverse", crossfade=True,
                              crossfade_ms=20.0, code="y = kudio.reverse(y)")

    session.set_selection(0.2, 0.4)
    session.cut(crossfade_ms=30.0)

    # copy, change the audio, paste somewhere else: the paste must be what
    # was copied, not what is at those samples now
    session.set_selection(0.5, 0.7)
    session.copy()
    session.clear_selection()
    effects.effect.setCurrentText("random_gain")        # seed left on random
    effects._apply()
    session.clear_selection()
    session.paste(at=1.3)

    session.set_crossfade_ms(12.0)
    session.clear_selection()
    session.insert_silence(0.25, at=0.1)

    session.set_selection(0.8, 0.9)                     # the same clip again,
    session.paste()                                     # over a selection

    session.set_selection(0.05, 1.8)
    session.crop_to_selection()

    enhance = EnhancePanel(session)
    enhance.method.setCurrentIndex(enhance.method.findData("logmmse"))
    run_and_wait(enhance)

    assert [step.label for step in session.history][:-1] == [
        "highpass", "time_stretch", "reverse", "cut", "random_gain", "paste",
        "insert silence", "paste", "crop"]
    assert "logmmse" in session.history[-1].code
    assert session.edit_script_warnings() == []
    assert_reproduces(session, tmp_path)


def test_the_steps_read_as_labelled_code(session):
    session.set_selection(0.25, 0.5)
    gain(session, -6.0)
    session.set_selection(0.1, 0.2)
    session.delete(crossfade_ms=0.0)

    script = session.edit_script()
    assert "# 1. gain\nhead, y, tail = y[:4000], y[4000:8000], y[8000:]" in script
    assert "y = kudio.gain(y, db=-6.0)\ny = np.concatenate([head, y, tail])" in script
    # the hard cut that was asked for, not the session's default crossfade
    assert "# 2. delete\ny = np.concatenate([y[:1600], y[3200:]])" in script
    assert script.rstrip().endswith('print(f"wrote {OUTPUT}: {len(y) / sr:.3f} s '
                                    'at {sr} Hz")')
    assert "kudio.save_wave(OUTPUT, y, sr, subtype='PCM_16')" in script


def test_the_original_is_read_the_way_the_session_read_it(qapp, tone):
    """Same arguments to file_load, so the script starts from the same samples."""
    session = AudioSession()
    session.load_file(tone, sr=8000)
    gain(session, 3.0)
    script = session.edit_script()
    assert "kudio.file_load(SOURCE, sr=8000, mono=True)" in script
    assert f"SOURCE = {str(tone.absolute())!r}" in script

    session.load_file(tone)
    gain(session, 3.0)
    script = session.edit_script()
    assert "kudio.file_load(SOURCE, sr=None, mono=True)" in script
    # every position in the script is a sample index at this rate
    assert "if sr != 16000:" in script


# ------------------------------------------------------------- the history

def test_undo_takes_the_step_and_its_code_out(session):
    session.set_selection(0.2, 0.4)
    session.delete()
    session.clear_selection()
    gain(session, 6.0)
    assert [step.label for step in session.history] == ["delete", "gain"]
    assert "kudio.gain(y, db=6.0)" in session.edit_script()

    session.undo()
    assert [step.label for step in session.history] == ["delete"]
    script = session.edit_script()
    assert "kudio.gain" not in script
    assert "# 1. delete" in script and "# 2." not in script


def test_an_edit_too_old_to_undo_is_still_in_the_script(session):
    """The undo stack forgets after UNDO_DEPTH; the audio does not."""
    for _ in range(UNDO_DEPTH + 3):
        gain(session, 0.5)
    while session.undo():
        pass
    assert len(session.history) == 3

    y, sr = session.original()
    for step in session.history:
        y = replay(step, y, sr)
    assert np.array_equal(y, session.y)


def test_revert_and_a_new_file_start_the_history_again(session, recording):
    gain(session, 6.0)
    assert session.has_history()

    session.revert()
    assert session.history == ()
    assert session.has_history() is False

    gain(session, 6.0)
    session.load_file(recording)
    assert session.history == ()
    gain(session, -3.0)
    assert f"SOURCE = {str(recording.absolute())!r}" in session.edit_script()


def test_a_recording_starts_a_history_the_script_cannot_read_back(session):
    session.set_audio(np.zeros(SR, dtype=np.float32), SR, name="recording")
    assert session.history == ()
    gain(session, 6.0)

    warnings = session.edit_script_warnings()
    assert warnings and "recorded" in warnings[0]
    assert "WARNING" in session.edit_script()


def test_a_step_without_code_stops_the_script_instead_of_skipping_it(
        session, tmp_path, monkeypatch):
    gain(session, 6.0)
    session.set_audio(session.y * 0.5, session.sr, undo_label="halve",
                      keep_original=True)
    gain(session, -6.0)

    assert session.history[1].code is None
    assert session.edit_script_warnings() == [
        "step 2 (halve) was not recorded as code"]
    script = tmp_path / "edits.py"
    script.write_text(session.edit_script(), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script), str(tmp_path / "out.wav")])
    with pytest.raises(NotImplementedError, match="step 2"):
        runpy.run_path(str(script), run_name="__main__")
    assert not (tmp_path / "out.wav").exists(), "no file is better than a wrong one"


def test_a_result_that_is_not_float32_is_converted_in_the_script_too(session):
    """The session stores float32; a step that skipped the conversion would
    feed the next one different numbers."""
    session.set_selection(0.25, 0.5)
    session.replace_selection(session.selected_audio().astype(np.float64) / 3,
                              undo_label="third", code="y = y.astype(np.float64) / 3")
    session.replace_selection(session.y.astype(np.float64) / 3,
                              undo_label="third", code="y = y.astype(np.float64) / 3")

    y, sr = session.original()
    for step in session.history:
        assert "dtype=np.float32" in step.code
        y = replay(step, y, sr)
    assert y.dtype == np.float32
    assert np.array_equal(y, session.y)


# ------------------------------------------------------------- the clipboard

def test_a_copy_from_before_an_undone_edit_is_rebuilt(session, tmp_path):
    """The clipboard outlives undo. What it holds came from audio the history
    no longer passes through, so a `clip = y[a:b]` line would copy the wrong
    samples."""
    gain(session, -12.0)
    session.set_selection(0.1, 0.3)
    session.copy()
    session.undo()
    session.clear_selection()
    session.replace_selection(kudio.reverse(session.y), undo_label="reverse",
                              code="y = kudio.reverse(y)")
    session.clear_selection()
    session.paste(at=0.5)

    assert "def copied():" in session.edit_script()
    assert session.edit_script_warnings() == []
    assert_reproduces(session, tmp_path)


def test_a_paste_from_another_file_at_another_rate_is_rebuilt(
        qapp, tmp_path, recording):
    other = tmp_path / "other.wav"
    t = np.arange(SR // 2) / 8000
    kudio.save_wave(other, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32),
                    8000)

    session = AudioSession()
    session.load_file(other)
    session.set_selection(0.2, 0.6)
    gain(session, 6.0)
    session.set_selection(0.1, 0.5)
    session.copy()

    session.load_file(recording)
    session.clear_selection()
    session.paste(at=1.0)

    script = session.edit_script()
    assert repr(str(other.absolute())) in script
    assert "kudio.resample(clip, 8000, sr)" in script
    assert_reproduces(session, tmp_path)


# ---------------------------------------------------------------- the panels

@pytest.mark.parametrize("name", [effect for effect in EffectsPanel.EFFECTS
                                  if effect != "split_on_silence"])
def test_every_effect_records_the_call_it_applied(real_session, tmp_path, name):
    """Replayed on the same audio, the recorded code gives what Apply gave.

    This is what stops the caption and the computation drifting apart: a
    parameter the caption leaves out shows up here as a different result.
    """
    panel = EffectsPanel(real_session)
    panel.effect.setCurrentText(name)
    if name == "add_noise_snr":
        noise = tmp_path / "noise.wav"
        kudio.save_wave(noise, 0.1 * np.random.default_rng(3).standard_normal(SR)
                        .astype(np.float32), SR)
        panel._noise_path = noise
    if name == "measured_room":
        ir = tmp_path / "hall.wav"
        response = np.concatenate([np.zeros(240, dtype=np.float32),
                                   kudio.rir(48000, rt60=0.4, seed=5)])
        kudio.save_wave(ir, response, 48000, subtype="FLOAT")
        assert panel.load_ir(ir)
    if name == "bit_depth":
        panel.depth_dither.setChecked(True)        # the seed matters with dither
    real_session.set_selection(0.5, 1.5)
    before = real_session.y.copy()
    panel._apply()

    assert [step.label for step in real_session.history] == [name]
    assert np.array_equal(replay(real_session.history[0], before,
                                 real_session.sr), real_session.y)


def test_a_measured_room_is_reproduced_by_the_script(real_session, tmp_path):
    """The response is read from its file in the script, at the clip's rate
    and from the direct sound on -- the same read the panel made."""
    session = real_session
    path = tmp_path / "hall.wav"
    response = np.concatenate([np.zeros(576, dtype=np.float32),
                               kudio.rir(48000, rt60=0.5, drr_db=2.0, seed=4)])
    kudio.save_wave(path, np.stack([response, 0.7 * response], axis=1), 48000,
                    subtype="FLOAT")

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("measured_room")
    assert panel.load_ir(path)
    session.set_selection(0.4, 1.2)
    panel._apply()
    session.clear_selection()
    panel.ir_align.setChecked(False)
    panel._apply()

    script = session.edit_script()
    assert f"kudio.file_load({str(path.absolute())!r}, sr=sr)[0][192:]" in script
    assert_reproduces(session, tmp_path)


def test_a_random_seed_is_pinned_to_the_one_apply_drew(session):
    """Random means a new result each Apply, and the history has to say which."""
    panel = EffectsPanel(session)
    panel.effect.setCurrentText("random_gain")
    assert "seed=None" in panel.call.text()

    panel._apply()
    panel._apply()
    first, second = (step.code for step in session.history)
    assert "seed=None" not in first and "seed=None" not in second
    assert first != second


@pytest.mark.parametrize("method", ["logmmse", WAVELET, TRAD])
def test_a_denoise_records_the_call_the_worker_ran(session, method):
    panel = EnhancePanel(session)
    panel.method.setCurrentIndex(panel.method.findData(method))
    before = session.y.copy()
    run_and_wait(panel)

    assert len(session.history) == 1
    assert np.array_equal(replay(session.history[0], before, session.sr),
                          session.y)


def test_a_denoise_of_audio_edited_while_it_ran_is_not_given_code(session):
    """It was computed from audio the history no longer holds, so any code
    would repeat it on different audio."""
    from PySide6.QtCore import QEventLoop, QTimer

    panel = EnhancePanel(session)
    panel.method.setCurrentIndex(panel.method.findData("logmmse"))
    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    panel._run()
    gain(session, -3.0)                    # lands before the result does
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)

    assert [step.code is None for step in session.history] == [False, True]
    assert session.edit_script_warnings()


def test_pressing_denoise_again_mid_run_leaves_the_running_jobs_code_alone(
        session):
    """The second press is refused; it must not relabel the first result."""
    from PySide6.QtCore import QEventLoop, QTimer

    panel = EnhancePanel(session)
    panel.method.setCurrentIndex(panel.method.findData("logmmse"))
    messages = []
    panel.status.connect(messages.append)
    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    panel._run()
    panel.method.setCurrentIndex(panel.method.findData(WAVELET))
    panel._run()
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)

    assert "A denoising job is already running." in messages
    assert len(session.history) == 1
    assert "'logmmse'" in session.history[0].code
    assert "wavelet" not in session.history[0].code


def learned_denoise(session, run_dir) -> None:
    """Denoise with a trained model through the panel's own Denoise path, on
    its worker thread.

    The panel is let go as soon as this returns, with its finished worker
    still awaiting deletion -- which crashed this process while a finished
    worker still held what it was handed (see `WorkerHost`).
    """
    panel = EnhancePanel(session)
    panel.method.setCurrentIndex(panel.method.findData(LEARNED))
    panel.run_dir.edit.setText(str(run_dir))
    run_and_wait(panel)


@requires_enhance
def test_a_learned_denoise_is_reproduced_by_the_script(session, trained_run,
                                                       tmp_path):
    run_dir, _ = trained_run
    session.set_selection(0.1, 0.3)
    session.delete()
    learned_denoise(session, run_dir)

    assert "Enhancer.load" in session.history[-1].code
    assert_reproduces(session, tmp_path)


@requires_enhance
def test_a_learned_denoise_at_another_rate_records_the_round_trip(
        qapp, tone, trained_run):
    """In at the model's rate and back out at the clip's, as the panel did."""
    run_dir, model_sr = trained_run
    session = AudioSession()
    session.load_file(tone, sr=2 * model_sr)
    learned_denoise(session, run_dir)

    y, sr = session.original()
    assert np.array_equal(replay(session.history[0], y, sr), session.y)


def test_applying_a_compared_result_records_the_call_that_made_it(real_session):
    panel = ComparePanel(real_session)
    panel.metrics.setChecked(False)
    panel.selection_only.setChecked(True)
    real_session.set_selection(0.5, 1.5)
    run_and_wait(panel)

    panel.table.selectRow(0)
    before = real_session.y.copy()
    panel._apply()
    step = real_session.history[-1]
    assert "kudio.spectral_enhance" in step.code
    assert np.array_equal(replay(step, before, real_session.sr), real_session.y)

    # The same result put into a range it was not computed from: no code can
    # say what that was, and the export has to know
    real_session.undo()
    real_session.set_selection(0.6, 1.6)
    panel._apply()
    assert real_session.history[-1].code is None
    panel.jobs.wait(5000)


def test_a_copy_from_an_earlier_take_is_not_read_from_this_one(qapp, tmp_path):
    """Two takes at one rate used to be the same origin, and a copy made
    before any edit counted as made earlier in this history: the script
    pasted this take's own samples where the session pasted the other's,
    and said nothing."""
    t = np.arange(SR) / SR
    first = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    second = (0.1 * np.random.default_rng(1).standard_normal(2 * SR)
              ).astype(np.float32)

    session = AudioSession()
    session.set_audio(first, SR, name="recording")
    session.set_selection(0.0, 0.5)
    session.copy()
    session.set_audio(second, SR, name="recording")
    session.clear_selection()
    session.paste(at=1.0)

    warnings = session.edit_script_warnings()
    assert any("clipboard came from a recording" in w for w in warnings)
    script = session.edit_script()
    assert "clip = y[0:8000]" not in script
    assert "NotImplementedError('the clipboard pasted here came from a " \
           "recording')" in script


def test_a_copy_within_one_take_is_still_taken_where_it_was_made(qapp):
    session = AudioSession()
    session.set_audio(np.linspace(-0.5, 0.5, 2 * SR, dtype=np.float32), SR,
                      name="recording")
    session.set_selection(0.0, 0.5)
    session.copy()
    session.clear_selection()
    session.paste(at=1.0)

    assert "clip = y[0:8000].copy()  # pasted in step 1" in session.edit_script()
    assert len(session.edit_script_warnings()) == 1, "only the placeholder"
    assert "placeholder" in session.edit_script_warnings()[0]


def test_a_copy_from_before_a_revert_is_still_the_same_audio(session, tmp_path):
    """Revert goes back to the audio the history starts from, so a copy from
    there is a copy from the start of this history."""
    session.set_selection(0.1, 0.3)
    session.copy()
    gain(session, -6.0)
    session.revert()
    session.clear_selection()
    session.paste(at=0.5)

    assert "clip = y[1600:4800].copy()" in session.edit_script()
    assert session.edit_script_warnings() == []
    assert_reproduces(session, tmp_path)


# ---------------------------------------------------------------- the source

def _take(tmp_path, name: str = "take.wav"):
    path = tmp_path / name
    t = np.arange(2 * SR) / SR
    kudio.save_wave(path, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32),
                    SR)
    return path


def test_saving_over_the_source_stops_the_script_instead_of_editing_twice(
        qapp, tmp_path):
    """Save As suggests the file that was opened. Saved over, SOURCE held the
    edited audio, and the script applied every edit to it a second time: no
    warning, and a shorter file than the one in the window."""
    path = _take(tmp_path)
    session = AudioSession()
    session.load_file(path)
    session.set_selection(0.5, 1.0)
    session.delete()
    session.clear_selection()
    gain(session, -6.0)
    session.save(path)

    warnings = session.edit_script_warnings()
    assert len(warnings) == 1
    assert "take.wav was saved over" in warnings[0]
    assert "every edit a second time" in warnings[0]

    script = tmp_path / "edits.py"
    script.write_text(session.edit_script(), encoding="utf-8")
    out = tmp_path / "out.wav"
    done = subprocess.run([sys.executable, str(script), str(out)],
                          capture_output=True, text=True, timeout=600,
                          cwd=tmp_path)
    assert done.returncode != 0
    assert "is not the file these edits were made on" in done.stderr
    assert not out.exists(), "no file is better than a wrong one"


def test_a_script_exported_before_the_save_stops_too(qapp, tmp_path):
    """The check is in the script, so it holds whenever the file changes."""
    path = _take(tmp_path)
    session = AudioSession()
    session.load_file(path)
    session.set_selection(0.5, 1.0)
    session.delete()
    script = tmp_path / "edits.py"
    script.write_text(session.edit_script(subtype="FLOAT"), encoding="utf-8")
    assert session.edit_script_warnings() == []

    y, sr = run_script(session, tmp_path, "before")      # still the original
    assert len(y) == len(session.y)

    session.save(path)
    done = subprocess.run([sys.executable, str(script),
                           str(tmp_path / "after.wav")],
                          capture_output=True, text=True, timeout=600,
                          cwd=tmp_path)
    assert done.returncode != 0
    assert "is not the file these edits were made on" in done.stderr


def test_a_save_that_leaves_the_size_and_the_time_alone_is_still_caught(
        qapp, tmp_path):
    """A gain keeps the length, so the file keeps its size, and a save in the
    same tick of a coarse clock keeps its time: Save As remembers instead."""
    import os

    path = _take(tmp_path)
    session = AudioSession()
    session.load_file(path)
    before = os.stat(path)
    gain(session, -6.0)
    session.save(path)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(path).st_size == before.st_size

    assert "was saved over" in session.edit_script_warnings()[0]


def test_a_source_changed_by_another_program_is_named(qapp, tmp_path):
    path = _take(tmp_path)
    session = AudioSession()
    session.load_file(path)
    gain(session, 3.0)
    kudio.save_wave(path, np.zeros(SR, dtype=np.float32), SR)

    warning, = session.edit_script_warnings()
    assert "take.wav has changed on disk since it was opened" in warning

    path.unlink()
    warning, = session.edit_script_warnings()
    assert "take.wav is gone" in warning


def test_saving_elsewhere_changes_nothing(session, tmp_path):
    gain(session, -6.0)
    session.save(tmp_path / "elsewhere.wav")
    assert session.edit_script_warnings() == []
    assert_reproduces(session, tmp_path)


def test_opening_the_saved_file_starts_a_history_on_its_own_bytes(
        qapp, tmp_path):
    """Reopened after the save, the file is a new starting point, and a
    script from there checks for the new bytes."""
    path = _take(tmp_path)
    session = AudioSession()
    session.load_file(path)
    gain(session, -6.0)
    session.save(path, subtype="FLOAT")
    session.load_file(path)
    gain(session, 3.0)
    assert session.edit_script_warnings() == []
    assert_reproduces(session, tmp_path)


# ------------------------------------------------- names in the comments

@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows file names cannot hold a newline")
def test_a_file_name_cannot_put_code_into_the_script(qapp, tmp_path):
    """The name goes into the header comment. A newline in it ended the
    comment, and what followed ran with the script."""
    name = "take\nprint('INJECTED')\n#\r.wav"
    path = _take(tmp_path, name)
    session = AudioSession()
    session.load_file(path)
    session.set_selection(0.1, 0.2)
    session.delete()

    script = session.edit_script(subtype="FLOAT")
    compile(script, "edits.py", "exec")
    header = script.splitlines()[1]
    assert header.startswith("# take\\nprint('INJECTED')\\n#\\r.wav, edited in")
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "x.wav")],
                          capture_output=True, text=True, timeout=600,
                          cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "INJECTED" not in done.stdout


def test_a_label_cannot_put_code_into_the_script(session, tmp_path):
    session.set_selection(0.1, 0.2)
    session.delete(undo_label="delete\rprint('INJECTED')\x00")
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="halve\nprint('INJECTED')",
                      keep_original=True)            # no code: also a warning

    script = session.edit_script()
    compile(script, "edits.py", "exec")
    assert "# 1. delete\\rprint('INJECTED')\\x00" in script
    assert "#   - step 2 (halve\\nprint('INJECTED')) was not recorded" in script
    # the list the window shows keeps the label as it is
    assert session.edit_script_warnings() == [
        "step 2 (halve\nprint('INJECTED')) was not recorded as code"]


@requires_enhance
def test_a_model_trained_again_is_used_and_recorded_as_what_it_is(
        session, trained_run, tmp_path):
    """Trained again in the same run directory, the model used to stay cached
    under the directory's name: Denoise went on with the old weights while
    the history recorded a load of the new ones. And a script recorded
    against the first model now stops, rather than denoise with the second."""
    import shutil

    from kudio_enhance.checkpoint import load_checkpoint, save_checkpoint

    trained, _ = trained_run
    run_dir = tmp_path / "exp"
    shutil.copytree(trained, run_dir)          # the shared run stays as it is
    panel = EnhancePanel(session)
    panel.method.setCurrentIndex(panel.method.findData(LEARNED))
    panel.run_dir.edit.setText(str(run_dir))
    run_and_wait(panel)
    first = session.y.copy()

    checkpoint_path = next(run_dir.glob("*.pt"))
    checkpoint = load_checkpoint(checkpoint_path)
    save_checkpoint(checkpoint_path,
                    {k: 0.5 * v for k, v in checkpoint["state_dict"].items()},
                    model=checkpoint["model"], target=checkpoint["target"])
    run_and_wait(panel)

    assert np.array_equal(replay(session.history[-1], first, session.sr),
                          session.y)
    y, sr = session.original()
    with pytest.raises(RuntimeError, match="trained again"):
        replay(session.history[0], y, sr)
