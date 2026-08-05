# -*- coding: utf-8 -*-
"""Reusable views: waveform, spectrogram/MFCC and the transport bar."""
from kudiostudio.widgets.transport import Player, TransportBar
from kudiostudio.widgets.views import AnalysisView, SpectrogramView, WaveformView

__all__ = [
    "WaveformView",
    "SpectrogramView",
    "AnalysisView",
    "TransportBar",
    "Player",
]
