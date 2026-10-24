# -*- coding: utf-8 -*-
"""The Batch panel: ``batch.*`` on the wire.

The page sends the whole form as one dict of settings, for the call it shows
and for a Run alike, and the plan is read from it here before the job starts.
The passes and the wording of the result are `kudiostudio.tools.batch`.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Dict, Optional, Tuple

import kudio
from kudiostudio import i18n
from kudiostudio.tools import batch as tools
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


def _text(settings: Dict[str, Any], key: str) -> str:
    return str(settings.get(key) or "").strip()


class BatchPanel(WebPanel):
    id = "batch"
    title = "Batch"
    icon = "batch"
    script = "js/panels/batch.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._outcome: Optional[tools.BatchOutcome] = None
        #: (output, input) of the run that produced `_outcome`, as it started
        self._folders: Tuple[str, str] = ("", "")

    # ------------------------------------------------------------- reading

    def _read(self, settings: Dict[str, Any], pin: bool = False) -> tools.BatchPlan:
        """The plan the form describes. With *pin*, a random room seed is
        drawn now, so the result can name it."""
        rate, subtype = str(settings.get("rate", "keep")), str(settings.get("subtype", "PCM_16"))
        normalize = str(settings.get("normalize", "none"))
        denoise = settings.get("denoise") or None
        noise = settings.get("noise") or None
        if rate not in tools.RATES or subtype not in tools.SUBTYPES \
                or normalize not in tools.NORMALIZE_MODES:
            raise ValueError(f"unknown choice: {rate!r}, {subtype!r}, {normalize!r}")
        if denoise is not None and denoise not in kudio.METHODS:
            raise ValueError(f"unknown denoise method {denoise!r}")
        if noise is not None and noise not in kudio.NOISE_ESTIMATORS:
            raise ValueError(f"unknown noise estimator {noise!r}")
        src, dst = _text(settings, "src"), _text(settings, "dst")
        convert = tools.convert_kwargs(
            rate, bool(settings.get("mono", True)), subtype, bool(settings.get("trim")),
            float(settings.get("top_db", 30.0)), normalize, float(settings.get("target", 0.0)))
        room = None
        if settings.get("room"):
            room = tools.room_plan(float(settings.get("rt60", 0.5)),
                                   float(settings.get("drr_db", 0.0)),
                                   int(settings.get("seed", 0)), _text(settings, "room_dst"),
                                   dst, pin=pin)
        return tools.BatchPlan(
            src=src, dst=dst, convert=convert, denoise=denoise,
            noise=noise if denoise is not None else None, room=room,
            tolerance=float(settings.get("tolerance", tools.LOUDNESS_TOLERANCE)),
            ceiling=float(settings.get("ceiling", tools.TRUE_PEAK_CEILING)))

    # ------------------------------------------------------------ the page

    def info(self) -> Dict:
        if self._outcome is None:
            return {"busy": self.busy, "result": "—", "misses": ""}
        dst, src = self._folders
        say = partial(i18n.translate, language=self.studio.language)
        lines = tools.result_lines(self._outcome, dst)
        return {"busy": self.busy, "result": "\n".join(say(line) for line in lines),
                "misses": tools.misses_text(self._outcome.loudness, dst, src)}

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info(),
                "rates": list(tools.RATES), "subtypes": list(tools.SUBTYPES),
                "normalize": {mode: tools.NORMALIZE_RANGES.get(mode)
                              for mode in tools.NORMALIZE_MODES},
                "denoise": [[name, kudio.METHODS[name].label] for name in kudio.METHODS],
                "noise": [[name, summary] for name, summary in kudio.NOISE_ESTIMATORS.items()],
                "tolerance": tools.LOUDNESS_TOLERANCE, "ceiling": tools.TRUE_PEAK_CEILING,
                "suffix": tools.REVERBERANT_SUFFIX,
                "tips": {"normalize": tools.NORMALIZE_TIP, "tolerance": tools.TOLERANCE_TIP,
                         "ceiling": tools.CEILING_TIP, "denoise": tools.DENOISE_TIP,
                         "room": tools.ROOM_TIP, "rt60": tools.RT60_TIP,
                         "drr": tools.DRR_TIP, "seed": tools.SEED_TIP},
                "hints": [tools.LAYOUT_HINT, tools.LOUDNESS_HINT]}

    @command
    def call_text(self, settings: Dict[str, Any]) -> str:
        plan = self._read(settings)
        return tools.call_text(plan.src, plan.dst, plan.convert, plan.denoise, plan.noise,
                               plan.room, plan.tolerance, plan.ceiling)

    # ----------------------------------------------------------------- run

    @command
    def run(self, settings: Dict[str, Any]) -> Dict:
        why = tools.problem(_text(settings, "src"), _text(settings, "dst"))
        if why is not None:
            self.report(why)
            return self.info()
        plan = self._read(settings, pin=True)
        if plan.room is not None and tools.trees_overlap(plan.room.folder, plan.dst):
            self.report(tools.OVERLAP)
            return self.info()
        self.report("Converting …")
        if not self.start_job("Running…", tools.run_batch, plan,
                              on_done=partial(self._finished, (plan.dst, plan.src)),
                              on_failed=self._failed):
            self.report("A batch job is already running.")
        return self.info()

    def _finished(self, folders: Tuple[str, str], outcome: tools.BatchOutcome) -> None:
        self._outcome, self._folders = outcome, folders
        self.publish(info=self.info())
        self.report(tools.status_text(outcome))

    def _failed(self, message: str) -> None:
        self._outcome = None
        self.publish(info=self.info())
        self.report(f"Batch failed — {message}")


__all__ = ["BatchPanel"]
