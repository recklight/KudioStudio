# -*- coding: utf-8 -*-
"""Claude: what the clip's measurements say, in words, with next steps.

The panel lists the figures before anything is sent, and the figures are
all that is sent: kudio's levels of the clip as it is now, and the Metrics
panel's table when it was made on the same audio. The request runs as a job,
since an answer takes a few seconds. The key, its test and the model are in
the Settings dialog, which calls the commands here.
"""
from __future__ import annotations

import logging
import webbrowser
from typing import Dict, List, Optional, Tuple

import numpy as np

from kudiostudio.measure import measure
from kudiostudio.tools import claude
from kudiostudio.web import analysis
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel

log = logging.getLogger(__name__)

#: how each figure is listed in the panel, in the order sent
SHOWN = (
    ("duration_s", "Duration", "{:.2f} s"),
    ("sample_rate_hz", "Sample rate", "{} Hz"),
    ("channels", "Channels", "{}"),
    ("edited_since_opened", "Edited since opened", None),
    ("peak_dbfs", "Peak", "{:+.1f} dBFS"),
    ("true_peak_dbtp", "True peak", "{:+.1f} dBTP"),
    ("rms_dbfs", "RMS", "{:+.1f} dBFS"),
    ("loudness_lufs", "Loudness", "{:+.1f} LUFS"),
    ("loudness_range_lu", "Loudness range", "{:.1f} LU"),
    ("noise_floor_dbfs", "Noise floor", "{:+.1f} dBFS"),
    ("estimated_snr_db", "Estimated SNR", "{:.1f} dB"),
    ("bandwidth_hz", "Bandwidth", "{:.0f} Hz"),
    ("clipped_samples", "Clipped samples", "{}"),
    ("dc_offset", "DC offset", "{:+.5f}"),
    ("silence_ratio", "Silence", "{:.0%}"),
    ("non_finite_samples", "Non-finite samples", "{}"),
)


def shown_rows(facts: Dict) -> List[List[str]]:
    """*facts* as [label, value] rows for the panel's table, in English."""
    levels = facts.get("measurements", {})
    rows = []
    for key, label, spec in SHOWN:
        value = levels.get(key)
        if value is None:
            rows.append([label, "—"])
        elif spec is None:
            rows.append([label, "yes" if value else "no"])
        else:
            rows.append([label, spec.format(value)])
    for problem in levels.get("kudio_problems", []):
        rows.append(["kudio found", problem])
    for row in facts.get("metrics_panel", []):
        rows.append([f"Metrics: {row['measure']}", row["value"]])
    return rows


class ClaudePanel(WebPanel):
    id = "claude"
    title = "Claude"
    icon = "claude"
    script = "js/panels/claude.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self.keys = claude.KeyStore()
        #: the last answer, and the audio version it is about
        self._answer: Optional[Dict] = None
        self._answer_version = -1
        #: answers so far, so the page can tell a new one from one it has
        self._answers = 0
        self._error = ""
        #: (version, facts): measured once per version of the audio
        self._facts: Optional[Tuple[int, Dict]] = None
        #: what the Models API last listed for the key, kept between starts
        #: so the Settings dialog has names before it is asked again
        saved = studio.prefs.get("claude_models")
        self._models: List[Dict] = saved if isinstance(saved, list) else []

    # ------------------------------------------------------------- state

    def status(self) -> Dict:
        source, hint = claude.key_source(self.keys)
        chosen = self.studio.options["claude_model"]
        model = claude.resolve(chosen)
        return {"missing": claude.missing(), "source": source, "hint": hint,
                "chosen": chosen, "model": model, "model_name": self._name(model),
                "recommended": claude.RECOMMENDED_MODEL,
                "recommended_name": self._name(claude.RECOMMENDED_MODEL),
                "models": self._models, "console": claude.CONSOLE_URL}

    def _name(self, model: str) -> str:
        return next((m["name"] for m in self._models if m["id"] == model),
                    claude.RECOMMENDED_NAME if model == claude.RECOMMENDED_MODEL else model)

    def info(self) -> Dict:
        return {"busy": self.busy, "status": self.status(), "answer": self._answer,
                "stale": self._answer is not None
                and self._answer_version != self.studio.version,
                "error": self._error}

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info()}

    # ------------------------------------------------------------- facts

    def _current_facts(self) -> Optional[Dict]:
        """The figures for the clip as it is now. Measured with the lock let
        go, as the Levels box is: a long file takes half a second."""
        if not self.session.has_audio:
            return None
        version = self.studio.version
        if self._facts is not None and self._facts[0] == version:
            return self._facts[1]
        y = np.asarray(self.session.y)
        sr, edited = self.session.sr, self.session.has_history()
        channels = 1 if y.ndim == 1 else y.shape[1]
        metrics = self.studio.panel("metrics")
        rows = metrics.rows_for(version) if metrics is not None else []
        self.studio.lock.release()
        try:
            measured = measure(analysis.mono(y), sr)
        finally:
            self.studio.lock.acquire()
        facts = claude.facts(measured.report, measured.true_peak_db, measured.range_lu,
                             channels, edited, rows)
        self._facts = (version, facts)
        return facts

    @command
    def facts(self) -> Dict:
        """What Ask Claude would send now, as the panel lists it."""
        facts = self._current_facts()
        if facts is None:
            return {"rows": [], "version": self.studio.version}
        return {"rows": shown_rows(facts), "version": self.studio.version}

    # --------------------------------------------------------------- ask

    @command
    def ask(self) -> Dict:
        why = claude.missing()
        if why:
            self.report(why)
            return self.info()
        if not self.require_audio():
            return self.info()
        if claude.key_source(self.keys)[0] == "none" and not self._models:
            # an ant profile still works without either, and a key that
            # has been tested once has listed models
            self.report("No API key yet. Add yours in Edit → Settings…")
            return self.info()
        facts = self._current_facts()
        version = self.studio.version
        chosen = self.studio.options["claude_model"]
        language = self.studio.language
        known = list(self._models)
        self._error = ""
        started = self.start_job("Asking Claude …", self._asking, facts, chosen, language,
                                 known, on_done=lambda result: self._answered(result, version))
        if not started:
            self.report("Another job is already running.")
        return self.info()

    def _asking(self, facts: Dict, chosen: str, language: str, known: List[Dict]):
        """Runs as the job: the answer, or the ClaudeError that says why not.
        A model that has been retired is asked of the recommended one."""
        client = claude.make_client(self.keys)
        try:
            return claude.ask(client, chosen, facts, language, known), False
        except claude.ClaudeError as e:
            if not e.retired or claude.resolve(chosen) == claude.RECOMMENDED_MODEL:
                return e, False
        try:
            return claude.ask(client, claude.RECOMMENDED_MODEL, facts, language, known), True
        except claude.ClaudeError as e:
            return e, True

    def _answered(self, result, version: int) -> None:
        outcome, retired = result
        if retired:
            gone = claude.resolve(self.studio.options["claude_model"])
            self.studio.options.put("claude_model", "auto")
            self.report(f"{gone} is not available any more; Settings is back on automatic.")
        if isinstance(outcome, claude.ClaudeError):
            self._error = str(outcome)
            self.report(self._error)
        else:
            self._answers += 1
            self._answer = {**outcome.to_dict(), "model_name": self._name(outcome.model),
                            "number": self._answers}
            self._answer_version = version
            self._error = ""
            cost = f", about ${outcome.cost:.3f}" if outcome.cost is not None else ""
            self.report(f"Claude answered: {outcome.input_tokens} tokens in, "
                        f"{outcome.output_tokens} out{cost}.")
        self.publish(info=self.info())

    @command
    def open_console(self) -> Dict:
        """Where a key is made, in the default browser."""
        webbrowser.open(claude.CONSOLE_URL)
        self.report(f"The Claude Console opens in your browser: {claude.CONSOLE_URL}")
        return {"url": claude.CONSOLE_URL}

    @command
    def show(self, panel: str) -> Dict:
        """Bring a panel a next step names to the front."""
        if panel in claude.PANELS:
            self.studio.events.publish("tool", id=panel)
        return {"shown": panel}

    # ----------------------------------------------------------- the key

    @command
    def save_key(self, key: str) -> Dict:
        key = str(key).strip()
        if not claude.looks_like_a_key(key):
            self.report("That does not look like an API key; one starts with sk-ant-.")
        elif not self.keys.set(key):
            self.report("The key could not be saved: this system has no credential store "
                        "KudioStudio can use. Set ANTHROPIC_API_KEY instead.")
        else:
            self.report("Key saved in the system's credential store.")
        self.publish(info=self.info())
        return self.info()

    @command
    def remove_key(self) -> Dict:
        self.keys.delete()
        self._models = []
        self.studio.prefs.set("claude_models", [])
        self.report("Key removed.")
        self.publish(info=self.info())
        return self.info()

    @command
    def test(self) -> Dict:
        """List the models the key can use: a check of the key that costs
        nothing, and the names the model choice offers. Answered in place,
        for the Settings dialog to show, with the lock let go meanwhile and
        15 s at most, so a dead connection does not hold the window."""
        why = claude.missing()
        if why:
            self.report(why)
            return self.info()
        self.studio.lock.release()
        try:
            result = self._listing()
        finally:
            self.studio.lock.acquire()
        self._listed(result)
        return self.info()

    def _listing(self):
        try:
            client = claude.make_client(self.keys).with_options(timeout=15.0, max_retries=0)
            return claude.list_models(client)
        except Exception as e:  # noqa: BLE001 - said in the status line
            return claude.said(e, "")

    def _listed(self, result) -> None:
        if isinstance(result, claude.ClaudeError):
            self.report(str(result))
        else:
            self._models = result
            self.studio.prefs.set("claude_models", result)
            self.report(f"The key works: {len(result)} models can answer here.")
        self.publish(info=self.info())


__all__ = ["ClaudePanel", "shown_rows"]
