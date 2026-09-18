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
#: something is wrong with the audio itself -- clipping, silence, a bad rate
WARN_COLOR = "#e06c5a"
#: a measurement that came back clean
OK_COLOR = "#7fbf7f"
#: markers -- deliberately not the selection colour, since both can be on
#: screen at once and "which of these is the thing I am editing" has to be
#: answerable at a glance
MARKER_COLOR = "#9b8ec4"
#: the f0 contour drawn over the spectrogram. Viridis runs blue-green-yellow,
#: so a curve has to be outside that ramp to read as an overlay rather than as
#: part of the image -- pink is the one hue the colour map never reaches.
PITCH_COLOR = "#ff5fa2"
#: the short-term loudness curve drawn over the waveform. Warm against the
#: waveform's blue, and distinct from the selection's amber, because both can
#: be on screen at once.
LOUDNESS_COLOR = "#f0d264"

__all__ = ["CALL_COLOR", "HINT_COLOR", "WAVE_COLOR", "ACCENT_COLOR",
           "WARN_COLOR", "OK_COLOR", "MARKER_COLOR", "PITCH_COLOR",
           "LOUDNESS_COLOR"]
