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


def test_the_evaluation_adds_hu_and_loizous_measures_and_lsd(studio, monkeypatch):
    """They need nothing beyond numpy, so they are there whatever is
    installed; the composite says what it is missing."""
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, None, None))
    monkeypatch.setattr(metrics, "installed",
                        lambda: {"PESQ": False, "STOI": False, "SDR": False})
    noisy = studio.session.y + 0.01 * np.random.default_rng(3).standard_normal(
        len(studio.session.y)).astype(np.float32)
    studio.session.set_audio(noisy, studio.session.sr, undo_label="noise",
                             keep_original=True)
    studio.call("metrics.evaluate", ["Original (before edits)"])
    assert studio.jobs.wait(30)
    rows = _rows(studio.call("metrics.describe"))
    for label in ("LLR", "Itakura-Saito", "WSS", "LSD"):
        assert re.fullmatch(r"\d+\.\d+", rows[label]), (label, rows[label])
    assert rows["Cepstral distance"].endswith(" dB")
    assert re.fullmatch(r"[+-]\d+\.\d{3} dB", rows["fwSegSNR"])
    assert rows["CSIG / CBAK / COVL"] == "needs PESQ, which is not installed"


def test_the_evaluation_adds_ncm_and_csii(studio, monkeypatch):
    """Intelligibility as STOI predicts it, from numpy and scipy alone, so
    the rows are there whatever is installed."""
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, None, None))
    monkeypatch.setattr(metrics, "installed",
                        lambda: {"PESQ": False, "STOI": False, "SDR": False})
    noisy = studio.session.y + 0.05 * np.random.default_rng(4).standard_normal(
        len(studio.session.y)).astype(np.float32)
    studio.session.set_audio(noisy, studio.session.sr, undo_label="noise",
                             keep_original=True)
    studio.call("metrics.evaluate", ["Original (before edits)"])
    assert studio.jobs.wait(30)
    rows = _rows(studio.call("metrics.describe"))
    for label in ("NCM",) + tuple(label for label, _ in metrics.CSII_LEVELS):
        assert re.fullmatch(r"[01]\.\d{3}", rows[label]), (label, rows[label])
        assert 0.0 < float(rows[label]) < 1.0, (label, rows[label])


def _syllables(sr: int, seconds: float = 3.0) -> np.ndarray:
    """A harmonic tone switched on and off twice a second, so the reference
    has loud, middling and quiet frames, as speech has."""
    t = np.arange(int(seconds * sr)) / sr
    voice = sum(np.sin(2 * np.pi * 180 * k * t) / k for k in range(1, 8))
    return (voice * np.clip(np.sin(2 * np.pi * 2 * t), 0.0, None) ** 2).astype(np.float32)


def test_ncm_and_csii_say_where_they_are_not_defined():
    voice = _syllables(44100)
    noisy = voice + 0.05 * np.random.default_rng(5).standard_normal(len(voice)).astype(
        np.float32)
    rows = dict(metrics.intelligibility_rows(voice, noisy, 44100))
    assert rows["NCM"] == "not defined at 44100 Hz (8 or 16 kHz only)"
    for label, _ in metrics.CSII_LEVELS:
        assert re.fullmatch(r"[01]\.\d{3}", rows[label]), (label, rows[label])
    assert metrics.intelligibility_rows(voice[:6000], noisy[:6000], 6000) == [
        ("NCM", "not defined at 6000 Hz (8 or 16 kHz only)"),
        ("CSII", "not defined at 6000 Hz (8 kHz or more)")]


def test_ncm_and_csii_with_no_figure_say_why(monkeypatch):
    sr = 16000
    voice = _syllables(sr)
    assert metrics.intelligibility_rows(np.zeros_like(voice), voice, sr) == [
        ("NCM", "n/a — the reference is silent"), ("CSII", "n/a — the reference is silent")]
    assert metrics.intelligibility_rows(voice[:300], voice[:300], sr) == [
        ("NCM", "n/a — too short to measure"), ("CSII", "n/a — too short to measure")]
    # a steady tone never drops 10 dB under its own RMS, so only the low level
    # is empty, and only its row says so
    tone = np.sin(2 * np.pi * 180 * np.arange(3 * sr) / sr).astype(np.float32)
    noisy = tone + 0.05 * np.random.default_rng(6).standard_normal(len(tone)).astype(
        np.float32)
    rows = dict(metrics.intelligibility_rows(tone, noisy, sr))
    assert rows["CSII (low level)"] == "n/a — no frame of the reference is at this level"
    assert re.fullmatch(r"[01]\.\d{3}", rows["CSII (high level)"])

    def broken(ref, deg, sr):
        raise RuntimeError("the coherence fell over")

    monkeypatch.setattr(kudio, "csii", broken)
    assert metrics.intelligibility_rows(voice, voice, sr)[-1] == (
        "CSII", "could not be computed for this pair")


def test_the_snr_rows_against_a_silent_reference_say_why():
    """kudio gives nan and a warning there, which the rows read rather than
    showing "+nan dB"."""
    voice = _syllables(16000)
    rows = dict(metrics.reference_rows(np.zeros_like(voice), voice, 16000))
    for label in ("SNR", "SI-SDR", "Segmental SNR"):
        assert rows[label] == "n/a — the reference is silent", (label, rows[label])
    assert rows["Compared samples"] == "48000 (3.00 s)"
    assert metrics._why_none([types.SimpleNamespace(
        message="pesq(): deg is digital silence throughout, and the pesq package gets "
                "NaN for it; scored nan")]) == "n/a — this clip is silent"


def test_a_clip_with_nan_in_it_is_reported_rather_than_scored(studio):
    """kudio refuses such a pair by raising; the panel says what is wrong
    before it gets that far."""
    broken = studio.session.y.copy()
    broken[100] = np.nan
    studio.session.set_audio(broken, studio.session.sr, undo_label="nan",
                             keep_original=True)
    said = _said(studio)
    studio.call("metrics.evaluate", ["Original (before edits)"])
    assert "This clip has NaN or inf samples; repair it before scoring." in said()


def test_the_composite_rows_name_the_pesq_inside_them(monkeypatch):
    y = np.zeros(16000, np.float32)
    monkeypatch.setattr(kudio, "composite", lambda ref, deg, sr: kudio.CompositeScore(
        3.104, 2.236, 2.6, 2.875, 0.3, 35.0, 4.9))
    rows = dict(metrics.composite_rows(y, y, 16000, {"PESQ": True}))
    assert rows == {"CSIG (signal)": "3.10 / 5", "CBAK (background)": "2.24 / 5",
                    "COVL (overall)": "2.60 / 5",
                    "PESQ inside them (wideband MOS-LQO)": "2.875"}
    assert dict(metrics.composite_rows(y, y, 8000, {"PESQ": True}))[
        "PESQ inside them (raw P.862)"] == "2.875"
    assert metrics.composite_rows(y, y, 44100, {"PESQ": True}) == [
        ("CSIG / CBAK / COVL", "not defined at 44100 Hz (8 or 16 kHz only)")]


def test_a_composite_that_fails_gives_its_row_a_reason(monkeypatch):
    """It runs in the job after PESQ, STOI and SDR, and its failure must not
    take their rows down with it."""
    y = np.zeros(16000, np.float32)

    def missing(ref, deg, sr):
        raise kudio.DependencyError("pesq", extra="pesq")

    def broken(ref, deg, sr):
        raise RuntimeError("the pesq package fell over")

    monkeypatch.setattr(kudio, "composite", missing)
    assert metrics.composite_rows(y, y, 16000, {"PESQ": True}) == [
        ("CSIG / CBAK / COVL", "needs PESQ, which is not installed")]
    monkeypatch.setattr(kudio, "composite", broken)
    assert metrics.composite_rows(y, y, 16000, {"PESQ": True}) == [
        ("CSIG / CBAK / COVL", "could not be computed for this pair")]


def test_at_another_rate_one_row_says_the_five_are_not_defined():
    y = np.random.default_rng(0).standard_normal(44100).astype(np.float32)
    rows = metrics.quality_rows(y, y + 0.1, 44100)
    assert rows[0] == ("LLR / IS / CEP / WSS / fwSegSNR",
                       "not defined at 44100 Hz (8 or 16 kHz only)")
    assert [label for label, _ in rows] == ["LLR / IS / CEP / WSS / fwSegSNR", "LSD"]


def test_a_measure_with_no_figure_says_why():
    sr = 16000
    voice = np.sin(2 * np.pi * 180 * np.arange(3 * sr) / sr).astype(np.float32)
    assert set(dict(metrics.quality_rows(np.zeros_like(voice), voice, sr)).values()) == {
        "n/a — the reference is silent"}
    assert dict(metrics.quality_rows(voice[:200], voice[:200], sr))["LSD"] == \
        "n/a — too short to measure"
    gated = voice.copy()
    gated[sr:2 * sr] = 0.0
    assert dict(metrics.blind_rows(gated, sr))["WADA-SNR"] == \
        "n/a — its pauses are digital silence"
    assert metrics.blind_rows(np.zeros(sr, np.float32), sr) == [
        ("SRMR (higher is drier)", "silence"), ("WADA-SNR", "silence")]


def test_srmr_and_wada_snr_fill_the_table(studio):
    drain = _said(studio)
    studio.call("metrics.blind")
    assert studio.jobs.wait(60)
    rows = _rows(studio.call("metrics.describe"))
    assert re.fullmatch(r"\d+\.\d\d", rows["SRMR (higher is drier)"])
    assert re.fullmatch(r"[+-]\d+\.\d dB", rows["WADA-SNR"])
    assert "Estimate complete." in drain()


def test_srmr_off_8_and_16_khz_is_measured_on_a_16_khz_copy(monkeypatch):
    seen = []

    def srmr(y, sr):
        seen.append((len(y), sr))
        return 5.0

    monkeypatch.setattr(kudio, "srmr", srmr)
    monkeypatch.setattr(kudio, "wada_snr", lambda y, sr: 12.0)
    y = np.random.default_rng(0).standard_normal(44100).astype(np.float32)
    rows = metrics.blind_rows(y, 44100)
    assert rows == [("SRMR (on a 16 kHz copy; higher is drier)", "5.00"),
                    ("WADA-SNR", "+12.0 dB")]
    assert seen == [(16000, 16000)]


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
    # the stand-in answers lqo whatever it gets, but kudio turns a silent
    # reference away before asking it, so the pair has to be audible
    y = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32)
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


def test_pesq_is_scored_on_the_pair_as_loaded_not_the_cut_one(studio, monkeypatch):
    """PESQ finds the delay itself, so the panel gives it the reference and
    the clip as loaded, for its own row and inside CSIG, CBAK and COVL; the
    rest get the pair cut to one length."""
    original = studio.session.y.copy()
    studio.session.set_audio(original[:-3200], studio.session.sr, undo_label="trim",
                             keep_original=True)
    handed = []

    def pesq(ref, deg, sr, mode="auto", scale="lqo"):
        handed.append((len(ref), len(deg), mode, scale))
        return 2.5

    monkeypatch.setattr(metrics, "installed",
                        lambda: {"PESQ": True, "STOI": False, "SDR": False})
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (1.0, None, None))
    monkeypatch.setattr(kudio, "pesq", pesq)
    monkeypatch.setattr(kudio, "composite", lambda ref, deg, sr, pesq=None: (
        kudio.CompositeScore(3.1, 2.2, 2.6, pesq, 0.3, 35.0, 4.9)))
    studio.call("metrics.evaluate", ["Original (before edits)", "", False, "MOS-LQO"])
    assert studio.jobs.wait(30)
    whole = (len(original), len(original) - 3200)
    # once: at 16 kHz the row's MOS-LQO is the score the composite takes
    assert handed == [(*whole, "auto", "lqo")]
    rows = _rows(studio.call("metrics.describe"))
    assert rows["PESQ (MOS-LQO)"] == "2.500"
    assert rows["PESQ inside them (wideband MOS-LQO)"] == "2.500"


def test_with_reference_keeps_eval_metrics_pesq_for_a_pair_lining_up_did_not_change(
        monkeypatch):
    y = np.random.default_rng(0).standard_normal(16000).astype(np.float32)
    monkeypatch.setattr(kudio, "eval_metrics", lambda sr, ref, deg, pesq_scale="lqo": (
        1.0, 0.9, 5.0))
    monkeypatch.setattr(kudio, "pesq", lambda *a, **k: pytest.fail("scored twice"))
    monkeypatch.setattr(metrics, "quality_rows", lambda ref, deg, sr: [])
    monkeypatch.setattr(metrics, "composite_rows", lambda *args: [])
    monkeypatch.setattr(metrics, "intelligibility_rows", lambda ref, deg, sr: [])
    result, rows = metrics.with_reference(16000, y, y, "lqo", {"PESQ": True}, whole=(y, y))
    assert result == (1.0, 0.9, 5.0) and rows == []


def test_pesq_gets_the_pair_as_loaded_when_the_clip_is_lined_up(studio, monkeypatch):
    """The path the docs single out: Line up on and a real delay, which
    shortens a side before anything else is scored."""
    original = studio.session.y.copy()
    late = np.concatenate([np.zeros(64, np.float32), original])
    studio.session.set_audio(late, studio.session.sr, undo_label="delay", keep_original=True)
    handed = []

    def pesq(ref, deg, sr, mode="auto", scale="lqo"):
        handed.append((len(ref), len(deg), mode, scale))
        return 2.5

    monkeypatch.setattr(metrics, "installed",
                        lambda: {"PESQ": True, "STOI": False, "SDR": False})
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (1.0, None, None))
    monkeypatch.setattr(kudio, "pesq", pesq)
    monkeypatch.setattr(kudio, "composite", lambda ref, deg, sr, pesq=None: (
        kudio.CompositeScore(3.1, 2.2, 2.6, pesq, 0.3, 35.0, 4.9)))
    studio.call("metrics.evaluate", ["Original (before edits)", "", True, "MOS-LQO"])
    assert studio.jobs.wait(30)
    # scored once: at 16 kHz the row's MOS-LQO is the score the composite takes
    assert handed == [(len(original), len(late), "auto", "lqo")]
    assert _rows(studio.call("metrics.describe"))["PESQ inside them (wideband MOS-LQO)"] == "2.500"


@pytest.mark.parametrize("scale, calls", [
    ("raw", [("auto", "raw")]),                    # the row is the composite's own
    ("lqo", [("auto", "lqo"), ("nb", "raw")]),     # narrowband MOS-LQO is not
])
def test_at_8_khz_the_composite_takes_raw_narrowband_pesq(monkeypatch, scale, calls):
    y = np.random.default_rng(1).standard_normal(8000).astype(np.float32)
    handed, given = [], {}

    def pesq(ref, deg, sr, mode="auto", scale="lqo"):
        handed.append((mode, scale))
        return 3.0

    def composite(ref, deg, sr, pesq=None):
        given["pesq"] = pesq
        return kudio.CompositeScore(3.1, 2.2, 2.6, pesq, 0.3, 35.0, 4.9)

    monkeypatch.setattr(kudio, "eval_metrics", lambda sr, ref, deg, pesq_scale="lqo": (
        1.0, None, None))
    monkeypatch.setattr(kudio, "pesq", pesq)
    monkeypatch.setattr(kudio, "composite", composite)
    monkeypatch.setattr(metrics, "quality_rows", lambda ref, deg, sr: [])
    monkeypatch.setattr(metrics, "intelligibility_rows", lambda ref, deg, sr: [])
    result, rows = metrics.with_reference(8000, y[:7200], y[:7200], scale, {"PESQ": True},
                                          whole=(y, y[:7200]))
    assert handed == calls and given == {"pesq": 3.0}
    assert dict(rows)["PESQ inside them (raw P.862)"] == "3.000"


def test_a_dnsmos_that_fails_at_once_leaves_evaluate_usable(studio, monkeypatch):
    """Without its models DNSMOS fails before the click's answer arrives, and
    that answer still says busy. Evaluate follows the job events, which by
    then have said the job is over."""
    import json
    import shutil
    import subprocess

    from test_enhance import PAGE

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    def missing(y, sr):
        raise FileNotFoundError("DNSMOS model not found")

    monkeypatch.setattr(kudio, "dnsmos", missing)
    before = studio.call("metrics.describe")
    studio.call("metrics.dnsmos")
    assert studio.jobs.wait(30)
    failed = studio.call("metrics.describe")
    # a path field has an input and a Browse button of its own
    script = (PAGE + "Fake.prototype.querySelector = function () { return new Fake('input'); };\n"
              "Object.defineProperty(Fake.prototype, 'button', {\n"
              "  get() { return this._browse ??= new Fake('browse'); } });\n"
              "Fake.prototype.setRows = function () {};\n"
              f"const info = {json.dumps(before)};\n"
              f"const failed = {json.dumps(failed)};\n"
              "let panel;\n"
              "const ctx = { ui, t: (text) => text, memory: {}, window() {},\n"
              "  call: async (name) => {\n"
              "    if (name === 'describe') return info;\n"
              "    panel.event({ info: failed });     // the job failed and said so\n"
              "    panel.busy(false);                 // and ended\n"
              "    return { ...failed, busy: true };  // then the click's answer\n"
              "  } };\n"
              f"const {{ mount }} = await import({json.dumps((STATIC / 'js/panels/metrics.js').as_uri())});\n"
              "panel = await mount(new Fake('root'), ctx);\n"
              "const [inspect, blind, dnsmos, evaluate] = made.filter((el) => el.kind === 'button');\n"
              "await dnsmos.given.onClick();\n"
              "console.log(JSON.stringify(evaluate.disabled));\n")
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) is False


def test_a_row_kudio_scored_nan_for_says_kudios_reason(monkeypatch):
    """LLR's nan for a frame too loud to square has a short reason of its
    own, and a nan whose reason the table does not know shows kudio's
    sentence rather than a bare n/a."""
    import warnings

    def too_loud(ref, deg, sr):
        warnings.warn("llr(): 3 of the 929 frames came out NaN, as a frame does when "
                      "it is too loud to square in float64; scored nan")
        return float("nan")

    def unknown(ref, deg, sr):
        warnings.warn("wss(): something new went wrong; scored nan")
        return float("nan")

    monkeypatch.setattr(kudio, "llr", too_loud)
    monkeypatch.setattr(kudio, "wss", unknown)
    voice = _syllables(16000)
    rows = dict(metrics.quality_rows(voice, voice, 16000))
    assert rows["LLR"] == "n/a — too loud to compute in float64"
    assert rows["WSS"] == "n/a — something new went wrong; scored nan"
    # in Chinese the sentence stays kudio's, and the words around it do not
    assert i18n.translate(rows["WSS"], "zh_TW") == "不適用 — something new went wrong; scored nan"


def test_the_composite_row_says_why_it_has_no_ratings(monkeypatch):
    import warnings

    def composite(ref, deg, sr, **kwargs):
        warnings.warn("composite(): of the 929 frames, 3 in LLR came out NaN, as a frame "
                      "does when it is too loud to square in float64")
        nan = float("nan")
        return kudio.CompositeScore(nan, nan, nan, 2.0, nan, 30.0, 5.0)

    monkeypatch.setattr(kudio, "composite", composite)
    voice = _syllables(16000)
    assert metrics.composite_rows(voice, voice, 16000, {"PESQ": True}, pesq=2.0) == [
        ("CSIG / CBAK / COVL", "n/a — too loud to compute in float64")]
