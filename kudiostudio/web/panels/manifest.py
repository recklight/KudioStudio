# -*- coding: utf-8 -*-
"""The Manifest panel: ``manifest.*`` on the wire.

A corpus is thousands of rows, so the table lives here and the page asks for
the rows it is about to draw (`rows`). Filtering, sorting and the selection
are kept on this side too: the page holds no copy that can drift from them.
What a manifest says and how its files are found is
`kudiostudio.tools.manifest`.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import kudio
from kudiostudio import i18n
from kudiostudio.tools import manifest as tools
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel

#: the file dialog's choices for a manifest
MANIFEST_TYPES = ("Manifest (*.json)", "All files (*.*)")

#: rows sent with every view, enough to fill the table before it asks
FIRST_ROWS = 80


class ManifestPanel(WebPanel):
    id = "manifest"
    title = "Manifest"
    icon = "manifest"
    script = "js/panels/manifest.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        self._manifest: Optional[tools.LoadedManifest] = None
        self._text: List[Tuple[str, ...]] = []
        #: every value each filter's field takes, after "all"
        self._choices: Dict[str, List[object]] = {}
        #: the chosen entry of each filter, 0 for "all"
        self._picked: Dict[str, int] = {}
        self._missing_only = False
        #: (column, descending); column -1 is the manifest's own order
        self._sort: Tuple[int, bool] = (-1, False)
        #: row numbers in the manifest, in the order the table shows them
        self._shown: List[int] = []
        #: the selected row's number in the manifest, kept across a re-sort
        self._selected: Optional[int] = None
        self._listening: Optional[str] = None
        #: the row and side last started, so a filter change that keeps the
        #: selection where it was does not start the same file again
        self._heard: Optional[Tuple[int, str]] = None

    def _say(self, text: str) -> str:
        return i18n.translate(text, self.studio.language)

    # ------------------------------------------------------------- loading

    @command
    def load(self, path: str) -> Dict:
        """Read *path* on a job; the table fills in when it is done.

        The current directory is taken now, because it is what a relative
        path in the manifest is tried against first.
        """
        path = Path(str(path).strip())
        if not self.start_job("Reading…", tools.read_manifest, path, Path.cwd(),
                              on_done=self._loaded,
                              on_failed=partial(self._load_failed, path)):
            self.report("A manifest is still being read.")
            return self.view()
        self.report(f"Reading {path.name} and looking for its files …")
        return self.view()

    @command
    def reload(self) -> Dict:
        if self._manifest is None:
            return self.view()
        return self.load(str(self._manifest.path))

    def _loaded(self, manifest: tools.LoadedManifest) -> None:
        # worked out before anything is replaced, so a manifest that cannot
        # be shown leaves the last one as it was rather than half of each
        try:
            choices = {field: tools.distinct(manifest.rows, field)
                       for field, _ in tools.FILTERS}
            text = [tools.row_text(row, manifest.folders) for row in manifest.rows]
        except Exception as e:  # noqa: BLE001
            self._load_failed(manifest.path, f"{type(e).__name__}: {e}")
            return
        self._stop_listening()
        self._manifest, self._text, self._choices = manifest, text, choices
        self._picked = {field: 0 for field, _ in tools.FILTERS}
        self._missing_only = False
        self._selected = None
        self._refresh()
        self.publish(view=self.view(), reset=True)
        self.report(tools.loaded_text(manifest))

    def _load_failed(self, path: Path, message: str) -> None:
        self.report(f"Could not read {path.name} — {message}")

    # ------------------------------------------------------------ the view

    def _wanted(self) -> Dict[str, object]:
        """``{Pair field: value}`` for every filter that is not on "all"."""
        return {field: self._choices[field][index - 1]
                for field, index in self._picked.items() if index > 0}

    def _refresh(self) -> None:
        if self._manifest is None:
            self._shown = []
            return
        rows = self._manifest.rows
        wanted, missing_only = self._wanted(), self._missing_only
        self._shown = [i for i in tools.ordered(rows, self._text, *self._sort)
                       if tools.accepts(rows[i], wanted, missing_only)]

    def _position(self) -> int:
        """Where the selected row is in the table now; -1 if it is not."""
        if self._selected is None:
            return -1
        try:
            return self._shown.index(self._selected)
        except ValueError:
            return -1

    def _row(self) -> Optional[tools.ManifestRow]:
        if self._manifest is None or self._position() < 0:
            return None
        return self._manifest.rows[self._selected]

    def _axes(self) -> Tuple[List[object], List[object]]:
        """The grid's noises and SNRs: the whole corpus's, narrowed only by
        the noise and SNR filters. Filtering by room or link changes the
        counts and not the grid, so a combination one room lacks is a zero
        rather than a row that quietly disappears."""
        wanted = self._wanted()
        noises = [wanted["noise"]] if "noise" in wanted else self._choices["noise"]
        snrs = [wanted["snr_db"]] if "snr_db" in wanted else self._choices["snr_db"]
        return noises, snrs

    def _coverage(self) -> Optional[Dict]:
        """The rows shown, counted per noise × SNR."""
        if self._manifest is None:
            return None
        rows = [self._manifest.rows[i] for i in self._shown]
        cov = tools.coverage(rows, *self._axes())
        if self._missing_only:
            note, tone = tools.missing_note(len(rows)), ""
        else:
            note, good = tools.describe_coverage(cov, len(rows))
            tone = "ok" if good else "warn"
        return {"noises": [tools.format_value(n) for n in cov.noises],
                "snrs": [tools.filter_text("snr_db", s) for s in cov.snrs],
                "counts": [[cov.count(n, s) for s in cov.snrs] for n in cov.noises],
                "holes": not self._missing_only,
                "note": [self._say(note), tone]}

    def view(self) -> Dict:
        manifest = self._manifest
        if manifest is None:
            return {"busy": self.busy, "loaded": False,
                    "summary": [self._say(tools.OPEN_HINT), ""],
                    "call": "kudio.load_manifest(path)"}
        parts, tone = tools.summary_parts(manifest)
        folders = manifest.folders
        return {
            "busy": self.busy, "loaded": True, "path": str(manifest.path),
            "call": f"kudio.load_manifest({str(manifest.path)!r})",
            "summary": [" · ".join(self._say(p) for p in parts), tone],
            "filters": [{"field": field, "label": label,
                         "choices": ["all"] + [tools.filter_text(field, v)
                                               for v in self._choices[field]],
                         "index": self._picked[field],
                         "enabled": len(self._choices[field]) > 1}
                        for field, label in tools.FILTERS],
            "missing_only": self._missing_only,
            "shown": len(self._shown), "total": len(manifest.rows),
            "showing": self._say(f"showing {len(self._shown)} of {len(manifest.rows)}"),
            "sort": list(self._sort),
            "header_tips": [self._say(tools.header_tip(folders.get(field, "")))
                            for field in tools.FIELDS],
            "coverage": self._coverage(),
            "rows": self.rows(0, FIRST_ROWS),
            "selection": self.selection(),
        }

    @command
    def describe(self) -> Dict:
        return {**super().describe(), "view": self.view(),
                "columns": list(tools.COLUMNS), "numeric": list(tools.NUMERIC_COLUMNS),
                "sides": list(tools.SIDES), "types": list(MANIFEST_TYPES),
                "tips": {"reload": tools.RELOAD_TIP, "open": tools.OPEN_TIP,
                         "coverage": tools.COVERAGE_TIP},
                "hint": tools.PATHS_HINT}

    @command
    def rows(self, start: int, count: int) -> List[Dict]:
        """The table's rows *start* to *start* + *count*, as it shows them."""
        if self._manifest is None:
            return []
        start = max(0, int(start))
        out = []
        for position in range(start, min(len(self._shown), start + max(0, int(count)))):
            index = self._shown[position]
            row = self._manifest.rows[index]
            missing = [tools.FIELDS.index(side) for side in row.missing]
            tips = {tools.FIELDS.index(side): self._say(tip) for side in tools.SIDES
                    if (tip := tools.path_tip(row, side, self._manifest.root))}
            out.append({"p": position, "cells": list(self._text[index]),
                        "missing": missing, "tips": tips})
        return out

    # ----------------------------------------------------- filters and sort

    @command
    def set_filters(self, picked: Dict[str, int], missing_only: bool = False) -> Dict:
        for field, index in (picked or {}).items():
            if field in self._picked and 0 <= int(index) <= len(self._choices[field]):
                self._picked[field] = int(index)
        self._missing_only = bool(missing_only)
        return self._changed()

    @command
    def sort(self, column: int, descending: bool = False) -> Dict:
        column = int(column)
        self._sort = (column if 0 <= column < len(tools.COLUMNS) else -1, bool(descending))
        return self._changed()

    @command
    def focus(self, noise: int, snr: int) -> Dict:
        """Show just the rows behind one coverage cell, by its place in the
        grid as last sent."""
        if self._manifest is None:
            return self.view()
        noises, snrs = self._axes()
        noise, snr = int(noise), int(snr)
        if 0 <= noise < len(noises) and 0 <= snr < len(snrs):
            self._picked["noise"] = self._choices["noise"].index(noises[noise]) + 1
            self._picked["snr_db"] = self._choices["snr_db"].index(snrs[snr]) + 1
        return self._changed()

    def _changed(self) -> Dict:
        self._refresh()
        if self._listening is not None:
            self._audition()                # the selection may have gone
        return self.view()

    # ----------------------------------------------------------- selection

    def selection(self) -> Dict:
        row = self._row()
        return {"position": self._position(), "listening": self._listening,
                "can_open": row is not None and row.noisy is not None,
                "sides": {side: {"enabled": row is not None and getattr(row, side) is not None,
                                 "tip": self._say(tools.listen_tip(row, side))}
                          for side in tools.SIDES}}

    @command
    def select(self, position: int) -> Dict:
        position = int(position)
        self._selected = self._shown[position] if 0 <= position < len(self._shown) else None
        if self._listening is not None:
            self._audition()                # follow the selection while playing
        return self.selection()

    @command
    def open_row(self) -> Dict:
        """Hand the selected row's noisy file to the window, as File → Open."""
        row = self._row()
        if row is None:
            self.report("Select a row first.")
            return self.studio.state()
        if row.noisy is None:
            self.report(f"{Path(row.pair.noisy).name} is not on disk — nothing to open.")
            return self.studio.state()
        self._stop_listening()
        return self.studio.open_files([str(row.noisy)])

    # ------------------------------------------------------------ audition

    @command
    def listen(self, side: str, on: bool = True) -> Dict:
        if side not in tools.SIDES:
            raise ValueError(f"unknown side {side!r}")
        if not on:
            if self._listening == side:
                self._stop_listening()
            return self.selection()
        self._listening, self._heard = side, None
        self._audition()
        return self.selection()

    def _audition(self) -> None:
        """Loop the chosen side of the selected row. Read here rather than on
        a job: a corpus file is seconds long and loads in milliseconds."""
        side, row = self._listening, self._row()
        path = getattr(row, side) if row is not None and side else None
        if path is None:
            self._stop_listening()
            return
        if self._heard == (self._selected, side):
            return
        try:
            y, sr = kudio.file_load(path, sr=None, mono=True)
        except Exception as e:  # noqa: BLE001
            self._stop_listening()
            self.report(f"Could not play {path.name} — {type(e).__name__}: {e}")
            return
        if not self.studio.start_preview(self.id, y, int(sr)):
            self._stop_listening()
            return
        # starting it may have ended another panel's preview, and told every
        # panel, this one included
        self._listening, self._heard = side, (self._selected, side)
        self.publish(selection=self.selection())
        self.report(f"Auditioning the {side} file, {path.name} — nothing has been loaded.")

    def _stop_listening(self) -> None:
        if self._listening is None:
            return
        if self.studio.previewing(self.id):
            self.studio.stop_preview()      # which comes back to cancel_preview
        self.cancel_preview()

    def cancel_preview(self) -> None:
        if self._listening is None:
            return
        self._listening = self._heard = None
        self.publish(selection=self.selection())


__all__ = ["ManifestPanel", "MANIFEST_TYPES"]
