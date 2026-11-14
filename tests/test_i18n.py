# -*- coding: utf-8 -*-
"""The interface language: the table, the lookup, and a window built in it."""
import json
import re
import shutil
import string
import subprocess
from pathlib import Path

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.locales import zh_TW
from kudiostudio.web.studio import Prefs, Studio

STATIC = Path(i18n.__file__).resolve().parent / "web" / "static"


@pytest.fixture
def chinese():
    i18n.load("zh_TW")
    yield
    i18n.load("en")


def _fields(text):
    return [f for _, f, _, _ in string.Formatter().parse(text) if f is not None]


def test_every_translation_takes_the_same_values():
    for key, value in zh_TW.MESSAGES.items():
        assert len(_fields(key)) == len(_fields(value)), (key, value)
        sample = [f"<{i}>" for i in range(len(_fields(key)))]
        formatted = value.format(*sample)
        assert all(s in formatted for s in sample), (key, "drops a value")


def test_menu_shortcuts_survive_translation():
    """A menu shows the key after a tab. Written as a backslash and a t in the
    translation, the menu shows those two characters instead; written as a
    backslash and a real tab, it shows the backslash."""
    for key, value in zh_TW.MESSAGES.items():
        assert "\\t" not in value, key
        assert value.count("\\") == key.count("\\"), key
        if "\t" in key:
            assert value.split("\t")[1:] == key.split("\t")[1:], key


def test_the_tables_parse_without_warnings():
    """An escape Python does not know, such as a backslash before a real tab,
    is kept as typed and only warned about -- the string still loads."""
    import importlib
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        source = Path(zh_TW.__file__).read_text(encoding="utf-8")
        compile(source, zh_TW.__file__, "exec")
    importlib.reload(zh_TW)


def test_normalize_reads_the_usual_spellings():
    for code in ("zh_TW", "zh-TW", "zh_tw", "zh-Hant-TW", "zh_HK"):
        assert i18n.normalize(code) == "zh_TW"
    for code in ("en", "en_US", None, "", "fr", "zh_CN"):
        assert i18n.normalize(code) == "en"


def test_english_is_left_alone():
    i18n.load("en")
    assert i18n.tr("Track pitch") == "Track pitch"


def test_exact_template_reordered_and_multiline(chinese):
    assert i18n.tr("Track pitch") == "追蹤音高"
    assert i18n.tr("Added 3 marker(s) for the voiced spans.") == "已為有聲區段新增 3 個標記。"
    assert i18n.tr("3800 Hz of 8000 Hz available") == "可用 8000 Hz 中的 3800 Hz"
    assert i18n.tr("Track pitch\nNothing to undo.") == "追蹤音高\n沒有可復原的動作。"
    assert i18n.tr("no such sentence") == "no such sentence"
    # a value that is itself a phrase is translated too
    assert i18n.tr("Saved the whole clip") == "已儲存 整段音訊"


@pytest.fixture
def window(tmp_path, monkeypatch):
    """Built the way `kudiostudio --lang zh_TW` builds it."""
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    studio = Studio(prefs=Prefs(tmp_path / "window.json"), language="zh-TW")
    yield studio
    studio.shutdown()
    i18n.load("en")


def test_a_window_in_traditional_chinese(window, tmp_path):
    """The page looks its own strings up in the table it is sent; what the
    Python side says reaches the status bar already translated."""
    assert window.language == "zh_TW"
    table = window.call("translations")
    assert table["language"] == "zh_TW"
    messages = table["messages"]

    # the File menu comes first and Open first in it, as the page lists them
    menus = (STATIC / "js" / "main.js").read_text(encoding="utf-8").split(
        "const MENUS = [", 1)[1]
    assert re.findall(r'title: "([^"]+)"', menus)[0] == "&File"
    assert re.findall(r'label: "([^"]+)"', menus)[0] == "&Open…"
    assert messages["&File"] == "檔案(&F)"
    assert messages["&Open…"] == "開啟(&O)…"
    tools = [entry["title"] for entry in window.call("rail")]
    assert tools[0] == "Inspect"
    assert [messages[title] for title in tools[1:4]] == ["效果", "增強", "比較"]
    assert "<h2 data-t>Files</h2>" in (STATIC / "index.html").read_text(encoding="utf-8")
    assert messages["Files"] == "檔案"

    pitch = (STATIC / "js" / "panels" / "pitch.js").read_text(encoding="utf-8")
    assert 'ui.button("Track pitch"' in pitch
    assert messages["Track pitch"] == "追蹤音高"
    # shown translated, sent back in English
    assert "translate: true" in pitch.split("ui.select(info.ranges", 1)[1].split(");", 1)[0]
    ranges = [r["label"] for r in window.call("pitch.describe")["ranges"]]
    assert ranges[0] == "Speech (65–400 Hz)"
    assert messages[ranges[0]] == "語音（65–400 Hz）"
    assert ranges[-1] == "Custom"

    events = window.events.subscribe()
    window.call("undo")
    said = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "message":
            said.append(event["text"])
    assert said == ["沒有可復原的動作。"]

    # the choice is saved, and the page switches without a restart
    assert window.call("set_language", ["en"]) == {
        "language": "en", "languages": dict(i18n.LANGUAGES), "messages": {}}
    assert Prefs(tmp_path / "window.json").get("language") == "en"


def test_quitting_mid_job_says_what_it_waits_for_in_chinese(window):
    """The line names the panels from their titles; the table holds the
    sentence around them, never a list of names it could not have seen."""
    import threading

    from kudiostudio.web import app as web_app

    class Window:
        def __init__(self):
            self.closed = threading.Event()

        def destroy(self):
            self.closed.set()

    events = window.events.subscribe()

    def said():
        out = []
        while not events.empty():
            event = events.get_nowait()
            if event["type"] == "message":
                out.append(event["text"])
        return out

    gate = threading.Event()
    assert window.jobs.run("batch", "converting", gate.wait, 20)
    first = Window()
    assert web_app.close_when_idle(window, first) is False
    assert said()[-1] == "將在批次完成後結束 — 執行中的工作無法中途停止。"

    assert window.jobs.run("synthesize", "Synthesizing…", gate.wait, 20)
    second = Window()
    assert web_app.close_when_idle(window, second) is False
    assert said()[-1] == "將在批次與合成完成後結束 — 執行中的工作無法中途停止。"

    # three or more: the names before the last are listed with 、
    assert window.jobs.run("enhance", "Denoising…", gate.wait, 20)
    third = Window()
    assert web_app.close_when_idle(window, third) is False
    assert said()[-1] == "將在批次、合成與增強完成後結束 — 執行中的工作無法中途停止。"
    gate.set()
    assert first.closed.wait(20) and second.closed.wait(20) and third.closed.wait(20)


def test_a_value_built_from_a_template_of_its_own_is_translated(chinese):
    """Batch's read-back line holds a whole clause as one of its values."""
    from kudiostudio.tools import batch
    check = batch.LoudnessCheck(target=-23.0, tolerance=1.0, ceiling=-1.0, checked=4)
    line = i18n.tr(batch.describe_loudness(check))
    assert line == "從磁碟讀回的響度：全部 4 個檔案在 -23 LUFS 的 ±1 LU 內，true peak 不超過 -1 dBTP。"


def test_a_panel_is_named_as_the_chinese_rail_names_it():
    """A line that sends you to another panel names it the way the rail you
    are looking at does."""
    from kudiostudio.web.panels import PANEL_CLASSES
    for title in ["Inspect"] + [cls.title for cls in PANEL_CLASSES]:
        if zh_TW.MESSAGES.get(title, title) == title:
            continue                        # Manifest is Manifest in both
        named = re.compile(rf"\b{re.escape(title)}\s*面板")
        for key, value in zh_TW.MESSAGES.items():
            assert not named.search(value), (title, key)


# ------------------------------------------------------ what kudio says itself

def _english_left(text):
    """Two English words in a row: a sentence left as it came. Units, metric
    names and figures stand alone and pass."""
    return re.search(r"[A-Za-z]{2,}[ ,]+[A-Za-z]{2,}", text)


def _kudio_sentences():
    """Every sentence of kudio's that the window shows as it comes, made by
    the kudio that is installed: the report's findings (Levels, Metrics,
    each take), an alignment, a room's measurement, DNSMOS, and a pitch
    track."""
    from kudio.core.align import Alignment
    from kudio.core.dnsmos import DnsmosScore
    from kudio.core.pitch import PitchTrack
    from kudio.core.report import AudioReport
    from kudio.core.room import Reverberation

    clip = dict(sr=16000, duration=4.0, peak_dbfs=-6.0, rms_dbfs=-20.0, lufs=-23.0,
                clipped_samples=0, clipped_ratio=0.0, dc_offset=0.0, silence_ratio=0.1,
                noise_floor_dbfs=-60.0, estimated_snr_db=30.0, bandwidth_hz=8000.0,
                band_limited=False, non_finite_samples=0)
    for changes in (dict(non_finite_samples=3), dict(peak_dbfs=float("-inf")),
                    dict(clipped_samples=120, clipped_ratio=0.0075), dict(peak_dbfs=1.2),
                    dict(peak_dbfs=-0.2), dict(peak_dbfs=-35.0), dict(dc_offset=0.05),
                    dict(band_limited=True, bandwidth_hz=3900.0),
                    dict(band_limited=True, bandwidth_hz=5300.0),
                    dict(silence_ratio=0.8), dict(estimated_snr_db=5.0)):
        yield from AudioReport(**{**clip, **changes}).problems()
    for delay, correlation in ((0, 0.98), (128, 0.98), (-1, 0.9), (64, -0.9)):
        yield str(Alignment(delay=delay, correlation=correlation, sr=16000, overlap=16000))
    nan = float("nan")
    for decay in (dict(t20=0.5, t30=0.5, edt=0.48, fit=0.99),
                  dict(t20=0.6, t30=0.6, edt=0.05, fit=0.99),
                  dict(t20=0.6, t30=0.6, edt=0.55, fit=0.5),
                  dict(t20=nan, t30=nan, edt=nan, fit=0.2),
                  dict(t20=0.5, t30=0.5, edt=nan, fit=0.99)):
        yield str(Reverberation(c50=9.4, c80=12.0, drr=3.0, sr=16000, **decay))
    for sig, bak, ovrl in ((4.3, 4.4, 4.2), (3.4, 3.6, 3.5), (2.2, 4.1, 2.5),
                           (3.9, 2.6, 1.8)):
        score = DnsmosScore(sig=sig, bak=bak, ovrl=ovrl, segments=12)
        yield score.summary()
        yield f"DNSMOS — {score}"
    frames = np.arange(100)
    for voiced in (frames < 51, np.zeros(100, dtype=bool)):
        hz = np.where(voiced, 179.6 + 0.01 * frames, np.nan)
        track = PitchTrack(hz, voiced, voiced.astype(float), frames * 0.01, 16000,
                           65.0, 400.0, 160)
        yield str(track)
        if voiced.any():
            yield f"Pitch: {track}"              # as the status bar says it
    yield from _synthesis_failures()


def _synthesis_failures():
    """The two ways a synthesis run fails before it writes anything, as the
    synthesize panel reports them ("Synthesis failed — <type>: <message>"):
    a clean folder with no WAV in it, and an output folder that holds one of
    the inputs, which kudio refuses to empty."""
    import tempfile

    import soundfile as sf
    from kudio.core.synth import Synthesizer

    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        clean, noise = root / "clean", root / "noise"
        clean.mkdir()
        noise.mkdir()
        sf.write(str(noise / "n.wav"), np.zeros(1600, np.float32), 16000)
        # the temporary folder's name changes from run to run; the test ids
        # are made from these sentences, so it is written as one fixed name
        def said(e):
            return f"{type(e).__name__}: {e}".replace(str(root), "corpus")

        try:
            Synthesizer(str(clean), str(noise), out_path=str(root / "out"))
        except Exception as e:                   # noqa: BLE001 - what the panel shows
            yield said(e)
        sf.write(str(clean / "c.wav"), np.zeros(1600, np.float32), 16000)
        quiet = root / "no-noise"
        quiet.mkdir()
        try:
            Synthesizer(str(clean), str(quiet), out_path=str(root / "out"))
        except Exception as e:                   # noqa: BLE001
            yield said(e)
        try:
            Synthesizer(str(clean), str(noise), out_path=str(clean)).syn(overwrite=True)
        except Exception as e:                   # noqa: BLE001
            yield said(e)


@pytest.mark.parametrize("sentence", sorted(set(_kudio_sentences())))
def test_what_kudio_says_is_shown_in_chinese(chinese, sentence):
    """kudio's wording can change in any release, and CI installs the newest
    one: a sentence reworded there fails here instead of turning up in
    English in the Chinese window."""
    shown = i18n.tr(sentence)
    assert shown != sentence and not _english_left(shown), shown


@pytest.mark.parametrize("language", ["en", "zh_TW"])
def test_no_menu_has_the_name_of_a_panel(language):
    """"View" and "Inspect" both came out 檢視 in Chinese: a menu and a panel
    of one name, and a page that says 檢視 could mean either."""
    from kudiostudio.web.panels import PANEL_CLASSES
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    menus = re.findall(r'^\s*\{ title: "(&[^"]+)", items:', main, re.M)
    assert len(menus) >= 5, menus
    panels = ["Inspect"] + [cls.title for cls in PANEL_CLASSES]

    def shown(text):
        return re.sub(r"\(&\w\)|&", "", i18n.tr(text)).strip()

    i18n.load(language)
    try:
        clash = {shown(m) for m in menus} & {shown(p) for p in panels}
    finally:
        i18n.load("en")
    assert not clash, clash


def test_the_metrics_panel_lists_kudio_findings_in_chinese(window, recording):
    """One finding a line, each behind a bullet. Translated as one text, the
    bullet went in with the first value: "· 6362" samples clipped."""
    y, sr = kudio.file_load(recording)
    hot = np.clip(y * 20 + 0.05, -1, 1).astype(np.float32)     # clipped, and off centre
    window.session.set_audio(hot, sr, name="hot")
    problems = kudio.audio_report(hot, sr).problems()
    assert len(problems) > 1

    text, warn = window.call("metrics.inspect")["inspect"]
    assert warn and text == "\n".join("· " + i18n.tr(p) for p in problems)
    assert re.match(r"· 有 \d+ 個樣本削波（佔整段的 [\d.]+%）— ", text), text
    assert not _english_left(text), text
    # a change of language mounts the panel again, which lists them in the new one
    window.call("set_language", ["en"])
    text, _ = window.call("metrics.describe")["inspect"]
    assert text == "\n".join("· " + p for p in problems)


def test_the_effects_panel_shows_the_room_reading_through_the_page_translation():
    """The reading comes from Python in English, as the panel's info() hands
    it over, so the page has to translate it as it shows it."""
    source = (STATIC / "js" / "panels" / "effects.js").read_text(encoding="utf-8")
    assert "irReadout.show(t(state.ir.measured)" in source


def test_a_measured_room_reads_in_chinese_on_the_page(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    from kudiostudio.tools import effects
    ir = kudio.rir(16000, rt60=0.5, drr_db=0.0, seed=1)
    path = tmp_path / "hall.wav"
    kudio.save_wave(path, np.concatenate([np.zeros(200, np.float32), ir]), 16000)
    response, _ = effects.read_response(path, 16000)
    text, _ = response.describe(True)
    script = tmp_path / "t.mjs"
    script.write_text(
        f"import {{ load, t }} from {json.dumps((STATIC / 'js' / 'i18n.js').as_uri())};\n"
        f"load({json.dumps(i18n.table('zh_TW'), ensure_ascii=False)});\n"
        f"process.stdout.write(t({json.dumps(text)}));\n",
        encoding="utf-8")
    done = subprocess.run([node, str(script)], capture_output=True, timeout=60,
                          encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert "直達聲" in done.stdout and "從那裡開始套用" in done.stdout, done.stdout
    assert not _english_left(done.stdout), done.stdout


def test_a_measured_room_reads_in_chinese(chinese, tmp_path):
    from kudiostudio.tools import effects
    ir = kudio.rir(16000, rt60=0.5, drr_db=0.0, seed=1)
    path = tmp_path / "hall.wav"
    kudio.save_wave(path, np.concatenate([np.zeros(200, np.float32), ir]), 16000)
    response, why = effects.read_response(path, 16000)
    text, _ = response.describe(True)
    shown = i18n.tr(text)
    assert "直達聲" in shown and "從那裡開始套用" in shown
    assert not _english_left(shown), shown


def test_the_page_translates_a_template_inside_a_template(tmp_path):
    """The page translates what the Python side hands it the same way: a
    value that is itself built from a template is translated too."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = tmp_path / "t.mjs"
    script.write_text(
        f"import {{ load, t }} from {json.dumps((STATIC / 'js' / 'i18n.js').as_uri())};\n"
        f"load({json.dumps(i18n.table('zh_TW'), ensure_ascii=False)});\n"
        f"process.stdout.write(t({json.dumps('lags by 128 samples (8.0 ms), correlation 0.982')}));\n",
        encoding="utf-8")
    done = subprocess.run([node, str(script)], capture_output=True, timeout=60,
                          encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "落後 128 個樣本（8.0 ms），相關係數 0.982"
