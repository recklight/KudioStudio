# -*- coding: utf-8 -*-
"""KudioStudio — a desktop workbench for the `kudio` audio toolkit.

Every panel in the UI is a thin, interactive shell around a documented
``kudio`` function, so anything you do here has a one-line scripting
equivalent.

    python -m kudiostudio        # or: kudiostudio
"""
import os

# pyqtgraph and matplotlib both auto-detect a Qt binding; pin them to PySide6
# so they can never pull in a second one at import time.
os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

__version__ = "0.2.0"
__author__ = "RL"

__all__ = ["__version__", "__author__"]
