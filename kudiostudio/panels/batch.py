# -*- coding: utf-8 -*-
"""Folder-wide conversion — the GUI form of the `kudio` command line.

Resample, change bit depth and trim silence across a whole directory. The file
list comes from :func:`kudio.check_input`, so the same folder/manifest/glob
inputs the library accepts work here too.

Two passes go further than the command line does, and both are built here
from public `kudio` calls rather than added to the library:

* **A loudness report.** ``convert_folder(lufs=...)`` applies a gain and says
  nothing about what came of it, and the job people actually have is
  "normalise this tree to -23 LUFS and tell me which files could not get
  there". Every written file is read back and measured, and the ones that
  missed -- clipped at full scale on the way up, over the true-peak ceiling,
  too short for BS.1770 to measure at all -- are listed with the reason.
  With a denoise pass as well, the levelling waits for it: denoising takes
  level out with the noise, so a tree levelled first and denoised after
  landed under the target it was asked for.
* **A reverberant copy.** "Re-room this tree and keep both": each written file
  is put in a room of its own (`kudio.rir`, `kudio.apply_rir`) and written to
  a second tree with the same layout, so a dry file and its wet copy pair by
  path.
"""
from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import DirPicker, Panel
from kudiostudio.theme import CALL_COLOR

log = logging.getLogger(__name__)

SUBTYPES = ("PCM_16", "PCM_24", "PCM_32", "FLOAT")
RATES = ("keep", "48000", "44100", "32000", "22050", "16000", "8000")
NORMALIZE_MODES = ("none", "peak", "LUFS")
#: (range, default, suffix) per mode -- a peak of -23 or a loudness of 0.99
#: are both nonsense, so the box changes with the choice
_NORMALIZE_RANGES = {
    "peak": ((0.01, 1.0), 0.99, ""),
    "LUFS": ((-60.0, 0.0), -23.0, " LUFS"),      # -23 is EBU R 128
}

#: How far from the target a file may land and still count as on it, in LU.
#: One LU is the margin `kudio loudness --target` allows.
LOUDNESS_TOLERANCE = 1.0
#: The true-peak ceiling, in dBTP. -1 is EBU R 128's: the headroom a converter
#: or a lossy encoder downstream needs to reconstruct the waveform unclipped.
TRUE_PEAK_CEILING = -1.0
#: BS.1770 gates in 400 ms blocks, so nothing shorter has a loudness at all.
MIN_LOUDNESS_SECONDS = 0.4
#: A peak this close to full scale is one `save_wave` clipped there. A
#: fixed-point file cannot go higher, so a peak sitting on the ceiling is a
#: peak that wanted to go past it.
_FULL_SCALE = 10.0 ** (-0.01 / 20.0)
#: How `convert_folder` records kudio's refusal to measure a clip shorter than
#: one block -- the one conversion failure that is a loudness finding.
_TOO_SHORT = "loudness() needs at least"
#: Where the reverberant copies go when no folder is named: beside the
#: output, never inside it, so neither tree picks up the other's files.
REVERBERANT_SUFFIX = "_reverberant"


# ----------------------------------------------------------------- the job

@dataclass(frozen=True)
class RoomPlan:
    """The room every file of a reverberant copy is put in.

    *seed* is the run's; each file draws from :func:`file_seed` of it. It is
    ``None`` only in a caption, before Run has drawn one.
    """
    rt60: float
    drr_db: float
    seed: Optional[int]
    folder: Path


@dataclass(frozen=True)
class BatchPlan:
    """Everything one Run needs, read off the panel on the GUI thread.

    The job runs on a worker, and a worker that reads the widgets as it goes
    reads them while they can still be changed: a value edited mid-run would
    apply to half the folder.
    """
    src: str
    dst: str
    #: keyword arguments for `kudio.convert_folder`
    convert: Dict[str, Any]
    denoise: Optional[str] = None
    noise: Optional[str] = None
    room: Optional[RoomPlan] = None
    tolerance: float = LOUDNESS_TOLERANCE
    ceiling: float = TRUE_PEAK_CEILING

    @property
    def lufs(self) -> Optional[float]:
        """The loudness target, or ``None``."""
        return self.convert.get("lufs")

    @property
    def level_last(self) -> bool:
        """Whether the loudness target is applied after the denoise pass
        rather than by the conversion. Denoising removes energy, so levelled
        first, a file comes out of the denoise under the target."""
        return self.denoise is not None and self.lufs is not None

    def conversion(self) -> Dict[str, Any]:
        """The keyword arguments `kudio.convert_folder` is actually given."""
        if self.level_last:
            return {k: v for k, v in self.convert.items() if k != "lufs"}
        return dict(self.convert)


@dataclass(frozen=True)
class LoudnessMiss:
    """A file the loudness target did not hold for, and why.

    *headroom* separates the two ways to miss. ``False``: the file is not at
    the target, or got there by clipping -- it could not get there. ``True``:
    it is as loud as asked, and what it lacks is the room above its peaks.
    """
    path: Path
    #: integrated loudness as written, in LUFS; ``None`` when there is none
    lufs: Optional[float]
    reason: str
    headroom: bool = False


@dataclass(frozen=True)
class LoudnessCheck:
    """What reading a normalised tree back found."""
    target: float
    tolerance: float
    ceiling: float
    #: files the target was asked of: every one written, and every one the
    #: conversion refused because it was too short to measure
    checked: int
    misses: Tuple[LoudnessMiss, ...] = ()

    @property
    def off_target(self) -> Tuple[LoudnessMiss, ...]:
        return tuple(m for m in self.misses if not m.headroom)

    @property
    def over_ceiling(self) -> Tuple[LoudnessMiss, ...]:
        return tuple(m for m in self.misses if m.headroom)

    @property
    def held(self) -> int:
        return self.checked - len(self.misses)


@dataclass(frozen=True)
class Levelling:
    """What the levelling pass after a denoise did, file by file."""
    target: float
    written: Tuple[Path, ...] = ()
    failed: Tuple[Tuple[Path, str], ...] = ()


@dataclass(frozen=True)
class RoomCopies:
    """What the reverberant pass wrote: ``(dry, wet)`` pairs, and failures."""
    folder: Path
    seed: int
    written: Tuple[Tuple[Path, Path], ...] = ()
    failed: Tuple[Tuple[Path, str], ...] = ()


@dataclass(frozen=True)
class BatchOutcome:
    """What one Run did.

    *result* is what the summary line counts: the conversion's result, or the
    denoise pass's when there was one, as it always has been. *converted* is
    the conversion's own, kept because the denoise pass only sees the files
    that were written and its result would lose the ones that were not.
    """
    result: Any
    converted: Optional[kudio.ConvertResult] = None
    rooms: Optional[RoomCopies] = None
    loudness: Optional[LoudnessCheck] = None
    levelled: Optional[Levelling] = None

    @property
    def failed(self) -> List[Tuple[Path, str]]:
        """Every ``(path, reason)`` a pass gave up on, in the order they ran."""
        failed: List[Tuple[Path, str]] = []
        if self.converted is not None and self.converted is not self.result:
            failed += list(self.converted.failed)
        failed += list(self.result.failed)
        if self.levelled is not None:
            failed += list(self.levelled.failed)
        if self.rooms is not None:
            failed += list(self.rooms.failed)
        return failed


def file_seed(base: int, relative) -> int:
    """The seed one file's room is drawn from: the run's seed and where the
    file sits in the tree.

    The path below the tree's root rather than the name, because a mirrored
    tree repeats names, and rather than the file's place in the list, which
    moves when a file is added. It is the derivation `kudio.Synthesizer` uses
    for its own per-file draws.
    """
    rel = PurePath(relative).as_posix()
    return int(base) ^ (zlib.crc32(rel.encode("utf-8")) & 0x7FFFFFFF)


def reverberant_folder(dst) -> Path:
    """Where the reverberant copies of *dst* go by default: beside it."""
    dst = Path(dst)
    return dst.with_name(dst.name + REVERBERANT_SUFFIX)


def trees_overlap(a, b) -> bool:
    """Whether one folder is the other or holds it.

    "Keep both" needs two trees. Written into the output, the copies would
    collide with the dry files or sit among them, and the next pass over the
    output -- this panel's own denoise, or the next Run -- would take them for
    more dry files.
    """
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or a in b.parents or b in a.parents


def write_reverberant(dry: Sequence[Path], root, room: RoomPlan, *,
                      subtype: str = "PCM_16",
                      lufs: Optional[float] = None) -> RoomCopies:
    """Put each file in *dry* in a room and write it below *room.folder*.

    The copy goes where the dry file sits below *root*, so the two trees pair
    by path, and an existing file there is renamed rather than overwritten,
    as everywhere else in this panel. The dry files are not touched.

    Each file gets a room of its own -- the same RT60 and DRR, a different
    tail drawn from :func:`file_seed` -- since one room repeated over a corpus
    is a single acoustic condition, and a model trained on it learns that
    room. `apply_rir` keeps the length and the peak, and `kudio.rir` puts the
    direct path at sample 0, so wet and dry stay sample-aligned. A
    multi-channel file gets the same room on every channel.

    With *lufs* the copy is levelled to it too: the room moves energy into the
    tail, and a copy left at the dry file's peak is no longer at the loudness
    the tree was normalised to.
    """
    if room.seed is None:
        raise ValueError("a reverberant pass needs a seed; draw one first")
    root = Path(root)
    written: List[Tuple[Path, Path]] = []
    failed: List[Tuple[Path, str]] = []
    for path in map(Path, dry):
        try:
            rel = path.relative_to(root)
            y, sr = kudio.file_load(path, sr=None, mono=False)
            ir = kudio.rir(sr, rt60=room.rt60, drr_db=room.drr_db,
                           seed=file_seed(room.seed, rel))
            if y.ndim == 1:
                wet = kudio.apply_rir(y, ir)
            else:
                wet = np.stack([kudio.apply_rir(y[:, c], ir)
                                for c in range(y.shape[1])], axis=1)
            if lufs is not None:
                wet = kudio.normalize_lufs(wet, sr, lufs=lufs)
            target = Path(kudio.check_file(Path(room.folder) / rel, rename=True))
            kudio.save_wave(target, wet, sr, subtype=subtype)
            written.append((path, target))
        except Exception as e:                                  # noqa: BLE001
            failed.append((path, f"{type(e).__name__}: {e}"))
    return RoomCopies(folder=Path(room.folder), seed=int(room.seed),
                      written=tuple(written), failed=tuple(failed))


def level_files(files: Sequence[Path], target: float, *,
                subtype: str = "PCM_16") -> Levelling:
    """Level each of *files* to *target* LUFS, in place.

    The same `kudio.normalize_lufs` the conversion applies, for a pass that
    has to come after another one: a file that cannot be levelled -- too short
    for BS.1770 -- is left as it is and listed, as the conversion lists it.
    """
    written: List[Path] = []
    failed: List[Tuple[Path, str]] = []
    for path in map(Path, files):
        try:
            y, sr = kudio.file_load(path, sr=None, mono=False)
            kudio.save_wave(path, kudio.normalize_lufs(y, sr, lufs=target), sr,
                            subtype=subtype)
            written.append(path)
        except Exception as e:                                  # noqa: BLE001
            failed.append((path, f"{type(e).__name__}: {e}"))
    return Levelling(target=float(target), written=tuple(written),
                     failed=tuple(failed))


def check_loudness(files: Sequence[Path], target: float, *,
                   tolerance: float = LOUDNESS_TOLERANCE,
                   ceiling: float = TRUE_PEAK_CEILING,
                   unwritten: Sequence[Tuple[Path, str]] = ()
                   ) -> LoudnessCheck:
    """Read each written file back and say which missed *target*, and why.

    Measured from disk rather than from the arrays that were written, because
    the disk is where it goes wrong: a fixed-point file clips everything past
    full scale, so a quiet file levelled up comes back with its peaks
    flattened and short of the target, and nothing before `save_wave` sees it.

    *unwritten* is the conversion's ``(path, reason)`` failures, of which only
    kudio's refusal to measure a clip shorter than one gating block is a
    loudness finding -- the rest are listed as failures already.
    """
    misses: List[LoudnessMiss] = []
    short = [Path(path) for path, reason in unwritten if _TOO_SHORT in reason]
    for path in short:
        misses.append(LoudnessMiss(
            path, None,
            f"too short to measure — BS.1770 needs "
            f"{MIN_LOUDNESS_SECONDS:g} s, so it was not written"))
    for path in map(Path, files):
        miss = _loudness_miss(path, target, tolerance, ceiling)
        if miss is not None:
            misses.append(miss)
    return LoudnessCheck(target=float(target), tolerance=float(tolerance),
                         ceiling=float(ceiling),
                         checked=len(files) + len(short), misses=tuple(misses))


def _loudness_miss(path: Path, target: float, tolerance: float,
                   ceiling: float) -> Optional[LoudnessMiss]:
    """Why *path* misses the target, or ``None`` when it does not.

    One reason per file, the most basic first: no level at all, then a level
    reached by clipping, then the wrong level, and only then the headroom of
    a file that is right. The true peak is read last because it costs an
    oversample, and a file already listed does not need one.
    """
    try:
        y, sr = kudio.file_load(path, sr=None, mono=False)
    except Exception as e:                                      # noqa: BLE001
        return LoudnessMiss(path, None, f"could not be read back — {e}")
    seconds = len(y) / sr
    if seconds < MIN_LOUDNESS_SECONDS:
        return LoudnessMiss(path, None,
                            f"too short to measure — {seconds:.2f} s, and "
                            f"BS.1770 needs {MIN_LOUDNESS_SECONDS:g} s")
    lufs = kudio.loudness(y, sr)
    if not np.isfinite(lufs):
        return LoudnessMiss(path, None, "silent — there is no level to raise")

    gap = lufs - target
    peak = float(np.max(np.abs(y)))
    if _FULL_SCALE <= peak <= 1.0:
        short_by = f"; {-gap:.1f} LU short" if gap < -tolerance else ""
        return LoudnessMiss(path, lufs,
                            f"clipped at full scale — {target:g} LUFS needs a "
                            f"peak above 0 dBFS{short_by}")
    if abs(gap) > tolerance:
        return LoudnessMiss(path, lufs,
                            f"{abs(gap):.1f} LU {'over' if gap > 0 else 'under'}")
    if peak > 1.0:
        # only a float file holds this; it is the level asked for, and the
        # first fixed-point copy of it clips
        return LoudnessMiss(path, lufs,
                            f"peaks at {20.0 * np.log10(peak):+.1f} dBFS — "
                            f"kept in a float file, clipped in any "
                            f"fixed-point copy", headroom=True)
    true_peak = kudio.true_peak(y, sr)
    if true_peak > ceiling:
        return LoudnessMiss(path, lufs,
                            f"true peak {true_peak:+.1f} dBTP, over the "
                            f"{ceiling:+.1f} dBTP ceiling", headroom=True)
    return None


def run_batch(plan: BatchPlan) -> BatchOutcome:
    """Do everything *plan* asks, in order, and report what came of it.

    Convert; denoise the output in place; level it, when a denoise pass means
    the conversion could not; write the reverberant copies; then read back
    what is on disk. The loudness check comes last so that it measures the
    files as the other passes left them.
    """
    # the loop this used to run by hand now lives in kudio, where the CLI
    # (`kudio convert --recursive`) reaches it too
    converted = kudio.convert_folder(plan.src, plan.dst, **plan.conversion())
    subtype = plan.convert.get("subtype", "PCM_16")
    result = converted
    if plan.denoise is not None and converted.written:
        # a second pass over what the first one wrote, in place: denoising has
        # to read whole files to estimate the noise, so it cannot ride along.
        # The subtype is passed on, or a FLOAT tree comes back 16-bit.
        result = kudio.enhance_folder(
            plan.dst, plan.dst, plan.denoise, noise=plan.noise,
            subtype=subtype, overwrite=True, report=True)

    lufs = plan.lufs
    levelled = None
    if plan.level_last and converted.outputs:
        # every file the conversion wrote, denoised or not: whatever the
        # denoise pass could not do, the tree is still asked to be at -23
        levelled = level_files(converted.outputs, lufs, subtype=subtype)

    rooms = None
    if plan.room is not None and converted.outputs:
        rooms = write_reverberant(converted.outputs, plan.dst, plan.room,
                                  subtype=subtype, lufs=lufs)

    loudness = None
    if lufs is not None:
        wet = [copy for _, copy in rooms.written] if rooms is not None else []
        loudness = check_loudness(
            list(converted.outputs) + wet, lufs,
            tolerance=plan.tolerance, ceiling=plan.ceiling,
            unwritten=converted.failed)
    return BatchOutcome(result=result, converted=converted, rooms=rooms,
                        loudness=loudness, levelled=levelled)


# ---------------------------------------------------------------- the panel

class BatchPanel(Panel):
    """Convert every audio file in a folder."""

    title = "Batch"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)

        self.src = DirPicker("input folder")
        self.dst = DirPicker("output folder")
        self.rate = QComboBox()
        self.rate.addItems(RATES)
        self.subtype = QComboBox()
        self.subtype.addItems(SUBTYPES)
        self.mono = QCheckBox("downmix to mono")
        self.mono.setChecked(True)
        self.trim = QCheckBox("trim leading/trailing silence")
        self.top_db = QDoubleSpinBox()
        self.top_db.setRange(1.0, 90.0)
        self.top_db.setValue(30.0)
        self.top_db.setSuffix(" dB")
        self.top_db.setEnabled(False)

        self.normalize_mode = QComboBox()
        self.normalize_mode.addItems(NORMALIZE_MODES)
        self.normalize_mode.setToolTip(
            "Peak scales by the loudest sample, which says nothing about how\n"
            "loud a file sounds: one stray transient normalises a whole clip\n"
            "to a whisper. LUFS (ITU-R BS.1770) levels by perceived loudness,\n"
            "which is what a dataset of mixed sources actually needs.")
        self.normalize_target = QDoubleSpinBox()
        self.normalize_target.setDecimals(2)
        self.normalize_target.setEnabled(False)

        # what "got there" means, for the read-back after a LUFS run
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.1, 6.0)
        self.tolerance.setSingleStep(0.1)
        self.tolerance.setDecimals(1)
        self.tolerance.setValue(LOUDNESS_TOLERANCE)
        self.tolerance.setPrefix("± ")
        self.tolerance.setSuffix(" LU")
        self.tolerance.setToolTip(
            "How far from the target a file may land and still count as on\n"
            "it. 1 LU is what `kudio loudness --target` allows.")
        self.ceiling = QDoubleSpinBox()
        self.ceiling.setRange(-20.0, 0.0)
        self.ceiling.setSingleStep(0.5)
        self.ceiling.setDecimals(1)
        self.ceiling.setValue(TRUE_PEAK_CEILING)
        self.ceiling.setSuffix(" dBTP")
        self.ceiling.setToolTip(
            "The true-peak ceiling. A quiet file levelled up can be at the\n"
            "target with its waveform overshooting full scale between the\n"
            "samples, and that clips in the next converter or codec it meets.\n"
            "-1 dBTP is EBU R 128's ceiling.")
        self.loudness_check = QWidget()
        check_row = QHBoxLayout(self.loudness_check)
        check_row.setContentsMargins(0, 0, 0, 0)
        check_row.addWidget(QLabel("within"))
        check_row.addWidget(self.tolerance)
        check_row.addWidget(QLabel("true peak ≤"))
        check_row.addWidget(self.ceiling)
        self._sync_normalize_target()

        self.denoise = QComboBox()
        self.denoise.addItem("off", None)
        for name in kudio.METHODS:
            self.denoise.addItem(kudio.METHODS[name].label, name)
        self.denoise.setToolTip(
            "Denoise every file on the way through (kudio.enhance_folder).\n"
            "A separate pass from the conversion, because it reads the whole\n"
            "file to estimate the noise — and because a folder of already-clean\n"
            "audio comes out worse, not better.")
        self.denoise_noise = QComboBox()
        for name, summary in kudio.NOISE_ESTIMATORS.items():
            self.denoise_noise.addItem(name, name)
            self.denoise_noise.setItemData(self.denoise_noise.count() - 1,
                                           summary, Qt.ToolTipRole)
        self.denoise_noise.setEnabled(False)

        self.room_box = QGroupBox("Reverberant copy — keep the dry tree too")
        self.room_box.setCheckable(True)
        self.room_box.setChecked(False)
        self.room_box.setToolTip(
            "Write every converted file again, in a room, to a second tree\n"
            "with the same layout. The dry files stay where they are, so the\n"
            "two pair by path: input and target for dereverberation, or two\n"
            "conditions of one corpus.")
        self.room_rt60 = QDoubleSpinBox()
        self.room_rt60.setRange(0.05, 5.0)
        self.room_rt60.setSingleStep(0.05)
        self.room_rt60.setDecimals(2)
        self.room_rt60.setValue(0.5)
        self.room_rt60.setSuffix(" s")
        self.room_rt60.setToolTip(
            "How long the tail takes to fall 60 dB. A treated studio is under\n"
            "0.3 s, a living room around 0.5, a hall over 1.5.")
        self.room_drr = QDoubleSpinBox()
        self.room_drr.setRange(-20.0, 30.0)
        self.room_drr.setSingleStep(1.0)
        self.room_drr.setDecimals(1)
        self.room_drr.setValue(0.0)
        self.room_drr.setSuffix(" dB")
        self.room_drr.setToolTip(
            "Direct-to-reverberant ratio — distance, in a readable unit.\n"
            "A close microphone is +10 dB or more; a metre or two away is\n"
            "around 0; the far end of a hall is negative.")
        self.room_seed = QSpinBox()
        self.room_seed.setRange(-1, 99999)
        self.room_seed.setValue(0)
        self.room_seed.setSpecialValueText("random")
        self.room_seed.setToolTip(
            "Every file gets a room of its own, drawn from this seed and the\n"
            "file's path, so the same seed rebuilds the same rooms. Random\n"
            "draws one at Run, and the result says which.")
        self.room_dst = DirPicker(f"beside the output: <output>{REVERBERANT_SUFFIX}")

        self.run_button = QPushButton("Run")
        self.call = self.call_label()
        self.result = QLabel("—")
        self.result.setWordWrap(True)
        # every file that missed, not the first five: "tell me which ones" is
        # the job, and the list is plain text so it can be copied out
        self.misses = QPlainTextEdit()
        self.misses.setReadOnly(True)
        self.misses.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.misses.setStyleSheet(f"color: {CALL_COLOR}; font-family: monospace;")
        self.misses.setMaximumHeight(180)
        self.misses.setVisible(False)

        normalize_row = QHBoxLayout()
        normalize_row.addWidget(self.normalize_mode)
        normalize_row.addWidget(self.normalize_target)
        denoise_row = QHBoxLayout()
        denoise_row.addWidget(self.denoise)
        denoise_row.addWidget(self.denoise_noise)

        form = QFormLayout()
        form.addRow("input", self.src)
        form.addRow("output", self.dst)
        form.addRow("sr", self.rate)
        form.addRow("subtype", self.subtype)
        form.addRow("top_db", self.top_db)
        form.addRow("normalize", normalize_row)
        form.addRow("check", self.loudness_check)
        form.addRow("denoise", denoise_row)

        room_form = QFormLayout(self.room_box)
        room_form.addRow("rt60", self.room_rt60)
        room_form.addRow("drr_db", self.room_drr)
        room_form.addRow("seed", self.room_seed)
        room_form.addRow("to", self.room_dst)

        box = QGroupBox("Batch conversion")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        for w in (self.mono, self.trim):
            box_layout.addWidget(w)
        box_layout.addWidget(self.room_box)
        box_layout.addWidget(self.call)
        box_layout.addWidget(self.run_button)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.result)
        layout.addWidget(self.misses)
        layout.addWidget(self.hint(
            "Output mirrors the input's folder structure, so two files with "
            "the same name in different subfolders do not collide. Existing "
            "files are renamed rather than overwritten (kudio.check_file)."))
        layout.addWidget(self.hint(
            "With a LUFS target every written file is read back and measured, "
            "and the ones that did not get there are listed with the reason."))
        layout.addStretch(1)

        self.trim.toggled.connect(self.top_db.setEnabled)
        self.trim.toggled.connect(self._update_call)
        self.run_button.clicked.connect(self._run)
        self.jobs.busy_changed.connect(self._set_busy)
        for w in (self.rate, self.subtype):
            w.currentIndexChanged.connect(self._update_call)
        self.normalize_mode.currentIndexChanged.connect(self._on_normalize_changed)
        self.denoise.currentIndexChanged.connect(self._on_denoise_changed)
        self.denoise_noise.currentIndexChanged.connect(self._update_call)
        for w in (self.normalize_target, self.tolerance, self.ceiling,
                  self.room_rt60, self.room_drr, self.room_seed):
            w.valueChanged.connect(self._update_call)
        self.mono.toggled.connect(self._update_call)
        self.room_box.toggled.connect(self._update_call)
        # the caption names the output, and the copies' default folder with it
        for picker in (self.src, self.dst, self.room_dst):
            picker.edit.textChanged.connect(self._update_call)
        self._update_call()

    # ------------------------------------------------------------------- ui

    def _target_rate(self) -> Optional[int]:
        text = self.rate.currentText()
        return None if text == "keep" else int(text)

    def _sync_normalize_target(self) -> None:
        mode = self.normalize_mode.currentText()
        spec = _NORMALIZE_RANGES.get(mode)
        self.normalize_target.setEnabled(spec is not None)
        # the read-back only has a target to hold the files to in LUFS
        self.loudness_check.setEnabled(mode == "LUFS")
        if spec is None:
            self.normalize_target.setSuffix("")
            return
        (low, high), default, suffix = spec
        self.normalize_target.blockSignals(True)
        self.normalize_target.setRange(low, high)
        self.normalize_target.setSuffix(suffix)
        self.normalize_target.setValue(default)
        self.normalize_target.blockSignals(False)

    def _on_normalize_changed(self) -> None:
        self._sync_normalize_target()
        self._update_call()

    def _normalize_kwargs(self) -> dict:
        mode = self.normalize_mode.currentText()
        if mode == "peak":
            return {"peak": float(self.normalize_target.value())}
        if mode == "LUFS":
            return {"lufs": float(self.normalize_target.value())}
        return {}

    def _convert_kwargs(self) -> dict:
        kwargs = {
            "sr": self._target_rate(),
            "mono": self.mono.isChecked(),
            "subtype": self.subtype.currentText(),
            "trim_db": self.top_db.value() if self.trim.isChecked() else None,
        }
        kwargs.update(self._normalize_kwargs())
        return kwargs

    def _room_plan(self, pin: bool = False) -> Optional[RoomPlan]:
        """The reverberant pass as set, or ``None`` when it is off.

        "Random" is a ``None`` seed -- or, with *pin*, a fresh draw, which is
        what Run does so that the result can name the seed it used.
        """
        if not self.room_box.isChecked():
            return None
        seed: Optional[int] = self.room_seed.value()
        if seed < 0:
            seed = int(np.random.default_rng().integers(2 ** 31)) if pin else None
        folder = self.room_dst.path()
        if not folder:
            dst = self.dst.path()
            folder = (str(reverberant_folder(dst)) if dst
                      else f"<output>{REVERBERANT_SUFFIX}")
        return RoomPlan(rt60=float(self.room_rt60.value()),
                        drr_db=float(self.room_drr.value()),
                        seed=seed, folder=Path(folder))

    def _plan(self) -> BatchPlan:
        """Everything the job needs, read now, with a random seed drawn."""
        return BatchPlan(src=self.src.path(), dst=self.dst.path(),
                         convert=self._convert_kwargs(),
                         denoise=self.denoise.currentData(),
                         noise=self.denoise_noise.currentData(),
                         room=self._room_plan(pin=True),
                         tolerance=float(self.tolerance.value()),
                         ceiling=float(self.ceiling.value()))

    def _on_denoise_changed(self) -> None:
        self.denoise_noise.setEnabled(self.denoise.currentData() is not None)
        self._update_call()

    def _update_call(self) -> None:
        plan = BatchPlan(src=self.src.path(), dst=self.dst.path(),
                         convert=self._convert_kwargs(),
                         denoise=self.denoise.currentData())
        convert = plan.conversion()
        arguments = ", ".join(f"{k}={v!r}" for k, v in convert.items())
        lines = [f"kudio.convert_folder({self.src.path()!r}, "
                 f"{self.dst.path()!r}, {arguments})"]
        method = plan.denoise
        if method is not None:
            # two passes, and the caption says so -- the denoise reads whole
            # files to estimate the noise, which the conversion does not
            lines.append(f"→ kudio.enhance_folder(out, out, {method!r}, "
                         f"noise={self.denoise_noise.currentData()!r}, "
                         f"subtype={convert['subtype']!r}, "
                         f"overwrite=True, report=True)")
        lufs = plan.lufs
        if plan.level_last:
            # levelled after the denoise, which takes level out with the noise
            lines.append(f"→ per file: kudio.normalize_lufs(y, sr, "
                         f"lufs={lufs}), after the denoise")
        room = self._room_plan()
        if room is not None:
            seed = "…" if room.seed is None else room.seed
            line = (f"→ per file: kudio.apply_rir(y, kudio.rir(sr, "
                    f"rt60={room.rt60}, drr_db={room.drr_db}, "
                    f"seed=file_seed({seed}, path)))")
            if lufs is not None:
                line += f", kudio.normalize_lufs(…, lufs={lufs})"
            lines.append(f"{line} → {str(room.folder)!r}")
        if lufs is not None:
            lines.append(f"→ read back: kudio.loudness, kudio.true_peak — "
                         f"within ±{self.tolerance.value():g} LU of "
                         f"{lufs:g} LUFS, true peak ≤ "
                         f"{self.ceiling.value():g} dBTP")
        self.call.setText("\n".join(lines))

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("Running…" if busy else "Run")

    # ------------------------------------------------------------------ run

    def _run(self) -> None:
        if not self.src.path() or not Path(self.src.path()).is_dir():
            self.report("Select a valid input folder.")
            return
        if not self.dst.path():
            self.report("Select an output folder.")
            return
        plan = self._plan()
        if plan.room is not None and trees_overlap(plan.room.folder, plan.dst):
            self.report("The reverberant copies need a folder of their own — "
                        "not the output folder, nor one inside it or around "
                        "it.")
            return
        self.report("Converting …")
        started = self.jobs.run(run_batch, plan, on_done=self._finished,
                                on_failed=self._failed)
        if not started:
            self.report("A batch job is already running.")

    def _convert(self):
        """Run the whole batch on the calling thread and return what the
        conversion -- or the denoise pass after it -- reported.

        :meth:`_run` does the same on a worker; this is the form a test or a
        script can call and wait for. :func:`run_batch` on :meth:`_plan`
        returns the rest.
        """
        return run_batch(self._plan()).result

    def _finished(self, outcome: BatchOutcome) -> None:
        result = outcome.result
        text = (f"{result.written}/{result.total} file(s) written to "
                f"{self.dst.path()}")
        reduction = getattr(result, "mean_reduction_db", lambda: None)()
        if reduction is not None:
            text += (f"\nNoise floor down {reduction:.1f} dB on average — "
                     f"a measurement of what was removed, not of whether it "
                     f"sounds better. Open one and listen.")
        if outcome.rooms is not None:
            text += "\n\n" + self._describe_rooms(outcome.rooms)

        listed: set = set()
        check = outcome.loudness
        if outcome.levelled is not None:
            text += (f"\nLevelled to {outcome.levelled.target:g} LUFS after "
                     f"the denoise pass, which takes level out with the "
                     f"noise.")
        if check is not None:
            text += "\n\n" + self._describe_loudness(check)
            listed = {miss.path for miss in check.misses}
        self._list_misses(check)

        # a file the loudness report already lists, with its reason in plain
        # words, is not listed a second time in kudio's
        failed = [(path, reason) for path, reason in outcome.failed
                  if Path(path) not in listed]
        if failed:
            lines = [f"{Path(path).name}: {reason}" for path, reason in failed[:5]]
            more = (f"\n… and {len(failed) - 5} more"
                    if len(failed) > 5 else "")
            text += f"\n\n{len(failed)} failed:\n" + "\n".join(lines) + more
        self.result.setText(text)
        message = (f"Batch complete — {result.written}/{result.total} written, "
                   f"{len(outcome.failed)} failed")
        if check is not None and check.misses:
            message += f", {len(check.misses)} missed the loudness target"
        self.report(message + ".")

    def _describe_rooms(self, rooms: RoomCopies) -> str:
        made = len(rooms.written)
        asked = made + len(rooms.failed)
        return (f"Reverberant copies: {made}/{asked} written to {rooms.folder} "
                f"— each file its own room, from seed {rooms.seed}; the same "
                f"seed makes the same rooms again.")

    @staticmethod
    def _describe_loudness(check: LoudnessCheck) -> str:
        holds = (f"within ±{check.tolerance:g} LU of {check.target:g} LUFS, "
                 f"true peak at or under {check.ceiling:g} dBTP")
        if not check.misses:
            return (f"Loudness, read back from disk: all {check.checked} "
                    f"file(s) {holds}.")
        parts = []
        if check.off_target:
            parts.append(f"{len(check.off_target)} could not get there")
        if check.over_ceiling:
            parts.append(f"{len(check.over_ceiling)} got there with too "
                         f"little headroom")
        return (f"Loudness, read back from disk: {check.held} of "
                f"{check.checked} file(s) {holds}. "
                f"{' and '.join(parts)} — listed below.")

    def _list_misses(self, check: Optional[LoudnessCheck]) -> None:
        """Every file that missed, one line each, grouped by how it missed."""
        if check is None or not check.misses:
            self.misses.clear()
            self.misses.setVisible(False)
            return
        names = {miss.path: self._shown(miss.path) for miss in check.misses}
        width = max(len(name) for name in names.values())
        lines: List[str] = []
        for heading, group in (("could not get there", check.off_target),
                               ("on target, too little headroom",
                                check.over_ceiling)):
            if not group:
                continue
            lines.append(f"{heading}:")
            for miss in group:
                level = "—" if miss.lufs is None else f"{miss.lufs:+.1f} LUFS"
                lines.append(f"  {names[miss.path]:<{width}}  {level:>11}  "
                             f"{miss.reason}")
        self.misses.setPlainText("\n".join(lines))
        self.misses.setVisible(True)

    def _shown(self, path: Path) -> str:
        """*path* as the list shows it: from the folder that holds the output.

        That folder holds the output tree and, by default, the reverberant one
        beside it, so the two read as ``out/…`` and ``out_reverberant/…``. A
        source file the conversion refused is shown from above the input.
        """
        for folder in (self.dst.path(), self.src.path()):
            if not folder:
                continue
            try:
                return Path(path).relative_to(Path(folder).parent).as_posix()
            except ValueError:
                continue
        return str(path)

    def _failed(self, message: str) -> None:
        self.result.setText("—")
        self._list_misses(None)
        self.report(f"Batch failed — {message}")
