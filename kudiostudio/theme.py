# -*- coding: utf-8 -*-
"""Colours shared by the views and the panels.

Secondary text uses fixed mid-tones rather than ``palette(mid)``, so it stays
legible on the dark theme and under ``--no-theme`` alike. The window's own
surfaces are further down; `kudiostudio.style` builds the stylesheet from them.
"""

#: monospace "here is the kudio call you just made" captions, and the
#: measured values in the Levels box
CALL_COLOR = "#6fd3f5"
#: explanatory one-liners under a control group
HINT_COLOR = "#8195aa"
#: waveform trace
WAVE_COLOR = "#38b6ff"
#: selection region and playback cursor. Amber against a window that is
#: otherwise cyan and blue, so where the edit is never has to be looked for.
ACCENT_COLOR = "#ffb02e"
#: something is wrong with the audio itself -- clipping, silence, a bad rate
WARN_COLOR = "#ff5f73"
#: a measurement that came back clean
OK_COLOR = "#3ddc97"
#: markers -- deliberately not the selection colour, since both can be on
#: screen at once and "which of these is the thing I am editing" has to be
#: answerable at a glance
MARKER_COLOR = "#a993f5"
#: the time-frequency image. Inferno runs black-purple-orange-yellow and is
#: perceptually uniform, so a level reads the same anywhere in the image, and
#: its warm ramp stands apart from the cool window around it.
SPECTRUM_MAP = "inferno"
#: the f0 contour drawn over the spectrogram. A curve has to be outside the
#: colour map's ramp to read as an overlay rather than as part of the image,
#: and inferno never reaches cyan.
PITCH_COLOR = "#3ff0ff"
#: the short-term loudness curve drawn over the waveform. Warm against the
#: waveform's blue, and distinct from the selection's amber, because both can
#: be on screen at once.
LOUDNESS_COLOR = "#f0d264"

#: Qt has no generic "monospace" family on Windows: ``font-family: monospace``
#: resolves to Tahoma there, which is proportional. These are tried in order,
#: so each platform finds its own fixed-pitch font.
MONO_CSS = ("font-family: 'Cascadia Mono', Consolas, Menlo, "
            "'DejaVu Sans Mono', monospace;")

# The window itself: blue-black surfaces, hairlines a shade lighter, and one
# cyan for whatever has focus. The plots carry the rest of the colour on
# screen (the spectrogram, the blue waveform, the amber selection).

#: behind everything: the window, dock title bars, the toolbar
BASE_COLOR = "#0b0f15"
#: cards, lists and tables
SURFACE_COLOR = "#10161e"
#: inputs and buttons
RAISED_COLOR = "#16202b"
#: under the mouse
HOVER_COLOR = "#1d2a38"
#: hairlines between areas
BORDER_COLOR = "#1b2633"
#: edges of the things you can click or type into
EDGE_COLOR = "#2a3a4d"
#: the plots' background, a step darker than the window so they read as the
#: content and everything else as the frame around it
CANVAS_COLOR = "#06090d"
#: ordinary text
TEXT_COLOR = "#d6e2ee"
#: text of a control that cannot be used right now
DISABLED_COLOR = "#4a5a6e"
#: focus, the current tool, the Play button
PRIMARY_COLOR = "#1fc6f0"
#: a selected row or the current tool, behind text that stays TEXT_COLOR
PRIMARY_SOFT = "rgba(31, 198, 240, 0.15)"
#: text and marks drawn on a bright fill -- the Play button, a checked box,
#: an A/B button showing the original
INK_COLOR = "#04121a"
#: axis ticks and labels on the plots
AXIS_COLOR = "#7488a0"


def mix(a: str, b: str, t: float) -> str:
    """The colour *t* of the way from *a* to *b*, both ``#rrggbb``.

    For the in-between shades -- a highlight's top edge, a dimmed accent --
    so that changing a colour above changes them with it.
    """
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


#: the current tool's icon and label, the playhead readout: the accent,
#: lifted towards white so text in it stays easy to read
PRIMARY_TEXT = mix(PRIMARY_COLOR, "#ffffff", 0.45)

__all__ = ["CALL_COLOR", "HINT_COLOR", "WAVE_COLOR", "ACCENT_COLOR",
           "WARN_COLOR", "OK_COLOR", "MARKER_COLOR", "SPECTRUM_MAP",
           "PITCH_COLOR", "LOUDNESS_COLOR", "MONO_CSS", "BASE_COLOR",
           "SURFACE_COLOR", "RAISED_COLOR", "HOVER_COLOR", "BORDER_COLOR",
           "EDGE_COLOR", "CANVAS_COLOR", "TEXT_COLOR", "DISABLED_COLOR",
           "PRIMARY_COLOR", "PRIMARY_SOFT", "PRIMARY_TEXT", "INK_COLOR",
           "AXIS_COLOR", "mix"]
