# -*- coding: utf-8 -*-
"""Regenerate every image the README embeds.

The screenshots drift out of date silently -- a caption changes, a control
moves, and nobody notices because nothing fails. Run this after any change to
the window and commit whatever moved::

    python tools/screenshots.py                # docs/images/*.png
    python tools/screenshots.py --lang zh_TW   # docs/images/*.zh-TW.png

The window is served from this process, as `kudiostudio` serves it, and drawn
by a headless Chrome, Chromium or Edge driven through the DevTools protocol.
Whichever of those is installed is used; KUDIOSTUDIO_BROWSER names another.
The protocol runs over a websocket, which the standard library does not
have, so the few lines of one this needs are written out below rather than
installed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"

# the README shows the window a clone of this repository builds, not the
# plugins this machine happens to have installed
os.environ["KUDIOSTUDIO_NO_PLUGINS"] = "1"
sys.path.insert(0, str(ROOT))

import numpy as np                                          # noqa: E402

import kudio                                                # noqa: E402
from kudiostudio import i18n                                # noqa: E402
from kudiostudio.web.server import LocalServer              # noqa: E402
from kudiostudio.web.studio import Prefs, Studio            # noqa: E402

WINDOW_SIZE = (1600, 900)
#: tall enough that the longest panel fits without scrolling
PANEL_HEIGHT = 1800
SR = 16000


# ------------------------------------------------------------------- audio

def demo_clip(path: Path) -> Path:
    """2.5 s of something with structure worth looking at: a rising chirp,
    three transients and a noise bed."""
    t = np.arange(int(2.5 * SR), dtype=np.float32) / SR
    chirp = 0.35 * np.sin(2 * np.pi * (300 + 700 * t / t[-1]) * t)
    y = chirp + 0.06 * np.random.default_rng(7).standard_normal(len(t))
    for at in (0.45, 1.45, 2.15):
        i = int(at * SR)
        y[i:i + 200] += np.linspace(0.9, 0.0, 200)
    kudio.save_wave(path, (y / np.max(np.abs(y)) * 0.85).astype(np.float32), SR)
    return path


def drifting_clip(path: Path) -> Path:
    """20 s of speech-like audio whose level wanders, for the loudness shot.

    A steady tone would draw a flat line, which demonstrates nothing; this is
    a take where somebody leaned away from the microphone in the middle and
    came back, which is what the curve exists to find.
    """
    t = np.arange(int(20 * SR), dtype=np.float64) / SR
    syllables = 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)
    # down 13 dB between 7 s and 13 s, easing in and out over half a second
    away = np.clip((t - 7.0) / 0.5, 0, 1) * np.clip((13.0 - t) / 0.5, 0, 1)
    level = 10 ** (-13.0 * away / 20.0)
    voice = sum(np.sin(2 * np.pi * 180 * k * t) / k for k in range(1, 12)) / 6
    y = voice * syllables * level
    y = y / np.max(np.abs(y)) * 0.7
    y += 0.004 * np.random.default_rng(3).standard_normal(len(t))
    kudio.save_wave(path, y.astype(np.float32), SR)
    return path


def small_corpus(root: Path) -> Path:
    """Three tones and two noises, mixed at three SNRs: a manifest with rows
    enough to show, written the way the Synthesize panel writes one."""
    (root / "clean").mkdir(parents=True)
    (root / "noise").mkdir()
    t = np.arange(SR) / SR
    for i in range(3):
        kudio.save_wave(root / "clean" / f"talker{i}.wav",
                        (0.05 * np.sin(2 * np.pi * (180 + 40 * i) * t)).astype(np.float32), SR)
    for j, name in enumerate(("cafe", "street")):
        kudio.save_wave(root / "noise" / f"{name}.wav",
                        (0.01 * np.random.default_rng(j).standard_normal(2 * SR)
                         ).astype(np.float32), SR)
    syx = kudio.Synthesizer(str(root / "clean"), str(root / "noise"),
                            out_path=str(root / "mixed"), snr_ratio=[-5, 0, 5])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        syx.syn(mode="inc", seed=1)
    return kudio.save_manifest(root / "mixed" / "manifest.json", syx.manifest())


# ---------------------------------------------------------------- the browser

def browser() -> str:
    named = os.environ.get("KUDIOSTUDIO_BROWSER")
    candidates = [named] if named else []
    candidates += [shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser",
                                                   "microsoft-edge", "chrome", "msedge")]
    candidates += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("no Chrome, Chromium or Edge found; set KUDIOSTUDIO_BROWSER to one")


class DevTools:
    """Just enough of the DevTools protocol: one page, commands and replies."""

    def __init__(self, url: str):
        parts = urlsplit(url)
        self.sock = socket.create_connection((parts.hostname, parts.port), timeout=120)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {parts.path} HTTP/1.1\r\nHost: {parts.netloc}\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                           ).encode())
        self.pending = b""
        while b"\r\n\r\n" not in self.pending:
            self.pending += self._recv()
        head, self.pending = self.pending.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))
        self.last_id = 0

    def _recv(self) -> bytes:
        chunk = self.sock.recv(1 << 16)
        if not chunk:
            raise ConnectionError("the browser closed the connection")
        return chunk

    def _read(self, n: int) -> bytes:
        while len(self.pending) < n:
            self.pending += self._recv()
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def _send(self, opcode: int, payload: bytes) -> None:
        # a client's frames are masked, by the protocol's rule
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            length = bytes([0x80 | n])
        elif n < 1 << 16:
            length = bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            length = bytes([0x80 | 127]) + struct.pack(">Q", n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode]) + length + mask + body)

    def _message(self) -> str:
        parts = []
        while True:
            first, second = self._read(2)
            opcode, n = first & 0x0F, second & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if opcode == 0x9:                       # ping
                self._send(0xA, payload)
                continue
            if opcode == 0x8:
                raise ConnectionError("the browser closed the connection")
            parts.append(payload)
            if first & 0x80:
                return b"".join(parts).decode("utf-8")

    def call(self, method: str, **params):
        self.last_id += 1
        mine = self.last_id
        self._send(0x1, json.dumps({"id": mine, "method": method, "params": params}).encode())
        while True:
            reply = json.loads(self._message())
            if reply.get("id") == mine:          # events in between are not wanted
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})


def open_browser(profile: Path) -> Tuple[subprocess.Popen, DevTools]:
    process = subprocess.Popen(
        [browser(), "--headless=new", f"--user-data-dir={profile}", "--remote-debugging-port=0",
         "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
         "--force-color-profile=srgb", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 30
    while not port_file.is_file() or not port_file.read_text().strip():
        if time.monotonic() > deadline or process.poll() is not None:
            raise SystemExit("the browser did not start")
        time.sleep(0.1)
    port = int(port_file.read_text().split()[0])
    targets = json.loads(urlopen(f"http://127.0.0.1:{port}/json/list").read())
    page = next(t for t in targets if t.get("type") == "page")
    return process, DevTools(page["webSocketDebuggerUrl"])


# ------------------------------------------------------------------ the page

class Page:
    def __init__(self, tools: DevTools, suffix: str, out: Path, language: str = "en"):
        self.tools = tools
        self.suffix = suffix
        self.language = language
        self.out = out
        self.size(*WINDOW_SIZE)

    def size(self, width: int, height: int) -> None:
        self.tools.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                        deviceScaleFactor=1, mobile=False)

    def js(self, expression: str):
        result = self.tools.call("Runtime.evaluate", expression=expression,
                                 awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(f"{expression[:80]}: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 60.0, what: str = "") -> None:
        deadline = time.monotonic() + timeout
        while not self.js(f"Boolean({expression})"):
            if time.monotonic() > deadline:
                raise SystemExit(f"timed out waiting for {what or expression}")
            time.sleep(0.1)

    @staticmethod
    def settle(seconds: float = 0.8) -> None:
        """Long enough for a plot to fetch what it shows and draw it."""
        time.sleep(seconds)

    def open(self, url: str) -> None:
        self.tools.call("Page.navigate", url=url)
        self.wait("document.querySelector('#rail button')", what="the tool rail")

    def tool(self, name: str) -> None:
        self.js(f"document.querySelector('[data-tool=\"{name}\"]').click()")
        if name != "inspect":
            self.wait(f"document.querySelector('[data-panel-host=\"{name}\"] .panel-section')",
                      what=f"the {name} panel")
        self.settle(0.5)

    def click(self, expression: str) -> None:
        self.js(f"({expression}).click()")

    def button(self, panel: str, text: str) -> str:
        """An expression for the button in *panel* whose label is *text*, in
        English; the page shows it in the window's language."""
        label = i18n.translate(text, self.language)
        return (f"[...document.querySelectorAll('[data-panel-host=\"{panel}\"] button')]"
                f".find((b) => b.textContent === {json.dumps(label)})")

    def save(self, name: str, element: Optional[str] = None, rect: Optional[str] = None) -> None:
        """The page, or the box of *element* (a JS expression), or *rect*
        (one that gives [x, y, width, height]), as docs/images/<name>.png."""
        params = {"format": "png"}
        if element:
            rect = (f"(() => {{ const r = ({element}).getBoundingClientRect();"
                    f" return [r.x, r.y, r.width, r.height]; }})()")
        if rect:
            x, y, w, h = self.js(rect)
            params["clip"] = {"x": x, "y": y, "width": w, "height": h, "scale": 1}
        data = self.tools.call("Page.captureScreenshot", **params)["data"]
        path = self.out / f"{name}{self.suffix}.png"
        path.write_bytes(base64.b64decode(data))
        print(f"  {path}  {path.stat().st_size // 1024} kB")

    def save_panel(self, name: str, panel: str) -> None:
        """The panel column, down to the end of what *panel* shows."""
        self.save(name, rect=f"""(() => {{
            const column = document.getElementById("panel").getBoundingClientRect();
            const host = document.querySelector('[data-panel-host="{panel}"]');
            const bottom = host.getBoundingClientRect().top + host.scrollHeight + 16;
            return [column.x, column.y, column.width, Math.min(column.height,
                                                               bottom - column.y)];
        }})()""")


STAGE = "document.getElementById('stage')"
SECTION_OF = "document.getElementById('{}').closest('section')"


# ------------------------------------------------------------------- scenes

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the README's images.")
    parser.add_argument("--lang", default="en", help="en, or zh_TW for the Chinese README")
    parser.add_argument("--out", type=Path, default=IMAGES,
                        help="where the images go (default: docs/images)")
    args = parser.parse_args(argv)
    suffix = "" if args.lang == "en" else "." + args.lang.replace("_", "-")
    args.out.mkdir(parents=True, exist_ok=True)
    kudio.play_audio = lambda *a, **k: None     # never make noise from a script

    with tempfile.TemporaryDirectory() as raw:
        scratch = Path(raw)
        studio = Studio(prefs=Prefs(scratch / "window.json"), language=args.lang)
        server = LocalServer(studio, 0)
        server.start()
        process, tools = open_browser(scratch / "profile")
        try:
            page = Page(tools, suffix, args.out, args.lang)
            shoot(page, studio, server, scratch)
        finally:
            process.kill()
            server.close()
            studio.shutdown()
    print("done")
    return 0


def open_demo(page: Page, studio: Studio, clip: Path) -> None:
    studio.call("open_files", [[str(clip)]])
    session = studio.session
    with studio.lock:
        # the three transients the demo clip plants, so the marker overlay and
        # the list both have something real in them
        session.add_marker(0.45, label="transient 1")
        session.add_marker(1.45, 1.60, "transient 2")
        session.add_marker(2.15, label="transient 3")
        session.set_selection(0.8, 1.6)
    page.wait("document.getElementById('app').dataset.state === 'ready'", what="the clip")
    page.settle(1.5)


def shoot(page: Page, studio: Studio, server: LocalServer, scratch: Path) -> None:
    demo = demo_clip(scratch / "demo.wav")
    page.open(server.url)
    open_demo(page, studio, demo)

    print("window and analysis views")
    page.tool("effects")
    page.save("main")
    page.save("sidebar_markers", element=SECTION_OF.format("marker-list"))
    page.save("analysis_stft", element=STAGE)
    for mode in ("mel", "mfcc"):
        page.click(f"document.querySelector('#view-mode button[data-mode=\"{mode}\"]')")
        page.settle(1.5)
        page.save(f"analysis_{mode}", element=STAGE)
    page.click("document.querySelector('#view-mode button[data-mode=\"stft\"]')")
    page.settle(1.0)

    print("tool panels")
    page.size(WINDOW_SIZE[0], PANEL_HEIGHT)
    for name in ("effects", "enhance", "synthesize"):
        page.tool(name)
        page.save_panel(f"panel_{name}", name)

    print("pitch")
    # The demo clip sweeps 300 -> 1700 Hz. Left on the speech preset the top
    # of that is outside the range, and pYIN answers with the sub-octave --
    # a plausible line in the wrong place, which is exactly what the panel
    # warns about. Naming the real range is the point of the control.
    with studio.lock:
        studio.session.clear_selection()
    page.tool("pitch")
    page.js("""(() => {
        const host = document.querySelector('[data-panel-host="pitch"]');
        const range = host.querySelector('select');
        range.value = String(range.options.length - 1);
        range.dispatchEvent(new Event('change'));
        const [fmin, fmax] = host.querySelectorAll('input.num');
        for (const [box, value] of [[fmin, 250], [fmax, 1800]]) {
          box.value = value;
          box.dispatchEvent(new Event('change'));
        }
    })()""")
    page.settle(0.5)
    page.click(page.button("pitch", "Track pitch"))
    page.settle(0.5)
    if not studio.jobs.wait(120):
        raise SystemExit("the Pitch panel never finished")
    page.settle(1.0)
    page.save_panel("panel_pitch", "pitch")
    page.size(*WINDOW_SIZE)
    with studio.lock:
        studio.session.set_selection(0.8, 1.6)
    page.settle(1.5)
    page.save("analysis_pitch", element=STAGE)

    print("loudness over time")
    # A 2.5 s clip is shorter than the 3 s short-term window, so it has no
    # curve at all -- and a picture of this feature wants material that
    # actually drifts. Loaded on its own, then put back.
    studio.call("open_files", [[str(drifting_clip(scratch / "drift.wav"))]])
    page.tool("inspect")
    page.wait("!document.getElementById('show-loudness').disabled", what="the loudness curve")
    page.click("document.getElementById('show-loudness')")
    page.settle(2.0)
    page.save("analysis_loudness", element=STAGE)
    page.save("sidebar_levels", element=SECTION_OF.format("levels"))
    page.click("document.getElementById('show-loudness')")
    open_demo(page, studio, demo)

    print("a take in progress")
    # the live view is drawn from what the monitor is fed, whatever feeds it;
    # the demo clip a tenth of a second at a time stands in for a microphone
    y, sr = studio.session.y, studio.session.sr
    with studio.lock:
        studio.monitor.start(sr)
        for i in range(0, len(y), sr // 10):
            studio.monitor.push(y[i:i + sr // 10], sr)
    page.settle(2.0)
    page.save("analysis_live", element=STAGE)
    with studio.lock:
        studio.monitor.stop()
    open_demo(page, studio, demo)

    print("comparison")
    page.size(WINDOW_SIZE[0], PANEL_HEIGHT)
    page.tool("compare")
    page.click(page.button("compare", "Run comparison"))
    page.settle(0.5)
    if not studio.jobs.wait(300):
        raise SystemExit("the Compare panel never finished")
    page.wait("document.querySelector('[data-panel-host=\"compare\"] tbody tr')",
              what="the comparison's rows")
    page.click("document.querySelector('[data-panel-host=\"compare\"] tbody tr')")
    page.settle(0.8)
    page.save_panel("panel_compare", "compare")
    page.size(*WINDOW_SIZE)

    print("A/B pair")
    page.tool("enhance")
    page.click(page.button("enhance", "Denoise"))
    page.settle(0.5)
    if not studio.jobs.wait(120):
        raise SystemExit("the Enhance panel never finished")
    page.wait("!document.getElementById('compare').disabled", what="the A/B switch")
    with studio.lock:
        studio.session.set_selection(0.8, 1.6)
    studio.call("seek", [1.1])
    page.settle(1.5)
    page.save("ab_edited", element=STAGE)
    page.click("document.getElementById('compare')")
    page.settle(1.5)
    page.save("ab_original", element=STAGE)
    page.click("document.getElementById('compare')")

    print("reading a corpus back")
    studio.call("open_manifest", [str(small_corpus(scratch / "corpus"))])
    if not studio.jobs.wait(60):
        raise SystemExit("the manifest was never read")
    page.size(WINDOW_SIZE[0], PANEL_HEIGHT)
    page.tool("manifest")
    page.wait("document.querySelector('[data-panel-host=\"manifest\"] tr[data-p]')",
              what="the manifest's rows")
    page.click("document.querySelector('[data-panel-host=\"manifest\"] tr[data-p=\"4\"]')")
    page.settle(0.8)
    page.save_panel("panel_manifest", "manifest")
    page.size(*WINDOW_SIZE)


if __name__ == "__main__":
    sys.exit(main())
