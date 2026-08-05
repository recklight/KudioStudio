# -*- coding: utf-8 -*-
"""Panels build, describe the call they will make, and actually make it."""
from __future__ import annotations

import numpy as np
import pytest


def test_effects_apply_gain_to_selection(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("gain")
    panel.gain_db.setValue(-6.0)
    assert "kudio.gain(y, db=-6.0)" in panel.call.text()

    session.set_selection(0.0, 0.5)
    before = session.y.copy()
    panel._apply()

    expected = before[:8000] * 10 ** (-6.0 / 20.0)
    assert np.allclose(session.y[:8000], expected, atol=1e-6)
    assert np.allclose(session.y[8000:], before[8000:])
    assert session.can_undo()


def test_effects_trim_silence_shortens_clip(session):
    from kudiostudio.panels.effects import EffectsPanel

    padded = np.concatenate([np.zeros(4000, dtype=np.float32), session.y])
    session.set_audio(padded, session.sr)

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("trim_silence")
    panel._apply()
    assert len(session.y) < len(padded)


def test_effects_split_reports_without_editing(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("split_on_silence")
    before = session.y.copy()
    panel._apply()

    assert np.allclose(session.y, before)
    assert "segment" in panel.split_result.text()


def test_effects_reports_failure_instead_of_raising(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("add_noise_snr")   # no noise file picked
    messages = []
    panel.status.connect(messages.append)
    panel._apply()

    assert messages and "failed" in messages[0]


def test_analysis_view_renders_both_modes(qapp, session):
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    assert "kudio.STFT" in view.call_hint.text()

    view.mode.setCurrentIndex(1)
    assert "kudio.mfcc" in view.call_hint.text()


def test_metrics_scores_against_the_original(session):
    from kudiostudio.panels.metrics import MetricsPanel

    panel = MetricsPanel(session)
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    panel.jobs.run = lambda *a, **k: True        # skip the optional-metric thread
    panel._run()

    labels = [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())]
    assert labels[:3] == ["SNR", "SI-SDR", "Segmental SNR"]


@pytest.mark.parametrize("text, expected", [
    ("-5, 0, 5", [-5, 0, 5]),
    ("0", [0]),
    ("3;6;9", [3, 6, 9]),
])
def test_parse_snr(text, expected):
    from kudiostudio.panels.synth import parse_snr
    assert parse_snr(text) == expected


def test_parse_snr_rejects_empty():
    from kudiostudio.panels.synth import parse_snr
    with pytest.raises(ValueError):
        parse_snr("  ")


def test_batch_converts_a_folder(qapp, tmp_path, tone):
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession
    import kudio

    src = tmp_path / "in"
    src.mkdir()
    for i in range(3):
        y, sr = kudio.file_load(tone)
        kudio.save_wave(src / f"clip{i}.wav", y, sr)
    out = tmp_path / "out"

    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(out))
    panel.rate.setCurrentText("8000")

    written, total, failed = panel._convert()
    assert (written, total, failed) == (3, 3, [])
    y, sr = kudio.file_load(next(out.glob("*.wav")))
    assert sr == 8000
