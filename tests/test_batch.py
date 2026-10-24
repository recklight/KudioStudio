# -*- coding: utf-8 -*-
"""The Batch panel's two passes past the command line.

"Normalise this tree to -23 LUFS and tell me which files could not get there"
is answered by reading every written file back, so the tests here build files
that cannot get there for each reason in turn and check the reason given.
"Re-room this tree and keep both" is checked the same way: the dry tree must
come through untouched and each wet copy must be exactly its seeded room.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio.tools.batch import (
    LOUDNESS_TOLERANCE,
    NORMALIZE_RANGES,
    TRUE_PEAK_CEILING,
    BatchOutcome,
    check_loudness,
    file_seed,
    reverberant_folder,
    run_batch,
    trees_overlap,
)
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio

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
def pair(tmp_path) -> Path:
    """The same take in two folders: a mirrored tree repeats names."""
    src = tmp_path / "in"
    for folder in ("a", "b"):
        (src / folder).mkdir(parents=True)
        kudio.save_wave(src / folder / "take.wav", tone(), SR)
    return src


@pytest.fixture
def studio(tmp_path):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.jobs.wait(30)
    s.shutdown()


def form(src, dst, **changes) -> dict:
    """The form as the page sends it, at the values it opens with.

    Picking a normalise mode puts that mode's default in the target box, as
    the page does, so a LUFS form asks for -23 unless it says otherwise.
    """
    settings = {"src": str(src), "dst": str(dst), "rate": "keep", "subtype": "PCM_16",
                "mono": True, "trim": False, "top_db": 30.0, "normalize": "none",
                "target": 0.0, "tolerance": LOUDNESS_TOLERANCE, "ceiling": TRUE_PEAK_CEILING,
                "denoise": None, "noise": next(iter(kudio.NOISE_ESTIMATORS)),
                "room": False, "rt60": 0.5, "drr_db": 0.0, "seed": 0, "room_dst": ""}
    mode = changes.get("normalize")
    if mode in NORMALIZE_RANGES and "target" not in changes:
        changes["target"] = NORMALIZE_RANGES[mode][1]
    settings.update(changes)
    return settings


def run(studio, settings) -> BatchOutcome:
    """The job as Run does it, on this thread, and its report."""
    panel = studio.panel("batch")
    plan = panel._read(settings, pin=True)
    outcome = run_batch(plan)
    panel._finished((plan.dst, plan.src), outcome)
    return outcome


def call(studio, settings) -> str:
    return studio.call("batch.call_text", [settings])


def shown(studio) -> dict:
    """The result and the list of misses, as the page is given them."""
    return studio.call("batch.describe")


def messages(studio):
    """What the status bar is told from now on, as a function that drains it."""
    q = studio.events.subscribe()

    def drain():
        said = []
        while not q.empty():
            event = q.get_nowait()
            if event["type"] == "message":
                said.append(event["text"])
        return said

    return drain


def by_name(check) -> dict:
    return {miss.path.name: miss for miss in check.misses}


#: Just enough of a page for a panel module to mount in node. Every ui.*
#: call and every element is one stand-in that keeps what it was given, and a
#: form keeps its rows by label, so a step finds a control by the row it is on.
PAGE = """\
const made = [];
class Fake {
  constructor(kind, given = {}) {
    Object.assign(this, { kind, given, rows: {}, children: [], on: {}, options: [],
                          hidden: false, disabled: false, classList: { add() {} },
                          value: given.value ?? given.checked });
    made.push(this);
  }
  get el() { return this; }
  get body() { return this; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(type, fn) { this.on[type] = fn; }
  row(label, control) { this.rows[label] = control; }
  setRange(low, high, { value } = {}) { if (value !== undefined) this.value = value; }
  values() { return {}; }
  set() {}
  show() {}
  setBusy() {}
}
globalThis.document = { createElement: (tag) => new Fake(tag) };
globalThis.Option = class extends Fake {
  constructor(text, value) { super("option", { value }); }
};
const ui = new Proxy({}, { get: (_, kind) => (...args) => {
  const fake = new Fake(kind, args.find((a) => a?.constructor === Object));
  if (Array.isArray(args[0])) fake.options = args[0].map(() => ({}));
  return fake;
} });
const formWith = (label) => made.find((el) => label in el.rows);
const row = (label) => formWith(label).rows[label];
const under = (el) => [el, ...(el.children ?? []).flatMap(under)];
"""


def mount_page(module: str, info: dict, steps: str):
    """Mount the page module *module* in node on the stand-ins above, with
    *info* as its panel's describe, then run *steps* and read back what they
    print as JSON. ``asked`` holds every other call the page made."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = (PAGE + f"const info = {json.dumps(info)};\n"
              "const asked = [];\n"
              "const ctx = { ui, t: (text) => text, memory: {}, window() {},\n"
              "  call: async (name, args) => (name === 'describe' ? info\n"
              "                               : (asked.push([name, args]), '')) };\n"
              f"const {{ mount }} = await import({json.dumps((STATIC / module).as_uri())});\n"
              "await mount(new Fake('root'), ctx);\n" + steps)
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------------- the loudness report

def test_every_written_file_is_read_back_against_the_target(studio, tree, tmp_path):
    check = run(studio, form(tree, tmp_path / "out", normalize="LUFS")).loudness

    assert check.target == -23.0 and check.tolerance == LOUDNESS_TOLERANCE
    assert check.checked == 4                  # three written, one refused
    assert "tone.wav" not in by_name(check)
    level = kudio.loudness(*kudio.file_load(tmp_path / "out" / "a" / "tone.wav"))
    assert level == pytest.approx(-23.0, abs=0.05)
    assert check.held == 1


def test_a_file_clipped_on_the_way_up_could_not_get_there(studio, tree, tmp_path):
    """Its loudness can even land near the target. It got there by clipping,
    which is not the file that was asked for."""
    check = run(studio, form(tree, tmp_path / "out", normalize="LUFS")).loudness
    miss = by_name(check)["spiky.wav"]

    assert miss.reason.startswith("clipped at full scale")
    assert miss.headroom is False
    written, _ = kudio.file_load(tmp_path / "out" / "a" / "spiky.wav")
    assert np.max(np.abs(written)) > 0.999, "it really was clipped"


def test_a_file_too_short_to_measure_is_listed_once_in_plain_words(studio, tree, tmp_path):
    outcome = run(studio, form(tree, tmp_path / "out", normalize="LUFS"))

    miss = by_name(outcome.loudness)["short.wav"]
    assert miss.lufs is None
    assert "too short to measure" in miss.reason and "0.4 s" in miss.reason
    # kudio's own failure for it is still in the outcome, but not repeated in
    # the summary beside the plain-words version
    assert any(path.name == "short.wav" for path, _ in outcome.failed)
    assert "short.wav" in shown(studio)["misses"]
    assert "short.wav" not in shown(studio)["result"]


def test_a_silent_file_has_no_level_to_raise(studio, tree, tmp_path):
    check = run(studio, form(tree, tmp_path / "out", normalize="LUFS")).loudness
    miss = by_name(check)["silent.wav"]
    assert miss.lufs is None and miss.reason.startswith("silent")


def test_a_float_tree_is_on_target_and_says_it_has_no_headroom(studio, tree, tmp_path):
    """FLOAT keeps the click 30 dB over full scale: the level is right, and the
    first fixed-point copy will clip."""
    check = run(studio, form(tree, tmp_path / "out", normalize="LUFS",
                             subtype="FLOAT")).loudness

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


def test_with_a_denoise_pass_the_tree_is_levelled_last(studio, tree, tmp_path):
    """Denoising takes level out with the noise. Levelled by the conversion
    and denoised after, the tree would land under the target it was asked
    for, and the report could only say why."""
    settings = form(tree, tmp_path / "out", normalize="LUFS", denoise="logmmse")
    convert, denoise, level, *_ = call(studio, settings).splitlines()
    assert "lufs" not in convert
    assert "enhance_folder" in denoise
    assert "normalize_lufs(y, sr, lufs=-23.0), after the denoise" in level

    outcome = run(studio, settings)
    check = outcome.loudness
    assert "tone.wav" not in by_name(check)
    written = kudio.file_load(tmp_path / "out" / "a" / "tone.wav")
    assert kudio.loudness(*written) == pytest.approx(-23.0, abs=0.05)
    assert "after the denoise pass" in shown(studio)["result"]

    # too short to level: the conversion wrote it, nothing could level it,
    # and it is listed once, in plain words
    miss = by_name(check)["short.wav"]
    assert "too short to measure" in miss.reason
    assert any(path.name == "short.wav" for path, _ in outcome.levelled.failed)
    assert "short.wav" not in shown(studio)["result"]
    assert check.checked == 4


def test_without_a_denoise_pass_the_conversion_levels(studio, tree, tmp_path):
    plan = studio.panel("batch")._read(form(tree, tmp_path / "out", normalize="LUFS"),
                                       pin=True)
    assert plan.level_last is False
    assert plan.conversion()["lufs"] == -23.0
    assert run_batch(plan).levelled is None


def test_the_denoise_pass_keeps_the_subtype_the_conversion_wrote(studio, tree, tmp_path):
    """enhance_folder writes PCM_16 unless told otherwise, so without the
    subtype passed on, a FLOAT tree comes back from the denoise pass 16-bit."""
    import soundfile

    settings = form(tree, tmp_path / "out", subtype="FLOAT", denoise="logmmse")
    assert "subtype='FLOAT'" in call(studio, settings).splitlines()[1]
    run(studio, settings)
    written = list((tmp_path / "out").rglob("*.wav"))
    assert written
    assert {soundfile.info(str(p)).subtype for p in written} == {"FLOAT"}


def test_there_is_no_report_without_a_loudness_target(studio, tree, tmp_path):
    settings = form(tree, tmp_path / "out", normalize="peak")
    outcome = run(studio, settings)
    assert outcome.loudness is None
    assert shown(studio)["misses"] == ""            # the page hides an empty list
    assert "read back" not in call(studio, settings)


def test_the_report_controls_follow_the_mode(studio):
    """Tolerance and ceiling are what the read-back holds files to, and only
    a LUFS run is read back."""
    live = mount_page("js/panels/batch.js", studio.call("batch.describe"), """
const normalize = under(row("normalize")).find((el) => el.kind === "select");
const check = under(row("check")).filter((el) => el.kind === "number");
const live = [check.map((el) => !el.disabled)];
for (const mode of ["LUFS", "none"]) {
  normalize.value = mode;
  normalize.given.onChange();
  live.push(check.map((el) => !el.disabled));
}
console.log(JSON.stringify(live));
""")
    opened, lufs, none = live
    assert opened == [False, False]                 # tolerance, ceiling
    assert lufs == [True, True]
    assert none == [False, False]


def test_the_tolerance_and_ceiling_set_reach_the_caption_and_the_plan(studio, tree,
                                                                       tmp_path):
    settings = form(tree, tmp_path / "out", normalize="LUFS", tolerance=0.5, ceiling=-2.0)
    last = call(studio, settings).splitlines()[-1]
    assert "within ±0.5 LU of -23 LUFS" in last and "-2 dBTP" in last

    plan = studio.panel("batch")._read(settings, pin=True)
    assert (plan.tolerance, plan.ceiling) == (0.5, -2.0)


def test_every_file_that_missed_is_listed_not_the_first_five(studio, tmp_path):
    """"Tell me which files" is the job; "… and 7 more" does not do it."""
    src = tmp_path / "in"
    src.mkdir()
    for i in range(12):
        kudio.save_wave(src / f"quiet{i:02d}.wav", np.zeros(SR, np.float32), SR)
    run(studio, form(src, tmp_path / "out", normalize="LUFS"))

    listed = [line for line in shown(studio)["misses"].splitlines() if "silent" in line]
    assert len(listed) == 12
    assert "0 of 12" in shown(studio)["result"]


def test_run_on_the_worker_reports_the_same(studio, tree, tmp_path):
    """The form is read before the job starts, not by the job while it can
    still change."""
    settings = form(tree, tmp_path / "out", normalize="LUFS")
    studio.call("batch.run", [settings])
    settings["normalize"] = "none"                  # too late to matter
    call(studio, settings)
    assert studio.jobs.wait(60)

    text = shown(studio)["misses"]
    assert "spiky.wav" in text and "short.wav" in text and "silent.wav" in text
    assert "1 of 4 file(s)" in shown(studio)["result"]


# -------------------------------------------------------- reverberant copies

def test_a_reverberant_copy_keeps_the_dry_tree_and_pairs_by_path(studio, pair, tmp_path):
    outcome = run(studio, form(pair, tmp_path / "out", room=True))

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


def test_each_wet_copy_is_its_seeded_room_and_nothing_else(studio, pair, tmp_path):
    outcome = run(studio, form(pair, tmp_path / "out", room=True,
                               subtype="FLOAT",       # compare arithmetic, not rounding
                               rt60=0.7, drr_db=3.0, seed=42))

    for dry_path, wet_path in outcome.rooms.written:
        dry, sr = kudio.file_load(dry_path)
        rel = dry_path.relative_to(tmp_path / "out")
        expected = kudio.apply_rir(dry, kudio.rir(sr, rt60=0.7, drr_db=3.0,
                                                  seed=file_seed(42, rel)))
        assert np.array_equal(kudio.file_load(wet_path)[0], expected)


def test_the_same_take_in_two_places_gets_two_rooms_and_the_seed_repeats_them(
        studio, pair, tmp_path):
    first = run(studio, form(pair, tmp_path / "out", room=True))
    a, b = (kudio.file_load(wet)[0] for _, wet in sorted(first.rooms.written))
    assert not np.allclose(a, b, atol=1e-3), "one room for the whole corpus"

    again = run(studio, form(pair, tmp_path / "again", room=True))
    for (_, one), (_, two) in zip(sorted(first.rooms.written),
                                  sorted(again.rooms.written)):
        assert np.array_equal(kudio.file_load(one)[0], kudio.file_load(two)[0])


def test_a_random_seed_is_drawn_at_run_and_reported(studio, pair, tmp_path):
    settings = form(pair, tmp_path / "out", room=True, seed=-1)
    assert "file_seed(…, path)" in call(studio, settings)

    panel = studio.panel("batch")
    plan = panel._read(settings, pin=True)
    assert isinstance(plan.room.seed, int)
    outcome = run_batch(plan)
    panel._finished((plan.dst, plan.src), outcome)
    assert outcome.rooms.seed == plan.room.seed
    assert f"seed {plan.room.seed}" in shown(studio)["result"]


def test_the_caption_names_the_room_and_where_it_goes(studio, pair, tmp_path):
    settings = form(pair, tmp_path / "out", room=True, rt60=0.8, seed=9)
    line = call(studio, settings).splitlines()[-1]
    assert "kudio.rir(sr, rt60=0.8, drr_db=0.0, seed=file_seed(9, path))" in line
    assert repr(str(tmp_path / "out_reverberant")) in line

    settings["room_dst"] = str(tmp_path / "wet")
    assert repr(str(tmp_path / "wet")) in call(studio, settings)
    settings["room"] = False
    assert "apply_rir" not in call(studio, settings)


@pytest.mark.parametrize("where", ["out", "out/wet"])
def test_the_copies_are_refused_a_folder_that_overlaps_the_output(studio, pair, tmp_path,
                                                                  where):
    said = messages(studio)
    studio.call("batch.run", [form(pair, tmp_path / "out", room=True,
                                   room_dst=str(tmp_path / where))])
    assert "folder of their own" in said()[-1]
    assert not (tmp_path / "out").exists(), "nothing was written"


def test_trees_overlap():
    assert trees_overlap("/data/out", "/data/out")
    assert trees_overlap("/data/out/wet", "/data/out")
    assert trees_overlap("/data", "/data/out")
    assert not trees_overlap("/data/out_reverberant", "/data/out")


def test_with_a_loudness_target_the_copies_are_levelled_too(studio, pair, tmp_path):
    """The room moves energy into the tail. Kept at the dry file's peak, the
    copy would be at another loudness than the tree it is beside."""
    outcome = run(studio, form(pair, tmp_path / "out", room=True, normalize="LUFS"))

    assert outcome.loudness.checked == 4             # both trees
    assert not outcome.loudness.misses
    for _, wet in outcome.rooms.written:
        assert kudio.loudness(*kudio.file_load(wet)) == pytest.approx(-23.0, abs=0.05)


def test_a_files_seed_depends_on_its_path_not_its_name():
    assert file_seed(7, "a/take.wav") != file_seed(7, "b/take.wav")
    assert file_seed(7, "a/take.wav") == file_seed(7, Path("a") / "take.wav")
    assert file_seed(7, "a/take.wav") != file_seed(8, "a/take.wav")
    assert file_seed(2 ** 31 - 1, "x.wav") >= 0
