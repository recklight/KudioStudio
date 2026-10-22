# -*- coding: utf-8 -*-
"""Colours shared by the views and the panels.

Secondary text uses fixed mid-tones rather than ``palette(mid)``, so it stays
legible on the dark theme and under ``--no-theme`` alike. The window's own
surfaces are further down; `kudiostudio.style` builds the stylesheet from them.
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

#: Qt has no generic "monospace" family on Windows: ``font-family: monospace``
#: resolves to Tahoma there, which is proportional. These are tried in order,
#: so each platform finds its own fixed-pitch font.
MONO_CSS = ("font-family: 'Cascadia Mono', Consolas, Menlo, "
            "'DejaVu Sans Mono', monospace;")

# The window itself. Neutral greys with a faint cool cast: the plots carry
# all the colour on screen (viridis, the blue waveform, the amber selection),
# so the chrome around them stays out of the way.

#: behind everything: the window, dock title bars, the toolbar
BASE_COLOR = "#15171b"
#: cards, lists and tables
SURFACE_COLOR = "#1c1f24"
#: inputs and buttons
RAISED_COLOR = "#262a31"
#: under the mouse
HOVER_COLOR = "#2f343c"
#: hairlines between areas
BORDER_COLOR = "#2b3037"
#: edges of the things you can click or type into
EDGE_COLOR = "#3a404a"
#: the plots' background, a step darker than the window so they read as the
#: content and everything else as the frame around it
CANVAS_COLOR = "#101215"
#: ordinary text
TEXT_COLOR = "#e3e6ea"
#: text of a control that cannot be used right now
DISABLED_COLOR = "#5f6670"
#: focus, the current tool, the Play button. Blue, as the waveform is; amber
#: is already the selection's and red already means trouble.
PRIMARY_COLOR = "#3d8bd9"
#: a selected row or the current tool, behind text that stays TEXT_COLOR
PRIMARY_SOFT = "rgba(61, 139, 217, 0.22)"
#: axis ticks and labels on the plots
AXIS_COLOR = "#8a939e"

__all__ = ["CALL_COLOR", "HINT_COLOR", "WAVE_COLOR", "ACCENT_COLOR",
           "WARN_COLOR", "OK_COLOR", "MARKER_COLOR", "PITCH_COLOR",
           "LOUDNESS_COLOR", "MONO_CSS", "BASE_COLOR", "SURFACE_COLOR",
           "RAISED_COLOR", "HOVER_COLOR", "BORDER_COLOR", "EDGE_COLOR",
           "CANVAS_COLOR", "TEXT_COLOR", "DISABLED_COLOR", "PRIMARY_COLOR",
           "PRIMARY_SOFT", "AXIS_COLOR"]
