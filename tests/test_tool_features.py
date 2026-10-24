# -*- coding: utf-8 -*-
"""The effects that shape a training set -- fades and filters, rooms both
synthetic and measured, the channel -- and what Batch, Synthesize and
Metrics say about the runs that use them."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import batch as batch_tools
from kudiostudio.tools import effects, metrics
from kudiostudio.tools import synth as synth_tools
from kudiostudio.tools.synth import parse_rt60
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio

SR = 16000


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def blank(tmp_path, monkeypatch):
    """A window with nothing open, and nothing to play through."""
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def studio(blank, tone):
    """The window with the 1 s test tone open."""
    blank.call("open_files", [[str(tone)]])
    return blank


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


def _published(studio, panel):
    """What *panel* sends its page from here on."""
    q = studio.events.subscribe()

    def drain():
        out = []
        while not q.empty():
            e = q.get_nowait()
            if e["type"] == "panel" and e["panel"] == panel:
                out.append(e)
        return out

    return drain


def _capped(name, sr):
    """An effect's cutoffs as the page offers them on a clip at *sr*: capped
    just under Nyquist, which the 8 kHz low-pass would sit on at 16 kHz.
    test_filter_cutoffs_are_capped_at_nyquist reads the page's boxes back."""
    return {p.name: min(p.default, sr / 2 - 1) for p in effects.PARAMS[name] if p.nyquist}


# ------------------------------------------------------- the page, under node

#: As much of a document as the controls use: enough to build them, change
#: them and read them back. The panel's own script is mounted on it the way
#: the window mounts it, and every call it makes is kept.
_PAGE = """
class Node {
  constructor(tag) {
    Object.assign(this, { tagName: tag, children: [], listeners: {}, dataset: {}, style: {},
                          className: "", textContent: "", title: "" });
    const names = new Set();
    this.classList = {
      add: (...n) => n.forEach((c) => names.add(c)),
      remove: (...n) => n.forEach((c) => names.delete(c)),
      toggle: (c, on = !names.has(c)) => { if (on) names.add(c); else names.delete(c); return on; },
      contains: (c) => names.has(c),
    };
  }
  get options() { return this.children.filter((c) => c.tagName === "option"); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  setAttribute() {}
  fire(type) { for (const fn of this.listeners[type] || []) fn({ target: this }); }
  *walk() { yield this; for (const c of this.children) if (c instanceof Node) yield* c.walk(); }
}
globalThis.document = { createElement: (tag) => new Node(tag),
                        querySelector: () => ({ content: "" }) };
const ui = await import(%(ui)s);
const { mount } = await import(%(script)s);
const info = %(info)s, data = %(data)s, calls = [];
const ctx = { ui, t: (s) => s, memory: %(memory)s, dialogs: {}, window() {},
              async call(name, args = []) {
                calls.push([name, args]);
                return name === "call_text" ? "" : info;
              } };
const root = new Node("div");
const hooks = await mount(root, ctx);
const find = (tag) => [...root.walk()].filter((n) => n.tagName === tag);
const settle = () => new Promise((done) => setTimeout(done, 200));
const sent = (name) => calls.filter(([n]) => n === name).map(([, args]) => args);
"""


def _page(script, info, scenario, memory=None, data=None):
    """Mount the page module *script* under node with *info* as its describe,
    run *scenario* after it, and read what that prints as JSON. The scenario
    has the module's `hooks`, `find(tag)`, `settle()`, `sent(name)` and
    *data* to work with."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = _PAGE % {"ui": json.dumps((STATIC / "js" / "ui.js").as_uri()),
                      "script": json.dumps((STATIC / script).as_uri()),
                      "info": json.dumps(info), "data": json.dumps(data),
                      "memory": json.dumps(memory or {})}
    done = subprocess.run([node, "--input-type=module"], input=source + scenario,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------- fades, filters and trims

@pytest.mark.parametrize("name", ["fade_in", "fade_out", "fade", "reverse", "remove_dc",
                                  "highpass", "lowpass", "bandpass", "bandstop", "vad_trim"])
def test_each_fade_filter_and_trim_names_its_call_and_runs(studio, name):
    """Each one has a parameter list of its own and actually runs."""
    assert name in studio.call("effects.describe")["params"]
    call_text, out = studio.panel("effects")._render(name, _capped(name, studio.session.sr))
    assert call_text.startswith(f"kudio.{name}(")
    assert np.asarray(out).size > 0


def test_reverse_actually_reverses(studio):
    _, out = studio.panel("effects")._render("reverse", {})
    assert np.array_equal(out, np.asarray(studio.session.y)[::-1])


def test_fade_reaches_silence_at_the_edges(studio):
    _, out = studio.panel("effects")._render("fade", {"fade_in_s": 0.05, "fade_out_s": 0.05})
    assert out[0] == pytest.approx(0.0, abs=1e-6)
    assert out[-1] == pytest.approx(0.0, abs=1e-6)


_CUTOFF_BOXES = """
const effect = find("select")[0], shown = [];
const boxes = () => find("input").filter((n) => n.type === "number")
  .map((n) => [Number(n.max), Number(n.value)]);
for (const state of data.states) {
  hooks.state(state);
  const seen = { arrived: [effect.value, boxes()] };
  for (const name of data.filters) {
    effect.value = name;
    effect.fire("change");
    seen[name] = boxes();
  }
  shown.push(seen);
}
console.log(JSON.stringify(shown));
"""


def test_filter_cutoffs_are_capped_at_nyquist(studio):
    """A cutoff above Nyquist is not a filter but a FeatureError at Apply.
    The page caps every box the parameter list marks at half the rate in the
    window's state: on the page that is up when the clip arrives, and on each
    one chosen after it."""
    info = studio.call("effects.describe")
    marked = {(name, p["name"]) for name in info["effects"]
              for p in info["params"][name] if p["nyquist"]}
    assert marked == {("highpass", "cutoff"), ("lowpass", "cutoff"),
                      ("bandpass", "low"), ("bandpass", "high"),
                      ("bandstop", "low"), ("bandstop", "high")}
    states = [studio.call("state")]
    studio.session.set_audio(np.zeros(8000, dtype=np.float32), 8000, name="8k")
    states.append(studio.call("state"))
    assert [s["sr"] for s in states] == [16000, 8000]

    filters = ["highpass", "lowpass", "bandpass", "bandstop"]
    shown = _page("js/panels/effects.js", info, _CUTOFF_BOXES, memory={"effect": "lowpass"},
                  data={"states": states, "filters": filters})

    def check(name, boxes, sr):
        numbers = [p for p in info["params"][name] if p["kind"] in ("float", "int")]
        assert len(boxes) == len(numbers)
        for p, (top, value) in zip(numbers, boxes):
            if p["nyquist"]:
                assert top <= sr / 2 and value <= top, (name, p["name"], sr)
            else:
                assert top == p["max"], (name, p["name"], sr)

    for state, seen in zip(states, shown):
        check(*seen.pop("arrived"), state["sr"])
        assert sorted(seen) == sorted(filters)
        for name, boxes in seen.items():
            check(name, boxes, state["sr"])
    # the 8 kHz default sits on Nyquist at 16 kHz, and the box holds just under it
    assert shown[0]["lowpass"][0] == [7999, 7999]
    assert shown[1]["lowpass"][0][0] <= 4000


def test_the_caption_follows_a_choice_not_just_a_number(studio):
    """The page asks for the call again when a choice changes, as it does
    when a number does."""
    assert "shape='linear'" in studio.call("effects.call_text", ["fade_in", {}])
    asked = _page("js/panels/effects.js", studio.call("effects.describe"), """
await settle();
const before = sent("call_text").length;
const shape = find("select").find((s) => s.options.some((o) => o.value === "cosine"));
shape.value = "cosine";
shape.fire("change");
await settle();
console.log(JSON.stringify(sent("call_text").slice(before)));
""", memory={"effect": "fade_in"})
    assert asked, "the page did not ask again"
    assert asked[-1][0] == "fade_in" and asked[-1][1]["shape"] == "cosine"
    assert "shape='cosine'" in studio.call("effects.call_text", asked[-1])


def _save_offered(info, then):
    """Whether the page's Save segments… can be pressed with *info* shown,
    and once each of *then* has arrived after it."""
    return _page("js/panels/effects.js", info, """
const save = find("button").find((b) => b.textContent === "Save segments…");
const offered = [!save.disabled];
for (const state of data) hooks.event({ info: state });
offered.push(!save.disabled);
console.log(JSON.stringify(offered));
""", memory={"effect": "split_on_silence"}, data=list(then))


def test_split_segments_can_be_written_out(studio, tmp_path):
    """split_on_silence changes nothing in the clip, so what it found is only
    of use once it is on disk."""
    before = studio.call("effects.describe")
    assert before["segments"] == 0
    info = studio.call("effects.apply", ["split_on_silence", {}])
    assert info["segments"] > 0
    assert _save_offered(before, [info]) == [False, True]

    out = tmp_path / "segments"          # not tmp_path itself: the fixture
    out.mkdir()                          # already wrote tone.wav in there
    studio.call("effects.save_segments", [str(out)])
    assert len(list(out.glob("*.wav"))) == info["segments"]


def test_new_audio_forgets_the_old_segments(studio, tmp_path):
    studio.call("effects.apply", ["split_on_silence", {}])
    shown = studio.call("effects.describe")
    assert shown["segments"] > 0
    published = _published(studio, "effects")
    studio.session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="new")
    sent = [e["info"] for e in published() if "info" in e]
    assert sent and sent[-1]["segments"] == 0
    assert studio.call("effects.measure_ir")["segments"] == 0
    assert _save_offered(shown, sent) == [True, False]

    said = _said(studio)
    out = tmp_path / "segments"
    out.mkdir()
    studio.call("effects.save_segments", [str(out)])
    assert said() == ["Run split_on_silence first."]
    assert list(out.iterdir()) == []


def test_every_effect_has_a_parameter_page(studio):
    """The page builds an effect's controls from its own entry in the list it
    is served. An effect without one would have no controls, and its call
    would fail on the first value it looked up."""
    info = studio.call("effects.describe")
    assert info["effects"] == list(effects.EFFECTS)
    assert set(info["params"]) == set(info["effects"])
    for name in info["effects"]:
        assert studio.call("effects.call_text",
                           [name, _capped(name, studio.session.sr)]).startswith("kudio.")


# --------------------------------------------------------------- DNSMOS

def test_dnsmos_fills_the_table(studio, monkeypatch):
    monkeypatch.setattr(kudio, "dnsmos", lambda y, sr: kudio.DnsmosScore(
        sig=3.1, bak=4.2, ovrl=3.4, segments=2))
    studio.call("metrics.dnsmos")
    assert studio.jobs.wait(30)
    info = studio.call("metrics.describe")
    labels = [label for label, _ in info["rows"]]
    assert labels[:3] == ["SIG (speech)", "BAK (background)", "OVRL (overall)"]
    text, warn = info["dnsmos"]
    assert "intrusive" not in text and not warn


# ---------------------------------------------------- denoising in Batch

def _form(studio, src="", dst="", **changes):
    """The settings the batch page sends with its controls as they start."""
    info = studio.call("batch.describe")
    settings = {"src": str(src), "dst": str(dst), "rate": info["rates"][0],
                "subtype": info["subtypes"][0], "mono": True, "trim": False,
                "top_db": 30.0, "normalize": "none", "target": 0.0,
                "tolerance": info["tolerance"], "ceiling": info["ceiling"],
                "denoise": None, "noise": info["noise"][0][0], "room": False,
                "rt60": 0.5, "drr_db": 0.0, "seed": 0, "room_dst": ""}
    settings.update(changes)
    return settings


def _denoise_page(studio):
    """The batch page as it starts and once logmmse is chosen: whether the
    noise estimator beside the denoiser is greyed out, and the settings the
    page sends for its call."""
    return _page("js/panels/batch.js", studio.call("batch.describe"), """
await settle();
const denoise = find("select").find((s) => s.options.some((o) => o.value === ""));
const noise = find("select").find((s) => s.options[0]?.value === info.noise[0][0]);
const seen = [[noise.disabled, sent("call_text").pop()[0]]];
denoise.value = "logmmse";
denoise.fire("change");
await settle();
seen.push([noise.disabled, sent("call_text").pop()[0]]);
console.log(JSON.stringify(seen));
""", memory={"src": "in", "dst": "out"})


def test_batch_denoise_is_off_by_default(blank):
    (greyed, settings), _ = _denoise_page(blank)
    assert settings["denoise"] is None
    assert greyed is True, "the estimator is greyed out with nothing to estimate for"
    assert settings == _form(blank, "in", "out")
    panel = blank.panel("batch")
    assert panel._read({"src": "in", "dst": "out"}).denoise is None

    # greyed out, it still sends its value; that value goes nowhere
    plan = panel._read(settings)
    assert plan.denoise is None and plan.noise is None
    assert "enhance_folder" not in blank.call("batch.call_text", [settings])


def test_choosing_a_denoiser_shows_the_second_pass(blank):
    _, (greyed, settings) = _denoise_page(blank)
    assert greyed is False
    assert settings == _form(blank, "in", "out", denoise="logmmse")
    noise = settings["noise"]
    assert blank.panel("batch")._read(settings).noise == noise
    text = blank.call("batch.call_text", [settings])
    assert "kudio.convert_folder(" in text
    assert "kudio.enhance_folder(" in text and "'logmmse'" in text
    assert f"noise={noise!r}" in text


def test_batch_denoises_a_folder(blank, tmp_path):
    """A second pass in place over what the conversion just wrote."""
    rng = np.random.default_rng(0)
    t = np.arange(int(3 * SR)) / SR
    speech = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 14))
    speech *= 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)
    clean = np.zeros(len(t), dtype=np.float32)
    clean[SR // 2:2 * SR] = (speech[:int(1.5 * SR)] / 7).astype(np.float32)
    noisy = (clean + 0.05 * rng.standard_normal(len(clean))).astype(np.float32)

    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    kudio.save_wave(src / "a.wav", noisy, SR)

    blank.call("batch.run", [_form(blank, src, out, denoise="logmmse")])
    assert blank.jobs.wait(120)
    result = blank.panel("batch")._outcome.result

    assert result.written == 1
    assert result.mean_reduction_db() > 5.0
    before = kudio.audio_report(*kudio.file_load(src / "a.wav"))
    after = kudio.audio_report(*kudio.file_load(next(out.glob("*.wav"))))
    assert after.noise_floor_dbfs < before.noise_floor_dbfs - 5.0


def test_the_denoise_summary_refuses_to_call_it_better(blank, tmp_path):
    outcome = batch_tools.BatchOutcome(result=kudio.EnhanceFolderResult(
        written=2, total=2, outputs=[], failed=[],
        floors=((tmp_path / "a.wav", -30.0, -45.0),
                (tmp_path / "b.wav", -32.0, -46.0))))
    blank.panel("batch")._finished((str(tmp_path), ""), outcome)
    text = blank.call("batch.describe")["result"]
    assert "14.5 dB on average" in text
    assert "not of whether it sounds better" in text


# ------------------------------------------------------------- the room

def test_the_room_effect_uses_a_real_impulse_response(session):
    """`reverb` is a comb filter; `room` convolves with an impulse response
    that can be measured afterwards."""
    call, operation = effects.operation("room", {"rt60": 0.7, "drr_db": 6.0, "seed": 3})
    assert "kudio.apply_rir" in call and "rt60=0.7" in call and "seed=3" in call

    wet = operation(session.y, session.sr)
    assert len(wet) == len(session.y), "the length is kept"
    assert not np.allclose(wet, session.y, atol=1e-3)

    # the room that was asked for is the room that came out
    measured = kudio.rt60(kudio.rir(session.sr, rt60=0.7, drr_db=6.0, seed=3),
                          session.sr)
    assert measured.t30 == pytest.approx(0.7, rel=0.06)
    assert measured.drr == pytest.approx(6.0, abs=0.2)


def test_the_room_effect_is_reproducible_from_its_seed(session):
    first = effects.operation("room", {"seed": 7})[1](session.y, session.sr)
    second = effects.operation("room", {"seed": 7})[1](session.y, session.sr)
    assert np.array_equal(first, second)

    other = effects.operation("room", {"seed": 8})[1](session.y, session.sr)
    assert not np.array_equal(other, first)


# ---------------------------------------------------------- a measured room

def measured_room(path, sr: int = 48000, delay_ms: float = 12.0,
                  stereo: bool = True) -> Path:
    """A room as a measurement leaves it: the travel time before the direct
    sound, a rate of its own, and two channels."""
    ir = np.concatenate([np.zeros(int(delay_ms / 1000 * sr), dtype=np.float32),
                         kudio.rir(sr, rt60=0.6, drr_db=3.0, seed=1)])
    data = np.stack([ir, 0.8 * ir], axis=1) if stereo else ir
    kudio.save_wave(path, data, sr, subtype="FLOAT")
    return Path(path)


def test_a_measured_room_is_read_at_the_clips_rate_and_in_mono(studio, tmp_path):
    path = measured_room(tmp_path / "hall.wav")
    studio.call("effects.load_ir", [str(path)])

    held = studio.panel("effects")._response
    assert held.sr == studio.session.sr == 16000
    assert held.samples.ndim == 1
    # the read the recorded call makes, sample for sample
    assert np.array_equal(held.samples, kudio.file_load(path, sr=16000)[0])


def test_the_measured_room_caption_is_the_call_apply_makes(studio, tmp_path):
    """Run with y and sr bound, the recorded step makes exactly what Apply did."""
    path = measured_room(tmp_path / "hall.wav")
    studio.call("effects.load_ir", [str(path)])
    onset = int(0.012 * 16000)                    # 12 ms of travel, at 16 kHz
    assert studio.call("effects.call_text", ["measured_room", {"ir": str(path)}]) == (
        f"kudio.apply_rir(y, kudio.file_load('hall.wav', sr=sr)[0][{onset}:])")

    before = studio.session.y.copy()
    studio.call("effects.apply", ["measured_room", {"ir": str(path)}])
    code = studio.session.history[-1].code
    assert repr(str(path.absolute())) in code
    pasted = {"kudio": kudio, "np": np, "y": before, "sr": studio.session.sr}
    exec(code, pasted)
    assert np.array_equal(pasted["y"], studio.session.y)
    assert len(studio.session.y) == len(before), "the length is kept"


def test_a_measured_rooms_travel_time_does_not_delay_the_audio(studio, tmp_path):
    """A response that is nothing but distance -- silence, then one impulse --
    is no room at all once it starts at the direct sound. Left with its
    travel time, it shifts the clip late by exactly that much."""
    delay = 160
    path = tmp_path / "distance.wav"
    impulse = np.zeros(delay + 1, dtype=np.float32)
    impulse[delay] = 1.0
    kudio.save_wave(path, impulse, 16000, subtype="FLOAT")

    studio.call("effects.load_ir", [str(path)])
    panel = studio.panel("effects")
    y = studio.session.y
    assert np.allclose(panel._render("measured_room", {})[1], y, atol=1e-6)

    late = panel._render("measured_room", {"align": False})[1]
    assert np.allclose(late[:delay], 0.0, atol=1e-6)
    assert np.allclose(late[delay:], y[:-delay], atol=1e-6)


def test_the_measured_reverberation_is_of_the_response_applied(studio, tmp_path):
    path = measured_room(tmp_path / "hall.wav")
    room = studio.call("effects.load_ir", [str(path)])["ir"]
    ir = kudio.file_load(path, sr=16000)[0]
    onset = int(np.argmax(np.abs(ir)))

    applied = kudio.rt60(ir[onset:], 16000)
    assert applied.t30 == pytest.approx(0.6, rel=0.08)   # the room it was built as
    assert str(applied) in room["measured"]
    assert "direct sound at 12.0 ms" in room["measured"] and "16000 Hz" in room["measured"]
    assert room["doubtful"] is False

    unaligned = studio.call("effects.measure_ir", [False])["ir"]["measured"]
    assert str(kudio.rt60(ir, 16000)) in unaligned


def test_a_file_that_is_not_a_room_is_measured_and_flagged(studio, recording):
    """Speech has a decay curve too; what it does not have is an EDT and a
    T30 that agree. The reading is flagged, and the page shows a flagged one
    in the warning colour so it is not read past."""
    room = studio.call("effects.load_ir", [str(recording)])["ir"]
    assert " but " in room["measured"]
    assert room["doubtful"] is True


def test_the_response_is_read_again_when_the_clip_changes_rate(studio, tmp_path):
    path = measured_room(tmp_path / "hall.wav")
    studio.call("effects.load_ir", [str(path)])

    studio.session.set_audio(kudio.resample(studio.session.y, 16000, 8000), 8000)
    held = studio.panel("effects")._response
    assert held.sr == 8000
    assert np.array_equal(held.samples, kudio.file_load(path, sr=8000)[0])
    assert studio.call("effects.call_text", ["measured_room", {}]).endswith(
        f"[0][{int(0.012 * 8000)}:])")
    assert "8000 Hz" in studio.call("effects.measure_ir")["ir"]["measured"]


def test_a_response_picked_with_nothing_open_is_read_again_for_the_clip(
        blank, tmp_path, tone):
    """Picked first, it is read and measured at its own rate. The clip that
    arrives later sets the rate the recorded call reads it at, and what is
    held from then on is that read."""
    path = measured_room(tmp_path / "hall.wav", sr=48000)
    room = blank.call("effects.load_ir", [str(path)])["ir"]
    panel = blank.panel("effects")
    assert panel._response.sr == 48000 and "48000 Hz" in room["measured"]

    blank.call("open_files", [[str(tone)]])
    assert panel._response.sr == 16000
    assert np.array_equal(panel._response.samples, kudio.file_load(path, sr=16000)[0])


def test_a_response_that_cannot_be_used_leaves_the_loaded_one(studio, tmp_path):
    said = _said(studio)
    studio.call("effects.load_ir", [str(measured_room(tmp_path / "hall.wav"))])

    info = studio.call("effects.load_ir", [str(tmp_path / "missing.wav")])
    assert "Could not read missing.wav" in said()[-1]
    assert info["ir"]["name"] == "hall.wav"
    silent = tmp_path / "silent.wav"
    kudio.save_wave(silent, np.zeros(4000, dtype=np.float32), 16000)
    info = studio.call("effects.load_ir", [str(silent)])
    assert "silent" in said()[-1]
    assert info["ir"]["name"] == "hall.wav"
    assert "'hall.wav'" in studio.call("effects.call_text", ["measured_room", {}])


def test_without_a_response_the_measured_room_reports_instead_of_raising(studio):
    assert "file_load('…'" in studio.call("effects.call_text", ["measured_room", {}])
    said = _said(studio)
    studio.call("effects.apply", ["measured_room", {}])
    last = said()[-1]
    assert "failed" in last and "no impulse response" in last
    assert studio.session.can_undo() is False


# --------------------------------------------------- synthesis with rooms

@pytest.mark.parametrize("text, expected", [
    ("", None),
    ("0.3", [0.3]),
    ("0.3, 0.6, 0.9", [0.3, 0.6, 0.9]),
    ("0.4;0.8", [0.4, 0.8]),
])
def test_parse_rt60(text, expected):
    """Empty means something here — no rooms at all, which is the default and
    has to stay reachable."""
    assert parse_rt60(text) == expected


@pytest.mark.parametrize("text", ["0", "-0.5", "0.3, 0"])
def test_parse_rt60_refuses_an_impossible_room(text):
    with pytest.raises(ValueError, match="positive"):
        parse_rt60(text)


def _folders(root, level=0.4, hz=180, noise_level=0.05, noise_seconds=1):
    """One clean tone and one noise file, each in a folder of its own."""
    clean, noise = root / "clean", root / "noise"
    clean.mkdir()
    noise.mkdir()
    t = np.arange(SR) / SR
    kudio.save_wave(clean / "a.wav",
                    (level * np.sin(2 * np.pi * hz * t)).astype(np.float32), SR)
    kudio.save_wave(noise / "n.wav",
                    (noise_level * np.random.default_rng(0)
                     .standard_normal(SR * noise_seconds)).astype(np.float32), SR)
    return str(clean), str(noise)


def test_synthesize_writes_a_manifest_with_the_rooms(blank, tmp_path):
    """The four-tuples cannot carry the room; the manifest can, and a training
    run needs to know what it was given."""
    clean, noise = _folders(tmp_path)
    out = tmp_path / "mixed"
    blank.call("synthesize.run", [clean, noise, str(out), "0"], {"rt60": "0.5"})
    assert blank.jobs.wait(60)
    assert blank.panel("synthesize")._result.produced

    manifest = kudio.load_manifest(out / "manifest.json")
    assert manifest and all(p.rt60 == 0.5 for p in manifest)
    assert all("rt500ms" in p.noisy for p in manifest)


def test_synthesize_records_the_link_and_the_target(blank, tmp_path):
    """Everything the four-tuples cannot carry, in one manifest: the room,
    the link the mixture travelled down, and where the wet target went."""
    clean, noise = _folders(tmp_path)
    out = tmp_path / "mixed"
    blank.call("synthesize.run", [clean, noise, str(out), "0"],
               {"rt60": "0.5", "channel": "telephone", "write_targets": True})
    assert blank.jobs.wait(60)

    result = blank.panel("synthesize")._result
    assert result.produced and result.link == "telephone"
    assert result.targets == len(result.produced)
    assert result.wanted == result.on_disk == len(result.produced)

    manifest = kudio.load_manifest(out / "manifest.json")
    assert manifest
    for pair in manifest:
        assert pair.rt60 == 0.5
        assert pair.channel == "telephone"
        assert Path(pair.target).is_file()
        # the link is constant for the run, so it is not in the names
        assert "telephone" not in Path(pair.noisy).name
        y, _ = kudio.file_load(pair.noisy, sr=None, mono=True)
        assert kudio.audio_report(y, SR).band_limited


# ------------------------------------------------------------ the channel

@pytest.mark.parametrize("name, fragment", [
    ("telephone", "kudio.telephone(y, sr, codec='mu_law')"),
    ("bit_depth", "kudio.bit_depth(y, bits=8"),
    ("dropouts", "kudio.dropouts(y, sr, loss=0.05"),
])
def test_the_channel_effects_name_the_call_they_make(session, name, fragment):
    call, operation = effects.operation(name, {})
    assert fragment in call

    out = operation(session.y, session.sr)
    assert len(out) == len(session.y)
    assert np.all(np.isfinite(out))


def test_the_phone_line_narrows_the_band(session):
    out = effects.operation("telephone", {})[1](session.y, session.sr)
    assert kudio.audio_report(out, session.sr).band_limited
    assert kudio.audio_report(out, session.sr).bandwidth_hz < 4200


def test_packet_loss_is_reproducible_from_its_seed(session):
    values = {"loss": 10.0, "seed": 4}
    first = effects.operation("dropouts", values)[1](session.y, session.sr)
    second = effects.operation("dropouts", values)[1](session.y, session.sr)
    assert np.array_equal(first, second)
    assert np.count_nonzero(first == 0.0) > 0

    held = effects.operation("dropouts", {**values, "conceal": "hold"})[1](
        session.y, session.sr)
    assert np.count_nonzero(held == 0.0) < np.count_nonzero(first == 0.0)


def test_the_burst_control_is_independent_at_zero_rather_than_a_run_of_none(session):
    """`burst=0` is not a run length kudio accepts, and `independent` is not
    `burst=1`. The control's special value at zero has to become None, not 0."""
    burst = next(p for p in effects.PARAMS["dropouts"] if p.name == "burst")
    assert burst.default == 0 and burst.special == "independent"
    assert "burst=None" in effects.operation("dropouts", {})[0]

    call, operation = effects.operation("dropouts", {"burst": 6})
    assert "burst=6" in call
    out = operation(session.y, session.sr)
    assert len(out) == len(session.y) and np.all(np.isfinite(out))


def test_a_burst_leaves_one_long_hole(session):
    """The same number of packets, arranged so the longest gap is far
    longer — which is what makes burst loss hard to conceal."""
    def longest_hole(y):
        longest = run = 0
        for value in y == 0.0:
            run = run + 1 if value else 0
            longest = max(longest, run)
        return longest

    # 20 seconds, not the fixture's one: 50 packets lose five at 10%, which
    # is one or two runs and far too few to say anything about their length
    long = np.tile(session.y, 20)

    values = {"loss": 10.0, "seed": 2}
    scattered = effects.operation("dropouts", values)[1](long, session.sr)
    bursty = effects.operation("dropouts", {**values, "burst": 8})[1](long, session.sr)

    # the same number of packets either way, to within the short last one
    packet = int(0.02 * session.sr)
    assert abs(np.count_nonzero(scattered == 0.0)
               - np.count_nonzero(bursty == 0.0)) < packet
    assert longest_hole(bursty) > 2 * longest_hole(scattered)


# --------------------------------------------- the clipping kudio reports

def test_synthesize_can_choose_not_to_clip(blank, tmp_path):
    """kudio clips a mixture at full scale and says so. FLOAT is the way not
    to, and the call names the format only when it is not the default."""
    assert synth_tools.SUBTYPES == ("PCM_16", "PCM_24", "FLOAT")
    assert blank.call("synthesize.describe")["subtypes"] == ["PCM_16", "PCM_24", "FLOAT"]
    clean, noise = _folders(tmp_path, level=0.2)

    def call(subtype):
        return blank.call("synthesize.call_text",
                          [clean, noise, "", "-5, 0, 5", "", 0.0, subtype, "none", False,
                           "regular", None])

    assert "subtype='FLOAT'" in call("FLOAT")
    assert "subtype=" not in call("PCM_16"), "the default stays out of the call"


def test_clipping_reaches_the_user_instead_of_stderr(blank, tmp_path):
    """Nothing in the window routes warnings anywhere, so the run has to
    catch them on its own thread, or kudio's warning only reaches stderr."""
    # a loud clean file at a low SNR is the case that clips
    clean, noise = _folders(tmp_path, level=0.9, hz=200, noise_level=0.3, noise_seconds=2)
    said = _said(blank)
    blank.call("synthesize.run", [clean, noise, str(tmp_path / "mix"), "-10"],
               {"mode": "regular"})
    assert blank.jobs.wait(60)

    result = blank.panel("synthesize")._result
    assert result.clipped, "kudio clipped and the run did not notice"
    assert any("past full scale" in n for n in result.notices)
    assert "clipped" in blank.call("synthesize.describe")["result"]
    last = said()[-1]
    assert "went past full scale" in last
    assert "subtype='FLOAT'" in last, "and says how to avoid it"


def test_float_keeps_what_pcm16_would_have_clipped(blank, tmp_path):
    clean, noise = _folders(tmp_path, level=0.9, hz=200, noise_level=0.3, noise_seconds=2)
    out = tmp_path / "wide"
    blank.call("synthesize.run", [clean, noise, str(out), "-10"],
               {"mode": "regular", "subtype": "FLOAT"})
    assert blank.jobs.wait(60)

    assert not blank.panel("synthesize")._result.clipped, "FLOAT should keep values past 1.0"
    y, _ = kudio.file_load(sorted(out.glob("*.wav"))[0], sr=None)
    assert float(np.max(np.abs(y))) > 1.0


# --------------------------------------------- the PESQ scale, and why

def test_metrics_names_which_pesq_scale_it_shows(studio, monkeypatch):
    """Raw P.862 and MOS-LQO are different scales -- raw 2.03 is about 1.65
    MOS-LQO -- so a bare 'PESQ' row does not say what the number is."""
    asked = []

    def scored(sr, ref, deg, pesq_scale="lqo"):
        asked.append(pesq_scale)
        return (2.409, 0.9, 12.0)

    monkeypatch.setattr(kudio, "eval_metrics", scored)
    labels = studio.call("metrics.describe")["pesq_scales"]
    assert labels == ["MOS-LQO", "raw P.862"]
    assert [metrics.pesq_scale(label) for label in labels] == ["lqo", "raw"]

    def evaluate(label):
        # the page sends the scale's label, the first one until another is chosen
        studio.call("metrics.evaluate", ["Original (before edits)", "", True, label])
        assert studio.jobs.wait(30)
        return studio.call("metrics.describe")["rows"]

    rows = evaluate(labels[0])
    assert asked == ["lqo"]
    assert rows[-3] == ["PESQ (MOS-LQO)", "2.409"]

    rows = evaluate(labels[1])
    assert asked == ["lqo", "raw"]
    assert [label for label, _ in rows[-3:]] == ["PESQ (raw P.862)", "STOI", "SDR"]


def test_pesq_install_advice_names_the_extra_that_installs_it(studio):
    """`kudio[eval]` does not carry PESQ, which builds from source and has an
    extra of its own, so one hint for all three back-ends would send people
    to an extra that installs nothing new."""
    assert "kudio[pesq]" in metrics.INSTALL_HINT["PESQ"]
    assert "compiler" in metrics.INSTALL_HINT["PESQ"]
    assert metrics.INSTALL_HINT["STOI"] == 'pip install "kudio[eval]"'
    assert metrics.INSTALL_HINT["SDR"] == 'pip install "kudio[eval]"'

    text = metrics.availability({"PESQ": False, "STOI": True, "SDR": True})
    assert "PESQ unavailable" in text
    assert "kudio[pesq]" in text and "kudio[eval]" not in text.split("·")[0]
    # what the panel shows is the real state, read when it is drawn
    assert studio.call("metrics.describe")["availability"] == \
        metrics.availability(metrics.installed())


@pytest.mark.parametrize("installed, sr, expected", [
    (False, 16000, "not installed"),
    (True, 16000, "could not be computed"),
    (True, 44100, "not defined at 44100 Hz"),
])
def test_none_means_two_different_things(blank, tmp_path, monkeypatch, installed, sr,
                                         expected):
    """An absent back-end returns None, and so does an installed one that
    cannot run on this pair. Calling both 'not installed' would tell people
    to install what they have."""
    path = tmp_path / f"t{sr}.wav"
    kudio.save_wave(path, (0.3 * np.sin(2 * np.pi * 200 * np.arange(sr) / sr)
                           ).astype(np.float32), sr)
    blank.call("open_files", [[str(path)]])
    monkeypatch.setattr(metrics, "installed",
                        lambda: {"PESQ": installed, "STOI": True, "SDR": True})
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, 0.9, 12.0))

    blank.call("metrics.evaluate", ["Original (before edits)"])
    assert blank.jobs.wait(30)
    rows = dict(map(tuple, blank.call("metrics.describe")["rows"]))
    assert expected in rows["PESQ (MOS-LQO)"]


def test_the_caption_is_asked_for_again_when_the_clip_changes(studio):
    """A new rate caps a cutoff under a new Nyquist frequency and reads a
    loaded response again, and either changes the call: the page asks for
    it afresh rather than keep showing the old one."""
    info = studio.call("effects.describe")
    at_16k = studio.call("state")
    studio.session.set_audio(kudio.resample(studio.session.y, 16000, 8000), 8000)
    at_8k = studio.call("state")
    asked = _page("js/panels/effects.js", info, """
hooks.state(data.states[0]);
await settle();
const before = sent("call_text").length;
hooks.state(data.states[1]);
await settle();
const afterRate = sent("call_text").length;
hooks.event({ info: data.info });
await settle();
console.log(JSON.stringify([afterRate - before, sent("call_text").length - afterRate]));
""", memory={"effect": "lowpass"}, data={"states": [at_16k, at_8k], "info": info})
    assert asked == [1, 1]
