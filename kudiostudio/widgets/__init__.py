# -*- coding: utf-8 -*-
"""Reusable views: waveform, spectrogram, level readout and the transport bar."""
from kudiostudio.widgets.levels import LevelReadout
from kudiostudio.widgets.markers import MarkerList
from kudiostudio.widgets.transport import Player, TransportBar
from kudiostudio.widgets.views import (
    AnalysisView,
    SpectrogramView,
    TimePlot,
    WaveformView,
)

__all__ = [
    "TimePlot",
    "WaveformView",
    "SpectrogramView",
    "AnalysisView",
    "LevelReadout",
    "MarkerList",
    "TransportBar",
    "Player",
]
