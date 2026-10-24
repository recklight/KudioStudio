# -*- coding: utf-8 -*-
"""Compare: every enhancement method over one clip, ranked, auditioned and
applied; and scoring against a reference that does not start at the same
sample."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import compare as tools
from kudiostudio.tools import metrics
from kudiostudio.tools.enhance import LearnedModel
from kudiostudio.web.panels import compare as compare_panel
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio

SR = 16000


def column(name: str) -> int:
    """Column index by name, so adding a column breaks no test."""
    return tools.COLUMNS.index(name)


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path, recording, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    s.call("open_files", [[str(recording)]])
    yield s
    s.jobs.wait(60)
    s.shutdown()


def listen(studio):
    """The status-bar lines said from now on, as a function that drains them."""
    q = studio.events.subscribe()

    def drain():
        said = []
        while not q.empty():
            event = q.get_nowait()
            if event["type"] == "message":
                said.append(event["text"])
        return said

    return drain


def run(studio, **kwargs):
    """Run a comparison -- no reference, gain rules swept, unless told
    otherwise -- and wait for it to land."""
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE, **kwargs})
    assert studio.jobs.wait(120)
    return studio.call("compare.describe")


def results(studio):
    """The results behind the rows, audio and all."""
    return studio.panel("compare")._results


def by_key(studio, key):
    return next(r for r in results(studio) if [r.method, r.noise] == list(key))


def hint(studio, sweep=tools.SWEEP_METHODS, noise="mcra", run_dir=""):
    """The call the page shows under the controls."""
    return studio.call("compare.call_text", [sweep, tools.DEFAULT_METHOD, noise,
                                             tools.REF_NONE, False, run_dir])


# ------------------------------------------------------------------ the page
#
# Which button is live, what it says and the order the rows are listed in are
# the page's to decide, in static/js/panels/compare.js. node runs that module
# here against a describe() answer from a real comparison, with just enough
# of a DOM for it and ui.js to build into and be clicked. What it asks the
# studio for is recorded, so a test can send exactly that to the studio.

PAGE = r"""
class Node {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
    this.classes = new Set();
    Object.assign(this, { textContent: "", innerHTML: "", title: "", hidden: false,
                          disabled: false, value: "", checked: false });
  }
  get className() { return [...this.classes].join(" "); }
  set className(v) { this.classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const c = this.classes;
    return {
      add: (...names) => names.forEach((n) => c.add(n)),
      remove: (...names) => names.forEach((n) => c.delete(n)),
      contains: (n) => c.has(n),
      toggle: (n, force) => {
        const on = force === undefined ? !c.has(n) : Boolean(force);
        if (on) c.add(n); else c.delete(n);
        return on;
      },
    };
  }
  append(...nodes) { this.children.push(...nodes.filter((n) => n instanceof Node)); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener() {}
  remove() {}
  focus() {}
  querySelector(tag) { return this.all().find((n) => n.tagName === tag.toUpperCase()) ?? null; }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  dispatch(type, extra = {}) {
    if (type === "click" && this.disabled) return;      // a browser does the same
    for (const fn of this.listeners[type] ?? []) {
      fn({ type, target: this, shiftKey: false, ctrlKey: false, metaKey: false,
           preventDefault() {}, stopPropagation() {}, ...extra });
    }
  }
}

globalThis.document = {
  createElement: (tag) => new Node(tag),
  querySelector: () => ({ content: "" }),
  addEventListener() {}, removeEventListener() {},
  body: new Node("body"),
};
globalThis.Option = class extends Node {
  constructor(text = "", value = "") { super("option"); this.textContent = text; this.value = value; }
};

const INFO = __INFO__;
const ACTIONS = __ACTIONS__;
const ui = await import(__UI__);
const { t } = await import(__I18N__);
const { mount } = await import(__PANEL__);

const calls = [];
const answers = { call_text: "", audition: { on: true } };
const ctx = {
  id: "compare", memory: {}, t, ui,
  dialogs: { choosePath: async () => null },
  call: async (name, args = []) => {
    if (name !== "describe" && name !== "call_text") calls.push([name, args]);
    return name in answers ? answers[name] : INFO;
  },
  window: async (name, args = []) => { calls.push([name, args]); },
  view: { show() {}, hide() {}, open: false },
  status() {},
  state: () => null,
};
const root = new Node("div");
const api = await mount(root, ctx);
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
await settle();

const nodes = () => root.all();
const buttons = () => nodes().filter((n) => n.tagName === "BUTTON");
const audition = buttons().find((b) => b.classList.contains("toggle-btn"));
const apply = buttons().find((b) => b.textContent === t("Apply this one"));
const save = buttons().find((b) => b.textContent === t("Save all…"));
const run = buttons().find((b) => b.classList.contains("primary"));
const [thead, tbody] = nodes().find((n) => n.tagName === "TABLE").children;
const headers = () => thead.children[0].children;

function control(label) {
  for (const form of nodes().filter((n) => n.classList.contains("form"))) {
    const kids = form.children;
    for (let i = 0; i + 1 < kids.length; i++) {
      if (kids[i].tagName === "LABEL" && kids[i].textContent === t(label)) {
        return kids[i + 1].children[0];
      }
    }
  }
  throw new Error(`nothing labelled ${label}`);
}

const file = control("File");
const look = () => ({
  rows: tbody.children.map((tr) => tr.children.map((td) => td.textContent)),
  selected: tbody.children.map((tr) => tr.classList.contains("selected")),
  hidden: headers().map((th) => th.hidden),
  audition: { enabled: !audition.disabled, text: audition.textContent, on: audition.on },
  apply: { enabled: !apply.disabled, tip: apply.title },
  save: !save.disabled,
  file: !file.querySelector("input").disabled,
  browse: !file.button.disabled,
  method: !control("Gain rule").disabled,
  noise: !control("Noise estimate").disabled,
  calls: [...calls],
});

const seen = [look()];
for (const [verb, arg] of ACTIONS) {
  if (verb === "click" || verb === "shift" || verb === "ctrl") {
    tbody.children[arg].dispatch("click", { shiftKey: verb === "shift", ctrlKey: verb === "ctrl" });
  } else if (verb === "header") {
    headers()[arg].dispatch("click");
  } else if (verb === "press") {
    ({ audition, apply, save, run })[arg].dispatch("click");
  } else if (verb === "choose") {
    const box = control(arg[0]);
    box.value = arg[1];
    box.dispatch("change");
  } else if (verb === "show") {
    api.event({ info: arg });
  } else {
    throw new Error(`no such action: ${verb}`);
  }
  await settle();
  seen.push(look());
}
console.log(JSON.stringify(seen));
"""


def page(info, *actions) -> list:
    """What the Compare page shows for *info*, a ``compare.describe`` answer,
    when it opens and after each of *actions*:

    ("click" | "ctrl" | "shift", row) clicks the row the page lists at that
    place, with that key held; ("header", column) clicks a column's header;
    ("press", "audition" | "apply" | "save" | "run") a button;
    ("choose", (label, value)) picks a value in the form; ("show", info)
    hands the page a new describe() answer, as a panel event does.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = STATIC / "js"
    source = (PAGE.replace("__INFO__", json.dumps(info))
              .replace("__ACTIONS__", json.dumps([list(a) for a in actions]))
              .replace("__UI__", json.dumps((js / "ui.js").as_uri()))
              .replace("__I18N__", json.dumps((js / "i18n.js").as_uri()))
              .replace("__PANEL__", json.dumps((js / "panels" / "compare.js").as_uri())))
    done = subprocess.run([node, "--input-type=module"], input=source,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def shown_keys(info, view) -> list:
    """The (method, noise) pair of each row, top to bottom as the page lists them."""
    named = {tuple(row["cells"][:2]): row["key"] for row in info["rows"]}
    return [named[tuple(cells[:2])] for cells in view["rows"]]


def asked(view, name: str) -> list:
    """The arguments of every *name* call the page has made, oldest first."""
    return [args for called, args in view["calls"] if called == name]


# ------------------------------------------------------------- the comparison

def test_a_comparison_fills_a_row_per_method(studio):
    info = run(studio)
    assert len(info["rows"]) == len(kudio.METHODS)
    assert len(results(studio)) == len(kudio.METHODS)
    assert all(r.error is None for r in results(studio))


def test_without_a_reference_the_quality_columns_are_hidden(studio):
    info = studio.call("compare.describe")
    assert info["references"][0] == tools.REF_NONE           # what the page starts on
    info = run(studio)
    assert info["has_reference"] is False
    assert all(page(info)[0]["hidden"][c] for c in tools.REFERENCE_COLUMNS)
    assert all(row["sort"][c] is None
               for row in info["rows"] for c in tools.REFERENCE_COLUMNS)
    assert all(r.si_sdr_db is None for r in results(studio))


def test_without_a_reference_the_verdict_refuses_to_call_a_winner(studio):
    """A low floor is what you also get by deleting the speech."""
    text = run(studio)["verdict"]["text"]
    assert "no quality score" in text
    assert "Audition" in text


def make_it_a_real_experiment(session, amplitude=0.15):
    """Clean signal as the baseline, noisy version as the working audio.

    This is the only arrangement in which "the original" is a *clean*
    reference. Denoising first and then comparing against the original does
    the opposite — see the not-clean test below.

    The noise has to be *heavy* (this lands near 0 dB SNR). Statistical
    enhancement is for low-SNR audio: above roughly 10 dB the speech distortion
    it introduces costs more than the noise it removes, and every method
    measures worse than doing nothing. That is the algorithms behaving
    correctly, so a fixture at 10 dB would be testing the wrong thing.
    """
    clean = np.array(session.y, copy=True)
    session.set_audio(clean, session.sr, name="clean")       # new baseline
    noise = amplitude * np.random.default_rng(3).standard_normal(len(clean))
    session.set_audio((clean + noise).astype(np.float32), session.sr,
                      undo_label="add noise", keep_original=True)


def test_with_a_clean_reference_the_quality_columns_appear(studio):
    make_it_a_real_experiment(studio.session)
    info = run(studio, reference=tools.REF_ORIGINAL)

    hidden = page(info)[0]["hidden"]
    assert not any(hidden[c] for c in tools.REFERENCE_COLUMNS[:2])     # SI-SDR and ΔSNR
    for name in ("SI-SDR", "ΔSNR"):
        assert all(row["sort"][column(name)] is not None for row in info["rows"])
    assert all(r.si_sdr_db is not None for r in results(studio))
    assert "Best against the reference" in info["verdict"]["text"]


def test_the_winner_is_the_best_si_sdr(studio):
    make_it_a_real_experiment(studio.session)
    info = run(studio, reference=tools.REF_ORIGINAL)

    best = max(results(studio), key=lambda r: r.si_sdr_db)
    assert kudio.METHODS[best.method].label in info["verdict"]["text"]
    assert best.delta_snr_db > 0


def test_a_reference_file_that_was_never_chosen_is_reported(studio):
    drain = listen(studio)
    studio.call("compare.run", kwargs={"reference": tools.REF_FILE})
    assert "Select a reference file" in drain()[-1]


def test_the_reference_file_row_is_only_live_for_that_choice(studio):
    views = page(studio.call("compare.describe"),
                 ("choose", ("Reference", tools.REF_FILE)),
                 ("choose", ("Reference", tools.REF_NONE)))
    assert [view["browse"] for view in views] == [False, True, False]
    assert [view["file"] for view in views] == [False, True, False]


# ------------------------------------------------------------------ the table

def test_numbers_sort_as_numbers():
    """Sorted as text, "10.2" lands below "9.66" — in a ranking table."""
    a, b = tools.as_number("9.66"), tools.as_number("10.2")
    assert (a < b) is True
    assert (b < a) is False
    assert ("10.2" < "9.66") is True             # what sorting the text would do


def test_a_column_of_numbers_actually_sorts(studio):
    ms = column("ms")
    rows = page(run(studio), ("header", ms))[-1]["rows"]
    values = [float(cells[ms]) for cells in rows]
    assert values == sorted(values)


def test_without_a_reference_the_table_keeps_kudios_order(studio):
    """Alphabetical by label is not an order anybody asked for."""
    info = run(studio)
    shown = shown_keys(info, page(info)[0])
    assert [key[0] for key in shown] == list(kudio.METHODS)


def test_with_a_reference_the_table_opens_ranked(studio):
    make_it_a_real_experiment(studio.session)
    info = run(studio, reference=tools.REF_ORIGINAL)
    view = page(info)[0]

    scores = [float(cells[column("SI-SDR")]) for cells in view["rows"]]
    assert scores == sorted(scores, reverse=True)
    best = max(results(studio), key=lambda r: r.si_sdr_db)
    assert shown_keys(info, view)[0] == [best.method, best.noise]


def test_selecting_a_row_enables_audition_and_apply(studio):
    before, after = page(run(studio), ("click", 0))
    assert before["audition"]["enabled"] is False
    assert after["audition"]["enabled"] is True
    assert after["apply"]["enabled"] is True


def test_auditioning_offers_the_audio_without_committing(studio):
    info = run(studio)
    view = page(info, ("click", 0), ("press", "audition"))[-1]
    before = np.array(studio.session.y, copy=True)

    assert studio.call("compare.audition", asked(view, "audition")[-1]) == {"on": True}
    assert studio.previewing("compare")
    top = shown_keys(info, view)[0]
    assert np.array_equal(studio._preview[0], by_key(studio, top).audio)
    assert np.array_equal(studio.session.y, before)
    assert studio.session.can_undo() is False


def test_switching_rows_while_auditioning_follows_the_selection(studio):
    info = run(studio)
    view = page(info, ("click", 0), ("press", "audition"), ("click", 1))[-1]
    first, second = shown_keys(info, view)[:2]
    assert asked(view, "audition") == [[[first]], [[second]]]

    for args in asked(view, "audition"):
        studio.call("compare.audition", args)
    assert studio.previewing("compare")
    assert np.array_equal(studio._preview[0], by_key(studio, second).audio)


def test_apply_commits_the_selected_result(studio):
    info = run(studio)
    view = page(info, ("click", 0), ("press", "apply"))[-1]
    chosen = by_key(studio, shown_keys(info, view)[0])

    studio.call("compare.apply", asked(view, "apply")[-1])
    assert np.allclose(studio.session.y, chosen.audio, atol=1e-6)
    assert studio.session.can_undo() is True
    assert studio.session.can_compare() is True       # A/B is now meaningful


def test_apply_releases_the_audition(studio):
    view = page(run(studio), ("click", 0), ("press", "audition"), ("press", "apply"))[-1]
    assert view["audition"]["on"] is False

    studio.call("compare.audition", asked(view, "audition")[-1])
    q = studio.events.subscribe()
    studio.call("compare.apply", asked(view, "apply")[-1])
    assert studio.previewing("compare") is False
    # what lets the page's Audition button back up when the preview ends
    # some other way
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert {"type": "preview", "owner": "compare", "on": False} in events


def test_new_audio_clears_a_stale_comparison(studio):
    """The rows describe audio that is no longer loaded."""
    info = run(studio)
    assert len(info["rows"]) > 0
    studio.session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="new")
    cleared = studio.call("compare.describe")
    assert cleared["rows"] == []
    assert results(studio) == []

    shown, emptied = page(info, ("show", cleared))
    assert shown["save"] is True
    assert emptied["save"] is False


def test_save_all_names_files_by_both_choices(studio, tmp_path):
    """Two rows can share a method; naming by method alone overwrites one."""
    run(studio, sweep=tools.SWEEP_NOISES)
    out = tmp_path / "results"
    out.mkdir()
    studio.call("compare.save_all", [str(out)])
    written = sorted(p.stem.split("_", 1)[1] for p in out.glob("*.wav"))
    assert written == sorted(f"{tools.DEFAULT_METHOD}_{n}"
                             for n in kudio.NOISE_ESTIMATORS)


def test_comparing_only_the_selection(studio):
    studio.call("set_selection", [0.2, 1.2])
    run(studio, selection_only=True)
    expected = int(1.0 * studio.session.sr)
    assert len(results(studio)) == len(kudio.METHODS)
    assert all(abs(len(r.audio) - expected) <= 2 for r in results(studio))


def test_a_selection_too_short_to_frame_is_refused(studio):
    studio.call("set_selection", [0.0, 0.01])           # 160 samples
    drain = listen(studio)
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE, "selection_only": True})
    assert "Too short" in drain()[-1]


def test_the_call_hint_names_what_it_will_run(studio):
    assert "kudio.compare_enhancers" in hint(studio)
    assert "noise='quantile'" in hint(studio, noise="quantile")


def break_wiener(monkeypatch) -> None:
    """Make the Wiener row fail, the way a method that raises leaves a row."""
    import kudio.enhance.spectral as module

    real = module.spectral_enhance

    def explode(y, sr, method='logmmse', **kwargs):
        if method == 'wiener':
            raise RuntimeError("boom")
        return real(y, sr, method, **kwargs)

    monkeypatch.setattr(kudio, "compare_enhancers",
                        lambda *a, **k: module.compare_enhancers(*a, **k))
    monkeypatch.setattr(module, "spectral_enhance", explode)


def test_a_failing_method_is_shown_as_a_row_not_an_exception(studio, monkeypatch):
    break_wiener(monkeypatch)
    info = run(studio)

    assert [r.method for r in results(studio) if r.error] == ["wiener"]
    assert [row["key"][0] for row in info["rows"] if row["error"]] == ["wiener"]
    assert len(info["rows"]) == len(kudio.METHODS)


def test_a_reference_that_is_not_clean_is_called_out(studio):
    """Comparing a denoised clip against the noisy original: every method
    scores worse than doing nothing, and the panel must say why rather than
    announcing a 'winner' that is -1 dB better than leaving it alone."""
    session = studio.session
    session.set_audio(kudio.spectral_enhance(session.y, session.sr, 'logmmse'),
                      session.sr, undo_label="logmmse", keep_original=True)
    info = run(studio, reference=tools.REF_ORIGINAL)

    assert all(r.delta_snr_db < 0 for r in results(studio) if r.error is None)
    text = info["verdict"]["text"]
    assert "No method beat leaving it alone" in text
    assert "not clean" in text


# ------------------------------------------------------------- sweeping axes

def live(sweep: str) -> dict:
    """Which choice a sweep listens to: the one whose value changes what runs.
    Holding a varied axis at one value is a contradiction, so the page must
    grey out the other picker and only that one."""
    base = tools.sweep_arguments(sweep, "logmmse", "mcra", None)
    return {"method": tools.sweep_arguments(sweep, "wiener", "mcra", None) != base,
            "noise": tools.sweep_arguments(sweep, "logmmse", "quantile", None) != base}


def pickers(view) -> dict:
    """Which of the two pickers the page leaves live."""
    return {"method": view["method"], "noise": view["noise"]}


def test_the_default_sweep_is_gain_rules(studio):
    info = studio.call("compare.describe")
    assert info["sweeps"][0] == tools.SWEEP_METHODS
    # the estimator is held fixed, so it is a choice; the rule is varied, so it is not
    assert pickers(page(info)[0]) == {"method": False, "noise": True} \
        == live(tools.SWEEP_METHODS)


def test_sweeping_estimators_flips_which_picker_is_live(studio):
    view = page(studio.call("compare.describe"),
                ("choose", ("Compare", tools.SWEEP_NOISES)))[-1]
    assert pickers(view) == {"method": True, "noise": False} == live(tools.SWEEP_NOISES)


def test_the_grid_sweep_fixes_neither(studio):
    view = page(studio.call("compare.describe"),
                ("choose", ("Compare", tools.SWEEP_GRID)))[-1]
    assert pickers(view) == {"method": False, "noise": False} == live(tools.SWEEP_GRID)


def test_sweeping_estimators_gives_a_row_per_estimator(studio):
    run(studio, sweep=tools.SWEEP_NOISES, method="logmmse")

    assert len(results(studio)) == len(kudio.NOISE_ESTIMATORS)
    assert {r.method for r in results(studio)} == {"logmmse"}
    assert {r.noise for r in results(studio)} == set(kudio.NOISE_ESTIMATORS)


def test_the_grid_sweep_runs_every_combination(studio):
    info = run(studio, sweep=tools.SWEEP_GRID)

    expected = len(kudio.METHODS) * len(kudio.NOISE_ESTIMATORS)
    assert len(results(studio)) == expected
    assert len(info["rows"]) == expected


def test_the_estimator_column_is_filled(studio):
    rows = run(studio, sweep=tools.SWEEP_NOISES)["rows"]
    shown = {row["cells"][column("noise")] for row in rows}
    assert shown == set(kudio.NOISE_ESTIMATORS)


def test_a_row_is_identified_by_both_choices(studio):
    """On the grid every method has a row per estimator, so the method alone
    names none of them."""
    rows = run(studio, sweep=tools.SWEEP_GRID)["rows"]
    keys = [tuple(row["key"]) for row in rows]
    assert len(set(keys)) == len(keys)


def test_the_grid_verdict_compares_the_two_axes(studio):
    """Which of the two decisions to spend effort on, answered per clip."""
    make_it_a_real_experiment(studio.session)
    info = run(studio, sweep=tools.SWEEP_GRID, reference=tools.REF_ORIGINAL)

    text = info["verdict"]["text"]
    assert "moved this clip" in text
    assert "noise estimator" in text and "gain rule" in text


def scored(scores: dict) -> list:
    return [kudio.EnhanceResult(
        method=m, noise=n, audio=np.zeros(0, dtype=np.float32),
        seconds=0.0, lufs=0.0, noise_floor_db=0.0, si_sdr_db=v)
        for (m, n), v in scores.items()]


def test_two_close_axes_are_called_a_tie():
    """Crowning an axis on a 0.1 dB difference is inventing a finding."""
    tied = scored({("a", "x"): 0.0, ("a", "y"): 4.0,
                   ("b", "x"): 4.0, ("b", "y"): 8.0})
    assert "about equally" in tools.axis_spread(tied)

    lopsided = scored({("a", "x"): 0.0, ("a", "y"): 10.0,
                       ("b", "x"): 0.1, ("b", "y"): 10.1})
    assert "noise estimator moved this clip more" in tools.axis_spread(lopsided)


def test_a_custom_row_is_left_out_of_the_axis_comparison():
    """It sits on neither axis, so it cannot speak to which one matters."""
    grid = scored({("a", "x"): 0.0, ("a", "y"): 4.0,
                   ("b", "x"): 4.0, ("b", "y"): 8.0})
    with_custom = grid + [kudio.EnhanceResult(
        method="model", noise=kudio.CUSTOM_NOISE,
        audio=np.zeros(0, dtype=np.float32), seconds=0.0, lufs=0.0,
        noise_floor_db=0.0, si_sdr_db=999.0)]
    assert tools.axis_spread(with_custom) == tools.axis_spread(grid)


def test_a_single_axis_sweep_makes_no_claim_about_which_matters(studio):
    """With one estimator there is nothing to compare it against."""
    make_it_a_real_experiment(studio.session)
    text = run(studio, reference=tools.REF_ORIGINAL)["verdict"]["text"]
    assert "Best against the reference" in text
    assert "moved this clip" not in text


def test_the_call_hint_follows_the_sweep(studio):
    text = hint(studio, sweep=tools.SWEEP_NOISES)
    assert "noises=list(kudio.NOISE_ESTIMATORS)" in text
    assert repr([tools.DEFAULT_METHOD]) in text

    assert "noises=list(kudio.NOISE_ESTIMATORS)" in hint(studio, sweep=tools.SWEEP_GRID)


# ------------------------------------------------- a learned model in the table

def rank_as_learned(monkeypatch, name: str, enhance) -> None:
    """Put *enhance* in the table as the learned row called *name*, whatever
    run directory is given."""
    monkeypatch.setattr(tools, "learned_entry", lambda *a, **k: (name, enhance))


def test_no_run_directory_means_no_extra_row(studio):
    assert tools.learned_entry("", LearnedModel(), lambda: True) is None
    run(studio)
    assert len(results(studio)) == len(kudio.METHODS)


def test_a_run_directory_adds_one_row(studio, tmp_path, monkeypatch):
    """Ranked in the same table as the statistical methods, because that is
    the question — 'is the model actually better than log-MMSE'."""
    run_dir = tmp_path / "runs" / "exp1"
    run_dir.mkdir(parents=True)
    rank_as_learned(monkeypatch, "exp1", lambda y, sr: kudio.gain(y, -3.0))

    run(studio, run_dir=str(run_dir))

    assert len(results(studio)) == len(kudio.METHODS) + 1
    learned = [r for r in results(studio) if r.method == "exp1"]
    assert len(learned) == 1
    assert learned[0].noise == kudio.CUSTOM_NOISE


def test_a_learned_row_is_not_swept_over_estimators(studio, monkeypatch):
    rank_as_learned(monkeypatch, "exp1", lambda y, sr: y)
    run(studio, sweep=tools.SWEEP_GRID)

    assert len([r for r in results(studio) if r.method == "exp1"]) == 1
    assert len(results(studio)) == \
        len(kudio.METHODS) * len(kudio.NOISE_ESTIMATORS) + 1


def test_a_learned_row_shows_its_own_name_in_the_table(studio, monkeypatch):
    rank_as_learned(monkeypatch, "exp1", lambda y, sr: y)
    labels = {row["cells"][0] for row in run(studio)["rows"]}
    assert "exp1" in labels                    # not looked up in kudio.METHODS


def test_a_learned_row_can_win(studio, monkeypatch):
    make_it_a_real_experiment(studio.session)
    clean = np.array(studio.session.original()[0], copy=True)
    rank_as_learned(monkeypatch, "oracle", lambda y, sr: clean[:len(y)])
    info = run(studio, reference=tools.REF_ORIGINAL)

    best = max(results(studio), key=lambda r: r.si_sdr_db)
    assert best.method == "oracle"
    assert "oracle" in info["verdict"]["text"]


def test_a_missing_kudioenhance_is_reported_not_raised(studio, tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    monkeypatch.setattr(compare_panel, "kudioenhance_available", lambda: False)

    assert "pip install" in hint(studio, run_dir=str(run_dir))
    drain = listen(studio)
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE,
                                       "run_dir": str(run_dir)})
    assert "pip install" in drain()[-1]


# -------------------------------------------- the learned row's model, kept

#: long enough that a row charged for it cannot pass for a fast one
SLOW_SECONDS = 0.6


def install_fake_model(monkeypatch):
    """A model that is slow to load, as a real one is, and instant to run.

    It sits where ``from kudioenhance.inference import Enhancer`` finds it,
    so the panel's own loading code is what runs, with kudioenhance
    installed or not. Set ``first_pass`` to make its first run slow too.
    """
    class Enhancer:
        loads = []
        load_seconds = SLOW_SECONDS
        first_pass = 0.0
        refuse = None

        def __init__(self):
            self.cfg = SimpleNamespace(audio=SimpleNamespace(sr=16000))
            self.passes = 0

        @classmethod
        def load(cls, run_dir):
            cls.loads.append(str(run_dir))
            time.sleep(cls.load_seconds)
            if cls.refuse is not None:
                raise cls.refuse
            return cls()

        def enhance(self, y):
            self.passes += 1
            if self.passes == 1:
                time.sleep(self.first_pass)
            return 0.5 * np.asarray(y, dtype=np.float32)

    inference = types.ModuleType("kudioenhance.inference")
    inference.Enhancer = Enhancer
    monkeypatch.setitem(sys.modules, "kudioenhance.inference", inference)
    monkeypatch.setattr(compare_panel, "kudioenhance_available", lambda: True)
    return Enhancer


@pytest.fixture
def run_dir(tmp_path) -> Path:
    path = tmp_path / "runs" / "exp1"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def slow_model(monkeypatch):
    return install_fake_model(monkeypatch)


def learned_row(studio):
    rows = [r for r in results(studio) if r.noise == kudio.CUSTOM_NOISE]
    assert len(rows) == 1
    return rows[0]


def test_a_learned_rows_time_leaves_out_loading_the_model(studio, slow_model, run_dir):
    """The column ranks rows against each other; a load inside the row would
    be charged at seconds against milliseconds of denoising."""
    run(studio, run_dir=str(run_dir))

    row = learned_row(studio)
    assert row.error is None
    assert slow_model.loads == [str(run_dir)]
    assert row.seconds < SLOW_SECONDS / 2


def test_a_learned_rows_time_leaves_out_its_first_pass(studio, slow_model, run_dir):
    """A network's first pass pays one-time set-up, as kudio's own methods
    do before compare_enhancers warms them up."""
    slow_model.load_seconds = 0.0
    slow_model.first_pass = SLOW_SECONDS
    run(studio, run_dir=str(run_dir))

    row = learned_row(studio)
    assert row.error is None
    assert row.seconds < SLOW_SECONDS / 2


def test_the_model_is_loaded_once_per_run_directory(studio, slow_model, run_dir,
                                                    tmp_path):
    slow_model.load_seconds = 0.0
    first = str(run_dir)
    run(studio, run_dir=first)
    run(studio, run_dir=first)
    assert slow_model.loads == [first]

    other = tmp_path / "runs" / "exp2"
    other.mkdir()
    hint(studio, run_dir=str(other))          # the page asks whenever the directory changes
    assert studio.panel("compare").model.enhancer is None     # the old model is let go
    run(studio, run_dir=str(other))
    assert slow_model.loads == [first, str(other)]
    assert learned_row(studio).method == "exp2"


def test_a_model_kept_from_another_run_is_not_used(studio, slow_model, run_dir):
    """A directory changed while a comparison is still loading leaves the old
    run's model in the cache after the change has cleared it."""
    slow_model.load_seconds = 0.0
    model = studio.panel("compare").model
    model.enhancer, model.run_dir = slow_model(), "runs/old"
    run(studio, run_dir=str(run_dir))
    assert slow_model.loads == [str(run_dir)]
    assert model.run_dir == str(run_dir)


def test_a_model_that_will_not_load_fails_its_own_row(studio, slow_model, run_dir):
    """Loading ahead of the comparison must not change what a failure looks
    like: one row with its error, beside every row that worked."""
    slow_model.load_seconds = 0.0
    slow_model.refuse = FileNotFoundError("no model at runs/exp1/ddae.pt")
    drain = listen(studio)
    run(studio, run_dir=str(run_dir))

    assert "no model at" in learned_row(studio).error
    others = [r for r in results(studio) if r.noise != kudio.CUSTOM_NOISE]
    assert len(others) == len(kudio.METHODS)
    assert all(r.error is None for r in others)
    assert not any("Comparison failed" in m for m in drain())


def test_a_model_trained_again_in_the_same_directory_is_loaded_again(
        studio, slow_model, run_dir):
    """Kept by the directory's name alone, a run trained again in place would
    go on being ranked with the old weights, while Apply recorded a load of
    the new ones."""
    slow_model.load_seconds = 0.0
    for name in ("config.yaml", "stats.npz", "ddae.pt"):
        (run_dir / name).write_bytes(b"first")
    run(studio, run_dir=str(run_dir))
    run(studio, run_dir=str(run_dir))
    assert slow_model.loads == [str(run_dir)], "unchanged: kept"

    # kudioenhance writes a checkpoint by replacing it
    retrained = run_dir / "ddae.tmp"
    retrained.write_bytes(b"second, and longer")
    retrained.replace(run_dir / "ddae.pt")
    run(studio, run_dir=str(run_dir))
    assert slow_model.loads == [str(run_dir)] * 2


def hold_comparison(monkeypatch):
    """Make the next comparison wait for the returned event before running."""
    release = threading.Event()
    real = tools.compare_with_model

    def held(*args, **kwargs):
        release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, "compare_with_model", held)
    return release


def test_results_of_audio_edited_while_they_ran_are_dropped(studio, monkeypatch):
    """Kept, they would be ranked as if made from the edit, and Apply would
    record them as code that computes something else from it -- with no
    warning at export."""
    session = studio.session
    drain = listen(studio)
    release = hold_comparison(monkeypatch)
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE})
    session.replace_selection(kudio.gain(session.y, -12.0), undo_label="gain",
                              code="y = kudio.gain(y, db=-12.0)")
    release.set()
    assert studio.jobs.wait(120)

    assert studio.call("compare.describe")["rows"] == []
    assert results(studio) == []
    assert "audio changed while the comparison ran" in drain()[-1]
    assert [step.label for step in session.history] == ["gain"]
    assert session.edit_script_warnings() == []


def test_rows_on_screen_are_applied_as_the_run_that_made_them(studio, monkeypatch):
    """A second run started while the first one's rows are on screen must not
    lend them its range: a row computed from a selection would be recorded
    as whole-clip code, and the script would write twice the audio."""
    session = studio.session
    studio.call("set_selection", [0.5, 1.5])
    info = run(studio, selection_only=True)
    assert len(results(studio)[0].audio) == session.sr

    release = hold_comparison(monkeypatch)
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE,
                                       "selection_only": False})
    studio.call("compare.apply", [info["rows"][0]["key"], False])
    release.set()
    assert studio.jobs.wait(120)

    assert session.history[-1].code is None
    assert any("was not recorded as code" in w
               for w in session.edit_script_warnings())


#: skipped without kudioenhance and PyTorch -- see conftest.py
requires_enhance = pytest.mark.learned


@requires_enhance
def test_a_trained_model_is_ranked_and_kept_for_the_next_comparison(studio, trained_run):
    run_dir, sr = trained_run
    assert studio.session.sr == sr

    run(studio, run_dir=str(run_dir))
    row = learned_row(studio)
    assert row.error is None
    assert row.method == run_dir.name
    assert len(row.audio) == len(studio.session.y)
    assert np.isfinite(row.audio).all()
    model = studio.panel("compare").model
    loaded = model.enhancer
    assert loaded is not None
    assert model.run_dir == str(run_dir)

    run(studio, run_dir=str(run_dir))
    assert learned_row(studio).error is None
    assert model.enhancer is loaded             # not read from disk again


# --------------------------------------------------- auditioning two at once

def test_the_button_says_what_it_will_do(studio):
    info = run(studio)
    _, one, every = page(info, ("click", 0), ("shift", len(info["rows"]) - 1))
    assert one["audition"]["text"] == "♪  Audition"
    assert every["audition"]["text"] == "♪  Audition A→B"


def test_apply_needs_exactly_one_row(studio):
    """Two rows is a comparison, not a decision."""
    info = run(studio)
    _, one, every = page(info, ("click", 0), ("shift", len(info["rows"]) - 1))
    assert one["apply"]["enabled"] is True

    assert every["apply"]["enabled"] is False
    assert "one row" in every["apply"]["tip"]


def test_rows_are_heard_in_the_tables_order_not_the_click_order(studio):
    """The table is ranked; hearing them ranked is what makes "the one above
    sounds better" a sentence about the ranking."""
    info = run(studio)
    # the lower row clicked first
    view = page(info, ("click", 3), ("ctrl", 1), ("press", "audition"))[-1]
    listed = shown_keys(info, view)
    assert asked(view, "audition") == [[[listed[1], listed[3]]]]


def test_rows_play_in_the_order_they_are_asked_for(studio):
    """Not in the order the results came in: sorted the other way round, the
    table lists the fourth result above the second, and that is the order
    the page asks for them in."""
    rows = run(studio)["rows"]
    upper, lower = rows[3]["key"], rows[1]["key"]
    studio.call("compare.audition", [[upper, lower]])

    heard = studio._preview[0]
    first, second = by_key(studio, upper).audio, by_key(studio, lower).audio
    gap = int(tools.AB_GAP_SECONDS * studio.session.sr)
    assert np.array_equal(heard[:len(first)], first)
    assert np.array_equal(heard[len(first) + gap:], second)


def test_a_failed_row_is_not_auditioned(studio, monkeypatch):
    break_wiener(monkeypatch)
    info = run(studio)
    view = page(info, ("click", 0), ("shift", len(info["rows"]) - 1),
                ("press", "audition"))[-1]
    sent = asked(view, "audition")[-1][0]
    assert "wiener" not in [key[0] for key in sent]
    assert len(sent) == len(kudio.METHODS) - 1

    # nor by the studio, asked for every row
    studio.call("compare.audition", [[row["key"] for row in info["rows"]]])
    usable = [r for r in results(studio) if r.error is None]
    gap = int(tools.AB_GAP_SECONDS * studio.session.sr)
    expected = sum(len(r.audio) for r in usable) + gap * (len(usable) - 1)
    assert len(studio._preview[0]) == expected


def test_apply_takes_the_one_usable_row_when_a_failed_one_sits_above_it(
        studio, monkeypatch):
    """Apply is offered for one usable row, so that is the one applied; the
    failed row above it has no audio, and asking for it changes nothing."""
    break_wiener(monkeypatch)
    info = run(studio)
    listed = shown_keys(info, page(info)[0])
    failed = next(i for i, key in enumerate(listed) if key[0] == "wiener")
    below = listed[failed + 1]
    views = page(info, ("click", failed), ("ctrl", failed + 1), ("press", "apply"))
    assert views[2]["apply"]["enabled"] is True
    assert asked(views[-1], "apply") == [[below, False]]

    studio.call("compare.apply", [listed[failed]])
    assert studio.session.can_undo() is False

    chosen = by_key(studio, below)
    studio.call("compare.apply", asked(views[-1], "apply")[0])
    assert studio.session.can_undo() is True
    assert np.allclose(studio.session.y, chosen.audio, atol=1e-6)


# ------------------------------- scoring against a reference out of step
#
# Every metric in the table compares sample *i* with sample *i*. Nothing in
# the resulting number distinguishes "the processing was bad" from "these
# two are eight milliseconds apart", and those mean opposite things.

def voiced(seconds: float = 2.0, seed: int = 0) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float64) / SR
    voice = sum(np.sin(2 * np.pi * 180 * k * t) / k for k in range(1, 10)) / 6
    voice *= 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    noise = 0.01 * np.random.default_rng(seed).standard_normal(len(t))
    return (voice + noise).astype(np.float32)


@pytest.fixture
def instant_metrics(monkeypatch):
    """PESQ, STOI and SDR answer at once, with nothing: the rows checked here
    are the ones filled before them."""
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, None, None))


@pytest.fixture
def delayed(studio, instant_metrics, tmp_path) -> Path:
    """The window holding a delayed copy of a reference file on disk; the
    file's path."""
    clean = voiced()
    reference = tmp_path / "clean.wav"
    kudio.save_wave(reference, clean, SR)
    studio.session.set_audio(np.concatenate([np.zeros(128, dtype=np.float32), clean]),
                             SR, name="delayed")
    return reference


def score(studio, path="", align=True, reference=metrics.REFERENCES[1]) -> dict:
    """The table the Metrics panel shows for the current audio against
    *reference*, as {label: value}."""
    info = studio.call("metrics.evaluate", [reference, str(path), align])
    assert studio.jobs.wait(30)
    return dict(map(tuple, info["rows"]))


def value(rows: dict, name: str) -> float:
    return float(rows[name].split()[0])


def test_the_offset_is_measured_and_named(studio, delayed):
    rows = score(studio, delayed)
    assert "128 samples" in rows["Alignment"]
    assert "8.0 ms" in rows["Alignment"]


def test_lining_up_is_worth_tens_of_decibels(studio, delayed):
    """Same two signals, same metric; the only difference is whether anyone
    took the offset out first."""
    misaligned = value(score(studio, delayed, align=False), "SI-SDR")
    aligned = value(score(studio, delayed, align=True), "SI-SDR")

    assert aligned > misaligned + 20.0, (misaligned, aligned)
    assert aligned > 40.0, "a clip against itself should score very high"


def test_leaving_it_in_is_flagged_rather_than_silently_scored(studio, delayed):
    rows = score(studio, delayed, align=False)

    warning = next(k for k in rows if "Not aligned" in k)
    assert "8.0 ms" in rows[warning]
    assert "measuring it" in rows[warning]


def test_aligned_scoring_compares_the_overlap(studio, delayed):
    compared = int(score(studio, delayed)["Compared samples"].split()[0])
    assert compared == len(voiced())


def unrelated(tmp_path, seed: int) -> Path:
    """Two seconds of noise that has nothing to do with the take."""
    path = tmp_path / "other.wav"
    kudio.save_wave(path, (0.2 * np.random.default_rng(seed)
                           .standard_normal(SR * 2)).astype(np.float32), SR)
    return path


def test_an_unrelated_reference_says_so(studio, instant_metrics, tmp_path):
    other = unrelated(tmp_path, seed=9)
    studio.session.set_audio(voiced(), SR, name="take")
    drain = listen(studio)
    rows = score(studio, other)

    warning = next(k for k in rows if "Reference" in k and "⚠" in k)
    assert "unrelated" in rows[warning]
    assert any("does not look like" in m for m in drain())


def test_an_unrelated_reference_is_not_lined_up_anyway(studio, instant_metrics, tmp_path):
    """Aligning two unrelated signals produces a confident-looking number
    about nothing, which is worse than a bad one."""
    other = unrelated(tmp_path, seed=11)
    studio.session.set_audio(voiced(), SR, name="take")
    rows = score(studio, other)

    assert "Alignment" in rows
    compared = int(rows["Compared samples"].split()[0])
    assert compared == SR * 2, "nothing should have been trimmed"


def test_an_edit_that_keeps_the_timing_reports_no_offset(studio, instant_metrics):
    session = studio.session
    session.set_audio(voiced(), SR, name="take")
    session.set_audio(session.y * 0.5, session.sr, undo_label="gain", keep_original=True)
    rows = score(studio, reference=metrics.REFERENCES[0])

    assert "already aligned" in rows["Alignment"]
    assert not any("Not aligned" in k for k in rows)


def test_a_resort_while_auditioning_replays_in_the_new_order(studio):
    """The loop plays the selected rows top to bottom, so turning the table
    over plays them again the other way round."""
    info = run(studio)
    view = page(info, ("click", 0), ("shift", 1), ("press", "audition"),
                ("header", 0), ("header", 0))[-1]
    heard = asked(view, "audition")
    assert len(heard) >= 2
    shown = shown_keys(info, view)
    assert heard[-1][0] == [k for k in shown if k in heard[0][0]]
    assert heard[-1][0] != heard[0][0]


def test_a_sort_that_keeps_the_order_does_not_restart_the_loop(studio):
    info = run(studio)
    # one row cannot change places with itself
    view = page(info, ("click", 0), ("press", "audition"), ("header", 0))[-1]
    assert len(asked(view, "audition")) == 1
    # and nothing plays again while nothing is playing
    view = page(info, ("click", 0), ("shift", 1), ("header", 0), ("header", 0))[-1]
    assert asked(view, "audition") == []
