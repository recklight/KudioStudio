# -*- coding: utf-8 -*-
"""Colours shared by the views and the panels.

``palette(mid)`` collapses into the background under QDarkStyle, so secondary
text uses fixed mid-tones that stay legible on light and dark alike.
"""

#: monospace "here is the kudio call you just made" captions
CALL_COLOR = "#7fb3d5"
#: explanatory one-liners under a control group
HINT_COLOR = "#9aa5b1"
#: waveform trace
WAVE_COLOR = "#4aa3df"
#: selection region and playback cursor
ACCENT_COLOR = "#e8a33d"

__all__ = ["CALL_COLOR", "HINT_COLOR", "WAVE_COLOR", "ACCENT_COLOR"]
