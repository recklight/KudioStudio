# -*- coding: utf-8 -*-
"""Batch in the window: the form read into a plan, the passes it shows, a
Run on a job, and the loudness read-back listed afterwards."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import batch as tools
from kudiostudio.web.studio import Prefs, Studio

SR = 16000


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def tree(tmp_path) -> Path:
    """A tone that can reach -23 LUFS and a whisper with one click in it that
    cannot: levelled by loudness, the click goes far past full scale."""
    src = tmp_path / "in"
    (src / "a").mkdir(parents=True)
    t = np.arange(SR) / SR
    kudio.save_wave(src / "a" / "tone.wav",
                    (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), SR)
    spiky = (0.001 * np.random.default_rng(0).standard_normal(SR)).astype(np.float32)
    spiky[SR // 2] = 0.5
    kudio.save_wave(src / "a" / "spiky.wav", spiky, SR)
    return src


def _settings(src="", dst="", **changes):
    settings = {"src": str(src), "dst": str(dst), "rate": "keep", "subtype": "PCM_16",
                "mono": True, "trim": False, "top_db": 30.0, "normalize": "none",
                "target": 0.0, "tolerance": 1.0, "ceiling": -1.0, "denoise": None,
                "noise": "mcra", "room": False, "rt60": 0.5, "drr_db": 0.0,
                "seed": 0, "room_dst": ""}
    settings.update(changes)
    return settings


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


def test_the_call_is_the_conversion_alone_by_default(studio):
    text = studio.call("batch.call_text", [_settings("in", "out")])
    assert text == ("kudio.convert_folder('in', 'out', sr=None, mono=True, "
                    "subtype='PCM_16', trim_db=None)")


def test_the_call_lists_every_pass_in_the_order_they_run(studio):
    text = studio.call("batch.call_text", [_settings(
        "in", "out", normalize="LUFS", target=-23.0, denoise="wiener", room=True,
        seed=-1)])
    lines = text.split("\n")
    assert "lufs" not in lines[0], "levelled after the denoise, not by the conversion"
    assert lines[1].startswith("→ kudio.enhance_folder(out, out, 'wiener'")
    assert lines[2].startswith("→ per file: kudio.normalize_lufs(y, sr, lufs=-23.0)")
    assert "seed=file_seed(…, path)" in lines[3]
    assert lines[3].endswith(f"→ {str(Path('out_reverberant'))!r}")
    assert lines[4].startswith("→ read back: kudio.loudness, kudio.true_peak")


def test_a_missing_input_folder_is_refused(studio, tmp_path):
    said = _said(studio)
    studio.call("batch.run", [_settings(tmp_path / "nowhere", tmp_path / "out")])
    assert said()[-1] == "Select a valid input folder."
    assert not studio.jobs.running()


def test_copies_inside_the_output_are_refused(studio, tree, tmp_path):
    said = _said(studio)
    studio.call("batch.run", [_settings(tree, tmp_path / "out", room=True,
                                        room_dst=str(tmp_path / "out" / "wet"))])
    assert said()[-1] == tools.OVERLAP


def test_a_run_converts_the_tree(studio, tree, tmp_path):
    said = _said(studio)
    studio.call("batch.run", [_settings(tree, tmp_path / "out", rate="8000")])
    assert studio.jobs.wait(60)
    assert said()[-1] == "Batch complete — 2/2 written, 0 failed."
    y, sr = kudio.file_load(tmp_path / "out" / "a" / "tone.wav", sr=None)
    assert sr == 8000
    info = studio.call("batch.describe")
    assert info["result"] == f"2/2 file(s) written to {tmp_path / 'out'}"
    assert info["misses"] == ""


def test_the_files_that_missed_the_target_are_listed(studio, tree, tmp_path):
    said = _said(studio)
    studio.call("batch.run", [_settings(tree, tmp_path / "out", normalize="LUFS",
                                        target=-23.0)])
    assert studio.jobs.wait(60)
    assert said()[-1].endswith(", 1 missed the loudness target.")
    info = studio.call("batch.describe")
    assert "Loudness, read back from disk: 1 of 2 file(s)" in info["result"]
    assert info["misses"].startswith("could not get there:\n  out/a/spiky.wav")
    assert "clipped at full scale" in info["misses"]


def test_a_random_room_seed_is_drawn_and_named(studio, tree, tmp_path):
    studio.call("batch.run", [_settings(tree, tmp_path / "out", room=True, seed=-1)])
    assert studio.jobs.wait(60)
    result = studio.call("batch.describe")["result"]
    assert "Reverberant copies: 2/2 written to" in result
    assert "from seed …" not in result and "from seed " in result
    assert (tmp_path / "out_reverberant" / "a" / "tone.wav").is_file()


def test_an_unknown_choice_is_an_error_not_a_run(studio, tree, tmp_path):
    with pytest.raises(ValueError, match="unknown choice"):
        studio.panel("batch").run(_settings(tree, tmp_path / "out", rate="96000"))
    with pytest.raises(ValueError, match="unknown denoise method"):
        studio.panel("batch").call_text(_settings(tree, tmp_path / "out", denoise="magic"))


def test_the_result_follows_the_language(studio, tree, tmp_path):
    studio.call("batch.run", [_settings(tree, tmp_path / "out")])
    assert studio.jobs.wait(60)
    studio.call("set_language", ["zh_TW"])
    result = studio.call("batch.describe")["result"]
    assert result == i18n.translate(f"2/2 file(s) written to {tmp_path / 'out'}", "zh_TW")
    assert "written to" not in result


def test_every_tool_on_the_rail_has_its_page(studio):
    rail = studio.call("rail")
    assert [e["id"] for e in rail] == ["inspect", "effects", "enhance", "compare", "record",
                                       "synthesize", "manifest", "pitch", "metrics", "batch"]
    assert all(e["script"] for e in rail[1:])
