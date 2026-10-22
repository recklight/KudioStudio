# -*- coding: utf-8 -*-
"""Panels from other packages: found, put on the rail, or left out and named."""
from __future__ import annotations

import sys
import types
from importlib import metadata

import pytest

from kudiostudio import i18n, icons, plugins
from kudiostudio.panels import PANEL_CLASSES, Panel

MODULE = "kudiostudio_test_plugin"


class NotesPanel(Panel):
    title = "Notes"
    icon = "test-notes"
    icon_svg = '<path d="M6 4h12v16H6z"/><path d="M9 9h6M9 13h6"/>'
    messages = {"zh_TW": {"Notes": "筆記"}}


class Unbuildable(Panel):
    title = "Unbuildable"

    def __init__(self, session, parent=None):
        raise RuntimeError("no device")


def not_a_panel():
    return None


@pytest.fixture
def plugin_module(monkeypatch):
    """A module the fake entry points can name, and a clean slate after."""
    module = types.ModuleType(MODULE)
    module.NotesPanel = NotesPanel
    module.Unbuildable = Unbuildable
    module.not_a_panel = not_a_panel
    monkeypatch.setitem(sys.modules, MODULE, module)
    monkeypatch.delenv(plugins.DISABLE_ENV, raising=False)
    extra = {k: list(v) for k, v in i18n._extra.items()}
    yield module
    icons._SHAPES.pop(NotesPanel.icon, None)
    icons.NAMES = tuple(icons._SHAPES)
    i18n._extra.clear()
    i18n._extra.update(extra)
    i18n.load("en")


def entry_points(monkeypatch, **targets):
    found = [metadata.EntryPoint(name, f"{MODULE}:{attr}" if ":" not in attr else attr,
                                 plugins.GROUP)
             for name, attr in targets.items()]
    monkeypatch.setattr(plugins, "_entry_points", lambda: found)


@pytest.fixture
def window_with(qapp):
    from kudiostudio.app import KudioStudio
    built = []

    def build():
        w = KudioStudio(reset=True)
        built.append(w)
        return w

    yield build
    for w in built:
        w.settings.clear()
        w.close()


def test_a_plugin_panel_joins_the_rail_after_the_built_in_ones(
        plugin_module, monkeypatch, window_with):
    entry_points(monkeypatch, notes="NotesPanel")
    window = window_with()

    assert isinstance(window.panels[-1], NotesPanel)
    assert window.built_in_panels == len(PANEL_CLASSES)
    buttons = window.tool_buttons.buttons()
    assert len(buttons) == len(PANEL_CLASSES) + 1
    assert buttons[-1].text() == "Notes"
    assert not buttons[-1].icon().isNull()
    assert "test-notes" in icons.NAMES
    # and below a rule, so it is clear it did not come with the window
    from PySide6.QtWidgets import QFrame
    assert window.findChild(QFrame, "railRule") is not None

    buttons[-1].click()
    assert window.tools.currentWidget() is window.panels[-1]
    assert [p.error for p in window.plugins] == [""]
    # it talks to the window like any panel
    window.panels[-1].status.emit("hello from a plugin")
    assert window.statusBar().currentMessage() == "hello from a plugin"


def test_the_window_opens_without_the_plugins_that_fail(
        plugin_module, monkeypatch, window_with):
    entry_points(monkeypatch, gone="kudiostudio_no_such_module:Panel",
                 function="not_a_panel", unbuildable="Unbuildable",
                 notes="NotesPanel")
    window = window_with()

    assert [type(p) for p in window.panels[window.built_in_panels:]] == [NotesPanel]
    errors = {p.name: p.error for p in window.plugins}
    assert errors["notes"] == ""
    assert errors["gone"].startswith("ModuleNotFoundError")
    assert "is not a Panel subclass" in errors["function"]
    assert errors["unbuildable"] == "RuntimeError: no device"
    message = window.statusBar().currentMessage()
    for name in ("gone", "function", "unbuildable"):
        assert f"Plugin {name} was left out" in message


def test_switching_plugins_off_finds_none(monkeypatch):
    def fail():
        raise AssertionError("looked for entry points")

    monkeypatch.setattr(plugins, "_entry_points", fail)
    monkeypatch.setenv(plugins.DISABLE_ENV, "1")
    assert plugins.discover() == []


def test_the_entry_point_group_is_read_on_this_python(monkeypatch):
    """3.9 hands back a dict of lists, 3.10 and later a selectable set."""
    monkeypatch.delenv(plugins.DISABLE_ENV, raising=False)
    assert isinstance(plugins._entry_points(), list)


def test_an_entry_point_listed_twice_is_loaded_once(monkeypatch):
    """An editable install shows up as two distributions on Python 3.9."""
    one = metadata.EntryPoint("notes", f"{MODULE}:NotesPanel", plugins.GROUP)
    twin = metadata.EntryPoint("notes", f"{MODULE}:NotesPanel", plugins.GROUP)
    other = metadata.EntryPoint("notes", "elsewhere:NotesPanel", plugins.GROUP)

    class Found(dict):
        def get(self, group, default=()):
            return [one, twin, other] if group == plugins.GROUP else default

    monkeypatch.setattr(plugins.metadata, "entry_points", lambda: Found())
    assert plugins._entry_points() == [one, other]


def test_plugin_translations_count_without_changing_the_window_s(plugin_module):
    i18n.add_messages("zh-TW", {"Notes": "筆記", "Track pitch": "別的翻譯",
                                "{} note(s) saved.": "已儲存 {} 則筆記。"})
    i18n.load("zh_TW")
    assert i18n.tr("Notes") == "筆記"
    assert i18n.tr("Track pitch") == "追蹤音高"
    assert i18n.tr("3 note(s) saved.") == "已儲存 3 則筆記。"
    # still there once the language is loaded again
    i18n.load("en")
    i18n.load("zh_TW")
    assert i18n.tr("Notes") == "筆記"
    # English needs no table
    i18n.add_messages("en", {"Notes": "Something else"})
    i18n.load("en")
    assert i18n.tr("Notes") == "Notes"


def test_a_plugin_draws_a_new_icon_but_not_over_a_built_in_one(plugin_module):
    with pytest.raises(ValueError, match="built-in"):
        icons.register("play", NotesPanel.icon_svg)
    with pytest.raises(ValueError, match="not valid SVG"):
        icons.register("test-notes", "<path d=")
    icons.register("test-notes", NotesPanel.icon_svg)
    assert not icons.rail_icon("test-notes").pixmap(24, 24).isNull()


def test_an_icon_without_a_name_is_refused(plugin_module):
    class Nameless(Panel):
        icon_svg = NotesPanel.icon_svg

    with pytest.raises(ValueError, match="no icon name"):
        plugins.register(Nameless)
