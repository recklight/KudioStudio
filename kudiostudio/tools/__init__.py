# -*- coding: utf-8 -*-
"""What the tool panels compute, without any window.

Each module here is one panel's working part: its parameters, described
once, and the kudio call they turn into. The panels in
`kudiostudio.web.panels` take their settings from the page and their calls
from here.
"""
from kudiostudio.tools.params import Param

__all__ = ["Param"]
