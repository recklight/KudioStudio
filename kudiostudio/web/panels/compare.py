# -*- coding: utf-8 -*-
"""The Compare panel: ``compare.*`` on the wire.

Rows are named by their (method, noise estimator) pair: once the grid varies
both, one method has a row per estimator. The page sorts and selects; this
side keeps the results, auditions and applies them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

import kudio
from kudiostudio.tools import compare as tools
from kudiostudio.tools.enhance import LearnedModel, kudioenhance_available, model_state
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


class ComparePanel(WebPanel):
    id = "compare"
    title = "Compare"
    icon = "compare"
    script = "js/panels/compare.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._results: List = []
        #: what the rows on screen were computed from, and the running job
        self._shown: Optional[tools.RunContext] = None
        self._verdict = ("", "hint")
        #: kept for the next comparison on the same run directory
        self.model = LearnedModel()

    def info(self) -> Dict:
        rows = []
        for index, result in enumerate(self._results):
            text = tools.cells(result)
            rows.append({
                "key": [result.method, result.noise], "cells": text,
                # by value, not by text: "9.66" must beat "10.2". The two label
                # columns sort in kudio's own order, cheapest first
                "sort": [index, index] + [tools.as_number(t) for t in text[2:]],
                "error": bool(result.error),
            })
        return {"busy": self.busy, "rows": rows,
                "has_reference": tools.has_reference(self._results),
                "verdict": {"text": self._verdict[0], "tone": self._verdict[1]}}

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info(),
                "references": list(tools.REFERENCES), "sweeps": list(tools.SWEEPS),
                "methods": [{"name": n, "label": s.label, "tip": s.summary}
                            for n, s in kudio.METHODS.items()],
                "noises": [{"name": n, "tip": s} for n, s in kudio.NOISE_ESTIMATORS.items()],
                "default_method": tools.DEFAULT_METHOD,
                "columns": list(tools.COLUMNS),
                "reference_columns": list(tools.REFERENCE_COLUMNS),
                "tips": {"sweep": tools.SWEEP_TIP, "run_dir": tools.RUN_DIR_TIP,
                         "selection": tools.SELECTION_TIP},
                "metrics_text": tools.METRICS_TEXT, "note": tools.RUN_NOTE}

    def _arguments(self, sweep, method, noise, run_dir):
        if run_dir != self.model.run_dir:
            self.model.forget()             # another run is another model
        learned = tools.learned_entry(run_dir, self.model, kudioenhance_available)
        return tools.sweep_arguments(sweep, method, noise, learned)

    @command
    def call_text(self, sweep: str, method: str, noise: str, reference: str,
                  metrics: bool = False, run_dir: str = "") -> str:
        try:
            arguments = self._arguments(sweep, method, noise, run_dir)
        except RuntimeError as e:           # kudioenhance not installed
            return str(e)
        return tools.call_text(arguments, reference, bool(metrics))

    # ------------------------------------------------------------------ run

    @command
    def run(self, reference: str, path: str = "", sweep: str = tools.SWEEP_METHODS,
            method: str = tools.DEFAULT_METHOD, noise: str = "", run_dir: str = "",
            metrics: bool = False, selection_only: bool = False) -> Dict:
        if not self.require_audio():
            return self.info()
        self.studio.stop_preview()
        try:
            ref = tools.reference_audio(reference, path, self.session)
        except RuntimeError as e:
            self.report(str(e))
            return self.info()
        y = np.asarray(self.session.y, dtype=np.float32)
        sel = self.session.selection() if selection_only else None
        sl = self.session.selected_slice() if sel is not None else None
        if sl is not None:
            y = y[sl]
            if ref is not None:
                ref = np.asarray(ref)[sl]
        if len(y) < 512:
            self.report("Too short to compare — select at least 512 samples.")
            return self.info()
        try:
            arguments = self._arguments(sweep, method, noise or next(iter(kudio.NOISE_ESTIMATORS)),
                                        run_dir)
        except RuntimeError as e:
            self.report(str(e))
            return self.info()
        methods = arguments.pop("methods")
        entries = methods or list(kudio.METHODS)
        learned_dir = run_dir if any(not isinstance(m, str) for m in entries) else None
        context = tools.RunContext(
            y=self.session.y, ran_on=(sl.start, sl.stop) if sl is not None else None,
            learned_dir=learned_dir, model=model_state(learned_dir) if learned_dir else None)

        def finished(results) -> None:
            if self.session.y is not context.y:
                # computed from audio the session no longer holds: ranked, it
                # would describe something else, and applied, it would put the
                # old audio back over the edit
                self.report("The audio changed while the comparison ran, so its results "
                            "describe audio that is no longer there — run it again.")
                return
            self._shown = context
            self._results = list(results)
            text, tone, said = tools.verdict(self._results)
            self._verdict = (text, tone)
            self.report(said)
            self.publish(info=self.info())

        self.report(f"Running {tools.count_runs(methods, arguments)} combination(s) …")
        if not self.start_job("Comparing…", tools.compare_with_model, y, self.session.sr,
                              methods, model=self.model, learned_dir=learned_dir,
                              reference=ref, metrics=bool(metrics), **arguments,
                              on_done=finished,
                              on_failed=lambda m: self.report(f"Comparison failed — {m}")):
            self.report("A comparison is already running.")
        return self.info()

    # -------------------------------------------------------------- results

    def _rows(self, keys: Sequence) -> List:
        """The results named by *keys*, in that order, that have audio."""
        by_key = {(r.method, r.noise): r for r in self._results}
        found = [by_key.get(tuple(k)) for k in keys]
        return [r for r in found if r is not None and r.error is None and r.audio.size]

    @command
    def audition(self, keys: List) -> Dict:
        """Play the rows named by *keys*, in that order -- the table's, not
        the order they were clicked in -- back to back, on a loop."""
        rows = self._rows(keys)
        if not rows:
            self.studio.stop_preview()
            return {"on": False}
        audio, said = tools.audition(rows, self.session.sr)
        self.report(said)
        return {"on": self.studio.start_preview(self.id, audio, self.session.sr)}

    @command
    def apply(self, key: List, selection_only: bool = False) -> Dict:
        rows = self._rows([key])
        if not rows:
            return self.info()
        result = rows[0]
        self.studio.stop_preview()
        audio = np.asarray(result.audio, dtype=np.float32)
        into_selection = bool(selection_only) and self.session.selection() is not None
        target = None
        if into_selection:
            sl = self.session.selected_slice()
            target = (sl.start, sl.stop)
        # The code recomputes the result from the audio it replaces. Put
        # somewhere other than where it was computed -- the selection moved
        # after the run -- it came from audio the step's code never sees.
        shown = self._shown
        code = (tools.script(result, shown)
                if shown is not None and self.session.y is shown.y
                and target == shown.ran_on else None)
        if into_selection:
            self.session.replace_selection(audio, undo_label=result.label, code=code)
        else:
            self.session.set_audio(audio, self.session.sr, undo_label=result.label,
                                   keep_original=True, code=code)
        self.report(f"Applied {result.label}. A/B it against the original with Ctrl+B.")
        return self.info()

    @command
    def save_all(self, folder: str) -> Dict:
        """Every result as a file, to compare outside this window."""
        usable = [r for r in self._results if r.error is None]
        if not usable:
            return self.info()
        stem = Path(self.session.name or "audio").stem
        written = 0
        try:
            for result in usable:
                kudio.save_wave(Path(folder) / tools.file_name(stem, result), result.audio,
                                self.session.sr)
                written += 1
        except Exception as e:  # noqa: BLE001
            self.report(f"Saved {written} of {len(usable)} — {type(e).__name__}: {e}")
            return self.info()
        self.report(f"Wrote {written} file(s) to {folder}")
        return self.info()

    def on_session_changed(self) -> None:
        """New audio means the stored results describe something else."""
        if self._results or self._verdict[0]:
            self._results = []
            self._shown = None
            self._verdict = ("", "hint")
            self.publish(info=self.info())


__all__ = ["ComparePanel"]
