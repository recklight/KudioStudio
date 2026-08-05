# -*- coding: utf-8 -*-
"""Entry point: ``python -m kudiostudio`` / ``kudiostudio``."""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(prog="kudiostudio",
                                     description="Desktop workbench for the kudio audio toolkit.")
    parser.add_argument("files", nargs="*", help="audio files to open on start-up")
    parser.add_argument("--reset", action="store_true",
                        help="discard saved window geometry and settings")
    parser.add_argument("--no-theme", action="store_true",
                        help="use the native Qt style instead of the dark theme")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from PySide6.QtWidgets import QApplication

    from kudiostudio import __version__
    from kudiostudio.app import KudioStudio

    app = QApplication(sys.argv[:1])
    app.setApplicationName("KudioStudio")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Kudio")

    if not args.no_theme:
        _apply_dark_theme(app)

    window = KudioStudio(reset=args.reset)
    window.show()
    for f in args.files:
        window.open_files([f])
    return app.exec()


def _apply_dark_theme(app) -> None:
    try:
        import qdarkstyle
    except ImportError:
        logging.getLogger(__name__).debug("QDarkStyle not installed; using native style")
        return
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))


if __name__ == "__main__":
    sys.exit(main())
