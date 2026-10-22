# -*- coding: utf-8 -*-
"""Shared fixtures. Qt runs headless so the suite works in CI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Whatever plugins this machine has installed stay out of the suite, which
# tests the window a clone of this repository builds. test_plugins.py turns
# discovery back on with entry points of its own.
os.environ["KUDIOSTUDIO_NO_PLUGINS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Set to 1 where the learned-model tests are the point of the run -- a CI job
#: that installs kudio-enhance and PyTorch for them. There a missing package
#: or a PyTorch that will not import fails those tests instead of skipping
#: them, since a job whose every learned test skipped would otherwise pass.
REQUIRE_LEARNED = os.environ.get("KUDIOSTUDIO_REQUIRE_LEARNED") == "1"


def learned_available() -> bool:
    """kudio-enhance and PyTorch are both there -- looked up, not imported."""
    import importlib.util
    return all(importlib.util.find_spec(name) is not None
               for name in ("kudio_enhance", "torch"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "learned: needs kudio-enhance and PyTorch; skipped without "
                   "them unless KUDIOSTUDIO_REQUIRE_LEARNED=1")


def pytest_collection_modifyitems(config, items):
    if REQUIRE_LEARNED or learned_available():
        return
    skip = pytest.mark.skip(reason="learned denoising needs kudio-enhance "
                                   "and PyTorch")
    for item in items:
        if "learned" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    from kudiostudio.workers import collect_on_gui_thread
    app = QApplication.instance() or QApplication([])
    # __main__ names the application, and a QSettings with no names has
    # nowhere to write on Windows -- every value read back empty. A name of
    # the suite's own also keeps it out of a developer's real settings
    app.setOrganizationName("KudioTests")
    app.setApplicationName("KudioStudioTests")
    # as the window does: tests let go of panels with no parent, and their
    # widgets must not be collected on the next test's worker thread
    collect_on_gui_thread()
    yield app


@pytest.fixture
def tone(tmp_path) -> Path:
    """A 1 s 440 Hz tone at 16 kHz, written to disk."""
    import kudio
    sr = 16000
    t = np.arange(sr, dtype=np.float32) / sr
    y = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    path = tmp_path / "tone.wav"
    kudio.save_wave(path, y, sr)
    return path


@pytest.fixture
def recording(tmp_path) -> Path:
    """2 s of speech-like audio with a noise floor, as a real take would have.

    The `tone` fixture is a mathematically pure sine, which genuinely has no
    energy above 440 Hz — so anything inspecting the spectrum correctly calls
    it band-limited. Tests about recordings want something that looks like one.
    """
    import kudio
    sr = 16000
    t = np.arange(2 * sr, dtype=np.float32) / sr
    y = sum(0.4 / k * np.sin(2 * np.pi * 180 * k * t) for k in range(1, 12))
    y *= 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    y = y / np.max(np.abs(y)) * 0.5
    y += 0.01 * np.random.default_rng(0).standard_normal(len(t))
    path = tmp_path / "recording.wav"
    kudio.save_wave(path, y.astype(np.float32), sr)
    return path


@pytest.fixture
def session(qapp, tone):
    from kudiostudio.session import AudioSession
    s = AudioSession()
    s.load_file(tone)
    return s


@pytest.fixture
def real_session(qapp, recording):
    from kudiostudio.session import AudioSession
    s = AudioSession()
    s.load_file(recording)
    return s


@pytest.fixture(scope="session")
def trained_run(tmp_path_factory):
    """A one-epoch kudio-enhance run — enough to exercise the real load path.

    Shared by every module that needs a model, and trained once: it is the
    slowest thing the suite does. Only requested by tests marked ``learned``,
    which skip when kudio-enhance or PyTorch is not installed, and it skips as
    well when PyTorch is installed but will not import — a CUDA library gone
    missing, say. kudio-enhance imports without the framework, so it is the
    training below that would find out, as an error rather than a skip. With
    KUDIOSTUDIO_REQUIRE_LEARNED=1 it fails instead.
    """
    try:
        import torch  # noqa: F401
    except ImportError as e:
        reason = f"learned denoising needs PyTorch, which will not import: {e}"
        if REQUIRE_LEARNED:
            pytest.fail(reason)
        pytest.skip(reason)

    import kudio
    from kudio_enhance import Config, pipeline

    root = tmp_path_factory.mktemp("enhance")
    clean_dir, noise_dir = root / "clean", root / "noise"
    clean_dir.mkdir()
    noise_dir.mkdir()

    sr = 16000          # matches the session fixture
    rng = np.random.default_rng(5)
    for i in range(4):
        t = np.arange(int(0.5 * sr)) / sr
        # at full scale, noise added at 0 dB clipped every mixture written
        y = 0.3 * np.sin(2 * np.pi * (150 + 40 * i) * t) * np.hanning(len(t))
        kudio.save_wave(clean_dir / f"u{i}.wav", y.astype(np.float32), sr)
    kudio.save_wave(noise_dir / "white.wav",
                    (0.3 * rng.normal(0, 1, sr)).astype(np.float32), sr)

    cfg = Config.from_dict({
        "audio": {"sr": sr, "n_fft": 256, "hop_length": 128},
        "data": {"clean_dir": str(clean_dir), "noise_dir": str(noise_dir),
                 "mixed_dir": str(root / "mixed"), "snr_db": [0],
                 "seed": 1, "val_split": 0.25, "test_split": 0.0},
        "model": {"name": "ddae", "context": 1, "units": [32], "dropout": 0.0},
        "train": {"epochs": 1, "batch_size": 32, "patience": 1,
                  "out_dir": str(root / "runs")},
    })
    pipeline.synthesize(cfg, "exp")
    pipeline.train(cfg, "exp", epochs=1, verbose=0)
    return cfg.run_dir("exp"), sr


def pytest_sessionfinish(session, exitstatus):
    session.config._kudiostudio_exitstatus = int(exitstatus)


def pytest_unconfigure(config):
    """Leave with pytest's verdict, before PySide's own teardown runs.

    Every test can pass and the process still die afterwards: PySide's
    interpreter-exit cleanup of a QApplication that hundreds of windows have
    passed through has crashed in shiboken's refcounting (bool_dealloc) on
    some Linux runners, turning a green suite into exit code 134. The report
    has been printed by now, so nothing is lost by skipping that teardown.
    """
    status = getattr(config, "_kudiostudio_exitstatus", None)
    if status is None:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)
