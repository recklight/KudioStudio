# -*- coding: utf-8 -*-
"""The Survey panel: ``survey.*`` on the wire.

`kudiostudio.tools.survey` over the Files list or a folder, as a job that
says how far it has got and stops between two files when asked. The table
comes back a filter at a time; a click on a row opens that file, and the
rows under a filter can become the Files list, to go through one by one.
The folder spectra and the timeline are drawn by the page from what
``spectra`` and ``timeline`` send.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Dict, List, Optional

from kudiostudio.tools import survey
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


def _plain(value: float, digits: int = 2) -> Optional[float]:
    """*value* for the page: JSON has no nan or inf."""
    return round(float(value), digits) if math.isfinite(value) else None


#: the voice detectors as the panel names them
DETECTOR_NAMES = {"kudio": "kudio.speech_ratio", "silero": "Silero VAD"}


class SurveyPanel(WebPanel):
    id = "survey"
    title = "Survey"
    icon = "survey"
    script = "js/panels/survey.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._rows: List[survey.SurveyRow] = []
        self._filter = "all"
        #: the column the table is sorted by and whether largest first;
        #: column -1 is the order the files were read in
        self._sort = (-1, False)
        self._stop = threading.Event()
        self._summary = ""

    def info(self) -> Dict:
        from kudiostudio.web.studio import shown_names      # it imports the panels
        shown = self._shown()
        names = shown_names([r.path for r in shown]) if shown else []
        return {"busy": self.busy, "filter": self._filter, "summary": self._summary,
                "total": len(self._rows), "sort": list(self._sort),
                "rows": [survey.cells(r, n) for r, n in zip(shown, names)],
                "paths": [r.path for r in shown],
                "flags": [bool(r.problems or r.error or r.seam) for r in shown],
                "counts": {f: sum(survey.matches(r, f) for r in self._rows if not r.error)
                           for f in survey.FILTERS}}

    def _shown(self) -> List[survey.SurveyRow]:
        if self._filter == "all":
            rows = list(self._rows)
        else:
            rows = [r for r in self._rows if not r.error and survey.matches(r, self._filter)]
        column, descending = self._sort
        return survey.sort_rows(rows, column, descending) if column >= 0 else rows

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info(), "columns": list(survey.COLUMNS),
                "filters": ["all", *survey.FILTERS], "clip_runs": list(survey.CLIP_RUNS),
                "files_in_list": len(self.studio.files),
                "metrics": [m for m, _ in survey.TIMELINE_METRICS],
                "time_pattern": survey.TIME_PATTERN,
                "detectors": [[d, DETECTOR_NAMES[d]] for d in survey.detectors()]}

    # ----------------------------------------------------------------- run

    @command
    def run(self, source: str = "", clip_run: int = 3, lines: bool = True,
            voice: bool = False, seams: bool = False, detector: str = "kudio") -> Dict:
        """Survey *source*, a folder or a pattern, or the Files list when it
        is empty. *detector* is one of `survey.detectors`."""
        if detector not in survey.detectors():
            raise ValueError(f"no voice detector {detector!r} here")
        if source:
            from kudiostudio.web.studio import expand_paths
            files, _ = expand_paths([source])
        else:
            files = list(self.studio.files)
        if not files:
            self.report("Nothing to survey: open some files, or pick a folder.")
            return self.info()
        if int(clip_run) not in survey.CLIP_RUNS:
            raise ValueError(f"clip_run is one of {survey.CLIP_RUNS}")
        self._stop.clear()
        owner = self.id

        def progress(i, n, name):
            if i % 5 == 0:
                self.studio.jobs.progress(owner, f"{i} of {n} — {name}")

        if not self.start_job(f"Surveying {len(files)} files …", survey.run_survey, files,
                              clip_run=int(clip_run), lines=bool(lines), voice=bool(voice),
                              detector=detector, seams=bool(seams), progress=progress,
                              stop=self._stop.is_set,
                              on_done=self._done,
                              on_failed=lambda m: self.report(f"Survey failed — {m}")):
            self.report("Another job is already running.")
        return self.info()

    def _done(self, rows) -> None:
        self._rows = list(rows)
        self._filter = "all"
        self._sort = (-1, False)
        failed = sum(bool(r.error) for r in self._rows)
        flagged = sum(survey.matches(r, "any finding") for r in self._rows if not r.error)
        self._summary = (f"{len(self._rows)} files, {flagged} with a finding"
                         + (f", {failed} would not load" if failed else "")
                         + (" — stopped before the end" if self._stop.is_set() else ""))
        self.publish(info=self.info())
        self.report(f"Survey complete: {self._summary}.")

    @command
    def stop(self) -> Dict:
        """Stop after the file being read now."""
        self._stop.set()
        return self.info()

    # --------------------------------------------------------------- table

    @command
    def show(self, which: str = "all") -> Dict:
        if which != "all" and which not in survey.FILTERS:
            raise ValueError(f"no filter {which!r}")
        self._filter = which
        return self.info()

    @command
    def sort(self, column: int) -> Dict:
        """Sort the table by *column*: the names A to Z and the numbers
        largest first, the other way round when it is already sorted so.
        Put in the Files list, the files keep this order."""
        column = int(column)
        if not 0 <= column < len(survey.COLUMNS):
            raise ValueError(f"no column {column}")
        current, descending = self._sort
        self._sort = (column, not descending if current == column else column > 0)
        return self.info()

    # ------------------------------------------------- across the files

    @command
    def spectra(self, statistic: str = "mean", reference: str = "") -> Dict:
        """Each folder's averaged spectrum, or each one less the folder
        *reference* names (``folder|rate``, as ``keys`` lists them)."""
        from kudiostudio.web.studio import shown_names
        found = survey.folder_spectra(self._rows, statistic)
        if not found:
            return {"series": [], "keys": [], "names": [],
                    "note": "No take long enough for a spectrum: run the survey first."}
        keys = [f"{f.folder}|{f.sr}" for f in found]
        names = shown_names([f.folder for f in found])
        rates = {f.sr for f in found}
        if len(rates) > 1:
            names = [f"{n} @ {f.sr} Hz" for n, f in zip(names, found)]
        labels = [f"{n} ({f.files})" for n, f in zip(names, found)]
        base = None
        if reference:
            if reference not in keys:
                raise ValueError(f"no folder {reference!r} in the survey")
            base = found[keys.index(reference)]
        series, left_out = [], 0
        for key, label, f in zip(keys, labels, found):
            if base is not None:
                if key == reference:
                    continue
                if f.sr != base.sr:
                    left_out += 1
                    continue
                db = f.db - base.db
            else:
                db = f.db
            series.append({"name": label, "key": key,
                           "x": [round(float(v), 2) for v in f.freqs],
                           "y": [_plain(v) for v in db]})
        note = ""
        if left_out:
            note = f"{left_out} folder(s) at another rate than the reference left out."
        return {"series": series, "keys": keys, "names": labels, "note": note,
                "unit": "dB" if base is not None else "dB re full scale per Hz",
                "reference": labels[keys.index(reference)] if base is not None else ""}

    @command
    def timeline(self, metric: str = "RMS", clock: str = "name", pattern: str = "",
                 low: float = 0.0, high: float = 0.0) -> Dict:
        """The takes on one clock, *metric* for each, and the gaps."""
        from kudiostudio.web.studio import shown_names
        units = dict(survey.TIMELINE_METRICS)
        if metric not in units:
            raise ValueError(f"no metric {metric!r}")
        line = survey.timeline(self._rows, metric, clock=clock, pattern=pattern,
                               band=(float(low), float(high)))
        names = shown_names([s["folder"] for s in line.series]) if line.series else []
        series = [{"name": f"{n} ({len(s['x'])})", "x": s["x"],
                   "y": [_plain(v) for v in s["y"]], "paths": s["paths"],
                   "seconds": s["seconds"], "flags": s["flags"]}
                  for n, s in zip(names, line.series)]
        timed = sum(len(s["x"]) for s in series)
        if line.gaps:
            longest = max(b - a for a, b in line.gaps)
            gaps = f"{len(line.gaps)} gap(s), the longest {longest:.0f} s"
        else:
            gaps = "no gap"
        if line.untimed:
            summary = f"{timed} takes on the clock, {gaps}; {line.untimed} without a time."
        else:
            summary = f"{timed} takes on the clock, {gaps}."
        return {"series": series, "gaps": [list(g) for g in line.gaps], "unit": units[metric],
                "summary": summary}

    @command
    def open_path(self, path: str) -> Dict:
        """Open a take the timeline points at."""
        if path not in {r.path for r in self._rows}:
            raise ValueError("not a file of the survey")
        return self._open(path)

    def _open(self, path: str) -> Dict:
        if path not in self.studio.files:
            self.studio.files.append(path)
            self.studio.files_version += 1
        self.studio.load(path)
        return self.studio.state()

    @command
    def open_row(self, index: int) -> Dict:
        shown = self._shown()
        if not 0 <= int(index) < len(shown):
            raise ValueError("no such row")
        return self._open(shown[int(index)].path)

    @command
    def to_files(self) -> Dict:
        """The rows on show become the Files list, the first of them open."""
        paths = [r.path for r in self._shown() if not r.error]
        if not paths:
            self.report("No file to put in the list.")
            return self.studio.state()
        self.studio.files[:] = paths
        self.studio.files_version += 1
        self.report(f"{len(paths)} files in the Files list.")
        self.studio.load(paths[0])
        return self.studio.state()

    @command
    def export(self, path: str) -> Dict:
        """Every row to *path*, and a line per folder beside it as
        ``<name>_folders.csv``."""
        if not self._rows:
            self.report("Nothing to export: run the survey first.")
            return {"written": False}
        target = Path(path)
        if target.suffix.lower() != ".csv":
            target = target.with_suffix(".csv")
        survey.write_csv(self._rows, str(target))
        folders = target.with_name(f"{target.stem}_folders.csv")
        survey.write_summary(self._rows, str(folders))
        self.studio.remember_save_dir(str(target))
        self.report(f"Saved {target.name} and {folders.name}.")
        return {"written": True, "path": str(target), "folders": str(folders)}

    def shutdown(self) -> None:
        self._stop.set()
