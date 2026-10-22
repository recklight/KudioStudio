# -*- coding: utf-8 -*-
"""Browse a corpus by its manifest — what `kudio.Synthesizer` wrote.

A synthesised corpus is thousands of files whose names carry the clean file,
the noise and the SNR. The room, the link and the reverberant target are not in
the names at all: they are only in ``manifest.json``, which is why the
Synthesize panel writes one. This panel reads it back, so the corpus can be
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
on its own paired one project's mixtures with the other's clean files while
the summary said every file was on disk.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel
from kudiostudio.theme import ACCENT_COLOR, INK_COLOR, OK_COLOR, WARN_COLOR

log = logging.getLogger(__name__)

MANIFEST_FILTER = "Manifest (*.json);;All files (*)"
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
    `Path` objects per candidate was most of the time a large manifest took.
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
    Caught here, on the worker, it is a sentence about the file. Caught by
    the table, it was a traceback with half the panel already showing the
    new manifest and the rest still showing the old one.
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

    Runs on a worker: the reading is quick, but finding the files is a stat
    or more per path, three paths a row, and a corpus is thousands of rows.
    *cwd* is the directory relative paths are tried against first; it is
    passed in rather than read here so the panel decides it on the GUI thread,
    at the moment the manifest was asked for.
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


def _filter_text(field: str, value) -> str:
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


# ----------------------------------------------------------------- model

class ManifestModel(QAbstractTableModel):
    """The rows behind the table, sorted and filtered here.

    A `QTableWidget` builds an item for every cell up front, which at twenty
    thousand rows is most of a second with the window frozen. A model is only
    asked for the cells on screen, and sorting a list of row numbers in Python
    takes milliseconds.

    Sorting compares values, not text — the same reason Compare's
    `NumericItem` exists: as text, "10" sorts before "5" and "-5" lands
    wherever the minus sign puts it. A row with no value sorts last whichever
    way the column is turned, and with the sort indicator cleared the rows go
    back to the manifest's own order.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: Tuple[ManifestRow, ...] = ()
        self._text: List[Tuple[str, ...]] = []
        self._folders: Dict[str, str] = {}
        self._root: Optional[Path] = None
        self._sorted: List[int] = []
        self._shown: List[int] = []
        self._accepts: Callable[[ManifestRow], bool] = lambda row: True
        self._sort: Tuple[int, Qt.SortOrder] = (-1, Qt.AscendingOrder)

    # -------------------------------------------------------------- content

    def set_manifest(self, manifest: Optional[LoadedManifest]) -> None:
        self.beginResetModel()
        self._rows = manifest.rows if manifest is not None else ()
        self._folders = dict(manifest.folders) if manifest is not None else {}
        self._root = manifest.root if manifest is not None else None
        self._text = [self._row_text(row) for row in self._rows]
        self._sorted = self._ordered(*self._sort)
        self._shown = [i for i in self._sorted if self._accepts(self._rows[i])]
        self.endResetModel()

    def _row_text(self, row: ManifestRow) -> Tuple[str, ...]:
        pair = row.pair
        return tuple(
            _shown_path(getattr(pair, field), self._folders.get(field, ""))
            if field in SIDES else format_value(getattr(pair, field))
            for field in FIELDS)

    def set_filter(self, accepts: Callable[[ManifestRow], bool]) -> None:
        """Show only the rows *accepts* passes, keeping the sort order."""
        self._relayout(lambda: setattr(self, "_accepts", accepts))

    def row_at(self, row: int) -> Optional[ManifestRow]:
        if 0 <= row < len(self._shown):
            return self._rows[self._shown[row]]
        return None

    def view_row(self, target: ManifestRow) -> int:
        """Where *target* is in the table now, or -1 when it is filtered out."""
        for position, index in enumerate(self._shown):
            if self._rows[index] is target:
                return position
        return -1

    def shown_rows(self) -> List[ManifestRow]:
        return [self._rows[i] for i in self._shown]

    @property
    def total(self) -> int:
        return len(self._rows)

    # -------------------------------------------------------------- sorting

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        self._relayout(lambda: setattr(self, "_sort", (column, order)))

    def _ordered(self, column: int, order: Qt.SortOrder) -> List[int]:
        everything = range(len(self._rows))
        if not 0 <= column < len(COLUMNS):
            return list(everything)
        keys = [self._key(source, column) for source in everything]
        present = [i for i in everything if keys[i] is not None]
        # stable, and from the manifest's order every time, so rows that tie
        # stay in the order the corpus was written in
        present.sort(key=keys.__getitem__,
                     reverse=order == Qt.DescendingOrder)
        return present + [i for i in everything if keys[i] is None]

    def _key(self, source: int, column: int):
        """What row *source* sorts by in *column*; ``None`` sorts last."""
        value = getattr(self._rows[source].pair, FIELDS[column])
        if column in NUMERIC_COLUMNS:
            return _number(value)
        if value is None or value == "":
            return None
        return self._text[source][column].casefold()

    def _relayout(self, change: Callable[[], None]) -> None:
        """Apply *change* and re-sort, keeping the selection on its row.

        A layout change with the persistent indexes moved by hand is what
        `QSortFilterProxyModel` does; a reset instead would drop the selection
        every time a column was clicked.
        """
        self.layoutAboutToBeChanged.emit()
        before = self.persistentIndexList()
        held = [self._shown[index.row()] if index.row() < len(self._shown)
                else None for index in before]
        change()
        self._sorted = self._ordered(*self._sort)
        self._shown = [i for i in self._sorted if self._accepts(self._rows[i])]
        where = {source: position for position, source in enumerate(self._shown)}
        after = [self.index(where[source], index.column())
                 if source in where else QModelIndex()
                 for source, index in zip(held, before)]
        self.changePersistentIndexList(before, after)
        self.layoutChanged.emit()

    # ------------------------------------------------------------ Qt model

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._shown)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.DisplayRole):
        if orientation != Qt.Horizontal or not 0 <= section < len(COLUMNS):
            return None
        if role == Qt.DisplayRole:
            return COLUMNS[section]
        if role == Qt.ToolTipRole:
            folder = self._folders.get(FIELDS[section], "")
            if folder:
                return f"paths shown relative to {folder}"
        return None

    #: the roles `data` answers. A view sizing its columns asks a cell for a
    #: dozen roles; turning the rest away first keeps that from costing
    #: a second on a large manifest.
    ROLES = frozenset({Qt.DisplayRole, Qt.TextAlignmentRole, Qt.ForegroundRole,
                       Qt.ToolTipRole})

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if role not in self.ROLES or not index.isValid() \
                or index.row() >= len(self._shown):
            return None
        source = self._shown[index.row()]
        row, column = self._rows[source], index.column()
        field = FIELDS[column]
        if role == Qt.DisplayRole:
            return self._text[source][column]
        if role == Qt.TextAlignmentRole and column in NUMERIC_COLUMNS:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if field not in SIDES:
            return None
        named = getattr(row.pair, field)
        found = getattr(row, field)
        if role == Qt.ForegroundRole and named and found is None:
            return QColor(WARN_COLOR)
        if role == Qt.ToolTipRole and named:
            if found is not None:
                return str(found)
            if self._root is not None and not os.path.isabs(named):
                return f"not on disk: {named}, looked for under {self._root}"
            return f"not on disk: {named}"
        return None


# ----------------------------------------------------------------- panel

class ManifestPanel(Panel):
    """Read a corpus manifest, check it against the disk, and listen to it."""

    title = "Manifest"
    icon = "manifest"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._manifest: Optional[LoadedManifest] = None
        #: the side being auditioned, ``None`` when nothing is
        self._listening: Optional[str] = None
        #: the row and side last handed to the window. A filter change that
        #: leaves the selection where it was must not start the file again.
        self._heard: Optional[Tuple[ManifestRow, str]] = None
        #: the value behind each filter entry after "all", per Pair field
        self._choices: Dict[str, List[object]] = {}
        #: what the coverage grid is drawing, so a cell can be traced back
        self._coverage: Optional[Coverage] = None

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("no manifest open")
        self.open_file_button = QPushButton("Open…")
        self.reload_button = QPushButton("Reload")
        self.reload_button.setEnabled(False)
        self.reload_button.setToolTip(
            "Read the manifest again and look for every file again — after a "
            "synthesis run has rewritten it, or a folder has been moved back.")
        picker = QHBoxLayout()
        picker.addWidget(self.path_edit, stretch=1)
        picker.addWidget(self.open_file_button)
        picker.addWidget(self.reload_button)

        self.call = self.call_label("kudio.load_manifest(path)")
        self.summary = QLabel("Open a manifest.json — the Synthesize panel "
                              "writes one beside every corpus.")
        self.summary.setWordWrap(True)

        self.filters: Dict[str, QComboBox] = {}
        grid = QGridLayout()
        for k, (field, label) in enumerate(FILTERS):
            box = QComboBox()
            box.addItem(ALL)
            box.setEnabled(False)
            self.filters[field] = box
            grid.addWidget(QLabel(label), k // 2, 2 * (k % 2))
            grid.addWidget(box, k // 2, 2 * (k % 2) + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.missing_only = QCheckBox("only rows with a file missing")
        self.shown = QLabel("")

        source = QGroupBox("kudio.load_manifest")
        source_layout = QVBoxLayout(source)
        source_layout.addLayout(picker)
        source_layout.addWidget(self.call)
        source_layout.addWidget(self.summary)
        source_layout.addLayout(grid)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.missing_only)
        filter_row.addStretch(1)
        filter_row.addWidget(self.shown)
        source_layout.addLayout(filter_row)

        self.model = ManifestModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        # a synthesised name starts with the clean file and ends with the SNR
        # and the room; the middle is the part that can go
        self.table.setTextElideMode(Qt.ElideMiddle)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        # sizing a column to its contents measures this many rows, not all of
        # them; the names in a corpus are all much the same length
        header.setResizeContentsPrecision(200)
        # opens in the manifest's own order, and a third click returns to it
        header.setSortIndicatorClearable(True)
        header.setSortIndicator(-1, Qt.AscendingOrder)
        self.table.setSortingEnabled(True)

        self.open_button = QPushButton("Open")
        self.open_button.setToolTip(
            "Load this row's noisy file into the session, as File → Open "
            "would. Double-clicking a row does the same.")
        self.listen: Dict[str, QPushButton] = {}
        actions = QHBoxLayout()
        actions.addWidget(self.open_button)
        actions.addSpacing(12)
        for side in SIDES:
            button = QPushButton(f"♪  {side}")
            button.setCheckable(True)
            button.setStyleSheet(
                f"QPushButton:checked {{ background: {ACCENT_COLOR}; "
                f"color: {INK_COLOR}; font-weight: bold; }}")
            # The side rides on the button, and one method serves all three.
            # A lambda or a partial over ``self`` here is held by the button,
            # which the panel holds: a cycle no collector can see, which kept
            # every Manifest panel alive until the interpreter shut down, and
            # crashed it there.
            button.setProperty("side", side)
            button.toggled.connect(self._on_listen_button_toggled)
            self.listen[side] = button
            actions.addWidget(button)
        actions.addStretch(1)

        self.coverage_table = QTableWidget(0, 0)
        self.coverage_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.coverage_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.coverage_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.coverage_table.setToolTip(
            "Rows per noise × SNR among the rows shown. Double-click a cell "
            "to show just those rows.")
        self.coverage_note = QLabel("")
        self.coverage_note.setWordWrap(True)
        coverage_box = QGroupBox("Rows per noise × SNR")
        coverage_layout = QVBoxLayout(coverage_box)
        coverage_layout.addWidget(self.coverage_table, stretch=1)
        coverage_layout.addWidget(self.coverage_note)

        rows_box = QWidget()
        rows_layout = QVBoxLayout(rows_box)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.addWidget(self.table, stretch=1)
        rows_layout.addLayout(actions)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(rows_box)
        splitter.addWidget(coverage_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(source)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.hint(
            "Relative paths are looked for under the current folder, then the "
            "manifest's folder and the folders above it. Auditioning plays a "
            "file without loading it; Open loads the noisy one for editing."))

        self.open_file_button.clicked.connect(self._pick_manifest)
        self.reload_button.clicked.connect(self._reload)
        for box in self.filters.values():
            box.currentIndexChanged.connect(self._apply_filters)
        self.missing_only.toggled.connect(self._apply_filters)
        self.table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)
        self.table.doubleClicked.connect(self._open)
        self.open_button.clicked.connect(self._open)
        self.coverage_table.cellDoubleClicked.connect(self._focus_combination)
        self.jobs.busy_changed.connect(self._set_busy)
        self._on_selection_changed()

    # ------------------------------------------------------------- loading

    @property
    def manifest(self) -> Optional[LoadedManifest]:
        return self._manifest

    def load(self, path) -> bool:
        """Read *path* on a worker; the table fills in when it is done.

        Returns False when a manifest is already being read. The current
        directory is taken now, on the GUI thread, because it is what a
        relative path in the manifest is tried against first.
        """
        path = Path(path)
        started = self.jobs.run(read_manifest, path, Path.cwd(),
                                on_done=self._loaded,
                                on_failed=lambda message: self._load_failed(
                                    path, message))
        if not started:
            self.report("A manifest is still being read.")
            return False
        self.report(f"Reading {path.name} and looking for its files …")
        return True

    def _pick_manifest(self) -> None:
        start = str(self._manifest.path.parent) if self._manifest else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a corpus manifest", start, MANIFEST_FILTER)
        if path:
            self.load(path)

    def _reload(self) -> None:
        if self._manifest is not None:
            self.load(self._manifest.path)

    def _set_busy(self, busy: bool) -> None:
        self.open_file_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy and self._manifest is not None)
        self.reload_button.setText("Reading…" if busy else "Reload")

    def _loaded(self, manifest: LoadedManifest) -> None:
        # Everything that can fail is worked out before anything is shown, so
        # a manifest that cannot be shown leaves the last one as it was rather
        # than half replaced.
        try:
            choices = {field: distinct(manifest.rows, field)
                       for field, _ in FILTERS}
        except Exception as e:                                  # noqa: BLE001
            self._load_failed(manifest.path, f"{type(e).__name__}: {e}")
            return
        self._stop_listening()
        self._manifest = manifest
        self.path_edit.setText(str(manifest.path))
        self.call.setText(f"kudio.load_manifest({str(manifest.path)!r})")
        self._fill_filters(choices)
        self.model.set_manifest(manifest)
        self._apply_filters()
        self.table.resizeColumnsToContents()

        rows, missing = len(manifest.rows), manifest.missing
        parts = [f"{rows} row(s)"]
        if missing:
            parts.append(f"{missing} with a file missing")
        elif rows:
            parts.append("every file on disk")
        if manifest.root is not None:
            parts.append(f"relative paths found under {manifest.root}")
        self.summary.setText(" · ".join(parts))
        self.summary.setStyleSheet(
            f"color: {WARN_COLOR};" if missing else
            (f"color: {OK_COLOR};" if rows else ""))
        self.report(f"{manifest.path.name} — {rows} row(s)"
                    + (f", {missing} with a file missing." if missing
                       else "."))

    def _load_failed(self, path: Path, message: str) -> None:
        self.report(f"Could not read {path.name} — {message}")

    # ------------------------------------------------------------- filters

    def _fill_filters(self, choices: Dict[str, List[object]]) -> None:
        """Offer *choices* -- every value each filter's field takes."""
        for field, box in self.filters.items():
            values = choices[field]
            self._choices[field] = values
            box.blockSignals(True)
            box.clear()
            box.addItem(ALL)
            box.addItems([_filter_text(field, v) for v in values])
            box.setCurrentIndex(0)
            # one value is not a choice; the filter stays visible but inert
            box.setEnabled(len(values) > 1)
            box.blockSignals(False)
        self.missing_only.blockSignals(True)
        self.missing_only.setChecked(False)
        self.missing_only.blockSignals(False)

    def _wanted(self) -> Dict[str, object]:
        """``{Pair field: value}`` for every filter that is not on "all"."""
        wanted = {}
        for field, box in self.filters.items():
            index = box.currentIndex()
            if index > 0:
                wanted[field] = self._choices[field][index - 1]
        return wanted

    def set_filter(self, field: str, value) -> bool:
        """Point the *field* filter at *value*; False if no row has it."""
        values = self._choices.get(field, [])
        if value not in values:
            return False
        self.filters[field].setCurrentIndex(values.index(value) + 1)
        return True

    def _apply_filters(self) -> None:
        wanted = self._wanted()
        missing_only = self.missing_only.isChecked()

        def accepts(row: ManifestRow) -> bool:
            if missing_only and not row.missing:
                return False
            return all(getattr(row.pair, f) == v for f, v in wanted.items())

        self.model.set_filter(accepts)
        shown = self.model.shown_rows()
        self.shown.setText(f"showing {len(shown)} of {self.model.total}"
                           if self._manifest is not None else "")
        self._fill_coverage(shown, wanted, missing_only)
        self._on_selection_changed()

    # ------------------------------------------------------------ coverage

    def _fill_coverage(self, rows: Sequence[ManifestRow],
                       wanted: Dict[str, object],
                       missing_only: bool = False) -> None:
        """Count the rows shown, over the whole corpus's axes.

        The axes come from the manifest, narrowed only by the noise and SNR
        filters themselves. Filtering by room or link changes the counts and
        not the grid, so a combination that one room lacks is a visible zero
        rather than a row that quietly disappears.

        The manifest records what was written, not what was asked for, so an
        SNR that no row ended up with has no column: a hole can only be shown
        where the corpus has the noise and the SNR somewhere else.

        With *missing_only* the grid counts the rows with a file missing,
        which says where the damage is and nothing about the corpus's
        coverage: a zero there is a combination with nothing missing, and
        reading it as a hole told a full corpus it needed more clean files.
        """
        table = self.coverage_table
        table.clear()
        if self._manifest is None:
            table.setRowCount(0)
            table.setColumnCount(0)
            self.coverage_note.setText("")
            self._coverage = None
            return
        noises = ([wanted["noise"]] if "noise" in wanted
                  else self._choices.get("noise", []))
        snrs = ([wanted["snr_db"]] if "snr_db" in wanted
                else self._choices.get("snr_db", []))
        cov = coverage(rows, noises, snrs)
        table.setRowCount(len(cov.noises))
        table.setColumnCount(len(cov.snrs))
        table.setVerticalHeaderLabels([format_value(n) for n in cov.noises])
        table.setHorizontalHeaderLabels(
            [_filter_text("snr_db", s) for s in cov.snrs])
        gap = QColor(WARN_COLOR)
        for r, noise in enumerate(cov.noises):
            for c, snr in enumerate(cov.snrs):
                count = cov.count(noise, snr)
                item = QTableWidgetItem(str(count))
                item.setTextAlignment(Qt.AlignCenter)
                if count == 0 and not missing_only:
                    item.setBackground(gap)
                    item.setForeground(QColor(INK_COLOR))
                table.setItem(r, c, item)
        self._fit_coverage()
        self._coverage = cov
        if missing_only:
            self.coverage_note.setText(
                f"Rows with a file missing, per noise × SNR: {len(rows)} in "
                f"all. Untick 'only rows with a file missing' for the "
                f"corpus's own coverage.")
            self.coverage_note.setStyleSheet("")
            return
        note, good = describe_coverage(cov, len(rows))
        self.coverage_note.setText(note)
        self.coverage_note.setStyleSheet(
            f"color: {OK_COLOR if good else WARN_COLOR};")

    #: noise rows the coverage grid makes room for before it scrolls
    COVERAGE_ROWS = 6

    def _fit_coverage(self) -> None:
        """Make the grid tall enough to see, up to COVERAGE_ROWS noises.

        Left to the splitter, a grid of three noises showed one of them and a
        scroll bar -- and a hole in a row that is scrolled out of view is not
        visible at a glance, which is the grid's whole job.
        """
        table = self.coverage_table
        table.resizeColumnsToContents()
        rows = min(table.rowCount(), self.COVERAGE_ROWS)
        height = (table.horizontalHeader().sizeHint().height()
                  + rows * table.verticalHeader().defaultSectionSize()
                  + 2 * table.frameWidth()
                  + table.horizontalScrollBar().sizeHint().height())
        table.setMinimumHeight(height)

    def _focus_combination(self, r: int, c: int) -> None:
        """Show just the rows behind one coverage cell."""
        cov = self._coverage
        if cov is None or r >= len(cov.noises) or c >= len(cov.snrs):
            return
        self.set_filter("noise", cov.noises[r])
        self.set_filter("snr_db", cov.snrs[c])

    # ----------------------------------------------------------- selection

    def selected_row(self) -> Optional[ManifestRow]:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model is not None else []
        return self.model.row_at(rows[0].row()) if rows else None

    def select_row(self, position: int) -> Optional[ManifestRow]:
        """Select the *position*-th row as the table shows it now."""
        self.table.selectRow(position)
        return self.selected_row()

    def _on_selection_changed(self, *_) -> None:
        row = self.selected_row()
        self.open_button.setEnabled(row is not None and row.noisy is not None)
        for side, button in self.listen.items():
            button.setEnabled(row is not None
                              and getattr(row, side) is not None)
            button.setToolTip(self._listen_tip(row, side))
        if self._listening is not None:
            self._audition()            # follow the selection while playing

    @staticmethod
    def _listen_tip(row: Optional[ManifestRow], side: str) -> str:
        if row is None:
            return "Select a row to hear it."
        named = getattr(row.pair, side)
        if not named:
            return ("This row has no target — the corpus was written without "
                    "reverberant targets.")
        if getattr(row, side) is None:
            return f"Not on disk: {named}"
        return f"Loop the {side} file without loading it."

    # -------------------------------------------------------------- open

    def _open(self, *_) -> None:
        """Hand the noisy file to the window, to load the way File → Open does."""
        row = self.selected_row()
        if row is None:
            self.report("Select a row first.")
            return
        if row.noisy is None:
            self.report(f"{Path(row.pair.noisy).name} is not on disk — "
                        f"nothing to open.")
            return
        self._stop_listening()
        self.open_requested.emit(str(row.noisy))

    # ---------------------------------------------------------- audition

    def _on_listen_button_toggled(self, on: bool) -> None:
        side = self.sender().property("side")
        if side in self.listen:
            self._on_listen_toggled(side, on)

    def _on_listen_toggled(self, side: str, on: bool) -> None:
        if not on:
            if self._listening == side:
                self._stop_listening()
            return
        for other, button in self.listen.items():
            if other != side and button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
        self._listening = side
        self._audition()

    def _audition(self) -> None:
        """Loop the chosen side of the selected row through the window.

        Read here, on the GUI thread: a corpus file is seconds long and loads
        in milliseconds, and a worker would only add a moment where the button
        is down and nothing is playing.
        """
        side, row = self._listening, self.selected_row()
        path = getattr(row, side) if row is not None and side else None
        if path is None:
            self._stop_listening()
            return
        if self._heard == (row, side):
            return
        try:
            y, sr = kudio.file_load(path, sr=None, mono=True)
        except Exception as e:                                  # noqa: BLE001
            self._stop_listening()
            self.report(f"Could not play {path.name} — {type(e).__name__}: {e}")
            return
        self._heard = (row, side)
        self.preview_ready.emit(y, int(sr))
        self.report(f"Auditioning the {side} file, {path.name} — nothing has "
                    f"been loaded.")

    def _stop_listening(self) -> None:
        if self._listening is not None:
            self.cancel_preview()
            self.preview_stopped.emit()

    def cancel_preview(self) -> None:
        self._listening = None
        self._heard = None
        for button in self.listen.values():
            if button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
