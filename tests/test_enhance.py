# -*- coding: utf-8 -*-
"""The Enhance panel: classic DSP always, learned models when available."""
from __future__ import annotations

import importlib.util
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from kudiostudio.panels.enhance import LEARNED, TRAD, WAVELET, EnhancePanel


def select(panel, key: str) -> None:
    """Pick a method by its key, not its row.

    The combo gained seven statistical methods above the original three, and
    every test that hardcoded index 0 broke. Names survive reordering; indices
    are a convention two files have to agree on by hand.
    """
    index = panel.method.findData(key)
    assert index >= 0, f"no method {key!r} in the combo"
    panel.method.setCurrentIndex(index)


def test_method_switch_greys_out_the_other_rows(session):
    panel = EnhancePanel(session)

    select(panel, TRAD)
    assert panel._method() == TRAD
    assert panel.thres.isEnabled() is False
    assert panel.run_dir.isEnabled() is False
    assert panel.noise.isEnabled() is False

    select(panel, WAVELET)
    assert panel.thres.isEnabled() is True
    assert panel.run_dir.isEnabled() is False

    select(panel, LEARNED)
    assert panel.thres.isEnabled() is False
    assert panel.run_dir.isEnabled() is True

    select(panel, "logmmse")
    assert panel.noise.isEnabled() is True
    assert panel.thres.isEnabled() is False


def test_call_hint_tracks_the_method(session):
    panel = EnhancePanel(session)

    select(panel, TRAD)
    assert panel.call.text() == "kudio.trad_enhance(y, sr)"

    select(panel, WAVELET)
    panel.thres.setValue(0.5)
    assert "wavelet_low_pass_filter" in panel.call.text()
    assert "thres=0.5" in panel.call.text()

    select(panel, LEARNED)
    panel.run_dir.edit.setText("runs/exp1")
    assert "kudioenhance.Enhancer.load('runs/exp1')" in panel.call.text()

    select(panel, "omlsa")
    assert "kudio.spectral_enhance(y, sr, 'omlsa'" in panel.call.text()
    assert "noise='mcra'" in panel.call.text()


def test_learned_without_a_run_directory_reports(session, monkeypatch):
    """Whatever is installed: the run directory is what this is about, and
    without kudioenhance the install hint used to come first."""
    import kudiostudio.panels.enhance as module

    monkeypatch.setattr(module, "kudioenhance_available", lambda: True)
    panel = EnhancePanel(session)
    select(panel, LEARNED)
    messages = []
    panel.status.connect(messages.append)

    panel._run()
    assert messages == ["Select a kudioenhance run directory first."]


def test_learned_without_the_package_shows_the_install_hint(session, monkeypatch):
    import kudiostudio.panels.enhance as module

    monkeypatch.setattr(module, "kudioenhance_available", lambda: False)
    panel = EnhancePanel(session)
    select(panel, LEARNED)
    panel.run_dir.edit.setText("runs/exp1")
    messages = []
    panel.status.connect(messages.append)

    panel._run()
    # the extra, which brings kudioenhance and PyTorch at versions that work
    # with this release, rather than either package on its own
    assert messages and 'pip install "kudiostudio[enhance]"' in messages[0]


def test_kudioenhance_without_pytorch_is_not_available(session, tmp_path,
                                                        monkeypatch):
    """The package imports without the framework; the first Denoise would not."""
    import kudiostudio.panels.enhance as module

    specs = {"kudioenhance": object(), "torch": None}
    monkeypatch.setattr(module, "importlib",
                        SimpleNamespace(util=SimpleNamespace(find_spec=specs.get)))
    assert module.kudioenhance_available() is False

    panel = EnhancePanel(session)
    select(panel, LEARNED)
    panel.run_dir.edit.setText(str(tmp_path))
    messages = []
    panel.status.connect(messages.append)
    panel._run()
    assert messages and "PyTorch" in messages[0]

    specs["torch"] = object()
    assert module.kudioenhance_available() is True


# -------------------------------------------- statistical methods (kudio 3.4)

def test_every_kudio_method_is_offered(session):
    """The list is built from kudio.METHODS, so it cannot fall behind it."""
    panel = EnhancePanel(session)
    offered = {panel.method.itemData(i) for i in range(panel.method.count())}
    assert set(kudio_methods()) <= offered


def kudio_methods():
    import kudio
    return kudio.METHODS


def test_controls_are_built_from_kudios_own_description(session):
    """A parameter added in kudio must appear here without editing the panel."""
    import kudio

    panel = EnhancePanel(session)
    for name, spec in kudio.METHODS.items():
        select(panel, name)
        boxes = panel._params[name]
        assert set(boxes) == {p.name for p in spec.params}, name
        for param in spec.params:
            box = boxes[param.name]
            assert box.value() == pytest.approx(param.default)
            assert box.minimum() == pytest.approx(param.low)
            assert box.maximum() == pytest.approx(param.high)
            assert box.toolTip() == param.help


@pytest.mark.parametrize("method", ["wiener", "logmmse", "omlsa", "specsub",
                                    "multiband", "mmse_stsa", "spectral_gate"])
def test_each_statistical_method_runs_through_the_panel(session, method):
    panel = EnhancePanel(session)
    select(panel, method)
    fn, args, kwargs, label = panel._operation(np.asarray(session.y), session.sr)
    out = fn(*args, **kwargs)
    assert len(out) == len(session.y)
    assert method in label or kudio_methods()[method].label in label


def test_the_noise_estimator_reaches_the_call(session):
    panel = EnhancePanel(session)
    select(panel, "logmmse")
    index = panel.noise.findData("quantile")
    panel.noise.setCurrentIndex(index)
    assert "noise='quantile'" in panel.call.text()
    _, _, kwargs, _ = panel._operation(np.asarray(session.y), session.sr)
    assert kwargs["noise"] == "quantile"


def test_the_noise_estimator_explains_itself(session):
    panel = EnhancePanel(session)
    select(panel, "logmmse")
    assert "recursive averaging" in panel.noise_hint.text()
    panel.noise.setCurrentIndex(panel.noise.findData("initial"))
    assert "room tone" in panel.noise_hint.text()


def test_the_separator_row_does_not_crash_the_panel(session):
    """A combo separator has no data; _sync_params sees None."""
    panel = EnhancePanel(session)
    separator = next(i for i in range(panel.method.count())
                     if panel.method.itemData(i) is None)
    panel.method.setCurrentIndex(separator)      # Qt normally skips these
    assert panel.call.text()                     # ...and nothing raised


def test_availability_note_matches_the_environment(session):
    from kudiostudio.panels.enhance import kudioenhance_available

    panel = EnhancePanel(session)
    text = panel.availability.text()
    if kudioenhance_available():
        assert "is installed" in text
    else:
        assert "pip install" in text


def test_changing_the_run_directory_drops_the_cached_model(session):
    panel = EnhancePanel(session)
    panel._model.enhancer, panel._model.run_dir = object(), "runs/old"

    panel.run_dir.edit.setText("runs/new")
    assert panel._model.enhancer is None
    assert panel._model.run_dir is None


# --------------------------------------------------------------- integration

def run_and_wait(panel) -> None:
    """Denoise runs on a worker; drive the loop until it lands."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    panel._run()
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)


# skipped without kudioenhance and PyTorch, by the marker in conftest.py --
# not a module-level importorskip, which would skip the tests that must run
# with or without them
requires_enhance = pytest.mark.learned


@requires_enhance
def test_learned_denoising_runs_against_a_real_model(session, trained_run):
    run_dir, sr = trained_run
    panel = EnhancePanel(session)
    assert session.sr == sr

    noisy = np.asarray(session.y, dtype=np.float32)
    out = panel._model.enhance(noisy, sr, str(run_dir))
    assert out.shape == noisy.shape
    assert np.isfinite(out).all()
    # the model is cached for the next call on the same directory
    assert panel._model.run_dir == str(run_dir)


@requires_enhance
def test_learned_denoising_refuses_a_rate_mismatch_when_told_to(session, trained_run):
    run_dir, sr = trained_run
    panel = EnhancePanel(session)

    with pytest.raises(ValueError, match="trained at"):
        panel._model.enhance(np.zeros(1000, dtype=np.float32), sr * 2,
                             str(run_dir), allow_resample=False)


@requires_enhance
def test_learned_denoising_resamples_across_rates(session, trained_run):
    """A clip at another rate goes in at the model's rate and comes back at its own."""
    run_dir, sr = trained_run
    panel = EnhancePanel(session)

    other_sr = sr * 2
    y = np.asarray(session.y, dtype=np.float32)
    out = panel._model.enhance(y, other_sr, str(run_dir), allow_resample=True)

    # length is preserved in the caller's rate, so it splices back cleanly
    assert out.shape == y.shape
    assert np.isfinite(out).all()


@requires_enhance
def test_the_learned_call_hint_runs_as_written(session, trained_run):
    """kudioenhance exports `Enhancer` from its package, so the hint is not a
    description but a statement: with `kudioenhance` imported it runs, and it
    gives what Denoise gives."""
    import kudioenhance

    run_dir, sr = trained_run
    panel = EnhancePanel(session)
    select(panel, LEARNED)
    panel.run_dir.edit.setText(str(run_dir))
    y = np.asarray(session.y, dtype=np.float32)

    out = eval(panel.call.text(), {"kudioenhance": kudioenhance, "y": y})
    assert np.allclose(out, panel._model.enhance(y, sr, str(run_dir)),
                       rtol=0.0, atol=1e-6)


@requires_enhance
def test_a_run_from_kudioenhance_0_1_is_reported_as_needing_retraining(
        session, trained_run, tmp_path):
    """0.1 saved Keras weights, which 0.2 cannot read. The panel passes on
    what to do about it, and commits nothing."""
    run_dir, _ = trained_run
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for name in ("config.yaml", "stats.npz"):
        shutil.copy(run_dir / name, legacy / name)
    (legacy / "ddae.keras").write_bytes(b"")     # where 0.1 left the weights

    panel = EnhancePanel(session)
    select(panel, LEARNED)
    panel.run_dir.edit.setText(str(legacy))
    before = session.y.copy()
    messages = []
    panel.status.connect(messages.append)

    run_and_wait(panel)
    assert "failed" in messages[-1]
    assert "Retrain it" in messages[-1]
    assert np.array_equal(session.y, before)
    assert not session.has_history()
