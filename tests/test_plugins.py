# -*- coding: utf-8 -*-
"""Panels from other packages: their entry points read on every Python this
runs on, their translations and icons taken on board, or refused."""
from __future__ import annotations

from importlib import metadata
from xml.etree import ElementTree

import pytest

from kudiostudio import i18n, plugins, shapes
from kudiostudio.web.panels.base import WebPanel

MODULE = "kudiostudio_test_plugin"


class NotesPanel(WebPanel):
    id = "notes"
    title = "Notes"
    icon = "test-notes"
    icon_svg = '<path d="M6 4h12v16H6z"/><path d="M9 9h6M9 13h6"/>'


@pytest.fixture
def clean_slate(monkeypatch):
    """Whatever a test registers is gone again afterwards."""
    monkeypatch.delenv(plugins.DISABLE_ENV, raising=False)
    extra = {k: list(v) for k, v in i18n._extra.items()}
    yield
    shapes.SHAPES.pop(NotesPanel.icon, None)
    i18n._extra.clear()
    i18n._extra.update(extra)
    i18n.load("en")


def test_switching_plugins_off_finds_none(monkeypatch):
    def fail(*_):
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


def test_plugin_translations_count_without_changing_the_window_s(clean_slate):
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


def test_a_plugin_draws_a_new_icon_but_not_over_a_built_in_one(clean_slate):
    with pytest.raises(ValueError, match="built-in"):
        shapes.register("play", NotesPanel.icon_svg)
    with pytest.raises(ValueError, match="not valid SVG"):
        shapes.register("test-notes", "<path d=")
    shapes.register("test-notes", NotesPanel.icon_svg)
    body = shapes.SHAPES["test-notes"]
    assert body == NotesPanel.icon_svg
    drawn = ElementTree.fromstring(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>')
    assert len(drawn) == 2


def test_an_icon_without_a_name_is_refused(clean_slate):
    class Nameless(WebPanel):
        icon_svg = NotesPanel.icon_svg

    with pytest.raises(ValueError, match="no icon name"):
        plugins.register(Nameless)
