# -*- coding: utf-8 -*-
"""The window as a whole, through the commands the page sends: opening a
file, selection and A/B, playback and loop, the panels' previews, the edit
script, the splice crossfade, the live monitor and quitting. Which menu
entries are greyed out and where the playhead is drawn follow from the
state these return, so that is what is checked."""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import kudio
from kudiostudio import i18n
from kudiostudio.tools import enhance
from kudiostudio.web import app as web_app
from kudiostudio.web.monitor import MONITOR_SECONDS
from kudiostudio.web.player import Playback
from kudiostudio.web.server import STATIC, LocalServer
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def opened(studio, tone):
    studio.call("open_files", [[str(tone)]])
    return studio


@pytest.fixture
def played(studio, monkeypatch):
    """What reached the player, as (samples, t0). Nothing behind it runs out
    on a timer, so a test says itself when playback has ended."""
    heard = []

    def play(y, sr, gain_db=0.0, t0=0.0):
        heard.append((len(y), t0))
        return Playback(float(t0), float(t0) + len(y) / sr, time.monotonic())

    monkeypatch.setattr(studio.player, "play", play)
    return heard


def _listen(studio):
    q = studio.events.subscribe()

    def drain():
        seen = []
        while not q.empty():
            seen.append(q.get_nowait())
        return seen

    return drain


def _messages(events):
    return [e["text"] for e in events if e["type"] == "message"]


def _states(events):
    return [e["state"] for e in events if e["type"] == "state"]


def _edit(studio):
    """Halve the gain, so the session has something to compare against."""
    studio.session.set_audio(studio.session.y * 0.5, studio.session.sr,
                             undo_label="gain", keep_original=True)


# ----------------------------------------------------------------- the window

def test_the_rail_has_every_panel(studio):
    titles = [e["title"] for e in studio.call("rail")]
    assert titles == ["Inspect", "Effects", "Enhance", "Compare", "Record", "Synthesize",
                      "Manifest", "Pitch", "Metrics", "Batch", "Survey", "Claude"]


def test_the_window_carries_the_application_icon(studio):
    """The native window takes the .ico; the page's own icon is the PNG the
    server hands out."""
    assert web_app.ICON.read_bytes()[:4] == b"\x00\x00\x01\x00"
    server = LocalServer(studio, 0)
    server.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        conn.request("GET", "/assets/logo.png")
        response = conn.getresponse()
        body = response.read()
        conn.close()
    finally:
        server.close()
    assert response.status == 200 and body.startswith(b"\x89PNG")


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows call")
def test_windows_files_the_process_under_kudiostudio_not_python():
    """In a process of its own: the ID, once set, is the process's for good."""
    script = (
        "import ctypes, sys\n"
        "from kudiostudio.desktop import APP_ID, set_windows_app_id\n"
        "assert set_windows_app_id()\n"
        "found = ctypes.c_wchar_p()\n"
        "shell32 = ctypes.windll.shell32\n"
        "assert shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(found)) == 0\n"
        "print(found.value)\n"
        "ctypes.windll.ole32.CoTaskMemFree(found)\n")
    root = str(Path(__file__).resolve().parent.parent)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=60, cwd=root)
    assert done.returncode == 0, done.stderr[-2000:]
    from kudiostudio.desktop import APP_ID
    assert done.stdout.strip() == APP_ID == "Kudio.KudioStudio"


def test_elsewhere_the_app_id_is_a_no_op(monkeypatch):
    from kudiostudio import desktop as entry
    monkeypatch.setattr(entry.sys, "platform", "linux")
    assert entry.set_windows_app_id() is False


def test_the_user_guide_opens_in_the_windows_language(studio, monkeypatch):
    from kudiostudio.web import studio as studio_module
    opened = []
    monkeypatch.setattr(studio_module.webbrowser, "open", opened.append)
    studio.call("open_guide")
    studio.call("set_language", ["zh_TW"])
    studio.call("open_guide")
    assert opened == ["https://recklight.github.io/KudioStudio/",
                      "https://recklight.github.io/KudioStudio/zh-TW/"]


# ---------------------------------------------------------------------- files

def test_saving_and_undo_wait_for_audio(studio):
    """Save As goes by has_audio and Undo by can_undo, in the menu and on
    the toolbar alike."""
    state = studio.call("state")
    assert state["has_audio"] is False
    assert state["can_undo"] is False


def test_opening_a_file_fills_the_list_and_the_views(studio, tone):
    state = studio.call("open_files", [[str(tone)]])
    assert studio.session.has_audio and state["has_audio"]
    assert [f["path"] for f in state["files"]] == [str(tone.resolve())]
    # the window's title and the file chip are built from these
    assert state["name"] == "tone.wav"
    assert state["sr"] == 16000 and state["samples"] == 16000


def test_a_bad_file_is_reported_instead_of_raised(studio, tmp_path):
    broken = tmp_path / "not-audio.wav"
    broken.write_text("this is not a wav file")
    drain = _listen(studio)
    assert studio.call("load", [str(broken)])["has_audio"] is False
    assert studio.session.has_audio is False
    assert _messages(drain())[-1].startswith("Could not open not-audio.wav — ")


def test_selection_round_trips_between_the_page_and_the_session(opened):
    drain = _listen(opened)
    opened.call("set_selection", [0.2, 0.6])
    assert opened.session.selection() == pytest.approx((0.2, 0.6))

    opened.session.set_selection(0.1, 0.3)
    assert _states(drain())[-1]["selection"] == pytest.approx([0.1, 0.3])


# ------------------------------------------------------------------------ A/B

def test_there_is_nothing_to_compare_until_there_is_an_edit(opened):
    """The A/B button and the Match loudness box both go by can_compare."""
    assert opened.call("state")["can_compare"] is False
    _edit(opened)
    assert opened.call("state")["can_compare"] is True


def test_compare_swaps_what_the_plots_show(opened):
    s = opened
    _edit(s)
    s.call("set_match_loudness", [False])
    state = s.call("set_compare", [True])
    assert state["comparing"] is True          # the button reads "original"
    assert np.allclose(s.audio()[0], s.session.original()[0])

    # Ctrl+B flips what the state says
    state = s.call("set_compare", [not state["comparing"]])
    assert state["comparing"] is False
    assert np.allclose(s.audio()[0], s.session.y)


def test_editing_drops_out_of_compare(opened):
    _edit(opened)
    opened.call("set_compare", [True])
    drain = _listen(opened)

    _edit(opened)
    assert opened.session.comparing is False
    assert _states(drain())[-1]["comparing"] is False


def test_the_status_line_names_the_loudness_gap(opened):
    _edit(opened)
    drain = _listen(opened)

    opened.call("set_compare", [True])
    message = _messages(drain())[-1]
    assert "levelled to the edit" in message
    assert "-6.0 LU" in message, message

    opened.call("set_match_loudness", [False])
    message = _messages(drain())[-1]
    assert "Match loudness" in message and "+6.0 LU" in message, message


def test_an_edit_that_leaves_the_loudness_alone_applies_plus_zero(opened):
    """A flipped polarity measures exactly as loud, and the gap of 0.0 came
    out as -0.0 LU applied."""
    s = opened.session
    s.set_audio(-s.y, s.sr, undo_label="flip", keep_original=True)
    drain = _listen(opened)
    opened.call("set_compare", [True])
    assert "(+0.0 LU applied)" in _messages(drain())[-1]


# ------------------------------------------------------------------- previews

def test_a_panel_preview_plays_without_touching_the_session(opened, played):
    s = opened
    s.call("set_selection", [0.25, 0.75])
    before = s.session.y.copy()

    assert s.call("effects.preview", ["gain", {"db": -6.0}]) == {"on": True}

    assert played == [(8000, pytest.approx(0.25))]
    assert np.allclose(s.session.y, before), "preview must not edit"
    state = s.call("state")
    assert state["can_undo"] is False
    assert state["previewing"] == "effects"


def test_a_preview_loops_until_something_else_claims_playback(opened, played):
    s = opened
    drain = _listen(s)
    s.call("effects.preview", ["gain", {}])
    assert len(played) == 1

    s._on_finished(1.0)
    assert len(played) == 2, "auditioning should have restarted"

    s.call("stop")
    assert s.call("state")["previewing"] == ""
    assert {"type": "preview", "owner": "effects", "on": False} in drain()

    s._on_finished(1.0)
    assert len(played) == 2, "and stop must really stop it"


def test_applying_the_effect_cancels_its_open_preview(opened, played):
    s = opened
    s.call("effects.preview", ["gain", {}])
    assert s.call("state")["previewing"] == "effects"
    drain = _listen(s)

    s.call("effects.apply", ["gain", {}])
    assert s.call("state")["previewing"] == ""
    assert {"type": "preview", "owner": "effects", "on": False} in drain()


def test_an_enhance_preview_renders_as_a_job_and_commits_nothing(opened, played,
                                                                 monkeypatch):
    s = opened
    gate = threading.Event()
    real = kudio.trad_enhance
    monkeypatch.setattr(kudio, "trad_enhance", lambda y, sr: (gate.wait(10), real(y, sr))[1])
    s.call("set_selection", [0.2, 0.7])
    before = s.session.y.copy()

    assert s.call("enhance.preview", [enhance.TRAD, {}]) == {"on": True}
    # the request is answered while the denoise is still running
    assert played == [] and s.call("state")["busy"] == ["enhance"]
    gate.set()
    assert s.jobs.wait(15)

    assert played, "the preview never reached the player"
    assert played[0][1] == pytest.approx(0.2), "should play at the selection"
    assert np.allclose(s.session.y, before), "preview must not edit"
    assert s.call("state")["can_undo"] is False


def test_denoising_releases_the_enhance_preview(opened, played, monkeypatch):
    s = opened
    s.call("enhance.preview", [enhance.TRAD, {}])
    assert s.jobs.wait(15)
    assert s.call("state")["previewing"] == "enhance"

    gate = threading.Event()
    real = kudio.trad_enhance
    monkeypatch.setattr(kudio, "trad_enhance", lambda y, sr: (gate.wait(10), real(y, sr))[1])
    drain = _listen(s)
    s.call("enhance.denoise", [enhance.TRAD, {}])
    # at once, not when the result is committed
    assert s.call("state")["previewing"] == ""
    assert {"type": "preview", "owner": "enhance", "on": False} in drain()
    gate.set()
    assert s.jobs.wait(15)


def test_a_looping_preview_is_not_affected_by_the_loop_switch(opened, played):
    """Preview always loops; the switch is for ordinary playback."""
    s = opened
    with s.lock:
        s.start_preview("effects", np.zeros(1600, dtype=np.float32), 16000)
    assert len(played) == 1
    s._on_finished(0.1)
    assert len(played) == 2                  # looped without the loop being set
    assert s.call("state")["loop"] is False


# ------------------------------------------------------------------- playhead

def test_clicking_the_waveform_moves_the_playhead(opened):
    """The page puts the playhead on both plots, and the time under them,
    from the playback event."""
    drain = _listen(opened)
    opened.call("seek", [0.4])
    assert opened.call("state")["playhead"] == pytest.approx(0.4)
    assert drain()[-1] == {"type": "playback", "playing": False,
                           "position": pytest.approx(0.4)}


def test_the_time_readout_shows_milliseconds():
    """The readout is the page's own; node runs its formatter. Milliseconds
    matter when picking a splice."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = (STATIC / "js" / "format.js").as_uri()
    done = subprocess.run([node, "--input-type=module"],
                          input=f"import {{ clock }} from {json.dumps(module)};\n"
                                f"console.log(clock(0.4));\n",
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "0:00.400"


def test_seek_is_clamped_and_reset_by_a_new_file(opened, tone):
    assert opened.call("seek", [-5.0])["position"] == 0.0

    opened.call("seek", [0.5])
    opened.call("load", [str(tone)])
    assert opened.call("state")["playhead"] == 0.0


def test_play_starts_from_the_playhead_when_nothing_is_selected(opened, played):
    opened.call("set_transport", kwargs={"selection_only": False})
    opened.call("seek", [0.25])
    opened.call("play")
    assert played == [(12000, pytest.approx(0.25))]    # 0.25 s in to the end of a 1 s clip


def test_play_uses_the_selection_when_there_is_one(opened, played):
    opened.call("set_selection", [0.25, 0.75])
    opened.call("play")
    assert played == [(8000, pytest.approx(0.25))]


def test_playback_running_out_goes_back_to_where_it_started(opened, played):
    s = opened
    s.call("set_selection", [0.4, 0.6])
    s.call("play")
    drain = _listen(s)

    s._on_finished(0.6)
    assert s.call("state")["playhead"] == pytest.approx(0.4)
    assert drain()[-1] == {"type": "playback", "playing": False,
                           "position": pytest.approx(0.4)}


def test_loop_repeats_the_same_range(opened, played):
    s = opened
    s.call("set_selection", [0.2, 0.4])
    s.call("play")
    assert [t0 for _, t0 in played] == [pytest.approx(0.2)]

    s._on_finished(0.4)                       # not looping: stays stopped
    assert len(played) == 1

    s.call("set_transport", kwargs={"loop": True})
    s.call("play")
    s._on_finished(0.4)
    assert len(played) == 3
    assert played[-1][1] == pytest.approx(0.2)


def test_the_loop_is_one_setting_the_menu_and_the_check_box_both_read(opened):
    assert opened.call("set_transport", kwargs={"loop": True})["loop"] is True
    # Ctrl+L flips what the state says
    state = opened.call("state")
    assert opened.call("set_transport", kwargs={"loop": not state["loop"]})["loop"] is False


# -------------------------------------------------------------------- editing

def test_undo_follows_the_session(opened):
    assert opened.call("state")["can_undo"] is False
    _edit(opened)
    assert opened.call("state")["can_undo"] is True
    assert opened.call("undo")["can_undo"] is False


def test_the_edit_entries_follow_whether_there_is_a_selection(opened):
    """Cut, Copy, Delete, Crop and Save selection go by can_copy, Paste by
    can_paste."""
    state = opened.call("state")
    assert state["can_copy"] is False
    assert state["can_paste"] is False

    assert opened.call("set_selection", [0.2, 0.6])["can_copy"] is True
    assert opened.call("copy")["can_paste"] is True


def test_paste_lands_at_the_playhead(opened):
    s = opened
    s.call("set_selection", [0.0, 0.2])
    s.call("copy")
    s.call("clear_selection")
    s.call("seek", [0.5])

    start, end = s.call("paste")["selection"]
    assert start == pytest.approx(0.5, abs=1e-3)
    assert end == pytest.approx(0.7, abs=1e-3)


# -------------------------------------------------------------------- markers

def test_markers_reach_the_plots(opened):
    drain = _listen(opened)
    opened.session.add_marker(0.3, 0.5, "here")
    markers = _states(drain())[-1]["markers"]
    assert len(markers) == 1
    assert (markers[0]["start"], markers[0]["end"]) == pytest.approx((0.3, 0.5))
    assert markers[0]["label"] == "here"


def test_clicking_a_marker_moves_the_playhead(opened):
    """The marker list seeks to the start of the row clicked."""
    opened.session.add_marker(0.6)
    start = opened.call("state")["markers"][0]["start"]
    opened.call("seek", [start])
    assert opened.call("state")["playhead"] == pytest.approx(0.6)


def test_the_playhead_is_what_a_new_marker_uses(opened):
    opened.call("seek", [0.42])
    opened.call("markers.add")
    assert opened.session.markers[0].start == pytest.approx(0.42)


def _played_for(studio, seconds):
    """Pretend playback started *seconds* ago, without waiting for it."""
    now = studio.player._current
    studio.player._current = Playback(now.start, now.end, now.clock - seconds)


def test_a_marker_added_during_playback_lands_where_playback_is(opened):
    """M while listening marks what is being heard, not where play was
    pressed."""
    opened.call("set_transport", kwargs={"selection_only": False})
    opened.call("seek", [0.1])
    opened.call("play")
    _played_for(opened, 0.4)
    opened.call("markers.add")
    assert 0.49 < opened.session.markers[0].start < 0.7


def test_paste_during_playback_lands_where_playback_is(opened):
    s = opened
    s.call("set_selection", [0.0, 0.2])
    s.call("copy")
    s.call("clear_selection")
    s.call("set_transport", kwargs={"selection_only": False})
    s.call("seek", [0.1])
    s.call("play")
    _played_for(s, 0.4)
    start, _ = s.call("paste")["selection"]
    assert 0.49 < start < 0.7


def test_a_preview_playing_is_not_the_clips_playhead(opened, played):
    """A panel's audition runs on its own clock (a tempo change is longer
    or shorter than the clip), so a marker added meanwhile goes where the
    audition started."""
    opened.call("set_selection", [0.3, 0.8])
    opened.start_preview("effects", np.zeros(16000, dtype=np.float32), 16000)
    opened.player._current = Playback(0.3, 1.3, time.monotonic() - 0.6)
    opened.call("markers.add")
    assert opened.session.markers[0].start == pytest.approx(0.3, abs=1e-3)


# --------------------------------------------------------------------- export

class SaveDialog:
    """The window's save dialog. *pick* turns the path it offers into the
    one the person ends up with, None for Cancel."""

    def __init__(self, pick):
        self.pick = pick
        self.offered = []

    def save_file(self, directory, filename, file_types):
        offered = str(Path(directory) / filename)
        self.offered.append(offered)
        return self.pick(offered)


def _save_to(studio, kind, file_types):
    """What the page does before it sends a save command: ask for the name
    to suggest, then for a path in the window's save dialog."""
    suggested = studio.call("suggest_name", [kind])
    return studio.call("choose_path", ["save"], {"suggested": suggested,
                                                 "file_types": file_types})["path"]


def test_save_selection_writes_only_the_range(opened, tmp_path):
    target = tmp_path / "part.wav"
    opened.call("set_selection", [0.25, 0.75])
    opened.dialogs = SaveDialog(lambda offered: str(target))
    opened.call("save_selection", [_save_to(opened, "selection", ["WAV (*.wav)"])])

    y, sr = kudio.file_load(target)
    assert len(y) == pytest.approx(0.5 * sr, abs=2)


def test_the_edits_export_as_a_script(opened, tmp_path):
    s = opened
    assert s.call("state")["has_history"] is False, "nothing to export"
    s.call("set_selection", [0.2, 0.4])
    s.call("delete")
    assert s.call("state")["has_history"] is True

    target = tmp_path / "edits.py"
    s.dialogs = SaveDialog(lambda offered: str(target))
    path = _save_to(s, "script", ["Python (*.py)"])
    assert s.dialogs.offered[0].endswith("tone_edits.py")

    drain = _listen(s)
    s.call("export_script", [path])
    script = target.read_text(encoding="utf-8")
    assert script == s.session.edit_script("tone_edited.wav")
    assert "# 1. delete" in script
    assert _messages(drain()) == ["Exported 1 edit(s) to edits.py"]

    assert s.call("undo")["has_history"] is False


def test_exporting_a_script_that_cannot_reproduce_the_edit_says_so(opened, tmp_path):
    _edit(opened)                            # an edit that brought no code
    target = tmp_path / "edits.py"
    opened.dialogs = SaveDialog(lambda offered: str(target))
    path = _save_to(opened, "script", ["Python (*.py)"])
    drain = _listen(opened)
    opened.call("export_script", [path])

    assert target.is_file()
    message = _messages(drain())[-1]
    assert "will not reproduce" in message
    assert "step 1 (gain)" in message


def test_cancelling_the_script_export_writes_nothing(opened, tmp_path):
    """The page sends export_script only with a path the dialog gave it."""
    _edit(opened)
    opened.dialogs = SaveDialog(lambda offered: None)
    assert _save_to(opened, "script", ["Python (*.py)"]) is None
    assert opened.dialogs.offered, "the dialog was never shown"
    assert list(tmp_path.glob("*.py")) == []


def test_a_script_exported_after_saving_over_the_original_says_so(studio, tmp_path):
    """Save As suggests the opened file. Accepted, and the edits exported,
    the script read the edited audio back and edited it again -- and the
    status line said all was well."""
    take = tmp_path / "take.wav"
    kudio.save_wave(take, (0.3 * np.sin(np.arange(32000) / 10)
                           ).astype(np.float32), 16000)
    studio.call("open_files", [[str(take)]])
    studio.call("set_selection", [0.5, 1.0])
    studio.call("delete")

    studio.dialogs = SaveDialog(lambda offered: offered)
    path = _save_to(studio, "audio", ["WAV (*.wav)"])
    assert Path(studio.dialogs.offered[0]) == take.resolve(), \
        "the opened file is what Save As offers"
    studio.call("save_as", [path])

    drain = _listen(studio)
    studio.call("export_script", [_save_to(studio, "script", ["Python (*.py)"])])
    message = _messages(drain())[-1]
    assert "will not reproduce them as it stands" in message
    assert "take.wav was saved over" in message


# ---------------------------------------------------------------- live monitor

def test_a_live_monitor_draws_without_touching_the_session(opened):
    s = opened
    before = np.array(s.session.y, copy=True)
    s.monitor.start(16000)
    s.monitor.push(np.zeros(8000, dtype=np.float32), 16000)
    picture = s.monitor.snapshot()
    s.monitor.stop()

    assert np.array_equal(s.session.y, before)
    # the rolling axis is the window, not the take: half a second of audio
    # sits at the right-hand edge of it rather than filling it. To the
    # nearest whole column: the window is a column count, not a time
    assert picture["span"] == pytest.approx(MONITOR_SECONDS, abs=0.05)
    assert picture["wave"]["start"] == pytest.approx(picture["span"] - 0.5, abs=0.01)


# ------------------------------------------------------ the splice crossfade

def test_the_splice_crossfade_is_reachable_and_visible(opened):
    """It alters the audio at every cut; a hidden constant would not do."""
    s = opened
    assert s.call("state")["crossfade_ms"] == 5.0
    drain = _listen(s)

    s.call("set_crossfade", [0.0])
    assert s.session.crossfade_ms == 0.0
    assert "hard cuts" in _messages(drain())[-1]

    s.call("set_crossfade", [12.0])
    assert s.session.crossfade_ms == 12.0
    assert "12 ms" in _messages(drain())[-1]


def test_the_crossfade_is_on_the_bar_not_behind_a_dialog(studio):
    """It changes what every cut and paste does to the audio, and the right
    length is per edit -- so the bar shows it and sets it, and the Edit menu
    names it, without opening anything."""
    assert studio.call("state")["crossfade_ms"] == studio.session.crossfade_ms
    drain = _listen(studio)

    assert studio.call("set_crossfade", [40.0])["crossfade_ms"] == 40.0
    assert studio.session.crossfade_ms == 40.0
    assert _messages(drain()) == ["Splices now crossfade over 40 ms."]


def test_the_dialog_and_the_bar_set_the_same_value(studio):
    """Both send set_crossfade; the bar's box and the menu entry then read
    the state."""
    assert studio.call("set_crossfade", [12.5])["crossfade_ms"] == 12.5
    assert studio.session.crossfade_ms == 12.5


def test_turning_it_off_says_what_that_costs(studio):
    drain = _listen(studio)
    studio.call("set_crossfade", [0.0])
    assert studio.session.crossfade_ms == 0.0
    assert any("hard cut" in m for m in _messages(drain()))


# -------------------------------------------------------------------- quitting

class FakeWindow:
    """The native window; `destroy` is what closes it."""

    def __init__(self):
        self.destroyed = threading.Event()

    def destroy(self):
        self.destroyed.set()


def test_quitting_waits_for_a_running_job(studio):
    """A kudio call cannot be stopped half-way, and a process that exits
    under a Batch run leaves half its output tree. The window stays until
    the job ends, says so, and then closes itself."""
    window = FakeWindow()
    batch = studio.panel("batch")
    gate = threading.Event()
    assert batch.start_job("converting", gate.wait, 20)
    drain = _listen(studio)

    assert web_app.close_when_idle(studio, window) is False, \
        "the close is put off while the job runs"
    assert _messages(drain())[-1].startswith("Quitting when Batch finishes")
    assert not window.destroyed.wait(0.05)
    gate.set()
    assert window.destroyed.wait(20)
    assert not batch.busy


def test_quitting_the_application_mid_job_exits_cleanly(tmp_path):
    """The whole program as `main` runs it, with a stand-in for pywebview
    whose window is closed while a Batch run is going: the close waits for
    the run, and the process then ends with 0."""
    script = tmp_path / "quit.py"
    script.write_text(textwrap.dedent(f'''
        import sys, threading, time, types
        sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
        from kudiostudio.web import app as entry

        made = []


        class Studio(entry.Studio):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made.append(self)


        class Handlers(list):
            def __iadd__(self, handler):
                self.append(handler)
                return self


        class Window:
            def __init__(self):
                self.events = types.SimpleNamespace(shown=Handlers(), closing=Handlers())
                self.destroyed = threading.Event()

            def destroy(self):
                self.destroyed.set()


        window = Window()


        def start(**options):
            studio = made[0]
            studio.jobs.run("batch", "converting", time.sleep, 1.0)
            assert [close() for close in window.events.closing] == [False]
            assert window.destroyed.wait(30), "the window never closed"
            assert not studio.jobs.running()


        webview = types.ModuleType("webview")
        webview.create_window = lambda *args, **kwargs: window
        webview.start = start
        sys.modules["webview"] = webview
        entry.Studio = Studio
        sys.exit(entry.main(["--lang", "en"]))
    '''), encoding="utf-8")
    # the settings file goes to the test's folder, not the real one
    env = dict(os.environ, APPDATA=str(tmp_path / "config"), HOME=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / "config"), PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120,
                          cwd=tmp_path, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "Quitting when Batch finishes" in done.stderr
    assert "Traceback" not in done.stderr


def test_redo_follows_the_session(opened):
    _edit(opened)
    assert opened.call("state")["can_redo"] is False
    assert opened.call("undo")["can_redo"] is True
    state = opened.call("redo")
    assert (state["can_undo"], state["can_redo"]) == (True, False)


def test_ctrl_shift_z_and_ctrl_y_redo_rather_than_undo():
    """Ctrl+Shift+Z is Redo in most editors; read as Ctrl+Z it took a second
    edit back."""
    page = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    redo = page.index('handled(); command("redo")')
    undo = page.index('else if (ctrl && key === "z") { handled(); command("undo"); }')
    branch = page[page.rindex("else if", 0, redo):redo]
    assert 'key === "y"' in branch and 'e.shiftKey && key === "z"' in branch
    assert redo < undo, "the Shift branch has to be tried before plain Ctrl+Z"


def test_an_answer_older_than_its_panels_last_event_is_dropped():
    """js/fresh.js, which panels.js puts between every panel command and the
    page: a quick job's event first, the answer to its click second."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = (STATIC / "js" / "fresh.js").as_uri()
    script = (f"const {{ Freshness }} = await import({json.dumps(module)});\n"
              "const f = new Freshness();\n"
              "const seen = [f.stale('enhance', { busy: true, serial: 3 })];\n"
              "f.heard({ type: 'panel', panel: 'enhance', serial: 4, info: {} });\n"
              "f.heard({ type: 'panel', panel: 'enhance', preview: false });\n"
              "seen.push(f.stale('enhance', { busy: true, serial: 3 }),\n"
              "          f.stale('enhance', { busy: true, serial: 4 }),\n"
              "          f.stale('synthesize', { busy: true, serial: 0 }),\n"
              "          f.stale('enhance', { on: true }), f.stale('enhance', null));\n"
              "console.log(JSON.stringify(seen));\n")
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [False, True, False, False, False, False]


def test_a_panel_hears_its_events_and_asks_describe_again_when_it_is_stale():
    """panels.js itself, on a stand-in document: the events it is given
    reach the check, a stale answer to a click is null, and a stale describe
    is asked again, since a panel mounts on it. Dropped as null, it failed
    the mount with a TypeError, most likely after a change of language,
    which mounts every panel again."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = (STATIC / "js" / "panels.js").as_uri()
    script = (
        "const element = () => ({ addEventListener() {}, replaceChildren() {}, hidden: true });\n"
        "globalThis.document = { querySelector: () => ({ content: 'token' }),\n"
        "                        getElementById: element, createElement: element };\n"
        f"const {{ Panels }} = await import({json.dumps(module)});\n"
        "const panels = new Panels({ apply() {}, status() {}, state: () => null });\n"
        "const ctx = panels.context({ id: 'enhance' });\n"
        "let answers = [];\n"
        "const asked = [];\n"
        "panels.run = async (name) => { asked.push(name); return answers.shift(); };\n"
        "panels.event({ type: 'panel', panel: 'enhance', serial: 4, info: {} });\n"
        "const seen = [];\n"
        "answers = [{ busy: true, serial: 3 }];\n"
        "seen.push(await ctx.call('denoise'));\n"
        "answers = [{ busy: false, serial: 3 }, { busy: false, serial: 4 }];\n"
        "seen.push(await ctx.call('describe'));\n"
        "answers = [1, 2, 3].map(() => ({ busy: false, serial: 2 }));\n"
        "seen.push(await ctx.call('describe'));\n"
        "answers = [''];\n"
        "seen.push(await ctx.call('set_level'));\n"
        "console.log(JSON.stringify({ seen, asked }));\n")
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "seen": [None, {"busy": False, "serial": 4}, {"busy": False, "serial": 2}, ""],
        "asked": ["enhance.denoise"] + ["enhance.describe"] * 5 + ["enhance.set_level"]}


def test_the_empty_window_names_every_format_open_takes():
    """The open dialog offers AIFF, and the line under Open audio… left it
    out, so someone with .aif files might not have tried."""
    from kudiostudio.web.studio import AUDIO_TYPES

    offered = {s.lstrip("*.").upper() for s in re.findall(r"\*\.\w+", AUDIO_TYPES[0])}
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    listed = re.search(r'<p class="hint mono">([^<]*)</p>', page).group(1).split(" · ")
    assert offered - {"AIF"} == set(listed)


# ------------------------------------------------------------------ save as

def test_save_as_in_a_browser_asks_before_writing_over_a_file(opened, tone):
    """The suggested name is the file the clip came from, and in a browser
    Enter in the path box wrote over it with no question."""
    before = tone.read_bytes()
    _edit(opened)
    answer = opened.call("save_as", [str(tone)])
    assert answer["confirm_replace"] == {"path": str(tone), "source": True}
    assert tone.read_bytes() == before
    assert "confirm_replace" not in opened.call("save_as", [str(tone), True])
    assert tone.read_bytes() != before


def test_save_as_takes_wav_flac_and_aiff_and_says_no_to_the_rest(opened, tmp_path):
    said = []
    opened.events.subscribe()
    original = opened.say
    opened.say = lambda text: (said.append(text), original(text))
    out = tmp_path / "out"
    out.mkdir()
    opened.call("save_as", [str(out / "plain")])
    opened.call("save_as", [str(out / "dot.")])
    opened.call("save_as", [str(out / "take.flac")])
    opened.call("save_as", [str(out / "take.aif")])
    opened.call("save_as", [str(out / "take.ogg")])
    assert sorted(p.name for p in out.iterdir()) == [
        "dot.wav", "plain.wav", "take.aif", "take.flac"]
    assert kudio.audio_info(out / "take.flac").sr == opened.session.sr
    # soundfile writes .aiff by its name and .aif only when told the format
    assert sf.info(str(out / "take.aif")).format == "AIFF"
    assert said[-1] == "Saved as WAV, FLAC or AIFF only — take.ogg asks for .ogg."


def test_after_save_as_the_clip_goes_by_its_new_name(opened, tmp_path):
    state = opened.call("save_as", [str(tmp_path / "renamed.wav")])
    assert (state["name"], state["path"]) == ("renamed.wav", str(tmp_path / "renamed.wav"))
    # and the Files list has it, as the open one: the list marks the file
    # whose path is the state's, and the saved file was in neither
    assert state["path"] in [f["path"] for f in state["files"]]


def test_enter_does_not_write_over_a_file_in_a_browser():
    """The Replace question had Replace focused, so the Enter that typed the
    path and a second one wrote over the file it was opened from. Cancel is
    focused for that question, and the button that answers yes elsewhere."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
class El {
  constructor(tag) { Object.assign(this, { tag, kids: [], dataset: {}, type: "", textContent: "" }); }
  append(...kids) { this.kids.push(...kids); }
  addEventListener() {}
  remove() {}
  focus() { globalThis.focused = this.textContent; }
  *all() { for (const k of this.kids) { yield k; yield* k.all(); } }
  querySelector(selector) {
    const fits = selector === "[data-focus]" ? (e) => "focus" in e.dataset
      : (e) => e.tag === "input" || (e.tag === "button" && e.type === "submit");
    for (const e of this.all()) if (fits(e)) return e;
    return null;
  }
}
globalThis.document = { createElement: (tag) => new El(tag), body: new El("body"),
                        addEventListener() {}, removeEventListener() {},
                        querySelector: () => ({ content: "token" }) };
const { confirm } = await import(__DIALOGS__);
const seen = [];
for (const focusCancel of [true, false]) {
  confirm({ title: "Replace take.wav?", submit: "Replace", focusCancel });
  seen.push(globalThis.focused);
}
console.log(JSON.stringify(seen));
""".replace("__DIALOGS__", json.dumps((STATIC / "js" / "dialogs.js").as_uri()))
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["Cancel", "Replace"]
    page = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    asked = page[page.index("async function saveTo"):page.index("const saveAs")]
    assert "focusCancel: true" in asked


def test_the_save_dialogs_offer_every_format_save_as_writes():
    from kudiostudio.web.editing import SAVE_SUFFIXES

    page = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    offered = re.search(r"const AUDIO_SAVE_TYPES = \[([^\]]*)\]", page).group(1)
    assert set(re.findall(r"\*(\.\w+)", offered)) == set(SAVE_SUFFIXES)


def test_a_new_language_says_the_status_line_again_in_it(opened):
    """The line from before the switch stayed in English until the next
    action, which read as a switch that had not taken."""
    drain = _listen(opened)
    opened.call("set_language", ["zh_TW"])
    try:
        assert _messages(drain())[-1].startswith("已載入 tone.wav")
    finally:
        opened.call("set_language", ["en"])


def test_every_tool_on_the_rail_has_a_name_assistive_technology_reads():
    """An icon over a small caption: read as an accessibility tree, each of
    the rail's buttons had no name. The rail built by panels.js on a
    stand-in document, from what the server lists, in Chinese."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = STATIC / "js"
    script = r"""
class El {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], dataset: {}, attributes: {}, className: "",
                          classList: { toggle() {} } });
  }
  append(...kids) { this.kids.push(...kids); }
  replaceChildren() { this.kids = []; }
  addEventListener() {}
  setAttribute(name, value) { this.attributes[name] = value; }
  *all() { for (const k of this.kids) { yield k; yield* k.all(); } }
  querySelectorAll(selector) {
    const key = selector.match(/^\[data-([\w-]+)\]$/)?.[1]
      .replace(/-(\w)/g, (_, c) => c.toUpperCase());
    return [...this.all()].filter((e) => key ? key in e.dataset : e.tag === selector);
  }
}
const ids = new Map();
globalThis.document = {
  createElement: (tag) => new El(tag),
  getElementById: (id) => ids.get(id) ?? ids.set(id, new El("div")).get(id),
  querySelector: () => ({ content: "token" }),
};
globalThis.fetch = async () => ({ json: async () => ({ ok: true, result: [
  { id: "inspect", title: "Inspect", icon: "inspect" },
  { id: "enhance", title: "Enhance", icon: "enhance" }] }) });
const i18n = await import(__I18N__);
i18n.load(__TABLE__);
const { Panels } = await import(__PANELS__);
await new Panels({ apply() {}, status() {}, state: () => null }).load();
const rail = document.getElementById("rail");
console.log(JSON.stringify(rail.querySelectorAll("button").map((b) => b.attributes["aria-label"])));
"""
    script = (script.replace("__I18N__", json.dumps((js / "i18n.js").as_uri()))
              .replace("__PANELS__", json.dumps((js / "panels.js").as_uri()))
              .replace("__TABLE__", json.dumps(i18n.table("zh_TW"), ensure_ascii=False)))
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    names = json.loads(done.stdout)
    assert names == [i18n.translate("Inspect", "zh_TW"), i18n.translate("Enhance", "zh_TW")]
    assert names[0] != "Inspect"


def test_files_of_the_same_name_are_told_apart_by_their_folders(studio, tmp_path, tone):
    """clean/spk1_01.wav and noisy/spk1_01.wav both read spk1_01.wav, and
    the reference was easy to pick wrong."""
    import shutil
    from kudiostudio.web.studio import shown_names

    for side in ("clean", "noisy"):
        (tmp_path / side).mkdir()
        shutil.copy(tone, tmp_path / side / "spk1_01.wav")
    studio.call("open_files", [[str(tmp_path / "clean" / "spk1_01.wav"),
                                str(tmp_path / "noisy" / "spk1_01.wav"), str(tone)]])
    assert [f["name"] for f in studio.call("state")["files"]] == [
        "clean/spk1_01.wav", "noisy/spk1_01.wav", "tone.wav"]
    assert shown_names(["a/x/take.wav", "b/x/take.wav", "c/y/take.wav"]) == [
        "a/x/take.wav", "b/x/take.wav", "y/take.wav"]
    # a root brings its own separator, and C:\/take.wav was what showed
    assert shown_names(["/take.wav", "/a/take.wav"]) == ["/take.wav", "a/take.wav"]
    if sys.platform == "win32":
        assert shown_names(["C:\\take.wav", "D:\\take.wav"]) == ["C:/take.wav", "D:/take.wav"]
