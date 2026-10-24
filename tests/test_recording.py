# -*- coding: utf-8 -*-
"""Stand-ins for the two things CI does not have, a microphone and a
browser, and a check on the real `StreamRecorder.drain()`.

`FakeRecorder` takes the place of `kudio.StreamRecorder` in the Record tests
(test_web_record.py). It hands over whatever the test feeds it, through the
same methods the Record panel reads on every tick.

`run_page` mounts a panel's page module in node, for the part of a panel
that only the page decides: which box is locked, what a button says, how a
reading is written out, what a preset writes into the boxes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading

import numpy as np
import pytest

from kudiostudio.web.server import STATIC


class FakeRecorder:
    instances = []

    def __init__(self, device=None, sr=16000, channels=1, **kwargs):
        self.sr = sr or 16000
        self.channels = channels
        self.device = device
        self.recording = False
        self.capped = False
        self._level = 0.0
        self._audio = np.zeros(0, dtype=np.float32)
        self._drained = 0
        FakeRecorder.instances.append(self)

    def start(self):
        self.recording = True
        return self

    def stop(self):
        self.recording = False
        return self._audio

    def take(self):
        return self._audio

    def level(self):
        return self._level

    def level_db(self):
        return 20 * np.log10(self._level) if self._level > 0 else float("-inf")

    def elapsed(self):
        return len(self._audio) / self.sr

    def tail(self, seconds):
        return self._audio[-int(seconds * self.sr):]

    def drain(self):
        new, self._drained = self._audio[self._drained:], len(self._audio)
        return new

    # -- test helper
    def feed(self, y, level=0.5):
        self._audio = np.concatenate([self._audio,
                                      np.asarray(y, dtype=np.float32)])
        self._level = level


#: Just enough of a document for a page module, and the ui.js it builds its
#: controls with. An event fires only when a step fires it, as in the
#: browser, where a script writing a box's value fires nothing.
PAGE = r"""
class Element {
  constructor(tag) {
    Object.assign(this, { tagName: tag, children: [], listeners: {}, found: {}, style: {},
                          dataset: {}, hidden: false, disabled: false, textContent: "",
                          title: "", className: "", value: "" });
    const names = new Set();
    this.classList = {
      add: (name) => names.add(name),
      contains: (name) => names.has(name),
      toggle: (name, on = !names.has(name)) => {
        if (on) names.add(name); else names.delete(name);
        return on;
      },
    };
  }
  get options() { return this.children; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  setAttribute(name, value) { this[name] = value; }
  querySelector(selector) { return (this.found[selector] ??= new Element("span")); }
  fire(type) { for (const fn of this.listeners[type] ?? []) fn({ target: this }); }
}
globalThis.Option = class extends Element {
  constructor(text = "", value = text) {
    super("option");
    Object.assign(this, { textContent: text, value });
  }
};
// api.js reads the session token from the page as it loads
globalThis.document = { createElement: (tag) => new Element(tag),
                        querySelector: () => ({ content: "" }) };

const ui = await import(__UI__);
const { t } = await import(__I18N__);
const { mount } = await import(__MODULE__);
const answers = __ANSWERS__;
const asked = [];
const ctx = {
  ui, t, memory: {},
  call: async (name, args = []) => {
    asked.push([name, args]);
    const answer = answers[`${name} ${JSON.stringify(args)}`] ?? answers[name];
    return Array.isArray(answer) ? answer.shift() : answer;
  },
};
const root = document.createElement("div");
const mounted = await mount(root, ctx);

function* walk(node) {
  yield node;
  for (const child of node.children) yield* walk(child);
}
// the control on the form row labelled *label*
function box(label) {
  for (const node of walk(root)) {
    const i = node.children.findIndex((c) => c.tagName === "label" && c.textContent === label);
    if (i >= 0) return node.children[i + 1].children[0];
  }
  throw new Error(`no row labelled ${label}`);
}
function text(pattern) {
  for (const node of walk(root)) {
    if (!node.children.length && pattern.test(node.textContent)) return node.textContent;
  }
  return null;
}
const primary = () => [...walk(root)].find((n) => n.tagName === "button" &&
                                                   n.className.includes("primary"));
const lastCall = (name = "call_text") => asked.filter(([n]) => n === name).pop()[1];
// the page's own calls are async; one turn of the loop lets them all land
const settle = () => new Promise((done) => setTimeout(done, 0));
async function choose(label, value) {
  box(label).value = String(value);
  box(label).fire("change");
  await settle();
}
async function type(label, value) {
  const { input } = box(label);
  input.value = String(value);
  input.fire("input");
  await settle();
}
async function click(node) {
  node.fire("click");
  await settle();
}

const seen = {};
__STEPS__
console.log(JSON.stringify(seen));
"""


def run_page(module, answers, steps):
    """Mount *module* (a path under static/js) in node and run *steps*, some
    JavaScript, after it. The page's calls are answered from *answers*: by
    ``"name [args]"`` first, then by name, and a list there is the answers
    to successive calls. A step hands the page an event through `mounted`,
    what the module's mount returned. Returns what the steps put in `seen`."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = STATIC / "js"
    source = (PAGE.replace("__UI__", json.dumps((js / "ui.js").as_uri()))
              .replace("__I18N__", json.dumps((js / "i18n.js").as_uri()))
              .replace("__MODULE__", json.dumps((js / module).as_uri()))
              .replace("__ANSWERS__", json.dumps(answers))
              .replace("__STEPS__", steps))
    done = subprocess.run([node, "--input-type=module"], input=source, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_drain_never_returns_the_same_audio_twice():
    """tail() overlaps between calls; feeding that to a recursive denoiser
    processes the same samples repeatedly and wrecks its state."""
    from kudio.core.stream import StreamRecorder

    # the real class with its buffers filled by hand, so no device is opened
    rec = StreamRecorder.__new__(StreamRecorder)
    rec._chunks, rec._frames, rec._drained = [], 0, 0
    rec._channels, rec._sr, rec._capped, rec._level = 1, 16000, False, 0.0
    rec._lock = threading.Lock()

    assert rec.drain().size == 0
    rec._chunks.append(np.arange(10, dtype=np.float32))
    assert np.array_equal(rec.drain(), np.arange(10))
    assert rec.drain().size == 0                     # nothing new
    rec._chunks.append(np.arange(10, 20, dtype=np.float32))
    assert np.array_equal(rec.drain(), np.arange(10, 20))
