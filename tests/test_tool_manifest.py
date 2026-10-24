# -*- coding: utf-8 -*-
"""What `kudio.Synthesizer` writes, read back and found on disk, and the
Manifest panel that shows it.

Every corpus here is synthesised for real: a manifest written by hand would
only test that the reading agrees with the person who wrote the test.
"""
from __future__ import annotations

import json
import os
import re
import threading
import warnings
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio.tools import manifest as tools
from kudiostudio.web.studio import Prefs, Studio

SR = 16000
#: the page's own script, where the menus are
MAIN_JS = (Path(__file__).resolve().parents[1]
           / "kudiostudio" / "web" / "static" / "js" / "main.js")

NOISY = tools.COLUMNS.index("noisy")
CLEAN = tools.COLUMNS.index("clean")
NOISE = tools.COLUMNS.index("noise")
SNR = tools.COLUMNS.index("SNR dB")
RT60 = tools.COLUMNS.index("RT60 s")
TARGET = tools.COLUMNS.index("target")


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


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


def _events(studio):
    q = studio.events.subscribe()

    def drain():
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out

    return drain


def _said(events):
    return [e["text"] for e in events if e["type"] == "message"]


def _open(studio, path):
    """Open *path* the way File → Open does, and wait for the panel to read it."""
    studio.call("open_files", [[str(path)]])
    assert studio.jobs.wait(30)
    return studio.call("manifest.describe")["view"]


def _load(studio, path):
    studio.call("manifest.load", [str(path)])
    assert studio.jobs.wait(30)
    return studio.call("manifest.describe")["view"]


def _column(view, name):
    column = tools.COLUMNS.index(name)
    return [row["cells"][column] for row in view["rows"]]


def _missing(view) -> int:
    return sum(1 for row in view["rows"] if row["missing"])


def _choices(view):
    return {f["field"]: f["choices"] for f in view["filters"]}


def _pick(studio, view, field, text):
    """Point the *field* filter at the choice it shows as *text*."""
    index = _choices(view)[field].index(text)
    return studio.call("manifest.set_filters", [{field: index}])


def _selected(view):
    """The cells of the selected row, as the table shows them now."""
    at = view["selection"]["position"]
    return view["rows"][at]["cells"] if at >= 0 else None


# ------------------------------------------------------------ path finding

def test_relative_paths_are_found_from_the_working_directory(relative_corpus,
                                                             tmp_path):
    manifest = tools.read_manifest(relative_corpus, cwd=tmp_path)
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
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    manifest = tools.read_manifest(relative_corpus)
    assert manifest.missing == 0
    assert manifest.root == tmp_path
    assert all(row.noisy.is_file() and row.clean.is_file()
               for row in manifest.rows)


def test_the_working_directory_comes_before_the_manifests_folder(tmp_path):
    beside, cwd = tmp_path / "beside", tmp_path / "cwd"
    beside.mkdir()
    cwd.mkdir()
    for folder in (beside, cwd):
        kudio.save_wave(folder / "a.wav", np.zeros(160, np.float32), SR)
    path = kudio.save_manifest(beside / "manifest.json",
                               [kudio.Pair(noisy="a.wav", clean="a.wav")])

    assert tools.search_folders(path, cwd)[:2] == (cwd, beside)
    assert tools.read_manifest(path, cwd).rows[0].noisy == cwd / "a.wav"

    (cwd / "a.wav").unlink()
    assert tools.read_manifest(path, cwd).rows[0].noisy == beside / "a.wav"


def test_absolute_paths_are_taken_as_written(absolute_corpus, tmp_path):
    manifest = tools.read_manifest(absolute_corpus, cwd=tmp_path)
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
    pairs = [kudio.Pair(noisy=f"mixed/s{i % 4}/x{i}.wav",
                        clean=f"clean/s{i % 4}/x{i}.wav")
             for i in range(2000)]
    path = kudio.save_manifest(tmp_path / "m" / "manifest.json", pairs)

    asked = []
    real_isdir = os.path.isdir
    monkeypatch.setattr(os.path, "isdir",
                        lambda folder: asked.append(folder) or real_isdir(folder))
    manifest = tools.read_manifest(path, cwd=tmp_path)
    monkeypatch.undo()

    assert manifest.missing == 2000
    folders = 8                                  # mixed/s0..s3, clean/s0..s3
    assert len(asked) == len(set(asked))
    assert len(asked) <= folders * len(tools.search_folders(path, tmp_path))


# --------------------------------------------------------------- missing

def test_missing_files_are_marked_and_counted(studio, relative_corpus):
    pairs = kudio.load_manifest(relative_corpus)
    gone_noisy = next(p for p in pairs if p.clean.endswith("c0.wav"))
    Path(gone_noisy.noisy).unlink()
    Path("clean/c2.wav").unlink()               # every row built from it
    expected = 1 + sum(p.clean.endswith("c2.wav") for p in pairs)

    view = _open(studio, relative_corpus)
    assert _missing(view) == expected == 7
    assert view["summary"][1] == "warn"
    assert f"{expected} with a file missing" in view["summary"][0]

    gone = os.path.basename(gone_noisy.noisy)
    by_noisy = {row["cells"][NOISY]: row for row in view["rows"]}
    assert by_noisy[gone]["missing"] == [NOISY]
    from_c2 = [row for row in view["rows"] if row["cells"][CLEAN] == "c2.wav"]
    assert len(from_c2) == expected - 1
    assert all(row["missing"] == [CLEAN] for row in from_c2)

    view = studio.call("manifest.set_filters", [{}, True])
    assert view["shown"] == expected
    assert view["showing"] == f"showing {expected} of 18"

    position = next(row["p"] for row in view["rows"] if row["cells"][NOISY] == gone)
    row = view["rows"][position]
    assert row["missing"] == [NOISY]
    assert row["tips"][NOISY].startswith("not on disk")
    assert CLEAN not in row["missing"], "the clean file is there"

    selection = studio.call("manifest.select", [position])
    assert selection["can_open"] is False
    assert selection["sides"]["clean"]["enabled"] is True
    assert selection["sides"]["noisy"]["enabled"] is False


def test_a_row_without_a_target_is_not_missing_one(studio, absolute_corpus,
                                                   relative_corpus):
    assert all(p.target is None for p in kudio.load_manifest(relative_corpus))
    view = _open(studio, relative_corpus)
    assert all(row["missing"] == [] and TARGET not in row["tips"]
               for row in view["rows"])
    target = studio.call("manifest.select", [0])["sides"]["target"]
    assert target["enabled"] is False
    assert "no target" in target["tip"]

    pairs = kudio.load_manifest(absolute_corpus)
    Path(pairs[0].target).unlink()
    view = _open(studio, absolute_corpus)
    assert _missing(view) == 1
    assert view["rows"][0]["missing"] == [TARGET]


def test_reload_finds_files_that_came_back(studio, relative_corpus):
    pairs = kudio.load_manifest(relative_corpus)
    moved = Path(pairs[0].noisy)
    aside = moved.with_suffix(".bak")
    moved.rename(aside)
    assert _missing(_open(studio, relative_corpus)) == 1

    aside.rename(moved)
    studio.call("manifest.reload")
    assert studio.jobs.wait(30)
    view = studio.call("manifest.describe")["view"]
    assert _missing(view) == 0
    assert "every file on disk" in view["summary"][0]


# --------------------------------------------------------------- coverage

def test_coverage_counts_rows_per_noise_and_snr(relative_corpus, tmp_path):
    manifest = tools.read_manifest(relative_corpus, cwd=tmp_path)
    cov = tools.coverage(manifest.rows)
    assert cov.noises == ("n0", "n1")
    assert cov.snrs == (-5, 0, 5)

    raw = json.loads(relative_corpus.read_text(encoding="utf-8"))
    for noise in cov.noises:
        for snr in cov.snrs:
            assert cov.count(noise, snr) == sum(
                1 for r in raw if r["noise"] == noise and r["snr_db"] == snr) == 3
    assert cov.gaps == []
    assert tools.describe_coverage(cov, len(manifest.rows)) == (
        "Every one of the 6 combinations has 3 row(s).", True)


def test_a_reg_corpus_smaller_than_its_grid_shows_the_holes(studio, tmp_path,
                                                            monkeypatch):
    """3 clean files cannot cover 2 noises × 3 SNRs once each. The grid has
    to draw the three holes, and the note has to say why they are there."""
    monkeypatch.chdir(tmp_path)
    _sources(tmp_path)
    path = _synthesize(Path("mixed/manifest.json"), "clean", "noise",
                       out_path="mixed", snr_ratio=[-5, 0, 5], mode="reg")
    cov = _open(studio, path)["coverage"]

    counts = cov["counts"]
    assert (len(cov["noises"]), len(cov["snrs"])) == (2, 3)
    assert [len(line) for line in counts] == [3, 3]
    assert sum(map(sum, counts)) == 3
    holes = [(r, c) for r in range(2) for c in range(3) if counts[r][c] == 0]
    assert len(holes) == 3
    assert cov["holes"], "a zero in this grid is drawn as a hole"
    note, tone = cov["note"]
    assert tone == "warn"
    assert "3 of 6" in note
    assert "3 row(s) cannot cover 6" in note
    assert "at least 6 clean files" in note


def test_holes_in_a_corpus_big_enough_to_fill_them_are_lost_rows():
    def row(noise, snr):
        return tools.ManifestRow(kudio.Pair(noisy=f"{noise}{snr}.wav", clean="c.wav",
                                            noise=noise, snr_db=snr),
                                 None, None, None)

    rows = [row("n0", 0), row("n0", 0), row("n1", 0), row("n0", 5)]
    cov = tools.coverage(rows)
    assert cov.gaps == [("n1", 5)]
    text, good = tools.describe_coverage(cov, len(rows))
    assert not good
    assert "1 of 4" in text and "gone missing" in text

    uneven = tools.coverage([row("n0", 0)] * 3 + [row("n1", 0)])
    text, good = tools.describe_coverage(uneven, 4)
    assert not good and "unevenly" in text and "1 to 3" in text

    within_one = tools.coverage([row("n0", 0)] * 2 + [row("n1", 0)])
    assert tools.describe_coverage(within_one, 3) == (
        "Every one of the 2 combinations has 1 or 2 rows.", True)


# ---------------------------------------------------------------- filters

def test_filters_narrow_the_rows_and_the_counts(studio, combined):
    view = _open(studio, combined)
    pairs = kudio.load_manifest(combined)
    assert len(pairs) == 24 and view["showing"] == "showing 24 of 24"

    view = _pick(studio, view, "noise", "n0")
    assert view["shown"] == sum(p.noise == "n0" for p in pairs)
    assert set(_column(view, "noise")) == {"n0"}
    assert len(view["coverage"]["noises"]) == 1, "only the chosen noise"

    view = _pick(studio, view, "noise", "all")
    view = _pick(studio, view, "rt60", "0.3 s")
    assert view["shown"] == sum(p.rt60 == 0.3 for p in pairs) == 3
    assert set(_column(view, "RT60 s")) == {"0.3"}
    # a room filter changes the counts, not the axes: every SNR the corpus
    # has keeps its column, so what this room lacks shows as a zero
    assert len(view["coverage"]["snrs"]) == 3

    view = _pick(studio, view, "rt60", "none")       # no room at all
    assert view["shown"] == 18
    view = _pick(studio, view, "channel", "telephone")
    assert view["shown"] == 0, "no row is both roomless and on a phone"

    view = _pick(studio, view, "rt60", "all")
    assert view["shown"] == 6
    assert set(_column(view, "link")) == {"telephone"}

    view = _pick(studio, view, "snr_db", "5 dB")
    assert set(_column(view, "SNR dB")) == {"5"}
    snrs = _choices(view)["snr_db"]
    assert "17 dB" not in snrs, "no row has it"
    # nor can it be asked for past the end of the list
    view = studio.call("manifest.set_filters", [{"snr_db": len(snrs)}])
    assert set(_column(view, "SNR dB")) == {"5"}


def test_the_filter_offers_what_the_corpus_holds(studio, combined):
    texts = _choices(_open(studio, combined))
    assert texts["noise"] == ["all", "n0", "n1"]
    assert texts["snr_db"] == ["all", "-5 dB", "0 dB", "5 dB"]
    assert texts["rt60"] == ["all", "0.3 s", "0.6 s", "none"]
    assert texts["channel"] == ["all", "telephone", "none"]


def test_double_clicking_a_coverage_cell_shows_those_rows(studio, relative_corpus):
    _open(studio, relative_corpus)
    view = studio.call("manifest.focus", [1, 2])        # n1 × 5 dB
    assert set(_column(view, "noise")) == {"n1"}
    assert set(_column(view, "SNR dB")) == {"5"}
    assert view["shown"] == 3


# ---------------------------------------------------------------- sorting

def test_numeric_columns_sort_by_value_not_by_text(studio, tmp_path):
    """As text, "10" sorts before "5" and "-5" lands wherever the minus sign
    puts it. A row with no SNR goes last in either direction, and clearing
    the sort puts the manifest's own order back."""
    snrs = [10, -5, 5, None, 2.5, -10]
    pairs = [kudio.Pair(noisy=f"m{i}.wav", clean="c.wav", noise="n", snr_db=s)
             for i, s in enumerate(snrs)]
    _open(studio, kudio.save_manifest(tmp_path / "m.json", pairs))

    view = studio.call("manifest.sort", [SNR, False])
    assert _column(view, "SNR dB") == ["-10", "-5", "2.5", "5", "10", "—"]
    view = studio.call("manifest.sort", [SNR, True])
    assert _column(view, "SNR dB") == ["10", "5", "2.5", "-5", "-10", "—"]

    view = studio.call("manifest.sort", [-1])
    assert _column(view, "noisy") == [f"m{i}.wav" for i in range(len(snrs))]


def test_sorting_and_filtering_keep_the_selected_row(studio, relative_corpus):
    view = _open(studio, relative_corpus)
    chosen = view["rows"][4]["cells"]
    studio.call("manifest.select", [4])
    view = studio.call("manifest.sort", [SNR, True])
    assert _selected(view) == chosen
    view = _pick(studio, view, "noise", chosen[NOISE])
    assert _selected(view) == chosen

    other = "n1" if chosen[NOISE] == "n0" else "n0"
    view = _pick(studio, view, "noise", other)
    assert _selected(view) is None, "filtered out, so not selected"
    assert view["selection"]["can_open"] is False


def test_paths_are_shown_under_the_folder_they_share(studio, relative_corpus):
    view = _open(studio, relative_corpus)
    # spelled the way this system spells a path: backslashes on Windows
    shared = os.path.join("data", "mixed")
    assert view["header_tips"][NOISY] == f"paths shown relative to {shared}"
    assert view["header_tips"][CLEAN] == "paths shown relative to clean"
    assert sorted(_column(view, "noisy")) == sorted(
        os.path.basename(p.noisy) for p in kudio.load_manifest(relative_corpus))


def test_paths_written_on_another_system_are_still_shortened(studio, tmp_path):
    """A corpus synthesised on Linux and read on Windows names its files with
    forward slashes; the shared folder is still taken off them."""
    pairs = [kudio.Pair(noisy=f"data/mixed/m{i}.wav", clean=f"clean/c{i}.wav")
             for i in range(3)]
    view = _open(studio, kudio.save_manifest(tmp_path / "m.json", pairs))
    assert view["header_tips"][NOISY] == \
        f"paths shown relative to {os.path.join('data', 'mixed')}"
    assert _column(view, "noisy") == ["m0.wav", "m1.wav", "m2.wav"]


# --------------------------------------------------------- loading itself

def test_the_manifest_is_read_off_the_calling_thread(studio, relative_corpus,
                                                     monkeypatch):
    threads = []
    real = tools.read_manifest

    def recording(*args, **kwargs):
        threads.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, "read_manifest", recording)
    view = studio.call("manifest.load", [str(relative_corpus)])
    assert not view["loaded"], "the table fills in when the job is done"
    assert studio.jobs.wait(30)

    assert threads and threads[0] is not threading.current_thread()
    assert studio.call("manifest.describe")["view"]["total"] == 18


def test_a_second_load_while_reading_is_refused(studio, relative_corpus,
                                                monkeypatch):
    release = threading.Event()
    real = tools.read_manifest
    monkeypatch.setattr(tools, "read_manifest",
                        lambda *a, **k: release.wait(10) and real(*a, **k))
    drain = _events(studio)

    try:
        # busy is what turns the page's Reload button into "Reading…"
        assert studio.call("manifest.load", [str(relative_corpus)])["busy"]
        studio.call("manifest.load", [str(relative_corpus)])
        events = drain()
        assert {"type": "job", "owner": "manifest", "busy": True,
                "label": "Reading…"} in events
        assert _said(events)[-1] == "A manifest is still being read."
    finally:
        release.set()
    assert studio.jobs.wait(30)
    view = studio.call("manifest.describe")["view"]
    assert view["total"] == 18
    assert view["busy"] is False


def test_a_file_that_is_not_a_manifest_is_reported(studio, tmp_path):
    broken = tmp_path / "manifest.json"
    broken.write_text('{"noisy": "a.wav"}', encoding="utf-8")
    drain = _events(studio)

    view = _load(studio, broken)
    said = _said(drain())
    assert not view["loaded"]
    assert said[-1].startswith("Could not read manifest.json")
    assert "not a JSON list" in said[-1]


# ----------------------------------------------------------- the window

def test_open_loads_the_selected_rows_noisy_file(studio, relative_corpus,
                                                 tmp_path, monkeypatch):
    """Through a sort, so the row on screen and the row in the manifest are
    different numbers -- the mix-up that would open the wrong file."""
    monkeypatch.chdir(tmp_path / "data")       # neither the root nor the folder
    first = _open(studio, relative_corpus)["rows"][0]
    view = studio.call("manifest.sort", [SNR, True])
    studio.call("manifest.select", [0])
    row = view["rows"][0]
    assert row["cells"][SNR] == "5"
    assert row["cells"] != first["cells"]

    studio.call("manifest.open_row")
    noisy = Path(row["tips"][NOISY]).resolve()
    assert studio.session.path == noisy
    assert str(noisy) in studio.files
    y, sr = kudio.file_load(noisy, sr=None, mono=True)
    assert studio.session.sr == sr
    assert np.array_equal(studio.session.y, y)

    # a double-click on a row selects it and then opens it
    other = view["rows"][3]
    studio.call("manifest.select", [3])
    studio.call("manifest.open_row")
    assert studio.session.path == Path(other["tips"][NOISY]).resolve()


def test_open_refuses_a_row_whose_noisy_file_is_gone(studio, relative_corpus):
    pairs = kudio.load_manifest(relative_corpus)
    Path(pairs[0].noisy).unlink()
    _open(studio, relative_corpus)
    studio.call("manifest.select", [0])
    drain = _events(studio)

    state = studio.call("manifest.open_row")
    assert state["has_audio"] is False
    assert "is not on disk" in _said(drain())[-1]


def test_auditioning_plays_each_side_without_loading_it(studio, absolute_corpus,
                                                        monkeypatch):
    played = []
    monkeypatch.setattr(studio.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(
                            (np.array(y), sr)))
    row = _open(studio, absolute_corpus)["rows"][2]
    studio.call("manifest.select", [2])

    for side in ("clean", "target", "noisy"):
        selection = studio.call("manifest.listen", [side, True])
        y, sr = kudio.file_load(row["tips"][tools.FIELDS.index(side)], sr=None, mono=True)
        assert played[-1][1] == sr
        assert np.allclose(played[-1][0], y), side
        assert selection["listening"] == side
    assert len(played) == 3
    assert studio.session.has_audio is False, "auditioning loads nothing"

    selection = studio.call("manifest.listen", ["noisy", False])   # the down button stops it
    assert studio.call("state")["previewing"] == ""
    assert selection["listening"] is None


def test_the_audition_follows_the_selection(studio, absolute_corpus, monkeypatch):
    played = []
    monkeypatch.setattr(studio.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(
                            np.array(y)))
    view = _open(studio, absolute_corpus)
    studio.call("manifest.select", [0])
    studio.call("manifest.listen", ["clean", True])

    selection = studio.call("manifest.select", [5])
    y, _ = kudio.file_load(view["rows"][5]["tips"][CLEAN], sr=None, mono=True)
    assert len(played) == 2
    assert np.allclose(played[-1], y)
    assert selection["listening"] == "clean"


def test_a_filter_that_keeps_the_row_does_not_restart_it(studio, absolute_corpus,
                                                         monkeypatch):
    """Changing what else is listed is no reason to hear the file from the top
    again; losing the row from the list is a reason to stop."""
    played = []
    monkeypatch.setattr(studio.player, "play",
                        lambda y, sr, gain=0.0, *, t0=0.0: played.append(len(y)))
    view = _open(studio, absolute_corpus)
    row = view["rows"][0]["cells"]
    studio.call("manifest.select", [0])
    studio.call("manifest.listen", ["noisy", True])
    assert len(played) == 1

    room = row[RT60]
    view = _pick(studio, view, "rt60", f"{room} s")
    assert _selected(view) == row
    assert len(played) == 1, "same row, same side: nothing to restart"

    view = _pick(studio, view, "rt60", "0.6 s" if room == "0.3" else "0.3 s")
    assert _selected(view) is None
    assert view["selection"]["listening"] is None
    assert studio.call("state")["previewing"] == ""

    _pick(studio, view, "rt60", "all")
    studio.call("manifest.select", [0])
    studio.call("manifest.listen", ["noisy", True])
    assert len(played) == 2, "a fresh press plays it again"


def test_something_else_claiming_playback_releases_the_button(
        studio, absolute_corpus, monkeypatch):
    monkeypatch.setattr(studio.player, "play", lambda *a, **k: None)
    _open(studio, absolute_corpus)
    studio.call("manifest.select", [0])
    studio.call("manifest.listen", ["noisy", True])
    assert studio.previewing("manifest")

    studio.call("stop")
    assert studio.call("manifest.describe")["view"]["selection"]["listening"] is None

    studio.call("manifest.listen", ["noisy", True])
    state = studio.call("manifest.open_row")           # opening ends the audition
    assert state["has_audio"]
    assert studio.call("manifest.describe")["view"]["selection"]["listening"] is None


class _Dialogs:
    """The window's file dialog: answers with *path*, and keeps the file
    types it was offered."""

    def __init__(self, path):
        self.path, self.types = path, []

    def open_files(self, directory, types, multiple=True):
        self.types.append(tuple(types))
        return [self.path]


def test_the_file_menu_opens_a_manifest_and_brings_the_panel_forward(
        studio, relative_corpus):
    """File → Open manifest… is the page's: it asks for the file with the
    types it names, then opens it. Nothing has to be loaded for either."""
    script = MAIN_JS.read_text(encoding="utf-8")
    file_menu = script.split('title: "&File"', 1)[1].split("title: ", 1)[0]
    entry = next(line for line in file_menu.splitlines() if '"Open &manifest…"' in line)
    assert "run: openManifest" in entry
    assert "enabled" not in entry, "needs no audio loaded"
    asks = script.split("async function openManifest()", 1)[1].split("\n}", 1)[0]
    assert 'choosePath("open"' in asks and 'command("open_manifest"' in asks
    types = json.loads(re.search(r"types: (\[[^\]]*\])", asks).group(1))

    studio.dialogs = _Dialogs(str(relative_corpus))
    assert not studio.session.has_audio
    drain = _events(studio)

    path = studio.call("choose_path", ["open"], {"title": "Open a corpus manifest",
                                                 "file_types": types})["path"]
    studio.call("open_manifest", [path])
    assert studio.jobs.wait(30)

    assert "*.json" in studio.dialogs.types[0][0]
    assert {"type": "tool", "id": "manifest"} in drain()
    assert studio.call("manifest.describe")["view"]["total"] == 18
    assert studio.prefs.get("last_dir") == str(relative_corpus.parent)


def test_a_manifest_named_on_the_command_line_opens_in_the_panel(
        studio, relative_corpus, tone):
    """Given to file_load, a manifest only fails as a file that is not audio.
    The command line hands its files to open_files, which sends it to the
    panel instead."""
    assert tools.is_manifest(relative_corpus) and tools.is_manifest("A.JSON")
    assert not tools.is_manifest(tone)
    drain = _events(studio)

    studio.call("open_files", [[str(relative_corpus), str(tone)]])
    assert studio.jobs.wait(30)
    events = drain()

    assert studio.call("manifest.describe")["view"]["total"] == 18
    assert {"type": "tool", "id": "manifest"} in events
    assert studio.files == [str(tone.resolve())], "audio only"
    assert studio.session.path == tone.resolve()
    assert not any("Could not open" in text for text in _said(events))


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
        studio, tmp_path, monkeypatch):
    """Two projects built from the same clean set name their mixtures alike.
    Started from the one that has some of them, the other's manifest is read
    whole against its own project: looked up file by file, it would pair this
    project's mixtures with that one's clean files and say every file was on
    disk."""
    theirs = _project(tmp_path / "theirs")
    pairs = kudio.load_manifest(theirs)
    ours = tmp_path / "ours"
    for pair in pairs[:3]:
        copy = ours / pair.noisy
        copy.parent.mkdir(parents=True, exist_ok=True)
        kudio.save_wave(copy, np.zeros(SR, np.float32), SR)

    manifest = tools.read_manifest(theirs, cwd=ours)
    assert manifest.missing == 0
    assert manifest.root == tmp_path / "theirs"
    assert all(tmp_path / "theirs" in getattr(row, side).parents
               for row in manifest.rows for side in ("noisy", "clean"))

    monkeypatch.chdir(ours)
    view = _open(studio, theirs)
    assert view["summary"][0].endswith(
        f"every file on disk · relative paths found under {tmp_path / 'theirs'}")


def test_without_a_folder_that_holds_everything_the_fullest_one_is_used(
        studio, tmp_path, monkeypatch):
    """A path missing from the chosen folder is missing, even where another
    folder has a file of that name: it is another corpus's file."""
    cwd, beside = tmp_path / "cwd", tmp_path / "beside"
    for folder, names in ((cwd, "ad"), (beside, "abc")):
        folder.mkdir()
        for name in names + "x":
            kudio.save_wave(folder / f"{name}.wav", np.zeros(160, np.float32), SR)
    path = kudio.save_manifest(
        beside / "manifest.json",
        [kudio.Pair(noisy=f"{name}.wav", clean="x.wav") for name in "abcd"])

    manifest = tools.read_manifest(path, cwd)
    assert manifest.root == beside
    assert [row.noisy for row in manifest.rows] == [
        beside / "a.wav", beside / "b.wav", beside / "c.wav", None]
    assert manifest.missing == 1

    monkeypatch.chdir(cwd)
    row = _open(studio, path)["rows"][3]
    assert row["tips"][NOISY] == f"not on disk: d.wav, looked for under {beside}"


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
        studio, relative_corpus, tmp_path, row, message):
    """Refused while the file is read, before the table is touched. Found by
    the filters, it would leave the panel naming the new manifest over the
    old one's rows."""
    good = _open(studio, relative_corpus)
    kept = studio.panel("manifest")._manifest
    drain = _events(studio)

    bad = tmp_path / "m1.json"
    bad.write_text(json.dumps([row]), encoding="utf-8")
    view = _load(studio, bad)

    assert _said(drain())[-1] == f"Could not read m1.json — ValueError: {message}"
    assert studio.panel("manifest")._manifest is kept
    assert view["path"] == good["path"]
    assert view["summary"] == good["summary"]
    assert view["total"] == 18


def test_a_manifest_that_cannot_be_shown_leaves_the_panel_as_it_was(
        studio, relative_corpus, monkeypatch):
    """The table's own work is done before anything is replaced, so a failure
    there too is a message and the last manifest, not half of each."""
    _open(studio, relative_corpus)
    kept = studio.panel("manifest")._manifest
    drain = _events(studio)
    monkeypatch.setattr(tools, "distinct", lambda rows, field: 1 / 0)

    view = _load(studio, relative_corpus)
    assert _said(drain())[-1].startswith("Could not read manifest.json — "
                                         "ZeroDivisionError")
    assert studio.panel("manifest")._manifest is kept
    assert view["total"] == 18


# ------------------------------------- coverage of the rows with damage

def test_rows_with_a_file_missing_are_counted_without_judging_the_corpus(
        studio, relative_corpus):
    """A full corpus with one mixture gone, filtered to the damage, has one
    row in one cell. That is no reason to tell it it needs more clean files."""
    pairs = kudio.load_manifest(relative_corpus)
    Path(pairs[0].noisy).unlink()
    full = _open(studio, relative_corpus)["coverage"]["note"]
    assert full[0].startswith("Every one of the 6 combinations")

    cov = studio.call("manifest.set_filters", [{}, True])["coverage"]
    note, tone = cov["note"]
    for wrong in ("cannot cover", "clean files", "gone missing", "unevenly"):
        assert wrong not in note
    assert note.startswith("Rows with a file missing, per noise × SNR: 1 in all")
    assert tone != "warn"
    zeros = [count for line in cov["counts"] for count in line if count == 0]
    assert len(zeros) == 5
    assert not cov["holes"], "a zero here is a combination with nothing missing"

    cov = studio.call("manifest.set_filters", [{}, False])["coverage"]
    assert cov["note"] == full
