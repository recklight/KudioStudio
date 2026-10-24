# -*- coding: utf-8 -*-
"""The window's tool panels, in rail order.

Inspect comes first and is drawn by the page itself from the window's own
commands. The rest follow `ORDER`: Compare beside Enhance, Manifest after
Synthesize, Pitch with Metrics.
"""
from __future__ import annotations

from typing import List, Tuple

from kudiostudio.web.panels.base import WebPanel
from kudiostudio.web.panels.batch import BatchPanel
from kudiostudio.web.panels.compare import ComparePanel
from kudiostudio.web.panels.effects import EffectsPanel
from kudiostudio.web.panels.enhance import EnhancePanel
from kudiostudio.web.panels.manifest import ManifestPanel
from kudiostudio.web.panels.metrics import MetricsPanel
from kudiostudio.web.panels.pitch import PitchPanel
from kudiostudio.web.panels.record import RecordPanel
from kudiostudio.web.panels.synth import SynthPanel

#: panels that have their Python half here, in rail order
PANEL_CLASSES: Tuple[type, ...] = (EffectsPanel, EnhancePanel, ComparePanel, RecordPanel,
                                   SynthPanel, ManifestPanel, PitchPanel, MetricsPanel,
                                   BatchPanel)

#: the rail's order, by id
ORDER = ("inspect", "effects", "enhance", "compare", "record", "synthesize",
         "manifest", "pitch", "metrics", "batch")


def module_url(panel: WebPanel) -> str:
    """Where the page imports *panel*'s module from: KudioStudio's static
    folder, or the folder a plugin's package serves at ``/plugins/<id>/``."""
    if panel.static_dir is None:
        return f"/static/{panel.script}"
    return f"/plugins/{panel.id}/{panel.script}"


def rail(panels: List[WebPanel]) -> List[dict]:
    """The rail's entries: Inspect, then every tool in `ORDER`, then any
    other panel (a plugin's) after a rule."""
    have = {p.id: p for p in panels}
    entries = [{"id": "inspect", "title": "Inspect", "icon": "inspect", "script": "",
                "url": ""}]
    for key in ORDER[1:]:
        if key in have:
            p = have[key]
            entries.append({"id": p.id, "title": p.title, "icon": p.icon,
                            "script": p.script, "url": module_url(p)})
    for p in panels:
        if p.id not in ORDER:
            entries.append({"id": p.id, "title": p.title, "icon": p.icon,
                            "script": p.script, "url": module_url(p), "extra": True})
    return entries


__all__ = ["WebPanel", "BatchPanel", "ComparePanel", "EffectsPanel", "EnhancePanel",
           "ManifestPanel", "MetricsPanel", "PitchPanel", "RecordPanel", "SynthPanel",
           "PANEL_CLASSES", "ORDER", "module_url", "rail"]
