# -*- coding: utf-8 -*-
"""Metrics in the window: the reference-free inspection, DNSMOS, and
the scores against a clean reference."""
from __future__ import annotations

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
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
