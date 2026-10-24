# -*- coding: utf-8 -*-
"""The interface language: the table, the lookup, and a window built in it."""
import re
import string
from pathlib import Path

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


def test_a_value_built_from_a_template_of_its_own_is_translated(chinese):
    """Batch's read-back line holds a whole clause as one of its values."""
    from kudiostudio.tools import batch
    check = batch.LoudnessCheck(target=-23.0, tolerance=1.0, ceiling=-1.0, checked=4)
    line = i18n.tr(batch.describe_loudness(check))
    assert line == "從磁碟讀回的響度：全部 4 個檔案在 -23 LUFS 的 ±1 LU 內，true peak 不超過 -1 dBTP。"
