# -*- coding: utf-8 -*-
"""Start the window: the local server, then a pywebview window on it.

    kudiostudio                    # the window
    kudiostudio clip.wav           # with a file open
    kudiostudio --browser          # the same page in your browser

``--browser`` serves the page and opens it in the default browser instead of
a window of its own. Everything works there except the native file dialogs,
which become a box to type a path into; it is also what the browser's
developer tools are for. A system with no web view for pywebview to draw
with -- a Linux desktop without WebKitGTK, say -- gets the browser too,
rather than no window at all.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import List, Optional, Sequence

import kudio
from kudiostudio import __version__, i18n, plugins, theme
from kudiostudio.desktop import set_windows_app_id
from kudiostudio.web.server import LocalServer
from kudiostudio.web.studio import Prefs, Studio

log = logging.getLogger(__name__)

ICON = Path(__file__).resolve().parent.parent / "assets" / "logo.ico"
WINDOW_SIZE = (1480, 900)
#: below this the tool panel and the plots start to crowd each other
MIN_SIZE = (1120, 680)


def _dialog_kind(name: str):
    """pywebview's constant for a dialog, from 5's names or 6's enum."""
    import webview
    enum = getattr(webview, "FileDialog", None)
    if enum is not None:
        return getattr(enum, name)
    return getattr(webview, f"{name}_DIALOG")


def _first(chosen) -> Optional[str]:
    """A dialog's one answer, whether it came as a string or a tuple."""
    if not chosen:
        return None
    if isinstance(chosen, (list, tuple)):
        return str(chosen[0]) if chosen else None
    return str(chosen)


class WindowDialogs:
    """The window's native file dialogs, for `Studio.choose_path`."""

    def __init__(self, window) -> None:
        self.window = window

    def open_files(self, directory: str, file_types: Sequence[str],
                   multiple: bool = True) -> List[str]:
        chosen = self.window.create_file_dialog(
            _dialog_kind("OPEN"), directory=directory or "",
            allow_multiple=multiple, file_types=tuple(file_types))
        return [str(p) for p in chosen or []]

    def save_file(self, directory: str, filename: str,
                  file_types: Sequence[str]) -> Optional[str]:
        return _first(self.window.create_file_dialog(
            _dialog_kind("SAVE"), directory=directory or "",
            save_filename=filename, file_types=tuple(file_types)))

    def folder(self, directory: str) -> Optional[str]:
        return _first(self.window.create_file_dialog(
            _dialog_kind("FOLDER"), directory=directory or ""))


def _waiting_for(studio: Studio) -> List[str]:
    """The titles of the panels whose jobs are running, in the window's
    language: ``[]``, ``[one]``, or ``[all but the last joined, the last]``.

    Each title is translated on its own: the table holds the sentence
    around the names, and a list of them joined here is nothing it could
    hold. Chinese lists its names with 、, not a comma.
    """
    names = []
    for owner, _ in studio.jobs.running():
        panel = studio.panel(owner)
        names.append(i18n.translate(panel.title if panel is not None else owner,
                                    studio.language))
    if len(names) < 2:
        return names
    comma = "、" if studio.language == "zh_TW" else ", "
    return [comma.join(names[:-1]), names[-1]]


def close_when_idle(studio: Studio, window) -> bool:
    """For the window's ``closing`` event: False keeps it open.

    A kudio call cannot be stopped half-way, and a process that exits under
    a running Batch leaves its output tree half-written. So closing waits:
    the window stays, says what it is waiting for, and closes itself when
    the last job ends.
    """
    names = _waiting_for(studio)
    if not names:
        return True
    if len(names) == 1:
        studio.say(f"Quitting when {names[0]} finishes — a running job cannot be "
                   f"stopped half-way.")
    else:
        studio.say(f"Quitting when {names[0]} and {names[1]} finish — a "
                   f"running job cannot be stopped half-way.")

    def later() -> None:
        studio.jobs.wait()
        window.destroy()

    threading.Thread(target=later, name="close-when-idle", daemon=True).start()
    return False


def dress_native_window(window) -> None:
    """The Windows title bar dark and the window's icon the logo.

    pywebview leaves both to the packaged executable, and a Python process
    has the interpreter's. Best effort: anything here failing costs looks,
    not function.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        import clr  # noqa: F401 - pythonnet, which pywebview runs on here
        from System import Action
        from System.Drawing import Icon

        form = window.native
        hwnd = form.Handle.ToInt64()
        dark = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 on Windows 11 and late Windows 10
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(dark), ctypes.sizeof(dark))
        if ICON.is_file():
            icon = Icon(str(ICON))
            form.Invoke(Action(lambda: setattr(form, "Icon", icon)))
    except Exception as e:  # noqa: BLE001
        log.debug("window dressing skipped: %s", e)


def serve_in_browser(server: LocalServer, studio: Studio) -> None:
    """The page in the default browser, until Ctrl+C -- which waits for a
    running job, as closing the window does (`stop_when_idle`)."""
    # flushed: written to a file or a pipe (an IDE's console, Git Bash), the
    # lines sit in the buffer for as long as the server runs, and the address
    # with its token is the only way back in once the browser tab is closed
    print(f"KudioStudio {__version__} on {server.url}", flush=True)
    print("Ctrl+C to stop.", flush=True)
    webbrowser.open(server.url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_when_idle(studio)


def stop_when_idle(studio: Studio) -> None:
    """After the first Ctrl+C in a browser: return when no job runs.

    For the reason `close_when_idle` gives. A second Ctrl+C is the way out
    when waiting is worse than a job left unfinished; it is not caught here,
    so the process stops at once.
    """
    names = _waiting_for(studio)
    if not names:
        return
    if len(names) == 1:
        studio.say(f"Stopping when {names[0]} finishes — a running job cannot be "
                   f"stopped half-way. Ctrl+C again stops now, with the job unfinished.")
    else:
        studio.say(f"Stopping when {names[0]} and {names[1]} finish — a "
                   f"running job cannot be stopped half-way. Ctrl+C again stops now, "
                   f"with the jobs unfinished.")
    # in short sleeps rather than one wait on the jobs: on Windows a Ctrl+C
    # interrupts a sleep at once, and a wait on a lock only when it times out
    while studio.jobs.running():
        time.sleep(0.2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kudiostudio",
        description="Desktop workbench for the kudio audio toolkit.")
    parser.add_argument("files", nargs="*",
                        help="audio files to open on start-up; a corpus "
                             "manifest (.json) opens in the Manifest panel")
    parser.add_argument("--lang", metavar="CODE",
                        help="interface language: en or zh_TW (default: the "
                             "saved choice, else the system's)")
    parser.add_argument("--no-plugins", action="store_true",
                        help="leave out the panels other packages add")
    parser.add_argument("--reset", action="store_true",
                        help="forget saved settings: the language, the analysis "
                             "view, the folders and file last used, and everything "
                             "in Settings")
    parser.add_argument("--browser", action="store_true",
                        help="open the page in the default browser instead of "
                             "a window of its own")
    parser.add_argument("--port", type=int, default=0,
                        help="the local port (default: any free one)")
    parser.add_argument("--debug", action="store_true",
                        help="the window's developer tools")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="debug logging from KudioStudio and kudio")
    parser.add_argument("--version", action="version",
                        version=f"KudioStudio {__version__} (kudio {kudio.__version__})")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")
    if args.verbose:
        # KudioStudio's own and kudio's loggers only, as `kudio -v` does: on
        # the root logger one start-up logged 597 lines, most of them numba's
        # compiler, and buried the traceback the flag is there for
        for name in ("kudiostudio", "kudio"):
            logging.getLogger(name).setLevel(logging.DEBUG)

    if args.no_plugins:
        os.environ[plugins.DISABLE_ENV] = "1"
    prefs = Prefs()
    if args.reset:
        prefs.clear()

    set_windows_app_id()
    studio = Studio(prefs=prefs, language=args.lang)
    server = LocalServer(studio, args.port)
    server.start()
    if args.files:
        studio.call("open_files", [list(args.files)])
    else:
        with studio.lock:
            studio.reopen_last()
    try:
        if args.browser:
            serve_in_browser(server, studio)
            return 0
        try:
            import webview
            window = webview.create_window(
                f"KudioStudio {__version__}", server.url,
                width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=MIN_SIZE,
                background_color=theme.BASE_COLOR, text_select=False)
            studio.dialogs = WindowDialogs(window)
            studio.close_window = window.destroy
            window.events.shown += lambda: dress_native_window(window)
            window.events.closing += lambda: close_when_idle(studio, window)
            webview.start(debug=args.debug, private_mode=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 - no web view: the browser still works
            log.warning("no window to draw in (%s: %s); opening the browser instead",
                        type(e).__name__, e)
            studio.dialogs = studio.close_window = None
            serve_in_browser(server, studio)
    except KeyboardInterrupt:
        pass
    finally:
        studio.shutdown()
        server.close()
    return 0


__all__ = ["main", "WindowDialogs", "close_when_idle", "dress_native_window",
           "serve_in_browser", "stop_when_idle"]
