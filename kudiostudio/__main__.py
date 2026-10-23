# -*- coding: utf-8 -*-
"""Entry point: ``python -m kudiostudio`` / ``kudiostudio``."""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(prog="kudiostudio",
                                     description="Desktop workbench for the kudio audio toolkit.")
    parser.add_argument("files", nargs="*",
                        help="audio files to open on start-up; a corpus "
                             "manifest (.json) opens in the Manifest panel")
    parser.add_argument("--reset", action="store_true",
                        help="discard saved window geometry and settings")
    parser.add_argument("--no-theme", action="store_true",
                        help="use the native Qt style instead of the dark theme")
    parser.add_argument("--no-plugins", action="store_true",
                        help="leave out the panels other packages add")
    parser.add_argument("--lang", metavar="CODE",
                        help="interface language: en or zh_TW (default: the "
                             "saved choice, else the system's)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.no_plugins:
        # an environment variable rather than an argument to the window, so
        # that the restart a language change asks for keeps it
        import os
        from kudiostudio import plugins
        os.environ[plugins.DISABLE_ENV] = "1"

    from PySide6.QtWidgets import QApplication

    from kudiostudio import __version__, icons
    from kudiostudio.app import KudioStudio
    from kudiostudio.desktop import set_windows_app_id

    set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("KudioStudio")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Kudio")
    # every window and dialog, the About box included, takes it from here
    app.setWindowIcon(icons.app_icon())

    from kudiostudio import i18n
    i18n.install(app, args.lang)

    if not args.no_theme:
        _apply_dark_theme(app)

    window = KudioStudio(reset=args.reset)
    window.show()
    if args.files:
        window.open_files(args.files)
    return app.exec()


def _apply_dark_theme(app) -> None:
    from kudiostudio import style
    style.apply(app)


if __name__ == "__main__":
    sys.exit(main())
