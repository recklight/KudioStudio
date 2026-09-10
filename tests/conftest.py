# -*- coding: utf-8 -*-
"""Shared fixtures. Qt runs headless so the suite works in CI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
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
