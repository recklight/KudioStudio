# -*- coding: utf-8 -*-
"""The Synthesize panel: ``synthesize.*`` on the wire.

The run never touches the open audio: it reads three folders and writes a
fourth, on a job, with every setting read into a `SynthPlan` before it
starts. The checks, the call and the wording of the result come from
`kudiostudio.tools.synth`.
"""
from __future__ import annotations

from functools import partial
from typing import Dict, Optional

from kudiostudio import i18n
from kudiostudio.tools import synth as tools
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


class SynthPanel(WebPanel):
    id = "synthesize"
    title = "Synthesize"
    icon = "synthesize"
    script = "js/panels/synth.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._result: Optional[tools.SynthResult] = None

    def _result_line(self) -> str:
        """The last run's line, translated a piece at a time: the notes
        after the count are separate sentences in the table."""
        if self._result is None:
            return "—"
        say = partial(i18n.translate, language=self.studio.language)
        notes = "".join(say(n) for n in tools.result_notes(self._result))
        return say(f"{self._result.on_disk} file(s) written to {self._result.out}{notes}")

    def info(self) -> Dict:
        return {"busy": self.busy, "result": self._result_line()}

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info(),
                "subtypes": list(tools.SUBTYPES), "modes": list(tools.MODES),
                "links": list(tools.LINKS),
                "tips": {"snr": tools.SNR_TIP, "rt60": tools.RT60_TIP,
                         "drr": tools.DRR_TIP, "subtype": tools.SUBTYPE_TIP,
                         "channel": tools.CHANNEL_TIP, "targets": tools.TARGETS_TIP,
                         "mode": tools.MODE_TIP},
                "seed_note": tools.SEED_NOTE}

    @command
    def call_text(self, clean: str, noise: str, out: str, snr: str, rt60: str,
                  drr_db: float, subtype: str, channel: str, write_targets: bool,
                  mode: str, seed: Optional[int]) -> str:
        return tools.call_text(clean, noise, out, snr, rt60, float(drr_db), subtype,
                               channel, bool(write_targets), mode, _seed(seed))

    @command
    def run(self, clean: str, noise: str, out: str, snr: str, rt60: str = "",
            drr_db: float = 0.0, subtype: str = "PCM_16", channel: str = "none",
            write_targets: bool = False, mode: str = "regular",
            seed: Optional[int] = None, add_clean: bool = False,
            overwrite: bool = True) -> Dict:
        clean, noise, out = (str(p or "").strip() for p in (clean, noise, out))
        why = tools.problem(clean, noise, out, snr, rt60, bool(write_targets))
        if why is not None:
            self.report(why)
            return self.info()
        if subtype not in tools.SUBTYPES or mode not in tools.MODES \
                or channel not in tools.LINKS:
            raise ValueError(f"unknown choice: {subtype!r}, {mode!r}, {channel!r}")
        rooms = tools.parse_rt60(rt60)
        plan = tools.SynthPlan(clean=clean, noise=noise, out=out,
                               snr=tuple(tools.parse_snr(snr)),
                               rooms=tuple(rooms) if rooms else None,
                               drr_db=float(drr_db), channel=tools.link(channel),
                               write_targets=bool(write_targets), subtype=subtype,
                               mode=mode, add_clean=bool(add_clean),
                               overwrite=bool(overwrite), seed=_seed(seed))
        self.report("Synthesizing …")
        if not self.start_job("Synthesizing…", tools.synthesize, plan,
                              on_done=self._finished, on_failed=self._failed):
            self.report("A synthesis job is already running.")
        return self.info()

    def _finished(self, result: tools.SynthResult) -> None:
        self._result = result
        self.publish(info=self.info())
        self.report(tools.result_text(result)[1])

    def _failed(self, message: str) -> None:
        self._result = None
        self.publish(info=self.info())
        self.report(f"Synthesis failed — {message}")


def _seed(value) -> Optional[int]:
    """The page's seed box: below zero is None. The box goes down to -1
    and shows that value as "None"."""
    if value is None or value == "":
        return None
    value = int(value)
    return None if value < 0 else value


__all__ = ["SynthPanel"]
