# -*- coding: utf-8 -*-
"""The Survey panel: audio_report and friends over a folder of takes."""
from __future__ import annotations

import inspect
import math
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import kudio
from kudiostudio.tools import survey
from kudiostudio.web.studio import Prefs, Studio

SR = 16000


def _voice(seconds=3.0, seed=0, level=0.3):
    """Harmonic syllables, the way the tests elsewhere fake speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    y = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 12))
    y = y * (np.sin(2 * np.pi * 2 * t) > 0) * level / 3
    return (y + 0.002 * rng.standard_normal(t.size)).astype(np.float32)


@pytest.fixture
def corpus(tmp_path):
    """Two folders: a clean take, a clipped one and a hum in the first; in
    the second, one recording cut into two files, and a file that is not
    audio at all."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    sf.write(str(a / "clean.wav"), _voice(), SR)
    sf.write(str(a / "hot.wav"), np.clip(10 * _voice(seed=1), -1, 1), SR)
    t = np.arange(3 * SR) / SR
    hum = sum(0.05 / k * np.sin(2 * np.pi * 50 * k * t) for k in range(1, 8))
    sf.write(str(a / "hum.wav"), (hum + 0.002 * np.random.default_rng(2).standard_normal(t.size))
             .astype(np.float32), SR)
    whole = 0.05 * np.random.default_rng(3).standard_normal(4 * SR)
    sf.write(str(b / "part_1.wav"), whole[:2 * SR], SR, subtype="FLOAT")
    sf.write(str(b / "part_2.wav"), whole[2 * SR:], SR, subtype="FLOAT")
    # the second half opened again with the last 100 samples of the first
    sf.write(str(b / "part_3.wav"), whole[2 * SR - 100:3 * SR], SR, subtype="FLOAT")
    (b / "broken.wav").write_bytes(b"not a wave file")
    return tmp_path


def _rows(root):
    files = sorted(str(p) for p in root.rglob("*.wav"))
    return {p.split("\\")[-1].split("/")[-1]: r
            for p, r in zip(files, survey.run_survey(files, voice=True, seams=True))}


def test_each_file_gets_a_row_and_a_bad_one_says_why(corpus):
    rows = _rows(corpus)
    assert set(rows) == {"clean.wav", "hot.wav", "hum.wav", "part_1.wav", "part_2.wav",
                         "part_3.wav", "broken.wav"}
    assert rows["broken.wav"].error and not rows["clean.wav"].error
    assert rows["hot.wav"].clipped > 0 and survey.matches(rows["hot.wav"], "clipped")
    assert not survey.matches(rows["clean.wav"], "clipped")


@pytest.mark.skipif(not hasattr(kudio, "tonal_lines"), reason="the kudio installed has no tonal_lines")
def test_a_hum_is_found_as_lines_and_a_comb(corpus):
    row = _rows(corpus)["hum.wav"]
    assert row.lines >= 5 and row.comb_hz == pytest.approx(50.0, abs=0.1)
    assert survey.matches(row, "narrowband lines")


def test_a_take_cut_from_one_stream_carries_on(corpus):
    """part_2 starts where part_1 stopped, so the step between them is like
    any step inside the end of part_1."""
    rows = _rows(corpus)
    assert math.isnan(rows["part_1.wav"].join_ratio), "the first of its folder"
    assert rows["part_2.wav"].join_ratio < 5 and not rows["part_2.wav"].seam


def test_a_file_that_opens_with_the_end_of_the_one_before_is_a_repeat():
    whole = 0.05 * np.random.default_rng(4).standard_normal(4000)
    ratio, repeats = survey.join(whole[:3000], whole[2900:])
    assert repeats
    ratio, repeats = survey.join(whole[:3000], whole[3000:])
    assert not repeats and ratio < 5


def test_a_jump_between_takes_is_a_seam():
    rng = np.random.default_rng(5)
    quiet = 0.001 * rng.standard_normal(3000)
    loud = 0.5 + 0.001 * rng.standard_normal(3000)
    ratio, repeats = survey.join(quiet, loud)
    assert ratio > survey.SEAM_RATIO and not repeats


def test_the_voice_share_stands_above_its_scrambled_copy_on_speech():
    """On the scrambled copy the syllables are gone and the share drops;
    on noise the two are alike."""
    speech, copy = survey.voice_share(_voice(seconds=4.0), SR)
    assert speech > copy + survey.VOICE_MARGIN
    noise = (0.1 * np.random.default_rng(6).standard_normal(4 * SR)).astype(np.float32)
    a, b = survey.voice_share(noise, SR)
    assert not a > b + survey.VOICE_MARGIN


@pytest.mark.skipif("band" not in inspect.signature(kudio.vad).parameters,
                    reason="the kudio installed judges the whole band")
def test_anything_that_rises_and_falls_stands_above_its_copy_not_only_speech():
    """The copy keeps the spectrum and loses the rise and fall in time, so
    noise switched on and off every 0.3 s counts as much as a voice does:
    the column picks takes out to listen to, it does not find voices."""
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(7)
    t = np.arange(4 * SR) / SR
    band = sosfilt(butter(4, (300, 3000), "bandpass", fs=SR, output="sos"),
                   rng.standard_normal(t.size))
    switched = (0.3 * band * (np.floor(t / 0.3) % 2 == 0)).astype(np.float32)
    share, copy = survey.voice_share(switched, SR)
    row = survey.SurveyRow(path="", folder="", voice=share, voice_control=copy)
    assert share > copy + 0.3 and row.rises_and_falls


def test_the_join_is_not_judged_after_digital_silence():
    """Out of a stretch of zeros every step reads as infinitely many median
    steps; that says nothing about a seam."""
    rng = np.random.default_rng(8)
    ratio, repeats = survey.join(np.zeros(3000), 0.1 * rng.standard_normal(3000))
    assert math.isnan(ratio) and not repeats
    row = survey.SurveyRow(path="", folder="", join_ratio=ratio)
    assert not row.seam and survey.cells(row, "x")[11] == "—"


def test_a_repeat_is_found_however_common_its_first_value_is():
    """A coarse grid makes the first sample of the next take a value the
    end of the one before holds tens of thousands of times."""
    rng = np.random.default_rng(9)
    previous = np.round(rng.standard_normal(200_000) * 1.5) / 64     # a few levels only
    head = previous[150_000:150_000 + 2 * survey.REPEAT_SAMPLES].copy()
    head[0] = previous[0]
    previous[150_000] = previous[0]
    assert np.count_nonzero(previous == head[0]) > 10_000
    ratio, repeats = survey.join(previous[:-1000], head)
    assert repeats


def test_the_scrambled_copy_keeps_the_spectrum():
    y = _voice()
    copy = survey.scrambled(y)
    assert np.allclose(np.abs(np.fft.rfft(copy)), np.abs(np.fft.rfft(y)), rtol=1e-3, atol=1e-3)
    assert np.corrcoef(copy, y)[0, 1] < 0.1


def test_the_folder_summary_counts_each_filter(corpus):
    files = sorted(str(p) for p in corpus.rglob("*.wav"))
    lines = {line["folder"].replace("\\", "/").split("/")[-1]: line
             for line in survey.folder_summary(survey.run_survey(files))}
    assert lines["a"]["files"] == 3 and lines["a"]["clipped"] == 1
    assert lines["b"]["files"] == 4 and lines["b"]["failed"] == 1


def test_the_csv_holds_every_row(corpus, tmp_path):
    import csv
    files = sorted(str(p) for p in corpus.rglob("*.wav"))
    rows = survey.run_survey(files)
    out = tmp_path / "out.csv"
    survey.write_csv(rows, str(out))
    with open(out, encoding="utf-8-sig") as f:
        read = list(csv.DictReader(f))
    assert len(read) == len(files) and set(survey.CSV_FIELDS) == set(read[0])


# ----------------------------------------------------------------- panel

@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda *a, **k: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


def _wait(studio, owner="survey", timeout=60):
    end = time.time() + timeout
    while studio.jobs.busy(owner) and time.time() < end:
        time.sleep(0.05)


def test_the_panel_surveys_a_folder_filters_and_fills_the_files_list(studio, corpus):
    studio.call("survey.run", [str(corpus)])
    _wait(studio)
    info = studio.call("survey.describe")
    assert info["total"] == 7 and info["counts"]["clipped"] == 1
    assert "7 files" in info["summary"]
    shown = studio.call("survey.show", ["clipped"])
    assert [p.replace("\\", "/").split("/")[-1] for p in shown["paths"]] == ["hot.wav"]
    state = studio.call("survey.to_files")
    assert [f["name"] for f in state["files"]] == ["hot.wav"]
    assert state["name"] == "hot.wav"


def test_a_row_opens_its_file(studio, corpus):
    studio.call("survey.run", [str(corpus / "a")])
    _wait(studio)
    state = studio.call("survey.open_row", [1])
    assert state["name"] == "hot.wav" and state["path"] in [f["path"] for f in state["files"]]


def test_the_files_list_is_surveyed_when_no_folder_is_named(studio, corpus):
    studio.call("open_files", [[str(corpus / "a" / "clean.wav")]])
    studio.call("survey.run", [""])
    _wait(studio)
    assert studio.call("survey.describe")["total"] == 1


def test_the_export_writes_the_rows_and_a_line_per_folder(studio, corpus, tmp_path):
    studio.call("survey.run", [str(corpus)])
    _wait(studio)
    out = studio.call("survey.export", [str(tmp_path / "report")])
    assert out["written"] and out["path"].endswith("report.csv")
    assert (tmp_path / "report_folders.csv").is_file()


def _names(paths):
    return [p.replace("\\", "/").split("/")[-1] for p in paths]


def test_the_table_sorts_by_a_column_and_turns_round_on_a_second_click(studio, corpus):
    studio.call("survey.run", [str(corpus)])
    _wait(studio)
    # Seconds: 3, 3, 3 in a, 2, 2, 2.0063 in b; the broken file last either way
    first = studio.call("survey.sort", [2])
    assert first["sort"] == [2, True]
    seconds = [float(r[2]) for r in first["rows"][:-1]]
    assert seconds == sorted(seconds, reverse=True)
    assert _names(first["paths"])[-1] == "broken.wav"
    second = studio.call("survey.sort", [2])
    assert second["sort"] == [2, False]
    assert [float(r[2]) for r in second["rows"][:-1]] == sorted(seconds)
    assert _names(second["paths"])[-1] == "broken.wav"
    # the names A to Z on the first click
    by_name = studio.call("survey.sort", [0])
    assert by_name["sort"] == [0, False]
    assert _names(by_name["paths"])[:3] == ["clean.wav", "hot.wav", "hum.wav"]


def test_a_sorted_row_opens_the_file_it_shows_and_the_list_keeps_the_order(studio, corpus):
    studio.call("survey.run", [str(corpus / "a")])
    _wait(studio)
    shown = studio.call("survey.sort", [4])          # Clipped, largest first
    assert _names(shown["paths"])[0] == "hot.wav"
    assert studio.call("survey.open_row", [0])["name"] == "hot.wav"
    state = studio.call("survey.to_files")
    assert [f["name"] for f in state["files"]] == _names(shown["paths"])


def test_rows_with_nothing_in_a_column_go_last():
    rows = [survey.SurveyRow(path="b", folder="", grid_bits=12.0),
            survey.SurveyRow(path="a", folder=""),
            survey.SurveyRow(path="c", folder="", grid_bits=14.0),
            survey.SurveyRow(path="d", folder="", error="no audio")]
    for descending, order in ((False, "bcad"), (True, "cbad")):
        assert "".join(r.path for r in survey.sort_rows(rows, 6, descending)) == order
    with pytest.raises(ValueError):
        survey.sort_rows(rows, len(survey.COLUMNS))


# ------------------------------------------------- folder spectra, timeline

def _tone_folders(root):
    """Folder hi holds three takes with a 3 kHz tone over noise, folder lo
    three of the same noise alone; one take in hi is 20 dB louder."""
    rng = np.random.default_rng(5)
    t = np.arange(2 * SR) / SR
    for folder, tone in (("hi", 0.1), ("lo", 0.0)):
        (root / folder).mkdir()
        for k in range(3):
            y = 0.01 * rng.standard_normal(t.size) + tone * np.sin(2 * np.pi * 3000 * t)
            if folder == "hi" and k == 2:
                y = 10 * y
            sf.write(str(root / folder / f"take_{k}.wav"), y.astype(np.float32), SR,
                     subtype="FLOAT")
    return sorted(str(p) for p in root.rglob("*.wav"))


def test_a_folder_spectrum_shows_what_its_takes_hold(tmp_path):
    rows = survey.run_survey(_tone_folders(tmp_path), lines=False, seams=False)
    for statistic in ("mean", "median"):
        found = {Path(f.folder).name: f for f in survey.folder_spectra(rows, statistic)}
        assert set(found) == {"hi", "lo"} and found["hi"].files == 3
        bin_3k = int(round(3000 / (SR / survey.SPECTRUM_SIZE)))
        lift = found["hi"].db[bin_3k] - found["lo"].db[bin_3k]
        assert lift > 30
        # away from the tone the two folders hold the same noise; the mean
        # carries the loud take's 20 dB, 10 log10(102 / 3) = 15.3 over three
        # takes, and the median does not. With one take far up, the median
        # of three is the larger of the other two in each bin, which sits
        # about 0.7 dB over either on 14 Welch segments
        away = slice(int(500 / (SR / survey.SPECTRUM_SIZE)), int(1500 / (SR / survey.SPECTRUM_SIZE)))
        gap = float(np.median(found["hi"].db[away] - found["lo"].db[away]))
        if statistic == "mean":
            assert gap == pytest.approx(10 * math.log10(102 / 3), abs=0.5)
        else:
            assert 0 < gap < 1.5
    with pytest.raises(ValueError):
        survey.folder_spectra(rows, "mode")


def test_the_band_level_of_the_whole_band_is_the_rms_level(tmp_path):
    rows = survey.run_survey(_tone_folders(tmp_path), lines=False, seams=False)
    for row in rows:
        assert survey.band_level(row, 0, 0) == pytest.approx(row.rms_dbfs, abs=0.2)
    hot = next(r for r in rows if r.path.endswith(str(Path("hi") / "take_0.wav")))
    # the tone at 0.1 is -23 dB; the noise in 2.9-3.1 kHz is 40 dB under
    assert survey.band_level(hot, 2900, 3100) == pytest.approx(20 * math.log10(0.1 / math.sqrt(2)),
                                                                abs=0.2)
    assert math.isnan(survey.band_level(survey.SurveyRow(path="x", folder=""), 0, 0))


def _take(seconds=2.0):
    return survey.SurveyRow(path="", folder="f", sr=SR, seconds=seconds, rms_dbfs=-20.0)


def test_the_timeline_reads_the_names_and_finds_the_gaps():
    names = ["rec_20261111_100000.wav", "rec_20261111_100002.wav", "rec_20261111_100004.wav",
             # four seconds missing: two whole takes
             "rec_20261111_100010.wav", "rec_20261111_100012_0007.wav", "notes.wav"]
    rows = []
    for n in names:
        row = _take()
        row.path = str(Path("f") / n)
        rows.append(row)
    line = survey.timeline(rows, "RMS")
    assert line.untimed == 1 and len(line.series) == 1
    xs = line.series[0]["x"]
    assert [x - xs[0] for x in xs] == [0, 2, 4, 10, 12]
    assert line.gaps == [(xs[0] + 6, xs[0] + 10)]
    assert line.series[0]["y"] == [-20.0] * 5


def test_a_gap_needs_a_whole_take_and_two_seconds():
    rows = []
    # whole-second names put one-second takes a second apart, or two; from
    # 4 to 7 two seconds are missing, not more, and from 7 to 11 three are
    for n, second in enumerate((0, 1, 3, 4, 7, 11)):
        row = _take(seconds=1.0)
        row.path = f"t_20261111_1200{second:02d}_{n}.wav"
        rows.append(row)
    line = survey.timeline(rows, "RMS")
    start = line.series[0]["x"][0]
    assert line.gaps == [(start + 8, start + 11)]


def test_the_timeline_takes_a_pattern_or_the_time_the_file_was_written(tmp_path):
    row = _take()
    row.path = "site3_2026-11-11T143111.wav"
    ordered = r"(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})(\d{2})"
    assert survey.timeline([row], pattern=ordered).series[0]["x"] == [
        survey.name_time("x_20261111_143111.wav")]
    # the groups go year first: day.month.year read in that order is no date
    row.path = "site3-11.11.2026-14h31m11s.wav"
    day_first = r"(\d{2})\.(\d{2})\.(\d{4})-(\d{2})h(\d{2})m(\d{2})s"
    assert survey.timeline([row], pattern=day_first).untimed == 1
    for short in (r"(\d{2})\.(\d{2})\.(\d{4})", r"nothing (\d)"):
        with pytest.raises(ValueError, match="six groups"):
            survey.timeline([row], pattern=short)
    # written at 14:31:13 local time, two seconds long: began at 14:31:11
    written = datetime(2026, 11, 11, 14, 31, 13).timestamp()
    row.modified = written
    line = survey.timeline([row], clock="modified")
    assert line.series[0]["x"] == [survey.name_time("x_20261111_143111.wav")]
    with pytest.raises(ValueError):
        survey.timeline([row], clock="atime")
    with pytest.raises(re.error):
        survey.timeline([row], pattern="(")


def test_the_panel_draws_the_folder_spectra_and_the_timeline(studio, tmp_path):
    files = _tone_folders(tmp_path)
    studio.call("open_files", [files])
    studio.call("survey.run", [""])
    _wait(studio)
    got = studio.call("survey.spectra", ["mean", ""])
    assert len(got["series"]) == 2 and got["unit"] == "dB re full scale per Hz"
    assert all(v is None or math.isfinite(v) for s in got["series"] for v in s["y"])
    less = studio.call("survey.spectra", ["mean", got["keys"][1]])
    assert len(less["series"]) == 1 and less["unit"] == "dB"
    assert less["reference"] == got["names"][1]
    with pytest.raises(ValueError):
        studio.call("survey.spectra", ["mean", "nowhere|16000"])
    # the takes have no time in their names; the time they were written serves
    line = studio.call("survey.timeline", ["Band level", "modified", "", 2900, 3100])
    assert sum(len(s["x"]) for s in line["series"]) == 6
    assert line["summary"].startswith("6 takes on the clock")
    assert studio.call("survey.timeline", ["RMS", "name", ""])["summary"] == (
        "0 takes on the clock, no gap; 6 without a time.")
    state = studio.call("survey.open_path", [line["series"][0]["paths"][0]])
    assert state["path"] == line["series"][0]["paths"][0]
    with pytest.raises(ValueError):
        studio.call("survey.open_path", [str(tmp_path / "elsewhere.wav")])


# ------------------------------------------------------------- the detectors

def test_silero_is_offered_only_where_it_is_installed(studio, monkeypatch):
    monkeypatch.setattr(survey, "silero_available", lambda: False)
    assert studio.call("survey.describe")["detectors"] == [["kudio", "kudio.speech_ratio"]]
    with pytest.raises(ValueError, match="no voice detector 'silero'"):
        studio.call("survey.run", ["", 3, True, True, False, "silero"])
    monkeypatch.setattr(survey, "silero_available", lambda: True)
    assert [d for d, _ in studio.call("survey.describe")["detectors"]] == ["kudio", "silero"]
    with pytest.raises(ValueError, match="no voice detector"):
        survey.voice_share(_voice(), SR, "webrtc")


def test_the_rises_and_falls_filter_is_the_share_over_the_copy(corpus, tmp_path):
    rows = [survey.SurveyRow(path="a", folder="f", voice=0.40, voice_control=0.05),
            survey.SurveyRow(path="b", folder="f", voice=0.12, voice_control=0.05)]
    assert [survey.matches(r, "rises and falls") for r in rows] == [True, False]
    out = tmp_path / "folders.csv"
    survey.write_summary(rows, str(out))
    head = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert "rises and falls" in head and "voice" not in head


@pytest.mark.skipif(not survey.silero_available(), reason="silero-vad is not installed")
@pytest.mark.parametrize("sr", [16000, 48000, 192000])
def test_silero_hears_steady_noise_as_no_speech_at_any_rate(sr):
    """At every rate the take is brought to 16 kHz and 100-4000 Hz before
    the model hears it; steady noise and its copy both come out near zero."""
    rng = np.random.default_rng(10)
    noise = (0.05 * rng.standard_normal(2 * sr)).astype(np.float32)
    share, copy = survey.voice_share(noise, sr, "silero")
    assert 0.0 <= share < 0.2 and 0.0 <= copy < 0.2
    assert math.isnan(survey.silero_ratio(noise[:sr // 100], sr)), "less than one window"
    assert survey.silero_ratio(np.zeros(sr, np.float32), sr) == 0.0
