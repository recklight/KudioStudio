# -*- coding: utf-8 -*-
"""The views around the clip: the marker list, the player, the Levels box
with its loudness curve, the live monitor while a take runs, and the pitch
contour over the spectrogram.

Most of it is decided in Python and tested there. What the page decides --
which marker buttons are live, when the loudness curve is up, whether a
contour is drawn on the axis in view -- is tested by running main.js under
node on a stand-in document, against a real Studio behind a real server, so
the page reads exactly what the back end sends. Those tests skip where node
is not installed, as the parse check in test_web.py does.
"""
from __future__ import annotations

import base64
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.measure import TRUE_PEAK_WARN_DBTP, measure
from kudiostudio.session import AudioSession, Marker
from kudiostudio.tools import record as record_tools
from kudiostudio.web import player as player_module
from kudiostudio.web import studio as studio_module
from kudiostudio.web.markers import format_span
from kudiostudio.web.monitor import MONITOR_LEVELS, MONITOR_REDRAW_EVERY, MONITOR_SECONDS
from kudiostudio.web.player import Player
from kudiostudio.web.server import STATIC, LocalServer
from kudiostudio.web.studio import Prefs, Studio

from test_recording import FakeRecorder

SR = 16000

#: the narrowest LUFS axis the waveform draws, as waveform.js has it
MIN_LOUDNESS_SPAN = float(re.search(
    r"const MIN_LOUDNESS_SPAN = ([\d.]+);",
    (STATIC / "js" / "waveform.js").read_text(encoding="utf-8")).group(1))


def varying(seconds: float = 12.0, dip_db: float = 14.0,
            amp: float = 0.5) -> np.ndarray:
    """A take whose level moves, so a curve has something to show."""
    t = np.arange(int(SR * seconds), dtype=np.float64) / SR
    gate = np.where((t % 4.0) < 2.0, 1.0, 10 ** (-dip_db / 20.0))
    return (amp * np.sin(2 * np.pi * 220 * t) * gate).astype(np.float32)


def noise(n: int, seed: int = 0) -> np.ndarray:
    return (0.2 * np.random.default_rng(seed).standard_normal(n)).astype(np.float32)


def _wave(path, y, sr=SR):
    kudio.save_wave(path, y, sr)
    return str(path)


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def quiet(monkeypatch):
    """Playback that reports success and plays nothing."""
    monkeypatch.setattr(kudio, "play_audio", lambda *args, **kwargs: None)


@pytest.fixture
def studio(tmp_path, quiet, monkeypatch):
    FakeRecorder.instances = []
    monkeypatch.setattr(kudio, "StreamRecorder", FakeRecorder)
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


@pytest.fixture
def opened(studio, tone):
    studio.call("open_files", [[str(tone)]])
    return studio


@pytest.fixture
def take(tmp_path):
    """Twelve seconds of `varying`: long enough for a loudness curve."""
    return _wave(tmp_path / "take.wav", varying())


def _events(studio):
    q = studio.events.subscribe()

    def drain(kind=None):
        out = []
        while not q.empty():
            e = q.get_nowait()
            if kind is None or e["type"] == kind:
                out.append(e)
        return out

    return drain


def _said(events):
    return [e["text"] for e in events if e["type"] == "message"]


# --------------------------------------------------------------------- page

#: main.js on a stand-in document. It talks to the real server through
#: node's own fetch, reads the event stream the way EventSource does, and
#: then runs what the test sends on stdin, one JSON line at a time, with
#: `$`, the two plots and the record of what the page asked for in scope.
PAGE = r"""
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const [folder, base, token] = process.argv.slice(2);
const moduleUrl = (name) => pathToFileURL(`${folder}/${name}`).href;
console.log = (...args) => console.error(...args);   // stdout is for replies

/** A canvas context that draws nothing and keeps a list of what it was told. */
function drawing(log = []) {
  const made = {
    createLinearGradient: () => ({ addColorStop() {} }),
    createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    measureText: (text) => ({ width: 7 * String(text).length }),
  };
  return new Proxy({ log }, {
    get(target, name) {
      if (typeof name === "symbol" || name === "then") return undefined;
      if (name in made) return made[name];
      if (name in target) return target[name];
      return (...args) => { log.push([name, ...args]); };
    },
    set(target, name, value) { target[name] = value; return true; },
  });
}

class Element {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.dataset = {};
    this.style = { setProperty() {} };
    this.names = new Set();
    this.classList = {
      add: (...names) => names.forEach((n) => this.names.add(n)),
      remove: (...names) => names.forEach((n) => this.names.delete(n)),
      toggle: (name, on = !this.names.has(name)) => {
        if (on) this.names.add(name); else this.names.delete(name);
        return on;
      },
      contains: (name) => this.names.has(name),
    };
    this.own = "";
    this.found = new Map();
    this.clientWidth = 900;
    this.clientHeight = 240;
  }

  get textContent() { return this.own + this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this.children = []; this.own = String(value ?? ""); }
  get innerHTML() { return this.html ?? ""; }
  set innerHTML(value) { this.children = []; this.own = ""; this.html = value; }

  append(...nodes) {
    for (const node of nodes) {
      if (typeof node === "string") { this.own += node; continue; }
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.own = ""; this.append(...nodes); }
  remove() {
    const parent = this.parentNode;
    if (parent) parent.children = parent.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  dispatch(type, extra = {}) {
    const event = { type, target: this, preventDefault() {}, stopPropagation() {}, ...extra };
    for (const fn of this.listeners[type] || []) fn(event);
  }
  querySelector(selector) {
    if (!this.found.has(selector)) this.found.set(selector, new Element());
    return this.found.get(selector);
  }
  /** Only plain tag names: all main.js asks of the elements it does not make. */
  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => node.children.forEach((child) => {
      if (child.tagName === selector.toUpperCase()) found.push(child);
      visit(child);
    });
    if (/^[a-z]+$/.test(selector)) visit(this);
    return found;
  }
  getContext() { return (this.context ||= drawing()); }
  getBoundingClientRect() {
    return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight };
  }
  setPointerCapture() {}
  contains() { return false; }
  matches() { return false; }
  focus() {}
  blur() {}
  toDataURL() { return "data:image/png;base64,"; }
}

const elements = new Map();
globalThis.document = {
  documentElement: new Element("html"),
  body: new Element("body"),
  activeElement: null,
  title: "",
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element("div", id));
    return elements.get(id);
  },
  createElement: (tag) => new Element(tag),
  querySelector: (selector) => (selector.startsWith("meta") ? { content: token } : null),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = globalThis;
globalThis.devicePixelRatio = 1;
globalThis.addEventListener = () => {};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.ResizeObserver = class {
  constructor(fn) { this.fn = fn; }
  observe() { queueMicrotask(() => this.fn([])); }
};
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(performance.now()), 0);

// what main.js finds in index.html before it changes anything: the three
// view buttons, and the loudness box, which starts disabled
for (const mode of ["stft", "mel", "mfcc"]) {
  const button = new Element("button");
  button.dataset.mode = mode;
  document.getElementById("view-mode").append(button);
}
document.getElementById("show-loudness").disabled = true;

const asked = [];       // the commands the page sent, as it sent them
const answered = [];    // the same, once the answer was back
const fetched = [];     // plot data ("peaks", "image"), once it was back
const send = globalThis.fetch;
globalThis.fetch = async (url, options = {}) => {
  const path = String(url);
  const command = path.startsWith("/api/") ? path.slice(5) : "";
  if (command) asked.push(command);
  const response = await send(new URL(path, base), options);
  if (command) answered.push(command);
  if (path.startsWith("/data/")) fetched.push(path.slice(6).split("?")[0]);
  return response;
};

const streams = [];
globalThis.EventSource = class {
  constructor(url) {
    const stop = new AbortController();
    streams.push(stop);
    send(new URL(url, base), { signal: stop.signal }).then(async (response) => {
      this.onopen?.();
      const decoder = new TextDecoder();
      let pending = "";
      for await (const chunk of response.body) {
        pending += decoder.decode(chunk, { stream: true });
        let end;
        while ((end = pending.indexOf("\n\n")) >= 0) {
          const block = pending.slice(0, end);
          pending = pending.slice(end + 2);
          if (block.startsWith("data: ")) this.onmessage?.({ data: block.slice(6) });
        }
      }
    }).catch(() => this.onerror?.());
  }
};

async function until(test, ms = 10000) {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await test();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`still waiting for ${test}`);
    await new Promise((done) => setTimeout(done, 10));
  }
}

const { TimePlot, RIGHT_GUTTER } = await import(moduleUrl("timeplot.js"));
const plots = {};
const readColors = TimePlot.prototype.readColors;
TimePlot.prototype.readColors = function () {
  plots[this.canvas.id] = this;      // "wave" and "spec", by their canvases
  return readColors.call(this);
};
await import(moduleUrl("main.js"));
const $ = (id) => document.getElementById(id);
await until(() => $("status").textContent === "Ready.");

const scope = { $, plots, asked, answered, fetched, until, drawing, RIGHT_GUTTER };
const AsyncFunction = (async () => {}).constructor;
const reply = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);
reply({ value: "ready" });
for await (const line of createInterface({ input: process.stdin })) {
  try {
    const { body, values } = JSON.parse(line);
    const run = new AsyncFunction(...Object.keys(scope), ...Object.keys(values), body);
    reply({ value: (await run(...Object.values(scope), ...Object.values(values))) ?? null });
  } catch (error) {
    reply({ error: String(error?.stack || error) });
  }
}
for (const stop of streams) stop.abort();
process.exit(0);
"""


class Page:
    """main.js running under node against *server*."""

    def __init__(self, node: str, server: LocalServer, script):
        script.write_text(PAGE, encoding="utf-8")
        self.process = subprocess.Popen(
            [node, str(script), str(STATIC / "js"), f"http://127.0.0.1:{server.port}",
             server.token],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        self._replies: queue.Queue = queue.Queue()
        self._errors = []
        self._marks = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self._errors.extend(self.process.stderr),
                         daemon=True).start()
        self._reply(30)

    def _read(self):
        for line in self.process.stdout:
            self._replies.put(line)
        self._replies.put(None)

    def _reply(self, timeout):
        try:
            line = self._replies.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("the page did not answer") from None
        if line is None:
            raise AssertionError("node stopped:\n" + "".join(self._errors))
        reply = json.loads(line)
        if "error" in reply:
            raise AssertionError(reply["error"])
        return reply["value"]

    def run(self, body: str, timeout: float = 30, **values):
        """Run *body* as an async function in the page; *values* are passed
        in under their names. Returns what it returns."""
        self.process.stdin.write(json.dumps({"body": body, "values": values}) + "\n")
        self.process.stdin.flush()
        return self._reply(timeout)

    def catch_up(self, studio) -> None:
        """Return once the page has handled every event sent so far: a line
        for the status bar goes out after them, and the page shows it."""
        self._marks += 1
        mark = f"checkpoint {self._marks}"
        studio.say(mark)
        self.run('await until(() => $("status").textContent === mark);', mark=mark)

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()


@pytest.fixture
def page(studio, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    server = LocalServer(studio, 0)
    server.start()
    p = Page(node, server, tmp_path / "page.mjs")
    yield p
    p.close()
    server.close()


#: open the loudness box once the curve has arrived
TICK_LOUDNESS = """
    const box = $("show-loudness");
    await until(() => box.disabled === false);
    box.checked = true;
    box.dispatch("change");
"""

#: switch the view with its button, wait for the image, and draw the contour
#: on a fresh context: returns how many segments were drawn
VIEW_AND_DRAW = """
    $("view-mode").children.find((b) => b.dataset.mode === mode).dispatch("click");
    await until(() => plots.spec.label === label && plots.spec.image);
    const log = [];
    plots.spec.drawOverlay(drawing(log), plots.spec.area);
    return log.filter(([name]) => name === "lineTo").length;
"""


# ------------------------------------------------------------------ markers

def test_format_span_reads_as_a_point_or_a_range():
    assert format_span(12.5, 12.5) == "0:12.500"
    assert format_span(12.5, 14.0) == "0:12.500 – 0:14.000"
    assert format_span(75.0, 75.0) == "1:15.000"


def test_the_list_mirrors_the_session(page, opened):
    rows = """
        await until(() => $("app").dataset.state === "ready");
        const list = $("marker-list");
        await until(() => list.children.length === count);
        return list.children.map((row) => row.textContent);"""
    assert page.run(rows, count=0) == []
    with opened.lock:
        opened.session.add_marker(0.5, 0.7, "noise")
    shown = page.run(rows, count=1)
    assert "noise" in shown[0]
    assert format_span(0.5, 0.7) in shown[0]      # the same reading as the status bar's
    with opened.lock:
        opened.session.clear_markers()
    assert page.run(rows, count=0) == []


def test_unnamed_markers_get_a_number(page, opened):
    with opened.lock:
        opened.session.add_marker(0.5)
    label = page.run("""
        const list = $("marker-list");
        await until(() => list.children.length === 1);
        return list.children[0].children[1].textContent;""")
    assert "marker 1" in label


def test_clicking_a_marker_asks_to_seek(page, opened):
    with opened.lock:
        opened.session.add_marker(0.42)
    page.run("""
        const list = $("marker-list");
        await until(() => list.children.length === 1);
        list.children[0].dispatch("click");
        await until(() => answered.includes("seek"));""")
    assert opened.playhead == pytest.approx(0.42)


def test_double_clicking_a_range_asks_to_select_it(page, opened):
    with opened.lock:
        opened.session.add_marker(0.2, 0.6)
    page.run("""
        const list = $("marker-list");
        await until(() => list.children.length === 1);
        list.children[0].dispatch("dblclick");
        await until(() => answered.includes("set_selection"));""")
    assert opened.session.selection() == pytest.approx((0.2, 0.6))


def test_double_clicking_a_point_selects_nothing(page, opened):
    with opened.lock:
        opened.session.add_marker(0.2)
    sent = page.run("""
        const list = $("marker-list");
        await until(() => list.children.length === 1);
        list.children[0].dispatch("dblclick");
        return asked.includes("set_selection");""")
    assert sent is False
    assert opened.session.selection() is None


def test_mark_selection_needs_one(opened):
    drain = _events(opened)
    opened.call("markers.mark_selection")
    assert opened.session.markers == ()
    assert "Select a range" in _said(drain())[-1]


def test_vad_fills_the_list(studio, recording):
    studio.call("open_files", [[str(recording)]])
    studio.call("markers.find_speech")
    markers = studio.session.markers
    assert markers, "kudio.vad found nothing in the speech fixture"
    assert all(m.label.startswith("speech") for m in markers)


def test_vad_on_silence_says_so(opened):
    opened.session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="silence")
    drain = _events(opened)
    opened.call("markers.find_speech")
    assert opened.session.markers == ()
    assert "no speech" in _said(drain())[-1]


def test_buttons_track_what_is_possible(page, opened):
    assert page.run("""
        await until(() => $("app").dataset.state === "ready");
        return $("marker-mark").disabled;""") is True
    opened.call("set_selection", [0.1, 0.2])
    assert page.run("""
        await until(() => $("marker-mark").disabled === false);
        return $("marker-remove").disabled;""") is True
    with opened.lock:
        opened.session.add_marker(0.5)
    assert page.run("""
        await until(() => $("marker-list").children.length === 1);
        return $("marker-remove").disabled;""") is True       # none picked yet
    assert page.run("""
        $("marker-list").children[0].dispatch("click");
        return $("marker-remove").disabled;""") is False


def test_plots_draw_the_markers(page, opened):
    with opened.lock:
        opened.session.set_markers([Marker(0.2, 0.4, "range"), Marker(0.8, 0.8, "point")])
    drawn = page.run("""
        await until(() => plots.wave.markers.length === 2);
        const lines = (plot) => {
          const log = [];
          plot.drawMarkers(drawing(log), plot.area);
          return log.filter(([name]) => name === "stroke").length;
        };
        return [lines(plots.wave), lines(plots.spec)];""")
    assert drawn == [2, 2]

    opened.call("markers.clear")
    assert page.run("""
        await until(() => plots.wave.markers.length === 0);
        return plots.spec.markers.length;""") == 0


def test_the_file_buttons_track_what_is_possible(page, opened):
    buttons = """return [$("marker-save").disabled, $("marker-export").disabled];"""
    page.run('await until(() => $("app").dataset.state === "ready");')
    assert page.run(buttons) == [True, True]          # no markers yet
    with opened.lock:
        opened.session.add_marker(0.5)
    page.run('await until(() => $("marker-list").children.length === 1);')
    assert page.run(buttons) == [False, False]


# ---------------------------------------------------------------- sidecars

def test_the_sidecar_sits_beside_the_audio():
    assert AudioSession.sidecar_path("a/clip.wav").name == "clip.wav.labels.json"
    # not .labels.json replacing .wav -- two clips of different formats with the
    # same stem would share one file
    assert AudioSession.sidecar_path("a/clip.flac").name == "clip.flac.labels.json"


def test_markers_round_trip_through_the_sidecar(session, tone):
    session.add_marker(0.2, 0.5, "noise")
    session.add_marker(0.8, label="click")
    written = session.save_markers()
    assert written == session.sidecar_path(tone)
    assert written.is_file()

    session.clear_markers()
    assert session.load_markers(written) == 2
    assert [(m.start, m.end, m.label) for m in session.markers] == [
        (0.2, 0.5, "noise"), (0.8, 0.8, "click")]


def test_opening_a_file_picks_up_its_sidecar(tone):
    first = AudioSession()
    first.load_file(tone)
    first.add_marker(0.3, 0.6, "kept")
    first.save_markers()

    later = AudioSession()
    later.load_file(tone)
    assert [m.label for m in later.markers] == ["kept"]


def test_opening_a_file_without_a_sidecar_is_silent(tone):
    s = AudioSession()
    messages = []
    s.message.connect(messages.append)
    s.load_file(tone)
    assert s.markers == ()
    assert not any("marker" in m for m in messages)


def test_opening_a_second_file_drops_the_first_ones_markers(tone, recording):
    """They point at moments in audio that is no longer loaded."""
    s = AudioSession()
    s.load_file(tone)
    s.add_marker(0.5, label="from the tone")
    s.load_file(recording)
    assert s.markers == ()


def test_a_broken_sidecar_says_so_rather_than_failing_silently(tone):
    AudioSession.sidecar_path(tone).write_text("{not json", encoding="utf-8")
    s = AudioSession()
    messages = []
    s.message.connect(messages.append)
    s.load_file(tone)
    assert s.has_audio is True                     # the audio still loads
    assert any("could not be read" in m for m in messages)


def test_exporting_an_audacity_label_track(session, tmp_path):
    session.add_marker(0.25, 0.75, "speech")
    path = session.save_markers(tmp_path / "labels.txt")
    assert path.read_text(encoding="utf-8") == "0.250000\t0.750000\tspeech\n"
    assert kudio.load_labels(path) == [kudio.Label(0.25, 0.75, "speech")]


# -------------------------------------------------------------------- player

def test_playhead_advances_and_stops_at_the_end(quiet):
    finished = threading.Event()
    ends = []

    def on_finished(end):
        ends.append((end, time.monotonic()))
        finished.set()

    player = Player(on_finished=on_finished)
    player_module._sounddevice()     # its first import can take longer than this clip
    playback = player.play(np.zeros(1600, dtype=np.float32), 16000, t0=0.5)  # 0.1 s
    assert player.playing is True
    assert playback.start == 0.5

    positions = [player.position]
    deadline = time.monotonic() + 5
    while not finished.wait(0.005) and time.monotonic() < deadline:
        positions.append(player.position)
    positions.append(player.position)

    assert [end for end, _ in ends] == [pytest.approx(0.6)]
    # by itself and on time: a 0.1 s clip is over well inside 300 ms
    assert ends[0][1] - playback.clock < 0.3
    assert player.playing is False
    # read off the clock, so already a little past t0 -- but from t0, not from 0
    assert 0.5 <= positions[0] < 0.6
    assert len(set(positions)) > 2, "the playhead should have moved more than once"
    assert positions == sorted(positions)
    assert positions[-1] == pytest.approx(0.6, abs=5e-3)
    player.shutdown()


def test_stop_freezes_the_playhead_without_claiming_it_finished(quiet):
    ends = []
    player = Player(on_finished=ends.append)
    player.play(np.zeros(16000, dtype=np.float32), 16000)   # 1 s
    time.sleep(0.12)
    player.stop()

    assert ends == []
    assert player.playing is False
    frozen = player.position
    assert 0.0 < frozen < 1.0
    time.sleep(0.12)
    assert player.position == frozen     # the clock is not still running
    player.shutdown()


def test_playing_again_rebases_the_timeline(quiet):
    player = Player()
    player.play(np.zeros(16000, dtype=np.float32), 16000, t0=0.0)
    time.sleep(0.08)
    player.play(np.zeros(1600, dtype=np.float32), 16000, t0=0.25)

    # `position` is read off a live monotonic clock, so whatever the scheduler
    # spends between play() and here is included. The claim under test is that
    # the timeline moved to 0.25 rather than continuing from 0.08 -- a tight
    # tolerance here only measures how busy the machine is.
    assert player.position == pytest.approx(0.25, abs=0.05)
    player.shutdown()


def test_a_backend_error_is_reported_and_stops_the_clock(monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda *args, **kwargs: "no output device")
    failed = threading.Event()
    failures = []
    player = Player(on_failed=lambda message: (failures.append(message), failed.set()))

    player.play(np.zeros(16000, dtype=np.float32), 16000)
    assert failed.wait(2.0)
    assert failures == ["no output device"]
    assert player.playing is False
    player.shutdown()


def test_no_output_device_and_no_pyaudio_is_reported_not_raised(monkeypatch):
    """A Linux box with sounddevice but no device to play on, and no PyAudio:
    kudio.play_audio falls back from sounddevice to PyAudio and raises, where
    a missing device on its own comes back as a message."""
    import kudio.core.stream as stream
    from kudio.exceptions import DependencyError

    class NoDevice:
        @staticmethod
        def play(*args, **kwargs):
            raise RuntimeError("Error querying device -1")

        @staticmethod
        def stop():
            pass

    def no_pyaudio():
        raise DependencyError("PyAudio", extra="audio")

    monkeypatch.setattr(player_module, "_sounddevice", lambda: NoDevice)
    monkeypatch.setitem(sys.modules, "sounddevice", NoDevice)
    monkeypatch.setattr(stream, "_pyaudio", no_pyaudio)
    failed = threading.Event()
    failures = []
    player = Player(on_failed=lambda message: (failures.append(message), failed.set()))

    player.play(np.zeros(16000, dtype=np.float32), 16000)
    assert failed.wait(2.0)
    assert len(failures) == 1 and "PyAudio" in failures[0]
    assert player.playing is False
    assert kudio.play_audio is stream.play_audio      # the real fallback ran
    player.shutdown()


# ------------------------------------------------------------- measurement

def test_one_pass_answers_everything_the_readout_shows():
    """Four measurements from one walk over the signal: run separately they
    would filter it four times and, worse, could describe four different
    moments of an edit."""
    y = varying()
    m = measure(y, SR)

    assert m.report is not None
    assert m.true_peak_db == pytest.approx(kudio.true_peak(y, SR), abs=1e-9)
    assert len(m.curve) > 1
    assert m.range_lu == pytest.approx(m.curve.range_lu, abs=1e-9)


def test_a_clip_shorter_than_the_window_still_measures_what_it_can():
    """The integrated figure and the true peak do not need three seconds."""
    m = measure(varying(seconds=1.0), SR)
    assert m.report is not None
    assert np.isfinite(m.true_peak_db)
    assert m.curve is None
    assert np.isnan(m.range_lu)


# ------------------------------------------------------------------ readout

def test_the_readout_shows_true_peak_and_range(page, studio, take):
    studio.call("open_files", [[take]])
    peak, spread = page.run("""
        await until(() => $("lv-problems").children.length > 0);
        return [$("lv-true-peak").textContent, $("lv-range").textContent];""")
    assert "dBTP" in peak
    assert "LU" in spread


def test_an_inter_sample_overshoot_is_reported_as_a_problem(studio):
    """`audio_report` cannot see this one: no sample is wrong, and the file
    still clips the moment anything reconstructs the waveform."""
    n = np.arange(SR * 4)
    y = (0.999 * np.sin(2 * np.pi * (SR / 4) * n / SR + np.pi / 4)).astype(np.float32)

    assert 20 * np.log10(np.max(np.abs(y))) < -2.5      # samples look safe
    assert kudio.true_peak(y, SR) > TRUE_PEAK_WARN_DBTP  # the waveform is not

    studio.session.set_audio(y, SR)
    levels = studio.call("levels")
    assert levels["true_peak_over"] is True
    assert any("dBTP" in p and "clip on conversion" in p for p in levels["problems"])


def test_a_quiet_take_is_not_accused_of_clipping(studio):
    studio.session.set_audio(varying(amp=0.05), SR)
    levels = studio.call("levels")
    assert not any("clip on conversion" in p for p in levels["problems"])


def test_clearing_takes_the_curve_down_with_it(studio, take):
    """Delete the whole clip and there is nothing left to measure: no curve
    and no figures, not the last clip's."""
    studio.call("open_files", [[take]])
    assert studio.call("levels")["curve"] is not None
    studio.call("select_all")
    assert studio.call("delete")["has_audio"] is False
    assert studio.call("levels") == {"version": studio.version, "empty": True}


def test_the_page_takes_the_figures_down_when_the_clip_goes(page, studio, take):
    studio.call("open_files", [[take]])
    page.run(TICK_LOUDNESS + "await until(() => plots.wave.loudness !== null);")
    studio.call("select_all")
    studio.call("delete")
    page.catch_up(studio)
    shown = page.run("""
        return [plots.wave.loudness, $("show-loudness").disabled,
                $("lv-loudness").textContent, $("lv-problems").children.length];""")
    assert shown == [None, True, "—", 0]


# ---------------------------------------------------------------- the curve

def test_the_curve_is_held_but_not_drawn_until_asked(page, studio, take):
    studio.call("open_files", [[take]])
    shown = page.run("""
        const box = $("show-loudness");
        await until(() => box.disabled === false);   // the curve is here
        const before = plots.wave.loudness;
        box.checked = true;
        box.dispatch("change");
        return [before, plots.wave.loudness !== null];""")
    assert shown == [None, True]


def test_turning_it_on_and_off_costs_no_measurement(page, studio, take):
    studio.call("open_files", [[take]])
    measured, kept = page.run(TICK_LOUDNESS + """
        const held = plots.wave.loudness.lufs;
        box.checked = false;
        box.dispatch("change");
        box.checked = true;
        box.dispatch("change");
        return [asked.filter((name) => name === "levels").length,
                plots.wave.loudness.lufs === held];""")
    assert measured == 1
    assert kept is True


def test_silence_breaks_the_line_rather_than_dropping_to_a_floor(page, studio, tmp_path):
    y = varying(seconds=12.0)
    y[int(4 * SR):int(8 * SR)] = 0.0
    assert np.isneginf(kudio.loudness_over_time(y, SR, window=kudio.MOMENTARY).lufs).any()

    studio.call("open_files", [[_wave(tmp_path / "gap.wav", y)]])
    lufs = studio.call("levels")["curve"]["lufs"]
    assert None in lufs, "silence has to become a gap, not a value"
    # the short-term window sees the K-weighting filter's tail die away
    # through the pause, finite and far below anything audible; drawn, it
    # would pull the axis down to that floor
    assert all(v is None or (np.isfinite(v) and v >= studio_module.SILENCE_LUFS)
               for v in lufs)
    pieces = sum(1 for i, v in enumerate(lufs)
                 if v is not None and (i == 0 or lufs[i - 1] is None))

    starts = page.run(TICK_LOUDNESS + """
        const log = [];
        plots.wave.drawLoudness(drawing(log), plots.wave.area);
        return log.filter(([name]) => name === "moveTo").length;""")
    assert starts == pieces >= 2


def test_a_steady_take_does_not_have_its_dither_magnified(page, studio, tmp_path):
    """Autoscaling to a tenth of a dB draws a level that looks all over the
    place when it is in fact perfectly steady."""
    t = np.arange(SR * 10) / SR
    steady = (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    studio.call("open_files", [[_wave(tmp_path / "steady.wav", steady)]])
    lo, hi = page.run(TICK_LOUDNESS + """
        return [plots.wave.loudness.lo, plots.wave.loudness.hi];""")
    assert hi - lo >= MIN_LOUDNESS_SPAN


def test_new_audio_replaces_the_curve_rather_than_keeping_it(page, studio, take, tone,
                                                             monkeypatch):
    studio.call("open_files", [[take]])
    page.run(TICK_LOUDNESS + "await until(() => plots.wave.loudness !== null);")

    # the next measurement waits, so what the page shows meanwhile can be seen
    gate = threading.Event()
    real = studio_module.measure
    monkeypatch.setattr(studio_module, "measure", lambda y, sr: gate.wait(10) and real(y, sr))
    studio.call("open_files", [[str(tone)]])   # one second: too short for a curve
    try:
        meanwhile = page.run("""
            await until(() => asked.filter((name) => name === "levels").length === 2);
            return plots.wave.loudness;""")
    finally:
        gate.set()
    assert meanwhile is None                  # not the last clip's curve over this one

    curve, box = page.run("""
        await until(() => $("loudness-wrap").title.includes("shorter than"));
        return [plots.wave.loudness, $("show-loudness").disabled];""")
    assert curve is None
    assert box is True
    assert studio.call("levels")["curve"] is None


def test_a_live_take_drops_the_curve(page, studio, take):
    studio.call("open_files", [[take]])
    page.run(TICK_LOUDNESS + "await until(() => plots.wave.loudness !== null);")

    with studio.lock:
        studio.monitor.start(SR)
    curve, same, gutter = page.run("""
        await until(() => $("app").classList.contains("live"));
        const right = plots.wave.margin.right;
        return [plots.wave.loudness, right === plots.spec.margin.right,
                right === RIGHT_GUTTER];""")
    assert curve is None
    assert same is True and gutter is False


# ------------------------------------------------------------ axis alignment

def test_the_two_plots_start_at_the_same_place(page, opened):
    """They share one view, which makes them show the same stretch of time
    and does nothing about where it lands on screen: an axis wider on one
    than on the other puts the same instant in two places."""
    left, x = page.run("""
        await until(() => plots.spec.image && plots.wave.chunk);
        return [[plots.wave.margin.left, plots.spec.margin.left],
                [plots.wave.timeToX(0.5), plots.spec.timeToX(0.5)]];""")
    assert left[0] == left[1]
    assert x[0] == x[1]


@pytest.mark.parametrize("sr", [8000, 16000, 48000])
def test_the_rate_does_not_move_one_plot_and_not_the_other(page, studio, tmp_path, sr):
    y = (0.4 * np.sin(2 * np.pi * 220 * np.arange(4 * sr) / sr)).astype(np.float32)
    studio.call("open_files", [[_wave(tmp_path / f"{sr}.wav", y, sr)]])
    places = set()
    for mode, label in (("stft", "Frequency"), ("mel", "Mel band"),
                        ("mfcc", "MFCC coefficient")):
        x = page.run("""
            $("view-mode").children.find((b) => b.dataset.mode === mode).dispatch("click");
            await until(() => plots.spec.label === label && plots.spec.image);
            return [plots.wave.timeToX(1), plots.spec.timeToX(1)];""", mode=mode, label=label)
        assert x[0] == x[1], mode
        places.add(x[0])
    assert len(places) == 1


def test_the_loudness_gutter_is_reserved_on_both_plots_or_neither(page, studio, take):
    """A gutter on one and not the other slides them apart -- which is the
    alignment the shared axis exists to guarantee."""
    studio.call("open_files", [[take]])
    off, on, off_again, gutter = page.run("""
        const box = $("show-loudness");
        await until(() => box.disabled === false);
        const right = () => [plots.wave.margin.right, plots.spec.margin.right];
        const seen = [right()];
        box.checked = true;
        box.dispatch("change");
        seen.push(right());
        box.checked = false;
        box.dispatch("change");
        seen.push(right());
        return [...seen, RIGHT_GUTTER];""")
    assert on == [gutter, gutter]
    assert off == off_again and off[0] == off[1] != gutter


# ------------------------------------------------------- the live monitor

def test_nothing_is_live_until_a_take_starts(studio):
    assert studio.monitor.sr == 0
    assert studio.monitor.stream is None
    assert studio.call("state")["monitoring"] is False


def test_starting_a_take_builds_a_stream_at_the_take_rate(studio):
    studio.monitor.start(8000)
    assert studio.monitor.sr == 8000
    assert studio.monitor.stream.sr == 8000
    assert studio.monitor.stream.window_seconds == pytest.approx(MONITOR_SECONDS, abs=0.05)


def test_a_second_take_starts_from_an_empty_window(studio):
    monitor = studio.monitor
    monitor.start(SR)
    monitor.push(noise(SR), SR)
    assert monitor.stream.frames_seen > 0

    monitor.start(SR)
    assert monitor.stream.frames_seen == 0
    assert monitor.wave.size == 0


def test_audio_after_stop_does_not_bring_the_live_view_back(studio):
    monitor = studio.monitor
    monitor.start(SR)
    monitor.stop()
    drain = _events(studio)
    monitor.push(noise(4096), SR)
    assert monitor.running is False
    assert not [e for e in drain() if e["type"] == "monitor"]


def test_a_rate_change_mid_stream_restarts_the_window(studio):
    monitor = studio.monitor
    monitor.start(SR)
    monitor.push(noise(4096), SR)
    monitor.push(noise(4096), 8000)
    assert monitor.sr == 8000
    assert monitor.stream.sr == 8000


def test_stopping_leaves_the_last_picture_up(page, studio):
    """Blanking it would hide the end of what was just recorded."""
    with studio.lock:
        studio.monitor.start(SR)
        for _ in range(MONITOR_REDRAW_EVERY):
            studio.monitor.push(noise(4096), SR)
    page.run("await until(() => plots.spec.image && plots.wave.chunk);")

    with studio.lock:
        studio.monitor.stop()
    assert studio.monitor.sr == 0
    assert studio.monitor.stream is None
    page.catch_up(studio)
    assert page.run("""
        return [$("app").classList.contains("live"), Boolean(plots.spec.image),
                Boolean(plots.wave.chunk)];""") == [False, True, True]


def test_every_column_is_computed_once_as_it_lands(studio):
    """The whole reason this is affordable. Drawing is throttled; analysis is
    not -- a skipped push would lose audio, a skipped picture loses nothing."""
    monitor = studio.monitor
    monitor.start(SR)
    hop = monitor.stream.hop_length
    n_fft = monitor.stream.n_fft

    total = 0
    for _ in range(10):
        monitor.push(noise(4096, seed=total), SR)
        total += 4096

    expected = (total - n_fft) // hop + 1
    assert monitor.stream.frames_seen == expected


def test_the_redraw_is_throttled_but_the_audio_is_not(studio):
    monitor = studio.monitor
    drain = _events(studio)
    monitor.start(SR)
    for _ in range(MONITOR_REDRAW_EVERY - 1):
        monitor.push(noise(1600), SR)
    assert monitor.stream.frames_seen > 0
    assert [e for e in drain("monitor") if "image" in e] == []      # not drawn yet

    monitor.push(noise(1600), SR)
    pictures = [e for e in drain("monitor") if "image" in e]
    assert len(pictures) == 1
    # coloured on the fixed live range, not on one picked from the picture
    lo, hi = MONITOR_LEVELS
    scaled = np.clip((monitor.stream.columns() - lo) / (hi - lo), 0.0, 1.0)
    expected = np.round(scaled * 255).astype(np.uint8).T[::-1]
    image = pictures[0]["image"]
    pixels = np.frombuffer(base64.b64decode(image["data"]), np.uint8)
    assert np.array_equal(pixels.reshape(image["rows"], image["columns"]), expected)


def test_the_waveform_keeps_only_the_window(studio):
    studio.monitor.start(SR)
    studio.monitor.push(noise(SR * 10), SR)
    assert studio.monitor.wave.size == int(MONITOR_SECONDS * SR)


def test_an_empty_chunk_is_harmless(studio):
    studio.monitor.start(SR)
    studio.monitor.push(np.zeros(0, dtype=np.float32), SR)
    assert studio.monitor.stream.frames_seen == 0


def test_multichannel_audio_is_mixed_down_not_flattened(studio):
    """Flattening (n, channels) interleaves them into a signal nobody
    recorded -- `SpectrogramStream` refuses 2-D input for that reason, so the
    monitor has to mix down before it gets there."""
    monitor = studio.monitor
    left = noise(4096, seed=1)
    right = noise(4096, seed=2)

    monitor.start(SR)
    monitor.push(np.stack([left, right], axis=1), SR)
    stereo = monitor.stream.columns()

    monitor.start(SR)
    monitor.push((left + right) / 2.0, SR)
    assert np.array_equal(stereo, monitor.stream.columns())


def test_the_mel_view_streams_mel_bands(studio):
    studio.call("set_analysis", kwargs={"mode": "mel", "n_mels": 48})
    studio.monitor.start(SR)
    studio.monitor.push(noise(8192), SR)

    assert studio.monitor.stream.n_mels == 48
    assert studio.monitor.stream.columns().shape[1] == 48


def test_mfcc_falls_back_to_the_stft_and_says_so(studio):
    studio.call("set_analysis", kwargs={"mode": "mfcc"})
    drain = _events(studio)
    studio.monitor.start(SR)
    assert studio.monitor.stream.n_mels == 0
    assert "MFCC has no live form" in drain("monitor")[0]["call"]


def test_changing_the_geometry_mid_take_restarts_the_picture(studio):
    studio.monitor.start(SR)
    studio.monitor.push(noise(SR), SR)
    assert studio.monitor.stream.frames_seen > 0

    studio.call("set_analysis", kwargs={"n_fft": 1024})
    assert studio.monitor.stream.n_fft == 1024
    assert studio.monitor.stream.frames_seen == 0


def test_the_caption_shows_the_live_call(page, studio):
    with studio.lock:
        studio.monitor.start(SR)
    live = page.run("""
        await until(() => $("app").classList.contains("live"));
        return $("call-hint").textContent;""")
    assert "SpectrogramStream" in live
    assert "push(chunk)" in live

    with studio.lock:
        studio.monitor.stop()
    after = page.run("""
        await until(() => !$("app").classList.contains("live"));
        return $("call-hint").textContent;""")
    assert after and "SpectrogramStream" not in after


def test_a_state_mid_take_leaves_the_live_caption_alone(page, studio):
    """The window state goes out whenever anything changes, a take or not;
    the caption is the stream's until the take ends."""
    with studio.lock:
        studio.monitor.start(SR)
    page.run('await until(() => $("app").classList.contains("live"));')
    studio._push_state()
    page.catch_up(studio)
    assert "SpectrogramStream" in page.run('return $("call-hint").textContent;')


def test_the_clips_marks_are_not_drawn_over_a_take(page, opened):
    """The plots show the take while it runs, on a timeline of its own; a
    selection, a marker or the playhead from the clip would point at nothing
    in it."""
    opened.session.add_marker(0.3, 0.5, "here")
    opened.call("set_selection", [0.2, 0.6])
    opened.call("seek", [0.4])
    page.catch_up(opened)
    with opened.lock:
        opened.monitor.start(SR)
    page.run('await until(() => $("app").classList.contains("live"));')
    opened._push_state()
    page.catch_up(opened)
    drawn = page.run("""
        return [plots.wave.markers.length, plots.spec.markers.length,
                plots.wave.selection, plots.spec.selection,
                plots.wave.playhead, plots.spec.playhead];""")
    assert drawn == [0, 0, None, None, None, None]


# ------------------------------------------------------- real audio wins

def _record(studio, chunks):
    """An open-ended take through the Record panel, fed *chunks* one tick
    at a time, then stopped and committed."""
    panel = studio.panel("record")
    panel._tick_loop = lambda: None          # ticked by hand below
    take = [record_tools.MODE_STREAM, 3, SR, 1, None, ""]
    studio.call("record.record", take)
    recorder = FakeRecorder.instances[-1]
    for chunk in chunks:
        recorder.feed(chunk)
        with studio.lock:
            panel.tick()
    studio.call("record.record", take)       # the same button, now Stop


def test_committing_a_take_ends_the_live_view(studio):
    drain = _events(studio)
    _record(studio, [noise(4096, seed=i) for i in range(MONITOR_REDRAW_EVERY)])

    assert studio.monitor.sr == 0
    assert studio.monitor.stream is None
    assert len(studio.session.y) == 4096 * MONITOR_REDRAW_EVERY
    order = [e["type"] for e in drain()
             if (e["type"] == "monitor" and e["on"] is False)
             or (e["type"] == "state" and e["state"]["has_audio"])]
    assert order[0] == "monitor"             # the live view ends before the take lands
    # the live dBFS range is not the offline log-power range; keeping it would
    # colour the committed take by a scale it was never measured on
    image, _ = studio.image()
    assert image.levels != MONITOR_LEVELS


def test_a_take_the_length_of_the_window_is_not_the_same_clip(page, studio):
    """The page keeps the zoom and the amplitude axis across an edit that
    leaves the length alone. A take as long as the live window, after
    another just as long, is new audio all the same: drawn from its own
    picture, on its own axis -- not on the live view's full scale."""
    each = int(MONITOR_SECONDS * SR) // MONITOR_REDRAW_EVERY
    landed = """
        await until(() => plots.spec.version === version && plots.spec.image
                          && plots.wave.version === version && plots.wave.chunk);
        return plots.wave.peak;"""
    _record(studio, [noise(each, seed=i) for i in range(MONITOR_REDRAW_EVERY)])
    page.run(landed, version=studio.version)

    _record(studio, [0.05 * noise(each, seed=10 + i) for i in range(MONITOR_REDRAW_EVERY)])
    assert studio.session.duration == pytest.approx(MONITOR_SECONDS)
    peak = page.run(landed, version=studio.version)
    assert peak == pytest.approx(float(np.max(np.abs(studio.session.y))), rel=1e-3)
    image, _ = studio.image()
    assert image.levels != MONITOR_LEVELS


# ------------------------------------------------------------------ overlay

def test_a_contour_is_drawn_over_the_stft(page, opened):
    with opened.lock:
        opened.set_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    drawn = page.run("""
        await until(() => plots.spec.overlay && plots.spec.yUnits === "Hz");
        const log = [];
        plots.spec.drawOverlay(drawing(log), plots.spec.area);
        return log.filter(([name]) => name === "lineTo").length;""")
    assert drawn == 49


def test_the_contour_survives_a_redraw(page, opened):
    """Every refresh replaces the image; a curve drawn once would vanish with
    the first parameter change."""
    with opened.lock:
        opened.set_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    drawn = page.run("""
        await until(() => plots.spec.overlay && plots.spec.image);
        const before = fetched.filter((path) => path === "image").length;
        $("n_fft").value = "1024";
        $("n_fft").dispatch("change");
        await until(() => fetched.filter((path) => path === "image").length > before);
        const log = [];
        plots.spec.drawOverlay(drawing(log), plots.spec.area);
        return log.filter(([name]) => name === "lineTo").length;""")
    assert opened.settings.n_fft == 1024
    assert opened.call("overlay") is not None
    assert drawn == 49


def test_the_contour_is_hidden_where_the_axis_is_not_hertz(page, opened):
    """On a mel image "180" is band 180, which is a plausible-looking line in
    the wrong place."""
    with opened.lock:
        opened.set_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    page.run('await until(() => plots.spec.overlay && plots.spec.yUnits === "Hz");')

    drawn = {mode: page.run(VIEW_AND_DRAW, mode=mode, label=label)
             for mode, label in (("mel", "Mel band"), ("mfcc", "MFCC coefficient"),
                                 ("stft", "Frequency"))}
    assert drawn == {"mel": 0, "mfcc": 0, "stft": 49}


def test_new_audio_drops_the_contour(studio, recording, tone):
    studio.call("open_files", [[str(recording)]])
    studio.call("pitch.track", [65, 400, 2048, 10])
    assert studio.jobs.wait(60)
    assert studio.call("overlay") is not None

    studio.call("open_files", [[str(tone)]])
    assert studio.call("overlay") is None


def test_any_edit_drops_a_curve_whoever_drew_it(opened):
    """A plugin panel draws on the spectrogram too, and does not always
    know when the audio under its curve has moved."""
    opened.set_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    opened.session.set_audio(opened.session.y[4000:], SR, undo_label="trim")
    assert opened.call("overlay") is None


def test_switching_to_the_original_drops_the_curve(opened):
    opened.session.set_audio(opened.session.y * 0.5, SR, undo_label="gain",
                             keep_original=True)
    opened.set_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    opened.call("set_compare", [True])
    assert opened.call("overlay") is None


def test_a_live_take_hides_the_contour(opened):
    opened.set_overlay(np.linspace(0.0, 1.0, 50), np.full(50, 180.0))
    drain = _events(opened)

    opened.monitor.start(SR)
    assert opened.call("overlay") is None
    assert {"type": "overlay", "curve": None} in drain()


def test_an_empty_contour_is_simply_no_contour(page, opened):
    with opened.lock:
        opened.set_overlay(np.zeros(0), np.zeros(0))
    page.catch_up(opened)
    assert page.run("""
        const log = [];
        plots.spec.drawOverlay(drawing(log), plots.spec.area);
        return [plots.spec.overlay, log.length];""") == [None, 0]


def test_the_two_rolling_plots_agree_about_where_now_is(studio):
    """They share a time axis. The spectrogram is always the full window wide
    with the newest column at the right; a waveform drawn from zero would put
    the same instant in two different places."""
    monitor = studio.monitor
    monitor.start(SR)
    for _ in range(MONITOR_REDRAW_EVERY):
        monitor.push(noise(SR // 4), SR)          # 1 s into a 3 s window

    picture = monitor.snapshot()
    assert picture["span"] == pytest.approx(MONITOR_SECONDS, abs=0.05)
    wave = picture["wave"]
    assert wave["kind"] == "peaks"
    # the page draws a column at its middle, so the last one sits within half
    # a column of the true end rather than exactly on it
    last = wave["start"] + (len(wave["values"]) // 2 - 0.5) * wave["step"]
    assert wave["start"] == pytest.approx(MONITOR_SECONDS - 1.0, abs=0.05)
    assert MONITOR_SECONDS - 0.1 < last <= MONITOR_SECONDS + 0.05


def test_a_full_window_starts_at_zero_again(studio):
    monitor = studio.monitor
    monitor.start(SR)
    monitor.push(noise(int(2 * MONITOR_SECONDS * SR)), SR)

    wave = monitor.snapshot()["wave"]
    assert wave["kind"] == "peaks"
    last = wave["start"] + (len(wave["values"]) // 2 - 0.5) * wave["step"]
    assert wave["start"] == pytest.approx(0.0, abs=0.02)
    assert MONITOR_SECONDS - 0.1 < last <= MONITOR_SECONDS + 0.05
