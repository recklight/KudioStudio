# -*- coding: utf-8 -*-
"""The Enhance panel: classic DSP always, learned models when available."""
from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

import kudio
from kudiostudio.tools import enhance as tools
from kudiostudio.tools.enhance import LEARNED, TRAD, WAVELET
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture
def studio(tmp_path, tone, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    s.call("open_files", [[str(tone)]])
    yield s
    s.jobs.wait(60)
    s.shutdown()


def values(**changes) -> dict:
    """The controls under the names the page sends them, at the values it
    opens with: the first noise estimator, 512-point frames."""
    v = {"noise": tools.DEFAULT_NOISE, "n_fft": 512, "thres": 0.8,
         "wavelet": tools.WAVELETS[0], "run_dir": "", "resample": True}
    v.update(changes)
    return v


def call_text(studio, method: str, **changes) -> str:
    return studio.call("enhance.call_text", [method, values(**changes)])


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


def test_a_method_shows_only_its_own_rows(studio):
    """The noise estimate row belongs to kudio's methods, thres to the wavelet
    filter and run_dir to a learned model; the page shows only the form of
    the kind chosen."""
    shown = mount_page("js/panels/enhance.js", studio.call("enhance.describe"), """
const method = row("Method");
const shown = {};
for (const name of ["trad", "wavelet", "learned", "logmmse"]) {
  method.value = name;
  method.on.change();
  shown[name] = { asked: asked.at(-1)[1][0] };
  for (const label of ["noise estimate", "thres", "run_dir"]) {
    shown[name][label] = !formWith(label).hidden;
  }
}
console.log(JSON.stringify(shown));
""")

    assert shown[TRAD]["asked"] == TRAD
    assert shown[TRAD]["thres"] is False
    assert shown[TRAD]["run_dir"] is False
    assert shown[TRAD]["noise estimate"] is False

    assert shown[WAVELET]["thres"] is True
    assert shown[WAVELET]["run_dir"] is False

    assert shown[LEARNED]["thres"] is False
    assert shown[LEARNED]["run_dir"] is True

    assert shown["logmmse"]["noise estimate"] is True
    assert shown["logmmse"]["thres"] is False


def test_call_hint_tracks_the_method(studio):
    assert call_text(studio, TRAD) == "kudio.trad_enhance(y, sr)"

    text = call_text(studio, WAVELET, thres=0.5)
    assert "wavelet_low_pass_filter" in text
    assert "thres=0.5" in text

    text = call_text(studio, LEARNED, run_dir="runs/exp1")
    assert "kudioenhance.Enhancer.load('runs/exp1')" in text

    text = call_text(studio, "omlsa")
    assert "kudio.spectral_enhance(y, sr, 'omlsa'" in text
    assert "noise='mcra'" in text


def test_learned_without_a_run_directory_reports(studio, monkeypatch):
    """kudioenhance is made to look installed, so whatever this machine has,
    the missing run directory is the one thing to report."""
    monkeypatch.setattr(tools, "kudioenhance_available", lambda: True)
    said = messages(studio)

    studio.call("enhance.denoise", [LEARNED, values()])
    assert said() == ["Select a kudioenhance run directory first."]


def test_learned_without_the_package_shows_the_install_hint(studio, monkeypatch):
    monkeypatch.setattr(tools, "kudioenhance_available", lambda: False)
    said = messages(studio)

    studio.call("enhance.denoise", [LEARNED, values(run_dir="runs/exp1")])
    reported = said()
    # the extra, which brings kudioenhance and PyTorch at versions that work
    # with this release, rather than either package on its own
    assert reported and 'pip install "kudiostudio[enhance]"' in reported[0]


def test_kudioenhance_without_pytorch_is_not_available(studio, tmp_path, monkeypatch):
    """The package imports without the framework; the first Denoise would not."""
    specs = {"kudioenhance": object(), "torch": None}
    monkeypatch.setattr(tools, "importlib",
                        SimpleNamespace(util=SimpleNamespace(find_spec=specs.get)))
    assert tools.kudioenhance_available() is False

    said = messages(studio)
    studio.call("enhance.denoise", [LEARNED, values(run_dir=str(tmp_path))])
    reported = said()
    assert reported and "PyTorch" in reported[0]

    specs["torch"] = object()
    assert tools.kudioenhance_available() is True


# -------------------------------------------- statistical methods (kudio 3.4)

def test_every_kudio_method_is_offered(studio):
    """The list is built from kudio.METHODS, so it cannot fall behind it."""
    described = studio.call("enhance.describe")
    offered = {m["name"] for m in described["statistical"] + described["others"]}
    assert set(kudio.METHODS) <= offered


def test_controls_are_built_from_kudios_own_description(studio):
    """A parameter added in kudio must appear here without editing the panel."""
    described = {m["name"]: {p["name"]: p for p in m["params"]}
                 for m in studio.call("enhance.describe")["statistical"]}
    for name, spec in kudio.METHODS.items():
        controls = described[name]
        assert set(controls) == {p.name for p in spec.params}, name
        for param in spec.params:
            control = controls[param.name]
            # ui.params makes a number box only for these kinds, and the box
            # holds its value to `decimals` places
            assert control["kind"] in ("float", "int"), param.name
            places = 0 if control["kind"] == "int" else control["decimals"]
            assert round(control["default"], places) == pytest.approx(param.default)
            assert control["min"] == pytest.approx(param.low)
            assert control["max"] == pytest.approx(param.high)
            assert control["tip"] == param.help


@pytest.mark.parametrize("method", ["wiener", "logmmse", "omlsa", "specsub",
                                    "multiband", "mmse_stsa", "spectral_gate"])
def test_each_statistical_method_runs_through_the_panel(studio, method):
    y = np.asarray(studio.session.y)
    fn, args, kwargs, label = studio.panel("enhance")._operation(method, values(), y)
    out = fn(*args, **kwargs)
    assert len(out) == len(y)
    assert method in label or kudio.METHODS[method].label in label


def test_the_noise_estimator_reaches_the_call(studio):
    assert "noise='quantile'" in call_text(studio, "logmmse", noise="quantile")
    _, _, kwargs, _ = studio.panel("enhance")._operation(
        "logmmse", values(noise="quantile"), np.asarray(studio.session.y))
    assert kwargs["noise"] == "quantile"


def test_the_noise_estimator_explains_itself(studio):
    hints = mount_page("js/panels/enhance.js", studio.call("enhance.describe"), """
const method = row("Method");
method.value = "logmmse";
method.on.change();
const noise = row("noise estimate");
const hint = formWith("noise estimate").rows[""];
const hints = [hint.textContent];
noise.value = "initial";
noise.given.onChange();
hints.push(hint.textContent);
console.log(JSON.stringify(hints));
""")
    assert "recursive averaging" in hints[0]
    assert "room tone" in hints[1]


def test_the_separator_row_does_not_crash_the_panel(studio):
    """The line between kudio's methods and the other three is an option
    with no value. Nothing can pick it; if the list lands on it anyway, the
    page still asks for a call, and gets one."""
    asked = mount_page("js/panels/enhance.js", studio.call("enhance.describe"), """
const method = row("Method");
method.value = "";
method.on.change();
console.log(JSON.stringify(asked.at(-1)));
""")
    name, (method, sent) = asked
    assert (name, method) == ("call_text", "")
    assert studio.call("enhance.call_text", [method, sent])


def test_availability_note_matches_the_environment(studio):
    note = studio.call("enhance.describe")["note"]
    if tools.kudioenhance_available():
        assert "is installed" in note
    else:
        assert "pip install" in note


def test_changing_the_run_directory_drops_the_cached_model(studio):
    model = studio.panel("enhance").model
    model.enhancer, model.run_dir = object(), "runs/old"

    studio.call("enhance.denoise", [LEARNED, values(run_dir="runs/new")])
    assert model.enhancer is None
    assert model.run_dir is None


# --------------------------------------------------------------- integration

# skipped without kudioenhance and PyTorch, by the marker in conftest.py --
# not a module-level importorskip, which would skip the tests that must run
# with or without them
requires_enhance = pytest.mark.learned


@requires_enhance
def test_learned_denoising_runs_against_a_real_model(studio, trained_run):
    run_dir, sr = trained_run
    model = studio.panel("enhance").model
    assert studio.session.sr == sr

    noisy = np.asarray(studio.session.y, dtype=np.float32)
    out = model.enhance(noisy, sr, str(run_dir))
    assert out.shape == noisy.shape
    assert np.isfinite(out).all()
    # the model is cached for the next call on the same directory
    assert model.run_dir == str(run_dir)


@requires_enhance
def test_learned_denoising_refuses_a_rate_mismatch_when_told_to(studio, trained_run):
    run_dir, sr = trained_run
    model = studio.panel("enhance").model

    with pytest.raises(ValueError, match="trained at"):
        model.enhance(np.zeros(1000, dtype=np.float32), sr * 2,
                      str(run_dir), allow_resample=False)


@requires_enhance
def test_learned_denoising_resamples_across_rates(studio, trained_run):
    """A clip at another rate goes in at the model's rate and comes back at its own."""
    run_dir, sr = trained_run
    model = studio.panel("enhance").model

    other_sr = sr * 2
    y = np.asarray(studio.session.y, dtype=np.float32)
    out = model.enhance(y, other_sr, str(run_dir), allow_resample=True)

    # length is preserved in the caller's rate, so it splices back cleanly
    assert out.shape == y.shape
    assert np.isfinite(out).all()


@requires_enhance
def test_the_learned_call_hint_runs_as_written(studio, trained_run):
    """kudioenhance exports `Enhancer` from its package, so the hint is not a
    description but a statement: with `kudioenhance` imported it runs, and it
    gives what Denoise gives."""
    import kudioenhance

    run_dir, sr = trained_run
    y = np.asarray(studio.session.y, dtype=np.float32)

    out = eval(call_text(studio, LEARNED, run_dir=str(run_dir)),
               {"kudioenhance": kudioenhance, "y": y})
    assert np.allclose(out, studio.panel("enhance").model.enhance(y, sr, str(run_dir)),
                       rtol=0.0, atol=1e-6)


@requires_enhance
def test_a_run_from_kudioenhance_0_1_is_reported_as_needing_retraining(
        studio, trained_run, tmp_path):
    """0.1 saved Keras weights, which 0.2 cannot read. The panel passes on
    what to do about it, and commits nothing."""
    run_dir, _ = trained_run
    keras_run = tmp_path / "keras_run"
    keras_run.mkdir()
    for name in ("config.yaml", "stats.npz"):
        shutil.copy(run_dir / name, keras_run / name)
    (keras_run / "ddae.keras").write_bytes(b"")     # where 0.1 left the weights

    before = studio.session.y.copy()
    said = messages(studio)

    studio.call("enhance.denoise", [LEARNED, values(run_dir=str(keras_run))])
    assert studio.jobs.wait(60)
    reported = said()
    assert "failed" in reported[-1]
    assert "Retrain it" in reported[-1]
    assert np.array_equal(studio.session.y, before)
    assert not studio.session.has_history()
