# -*- coding: utf-8 -*-
"""Edit → Settings…: what each setting may be, that it is kept, and that it
does what it says, in Python and on the page."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
import soundfile as sf

import kudio
from kudiostudio import theme
from kudiostudio.web import options
from kudiostudio.web.server import STATIC, LocalServer, theme_css
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture
def prefs_path(tmp_path):
    return tmp_path / "window.json"


@pytest.fixture
def make(prefs_path, monkeypatch):
    """A Studio on the settings file *prefs_path*, as a new start of the
    window would read it."""
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    made = []

    def build():
        studio = Studio(prefs=Prefs(prefs_path), language="en")
        made.append(studio)
        return studio

    yield build
    for studio in made:
        studio.shutdown()


def _said(studio):
    said = []
    original = studio.say
    studio.say = lambda text: (said.append(text), original(text))
    return said


# ----------------------------------------------------------- the values

def test_a_new_window_has_the_defaults(make):
    studio = make()
    info = studio.call("options.describe")
    assert info["values"] == options.DEFAULTS
    assert studio.state()["options"] == options.DEFAULTS
    assert studio.session.undo_depth == 20
    assert studio.session.crossfade_ms == 5.0


@pytest.mark.parametrize("key, value", [
    ("zoom", 1.3), ("zoom", "big"),
    ("ui_font", 'Arial"; color: red'), ("mono_font", "x" * 65), ("ui_font", "a{b}"),
    ("bit_depth", "MP3"), ("spectrum_map", "jet"), ("text_colors", "neon"),
    ("crossfade_ms", -1), ("crossfade_ms", 201),
    ("undo_depth", 0), ("undo_depth", 101), ("undo_depth", 2.5), ("undo_depth", True),
    ("reopen_last", "yes"), ("ask_before_replacing", 1),
    ("theme", "light"),
])
def test_a_value_a_setting_cannot_take_is_refused(key, value):
    with pytest.raises((TypeError, ValueError)):
        options.checked(key, value)


def test_a_refused_value_changes_nothing_and_says_why(make):
    studio = make()
    said = _said(studio)
    info = studio.call("options.set", ["undo_depth", 0])
    assert info["values"]["undo_depth"] == 20
    assert said[-1] == "Not changed — undo keeps 1 to 100 steps."


def test_a_setting_is_kept_for_the_next_start(make, prefs_path):
    make().call("options.set", ["bit_depth", "PCM_24"])
    make().call("options.set", ["zoom", 1.25])
    saved = json.loads(prefs_path.read_text(encoding="utf-8"))["options"]
    # only what differs from the defaults is written
    assert saved == {"bit_depth": "PCM_24", "zoom": 1.25}
    assert make().options["bit_depth"] == "PCM_24"


def test_a_value_edited_into_nonsense_costs_that_value_alone(make, prefs_path):
    prefs_path.write_text(json.dumps({"options": {"zoom": 7, "undo_depth": 50,
                                                  "theme": "light"}}), encoding="utf-8")
    studio = make()
    assert studio.options["zoom"] == 1.0
    assert studio.options["undo_depth"] == 50
    assert studio.session.undo_depth == 50


def test_restore_defaults_keeps_the_language_and_the_analysis(make, prefs_path):
    studio = make()
    studio.call("set_language", ["zh_TW"])
    studio.call("options.set", ["undo_depth", 5])
    studio.call("options.reset")
    assert studio.options.values == options.DEFAULTS
    assert studio.session.undo_depth == 20
    assert Prefs(prefs_path).get("language") == "zh_TW"
    studio.call("set_language", ["en"])


# ------------------------------------------------------------- editing

def test_fewer_undo_steps_drop_the_oldest(make, tone):
    studio = make()
    studio.call("open_files", [[str(tone)]])
    s = studio.session
    for _ in range(6):
        s.set_selection(0.1, 0.2)
        s.delete()
    studio.call("options.set", ["undo_depth", 3])
    assert sum(s.undo() for _ in range(6)) == 3
    for _ in range(5):
        s.set_selection(0.1, 0.2)
        s.delete()
    assert sum(s.undo() for _ in range(6)) == 3


def test_the_splice_crossfade_set_anywhere_is_kept(make):
    """The transport's box and Edit → Splice crossfade… set the same value
    the Settings dialog shows, and the next start splices the same way."""
    make().call("set_crossfade", [12])
    studio = make()
    assert studio.session.crossfade_ms == 12.0
    assert studio.options["crossfade_ms"] == 12.0


# -------------------------------------------------------------- saving

@pytest.mark.parametrize("depth, name, written", [
    ("PCM_16", "take.wav", "PCM_16"), ("PCM_24", "take.wav", "PCM_24"),
    ("FLOAT", "take.wav", "FLOAT"), ("FLOAT", "take.aiff", "FLOAT"),
    ("PCM_24", "take.flac", "PCM_24"),
])
def test_save_as_writes_the_bit_depth_in_settings(make, tone, tmp_path, depth, name, written):
    studio = make()
    studio.call("open_files", [[str(tone)]])
    studio.call("options.set", ["bit_depth", depth])
    studio.call("save_as", [str(tmp_path / "out" / name)])
    assert sf.info(str(tmp_path / "out" / name)).subtype == written


def test_float_to_flac_is_written_as_24_bit_and_says_so(make, tone, tmp_path):
    studio = make()
    studio.call("open_files", [[str(tone)]])
    studio.call("options.set", ["bit_depth", "FLOAT"])
    said = _said(studio)
    studio.call("save_as", [str(tmp_path / "take.flac")])
    studio.call("save_selection", [str(tmp_path / "part.flac")])
    assert sf.info(str(tmp_path / "take.flac")).subtype == "PCM_24"
    assert "FLAC holds no floating point, so take.flac was written as 24-bit." in said


def test_the_exported_script_writes_the_same_bit_depth(make, tone, tmp_path):
    studio = make()
    studio.call("open_files", [[str(tone)]])
    studio.call("options.set", ["bit_depth", "PCM_24"])
    studio.session.set_selection(0.1, 0.2)
    studio.session.delete()
    studio.call("export_script", [str(tmp_path / "edits.py")])
    assert "subtype='PCM_24'" in (tmp_path / "edits.py").read_text(encoding="utf-8")


# --------------------------------------------------------------- start

def test_the_last_file_is_opened_at_start_when_asked(make, tone, tmp_path):
    make().call("open_files", [[str(tone)]])
    assert make().reopen_last() is False            # off by default
    studio = make()
    studio.call("options.set", ["reopen_last", True])
    assert make().reopen_last() is True
    again = make()
    again.reopen_last()
    assert again.session.path is not None and again.session.path.name == "tone.wav"


def test_a_saved_file_is_the_last_one_and_a_missing_one_is_not_opened(make, tone, tmp_path):
    studio = make()
    studio.call("options.set", ["reopen_last", True])
    studio.call("open_files", [[str(tone)]])
    studio.call("save_as", [str(tmp_path / "kept.wav")])
    later = make()
    later.reopen_last()
    assert later.session.path.name == "kept.wav"
    (tmp_path / "kept.wav").unlink()
    gone = make()
    assert gone.reopen_last() is False and not gone.session.has_audio


# ---------------------------------------------------------- spectrogram

def test_the_spectrogram_colours_follow_the_setting(make):
    studio = make()
    server = LocalServer(studio)
    try:
        inferno = server.colormap()
        studio.call("options.set", ["spectrum_map", "viridis"])
        assert server.colormap() == server.colormap("viridis") != inferno
        # a name not offered gets the setting's map rather than an error
        assert server.colormap("jet") == server.colormap("viridis")
        assert len(server.colormap("gray")) == 256 * 3
    finally:
        server.server_close()


# ------------------------------------------------------------ text colours

def _contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two #rrggbb colours."""
    def luminance(colour):
        rgb = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_every_text_colour_set_reads_as_the_theme_says():
    """The ratios theme.py gives, and none below the 4.5:1 of WCAG AA, the
    hints on a raised row included."""
    plain = {"text": theme.TEXT_COLOR, "hint": theme.HINT_COLOR, "axis": theme.AXIS_COLOR}
    seen = {}
    for name, colours in theme.TEXT_SETS.items():
        c = {**plain, **colours}
        seen[name] = [round(_contrast(c["text"], theme.SURFACE_COLOR), 1),
                      round(_contrast(c["hint"], theme.SURFACE_COLOR), 1),
                      round(_contrast(c["axis"], theme.CANVAS_COLOR), 1)]
        assert _contrast(c["hint"], theme.RAISED_COLOR) >= 4.5, name
    assert seen == {"standard": [13.8, 5.9, 5.5], "bright": [17.0, 9.8, 9.4],
                    "warm": [14.7, 7.7, 7.0], "high": [18.2, 12.9, 12.6]}


def test_each_text_colour_set_is_a_rule_the_page_switches_to():
    css = theme_css()
    assert ':root[data-text="bright"] { --text: #f4f8fc; --hint: #b0c0d2; --axis: #a3b4c6; }' in css
    assert 'data-text="standard"' not in css
    # a set only changes variables the plain theme already has
    for colours in theme.TEXT_SETS.values():
        for key in colours:
            assert f"\n  --{key}: #" in css, key


# ---------------------------------------------------------------- page

def _node(script: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_zoomed_canvas_is_drawn_and_pointed_at_in_its_own_pixels():
    """At 125% a canvas 400 CSS pixels wide covers 500 pixels of the window:
    its bitmap is sized for 500, and a pointer 250 pixels in is 200 CSS
    pixels in. Unzoomed, both are as they were."""
    module = (STATIC / "js" / "zoom.js").as_uri()
    seen = _node(f"""
globalThis.window = {{ devicePixelRatio: 2 }};
const {{ pixelRatio, pointIn, scaleOf }} = await import({json.dumps(module)});
const canvas = (shown) => ({{ clientWidth: 400,
  getBoundingClientRect: () => ({{ left: 100, top: 10, width: shown }}) }});
const event = {{ clientX: 350, clientY: 60 }};
console.log(JSON.stringify([scaleOf(canvas(500)), pixelRatio(canvas(500)), pointIn(event, canvas(500)),
                            pixelRatio(canvas(400)), pointIn(event, canvas(400))]));
""")
    assert seen == [1.25, 2.5, {"x": 200, "y": 40}, 2, {"x": 250, "y": 50}]


def test_every_pointer_on_the_plots_goes_through_the_zoom():
    """A position worked out as clientX - left is in window pixels, and at
    125% it put a click a quarter further along than the pointer was."""
    for name in ("timeplot.js", "spectrogram.js", "overview.js"):
        page = (STATIC / "js" / name).read_text(encoding="utf-8")
        assert "clientX" not in page and "devicePixelRatio" not in page, name


SETTINGS_PAGE = """
const listeners = [];
globalThis.addEventListener = (type, fn) => listeners.push([type, fn]);
const resize = (width, height) => {
  globalThis.innerWidth = width; globalThis.innerHeight = height;
  for (const [type, fn] of listeners) if (type === "resize") fn();
};
const props = new Map();
const root = { dataset: {}, style: { zoom: "", setProperty: (k, v) => props.set(k, v),
                        removeProperty: (k) => props.delete(k),
                        getPropertyValue: (k) => props.get(k) ?? "" } };
globalThis.document = { documentElement: root, querySelector: () => ({ content: "token" }) };
globalThis.getComputedStyle = () => ({ getPropertyValue: (k) => (k === "--font-ui"
  ? "Segoe UI, sans-serif" : "Consolas, monospace") });
const settings = await import(__SETTINGS__);
const dialogs = await import(__DIALOGS__);
"""


def _settings_page(steps: str):
    js = STATIC / "js"
    return _node(SETTINGS_PAGE.replace("__SETTINGS__", json.dumps((js / "settings.js").as_uri()))
                 .replace("__DIALOGS__", json.dumps((js / "dialogs.js").as_uri())) + steps)


def test_the_page_puts_the_size_and_fonts_into_effect():
    seen = _settings_page("""
const calls = [];
const hooks = { resized: () => calls.push("resized"), colormap: (m) => calls.push(m) };
const base = { zoom: 1, ui_font: "", mono_font: "", spectrum_map: "inferno",
               ask_before_replacing: true };
settings.applyOptions(base, hooks);
settings.applyOptions({ ...base, zoom: 1.25, ui_font: "Noto Sans TC", spectrum_map: "gray" }, hooks);
const zoomed = [root.style.zoom, props.get("--zoom"), props.get("--font-ui")];
settings.applyOptions({ ...base, zoom: 1.25, spectrum_map: "gray" }, hooks);
settings.applyOptions({ ...base, zoom: 1.25, spectrum_map: "gray" }, hooks);
console.log(JSON.stringify({ zoomed, font: props.has("--font-ui"), calls }));
""")
    assert seen["zoomed"] == ["1.25", "1.25", '"Noto Sans TC", Segoe UI, sans-serif']
    assert seen["font"] is False
    # the first state puts the defaults in place; the map on screen is
    # already the setting's, so only a change fetches another
    assert seen["calls"] == ["resized", "gray", "resized", "resized"]


def test_the_page_switches_the_text_colours_and_redraws_the_plots():
    seen = _settings_page("""
const calls = [];
const hooks = { resized: () => calls.push("resized") };
const base = { zoom: 1, ui_font: "", mono_font: "", spectrum_map: "inferno",
               ask_before_replacing: true, text_colors: "standard" };
settings.applyOptions(base, hooks);
const before = "text" in root.dataset;
settings.applyOptions({ ...base, text_colors: "warm" }, hooks);
const warm = root.dataset.text;
settings.applyOptions({ ...base, text_colors: "warm" }, hooks);
settings.applyOptions({ ...base, text_colors: "standard" }, hooks);
console.log(JSON.stringify({ before, warm, after: "text" in root.dataset, calls }));
""")
    # the plots draw their figures on a canvas, so they are told each time
    assert seen == {"before": False, "warm": "warm", "after": False,
                    "calls": ["resized", "resized", "resized"]}


def test_a_size_is_shown_as_far_as_the_window_has_room_for_it():
    """The layout wants 1100 x 640 CSS pixels. 125% in the least window,
    1120 x 680, would leave it 896 wide, so the page gets 101% and says
    why; a larger window then shows the size chosen."""
    seen = _settings_page("""
globalThis.innerWidth = 1120; globalThis.innerHeight = 680;
const said = [], redrawn = [];
const hooks = { resized: () => redrawn.push(root.style.zoom),
                cramped: (shown, chosen) => said.push([shown, chosen]) };
const base = { zoom: 1, ui_font: "", mono_font: "", spectrum_map: "inferno",
               ask_before_replacing: true, text_colors: "standard" };
settings.applyOptions(base, hooks);
settings.applyOptions({ ...base, zoom: 1.25 }, hooks);
const small = root.style.zoom;
resize(1480, 860);
const larger = root.style.zoom;
resize(1480, 860);
console.log(JSON.stringify({ small, larger, said, redrawn, css: props.get("--zoom"),
  fits: [settings.fittingZoom(1.5, 1480, 860), settings.fittingZoom(0.9, 800, 500),
         settings.fittingZoom(1.25, 900, 600), settings.fittingZoom(1.5, undefined, 0)] }));
""")
    assert seen["small"] == "1.01" and seen["larger"] == "1.25" and seen["css"] == "1.25"
    assert seen["said"] == [[1.01, 1.25]]
    # once for the first state, once for the change, once for the resize
    assert seen["redrawn"] == ["1", "1.01", "1.25"]
    # smaller sizes always fit; a window under the layout's own gets 100%
    assert seen["fits"] == [1.34, 0.9, 1, 1.5]


def test_with_the_question_off_unsaved_audio_is_replaced_without_one():
    seen = _settings_page("""
settings.applyOptions({ zoom: 1, ui_font: "", mono_font: "", spectrum_map: "inferno",
                        ask_before_replacing: false });
const answer = await dialogs.confirmReplace({ title: "Open another file?", submit: "Open" });
console.log(JSON.stringify(answer));
""")
    assert seen is True


def test_ctrl_plus_and_minus_step_through_the_sizes():
    seen = _settings_page("""
const zooms = [0.9, 1, 1.1, 1.25, 1.5];
console.log(JSON.stringify([settings.nextZoom(zooms, 1, 1), settings.nextZoom(zooms, 1.5, 1),
                            settings.nextZoom(zooms, 0.9, -1), settings.nextZoom(zooms, 1.25, -1),
                            settings.nextZoom(zooms, 1.3, 1)]));
""")
    assert seen == [1.1, 1.5, 0.9, 1.1, 1]


def test_the_layout_divides_what_is_sized_by_the_window_by_the_zoom():
    """100vh at 125% is a quarter taller than the window, and the transport
    bar went below its bottom edge."""
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    import re
    units = re.findall(r"[\d.]+v[hw]\b[^;]*;", css)
    assert units and all("/ var(--zoom, 1)" in u for u in units), units


def test_the_language_is_chosen_in_settings_not_in_the_top_bar():
    """The top bar's corner had a language menu of its own; the choice is
    the first row of Settings now, named in both languages."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="language"' not in page
    script = (STATIC / "js" / "settings.js").read_text(encoding="utf-8")
    interface = script[script.index('group("Interface")'):script.index('group("Editing")')]
    assert 'rows.row("Language / 語言"' in interface
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    assert "setLanguageHandler(" in main and '$("language")' not in main
