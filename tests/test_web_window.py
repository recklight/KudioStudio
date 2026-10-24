# -*- coding: utf-8 -*-
"""The window's Edit, File and marker commands, panel previews,
jobs and dialogs -- the parts of the Studio the tool panels build on."""
from __future__ import annotations

import base64
import threading
import time

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.session import Marker
from kudiostudio.web import app as web_app
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def quiet(monkeypatch):
    played = []
    monkeypatch.setattr(kudio, "play_audio",
                        lambda y, rate=None, **_: played.append((len(y), rate)))
    return played


@pytest.fixture
def studio(tmp_path, quiet):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def opened(studio, tone):
    studio.call("open_files", [[str(tone)]])
    return studio


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


# ------------------------------------------------------------------- editing

def test_cut_and_paste_at_the_playhead(opened):
    s = opened
    s.call("set_crossfade", [0])         # a crossfade overlaps the seams
    n = len(s.session.y)
    s.call("set_selection", [0.25, 0.5])
    state = s.call("cut")
    assert state["can_paste"] and len(s.session.y) < n
    s.call("seek", [0.0])
    s.call("paste")
    assert len(s.session.y) == pytest.approx(n, abs=2)


def test_editing_without_a_selection_says_what_is_missing(opened):
    drain = _listen(opened)
    for name in ("cut", "copy", "delete", "crop"):
        opened.call(name)
    said = _messages(drain())
    assert "Select a range to cut — Ctrl+A selects everything." in said
    assert "Select a range to copy." in said
    assert "Select a range to delete." in said
    opened.call("paste")
    assert "The clipboard is empty." in _messages(drain())


def test_silence_goes_in_at_the_playhead(opened):
    s = opened
    n, sr = len(s.session.y), s.session.sr
    s.call("set_crossfade", [0])
    s.call("seek", [0.5])
    s.call("insert_silence", [0.25])
    assert len(s.session.y) == n + int(0.25 * sr)
    assert np.all(s.session.y[int(0.5 * sr):int(0.75 * sr)] == 0)


def test_the_crossfade_is_clamped_and_announced(opened):
    drain = _listen(opened)
    assert opened.call("set_crossfade", [500])["crossfade_ms"] == 200
    assert opened.call("set_crossfade", [0])["crossfade_ms"] == 0
    said = _messages(drain())
    assert said == ["Splices now crossfade over 200 ms.",
                    "Splices are now hard cuts — expect a click at every join."]


def test_save_as_and_save_selection_write_wav_files(opened, tmp_path):
    s = opened
    out = tmp_path / "out" / "whole.wav"
    out.parent.mkdir()
    s.call("save_as", [str(out)])
    y, sr = kudio.file_load(out)
    assert sr == s.session.sr and len(y) == len(s.session.y)
    assert s.prefs.get("last_dir") == str(out.parent)

    s.call("set_selection", [0.0, 0.5])
    part = tmp_path / "part.wav"
    s.call("save_selection", [str(part)])
    assert len(kudio.file_load(part)[0]) == pytest.approx(0.5 * sr, abs=1)


def test_the_suggested_names_follow_the_clip(opened):
    s = opened
    assert s.call("suggest_name", ["audio"]) == "tone.wav"
    assert s.call("suggest_name", ["image"]) == "tone.png"
    assert s.call("suggest_name", ["script"]) == "tone_edits.py"
    s.call("set_selection", [0.25, 0.5])
    assert s.call("suggest_name", ["selection"]) == "tone_0.250-0.500.wav"
    with pytest.raises(ValueError):
        s.call("suggest_name", ["nonsense"])


def test_the_edit_script_is_written_or_refused(opened, tmp_path):
    s = opened
    drain = _listen(s)
    script = tmp_path / "edits.py"
    s.call("export_script", [str(script)])
    assert not script.exists()
    assert "No edits to export yet." in _messages(drain())
    s.call("set_selection", [0.0, 0.25])
    s.call("delete")
    s.call("export_script", [str(script)])
    assert script.is_file() and "kudio" in script.read_text(encoding="utf-8")
    assert any(m.startswith("Exported 1 edit(s) to edits.py") for m in _messages(drain()))


def test_the_image_is_written_as_the_page_drew_it(opened, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    out = tmp_path / "view.png"
    opened.call("write_image", [str(out), "data:image/png;base64,"
                                + base64.b64encode(png).decode()])
    assert out.read_bytes() == png
    drain = _listen(opened)
    bad = tmp_path / "bad.png"
    opened.call("write_image", [str(bad), base64.b64encode(b"GIF89a").decode()])
    assert not bad.exists()
    assert _messages(drain()) == ["Could not write bad.png"]


# ------------------------------------------------------------------- markers

def test_markers_are_added_named_and_removed(opened):
    s = opened
    s.call("seek", [0.4])
    s.call("markers.add")
    s.call("set_selection", [0.1, 0.2])
    state = s.call("markers.mark_selection")
    # kept in time order
    assert [(m["start"], m["end"]) for m in state["markers"]] == \
        [(pytest.approx(0.1), pytest.approx(0.2)), (0.4, 0.4)]
    state = s.call("markers.rename", [1, "click"])
    assert state["markers"][1]["label"] == "click"
    state = s.call("markers.remove", [0])
    assert [m["label"] for m in state["markers"]] == ["click"]
    assert s.call("markers.clear")["markers"] == []


def test_markers_saved_beside_the_audio_come_back_with_it(opened, tone):
    s = opened
    s.session.set_markers([Marker(0.1, 0.3, "word")])
    s.call("markers.save")
    assert s.session.sidecar_path(tone).is_file()
    s.call("markers.clear")
    s.call("load", [str(tone)])
    assert [m.label for m in s.session.markers] == ["word"]


def test_markers_export_and_import_as_an_audacity_track(opened, tmp_path):
    s = opened
    s.session.set_markers([Marker(0.1, 0.3, "a"), Marker(0.5, 0.5, "b")])
    track = tmp_path / "labels.txt"
    s.call("markers.export", [str(track)])
    s.call("markers.clear")
    s.call("markers.load", [str(track)])
    assert [m.label for m in s.session.markers] == ["a", "b"]


def test_a_recording_with_no_file_cannot_have_a_sidecar(studio):
    studio.session.set_audio(np.zeros(1600, np.float32), 16000, name="take")
    studio.session.set_markers([Marker(0.0, 0.0, "x")])
    drain = _listen(studio)
    studio.call("markers.save")
    assert _messages(drain()) == [
        "This audio has no file to sit beside — use Export… instead."]


def test_find_speech_replaces_the_list(opened, monkeypatch):
    s = opened
    s.session.set_markers([Marker(0.0, 0.0, "old")])
    monkeypatch.setattr(kudio, "vad", lambda y, sr: [(0.1, 0.3), (0.6, 0.8)])
    state = s.call("markers.find_speech")
    assert [m["label"] for m in state["markers"]] == ["speech 1", "speech 2"]


# ------------------------------------------------------------------ preview

def test_a_preview_loops_until_something_else_plays(opened, quiet):
    s = opened
    drain = _listen(s)
    s.call("set_selection", [0.5, 0.75])
    rendered = np.zeros(1600, np.float32)
    with s.lock:
        assert s.start_preview("effects", rendered, 16000)
    started = [e for e in drain() if e["type"] == "playback"]
    assert started[-1]["preview"] == "effects" and started[-1]["start"] == 0.5
    assert s.call("state")["previewing"] == "effects"

    s._on_finished(0.6)                     # ran out: it starts again
    again = [e for e in drain() if e["type"] == "playback"]
    assert again and again[-1].get("preview") == "effects"

    s.call("play")                          # the clip claims playback back
    events = drain()
    assert {"type": "preview", "owner": "effects", "on": False} in events
    assert s.call("state")["previewing"] == ""


def test_an_edit_ends_the_preview(opened):
    s = opened
    with s.lock:
        s.start_preview("effects", np.zeros(800, np.float32), 16000)
    s.call("set_selection", [0.0, 0.1])
    s.call("delete")
    assert s.call("state")["previewing"] == ""


def test_switching_preview_off_stops_the_sound(opened):
    s = opened
    with s.lock:
        s.start_preview("effects", np.zeros(16000, np.float32), 16000)
    assert s.call("stop_preview")["playing"] is False
    assert s.call("state")["previewing"] == ""


# --------------------------------------------------------------------- jobs

def test_a_job_runs_once_per_owner_and_reports_back(studio):
    drain = _listen(studio)
    gate, done = threading.Event(), []
    assert studio.jobs.run("batch", "converting", gate.wait, 5,
                           on_done=lambda r: done.append((r, studio.lock._is_owned())))
    assert studio.jobs.busy("batch")
    assert not studio.jobs.run("batch", "again", lambda: None)
    assert studio.call("state")["busy"] == ["batch"]
    gate.set()
    assert studio.jobs.wait(5)
    assert done == [(True, True)]           # the result, under the lock
    jobs = [e for e in drain() if e["type"] == "job"]
    assert [e["busy"] for e in jobs] == [True, False]


def test_a_job_can_hand_on_to_the_next_for_the_same_owner(studio):
    """While its callback runs the owner is still busy, so starting the next
    job there would be refused; returning it runs it once this one is over."""
    ran = []

    def first_done(result):
        assert not studio.jobs.run("enhance", "x", lambda: None)     # still busy
        return lambda: studio.jobs.run("enhance", "second", lambda: "two",
                                       on_done=ran.append)

    studio.jobs.run("enhance", "first", lambda: "one", on_done=first_done)
    deadline = time.monotonic() + 5
    while ran != ["two"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ran == ["two"]


def test_a_failed_job_is_reported_not_raised(studio):
    drain = _listen(studio)

    def broken():
        raise OSError("disk full")

    studio.jobs.run("batch", "converting", broken)
    assert studio.jobs.wait(5)
    assert "OSError: disk full" in _messages(drain())


# ------------------------------------------------------------------ dialogs

class FakeDialogs:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def open_files(self, directory, types, multiple=True):
        self.asked.append(("open", directory, multiple))
        return self.answer

    def save_file(self, directory, filename, types):
        self.asked.append(("save", directory, filename))
        return self.answer[0] if self.answer else None

    def folder(self, directory):
        self.asked.append(("folder", directory))
        return self.answer[0] if self.answer else None


def test_without_a_window_the_page_is_asked_for_the_path(studio, tmp_path):
    studio.prefs.set("last_dir", str(tmp_path))
    answer = studio.call("choose_path", ["save"], {"suggested": "tone.wav"})
    assert answer == {"needs_path": True, "suggested": str(tmp_path / "tone.wav")}


def test_the_window_dialog_starts_in_the_last_folder(studio, tmp_path):
    studio.prefs.set("last_dir", str(tmp_path))
    studio.dialogs = FakeDialogs(["C:/x/out.wav"])
    assert studio.call("choose_path", ["save"], {"suggested": "tone.wav"}) == \
        {"path": "C:/x/out.wav"}
    assert studio.dialogs.asked == [("save", str(tmp_path), "tone.wav")]
    assert studio.call("choose_path", ["open_many"])["path"] == ["C:/x/out.wav"]
    studio.dialogs = FakeDialogs([])
    assert studio.call("choose_path", ["folder"]) == {"path": None}
    with pytest.raises(ValueError):
        studio.call("choose_path", ["everything"])


def test_the_lock_is_free_while_a_dialog_is_open(studio):
    seen = []

    class Slow(FakeDialogs):
        def folder(self, directory):
            t = threading.Thread(target=lambda: seen.append(
                studio.lock.acquire(timeout=2) and (studio.lock.release() or True)))
            t.start()
            t.join()
            return None

    studio.dialogs = Slow([])
    studio.call("choose_path", ["folder"])
    assert seen == [True]


# -------------------------------------------------------------------- close

class FakeWindow:
    def __init__(self):
        self.destroyed = threading.Event()

    def destroy(self):
        self.destroyed.set()


def test_closing_waits_for_a_running_job(studio):
    window = FakeWindow()
    assert web_app.close_when_idle(studio, window) is True
    gate = threading.Event()
    drain = _listen(studio)
    studio.jobs.run("batch", "converting", gate.wait, 5)
    assert web_app.close_when_idle(studio, window) is False
    assert any("Quitting when Batch finishes" in m for m in _messages(drain()))
    time.sleep(0.05)
    assert not window.destroyed.is_set()
    gate.set()
    assert window.destroyed.wait(5)


def test_the_rail_lists_every_tool_in_order(studio):
    entries = studio.call("rail")
    assert [e["id"] for e in entries][:3] == ["inspect", "effects", "enhance"]
    assert entries[0]["script"] == "" and all(e["script"] for e in entries[1:])
