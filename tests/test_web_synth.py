# -*- coding: utf-8 -*-
"""Synthesize in the window: the call it shows, the checks before a
run, the run itself on a job, and the result line in either language."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


def _said(studio):
    q = studio.events.subscribe()

    def drain():
        out = []
        while not q.empty():
            e = q.get_nowait()
            if e["type"] == "message":
                out.append(e["text"])
        return out

    return drain


def _corpus(tmp_path, clean_level=0.4, noise_level=0.05):
    sr = 16000
    clean, noise = tmp_path / "clean", tmp_path / "noise"
    clean.mkdir()
    noise.mkdir()
    t = np.arange(sr) / sr
    kudio.save_wave(clean / "a.wav",
                    (clean_level * np.sin(2 * np.pi * 180 * t)).astype(np.float32), sr)
    kudio.save_wave(noise / "n.wav",
                    (noise_level * np.random.default_rng(0).standard_normal(2 * sr)
                     ).astype(np.float32), sr)
    return str(clean), str(noise)


def _call(studio, rt60="", drr=0.0, subtype="PCM_16", channel="none", targets=False, seed=-1):
    return studio.call("synthesize.call_text",
                       ["c", "n", "o", "-5, 0, 5", rt60, drr, subtype, channel, targets,
                        "regular", seed])


def test_the_call_leaves_out_what_is_at_its_default(studio):
    text = _call(studio)
    assert text == ("kudio.Synthesizer('c', 'n', out_path='o', snr_ratio=[-5, 0, 5])"
                    ".syn(mode='regular', seed=None)")


def test_the_call_names_the_rooms_the_link_and_the_format(studio):
    text = _call(studio, rt60="0.3, 0.8", drr=3, subtype="FLOAT", channel="telephone",
                 targets=True, seed=7)
    assert "rt60=[0.3, 0.8], drr_db=3.0" in text
    assert "channel='telephone'" in text and "write_targets=True" in text
    assert "subtype='FLOAT'" in text and text.endswith("seed=7)")


def test_a_bad_room_list_is_refused_before_anything_starts(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    said = _said(studio)
    studio.call("synthesize.run", [clean, noise, str(tmp_path / "mix"), "0", "0.3, -1"])
    assert said()[-1].startswith("Bad rt60 list — ")
    assert not studio.jobs.running()


def test_targets_without_a_room_are_refused(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    said = _said(studio)
    studio.call("synthesize.run", [clean, noise, str(tmp_path / "mix"), "0"],
                {"write_targets": True})
    assert "need rt60" in said()[-1]


def test_a_missing_folder_is_named(studio, tmp_path):
    said = _said(studio)
    studio.call("synthesize.run", [str(tmp_path / "nowhere"), str(tmp_path), "x", "0"])
    assert said()[-1] == "Select a valid clean folder."


def test_a_run_writes_the_corpus_and_its_manifest(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    out = tmp_path / "mix"
    said = _said(studio)
    studio.call("synthesize.run", [clean, noise, str(out), "0, 5"],
                {"rt60": "0.5", "channel": "telephone", "mode": "inc", "seed": 3})
    assert studio.jobs.wait(60)
    manifest = kudio.load_manifest(out / "manifest.json")
    assert len(manifest) == 2
    assert all(p.rt60 == 0.5 and p.channel == "telephone" for p in manifest)
    assert said()[-1] == "Synthesis complete — 2 file(s)."
    line = studio.call("synthesize.describe")["result"]
    assert line.startswith(f"2 file(s) written to {out}")
    assert "manifest.json beside them" in line and "down a telephone link" in line


def test_the_same_seed_writes_the_same_corpus(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    for name in ("a", "b"):
        studio.call("synthesize.run", [clean, noise, str(tmp_path / name), "0"], {"seed": 11})
        assert studio.jobs.wait(60)
    first = sorted((tmp_path / "a").rglob("*.wav"))
    second = sorted((tmp_path / "b").rglob("*.wav"))
    assert first and len(first) == len(second)
    for x, y in zip(first, second):
        assert np.array_equal(kudio.file_load(x, sr=None)[0], kudio.file_load(y, sr=None)[0])


def test_clipping_is_reported_with_the_way_out(studio, tmp_path):
    clean, noise = _corpus(tmp_path, clean_level=0.9, noise_level=0.3)
    said = _said(studio)
    studio.call("synthesize.run", [clean, noise, str(tmp_path / "mix"), "-10"])
    assert studio.jobs.wait(60)
    last = said()[-1]
    assert "went past full scale" in last and "subtype='FLOAT'" in last
    assert "clipped (worst peak" in studio.call("synthesize.describe")["result"]


def test_the_result_line_follows_the_language(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    studio.call("synthesize.run", [clean, noise, str(tmp_path / "mix"), "0"],
                {"channel": "telephone"})
    assert studio.jobs.wait(60)
    studio.call("set_language", ["zh_TW"])
    line = studio.call("synthesize.describe")["result"]
    assert line.startswith("已將 1 個檔案寫入 ")
    assert "manifest.json 在旁邊" in line and "經過 telephone 鏈路" in line


def test_an_unknown_choice_is_an_error_not_a_run(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    with pytest.raises(ValueError, match="unknown choice"):
        studio.panel("synthesize").run(clean, noise, str(tmp_path / "mix"), "0",
                                       subtype="PCM_8")


def test_the_rail_has_synthesize_ready(studio):
    entry = next(e for e in studio.call("rail") if e["id"] == "synthesize")
    assert entry["script"] == "js/panels/synth.js"
    assert Path(__file__).parents[1].joinpath("kudiostudio", "web", "static",
                                              entry["script"]).is_file()
