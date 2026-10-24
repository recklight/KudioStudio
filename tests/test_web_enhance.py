# -*- coding: utf-8 -*-
"""Enhance in the window: Denoise and Preview as jobs, the call they record,
and the method list the page builds from."""
from __future__ import annotations

import threading

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import enhance as tools
from kudiostudio.web.studio import Prefs, Studio


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


#: what every method's controls make before anything is touched
DEFAULT_CALLS = {
    "specsub": ("kudio.spectral_enhance(y, sr, 'specsub', noise='mcra', n_fft=512, "
                "over_subtraction=2.0, spectral_floor=0.002, gain_floor_db=-25.0)"),
    "multiband": ("kudio.spectral_enhance(y, sr, 'multiband', noise='mcra', n_fft=512, "
                  "bands=6.0, spectral_floor=0.002, gain_floor_db=-25.0)"),
    "wiener": ("kudio.spectral_enhance(y, sr, 'wiener', noise='mcra', n_fft=512, "
               "alpha=0.98, gain_floor_db=-25.0)"),
    "mmse_stsa": ("kudio.spectral_enhance(y, sr, 'mmse_stsa', noise='mcra', n_fft=512, "
                  "alpha=0.98, gain_floor_db=-25.0)"),
    "logmmse": ("kudio.spectral_enhance(y, sr, 'logmmse', noise='mcra', n_fft=512, "
                "alpha=0.98, gain_floor_db=-25.0)"),
    "omlsa": ("kudio.spectral_enhance(y, sr, 'omlsa', noise='mcra', n_fft=512, "
              "alpha=0.98, gain_floor_db=-25.0, q=0.3)"),
    "spectral_gate": ("kudio.spectral_enhance(y, sr, 'spectral_gate', noise='mcra', "
                      "n_fft=512, n_std_thresh=1.5, prop_decrease=1.0, "
                      "freq_smooth_hz=500.0, time_smooth_ms=50.0)"),
    "trad": "kudio.trad_enhance(y, sr)",
    "wavelet": "kudio.wavelet_low_pass_filter(y, thres=0.8, wavelet='db2')",
    "learned": "kudioenhance.Enhancer.load('').enhance(y)",
}


def _untouched(info, method):
    """The values the page sends for *method* before anything is touched:
    enhance.js starts n_fft at 512, thres at 0.8 and resampling on, and the
    rest at the first entry of each list."""
    values = {"noise": info["noise"][0]["name"], "n_fft": 512, "thres": 0.8,
              "wavelet": info["wavelets"][0], "run_dir": "", "resample": True}
    for m in info["statistical"]:
        if m["name"] == method:
            values.update({p["name"]: p["default"] for p in m["params"]})
    return values


def test_each_method_starts_from_its_default_call(studio):
    info = studio.call("enhance.describe")
    methods = [m["name"] for m in info["statistical"] + info["others"]]
    assert methods == list(DEFAULT_CALLS)
    for name in methods:
        text = studio.call("enhance.call_text", [name, _untouched(info, name)])
        assert text == DEFAULT_CALLS[name], name


def test_every_method_has_its_parameters_described():
    schema = tools.schema()
    assert [m["name"] for m in schema["statistical"]] == list(kudio.METHODS)
    assert [m["name"] for m in schema["others"]] == [tools.TRAD, tools.WAVELET, tools.LEARNED]
    for m in schema["statistical"]:
        spec = kudio.METHODS[m["name"]]
        assert [p["name"] for p in m["params"]] == [p.name for p in spec.params]


def test_a_denoise_is_committed_with_its_code(studio):
    before = studio.session.y.copy()
    info = studio.call("enhance.denoise", ["wiener", {"n_fft": 512}])
    assert info["busy"]
    assert studio.jobs.wait(60)
    assert not np.array_equal(studio.session.y, before)
    step = studio.session.history[-1]
    assert "kudio.spectral_enhance(y, sr, 'wiener'" in step.code
    assert studio.call("enhance.describe")["result"].startswith("SNR ")


def test_an_edit_while_it_runs_leaves_the_step_without_code(studio, monkeypatch):
    gate = threading.Event()
    real = kudio.spectral_enhance

    def slow(*args, **kwargs):
        gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(kudio, "spectral_enhance", slow)
    studio.call("enhance.denoise", ["wiener", {}])
    studio.call("set_selection", [0.0, 0.1])
    studio.call("delete")
    gate.set()
    assert studio.jobs.wait(60)
    assert studio.session.history[-1].code is None


def test_a_second_denoise_is_refused_while_one_runs(studio, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(kudio, "trad_enhance", lambda y, sr: (gate.wait(10), y)[1])
    drain = _said(studio)
    studio.call("enhance.denoise", ["trad", {}])
    studio.call("enhance.denoise", ["trad", {}])
    gate.set()
    studio.jobs.wait(30)
    assert "A denoising job is already running." in drain()


def test_a_learned_model_without_the_package_says_how_to_get_it(studio, monkeypatch):
    monkeypatch.setattr(tools, "kudioenhance_available", lambda: False)
    drain = _said(studio)
    studio.call("enhance.denoise", ["learned", {"run_dir": "nowhere"}])
    assert drain() == [tools.INSTALL_HINT]


def test_a_preview_loops_the_selected_part_of_the_whole_denoise(studio):
    studio.call("set_selection", [0.5, 1.0])
    assert studio.call("enhance.preview", ["trad", {}]) == {"on": True}
    assert studio.jobs.wait(60)
    y, sr, t0, owner = studio._preview
    assert owner == "enhance" and t0 == 0.5
    assert len(y) == pytest.approx(0.5 * sr, abs=2)


def test_values_that_move_while_it_renders_are_rendered_next(studio, monkeypatch):
    gate = threading.Event()
    calls = []
    real = kudio.wavelet_low_pass_filter

    def slow(y, thres=0.8, wavelet="db2"):
        calls.append(thres)
        if len(calls) == 1:
            gate.wait(10)
        return real(y, thres=thres, wavelet=wavelet)

    monkeypatch.setattr(kudio, "wavelet_low_pass_filter", slow)
    studio.call("enhance.preview", ["wavelet", {"thres": 0.5}])
    studio.call("enhance.preview", ["wavelet", {"thres": 1.5}])
    studio.call("enhance.preview", ["wavelet", {"thres": 2.5}])   # only the last counts
    gate.set()
    for _ in range(3):
        studio.jobs.wait(60)
    assert calls == [0.5, 2.5]
    assert studio.call("state")["previewing"] == "enhance"


def test_switching_preview_off_drops_a_render_in_flight(studio, monkeypatch):
    gate = threading.Event()
    real = kudio.trad_enhance
    monkeypatch.setattr(kudio, "trad_enhance", lambda y, sr: (gate.wait(10), real(y, sr))[1])
    studio.call("enhance.preview", ["trad", {}])
    studio.call("enhance.stop_preview")
    gate.set()
    studio.jobs.wait(60)
    assert studio.call("state")["previewing"] == ""
