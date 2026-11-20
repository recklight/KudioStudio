# -*- coding: utf-8 -*-
"""The window's Edit, File and marker commands, panel previews,
jobs and dialogs -- the parts of the Studio the tool panels build on."""
from __future__ import annotations

import base64
import threading
import time
import types
from pathlib import Path

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


def test_paths_that_are_not_files_stay_out_of_the_list_and_are_named(studio, tone, tmp_path):
    """A few thousand paths written the way Git Bash writes them (/c/Users/...) all
    went into the list as C:\\c\\Users\\..., none of them a file, and the
    window said nothing about it."""
    drain = _listen(studio)
    gone = [str(tmp_path / "gone_1.wav"), str(tmp_path / "gone_2.wav")]
    state = studio.call("open_files", [[gone[0], str(tone), gone[1]]])
    assert [f["path"] for f in state["files"]] == [str(tone.resolve())]
    assert state["has_audio"]
    said = _messages(drain())
    # after the tone that did open, so the line ends on what went wrong
    assert said[0].startswith("Loaded tone.wav")
    assert said[-1] == "Not found: gone_1.wav and 1 more — left out of the Files list."
    assert studio.call("about")["said"] == said[-1]


def test_nothing_found_leaves_the_window_as_it_was(studio, tmp_path):
    drain = _listen(studio)
    state = studio.call("open_files", [[str(tmp_path / "gone.wav")]])
    assert state["files"] == [] and not state["has_audio"]
    assert _messages(drain()) == ["Not found: gone.wav — left out of the Files list."]


def test_what_was_said_before_the_page_came_is_there_when_it_comes(studio, tmp_path):
    """Files named on the command line are opened before the page connects,
    and the page's first word was "Ready." over whatever had been said."""
    assert studio.call("about")["said"] == ""
    studio.call("open_files", [[str(tmp_path / "gone.wav")]])
    assert studio.call("about")["said"] == "Not found: gone.wav — left out of the Files list."
    studio.call("set_language", ["zh_TW"])
    assert studio.call("about")["said"] == "找不到：gone.wav — 沒有放進檔案清單。"


PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(16)).decode()


@pytest.mark.parametrize("write", [
    lambda s, folder: s.call("write_image", [str(folder / "plots.png"), PNG]),
    lambda s, folder: (s.call("set_selection", [0.0, 0.5]),
                       s.call("save_selection", [str(folder / "part.wav")])),
    lambda s, folder: (s.call("markers.add", [0.25]),
                       s.call("markers.export", [str(folder / "marks.txt")])),
], ids=["analysis image", "selection", "markers"])
def test_a_save_dialog_starts_where_the_last_save_went(studio, tmp_path, tone, write):
    """One remembered folder served opening and saving alike, so opening the
    next take of a recordings folder sent the next export back among the
    takes it was made from. Saving now remembers its own folder; opening
    still starts where the files are."""
    import shutil
    takes, out = tmp_path / "takes", tmp_path / "out"
    takes.mkdir()
    out.mkdir()
    first, second = takes / "a.wav", takes / "b.wav"
    shutil.copy(tone, first)
    shutil.copy(tone, second)
    studio.call("open_files", [[str(first), str(second)]])
    write(studio, out)
    studio.call("load", [str(second)])
    assert studio.call("choose_path", ["save"], {"suggested": "b.png"}) == \
        {"needs_path": True, "suggested": str(out / "b.png")}
    assert studio.call("choose_path", ["open"]) == {"needs_path": True, "suggested": str(takes)}


def test_with_nothing_saved_yet_a_save_dialog_starts_where_files_open(studio, tmp_path):
    studio.prefs.set("last_dir", str(tmp_path))
    assert studio.call("choose_path", ["save"], {"suggested": "x.png"}) == \
        {"needs_path": True, "suggested": str(tmp_path / "x.png")}


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


class FakeServer:
    def __init__(self, studio, port):
        self.url = "http://127.0.0.1:0/?token=test"
        self.closed = False

    def start(self):
        pass

    def close(self):
        self.closed = True


def _browser(monkeypatch, studio, sleep):
    """``kudiostudio --browser`` on *studio*, with *sleep* for every sleep
    in web.app, and no browser and no port; returns the server it made."""
    made = []

    def server(s, port):
        made.append(FakeServer(s, port))
        return made[-1]

    monkeypatch.setattr(web_app, "Prefs", lambda: studio.prefs)
    monkeypatch.setattr(web_app, "Studio", lambda prefs, language: studio)
    monkeypatch.setattr(web_app, "LocalServer", server)
    monkeypatch.setattr(web_app, "set_windows_app_id", lambda: None)
    monkeypatch.setattr(web_app, "webbrowser", types.SimpleNamespace(open=lambda url: True))
    monkeypatch.setattr(web_app, "time", types.SimpleNamespace(sleep=sleep))
    assert web_app.main(["--browser"]) == 0
    return made[0]


def test_ctrl_c_in_the_browser_waits_for_a_running_job(studio, monkeypatch):
    """As closing the window does: stopped half-way, a Batch run leaves its
    output tree half-written."""
    gate = threading.Event()
    studio.jobs.run("batch", "converting", gate.wait, 5)
    drain = _listen(studio)
    asleep = []

    def sleep(seconds):
        asleep.append(studio.jobs.busy("batch"))
        if len(asleep) == 1:
            raise KeyboardInterrupt         # the page is up; Ctrl+C
        gate.set()
        time.sleep(0.01)

    server = _browser(monkeypatch, studio, sleep)
    assert asleep[:2] == [True, True]       # it slept on with the job running
    assert not studio.jobs.running() and server.closed
    said = [m for m in _messages(drain()) if m.startswith("Stopping when")]
    assert said == ["Stopping when Batch finishes — a running job cannot be stopped "
                    "half-way. Ctrl+C again stops now, with the job unfinished."]
    assert i18n.translate(said[0], "zh_TW").startswith("將在批次完成後停止 — ")


def test_a_second_ctrl_c_stops_at_once(studio, monkeypatch):
    gate = threading.Event()
    studio.jobs.run("batch", "converting", gate.wait, 5)

    def sleep(seconds):
        raise KeyboardInterrupt

    try:
        server = _browser(monkeypatch, studio, sleep)
        assert studio.jobs.busy("batch") and server.closed
    finally:
        gate.set()


def test_the_rail_lists_every_tool_in_order(studio):
    entries = studio.call("rail")
    assert [e["id"] for e in entries][:3] == ["inspect", "effects", "enhance"]
    assert entries[0]["script"] == "" and all(e["script"] for e in entries[1:])


def test_the_address_is_out_before_the_server_settles_in(studio, monkeypatch):
    """Printed to a file or a pipe, the address with its token sat in the
    buffer for as long as the server ran, and a closed tab could not be
    opened again."""
    import io
    import sys

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="utf-8"))
    seen = []

    def sleep(seconds):
        seen.append(raw.getvalue().decode("utf-8"))
        raise KeyboardInterrupt

    _browser(monkeypatch, studio, sleep)
    assert seen and "KudioStudio" in seen[0] and "Ctrl+C to stop." in seen[0]


def test_verbose_turns_up_kudiostudio_and_kudio_only(studio, monkeypatch):
    """`kudiostudio -v` logged 597 lines at start-up, most of them numba's
    compiler, over the traceback it was asked for."""
    import logging

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])          # so basicConfig acts here
    monkeypatch.setattr(root, "level", root.level)
    for name in ("kudiostudio", "kudio"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.getLogger(name).level)
    monkeypatch.setattr(web_app, "Prefs", lambda: studio.prefs)
    monkeypatch.setattr(web_app, "Studio", lambda prefs, language: studio)
    monkeypatch.setattr(web_app, "LocalServer", lambda s, port: FakeServer(s, port))
    monkeypatch.setattr(web_app, "set_windows_app_id", lambda: None)
    monkeypatch.setattr(web_app, "webbrowser", types.SimpleNamespace(open=lambda url: True))

    def stop(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(web_app, "time", types.SimpleNamespace(sleep=stop))
    try:
        assert web_app.main(["--browser", "-v"]) == 0
        assert logging.getLogger("kudiostudio").getEffectiveLevel() == logging.DEBUG
        assert logging.getLogger("kudio.core").getEffectiveLevel() == logging.DEBUG
        assert logging.getLogger("numba.core").getEffectiveLevel() == logging.INFO
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)


def test_version_names_kudiostudio_and_kudio(capsys):
    import kudio
    from kudiostudio import __version__

    with pytest.raises(SystemExit) as done:
        web_app.main(["--version"])
    assert done.value.code == 0
    assert capsys.readouterr().out == f"KudioStudio {__version__} (kudio {kudio.__version__})\n"


# -------------------------------------------------- folders, patterns, lists

def _takes(root, names):
    import soundfile as sf
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), np.zeros(1600, np.float32), 16000)
    return root


def test_a_folder_opens_as_the_audio_under_it(studio, tmp_path):
    """Two folders of takes named alike, and a note beside them: the folder
    stands for its audio, in order, the note left out."""
    root = _takes(tmp_path / "corpus", ["a/take_01.wav", "a/take_02.flac", "b/take_01.wav"])
    (root / "notes.txt").write_text("not audio", encoding="utf-8")
    state = studio.call("open_files", [[str(root)]])
    assert [Path(f["path"]).relative_to(root.resolve()).as_posix() for f in state["files"]] == [
        "a/take_01.wav", "a/take_02.flac", "b/take_01.wav"]
    assert state["name"] == "take_01.wav"


def test_a_pattern_opens_the_files_it_matches(studio, tmp_path):
    """The shell on Windows passes *.wav on as it is."""
    root = _takes(tmp_path / "corpus", ["x_1.wav", "x_2.wav", "y_1.wav"])
    state = studio.call("open_files", [[str(root / "x_*.wav")]])
    assert [Path(f["path"]).name for f in state["files"]] == ["x_1.wav", "x_2.wav"]


def test_a_folder_with_no_audio_says_so(studio, tmp_path):
    drain = _listen(studio)
    (tmp_path / "empty").mkdir()
    state = studio.call("open_files", [[str(tmp_path / "empty")]])
    assert state["files"] == []
    assert _messages(drain())[-1] == f"No audio in {tmp_path / 'empty'}."


def test_switching_files_leaves_the_unchanged_list_out(studio, tmp_path):
    """With a few thousand files the list was half a megabyte, sent again on
    every switch. The reply to a load, and each push after the first, leave
    it out and say which list is still current."""
    root = _takes(tmp_path / "corpus", ["one.wav", "two.wav"])
    drain = _listen(studio)
    opened = studio.call("open_files", [[str(root)]])
    assert len(opened["files"]) == 2
    pushed = [e["state"] for e in drain() if e["type"] == "state"]
    assert "files" in pushed[0]
    switched = studio.call("load", [opened["files"][1]["path"]])
    assert "files" not in switched
    assert switched["files_version"] == opened["files_version"]
    assert all("files" not in e["state"] for e in drain() if e["type"] == "state")
    removed = studio.call("remove_file", [opened["files"][0]["path"]])
    assert removed["files_version"] > opened["files_version"]
    assert [f["name"] for f in removed["files"]] == ["two.wav"]


@pytest.mark.parametrize("sr, judged_on_band", [(16000, False), (192000, True)])
def test_find_speech_at_a_high_rate_judges_the_speech_band(studio, sr, judged_on_band,
                                                           monkeypatch):
    """At 192 kHz a floor above 20 kHz can outweigh a voice on the whole band;
    a kudio whose vad takes a band is asked for 100-4000 Hz there."""
    import inspect
    asked = {}

    def vad(y, rate, band=None):
        asked["band"] = band
        return [(0.1, 0.4)]

    monkeypatch.setattr(kudio, "vad", vad)
    assert "band" in inspect.signature(kudio.vad).parameters
    studio.session.set_audio(np.zeros(sr, np.float32), sr, name="take")
    studio.call("markers.find_speech")
    assert (asked["band"] == (100.0, 4000.0)) is judged_on_band


def test_an_output_folder_in_settings_is_where_every_save_dialog_starts(studio, tmp_path):
    """Exports, images and the plugins' files all went beside the recordings
    or wherever the last save was; Settings can name one folder for them."""
    out = tmp_path / "results"
    out.mkdir()
    studio.prefs.set("last_save_dir", str(tmp_path))
    studio.call("options.set", ["output_dir", str(out)])
    answer = studio.call("choose_path", ["save"], {"suggested": "image.png"})
    assert answer["suggested"] == str(out / "image.png")
    studio.call("options.set", ["output_dir", ""])
    answer = studio.call("choose_path", ["save"], {"suggested": "image.png"})
    assert answer["suggested"] == str(tmp_path / "image.png")


def test_a_save_dialog_is_kept_out_of_a_folder_that_takes_no_file(studio, tmp_path, monkeypatch):
    """A recordings folder on a read-only share: the dialog would let the
    person pick it and the save would fail at the end."""
    from kudiostudio.web import studio as studio_module

    locked = tmp_path / "recordings"
    locked.mkdir()
    monkeypatch.setattr(studio_module, "writable", lambda folder: folder != str(locked))
    studio.prefs.set("last_save_dir", str(locked))
    drain = _listen(studio)
    for _ in range(2):
        answer = studio.call("choose_path", ["save"], {"suggested": "take.wav"})
        assert answer["suggested"] == str(Path.home() / "take.wav")
    said = [m for m in _messages(drain()) if "cannot be written to" in m]
    assert said == [f"{locked} cannot be written to: the save starts in the home folder. "
                    "Settings → Output folder sets where every save starts."]
    # an output folder that takes files is where the dialog starts again
    out = tmp_path / "results"
    out.mkdir()
    studio.call("options.set", ["output_dir", str(out)])
    assert studio.call("choose_path", ["save"], {"suggested": "take.wav"})["suggested"] == str(
        out / "take.wav")


def test_writable_tries_a_file_rather_than_asking(tmp_path):
    from kudiostudio.web.studio import writable

    assert writable(str(tmp_path))
    assert not writable(str(tmp_path / "missing"))


def test_an_output_folder_that_is_not_one_is_refused(studio, tmp_path):
    drain = _listen(studio)
    info = studio.call("options.set", ["output_dir", str(tmp_path / "nowhere")])
    assert info["values"]["output_dir"] == ""
    assert _messages(drain())[-1] == f"Not changed — {tmp_path / 'nowhere'} is not a folder."


def _bursts(sr=48000):
    """Noise with a 1 kHz burst at 0.50-0.55 s and an 8 kHz one at 1.20-1.25 s."""
    rng = np.random.default_rng(0)
    t = np.arange(2 * sr) / sr
    y = 0.01 * rng.standard_normal(t.size)
    for hz, start in ((1000, 0.50), (8000, 1.20)):
        on = (t >= start) & (t < start + 0.05)
        y[on] += 0.3 * np.sin(2 * np.pi * hz * t[on])
    return y.astype(np.float32), sr


@pytest.mark.parametrize("low, high, found", [
    (0, 0, [(0.50, 0.55), (1.20, 1.25)]),
    (500, 2000, [(0.50, 0.55)]),
    (6000, 0, [(1.20, 1.25)]),
])
def test_find_events_marks_what_stands_out_in_a_band(studio, low, high, found):
    y, sr = _bursts()
    studio.session.set_audio(y, sr, name="take")
    state = studio.call("markers.find_events", [low, high, 12])
    spans = [(m["start"], m["end"]) for m in state["markers"]]
    assert len(spans) == len(found)
    for (a, b), (x, z) in zip(spans, found):
        assert a == pytest.approx(x, abs=0.01) and b == pytest.approx(z, abs=0.01)
    assert [m["label"] for m in state["markers"]] == [f"event {n}" for n in
                                                      range(1, len(found) + 1)]


def test_find_events_names_the_band_it_searched(studio):
    drain = _listen(studio)
    y, sr = _bursts()
    studio.session.set_audio(y, sr, name="take")
    for band, said in (([0, 0], "the whole band"), ([6000, 0], "6000 Hz and up"),
                       ([500, 2000], "500–2000 Hz")):
        studio.call("markers.find_events", band + [12])
        assert _messages(drain())[-1].endswith(f"over the median in {said}.")


def test_find_events_says_when_nothing_stands_out(studio):
    drain = _listen(studio)
    studio.session.set_audio(np.zeros(16000, np.float32) + 0.01, 16000, name="flat")
    studio.call("markers.find_events", [100, 4000, 12])
    assert _messages(drain())[-1] == "No event stands 12 dB over the median in 100–4000 Hz."
    studio.call("markers.find_events", [4000, 100, 12])
    assert _messages(drain())[-1] == ("Not searched — the band runs from low to high, "
                                      "got 4000 to 100 Hz.")
