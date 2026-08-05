# -*- coding: utf-8 -*-
"""Tool panels — one per `kudio` area, all sharing the same AudioSession."""
from kudiostudio.panels.base import Panel
from kudiostudio.panels.batch import BatchPanel
from kudiostudio.panels.browse import FileList
from kudiostudio.panels.effects import EffectsPanel
from kudiostudio.panels.enhance import EnhancePanel
from kudiostudio.panels.metrics import MetricsPanel
from kudiostudio.panels.record import RecordPanel
from kudiostudio.panels.synth import SynthPanel

#: Order of the tabs in the tool dock.
PANEL_CLASSES = (EffectsPanel, EnhancePanel, RecordPanel, SynthPanel,
                 MetricsPanel, BatchPanel)

__all__ = [
    "Panel",
    "FileList",
    "EffectsPanel",
    "EnhancePanel",
    "RecordPanel",
    "SynthPanel",
    "MetricsPanel",
    "BatchPanel",
    "PANEL_CLASSES",
]
