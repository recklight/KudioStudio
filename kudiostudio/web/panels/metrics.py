# -*- coding: utf-8 -*-
"""The Metrics panel: ``metrics.*`` on the wire.

One table, filled by whichever of the three was asked last: the
reference-free inspection, DNSMOS, or the scores against a clean reference.
The rows and their wording come from `kudiostudio.tools.metrics`.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

import kudio
from kudiostudio.tools import metrics
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


class MetricsPanel(WebPanel):
    id = "metrics"
    title = "Metrics"
    icon = "metrics"
    script = "js/panels/metrics.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._rows: List[Tuple[str, str]] = []
        #: (text, warn) under each of the reference-free buttons
        self._inspect: Optional[Tuple[str, bool]] = None
        self._dnsmos: Optional[Tuple[str, bool]] = None
        self._installed: Dict[str, bool] = {}

    def info(self) -> Dict:
        return {"busy": self.busy, "rows": [list(r) for r in self._rows],
                "inspect": list(self._inspect) if self._inspect else None,
                "dnsmos": list(self._dnsmos) if self._dnsmos else None}

    @command
    def describe(self) -> Dict:
        self._installed = metrics.installed()
        return {**super().describe(), **self.info(),
                "references": list(metrics.REFERENCES),
                "pesq_scales": [label for label, _ in metrics.PESQ_SCALES],
                "pesq_tip": metrics.PESQ_TIP, "align_tip": metrics.ALIGN_TIP,
                "dnsmos_tip": metrics.DNSMOS_TIP,
                "availability": metrics.availability(self._installed)}

    def _fill(self, rows) -> None:
        self._rows = list(rows)
        self.publish(info=self.info())

    # ---------------------------------------------------------- no reference

    @command
    def inspect(self) -> Dict:
        if not self.require_audio():
            return self.info()
        try:
            report = kudio.audio_report(np.asarray(self.session.y, dtype=np.float32),
                                        self.session.sr)
        except Exception as e:  # noqa: BLE001
            self.report(f"Inspection failed — {type(e).__name__}: {e}")
            return self.info()
        rows, problems = metrics.inspect_rows(report)
        self._inspect = (("· " + "\n· ".join(problems), True) if problems
                         else (metrics.NOTHING_WRONG, False))
        self._fill(rows)
        self.report("Inspection complete.")
        return self.info()

    @command
    def dnsmos(self) -> Dict:
        """What a listener would say about this clip, with no reference.
        `audio_report` answers "is it technically sound"; this is the other
        half, and the only way to it without a listening panel."""
        if not self.require_audio():
            return self.info()
        y = np.asarray(self.session.y, dtype=np.float32)
        self.report("Running DNSMOS …")
        if not self.start_job("Running DNSMOS …", kudio.dnsmos, y, self.session.sr,
                              on_done=self._dnsmos_done, on_failed=self._dnsmos_failed):
            self.report("Another job is already running.")
        return self.info()

    def _dnsmos_done(self, score) -> None:
        self._dnsmos = (score.summary(), False)
        self._fill(metrics.dnsmos_rows(score))
        self.report(f"DNSMOS — {score}")

    def _dnsmos_failed(self, message: str) -> None:
        # the useful failure here is "you do not have the model", and kudio's
        # message says where to get it -- so it is shown in full
        self._dnsmos = (message, True)
        self.publish(info=self.info())
        self.report("DNSMOS unavailable — see the panel for what is missing.")

    # -------------------------------------------------------- with reference

    def _reference_audio(self, reference: str, path: str):
        if reference == metrics.REFERENCES[1]:
            if not path:
                raise ValueError("no reference file selected")
            return kudio.file_load(Path(path), sr=self.session.sr)
        original = self.session.original()
        if original is None:
            raise ValueError("no original audio in this session")
        return original

    @command
    def evaluate(self, reference: str, path: str = "", align: bool = True,
                 pesq_label: str = "MOS-LQO") -> Dict:
        if not self.require_audio():
            return self.info()
        scale = metrics.pesq_scale(pesq_label)
        try:
            ref, ref_sr = self._reference_audio(reference, path)
        except Exception as e:  # noqa: BLE001
            self.report(f"Reference unavailable — {e}")
            return self.info()
        sr = self.session.sr
        if ref_sr != sr:
            self.report(f"Sample-rate mismatch: reference {ref_sr} Hz vs current {sr} Hz.")
            return self.info()
        deg = np.asarray(self.session.y, dtype=np.float32)
        ref = np.asarray(ref, dtype=np.float32)
        if min(len(ref), len(deg)) == 0:
            self.report("Nothing to compare.")
            return self.info()

        rows, ref, deg, said = metrics.line_up(ref, deg, sr, bool(align))
        for line in said:
            self.report(line)
        n = min(len(ref), len(deg))
        ref, deg = ref[:n], deg[:n]
        rows += metrics.reference_rows(ref, deg, sr)
        self._fill(rows)
        self._installed = self._installed or metrics.installed()
        self.report("Computing PESQ / STOI / SDR …")

        def done(result) -> None:
            self._fill(rows + metrics.optional_rows(result, pesq_label,
                                                    self._installed, sr))
            self.report("Evaluation complete.")

        if not self.start_job("Computing PESQ / STOI / SDR …",
                              partial(kudio.eval_metrics, pesq_scale=scale), sr, ref, deg,
                              on_done=done,
                              on_failed=lambda m: self.report(f"Evaluation failed — {m}")):
            self.report("An evaluation is already running.")
        return self.info()


__all__ = ["MetricsPanel"]
