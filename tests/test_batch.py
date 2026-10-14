# -*- coding: utf-8 -*-
"""The Batch panel's two passes past the command line.

"Normalise this tree to -23 LUFS and tell me which files could not get there"
is answered by reading every written file back, so the tests here build files
that cannot get there for each reason in turn and check the reason given.
"Re-room this tree and keep both" is checked the same way: the dry tree must
come through untouched and each wet copy must be exactly its seeded room.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio.panels.batch import (
    LOUDNESS_TOLERANCE,
    BatchOutcome,
    BatchPanel,
    check_loudness,
    file_seed,
    reverberant_folder,
    run_batch,
    trees_overlap,
)
from kudiostudio.session import AudioSession

SR = 16000


def tone(seconds: float = 1.0, amplitude: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def spiky() -> np.ndarray:
    """A whisper with one click in it. Levelled by loudness, the whisper sets
    the gain and the click goes 30 dB past full scale."""
    y = (0.001 * np.random.default_rng(0).standard_normal(SR)).astype(np.float32)
    y[SR // 2] = 0.5
    return y


@pytest.fixture
def tree(tmp_path) -> Path:
    """One file that can reach -23 LUFS and three that cannot, in two folders."""
    src = tmp_path / "in"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir()
    kudio.save_wave(src / "a" / "tone.wav", tone(), SR)
    kudio.save_wave(src / "a" / "spiky.wav", spiky(), SR)
    kudio.save_wave(src / "b" / "short.wav", tone(0.2), SR)
    kudio.save_wave(src / "b" / "silent.wav", np.zeros(SR, np.float32), SR)
    return src


@pytest.fixture
def panel(qapp, tree, tmp_path):
    p = BatchPanel(AudioSession())
    p.src.edit.setText(str(tree))
    p.dst.edit.setText(str(tmp_path / "out"))
    yield p
    p.jobs.wait(30_000)


def run(panel) -> BatchOutcome:
    """The job as Run does it, on this thread, and its report."""
    outcome = run_batch(panel._plan())
    panel._finished(outcome)
    return outcome


def by_name(check) -> dict:
    return {miss.path.name: miss for miss in check.misses}


# ------------------------------------------------------- the loudness report

def test_every_written_file_is_read_back_against_the_target(panel, tmp_path):
    panel.normalize_mode.setCurrentText("LUFS")
    check = run(panel).loudness

    assert check.target == -23.0 and check.tolerance == LOUDNESS_TOLERANCE
    assert check.checked == 4                  # three written, one refused
    assert "tone.wav" not in by_name(check)
    level = kudio.loudness(*kudio.file_load(tmp_path / "out" / "a" / "tone.wav"))
    assert level == pytest.approx(-23.0, abs=0.05)
    assert check.held == 1


def test_a_file_clipped_on_the_way_up_could_not_get_there(panel, tmp_path):
    """Its loudness can even land near the target. It got there by clipping,
    which is not the file that was asked for."""
    panel.normalize_mode.setCurrentText("LUFS")
    miss = by_name(run(panel).loudness)["spiky.wav"]

    assert miss.reason.startswith("clipped at full scale")
    assert miss.headroom is False
    written, _ = kudio.file_load(tmp_path / "out" / "a" / "spiky.wav")
    assert np.max(np.abs(written)) > 0.999, "it really was clipped"


def test_a_file_too_short_to_measure_is_listed_once_in_plain_words(panel):
    panel.normalize_mode.setCurrentText("LUFS")
    outcome = run(panel)

    miss = by_name(outcome.loudness)["short.wav"]
    assert miss.lufs is None
    assert "too short to measure" in miss.reason and "0.4 s" in miss.reason
    # kudio's own failure for it is still in the outcome, but not repeated in
    # the summary beside the plain-words version
    assert any(path.name == "short.wav" for path, _ in outcome.failed)
    assert "short.wav" in panel.misses.toPlainText()
    assert "short.wav" not in panel.result.text()


def test_a_silent_file_has_no_level_to_raise(panel):
    panel.normalize_mode.setCurrentText("LUFS")
    miss = by_name(run(panel).loudness)["silent.wav"]
    assert miss.lufs is None and miss.reason.startswith("silent")


def test_a_float_tree_is_on_target_and_says_it_has_no_headroom(panel):
    """FLOAT keeps the click 30 dB over full scale: the level is right, and the
    first fixed-point copy will clip."""
    panel.normalize_mode.setCurrentText("LUFS")
    panel.subtype.setCurrentText("FLOAT")
    check = run(panel).loudness

    miss = by_name(check)["spiky.wav"]
    assert miss.headroom is True
    assert miss.lufs == pytest.approx(-23.0, abs=0.05)
    assert "dBFS" in miss.reason and "fixed-point" in miss.reason
    assert miss in check.over_ceiling and miss not in check.off_target


def test_a_true_peak_over_the_ceiling_is_on_target_and_still_listed(tmp_path):
    """A sine at a quarter of the rate, sampled 45° off its crest, never puts a
    sample above 0.8 -- and peaks at +1.2 dBTP between them."""
    n = np.arange(2 * SR)
    y = (1.13 * np.sin(2 * np.pi * n / 4 + np.pi / 4)).astype(np.float32)
    path = tmp_path / "overshoot.wav"
    kudio.save_wave(path, y, SR, subtype="FLOAT")
    level = kudio.loudness(y, SR)
    assert np.max(np.abs(y)) < 0.8

    miss, = check_loudness([path], level).misses
    assert miss.headroom is True
    assert "true peak +1.2 dBTP" in miss.reason and "-1.0 dBTP" in miss.reason

    assert check_loudness([path], level, ceiling=0.0).misses
    assert not check_loudness([path], level, ceiling=2.0).misses


def test_the_tolerance_is_the_one_stated(tmp_path):
    path = tmp_path / "tone.wav"
    kudio.save_wave(path, tone(amplitude=0.1), SR)
    level = kudio.loudness(*kudio.file_load(path))

    assert not check_loudness([path], level - 0.8, tolerance=1.0).misses
    miss, = check_loudness([path], level - 0.8, tolerance=0.5).misses
    assert miss.reason == "0.8 LU over" and miss.headroom is False
    miss, = check_loudness([path], level + 0.8, tolerance=0.5).misses
    assert miss.reason == "0.8 LU under"


def test_with_a_denoise_pass_the_tree_is_levelled_last(panel, tmp_path):
    """Denoising takes level out with the noise. Levelled by the conversion
    and denoised after, the tree landed under the target it was asked for,
    and the report could only say why."""
    panel.normalize_mode.setCurrentText("LUFS")
    panel.denoise.setCurrentIndex(panel.denoise.findData("logmmse"))
    convert, denoise, level, *_ = panel.call.text().splitlines()
    assert "lufs" not in convert
    assert "enhance_folder" in denoise
    assert "normalize_lufs(y, sr, lufs=-23.0), after the denoise" in level

    outcome = run(panel)
    check = outcome.loudness
    assert "tone.wav" not in by_name(check)
    written = kudio.file_load(tmp_path / "out" / "a" / "tone.wav")
    assert kudio.loudness(*written) == pytest.approx(-23.0, abs=0.05)
    assert "after the denoise pass" in panel.result.text()

    # too short to level: the conversion wrote it, nothing could level it,
    # and it is listed once, in plain words
    miss = by_name(check)["short.wav"]
    assert "too short to measure" in miss.reason
    assert any(path.name == "short.wav" for path, _ in outcome.levelled.failed)
    assert "short.wav" not in panel.result.text()
    assert check.checked == 4


def test_without_a_denoise_pass_the_conversion_levels(panel):
    panel.normalize_mode.setCurrentText("LUFS")
    plan = panel._plan()
    assert plan.level_last is False
    assert plan.conversion()["lufs"] == -23.0
    assert run_batch(plan).levelled is None


def test_the_denoise_pass_keeps_the_subtype_the_conversion_wrote(panel, tmp_path):
    """enhance_folder writes PCM_16 unless told otherwise, so a FLOAT tree used
    to come back from the denoise pass 16-bit."""
    import soundfile

    panel.subtype.setCurrentText("FLOAT")
    panel.denoise.setCurrentIndex(panel.denoise.findData("logmmse"))
    assert "subtype='FLOAT'" in panel.call.text().splitlines()[1]
    run(panel)
    written = list((tmp_path / "out").rglob("*.wav"))
    assert written
    assert {soundfile.info(str(p)).subtype for p in written} == {"FLOAT"}


def test_there_is_no_report_without_a_loudness_target(panel):
    panel.normalize_mode.setCurrentText("peak")
    outcome = run(panel)
    assert outcome.loudness is None
    assert panel.misses.isVisible() is False
    assert "read back" not in panel.call.text()


def test_the_report_controls_follow_the_mode(panel):
    assert panel.loudness_check.isEnabled() is False
    panel.normalize_mode.setCurrentText("LUFS")
    assert panel.loudness_check.isEnabled() is True
    panel.tolerance.setValue(0.5)
    panel.ceiling.setValue(-2.0)
    last = panel.call.text().splitlines()[-1]
    assert "within ±0.5 LU of -23 LUFS" in last and "-2 dBTP" in last

    plan = panel._plan()
    assert (plan.tolerance, plan.ceiling) == (0.5, -2.0)
    panel.normalize_mode.setCurrentText("none")
    assert panel.loudness_check.isEnabled() is False


def test_every_file_that_missed_is_listed_not_the_first_five(qapp, tmp_path):
    """"Tell me which files" is the job; "… and 7 more" does not do it."""
    src = tmp_path / "in"
    src.mkdir()
    for i in range(12):
        kudio.save_wave(src / f"quiet{i:02d}.wav", np.zeros(SR, np.float32), SR)
    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(tmp_path / "out"))
    panel.normalize_mode.setCurrentText("LUFS")
    run(panel)

    listed = [line for line in panel.misses.toPlainText().splitlines()
              if "silent" in line]
    assert len(listed) == 12
    assert "0 of 12" in panel.result.text()


def test_run_on_the_worker_reports_the_same(panel):
    """The panel's widgets are read before the job starts, not from the
    worker while they can still change."""
    from PySide6.QtCore import QEventLoop, QTimer

    panel.normalize_mode.setCurrentText("LUFS")
    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    panel._run()
    panel.normalize_mode.setCurrentText("none")        # too late to matter
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)

    text = panel.misses.toPlainText()
    assert "spiky.wav" in text and "short.wav" in text and "silent.wav" in text
    assert "1 of 4 file(s)" in panel.result.text()


# -------------------------------------------------------- reverberant copies

@pytest.fixture
def pair(qapp, tmp_path):
    """The same take in two folders: a mirrored tree repeats names."""
    src = tmp_path / "in"
    for folder in ("a", "b"):
        (src / folder).mkdir(parents=True)
        kudio.save_wave(src / folder / "take.wav", tone(), SR)
    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(tmp_path / "out"))
    panel.room_box.setChecked(True)
    return panel


def test_a_reverberant_copy_keeps_the_dry_tree_and_pairs_by_path(pair, tmp_path):
    outcome = run(pair)

    wet_root = tmp_path / "out_reverberant"
    assert outcome.rooms.folder == wet_root == reverberant_folder(tmp_path / "out")
    assert {p.relative_to(wet_root).as_posix() for _, p in outcome.rooms.written} \
        == {"a/take.wav", "b/take.wav"}
    for dry_path, wet_path in outcome.rooms.written:
        dry, dry_sr = kudio.file_load(dry_path)
        wet, wet_sr = kudio.file_load(wet_path)
        # the dry file is what the conversion wrote, untouched
        assert np.array_equal(dry, kudio.file_load(tmp_path / "in" /
                                                   dry_path.relative_to(tmp_path / "out"))[0])
        assert (wet_sr, len(wet)) == (dry_sr, len(dry))
        assert not np.allclose(wet, dry, atol=1e-3)


def test_each_wet_copy_is_its_seeded_room_and_nothing_else(pair, tmp_path):
    pair.subtype.setCurrentText("FLOAT")          # compare arithmetic, not rounding
    pair.room_rt60.setValue(0.7)
    pair.room_drr.setValue(3.0)
    pair.room_seed.setValue(42)
    outcome = run(pair)

    for dry_path, wet_path in outcome.rooms.written:
        dry, sr = kudio.file_load(dry_path)
        rel = dry_path.relative_to(tmp_path / "out")
        expected = kudio.apply_rir(dry, kudio.rir(sr, rt60=0.7, drr_db=3.0,
                                                  seed=file_seed(42, rel)))
        assert np.array_equal(kudio.file_load(wet_path)[0], expected)


def test_the_same_take_in_two_places_gets_two_rooms_and_the_seed_repeats_them(
        pair, tmp_path):
    first = run(pair)
    a, b = (kudio.file_load(wet)[0] for _, wet in sorted(first.rooms.written))
    assert not np.allclose(a, b, atol=1e-3), "one room for the whole corpus"

    pair.dst.edit.setText(str(tmp_path / "again"))
    again = run(pair)
    for (_, one), (_, two) in zip(sorted(first.rooms.written),
                                  sorted(again.rooms.written)):
        assert np.array_equal(kudio.file_load(one)[0], kudio.file_load(two)[0])


def test_a_random_seed_is_drawn_at_run_and_reported(pair):
    pair.room_seed.setValue(-1)
    assert "file_seed(…, path)" in pair.call.text()

    plan = pair._plan()
    assert isinstance(plan.room.seed, int)
    outcome = run_batch(plan)
    pair._finished(outcome)
    assert outcome.rooms.seed == plan.room.seed
    assert f"seed {plan.room.seed}" in pair.result.text()


def test_the_caption_names_the_room_and_where_it_goes(pair, tmp_path):
    pair.room_rt60.setValue(0.8)
    pair.room_seed.setValue(9)
    line = pair.call.text().splitlines()[-1]
    assert "kudio.rir(sr, rt60=0.8, drr_db=0.0, seed=file_seed(9, path))" in line
    assert repr(str(tmp_path / "out_reverberant")) in line

    pair.room_dst.edit.setText(str(tmp_path / "wet"))
    assert repr(str(tmp_path / "wet")) in pair.call.text()
    pair.room_box.setChecked(False)
    assert "apply_rir" not in pair.call.text()


@pytest.mark.parametrize("where", ["out", "out/wet"])
def test_the_copies_are_refused_a_folder_that_overlaps_the_output(pair, tmp_path,
                                                                  where):
    messages = []
    pair.status.connect(messages.append)
    pair.room_dst.edit.setText(str(tmp_path / where))
    pair._run()
    assert "folder of their own" in messages[-1]
    assert not (tmp_path / "out").exists(), "nothing was written"


def test_trees_overlap():
    assert trees_overlap("/data/out", "/data/out")
    assert trees_overlap("/data/out/wet", "/data/out")
    assert trees_overlap("/data", "/data/out")
    assert not trees_overlap("/data/out_reverberant", "/data/out")


def test_with_a_loudness_target_the_copies_are_levelled_too(pair):
    """The room moves energy into the tail. Kept at the dry file's peak, the
    copy would be at another loudness than the tree it is beside."""
    pair.normalize_mode.setCurrentText("LUFS")
    outcome = run(pair)

    assert outcome.loudness.checked == 4             # both trees
    assert not outcome.loudness.misses
    for _, wet in outcome.rooms.written:
        assert kudio.loudness(*kudio.file_load(wet)) == pytest.approx(-23.0, abs=0.05)


def test_a_files_seed_depends_on_its_path_not_its_name():
    assert file_seed(7, "a/take.wav") != file_seed(7, "b/take.wav")
    assert file_seed(7, "a/take.wav") == file_seed(7, Path("a") / "take.wav")
    assert file_seed(7, "a/take.wav") != file_seed(8, "a/take.wav")
    assert file_seed(2 ** 31 - 1, "x.wav") >= 0
