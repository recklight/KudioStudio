# -*- coding: utf-8 -*-
"""The local server the window's page talks to.

Bound to 127.0.0.1 on a port the system picks, and closed with the window:
nothing outside this machine can reach it, and nothing on it outlives the
run. Inside the machine any program, and any web page in a browser, can open
a connection to a local port, so two more checks stand between those and the
session: every request that reads or changes anything carries a token made
for this run, and the Host header has to name 127.0.0.1 or localhost -- the
second is what stops a page that has pointed its own domain at 127.0.0.1
from talking to the server under that domain's name.

Routes:

``GET /``                 the page, with this run's token in it
``GET /static/...``       its scripts and styles
``GET /theme.css``        the colours, from `kudiostudio.theme`
``GET /colormap.bin``     the spectrogram's 256 colours, RGB bytes
``GET /icons.json``       the line icons, from `kudiostudio.shapes`
``GET /assets/logo.png``  the application's mark
``POST /api/<command>``   run a `Studio` command, JSON in and out
``GET /data/peaks``       the waveform for a stretch of time, binary
``GET /data/image``       the spectrogram for a stretch of time, binary
``GET /events``           a stream of what changed (server-sent events)
"""
from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import queue
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np

from kudiostudio import theme
from kudiostudio.shapes import SHAPES
from kudiostudio.web.studio import COMMANDS, Studio

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
LOGO = Path(__file__).resolve().parent.parent / "assets" / "logo.png"

#: a page that has stopped reading still holds a thread; this is how often
#: the stream checks whether it is still there
KEEPALIVE_SECONDS = 15.0


def theme_css() -> str:
    """Every colour in `kudiostudio.theme` as a CSS custom property:
    BASE_COLOR becomes ``--base``, PRIMARY_SOFT ``--primary-soft``."""
    lines = [":root {"]
    for name in sorted(dir(theme)):
        value = getattr(theme, name)
        if not name.isupper() or not isinstance(value, str):
            continue
        if not (value.startswith("#") or value.startswith("rgba(")):
            continue
        css = name[:-len("_COLOR")] if name.endswith("_COLOR") else name
        lines.append(f"  --{css.lower().replace('_', '-')}: {value};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def colormap_bytes(name: str = theme.SPECTRUM_MAP) -> bytes:
    """256 RGB triples of matplotlib's *name* colour map."""
    from matplotlib import colormaps
    rgba = colormaps[name](np.linspace(0.0, 1.0, 256))
    return (np.round(rgba[:, :3] * 255).astype(np.uint8)).tobytes()


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, studio: Studio, port: int = 0):
        self.studio = studio
        self.token = secrets.token_urlsafe(24)
        self.closing = threading.Event()
        self._colormap: Optional[bytes] = None
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def colormap(self) -> bytes:
        if self._colormap is None:
            self._colormap = colormap_bytes()
        return self._colormap

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="kudiostudio-server",
                                  daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self.closing.set()
        self.shutdown()
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # quiet: one line per request
        log.debug("%s " + fmt, self.address_string(), *args)

    # ------------------------------------------------------------ checks

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _token_ok(self, query: Dict[str, list]) -> bool:
        given = self.headers.get("X-Kudio-Token") or (query.get("token") or [""])[0]
        return hmac.compare_digest(given.encode(), self.server.token.encode())

    # ------------------------------------------------------------ replies

    def _send(self, status: int, body: bytes, content_type: str,
              headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _refuse(self, status: int, why: str) -> None:
        # a refused POST still has its body waiting unread; on a kept-alive
        # connection that body would be taken for the next request line
        self.close_connection = True
        body = json.dumps({"ok": False, "error": why}).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Connection": "close"})

    # ------------------------------------------------------------ routing

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = unquote(parts.path)
        if not self._host_ok():
            return self._refuse(HTTPStatus.FORBIDDEN, "wrong host")
        if path in ("/", "/index.html"):
            return self._page(query)
        if path == "/theme.css":
            return self._send(HTTPStatus.OK, theme_css().encode(), "text/css; charset=utf-8")
        if path == "/icons.json":
            return self._json(HTTPStatus.OK, SHAPES)
        if path == "/colormap.bin":
            return self._send(HTTPStatus.OK, self.server.colormap(), "application/octet-stream")
        if path == "/assets/logo.png":
            return self._file(LOGO)
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if not self._token_ok(query):
            return self._refuse(HTTPStatus.FORBIDDEN, "token")
        if path == "/data/peaks":
            return self._peaks(query)
        if path == "/data/image":
            return self._image(query)
        if path == "/events":
            return self._events()
        return self._refuse(HTTPStatus.NOT_FOUND, "no such page")

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if not self._host_ok():
            return self._refuse(HTTPStatus.FORBIDDEN, "wrong host")
        if not self._token_ok(parse_qs(parts.query)):
            return self._refuse(HTTPStatus.FORBIDDEN, "token")
        if not parts.path.startswith("/api/"):
            return self._refuse(HTTPStatus.NOT_FOUND, "no such command")
        name = parts.path[len("/api/"):]
        if name not in COMMANDS:
            return self._refuse(HTTPStatus.NOT_FOUND, f"no command {name!r}")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._refuse(HTTPStatus.BAD_REQUEST, "the body is not JSON")
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        try:
            result = self.server.studio.call(name, args, kwargs)
        except (TypeError, ValueError) as e:
            return self._refuse(HTTPStatus.BAD_REQUEST, f"{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - the page shows it; the server lives
            log.exception("command %s failed", name)
            return self._json(HTTPStatus.OK, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return self._json(HTTPStatus.OK, {"ok": True, "result": result})

    # ------------------------------------------------------------ static

    def _page(self, query) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        # the token is only written into the page for a request that already
        # had it, so loading "/" from another origin learns nothing
        token = self.server.token if self._token_ok(query) else ""
        html = html.replace("%%TOKEN%%", token)
        html = html.replace("%%LANG%%", self.server.studio.language.replace("_", "-"))
        self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, relative: str) -> None:
        target = (STATIC / relative).resolve()
        if STATIC not in target.parents or not target.is_file():
            return self._refuse(HTTPStatus.NOT_FOUND, "no such file")
        self._file(target)

    def _file(self, path: Path) -> None:
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            kind = "text/javascript"    # Windows' registry has said otherwise
        if kind.startswith("text/"):
            kind += "; charset=utf-8"
        self._send(HTTPStatus.OK, path.read_bytes(), kind)

    # ------------------------------------------------------------ data

    @staticmethod
    def _number(query, name: str, default: float) -> float:
        try:
            return float(query.get(name, [default])[0])
        except (TypeError, ValueError):
            return default

    def _peaks(self, query) -> None:
        t0 = self._number(query, "t0", 0.0)
        t1 = self._number(query, "t1", 0.0)
        columns = int(np.clip(self._number(query, "columns", 800), 1, 16384))
        result, version = self.server.studio.peaks(t0, t1, columns)
        if result is None:
            return self._send(HTTPStatus.OK, b"", "application/octet-stream",
                              {"X-Kudio-Version": str(version), "X-Kudio-Kind": "empty"})
        self._send(HTTPStatus.OK, result.values.astype("<f4").tobytes(),
                   "application/octet-stream", {
                       "X-Kudio-Version": str(version),
                       "X-Kudio-Kind": result.kind,
                       "X-Kudio-Start": repr(result.start),
                       "X-Kudio-Step": repr(result.step),
                   })

    def _image(self, query) -> None:
        t0 = self._number(query, "t0", 0.0)
        t1 = self._number(query, "t1", 0.0)
        columns = int(np.clip(self._number(query, "columns", 800), 1, 8192))
        rows = int(np.clip(self._number(query, "rows", 400), 1, 4096))
        try:
            img, version = self.server.studio.image()
        except Exception as e:  # noqa: BLE001 - a view kudio cannot make
            log.warning("analysis failed: %s", e)
            return self._refuse(HTTPStatus.UNPROCESSABLE_ENTITY, f"{type(e).__name__}: {e}")
        if img is None:
            return self._send(HTTPStatus.OK, b"", "application/octet-stream",
                              {"X-Kudio-Version": str(version), "X-Kudio-Kind": "empty"})
        from kudiostudio.web.analysis import image_slice
        part = image_slice(img, t0, t1, columns, rows)
        headers = {
            "X-Kudio-Version": str(version),
            "X-Kudio-Kind": "image",
            "X-Kudio-Rows": str(part.pixels.shape[0]),
            "X-Kudio-Columns": str(part.pixels.shape[1] if part.pixels.ndim == 2 else 0),
            "X-Kudio-T0": repr(part.t0),
            "X-Kudio-T1": repr(part.t1),
            "X-Kudio-Ymax": repr(img.y_max),
            "X-Kudio-Label": img.y_label,
            "X-Kudio-Units": img.y_units or "",
        }
        self._send(HTTPStatus.OK, part.pixels.tobytes(), "application/octet-stream", headers)

    # ------------------------------------------------------------ events

    def _events(self) -> None:
        studio = self.server.studio
        q = studio.events.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            with studio.lock:
                first = {"type": "state", "state": studio.state()}
            self._event(first)
            while not self.server.closing.is_set():
                try:
                    event = q.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": still here\n\n")
                    self.wfile.flush()
                    continue
                self._event(event)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            studio.events.unsubscribe(q)
            self.close_connection = True

    def _event(self, event) -> None:
        data = json.dumps(event, default=_json_default, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


__all__ = ["LocalServer", "Handler", "theme_css", "colormap_bytes", "STATIC"]
