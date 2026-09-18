# -*- coding: utf-8 -*-
"""Scoring against a reference that does not start at the same sample.

Every metric in the panel compares sample *i* with sample *i*. Nothing in the
resulting number distinguishes "your processing was bad" from "these two are
eight milliseconds apart", and those mean opposite things.
"""
from __future__ import annotations

import numpy as np
import pytest

import kudio
from kudiostudio.panels.metrics import MetricsPanel
from kudiostudio.session import AudioSession

SR = 16000


def tone(seconds: float = 2.0, seed: int = 0) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float64) / SR
    voice = sum(np.sin(2 * np.pi * 180 * k * t) / k for k in range(1, 10)) / 6
    voice *= 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    noise = 0.01 * np.random.default_rng(seed).standard_normal(len(t))
    return (voice + noise).astype(np.float32)


@pytest.fixture
def panel(qapp, tmp_path):
    """A session holding a delayed copy of a reference file on disk."""
    clean = tone()
    reference = tmp_path / "clean.wav"
    kudio.save_wave(reference, clean, SR)

    session = AudioSession()
    session.set_audio(np.concatenate([np.zeros(128, dtype=np.float32), clean]),
                      SR, name="delayed")

    p = MetricsPanel(session)
    p.reference.setCurrentIndex(1)              # File…
    p._ref_path = reference
    p.jobs.run = lambda *a, **k: True           # skip the optional-metric thread
    yield p
    p.jobs.wait(3000)


def rows(panel) -> dict:
    return {panel.table.item(r, 0).text(): panel.table.item(r, 1).text()
            for r in range(panel.table.rowCount())}


def value(panel, name: str) -> float:
    return float(rows(panel)[name].split()[0])


# ------------------------------------------------------------ what it fixes

def test_the_offset_is_measured_and_named(panel):
    panel._run()
    assert "128 samples" in rows(panel)["Alignment"]
    assert "8.0 ms" in rows(panel)["Alignment"]


def test_lining_up_is_worth_tens_of_decibels(panel):
    """Same two signals, same metric; the only difference is whether anyone
    took the offset out first."""
    panel.align.setChecked(False)
    panel._run()
    misaligned = value(panel, "SI-SDR")

    panel.align.setChecked(True)
    panel._run()
    aligned = value(panel, "SI-SDR")

    assert aligned > misaligned + 20.0, (misaligned, aligned)
    assert aligned > 40.0, "a clip against itself should score very high"


def test_leaving_it_in_is_flagged_rather_than_silently_scored(panel):
    panel.align.setChecked(False)
    panel._run()

    warning = next(k for k in rows(panel) if "Not aligned" in k)
    assert "8.0 ms" in rows(panel)[warning]
    assert "measuring it" in rows(panel)[warning]


def test_aligned_scoring_compares_the_overlap(panel):
    panel._run()
    compared = int(rows(panel)["Compared samples"].split()[0])
    assert compared == len(tone())


# ----------------------------------------------------- the reference is wrong

def test_an_unrelated_reference_says_so(qapp, tmp_path):
    other = tmp_path / "other.wav"
    kudio.save_wave(other, (0.2 * np.random.default_rng(9)
                            .standard_normal(SR * 2)).astype(np.float32), SR)

    session = AudioSession()
    session.set_audio(tone(), SR, name="take")
    panel = MetricsPanel(session)
    panel.reference.setCurrentIndex(1)
    panel._ref_path = other
    panel.jobs.run = lambda *a, **k: True

    messages = []
    panel.status.connect(messages.append)
    panel._run()

    warning = next(k for k in rows(panel) if "Reference" in k and "⚠" in k)
    assert "unrelated" in rows(panel)[warning]
    assert any("does not look like" in m for m in messages)
    panel.jobs.wait(3000)


def test_an_unrelated_reference_is_not_lined_up_anyway(qapp, tmp_path):
    """Aligning two unrelated signals produces a confident-looking number
    about nothing, which is worse than a bad one."""
    other = tmp_path / "other.wav"
    kudio.save_wave(other, (0.2 * np.random.default_rng(11)
                            .standard_normal(SR * 2)).astype(np.float32), SR)

    session = AudioSession()
    session.set_audio(tone(), SR, name="take")
    panel = MetricsPanel(session)
    panel.reference.setCurrentIndex(1)
    panel._ref_path = other
    panel.jobs.run = lambda *a, **k: True
    panel._run()

    assert "Alignment" in rows(panel)
    compared = int(rows(panel)["Compared samples"].split()[0])
    assert compared == SR * 2, "nothing should have been trimmed"
    panel.jobs.wait(3000)


# --------------------------------------------------------- the ordinary case

def test_an_edit_that_keeps_the_timing_reports_no_offset(qapp, tone_session):
    tone_session.set_audio(tone_session.y * 0.5, tone_session.sr,
                           undo_label="gain", keep_original=True)
    panel = MetricsPanel(tone_session)
    panel.jobs.run = lambda *a, **k: True
    panel._run()

    assert "already aligned" in rows(panel)["Alignment"]
    assert not any("Not aligned" in k for k in rows(panel))
    panel.jobs.wait(3000)


@pytest.fixture
def tone_session(qapp):
    session = AudioSession()
    session.set_audio(tone(), SR, name="take")
    return session
