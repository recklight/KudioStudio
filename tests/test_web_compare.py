# -*- coding: utf-8 -*-
"""Compare in the window: the run, the verdict, auditioning rows back to
back, and applying one with the code that makes it again."""
from __future__ import annotations

import threading

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import compare as tools
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


def _run(studio, **kwargs):
    args = {"reference": tools.REF_NONE, "sweep": tools.SWEEP_METHODS, **kwargs}
    studio.call("compare.run", kwargs=args)
    assert studio.jobs.wait(120)
    return studio.call("compare.describe")


#: each sweep's call from the controls as the page starts them, with the
#: quality scores asked for on the full grid only
DEFAULT_CALLS = {
    tools.SWEEP_METHODS: ("kudio.compare_enhancers(y, sr, reference=None, noise='mcra', "
                          "metrics=False)"),
    tools.SWEEP_NOISES: ("kudio.compare_enhancers(y, sr, ['logmmse'], reference=None, "
                         "noises=list(kudio.NOISE_ESTIMATORS), metrics=False)"),
    tools.SWEEP_GRID: ("kudio.compare_enhancers(y, sr, reference=None, "
                       "noises=list(kudio.NOISE_ESTIMATORS), metrics=True)"),
}


def test_each_sweep_starts_from_its_default_call(studio):
    info = studio.call("compare.describe")
    assert info["sweeps"] == list(DEFAULT_CALLS)
    for sweep in info["sweeps"]:
        text = studio.call("compare.call_text", [
            sweep, info["default_method"], info["noises"][0]["name"],
            info["references"][0], sweep == tools.SWEEP_GRID])
        assert text == DEFAULT_CALLS[sweep], sweep


def test_without_a_reference_the_quietest_row_is_named_and_doubted(studio):
    info = _run(studio)
    assert len(info["rows"]) == len(kudio.METHODS)
    assert not info["has_reference"]
    assert info["verdict"]["tone"] == "hint"
    assert info["verdict"]["text"].startswith("No reference, so there is no quality score.")


def test_against_the_original_the_rows_are_scored(studio):
    sr = studio.session.sr
    noisy = studio.session.y + 0.05 * np.random.default_rng(0).standard_normal(
        len(studio.session.y)).astype(np.float32)
    studio.session.set_audio(noisy, sr, undo_label="noise", keep_original=True)
    info = _run(studio, reference=tools.REF_ORIGINAL)
    assert info["has_reference"]
    si_sdr = tools.COLUMNS.index("SI-SDR")
    assert all(row["sort"][si_sdr] is not None for row in info["rows"] if not row["error"])
    assert info["verdict"]["tone"] in ("ok", "warn")


def test_two_rows_are_heard_back_to_back_with_a_gap(studio):
    info = _run(studio)
    keys = [row["key"] for row in info["rows"][:2]]
    assert studio.call("compare.audition", [keys]) == {"on": True}
    y, sr, _, owner = studio._preview
    n = len(studio.session.y)
    assert owner == "compare"
    assert len(y) == 2 * n + int(tools.AB_GAP_SECONDS * sr)


def test_a_row_is_applied_with_the_code_that_makes_it(studio):
    info = _run(studio)
    key = info["rows"][0]["key"]
    studio.call("compare.apply", [key])
    code = studio.session.history[-1].code
    assert code == (f"y = np.asarray(kudio.spectral_enhance(y, sr, {key[0]!r}, "
                    f"noise={key[1]!r}), dtype=np.float32)")


def test_a_row_applied_after_the_selection_moved_has_no_code(studio):
    studio.call("set_selection", [0.2, 1.0])
    info = _run(studio, selection_only=True)
    studio.call("set_selection", [0.3, 1.1])
    studio.call("compare.apply", [info["rows"][0]["key"], True])
    assert studio.session.history[-1].code is None


def test_results_for_audio_since_edited_are_dropped(studio, monkeypatch):
    gate = threading.Event()
    real = tools.compare_with_model

    def slow(*args, **kwargs):
        gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, "compare_with_model", slow)
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE})
    studio.call("set_selection", [0.0, 0.1])
    studio.call("delete")
    gate.set()
    assert studio.jobs.wait(120)
    assert studio.call("compare.describe")["rows"] == []


def test_every_result_is_saved_under_its_method_and_estimator(studio, tmp_path):
    _run(studio)
    out = tmp_path / "all"
    out.mkdir()
    studio.call("compare.save_all", [str(out)])
    names = sorted(p.name for p in out.glob("*.wav"))
    assert len(names) == len(kudio.METHODS)
    assert all(name.startswith("recording_") for name in names)


def test_new_audio_clears_the_table(studio):
    _run(studio)
    studio.session.set_audio(np.zeros(16000, np.float32), 16000, name="new")
    info = studio.call("compare.describe")
    assert info["rows"] == [] and info["verdict"]["text"] == ""


def test_too_short_a_selection_is_refused(studio):
    studio.call("set_selection", [0.0, 0.02])
    q = studio.events.subscribe()
    studio.call("compare.run", kwargs={"reference": tools.REF_NONE, "selection_only": True})
    said = []
    while not q.empty():
        e = q.get_nowait()
        if e["type"] == "message":
            said.append(e["text"])
    assert "Too short to compare — select at least 512 samples." in said
