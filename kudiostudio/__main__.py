# -*- coding: utf-8 -*-
"""Entry point: ``python -m kudiostudio`` / ``kudiostudio``."""
from __future__ import annotations

import sys

from kudiostudio.web.app import main

if __name__ == "__main__":
    sys.exit(main())
