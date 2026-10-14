# -*- coding: utf-8 -*-
"""The Manifest panel reads what `kudio.Synthesizer` writes, and finds it on disk.

Every corpus here is synthesised for real: a manifest written by hand would
only test that the panel agrees with the person who wrote the test.
"""
from __future__ import annotations

import json
import os
import threading
import warnings
from pathlib import Path

import numpy as np
import pytest

import kudio

SR = 16000


def _sources(root: Path, n_clean: int = 3, n_noise: int = 2) -> None:
    """Short, quiet tones and noises under *root*/clean and *root*/noise.

    Quiet enough that -5 dB does not clip: a clipping warning is kudio's
    business, and not what these tests are about.
    """
    (root / "clean").mkdir()
    (root / "noise").mkdir()
    t = np.arange(SR) / SR
    for i in range(n_clean):
        kudio.save_wave(root / "clean" / f"c{i}.wav",
                        (0.05 * np.sin(2 * np.pi * (180 + 40 * i) * t)
                         ).astype(np.float32), SR)
    for j in range(n_noise):
        kudio.save_wave(root / "noise" / f"n{j}.wav",
                        (0.01 * np.random.default_rng(j).standard_normal(2 * SR)
                         ).astype(np.float32), SR)


def _synthesize(manifest: Path, *args, mode: str = "inc", **kwargs) -> Path:
    syx = kudio.Synthesizer(*args, **kwargs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        syx.syn(mode=mode, seed=1)
    return kudio.save_manifest(manifest, syx.manifest())


@pytest.fixture
def relative_corpus(tmp_path, monkeypatch) -> Path:
    """Synthesised the usual way: from the project folder, with relative paths.

    3 clean × 2 noises × 3 SNRs in inc mode — 18 rows, 3 per combination. The
    manifest sits in the output folder, two levels below where the paths start.
    """
    monkeypatch.chdir(tmp_path)
    _sources(tmp_path)
    manifest = _synthesize(Path("data/mixed/manifest.json"), "clean", "noise",
                           out_path="data/mixed", snr_ratio=[-5, 0, 5])
    return manifest.absolute()


@pytest.fixture
def absolute_corpus(tmp_path) -> Path:
    """Absolute paths, two rooms, a telephone link and reverberant targets."""
    root = tmp_path / "abs"
    root.mkdir()
    _sources(root)
    return _synthesize(root / "mixed" / "manifest.json", str(root / "clean"),
                       str(root / "noise"), out_path=str(root / "mixed"),
                       snr_ratio=[0, 5], rt60=[0.3, 0.6], channel="telephone",
                       write_targets=True, mode="reg")


@pytest.fixture
def combined(relative_corpus, absolute_corpus, tmp_path) -> Path:
    """Both corpora in one manifest — relative and absolute rows, with and
    without a room and a link, which is what merging two runs gives."""
    pairs = (kudio.load_manifest(relative_corpus)
             + kudio.load_manifest(absolute_corpus))
    return kudio.save_manifest(tmp_path / "combined.json", pairs)


def _read(panel, path, qapp):
    """Load *path* the way the panel does, on its worker, and wait for it."""
    assert panel.load(path)
    panel.jobs.wait(20000)
    qapp.processEvents()
    qapp.processEvents()
    return panel.manifest


@pytest.fixture
def panel(qapp, session):
    from kudiostudio.panels.manifest import ManifestPanel
    p = ManifestPanel(session)
    yield p
    p.jobs.wait(20000)


@pytest.fixture
def window(qapp):
    from kudiostudio.app import KudioStudio
    w = KudioStudio(reset=True)
    yield w
    w.close()


def _column(panel, name):
    from kudiostudio.panels.manifest import COLUMNS
    column = COLUMNS.index(name)
    model = panel.model
    return [model.data(model.index(r, column)) for r in range(model.rowCount())]


# ------------------------------------------------------------ path finding

def test_relative_paths_are_found_from_the_working_directory(relative_corpus,
                                                             tmp_path):
    from kudiostudio.panels.manifest import read_manifest

    manifest = read_manifest(relative_corpus, cwd=tmp_path)
    assert len(manifest.rows) == 18
    assert manifest.missing == 0
    for row in manifest.rows:
        assert not Path(row.pair.noisy).is_absolute(), "the corpus is relative"
        assert row.noisy == tmp_path / row.pair.noisy
        assert row.clean == tmp_path / row.pair.clean
    assert manifest.root == tmp_path


def test_relative_paths_are_found_from_anywhere_above_the_manifest(
        relative_corpus, tmp_path, monkeypatch):
    """The Studio is rarely started from the folder the synthesis ran in.
    The manifest is two folders below where its paths start, so they are
    found by walking up from it."""
    from kudiostudio.panels.manifest import read_manifest

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    manifest = read_manifest(relative_corpus)
    assert manifest.missing == 0
    assert manifest.root == tmp_path
    assert all(row.noisy.is_file() and row.clean.is_file()
               for row in manifest.rows)


def test_the_working_directory_comes_before_the_manifests_folder(tmp_path):
    from kudiostudio.panels.manifest import read_manifest, search_folders

    beside, cwd = tmp_path / "beside", tmp_path / "cwd"
    beside.mkdir()
    cwd.mkdir()
    for folder in (beside, cwd):
        kudio.save_wave(folder / "a.wav", np.zeros(160, np.float32), SR)
    path = kudio.save_manifest(beside / "manifest.json",
                               [kudio.Pair(noisy="a.wav", clean="a.wav")])

    assert search_folders(path, cwd)[:2] == (cwd, beside)
    assert read_manifest(path, cwd).rows[0].noisy == cwd / "a.wav"

    (cwd / "a.wav").unlink()
    assert read_manifest(path, cwd).rows[0].noisy == beside / "a.wav"


def test_absolute_paths_are_taken_as_written(absolute_corpus, tmp_path):
    from kudiostudio.panels.manifest import read_manifest

    manifest = read_manifest(absolute_corpus, cwd=tmp_path)
    assert len(manifest.rows) == 6             # 3 clean files × 2 rooms
    assert manifest.missing == 0
    assert manifest.root is None, "nothing relative was looked for"
    for row in manifest.rows:
        assert row.noisy == Path(row.pair.noisy)
        assert row.target == Path(row.pair.target) and row.target.is_file()


def test_folder_lookups_are_asked_once_per_folder(tmp_path, monkeypatch):
    """A manifest from another machine finds nothing and tries every folder
    for every path. Asking the disk about each folder once is what keeps
    that from being a stat per file per candidate."""
    from kudiostudio.panels.manifest import read_manifest, search_folders

    pairs = [kudio.Pair(noisy=f"mixed/s{i % 4}/x{i}.wav",
                        clean=f"clean/s{i % 4}/x{i}.wav")
             for i in range(2000)]
    path = kudio.save_manifest(tmp_path / "m" / "manifest.json", pairs)

    asked = []
    real_isdir = os.path.isdir
    monkeypatch.setattr(os.path, "isdir",
                        lambda folder: asked.append(folder) or real_isdir(folder))
    manifest = read_manifest(path, cwd=tmp_path)
    monkeypatch.undo()

    assert manifest.missing == 2000
    folders = 8                                  # mixed/s0..s3, clean/s0..s3
    assert len(asked) == len(set(asked))
    assert len(asked) <= folders * len(search_folders(path, tmp_path))


# --------------------------------------------------------------- missing

def test_missing_files_are_marked_and_counted(panel, relative_corpus, qapp):
    from PySide6.QtCore import Qt
    from kudiostudio.panels.manifest import COLUMNS
    from kudiostudio.theme import WARN_COLOR

    pairs = kudio.load_manifest(relative_corpus)
    gone_noisy = next(p for p in pairs if p.clean.endswith("c0.wav"))
    Path(gone_noisy.noisy).unlink()
    Path("clean/c2.wav").unlink()               # every row built from it
    expected = 1 + sum(p.clean.endswith("c2.wav") for p in pairs)

    manifest = _read(panel, relative_corpus, qapp)
    assert manifest.missing == expected == 7
    assert f"{expected} with a file missing" in panel.summary.text()

    by_noisy = {row.pair.noisy: row for row in manifest.rows}
    assert by_noisy[gone_noisy.noisy].missing == ("noisy",)
    assert all(row.missing == ("clean",) for row in manifest.rows
               if row.pair.clean.endswith("c2.wav"))

    panel.missing_only.setChecked(True)
    assert panel.model.rowCount() == expected
    assert panel.shown.text() == f"showing {expected} of 18"

    model = panel.model
    noisy = COLUMNS.index("noisy")
    position = model.view_row(by_noisy[gone_noisy.noisy])
    cell = model.index(position, noisy)
    assert model.data(cell, Qt.ForegroundRole).name() == WARN_COLOR
    assert model.data(cell, Qt.ToolTipRole).startswith("not on disk")
    assert model.data(model.index(position, COLUMNS.index("clean")),
                      Qt.ForegroundRole) is None, "the clean file is there"

    panel.select_row(position)
    assert panel.open_button.isEnabled() is False
    assert panel.listen["clean"].isEnabled() is True
    assert panel.listen["noisy"].isEnabled() is False


def test_a_row_without_a_target_is_not_missing_one(panel, absolute_corpus,
                                                   relative_corpus, qapp):
    manifest = _read(panel, relative_corpus, qapp)
    assert all(row.pair.target is None and row.missing == ()
               for row in manifest.rows)
    panel.select_row(0)
    assert panel.listen["target"].isEnabled() is False
    assert "no target" in panel.listen["target"].toolTip()

    pairs = kudio.load_manifest(absolute_corpus)
    Path(pairs[0].target).unlink()
    manifest = _read(panel, absolute_corpus, qapp)
    assert manifest.missing == 1
    assert manifest.rows[0].missing == ("target",)


def test_reload_finds_files_that_came_back(panel, relative_corpus, qapp):
    pairs = kudio.load_manifest(relative_corpus)
    moved = Path(pairs[0].noisy)
    aside = moved.with_suffix(".bak")
    moved.rename(aside)
    assert _read(panel, relative_corpus, qapp).missing == 1

    aside.rename(moved)
    panel.reload_button.click()
    panel.jobs.wait(20000)
    qapp.processEvents()
    assert panel.manifest.missing == 0
    assert "every file on disk" in panel.summary.text()


# --------------------------------------------------------------- coverage

def test_coverage_counts_rows_per_noise_and_snr(relative_corpus, tmp_path):
    from kudiostudio.panels.manifest import (coverage, describe_coverage,
                                             read_manifest)

    manifest = read_manifest(relative_corpus, cwd=tmp_path)
    cov = coverage(manifest.rows)
    assert cov.noises == ("n0", "n1")
    assert cov.snrs == (-5, 0, 5)

    raw = json.loads(relative_corpus.read_text(encoding="utf-8"))
    for noise in cov.noises:
        for snr in cov.snrs:
            assert cov.count(noise, snr) == sum(
                1 for r in raw if r["noise"] == noise and r["snr_db"] == snr) == 3
    assert cov.gaps == []
    assert describe_coverage(cov, len(manifest.rows)) == (
        "Every one of the 6 combinations has 3 row(s).", True)


def test_a_reg_corpus_smaller_than_its_grid_shows_the_holes(panel, tmp_path,
                                                            monkeypatch, qapp):
    """3 clean files cannot cover 2 noises × 3 SNRs once each. The grid has
    to draw the three holes, and the note has to say why they are there."""
    from kudiostudio.theme import WARN_COLOR

    monkeypatch.chdir(tmp_path)
    _sources(tmp_path)
    path = _synthesize(Path("mixed/manifest.json"), "clean", "noise",
                       out_path="mixed", snr_ratio=[-5, 0, 5], mode="reg")
    _read(panel, path, qapp)

    table = panel.coverage_table
    assert (table.rowCount(), table.columnCount()) == (2, 3)
    counts = [[int(table.item(r, c).text()) for c in range(3)]
              for r in range(2)]
    assert sum(map(sum, counts)) == 3
    holes = [(r, c) for r in range(2) for c in range(3) if counts[r][c] == 0]
    assert len(holes) == 3
    for r, c in holes:
        assert table.item(r, c).background().color().name() == WARN_COLOR
    assert "3 of 6" in panel.coverage_note.text()
    assert "3 row(s) cannot cover 6" in panel.coverage_note.text()
    assert "at least 6 clean files" in panel.coverage_note.text()


def test_holes_in_a_corpus_big_enough_to_fill_them_are_lost_rows():
    from kudiostudio.panels.manifest import (ManifestRow, coverage,
                                             describe_coverage)

    def row(noise, snr):
        return ManifestRow(kudio.Pair(noisy=f"{noise}{snr}.wav", clean="c.wav",
                                      noise=noise, snr_db=snr),
                           None, None, None)

    rows = [row("n0", 0), row("n0", 0), row("n1", 0), row("n0", 5)]
    cov = coverage(rows)
    assert cov.gaps == [("n1", 5)]
    text, good = describe_coverage(cov, len(rows))
    assert not good
    assert "1 of 4" in text and "gone missing" in text

    uneven = coverage([row("n0", 0)] * 3 + [row("n1", 0)])
    text, good = describe_coverage(uneven, 4)
    assert not good and "unevenly" in text and "1 to 3" in text

    within_one = coverage([row("n0", 0)] * 2 + [row("n1", 0)])
    assert describe_coverage(within_one, 3) == (
        "Every one of the 2 combinations has 1 or 2 rows.", True)


# ---------------------------------------------------------------- filters

def test_filters_narrow_the_rows_and_the_counts(panel, combined, qapp):
    manifest = _read(panel, combined, qapp)
    pairs = [row.pair for row in manifest.rows]
    assert len(pairs) == 24 and panel.shown.text() == "showing 24 of 24"

    assert panel.set_filter("noise", "n0")
    assert panel.model.rowCount() == sum(p.noise == "n0" for p in pairs)
    assert set(_column(panel, "noise")) == {"n0"}
    assert panel.coverage_table.rowCount() == 1, "only the chosen noise"

    panel.filters["noise"].setCurrentIndex(0)
    assert panel.set_filter("rt60", 0.3)
    assert panel.model.rowCount() == sum(p.rt60 == 0.3 for p in pairs) == 3
    assert set(_column(panel, "RT60 s")) == {"0.3"}
    # a room filter changes the counts, not the axes: every SNR the corpus
    # has keeps its column, so what this room lacks shows as a zero
    assert panel.coverage_table.columnCount() == 3

    assert panel.set_filter("rt60", None)         # "none": no room at all
    assert panel.model.rowCount() == 18
    assert panel.set_filter("channel", "telephone")
    assert panel.model.rowCount() == 0, "no row is both roomless and on a phone"

    panel.filters["rt60"].setCurrentIndex(0)
    assert panel.model.rowCount() == 6
    assert set(_column(panel, "link")) == {"telephone"}

    assert panel.set_filter("snr_db", 5)
    assert set(_column(panel, "SNR dB")) == {"5"}
    assert panel.set_filter("snr_db", 17) is False, "no row has it"


def test_the_filter_offers_what_the_corpus_holds(panel, combined, qapp):
    _read(panel, combined, qapp)
    texts = {field: [box.itemText(i) for i in range(box.count())]
             for field, box in panel.filters.items()}
    assert texts["noise"] == ["all", "n0", "n1"]
    assert texts["snr_db"] == ["all", "-5 dB", "0 dB", "5 dB"]
    assert texts["rt60"] == ["all", "0.3 s", "0.6 s", "none"]
    assert texts["channel"] == ["all", "telephone", "none"]


def test_double_clicking_a_coverage_cell_shows_those_rows(panel, relative_corpus,
                                                          qapp):
    _read(panel, relative_corpus, qapp)
    panel.coverage_table.cellDoubleClicked.emit(1, 2)        # n1 × 5 dB
    assert set(_column(panel, "noise")) == {"n1"}
    assert set(_column(panel, "SNR dB")) == {"5"}
    assert panel.model.rowCount() == 3


# ---------------------------------------------------------------- sorting

def test_numeric_columns_sort_by_value_not_by_text(panel, tmp_path, qapp):
    """As text, "10" sorts before "5" and "-5" lands wherever the minus sign
    puts it. A row with no SNR goes last in either direction, and clearing
    the sort puts the manifest's own order back."""
    from PySide6.QtCore import Qt
    from kudiostudio.panels.manifest import COLUMNS

    snrs = [10, -5, 5, None, 2.5, -10]
    pairs = [kudio.Pair(noisy=f"m{i}.wav", clean="c.wav", noise="n", snr_db=s)
             for i, s in enumerate(snrs)]
    _read(panel, kudio.save_manifest(tmp_path / "m.json", pairs), qapp)
    column = COLUMNS.index("SNR dB")

    panel.table.sortByColumn(column, Qt.AscendingOrder)
    assert _column(panel, "SNR dB") == ["-10", "-5", "2.5", "5", "10", "—"]
    panel.table.sortByColumn(column, Qt.DescendingOrder)
    assert _column(panel, "SNR dB") == ["10", "5", "2.5", "-5", "-10", "—"]

    panel.table.sortByColumn(-1, Qt.AscendingOrder)
    assert _column(panel, "noisy") == [f"m{i}.wav" for i in range(len(snrs))]


def test_sorting_and_filtering_keep_the_selected_row(panel, relative_corpus,
                                                     qapp):
    from PySide6.QtCore import Qt
    from kudiostudio.panels.manifest import COLUMNS

    _read(panel, relative_corpus, qapp)
    chosen = panel.select_row(4)
    panel.table.sortByColumn(COLUMNS.index("SNR dB"), Qt.DescendingOrder)
    assert panel.selected_row() is chosen
    assert panel.set_filter("noise", chosen.pair.noise)
    assert panel.selected_row() is chosen

    other = "n1" if chosen.pair.noise == "n0" else "n0"
    panel.set_filter("noise", other)
    assert panel.selected_row() is None, "filtered out, so not selected"
    assert panel.open_button.isEnabled() is False


def test_paths_are_shown_under_the_folder_they_share(panel, relative_corpus,
                                                     qapp):
    from PySide6.QtCore import Qt

    _read(panel, relative_corpus, qapp)
    # spelled the way this system spells a path: backslashes on Windows
    shared = os.path.join("data", "mixed")
    assert panel.manifest.folders["noisy"] == shared
    assert panel.manifest.folders["clean"] == "clean"
    assert sorted(_column(panel, "noisy")) == sorted(
        os.path.basename(p.noisy) for p in kudio.load_manifest(relative_corpus))
    assert panel.model.headerData(0, Qt.Horizontal, Qt.ToolTipRole) == \
        f"paths shown relative to {shared}"


def test_paths_written_on_another_system_are_still_shortened(panel, tmp_path,
                                                             qapp):
    """A corpus synthesised on Linux and read on Windows names its files with
    forward slashes; the shared folder is still taken off them."""
    pairs = [kudio.Pair(noisy=f"data/mixed/m{i}.wav", clean=f"clean/c{i}.wav")
             for i in range(3)]
    _read(panel, kudio.save_manifest(tmp_path / "m.json", pairs), qapp)
    assert panel.manifest.folders["noisy"] == os.path.join("data", "mixed")
    assert _column(panel, "noisy") == ["m0.wav", "m1.wav", "m2.wav"]


# --------------------------------------------------------- loading itself

def test_the_manifest_is_read_off_the_gui_thread(panel, relative_corpus, qapp,
                                                 monkeypatch):
    from kudiostudio.panels import manifest as module

    threads = []
    real = module.read_manifest

    def recording(*args, **kwargs):
        threads.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "read_manifest", recording)
    assert panel.load(relative_corpus)
    assert panel.manifest is None, "the table fills in when the worker is done"
    panel.jobs.wait(20000)
    qapp.processEvents()

    assert threads and threads[0] is not threading.main_thread()
    assert len(panel.manifest.rows) == 18


def test_a_second_load_while_reading_is_refused(panel, relative_corpus, qapp,
                                                monkeypatch):
    from kudiostudio.panels import manifest as module

    release = threading.Event()
    real = module.read_manifest
    monkeypatch.setattr(module, "read_manifest",
                        lambda *a, **k: release.wait(10) and real(*a, **k))
    messages = []
    panel.status.connect(messages.append)

    assert panel.load(relative_corpus)
    assert panel.reload_button.text() == "Reading…"
    assert panel.load(relative_corpus) is False
    assert messages[-1] == "A manifest is still being read."

    release.set()
    panel.jobs.wait(20000)
    qapp.processEvents()
    assert len(panel.manifest.rows) == 18
    assert panel.reload_button.text() == "Reload"


def test_a_file_that_is_not_a_manifest_is_reported(panel, tmp_path, qapp):
    broken = tmp_path / "manifest.json"
    broken.write_text('{"noisy": "a.wav"}', encoding="utf-8")
    messages = []
    panel.status.connect(messages.append)

    panel.load(broken)
    panel.jobs.wait(20000)
    qapp.processEvents()
    assert panel.manifest is None
    assert messages[-1].startswith("Could not read manifest.json")
    assert "not a JSON list" in messages[-1]


# ------------------------------------------------------ the main window

def _open(window, path, qapp):
    window.open_manifest(path)
    window.manifest_panel.jobs.wait(20000)
    qapp.processEvents()
    qapp.processEvents()
    return window.manifest_panel


def test_open_loads_the_selected_rows_noisy_file(window, relative_corpus,
                                                 tmp_path, monkeypatch, qapp):
    """Through a sort, so the row on screen and the row in the manifest are
    different numbers -- the mix-up that would open the wrong file."""
    from PySide6.QtCore import Qt
    from kudiostudio.panels.manifest import COLUMNS

    monkeypatch.chdir(tmp_path / "data")       # neither the root nor the folder
    panel = _open(window, relative_corpus, qapp)
    panel.table.sortByColumn(COLUMNS.index("SNR dB"), Qt.DescendingOrder)
    row = panel.select_row(0)
    assert row.pair.snr_db == 5
    assert row is not panel.manifest.rows[0]

    panel.open_button.click()
    assert window.session.path == row.noisy
    assert str(row.noisy.resolve()) in window.files.paths()
    y, sr = kudio.file_load(row.noisy, sr=None, mono=True)
    assert window.session.sr == sr
    assert np.array_equal(window.session.y, y)

    other = panel.select_row(3)
    panel.table.doubleClicked.emit(panel.model.index(3, 0))
    assert window.session.path == other.noisy


def test_open_refuses_a_row_whose_noisy_file_is_gone(window, relative_corpus,
                                                     qapp):
    pairs = kudio.load_manifest(relative_corpus)
    Path(pairs[0].noisy).unlink()
    panel = _open(window, relative_corpus, qapp)
    panel.select_row(0)

    panel._open()
    assert window.session.has_audio is False
    assert "is not on disk" in window.statusBar().currentMessage()


def test_auditioning_plays_each_side_without_loading_it(window, absolute_corpus,
                                                        monkeypatch, qapp):
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(
                            (np.array(y), sr)))
    panel = _open(window, absolute_corpus, qapp)
    row = panel.select_row(2)

    for side in ("clean", "target", "noisy"):
        panel.listen[side].click()
        y, sr = kudio.file_load(getattr(row, side), sr=None, mono=True)
        assert played[-1][1] == sr
        assert np.allclose(played[-1][0], y), side
        assert [s for s, b in panel.listen.items() if b.isChecked()] == [side]
    assert len(played) == 3
    assert window.session.has_audio is False, "auditioning loads nothing"

    panel.listen["noisy"].click()                # the down button stops it
    assert window._preview is None
    assert not any(b.isChecked() for b in panel.listen.values())


def test_the_audition_follows_the_selection(window, absolute_corpus,
                                            monkeypatch, qapp):
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(
                            np.array(y)))
    panel = _open(window, absolute_corpus, qapp)
    panel.select_row(0)
    panel.listen["clean"].click()

    row = panel.select_row(5)
    y, _ = kudio.file_load(row.clean, sr=None, mono=True)
    assert len(played) == 2
    assert np.allclose(played[-1], y)
    assert panel.listen["clean"].isChecked()


def test_a_filter_that_keeps_the_row_does_not_restart_it(window, absolute_corpus,
                                                         monkeypatch, qapp):
    """Changing what else is listed is no reason to hear the file from the top
    again; losing the row from the list is a reason to stop."""
    played = []
    monkeypatch.setattr(window.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(len(y)))
    panel = _open(window, absolute_corpus, qapp)
    row = panel.select_row(0)
    panel.listen["noisy"].click()
    assert len(played) == 1

    assert panel.set_filter("rt60", row.pair.rt60)
    assert panel.selected_row() is row
    assert len(played) == 1, "same row, same side: nothing to restart"

    panel.set_filter("rt60", 0.6 if row.pair.rt60 == 0.3 else 0.3)
    assert panel.selected_row() is None
    assert not panel.listen["noisy"].isChecked()
    assert window._preview is None

    panel.filters["rt60"].setCurrentIndex(0)
    panel.select_row(0)
    panel.listen["noisy"].click()
    assert len(played) == 2, "a fresh press plays it again"


def test_something_else_claiming_playback_releases_the_button(
        window, absolute_corpus, monkeypatch, qapp):
    monkeypatch.setattr(window.player, "play", lambda *a, **k: None)
    panel = _open(window, absolute_corpus, qapp)
    panel.select_row(0)
    panel.listen["noisy"].click()
    assert window._preview is not None

    window._stop()
    assert not panel.listen["noisy"].isChecked()

    panel.listen["noisy"].click()
    panel.open_button.click()                     # opening ends the audition
    assert window.session.has_audio
    assert not panel.listen["noisy"].isChecked()


def test_the_file_menu_opens_a_manifest_and_brings_the_panel_forward(
        window, relative_corpus, monkeypatch, qapp):
    from PySide6.QtWidgets import QFileDialog

    # the action held while its menu is used: PySide6 6.10 deletes a menu
    # reached through a temporary action
    file_action = window.menuBar().actions()[0]
    file_menu = file_action.menu()
    assert window.open_manifest_action in file_menu.actions()
    assert window.open_manifest_action.isEnabled(), "needs no audio loaded"

    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(
        lambda *a, **k: asked.append(a[3]) or (str(relative_corpus), "")))
    window.tools.setCurrentIndex(0)
    window.open_manifest_action.trigger()
    window.manifest_panel.jobs.wait(20000)
    qapp.processEvents()

    assert "*.json" in asked[0]
    assert window.tools.currentWidget() is window.manifest_panel
    assert len(window.manifest_panel.manifest.rows) == 18
    assert window.settings.value("last_dir") == str(relative_corpus.parent)


def test_the_panel_sits_after_synthesize(window):
    titles = [window.tools.tabText(i) for i in range(window.tools.count())]
    assert titles.index("Manifest") == titles.index("Synthesize") + 1


def test_a_manifest_named_on_the_command_line_opens_in_the_panel(
        window, relative_corpus, tone, qapp):
    """``kudiostudio manifest.json`` used to hand it to file_load, which said
    it was not audio."""
    from kudiostudio.panels.manifest import is_manifest

    assert is_manifest(relative_corpus) and is_manifest("A.JSON")
    assert not is_manifest(tone)

    window.open_files([str(relative_corpus), str(tone)])
    window.manifest_panel.jobs.wait(20000)
    qapp.processEvents()

    assert len(window.manifest_panel.manifest.rows) == 18
    assert window.tools.currentWidget() is window.manifest_panel
    assert window.files.paths() == [str(tone.resolve())], "audio only"
    assert window.session.path == tone
    assert "Could not open" not in window.statusBar().currentMessage()


# --------------------------------------------- one corpus, not two mixed

def _project(root: Path) -> Path:
    """A corpus synthesised from *root* the usual way: relative paths, with
    the manifest in data/mixed."""
    root.mkdir()
    _sources(root)
    here = Path.cwd()
    os.chdir(root)
    try:
        return _synthesize(Path("data/mixed/manifest.json"), "clean", "noise",
                           out_path="data/mixed", snr_ratio=[0, 5]).absolute()
    finally:
        os.chdir(here)


def test_two_projects_with_the_same_names_are_never_mixed_row_by_row(
        tmp_path, panel, qapp, monkeypatch):
    """Two projects built from the same clean set name their mixtures alike.
    Started from the one that has some of them, the other's manifest used to
    pair this project's mixtures with that one's clean files -- and say every
    file was on disk."""
    from kudiostudio.panels.manifest import read_manifest

    theirs = _project(tmp_path / "theirs")
    pairs = kudio.load_manifest(theirs)
    ours = tmp_path / "ours"
    for pair in pairs[:3]:
        copy = ours / pair.noisy
        copy.parent.mkdir(parents=True, exist_ok=True)
        kudio.save_wave(copy, np.zeros(SR, np.float32), SR)

    manifest = read_manifest(theirs, cwd=ours)
    assert manifest.missing == 0
    assert manifest.root == tmp_path / "theirs"
    assert all(tmp_path / "theirs" in getattr(row, side).parents
               for row in manifest.rows for side in ("noisy", "clean"))

    monkeypatch.chdir(ours)
    _read(panel, theirs, qapp)
    assert panel.summary.text().endswith(
        f"every file on disk · relative paths found under {tmp_path / 'theirs'}")


def test_without_a_folder_that_holds_everything_the_fullest_one_is_used(
        tmp_path, panel, qapp):
    """A path missing from the chosen folder is missing, even where another
    folder has a file of that name: it is another corpus's file."""
    from PySide6.QtCore import Qt
    from kudiostudio.panels.manifest import COLUMNS, read_manifest

    cwd, beside = tmp_path / "cwd", tmp_path / "beside"
    for folder, names in ((cwd, "ad"), (beside, "abc")):
        folder.mkdir()
        for name in names + "x":
            kudio.save_wave(folder / f"{name}.wav", np.zeros(160, np.float32), SR)
    path = kudio.save_manifest(
        beside / "manifest.json",
        [kudio.Pair(noisy=f"{name}.wav", clean="x.wav") for name in "abcd"])

    manifest = read_manifest(path, cwd)
    assert manifest.root == beside
    assert [row.noisy for row in manifest.rows] == [
        beside / "a.wav", beside / "b.wav", beside / "c.wav", None]
    assert manifest.missing == 1

    panel.load(path)
    panel.jobs.wait(20000)
    qapp.processEvents()
    model = panel.model
    cell = model.index(model.view_row(panel.manifest.rows[3]),
                       COLUMNS.index("noisy"))
    assert model.data(cell, Qt.ToolTipRole) == \
        f"not on disk: d.wav, looked for under {beside}"


# ---------------------------------------------------- a broken manifest

@pytest.mark.parametrize("row, message", [
    ({"noisy": "a.wav", "clean": "b.wav", "noise": "n", "snr_db": [5]},
     "row 1: snr_db is list [5], expected a number or null"),
    ({"noisy": "a.wav", "clean": "b.wav", "channel": {"codec": "g711"}},
     "row 1: channel is dict {'codec': 'g711'}, expected text or null"),
    ({"noisy": ["a.wav"], "clean": "b.wav"},
     "row 1: noisy is list ['a.wav'], expected a path"),
    ({"noisy": "a.wav", "clean": "b.wav", "rt60": True},
     "row 1: rt60 is bool True, expected a number or null"),
])
def test_a_value_no_manifest_holds_is_refused_and_the_last_one_kept(
        panel, relative_corpus, tmp_path, qapp, row, message):
    """It used to get as far as the filters, raise there, and leave the
    panel naming the new manifest over the old one's rows."""
    good = _read(panel, relative_corpus, qapp)
    shown = panel.path_edit.text()
    summary = panel.summary.text()
    messages = []
    panel.status.connect(messages.append)

    bad = tmp_path / "m1.json"
    bad.write_text(json.dumps([row]), encoding="utf-8")
    panel.load(bad)
    panel.jobs.wait(20000)
    qapp.processEvents()

    assert messages[-1] == f"Could not read m1.json — ValueError: {message}"
    assert panel.manifest is good
    assert panel.path_edit.text() == shown
    assert panel.summary.text() == summary
    assert panel.model.total == 18


def test_a_manifest_that_cannot_be_shown_leaves_the_panel_as_it_was(
        panel, relative_corpus, qapp, monkeypatch):
    """The table's own work is done before anything is replaced, so a failure
    there too is a message and the last manifest, not half of each."""
    from kudiostudio.panels import manifest as module

    good = _read(panel, relative_corpus, qapp)
    messages = []
    panel.status.connect(messages.append)
    monkeypatch.setattr(module, "distinct",
                        lambda rows, field: 1 / 0)
    panel.load(relative_corpus)
    panel.jobs.wait(20000)
    qapp.processEvents()

    assert messages[-1].startswith("Could not read manifest.json — "
                                   "ZeroDivisionError")
    assert panel.manifest is good
    assert panel.model.total == 18


# ------------------------------------- coverage of the rows with damage

def test_rows_with_a_file_missing_are_counted_without_judging_the_corpus(
        panel, relative_corpus, qapp):
    """A full corpus with one mixture gone, filtered to the damage, has one
    row in one cell. That is no reason to tell it it needs more clean files."""
    from kudiostudio.theme import WARN_COLOR

    pairs = kudio.load_manifest(relative_corpus)
    Path(pairs[0].noisy).unlink()
    _read(panel, relative_corpus, qapp)
    full = panel.coverage_note.text()
    assert full.startswith("Every one of the 6 combinations")

    panel.missing_only.setChecked(True)
    note = panel.coverage_note.text()
    for wrong in ("cannot cover", "clean files", "gone missing", "unevenly"):
        assert wrong not in note
    assert note.startswith("Rows with a file missing, per noise × SNR: 1 in all")
    assert WARN_COLOR not in panel.coverage_note.styleSheet()
    table = panel.coverage_table
    zeros = [table.item(r, c) for r in range(table.rowCount())
             for c in range(table.columnCount())
             if table.item(r, c).text() == "0"]
    assert len(zeros) == 5
    assert all(item.background().color().name() != WARN_COLOR for item in zeros)

    panel.missing_only.setChecked(False)
    assert panel.coverage_note.text() == full


# -------------------------------------------------------------- lifetime

def test_a_manifest_panel_let_go_is_freed(qapp, session):
    """The listen buttons used to hold the panel through a lambda over
    ``self``: never collected, and destroyed at interpreter exit, where it
    crashed the process after every test had passed."""
    import gc
    import weakref

    from kudiostudio.panels.manifest import ManifestPanel

    panel = ManifestPanel(session)
    panel.listen["clean"].setChecked(True)
    panel.listen["clean"].setChecked(False)
    gone = weakref.ref(panel)
    del panel
    gc.collect()
    assert gone() is None
