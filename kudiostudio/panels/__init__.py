# -*- coding: utf-8 -*-
"""Tool panels — one per `kudio` area, all sharing the same AudioSession."""
from kudiostudio.panels.base import Panel
from kudiostudio.panels.batch import BatchPanel
from kudiostudio.panels.browse import FileList
from kudiostudio.panels.compare import ComparePanel
from kudiostudio.panels.effects import EffectsPanel
from kudiostudio.panels.enhance import EnhancePanel
from kudiostudio.panels.metrics import MetricsPanel
from kudiostudio.panels.pitch import PitchPanel
from kudiostudio.panels.record import RecordPanel
from kudiostudio.panels.synth import SynthPanel

#: Order of the tabs in the tool dock. Compare sits next to Enhance because
#: the two are the same question asked twice: "which one" and "this one".
#: Pitch sits with Metrics because both measure rather than change.
PANEL_CLASSES = (EffectsPanel, EnhancePanel, ComparePanel, RecordPanel,
                 SynthPanel, PitchPanel, MetricsPanel, BatchPanel)

__all__ = [
    "Panel",
    "FileList",
    "EffectsPanel",
    "EnhancePanel",
    "ComparePanel",
    "RecordPanel",
    "SynthPanel",
    "PitchPanel",
    "MetricsPanel",
    "BatchPanel",
    "PANEL_CLASSES",
]
