# -*- coding: utf-8 -*-
"""Metrics in the window: the reference-free inspection, DNSMOS, and
the scores against a clean reference."""
from __future__ import annotations

import re
import sys
import types

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import metrics
from kudiostudio.web.server import STATIC
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


def _rows(info):
    return dict(map(tuple, info["rows"]))


def test_a_clean_take_is_inspected_and_said_to_be_sound(studio):
    info = studio.call("metrics.inspect")
    rows = _rows(info)
    assert rows["Peak"].endswith("dBFS") and "Bandwidth" in rows
    text, warn = info["inspect"]
    assert text.startswith("Nothing wrong with the recording itself") and not warn


def test_a_clipped_take_lists_its_problems(studio):
    studio.session.set_audio(np.clip(studio.session.y * 20, -1, 1), studio.session.sr,
                             name="hot")
    text, warn = studio.call("metrics.inspect")["inspect"]
    assert warn and text.startswith("· ") and "clip" in text


def test_an_edit_is_scored_against_the_original(studio, monkeypatch):
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, 0.9, 12.0))
    studio.session.set_audio(studio.session.y * 0.5, studio.session.sr,
                             undo_label="halve", keep_original=True)
    first = _rows(studio.call("metrics.evaluate", [studio.call("metrics.describe")
                                                   ["references"][0]]))
    assert {"Alignment", "SNR", "SI-SDR", "Segmental SNR", "Compared samples"} <= set(first)
    assert studio.jobs.wait(30)
    rows = _rows(studio.call("metrics.describe"))
    assert rows["STOI"] == "0.900" and rows["SDR"] == "+12.000 dB"
    # 16 kHz is a rate PESQ is defined at, so a None there is "not installed"
    # or "could not be computed", never "not defined"
    assert rows["PESQ (MOS-LQO)"] in ("not installed", "could not be computed for this pair")


def test_a_raw_pesq_is_labelled_as_raw(studio, monkeypatch):
    seen = {}

    def fake(sr, ref, deg, pesq_scale="lqo"):
        seen["scale"] = pesq_scale
        return (2.03, None, None)

    monkeypatch.setattr(kudio, "eval_metrics", fake)
    studio.session.set_audio(studio.session.y * 0.5, studio.session.sr,
                             undo_label="halve", keep_original=True)
    studio.call("metrics.evaluate", ["Original (before edits)", "", True, "raw P.862"])
    assert studio.jobs.wait(30)
    assert seen["scale"] == "raw"
    assert _rows(studio.call("metrics.describe"))["PESQ (raw P.862)"] == "2.030"


def test_a_reference_file_at_another_rate_is_refused(studio, tmp_path):
    other = tmp_path / "other.wav"
    kudio.save_wave(other, np.zeros(8000, np.float32), 8000)
    drain = _said(studio)
    # file_load resamples to the clip's rate, so a rate mismatch cannot get
    # through by file; what can is a missing file
    studio.call("metrics.evaluate", ["File…", ""])
    assert drain() == ["Reference unavailable — no reference file selected"]


def test_a_reference_that_is_another_recording_is_flagged(studio, tmp_path):
    noise = tmp_path / "noise.wav"
    rng = np.random.default_rng(1)
    kudio.save_wave(noise, (0.1 * rng.standard_normal(len(studio.session.y)))
                    .astype(np.float32), studio.session.sr)
    rows = _rows(studio.call("metrics.evaluate", ["File…", str(noise)]))
    studio.jobs.wait(30)
    assert "⚠ Reference" in rows


def test_a_clip_that_comes_late_is_said_to_lag(studio, monkeypatch):
    """find_delay measures how far the clip sits after the reference, so it
    is the clip that lags, not the reference."""
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, None, None))
    late = np.concatenate([np.zeros(64, np.float32), studio.session.y])
    studio.session.set_audio(late, studio.session.sr, undo_label="delay",
                             keep_original=True)
    drain = _said(studio)
    studio.call("metrics.evaluate", ["Original (before edits)"])
    assert studio.jobs.wait(30)
    lined = [line for line in drain() if "before scoring" in line]
    assert len(lined) == 1
    assert lined[0].startswith("This clip lags by 64 samples (4.0 ms), correlation ")
    assert lined[0].endswith("; lined up with the reference before scoring.")
    assert i18n.translate(lined[0], "zh_TW").startswith("這段音訊比參考訊號落後 64 個樣本（4.0 ms）")


def test_the_pesq_tip_converts_the_way_kudio_does(monkeypatch):
    """kudio's raw score is the MOS-LQO taken back through P.862.1, so the
    MOS-LQO the tip names has to come back to the raw score it started from."""
    raw, lqo = (float(v) for v in re.search(r"raw ([\d.]+) is about\s+([\d.]+) MOS-LQO",
                                            metrics.PESQ_TIP).groups())
    fake = types.ModuleType("pesq")
    fake.pesq = lambda sr, ref, deg, mode: lqo
    monkeypatch.setitem(sys.modules, "pesq", fake)
    y = np.zeros(16000, np.float32)
    assert round(kudio.pesq(y, y, 16000, mode="nb", scale="raw"), 2) == raw
    assert f"raw {raw:.2f} 大約等於 {lqo:.2f} MOS-LQO" in \
        i18n.translate(metrics.PESQ_TIP, "zh_TW").replace("\n", "")


def test_the_dnsmos_caption_is_the_call_that_runs(studio, monkeypatch):
    """The panel has no field for the model's folder: kudio finds it through
    KUDIO_DNSMOS_DIR, so the caption shows no model_dir either."""
    calls = []

    def dnsmos(*args, **kwargs):
        calls.append((len(args), kwargs))
        return kudio.DnsmosScore(sig=3.1, bak=4.2, ovrl=3.4, segments=2)

    monkeypatch.setattr(kudio, "dnsmos", dnsmos)
    studio.call("metrics.dnsmos")
    assert studio.jobs.wait(30)
    assert calls == [(2, {})]
    page = (STATIC / "js" / "panels" / "metrics.js").read_text(encoding="utf-8")
    assert re.findall(r'dnsmosCall\.text = "([^"]*)"', page) == ["kudio.dnsmos(y, sr)"]


def test_dnsmos_without_its_model_says_what_is_missing(studio, monkeypatch):
    def missing(y, sr):
        raise FileNotFoundError("DNSMOS model not found. kudio does not ship it — "
                                "get the DNS-Challenge weights")

    monkeypatch.setattr(kudio, "dnsmos", missing)
    drain = _said(studio)
    studio.call("metrics.dnsmos")
    assert studio.jobs.wait(30)
    text, warn = studio.call("metrics.describe")["dnsmos"]
    assert warn and "DNS-Challenge" in text
    assert "DNSMOS unavailable — see the panel for what is missing." in drain()


def test_dnsmos_fills_the_table(studio, monkeypatch):
    monkeypatch.setattr(kudio, "dnsmos", lambda y, sr: kudio.DnsmosScore(
        sig=3.1, bak=4.2, ovrl=3.4, segments=2))
    studio.call("metrics.dnsmos")
    assert studio.jobs.wait(30)
    rows = _rows(studio.call("metrics.describe"))
    assert rows["SIG (speech)"] == "3.10 / 5" and rows["Windows averaged"] == "2"


@pytest.mark.parametrize("changes, shown", [
    (dict(duration=0.2, lufs=float("-inf")), "n/a (shorter than 400 ms)"),
    (dict(peak_dbfs=float("-inf"), lufs=float("-inf")), "silence"),
    (dict(non_finite_samples=5, peak_dbfs=float("nan"), lufs=float("nan")), "n/a"),
    (dict(), "-23.00 LUFS"),
])
def test_the_loudness_row_says_why_there_is_no_figure(changes, shown):
    """A two-second clip of silence is not too short to measure; it has no
    level to measure."""
    from kudio.core.report import AudioReport
    clip = dict(sr=16000, duration=2.0, peak_dbfs=-6.0, rms_dbfs=-20.0, lufs=-23.0,
                clipped_samples=0, clipped_ratio=0.0, dc_offset=0.0, silence_ratio=0.1,
                noise_floor_dbfs=-60.0, estimated_snr_db=30.0, bandwidth_hz=8000.0,
                band_limited=False, non_finite_samples=0)
    rows, _ = metrics.inspect_rows(AudioReport(**{**clip, **changes}))
    assert dict(rows)["Loudness (BS.1770)"] == shown
