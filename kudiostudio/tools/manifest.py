# -*- coding: utf-8 -*-
"""Read a corpus manifest -- what `kudio.Synthesizer` wrote.

A synthesised corpus is thousands of files whose names carry the clean file,
the noise and the SNR. The room, the link and the reverberant target are not in
the names at all: they are only in ``manifest.json``, which is why the
Synthesize panel writes one. The Manifest panel reads it back, so the corpus can be
looked at as what it is — rows of noisy, clean, noise, SNR, room, link and
target — instead of as a folder of names.

Two questions decide whether a corpus is usable, and both are answered here
before anything is trained on it:

- **Is every file still there?** A manifest outlives the files it names: a
  folder gets moved, a run gets half-deleted, a manifest gets copied to
  another machine. Rows whose files are missing are marked and counted.
- **Is the noise × SNR grid covered?** ``reg`` mode promises every
  combination once in each ``len(noise) * len(snr)`` outputs. A corpus with
  fewer clean files than that cannot keep the promise, and one that has
  enough but still shows a hole has lost rows somewhere. The coverage grid
  shows both at a glance.

**Paths in a manifest may be relative** — to the working directory the
synthesis ran in, which nothing records. They are looked for under the current
directory first, then the manifest's own folder, then each folder above it:
the Synthesizer writes the manifest into its output folder, which sits below
the directory it ran in, and a KudioEnhance run keeps its manifest in the run
directory, which sits below the project. **The whole manifest is read against
one of those folders** — the first that holds every file it names, or failing
that the one that holds most of them — never file by file: two projects built
from the same clean set name their mixtures alike, and looking each path up
on its own would pair one project's mixtures with the other's clean files
while the summary said every file was on disk.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

import kudio

#: what a manifest is called on disk. kudio reads no audio format from JSON,
#: so a ``.json`` among files to open is a manifest, not audio
MANIFEST_SUFFIX = ".json"

#: the three files a row can name, in the order the table shows them
SIDES = ("noisy", "clean", "target")

COLUMNS = ("noisy", "clean", "noise", "SNR dB", "RT60 s", "link", "target")
#: the `kudio.Pair` field behind each column
FIELDS = ("noisy", "clean", "noise", "snr_db", "rt60", "channel", "target")
#: columns that hold a number, and so sort by value rather than by text
NUMERIC_COLUMNS = (COLUMNS.index("SNR dB"), COLUMNS.index("RT60 s"))

#: ``(Pair field, label)`` for each filter, in the order they are laid out
FILTERS = (("noise", "noise"), ("snr_db", "SNR"),
           ("rt60", "room"), ("channel", "link"))
ALL = "all"

#: what each `kudio.Pair` field may hold. kudio reads a manifest without
#: looking at the values, and a list where a number belongs only fails later,
#: in the middle of filling the table, half of it already replaced
_TEXT_FIELDS = ("target", "noise", "channel")
_NUMBER_FIELDS = ("snr_db", "rt60")


def is_manifest(path) -> bool:
    """Whether *path* names a corpus manifest rather than audio."""
    return Path(path).suffix.lower() == MANIFEST_SUFFIX


# --------------------------------------------------------------- the data

@dataclass(frozen=True)
class ManifestRow:
    """One row of a manifest, and where its files actually are.

    The pair is kept exactly as it was read, so anything the table shows can
    be traced back to a line of the manifest; the resolved paths are what the
    panel opens and plays. A resolved path is ``None`` when the file is not on
    disk — or, for the target, when the row never had one.
    """

    pair: kudio.Pair
    noisy: Optional[Path]
    clean: Optional[Path]
    target: Optional[Path]

    @property
    def missing(self) -> Tuple[str, ...]:
        """The sides the manifest names and the disk does not have.

        A row without a target is not missing one: the corpus was written
        without ``write_targets``, and there is nothing to look for.
        """
        return tuple(side for side in SIDES
                     if getattr(self.pair, side) and getattr(self, side) is None)


@dataclass(frozen=True)
class LoadedManifest:
    """A manifest read from disk, with every file it names looked for."""

    path: Path
    rows: Tuple[ManifestRow, ...]
    #: where relative paths were looked for, in order
    search: Tuple[Path, ...]
    #: the one folder every relative path was read against: the first search
    #: folder that holds all of them, or the one that holds most. ``None``
    #: when every path was absolute or none was found anywhere. Shown,
    #: because a corpus of the same layout in the working directory would be
    #: found first, and which one the table describes should not be a guess.
    root: Optional[Path]
    #: the folder each kind of file shares, as the manifest spells it, so the
    #: table can show ``sub/a_n0_5dB.wav`` instead of the same long prefix
    #: ten thousand times. Empty when the paths share nothing.
    folders: Dict[str, str]

    @property
    def missing(self) -> int:
        """How many rows name at least one file that is not on disk."""
        return sum(1 for row in self.rows if row.missing)


def search_folders(manifest, cwd=None) -> Tuple[Path, ...]:
    """Where a relative path in *manifest* is looked for, in order.

    The current directory first, because that is what a relative path means
    to every other program; then the manifest's own folder, for a manifest
    written beside the files it names; then each folder above that, for the
    usual case of ``Synthesizer(..., out_path='data/mixed')``, whose manifest
    is in ``data/mixed`` and whose paths start at the folder above ``data``.
    """
    here = Path(manifest).absolute().parent
    ordered: List[Path] = []
    for folder in (Path(cwd if cwd is not None else Path.cwd()).absolute(),
                   here, *here.parents):
        if folder not in ordered:
            ordered.append(folder)
    return tuple(ordered)


class _Finder:
    """Looks a manifest's paths up under one of a list of folders.

    One folder for the whole manifest, chosen by :meth:`choose`: a corpus is
    one tree, and a path found under one folder beside its partner found
    under another is two corpora mixed, not one corpus found.

    A corpus of ten thousand rows lives in a handful of folders. Asking
    whether each folder exists once, rather than statting every file under
    every candidate, is what keeps a manifest from another machine — where
    nothing is found and every candidate gets tried — from taking minutes.

    Strings and `os.path` rather than `Path` objects: building three or four
    `Path` objects per candidate would be most of the time a large manifest
    takes.
    """

    def __init__(self, search: Sequence[Path]):
        self.search = tuple(search)
        self.root: Optional[str] = None
        self._bases = [str(base) for base in self.search]
        self._folders: Dict[str, bool] = {}
        #: the relative paths found under the chosen root
        self._found: set = set()

    def _exists(self, base: str, text: str) -> bool:
        candidate = os.path.join(base, text)
        folder = os.path.dirname(candidate)
        exists = self._folders.get(folder)
        if exists is None:
            exists = self._folders[folder] = os.path.isdir(folder)
        return exists and os.path.isfile(candidate)

    def choose(self, texts: Iterable[Optional[str]]) -> Optional[str]:
        """Pick the folder the relative paths among *texts* are read against.

        The first search folder that holds every one of them; failing that,
        the one holding the most, the earlier on a tie; ``None`` when no
        folder holds any.
        """
        relative = {t for t in texts if t and not os.path.isabs(t)}
        best, best_found = None, set()
        for base in self._bases:
            found = {t for t in relative if self._exists(base, t)}
            if len(found) > len(best_found):
                best, best_found = base, found
            if len(found) == len(relative):
                break
        self.root, self._found = best, best_found
        return best

    def __call__(self, text: Optional[str]) -> Optional[Path]:
        if not text:
            return None
        if os.path.isabs(text):
            return Path(text) if os.path.isfile(text) else None
        if self.root is None or text not in self._found:
            return None
        return Path(os.path.join(self.root, text))


def _common_folder(texts: Iterable[Optional[str]]) -> str:
    """The folder every one of *texts* is under, or ``""`` if there is none.

    Spelled the way this system spells paths, whichever system wrote the
    manifest: a corpus synthesised on Linux and opened on Windows names its
    files with forward slashes.
    """
    folders = [os.path.dirname(os.path.normpath(t)) for t in texts if t]
    if not folders:
        return ""
    try:
        common = os.path.commonpath(folders)
    except ValueError:              # absolute and relative mixed, or two drives
        return ""
    return "" if common in ("", ".") else common


def check_pairs(pairs: Sequence[kudio.Pair]) -> None:
    """Raise ValueError, naming the row, for a value no manifest should hold.

    kudio writes text and numbers; a hand-edited or foreign manifest can hold
    a list or an object anywhere, and `kudio.load_manifest` passes it on.
    Checked here, on the job's thread, so what the person reads is a sentence
    about the file, with the field and what it expected.
    """
    def kind(value) -> str:
        return f"{type(value).__name__} {value!r}"

    for number, pair in enumerate(pairs, 1):
        for field in ("noisy", "clean"):
            value = getattr(pair, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"row {number}: {field} is {kind(value)}, "
                                 f"expected a path")
        for field in _TEXT_FIELDS:
            value = getattr(pair, field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"row {number}: {field} is {kind(value)}, "
                                 f"expected text or null")
        for field in _NUMBER_FIELDS:
            value = getattr(pair, field)
            if value is not None and (isinstance(value, bool)
                                      or not isinstance(value, (int, float))):
                raise ValueError(f"row {number}: {field} is {kind(value)}, "
                                 f"expected a number or null")


def read_manifest(path, cwd=None) -> LoadedManifest:
    """Load *path* with :func:`kudio.load_manifest` and find every file in it.

    Runs on a job's thread: the reading is quick, but finding the files is a
    stat or more per path, three paths a row, and a corpus is thousands of
    rows. *cwd* is the directory relative paths are tried against first; it
    is passed in rather than read here so the panel decides it at the moment
    the manifest is asked for.
    """
    path = Path(path).absolute()
    pairs = kudio.load_manifest(path)
    check_pairs(pairs)
    search = search_folders(path, cwd)
    find = _Finder(search)
    root = find.choose(getattr(p, side) for p in pairs for side in SIDES)
    rows = tuple(ManifestRow(pair, find(pair.noisy), find(pair.clean),
                             find(pair.target))
                 for pair in pairs)
    folders = {side: _common_folder(getattr(p, side) for p in pairs)
               for side in SIDES}
    return LoadedManifest(path, rows, search,
                          Path(root) if root is not None else None, folders)


class Coverage(NamedTuple):
    """How many rows each noise × SNR combination has.

    The axes are given rather than read off the rows being counted, so that a
    combination with no rows still has a cell — a hole that is not drawn is a
    hole nobody sees.
    """

    noises: Tuple[str, ...]
    snrs: Tuple[object, ...]
    counts: Dict[Tuple[str, object], int]

    def count(self, noise: str, snr) -> int:
        return self.counts.get((noise, snr), 0)

    @property
    def combinations(self) -> int:
        return len(self.noises) * len(self.snrs)

    @property
    def gaps(self) -> List[Tuple[str, object]]:
        """Every combination with no rows, noise by noise."""
        return [(noise, snr) for noise in self.noises for snr in self.snrs
                if self.count(noise, snr) == 0]


def _value_order(value) -> tuple:
    """Sort key for a filter or axis value: numbers by value, ``None`` last."""
    number = _number(value)
    if number is not None:
        return (0, number, "")
    return (1 if value is not None else 2, 0.0, str(value))


def distinct(rows: Iterable[ManifestRow], field: str) -> List[object]:
    """Every value *field* takes in *rows*, numbers in numeric order."""
    return sorted({getattr(row.pair, field) for row in rows}, key=_value_order)


def coverage(rows: Sequence[ManifestRow], noises: Optional[Sequence[str]] = None,
             snrs: Optional[Sequence[object]] = None) -> Coverage:
    """Count *rows* per noise × SNR, over the given axes or the rows' own."""
    noises = tuple(distinct(rows, "noise") if noises is None else noises)
    snrs = tuple(distinct(rows, "snr_db") if snrs is None else snrs)
    counts: Dict[Tuple[str, object], int] = Counter(
        (row.pair.noise, row.pair.snr_db) for row in rows)
    return Coverage(noises, snrs, dict(counts))


def describe_coverage(cov: Coverage, rows: int) -> Tuple[str, bool]:
    """One sentence about *cov*, and whether it is good news.

    A hole means different things depending on the row count. With fewer rows
    than combinations, ``reg`` mode cannot reach them all and the fix is more
    clean files. With enough rows and a hole anyway, it is not what one run
    writes: ``reg`` fills every combination once per cycle and ``inc`` fills
    them all. Rows have been lost — a collision, a hand edit — or the manifest
    is two runs with different grids, merged.
    """
    total = cov.combinations
    if total == 0:
        return "Nothing to cover.", True
    gaps = len(cov.gaps)
    if gaps:
        if rows < total:
            return (f"{gaps} of {total} noise × SNR combinations have no rows "
                    f"— {rows} row(s) cannot cover {total} combinations; in "
                    f"reg mode that takes at least {total} clean files."), False
        return (f"{gaps} of {total} noise × SNR combinations have no rows, "
                f"although {rows} rows are enough to cover every one. One run "
                f"never leaves holes like that: rows have gone missing, or "
                f"this manifest is more than one run."), False
    counts = [cov.count(n, s) for n in cov.noises for s in cov.snrs]
    low, high = min(counts), max(counts)
    if low == high:
        return (f"Every one of the {total} combinations has {low} "
                f"row(s)."), True
    if high - low == 1:
        return (f"Every one of the {total} combinations has {low} or {high} "
                f"rows."), True
    # reg mode keeps every combination within one row of every other, and
    # inc mode makes them equal; a wider spread is not a corpus either wrote
    return (f"Every one of the {total} combinations has rows, but unevenly — "
            f"{low} to {high} each."), False


# ------------------------------------------------------------------ text

def _number(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_value(value) -> str:
    """How a manifest value is shown: ``5``, ``2.5``, ``0.3`` — or a dash."""
    if value is None or value == "":
        return "—"
    number = _number(value)
    return f"{number:g}" if number is not None else str(value)


def filter_text(field: str, value) -> str:
    """A filter choice. "none" is a choice — no room, no link — not a gap."""
    if value is None or value == "":
        return "none"
    text = format_value(value)
    return {"snr_db": f"{text} dB", "rt60": f"{text} s"}.get(field, text)


def _shown_path(text: Optional[str], folder: str) -> str:
    """*text* without the folder every path of its kind shares."""
    if not text:
        return "—"
    spelled = os.path.normpath(text)            # as `_common_folder` spells it
    if folder and spelled.startswith(folder + os.sep):
        return spelled[len(folder) + 1:]
    return text


def row_text(row: ManifestRow, folders: Dict[str, str]) -> Tuple[str, ...]:
    """The table's cells for *row*, the shared folder of each kind left off."""
    pair = row.pair
    return tuple(_shown_path(getattr(pair, field), folders.get(field, ""))
                 if field in SIDES else format_value(getattr(pair, field))
                 for field in FIELDS)


# --------------------------------------------------------------- sorting

def sort_key(row: ManifestRow, text: Sequence[str], column: int):
    """What *row* sorts by in *column*; ``None`` sorts last.

    Values, not text: as text, "10" sorts before "5" and "-5" lands wherever
    the minus sign puts it.
    """
    value = getattr(row.pair, FIELDS[column])
    if column in NUMERIC_COLUMNS:
        return _number(value)
    if value is None or value == "":
        return None
    return text[column].casefold()


def ordered(rows: Sequence[ManifestRow], texts: Sequence[Sequence[str]], column: int,
            descending: bool = False) -> List[int]:
    """Row numbers sorted by *column*; the manifest's own order for no column.

    A row with no value sorts last whichever way the column is turned.
    """
    everything = range(len(rows))
    if not 0 <= column < len(COLUMNS):
        return list(everything)
    keys = [sort_key(rows[i], texts[i], column) for i in everything]
    present = [i for i in everything if keys[i] is not None]
    # stable, and from the manifest's order every time, so rows that tie
    # stay in the order the corpus was written in
    present.sort(key=keys.__getitem__, reverse=descending)
    return present + [i for i in everything if keys[i] is None]


def accepts(row: ManifestRow, wanted: Dict[str, object], missing_only: bool = False) -> bool:
    """Whether *row* passes the filters: ``{Pair field: value}`` and the
    missing-files box."""
    if missing_only and not row.missing:
        return False
    return all(getattr(row.pair, f) == v for f, v in wanted.items())


# ----------------------------------------------------------------- words

OPEN_HINT = "Open a manifest.json — the Synthesize panel writes one beside every corpus."
RELOAD_TIP = ("Read the manifest again and look for every file again — after a synthesis "
              "run has rewritten it, or a folder has been moved back.")
OPEN_TIP = ("Load this row's noisy file into the session, as File → Open would. "
            "Double-clicking a row does the same.")
COVERAGE_TIP = ("Rows per noise × SNR among the rows shown. Double-click a cell to show "
                "just those rows.")
PATHS_HINT = ("Relative paths are looked for under the current folder, then the manifest's "
              "folder and the folders above it. Auditioning plays a file without loading "
              "it; Open loads the noisy one for editing.")


def summary_parts(manifest: LoadedManifest) -> Tuple[List[str], str]:
    """The line under the path, in parts, and its tone: ``"warn"`` when a
    file is missing, ``"ok"`` when every one is there, ``""`` for no rows."""
    rows, missing = len(manifest.rows), manifest.missing
    parts = [f"{rows} row(s)"]
    if missing:
        parts.append(f"{missing} with a file missing")
    elif rows:
        parts.append("every file on disk")
    if manifest.root is not None:
        parts.append(f"relative paths found under {manifest.root}")
    return parts, ("warn" if missing else "ok" if rows else "")


def loaded_text(manifest: LoadedManifest) -> str:
    """The status line once a manifest is read."""
    rows, missing = len(manifest.rows), manifest.missing
    return (f"{manifest.path.name} — {rows} row(s)"
            + (f", {missing} with a file missing." if missing else "."))


def missing_note(rows: int) -> str:
    """The note under the grid while it counts only the damaged rows."""
    return (f"Rows with a file missing, per noise × SNR: {rows} in all. Untick 'only "
            f"rows with a file missing' for the corpus's own coverage.")


def header_tip(folder: str) -> str:
    return f"paths shown relative to {folder}" if folder else ""


def path_tip(row: ManifestRow, field: str, root: Optional[Path]) -> Optional[str]:
    """Where a file cell's file is, or why it is not there."""
    named = getattr(row.pair, field)
    if not named:
        return None
    found = getattr(row, field)
    if found is not None:
        return str(found)
    if root is not None and not os.path.isabs(named):
        return f"not on disk: {named}, looked for under {root}"
    return f"not on disk: {named}"


def listen_tip(row: Optional[ManifestRow], side: str) -> str:
    if row is None:
        return "Select a row to hear it."
    named = getattr(row.pair, side)
    if not named:
        return "This row has no target — the corpus was written without reverberant targets."
    if getattr(row, side) is None:
        return f"Not on disk: {named}"
    return f"Loop the {side} file without loading it."
