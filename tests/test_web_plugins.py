# -*- coding: utf-8 -*-
"""Panels from other packages in the window: found under the plugin entry
point group, put on the rail after the built-in tools, their page module
served from their own package -- or left out and named."""
from __future__ import annotations

import http.client
import json
import sys
import types
from importlib import metadata

import pytest

from kudiostudio import i18n, plugins, shapes
from kudiostudio.web.commands import command
from kudiostudio.web.panels import ORDER, PANEL_CLASSES
from kudiostudio.web.panels.base import WebPanel
from kudiostudio.web.server import LocalServer
from kudiostudio.web.studio import Prefs, Studio

MODULE = "kudiostudio_test_web_plugin"


class NotesPanel(WebPanel):
    id = "notes"
    title = "Notes"
    icon = "test-web-notes"
    icon_svg = '<path d="M6 4h12v16H6z"/><path d="M9 9h6M9 13h6"/>'
    messages = {"zh_TW": {"Notes": "筆記"}}
    script = "notes.js"

    @command
    def hello(self, name: str) -> str:
        return f"hello {name}"


class Unbuildable(WebPanel):
    id = "unbuildable"

    def __init__(self, studio):
        raise RuntimeError("no device")


class Squatter(WebPanel):
    """Wants a built-in tool's place."""
    id = "effects"


class Lookalike:
    """A panel's attributes, without being a WebPanel."""
    id = "lookalike"
    title = "Lookalike"
    script = "lookalike.js"


def not_a_panel():
    return None


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """A module the fake entry points can name, its page module on disk, and
    a clean slate after."""
    static = tmp_path / "plugin-static"
    static.mkdir()
    (static / "notes.js").write_text("export async function mount(root) {}\n",
                                     encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for the page", encoding="utf-8")
    monkeypatch.setattr(NotesPanel, "static_dir", static)
    module = types.ModuleType(MODULE)
    for target in (NotesPanel, Unbuildable, Squatter, Lookalike, not_a_panel):
        setattr(module, target.__name__, target)
    monkeypatch.setitem(sys.modules, MODULE, module)
    monkeypatch.delenv(plugins.DISABLE_ENV, raising=False)
    extra = {k: list(v) for k, v in i18n._extra.items()}
    yield module
    shapes.SHAPES.pop(NotesPanel.icon, None)
    i18n._extra.clear()
    i18n._extra.update(extra)
    i18n.load("en")


def entry_points(monkeypatch, **targets):
    """*targets* as the entry points the window finds: an attribute of the
    test's module, or a ``module:attribute`` of its own."""
    found = [metadata.EntryPoint(name, attr if ":" in attr else f"{MODULE}:{attr}",
                                 plugins.GROUP)
             for name, attr in targets.items()]
    monkeypatch.setattr(plugins, "_entry_points",
                        lambda group=plugins.GROUP: found if group == plugins.GROUP else [])


@pytest.fixture
def studio_with(tmp_path):
    built = []

    def build(language="en"):
        s = Studio(prefs=Prefs(tmp_path / f"window{len(built)}.json"), language=language)
        built.append(s)
        return s

    yield build
    for s in built:
        s.shutdown()


def _get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


def test_a_plugin_panel_joins_the_rail_after_the_built_in_ones(plugin, monkeypatch,
                                                                 studio_with):
    entry_points(monkeypatch, notes="NotesPanel")
    studio = studio_with()
    rail = studio.call("rail")
    assert rail[-1] == {"id": "notes", "title": "Notes", "icon": "test-web-notes",
                        "script": "notes.js", "url": "/plugins/notes/notes.js",
                        "extra": True}
    assert [e["id"] for e in rail] == [*ORDER, "notes"]
    # and below a rule, which the page draws before the first entry marked extra
    assert not any(e.get("extra") for e in rail[:-1])
    assert isinstance(studio.panels[-1], NotesPanel)
    assert studio.call("notes.hello", ["RL"]) == "hello RL"
    assert [p.error for p in studio.plugins] == [""]
    assert shapes.SHAPES["test-web-notes"] == NotesPanel.icon_svg
    # it talks to the window like any panel
    events = studio.events.subscribe()
    studio.panel("notes").report("hello from a plugin")
    assert events.get_nowait() == {"type": "message", "text": "hello from a plugin"}


def test_the_window_opens_without_the_plugins_that_fail(plugin, monkeypatch, studio_with):
    entry_points(monkeypatch, gone="kudiostudio_no_such_module:Panel",
                 function="not_a_panel", unbuildable="Unbuildable", notes="NotesPanel")
    studio = studio_with()

    assert [type(p) for p in studio.panels[len(PANEL_CLASSES):]] == [NotesPanel]
    errors = {p.name: p.error for p in studio.plugins}
    assert errors["notes"] == ""
    assert errors["gone"].startswith("ModuleNotFoundError")
    assert "is not a WebPanel subclass" in errors["function"]
    assert errors["unbuildable"] == "RuntimeError: no device"
    left_out = studio.call("about")["left_out"]
    for name in ("gone", "function", "unbuildable"):
        assert any(line.startswith(f"Plugin {name} was left out") for line in left_out), name


def test_its_module_is_served_from_its_own_folder_and_nothing_above(plugin, monkeypatch,
                                                                     studio_with):
    entry_points(monkeypatch, notes="NotesPanel")
    server = LocalServer(studio_with(), 0)
    server.start()
    try:
        status, body = _get(server, "/plugins/notes/notes.js")
        assert status == 200 and body.startswith(b"export async function mount")
        assert _get(server, "/plugins/notes/../secret.txt")[0] == 404
        assert _get(server, "/plugins/notes/%2e%2e/secret.txt")[0] == 404
        assert _get(server, "/plugins/effects/../../server.py")[0] == 404
        assert _get(server, "/plugins/nobody/notes.js")[0] == 404
        icons = json.loads(_get(server, "/icons.json")[1])
        assert icons["test-web-notes"] == NotesPanel.icon_svg
    finally:
        server.close()


def test_its_translations_reach_the_page(plugin, monkeypatch, studio_with):
    entry_points(monkeypatch, notes="NotesPanel")
    studio = studio_with("zh_TW")
    assert studio.call("translations")["messages"]["Notes"] == "筆記"


def test_about_names_the_plugin(plugin, monkeypatch, studio_with):
    entry_points(monkeypatch, notes="NotesPanel")
    about = studio_with().call("about")
    assert about["plugins"] == [MODULE] and about["left_out"] == []


def test_a_plugin_that_cannot_be_built_is_left_out_and_named(plugin, monkeypatch,
                                                              studio_with):
    entry_points(monkeypatch, notes="NotesPanel", broken="Unbuildable",
                 squatter="Squatter", lookalike="Lookalike", missing="not_there")
    studio = studio_with()
    assert [e["id"] for e in studio.call("rail") if e.get("extra")] == ["notes"]
    left_out = studio.call("about")["left_out"]
    assert len(left_out) == 4
    assert "Plugin broken was left out — RuntimeError: no device" in left_out
    assert any(s.startswith("Plugin lookalike was left out — TypeError: ") and
               "is not a WebPanel subclass" in s for s in left_out)
    assert any(s.startswith("Plugin squatter was left out — ValueError: its id 'effects'")
               for s in left_out)
    assert any(s.startswith("Plugin missing was left out — AttributeError") for s in left_out)
    assert studio.panel("effects").__class__.__name__ == "EffectsPanel"


def test_a_built_in_icon_cannot_be_redrawn():
    with pytest.raises(ValueError, match="built-in"):
        shapes.register("play", "<path d='M0 0'/>")
    with pytest.raises(ValueError, match="not valid SVG"):
        shapes.register("test-web-broken", "<path d=")
    assert "test-web-broken" not in shapes.SHAPES


def test_switched_off_there_are_none(plugin, monkeypatch, studio_with):
    entry_points(monkeypatch, notes="NotesPanel")
    monkeypatch.setenv(plugins.DISABLE_ENV, "1")
    studio = studio_with()
    assert studio.plugins == [] and not any(e.get("extra") for e in studio.call("rail"))
