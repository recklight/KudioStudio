# -*- coding: utf-8 -*-
"""The interface language: the table, the lookup, and a window built in it."""
import os
import string
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kudiostudio import i18n
from kudiostudio.locales import zh_TW


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
    translation, the menu shows those two characters instead."""
    for key, value in zh_TW.MESSAGES.items():
        assert "\\t" not in value, key
        if "\t" in key:
            assert value.split("\t")[1:] == key.split("\t")[1:], key


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
    assert i18n.tr("Added 4 file(s) from /data.") == "已從 /data 加入 4 個檔案。"
    assert i18n.tr("Track pitch\nNothing to undo.") == "追蹤音高\n沒有可復原的動作。"
    assert i18n.tr("no such sentence") == "no such sentence"
    # a value that is itself a phrase is translated too
    assert i18n.tr("Saved the whole clip") == "已儲存 整段音訊"


def test_a_window_in_traditional_chinese(tmp_path):
    """Built the way `kudiostudio --lang zh_TW` builds it, in a process of its
    own: the Qt setters stay wrapped for the life of a process."""
    script = tmp_path / "zh.py"
    script.write_text(textwrap.dedent(f'''
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
        from PySide6.QtWidgets import QApplication, QMessageBox
        from kudiostudio import i18n

        app = QApplication([])
        app.setOrganizationName("KudioTests")
        app.setApplicationName("I18n")
        assert i18n.install(app, "zh-TW") == "zh_TW"
        from kudiostudio.app import KudioStudio
        w = KudioStudio(reset=True)
        w.show()
        app.processEvents()

        menus = [a.text() for a in w.menuBar().actions()]
        assert menus[0] == "檔案(&F)", menus
        assert w.open_action.text() == "開啟(&O)…"
        assert [w.tools.tabText(i) for i in range(3)] == ["效果", "增強", "比較"]
        assert w.files_dock.windowTitle() == "檔案"

        pitch = next(p for p in w.panels if type(p).__name__ == "PitchPanel")
        assert pitch.run_button.text() == "追蹤音高"
        # shown translated, read back in English
        box = pitch.range_choice
        assert box.itemData(0, 0) == "語音（65–400 Hz）"
        assert box.currentText() == "Speech (65–400 Hz)"
        assert box.findText("Custom") == box.count() - 1

        w.show_message("Nothing to undo.")
        assert w.statusBar().currentMessage() == "沒有可復原的動作。"

        # the choice is saved; declining the restart keeps this window
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        w.set_language("en")
        assert w.settings.value("language") == "en"
        assert w.isVisible()
        w.settings.clear()
        w.close()
        print("ok")
    '''), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / "config"))
    done = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, timeout=120, cwd=tmp_path, env=env)
    assert done.returncode == 0, done.stderr[-3000:]
    assert "ok" in done.stdout
