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
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import numpy as np
import pytest

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
                      "Manifest", "Pitch", "Metrics", "Batch"]


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
