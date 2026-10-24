# -*- coding: utf-8 -*-
"""Manifest in the window: a corpus opened from File → Open, its rows
served a slice at a time, filtered, sorted, checked against the disk and
auditioned. The corpora are synthesised for real, as in test_tool_manifest."""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import manifest as tools
from kudiostudio.web.studio import Prefs, Studio

SR = 16000
SNR = tools.COLUMNS.index("SNR dB")


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def corpus(tmp_path, monkeypatch) -> Path:
    """3 clean × 2 noises × 3 SNRs in inc mode, relative paths: 18 rows,
    3 per combination, the manifest two folders below where paths start."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clean").mkdir()
    (tmp_path / "noise").mkdir()
    t = np.arange(SR) / SR
    for i in range(3):
        kudio.save_wave(tmp_path / "clean" / f"c{i}.wav",
                        (0.05 * np.sin(2 * np.pi * (180 + 40 * i) * t)).astype(np.float32), SR)
    for j in range(2):
        kudio.save_wave(tmp_path / "noise" / f"n{j}.wav",
                        (0.01 * np.random.default_rng(j).standard_normal(2 * SR)
                         ).astype(np.float32), SR)
    syx = kudio.Synthesizer("clean", "noise", out_path="data/mixed", snr_ratio=[-5, 0, 5])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        syx.syn(mode="inc", seed=1)
    return kudio.save_manifest(Path("data/mixed/manifest.json"), syx.manifest()).absolute()


def _events(studio):
    q = studio.events.subscribe()

    def drain(kind=None):
        out = []
        while not q.empty():
            e = q.get_nowait()
            if kind is None or e["type"] == kind:
                out.append(e)
        return out

    return drain


def _open(studio, path):
    studio.call("open_files", [[str(path)]])
    assert studio.jobs.wait(30)
    return studio.call("manifest.describe")["view"]


def test_file_open_sends_a_manifest_to_its_panel(studio, corpus):
    drain = _events(studio)
    view = _open(studio, corpus)
    assert {"type": "tool", "id": "manifest"} in drain("tool")
    assert not studio.session.has_audio, "a manifest is not audio"
    assert view["loaded"] and view["total"] == view["shown"] == 18
    assert view["summary"][1] == "ok" and "every file on disk" in view["summary"][0]
    assert view["call"] == f"kudio.load_manifest({str(corpus)!r})"


def test_rows_come_a_slice_at_a_time(studio, corpus):
    view = _open(studio, corpus)
    assert [r["p"] for r in view["rows"]] == list(range(18))
    tail = studio.call("manifest.rows", [15, 10])
    assert [r["p"] for r in tail] == [15, 16, 17]
    # the shared folder is left off the names
    assert all(not r["cells"][0].startswith("data") for r in tail)


def test_snr_sorts_by_value_and_a_third_click_restores_the_order(studio, corpus):
    first = _open(studio, corpus)["rows"]
    down = studio.call("manifest.sort", [SNR, True])["rows"]
    assert [r["cells"][SNR] for r in down[:6]] == ["5"] * 6
    assert [r["cells"][SNR] for r in down[-6:]] == ["-5"] * 6
    again = studio.call("manifest.sort", [-1])["rows"]
    assert [r["cells"] for r in again] == [r["cells"] for r in first]


def test_a_filter_narrows_the_rows_and_the_grid(studio, corpus):
    _open(studio, corpus)
    view = studio.call("manifest.set_filters", [{"noise": 1}])
    assert view["shown"] == 9 and view["showing"] == "showing 9 of 18"
    cov = view["coverage"]
    assert len(cov["noises"]) == 1 and cov["counts"] == [[3, 3, 3]]
    assert cov["note"] == ["Every one of the 3 combinations has 3 row(s).", "ok"]


def test_a_coverage_cell_shows_its_own_rows(studio, corpus):
    view = _open(studio, corpus)
    assert view["coverage"]["snrs"] == ["-5 dB", "0 dB", "5 dB"]
    view = studio.call("manifest.focus", [1, 2])
    assert view["shown"] == 3
    picked = {f["field"]: f["index"] for f in view["filters"]}
    assert picked["noise"] == 2 and picked["snr_db"] == 3
    assert {r["cells"][SNR] for r in view["rows"]} == {"5"}


def test_a_missing_file_is_marked_and_can_be_shown_alone(studio, corpus):
    first = kudio.load_manifest(corpus)[0]
    os.remove(first.noisy)
    view = _open(studio, corpus)
    assert view["summary"][1] == "warn" and "1 with a file missing" in view["summary"][0]
    view = studio.call("manifest.set_filters", [{}, True])
    assert view["shown"] == 1
    row = view["rows"][0]
    assert row["missing"] == [0]
    assert row["tips"][0].startswith("not on disk: ")
    # counting the damage says nothing about coverage, so no holes are marked
    assert not view["coverage"]["holes"]
    assert view["coverage"]["note"][0].startswith("Rows with a file missing")


def test_the_selection_stays_on_its_row_through_a_sort(studio, corpus):
    view = _open(studio, corpus)
    chosen = view["rows"][0]["cells"]
    studio.call("manifest.select", [0])
    view = studio.call("manifest.sort", [SNR, True])
    at = view["selection"]["position"]
    assert at > 0 and view["rows"][at]["cells"] == chosen


def test_audition_follows_the_selection_and_stops(studio, corpus):
    drain = _events(studio)
    _open(studio, corpus)
    studio.call("manifest.select", [0])
    assert studio.call("manifest.listen", ["noisy", True])["listening"] == "noisy"
    assert studio.previewing("manifest")
    heard = studio._preview[0]
    studio.call("manifest.select", [5])
    assert studio._preview[0] is not heard, "the next row's file plays"
    assert not studio.session.has_audio, "auditioning loads nothing"
    assert studio.call("manifest.listen", ["noisy", False])["listening"] is None
    assert not studio.previewing("manifest")
    assert {"type": "preview", "owner": "manifest", "on": False} in drain("preview")


def test_a_row_without_a_target_cannot_be_heard_as_one(studio, corpus):
    _open(studio, corpus)
    side = studio.call("manifest.select", [0])["sides"]["target"]
    assert not side["enabled"] and "no target" in side["tip"]


def test_starting_an_audition_ends_another_panels_preview(studio, corpus):
    _open(studio, corpus)
    studio.start_preview("effects", np.zeros(1600, np.float32), SR)
    drain = _events(studio)
    studio.call("manifest.select", [0])
    studio.call("manifest.listen", ["clean", True])
    assert {"type": "preview", "owner": "effects", "on": False} in drain("preview")
    assert studio.previewing("manifest")


def test_open_loads_the_noisy_file_for_editing(studio, corpus):
    view = _open(studio, corpus)
    studio.call("manifest.select", [0])
    state = studio.call("manifest.open_row")
    assert state["has_audio"] and studio.session.name == view["rows"][0]["cells"][0]


def test_something_that_is_not_a_manifest_is_reported(studio, tmp_path):
    broken = tmp_path / "manifest.json"
    broken.write_text('{"noisy": "a.wav"}', encoding="utf-8")
    drain = _events(studio)
    studio.call("open_manifest", [str(broken)])
    assert studio.jobs.wait(30)
    said = [e["text"] for e in drain("message")]
    assert said[-1].startswith("Could not read manifest.json — ")
    assert not studio.call("manifest.describe")["view"]["loaded"]


def test_the_summary_follows_the_language(studio, corpus):
    _open(studio, corpus)
    studio.call("set_language", ["zh_TW"])
    view = studio.call("manifest.describe")["view"]
    english = "every file on disk"
    assert english not in view["summary"][0]
    assert view["summary"][0] == " · ".join(
        i18n.translate(p, "zh_TW") for p in ["18 row(s)", english,
                                              f"relative paths found under {corpus.parents[2]}"])


def test_the_rail_has_manifest_ready(studio):
    entry = next(e for e in studio.call("rail") if e["id"] == "manifest")
    assert entry["script"] == "js/panels/manifest.js"
