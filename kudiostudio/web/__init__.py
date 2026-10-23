# -*- coding: utf-8 -*-
"""KudioStudio drawn with HTML, in a pywebview window.

The page in `static/` is plain HTML, CSS and JavaScript with no build step;
it talks to `Studio` through a server on 127.0.0.1 (`server`), and the audio
is analysed and played here, in Python, never in the page.
"""
