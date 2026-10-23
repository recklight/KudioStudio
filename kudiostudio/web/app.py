# -*- coding: utf-8 -*-
"""Start the window: the local server, then a pywebview window on it.

    python -m kudiostudio.web                 # the window
    python -m kudiostudio.web clip.wav        # with a file open
    python -m kudiostudio.web --browser       # the same page in your browser

``--browser`` serves the page and opens it in the default browser instead of
a window of its own. Everything works there except the native open dialog,
which becomes a box to type a path into; it is what the browser's developer
tools are for.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from pathlib import Path
from typing import List, Optional, Sequence

from kudiostudio import __version__, theme
from kudiostudio.desktop import set_windows_app_id
from kudiostudio.web.server import LocalServer
from kudiostudio.web.studio import Studio

log = logging.getLogger(__name__)

ICON = Path(__file__).resolve().parent.parent / "assets" / "logo.ico"
WINDOW_SIZE = (1480, 900)
#: below this the tool panel and the plots start to crowd each other
MIN_SIZE = (1120, 680)


class WindowDialogs:
    """The window's native open dialog, for `Studio.open_dialog`."""

    def __init__(self, window) -> None:
        self.window = window

    def open_files(self, directory: str, file_types: Sequence[str]) -> List[str]:
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) \
            or webview.OPEN_DIALOG
        chosen = self.window.create_file_dialog(
            kind, directory=directory or "", allow_multiple=True,
            file_types=tuple(file_types))
        return list(chosen or [])


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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kudiostudio.web",
        description="KudioStudio in its own window, drawn with HTML.")
    parser.add_argument("files", nargs="*", help="audio files to open on start-up")
    parser.add_argument("--browser", action="store_true",
                        help="open the page in the default browser instead")
    parser.add_argument("--port", type=int, default=0,
                        help="the local port (default: any free one)")
    parser.add_argument("--lang", metavar="CODE",
                        help="interface language: en or zh_TW")
    parser.add_argument("--debug", action="store_true",
                        help="the window's developer tools")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    set_windows_app_id()
    studio = Studio(language=args.lang)
    server = LocalServer(studio, args.port)
    server.start()
    if args.files:
        studio.call("open_files", [list(args.files)])
    try:
        if args.browser:
            print(f"KudioStudio {__version__} on {server.url}")
            print("Ctrl+C to stop.")
            webbrowser.open(server.url)
            while True:
                time.sleep(3600)
        import webview
        window = webview.create_window(
            f"KudioStudio {__version__}", server.url,
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=MIN_SIZE,
            background_color=theme.BASE_COLOR, text_select=False)
        studio.dialogs = WindowDialogs(window)
        window.events.shown += lambda: dress_native_window(window)
        webview.start(debug=args.debug, private_mode=True)
    except KeyboardInterrupt:
        pass
    finally:
        studio.shutdown()
        server.close()
    return 0


__all__ = ["main", "WindowDialogs", "dress_native_window"]
