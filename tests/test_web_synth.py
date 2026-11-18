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

from test_batch import mount_page


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


def _corpus(tmp_path, clean_level=0.4, noise_level=0.05, clean_files=1):
    sr = 16000
    clean, noise = tmp_path / "clean", tmp_path / "noise"
    clean.mkdir()
    noise.mkdir()
    t = np.arange(sr) / sr
    for i, name in enumerate("abcdefgh"[:clean_files]):
        kudio.save_wave(clean / f"{name}.wav",
                        (clean_level * np.sin(2 * np.pi * (180 + 40 * i) * t)
                         ).astype(np.float32), sr)
    kudio.save_wave(noise / "n.wav",
                    (noise_level * np.random.default_rng(0).standard_normal(2 * sr)
                     ).astype(np.float32), sr)
    return str(clean), str(noise)


def _wavs(folder):
    return {p.relative_to(folder).as_posix(): kudio.file_load(p, sr=None)[0]
            for p in sorted(Path(folder).rglob("*.wav"))}


def _call(studio, rt60="", drr=0.0, subtype="PCM_16", channel="none", targets=False, seed=-1,
          *boxes):
    return studio.call("synthesize.call_text",
                       ["c", "n", "o", "-5, 0, 5", rt60, drr, subtype, channel, targets,
                        "regular", seed, *boxes])


def test_the_call_leaves_out_what_is_at_its_default(studio):
    """kudio's default, that is: the window leaves out_path alone unless
    asked, and kudio's syn() empties it unless told not to."""
    text = _call(studio)
    assert text == ("kudio.Synthesizer('c', 'n', out_path='o', snr_ratio=[-5, 0, 5])"
                    ".syn(mode='regular', overwrite=False, seed=None)")


def test_the_call_names_the_rooms_the_link_and_the_format(studio):
    text = _call(studio, rt60="0.3, 0.8", drr=3, subtype="FLOAT", channel="telephone",
                 targets=True, seed=7)
    assert "rt60=[0.3, 0.8], drr_db=3.0" in text
    assert "channel='telephone'" in text and "write_targets=True" in text
    assert "subtype='FLOAT'" in text and text.endswith("seed=7)")


def test_the_call_names_the_boxes_that_are_off_their_default(studio):
    """Off its default, either box changes the run, so the line shown has to
    say so or it is not the call that runs."""
    assert _call(studio, "", 0.0, "PCM_16", "none", False, -1, False, True).endswith(
        ".syn(mode='regular', seed=None)")
    assert _call(studio, "", 0.0, "PCM_16", "none", False, -1, True, False).endswith(
        ".syn(mode='regular', is_add_clean=True, overwrite=False, seed=None)")

    asked = mount_page("js/panels/synth.js", studio.call("synthesize.describe"), """
const boxes = made.filter((el) => el.kind === "check");
boxes.find((el) => /is_add_clean/.test(el.label)).value = true;
const overwrite = boxes.find((el) => /overwrite/.test(el.label));
overwrite.value = false;
await overwrite.given.onChange();
console.log(JSON.stringify(asked.filter(([name]) => name === "call_text").pop()[1]));
""")
    assert asked[-2:] == [True, False]


def test_the_overwrite_box_says_it_empties_the_whole_folder(studio):
    """syn(overwrite=True) deletes out_path, whatever else is in it, before
    the run. Read as "overwrite existing files", it sounds like one file at
    a time."""
    tip = studio.call("synthesize.describe")["tips"]["overwrite"]
    words = " ".join(tip.split())
    assert "the whole folder is deleted before the run" in words
    assert "files that have nothing to do with the corpus included" in words
    box = mount_page("js/panels/synth.js", studio.call("synthesize.describe"), """
const box = made.find((el) => el.kind === "check" && /overwrite/.test(el.label));
console.log(JSON.stringify({ label: box.label, title: box.title, checked: box.given.checked }));
""")
    assert box["title"] == tip
    assert box["label"].startswith("delete everything in out_path")
    # off until someone ticks it: deleting a folder is not a default
    assert box["checked"] is False


def test_a_run_that_does_not_ask_leaves_out_paths_other_files_alone(studio, tmp_path):
    clean, noise = _corpus(tmp_path)
    out = tmp_path / "mix"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    studio.call("synthesize.run", [clean, noise, str(out), "5"])
    assert studio.jobs.wait(60)
    assert (out / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_a_run_that_writes_nothing_says_why(studio, tmp_path):
    """With the box off, kudio leaves a folder that holds anything alone and
    writes nothing; its warning is the only place the reason is given."""
    clean, noise = _corpus(tmp_path)
    out = tmp_path / "mix"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    said = _said(studio)
    studio.call("synthesize.run", [clean, noise, str(out), "0"], {"overwrite": False})
    assert studio.jobs.wait(60)

    last = said()[-1]
    assert last.startswith(f"{out} is not empty, so nothing was synthesised.")
    assert "complete" not in last
    assert (out / "notes.txt").read_text(encoding="utf-8") == "mine"
    line = studio.call("synthesize.describe")["result"]
    assert line.splitlines()[0] == f"0 file(s) written to {out}"
    assert line.splitlines()[1] == last

    shown = i18n.translate(last, "zh_TW")
    assert shown.startswith(f"{out} 不是空的") and "synthesised" not in shown


def test_rooms_multiply_the_output_in_regular_mode_too(studio, tmp_path):
    clean, noise = _corpus(tmp_path, clean_files=3)
    studio.call("synthesize.run", [clean, noise, str(tmp_path / "mix"), "0"],
                {"rt60": "0.3, 0.8", "mode": "regular", "seed": 1})
    assert studio.jobs.wait(60)
    assert len(_wavs(tmp_path / "mix")) == 6
    tip = studio.call("synthesize.describe")["tips"]["rt60"]
    assert "regular" in tip and "inc" in tip


def test_the_seed_does_not_decide_the_pairings(studio, tmp_path):
    """In regular mode the noise and SNR each clean file gets follow the
    file order; the seed moves where the noise is cropped."""
    clean, noise = _corpus(tmp_path, clean_files=3)
    pairs, mixtures = [], []
    for seed in (1, 2):
        out = tmp_path / f"seed{seed}"
        studio.call("synthesize.run", [clean, noise, str(out), "-5, 0, 5"], {"seed": seed})
        assert studio.jobs.wait(60)
        pairs.append(sorted((Path(p.clean).name, p.noise, p.snr_db)
                            for p in kudio.load_manifest(out / "manifest.json")))
        mixtures.append(_wavs(out))
    assert pairs[0] == pairs[1]
    assert mixtures[0].keys() == mixtures[1].keys()
    assert any(not np.array_equal(mixtures[0][k], mixtures[1][k]) for k in mixtures[0])
    note = studio.call("synthesize.describe")["seed_note"]
    assert "file order" in note and "pairings" not in note


def test_a_seed_left_random_is_named_and_rebuilds_the_corpus(studio, tmp_path):
    clean, noise = _corpus(tmp_path, clean_level=0.1, clean_files=2)
    said = _said(studio)
    studio.call("synthesize.run", [clean, noise, str(tmp_path / "a"), "5, 10"],
                {"rt60": "0.4"})
    assert studio.jobs.wait(60)
    seed = studio.panel("synthesize")._result.seed
    assert isinstance(seed, int) and seed >= 0
    assert said()[-1] == (f"Synthesis complete — 2 file(s) from seed {seed}; the same "
                          f"seed writes the same corpus again.")
    assert f"  ·  seed {seed}" in studio.call("synthesize.describe")["result"]

    studio.call("synthesize.run", [clean, noise, str(tmp_path / "b"), "5, 10"],
                {"rt60": "0.4", "seed": seed})
    assert studio.jobs.wait(60)
    first, again = _wavs(tmp_path / "a"), _wavs(tmp_path / "b")
    assert first.keys() == again.keys() and len(first) == 2
    assert all(np.array_equal(first[k], again[k]) for k in first)

    # and the box takes it: kudio draws below 2**31, far past 99999
    top = mount_page("js/panels/synth.js", studio.call("synthesize.describe"), """
console.log(JSON.stringify(row("seed").given.max));
""")
    assert top >= 2 ** 31 - 2


def test_a_missing_folder_reads_in_chinese(studio, tmp_path):
    studio.call("set_language", ["zh_TW"])
    said = _said(studio)
    studio.call("synthesize.run", [str(tmp_path / "nowhere"), str(tmp_path), "x", "0"])
    assert said()[-1] == "請選擇有效的 clean 資料夾。"
    studio.call("synthesize.run", [str(tmp_path), str(tmp_path / "nowhere"), "x", "0"])
    assert said()[-1] == "請選擇有效的 noise 資料夾。"


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
    line = studio.call("synthesize.describe")["result"]
    assert "clipped (worst peak" in line
    assert "went past full scale" not in line, "kudio's own sentence is not repeated under it"


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


def test_the_answer_to_a_refused_run_is_older_than_its_result(studio, tmp_path):
    """kudio refuses a folder that is not empty at once, and the result says
    so before the click is answered. The answer carries the state from when
    the run began, numbered below that result, so the page drops it instead
    of putting the run before's line back."""
    clean, noise = _corpus(tmp_path)
    out = tmp_path / "mix"
    studio.call("synthesize.run", [clean, noise, str(out), "0"])
    assert studio.jobs.wait(60)
    q = studio.events.subscribe()
    answer = studio.call("synthesize.run", [clean, noise, str(out), "0"])
    assert studio.jobs.wait(60)
    events = []
    while not q.empty():
        e = q.get_nowait()
        if e["type"] == "panel" and e["panel"] == "synthesize":
            events.append(e)
    assert answer["result"] == "—"
    assert events[-1]["info"]["result"].startswith(f"0 file(s) written to {out}")
    assert answer["serial"] < events[-1]["serial"]
