# -*- coding: utf-8 -*-
"""Folder-wide conversion -- the `kudio` command line.

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
  would land under the target it was asked for.
* **A reverberant copy.** "Re-room this tree and keep both": each written file
  is put in a room of its own (`kudio.rir`, `kudio.apply_rir`) and written to
  a second tree with the same layout, so a dry file and its wet copy pair by
  path.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import kudio

SUBTYPES = ("PCM_16", "PCM_24", "PCM_32", "FLOAT")
RATES = ("keep", "48000", "44100", "32000", "22050", "16000", "8000")
NORMALIZE_MODES = ("none", "peak", "LUFS")
#: (range, default, suffix) per mode -- a peak of -23 or a loudness of 0.99
#: are both nonsense, so the box changes with the choice
NORMALIZE_RANGES = {
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
    """Everything one Run needs, read from the form before the job starts.

    The job runs on a thread of its own, and one that read the controls as it
    went would read them while they can still be changed: a value edited
    mid-run would apply to half the folder.
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
    denoise pass's when there was one. *converted* is the conversion's own,
    kept because the denoise pass only sees the files that were written and
    its result would lose the ones that were not.
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
    # the loop lives in kudio, where the CLI (`kudio convert --recursive`)
    # reaches it too
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


# ------------------------------------------------------------ the settings

NORMALIZE_TIP = ("Peak scales by the loudest sample, which says nothing about how\n"
                 "loud a file sounds: one stray transient normalises a whole clip\n"
                 "to a whisper. LUFS (ITU-R BS.1770) levels by perceived loudness,\n"
                 "which is what a dataset of mixed sources actually needs.")
TOLERANCE_TIP = ("How far from the target a file may land and still count as on\n"
                 "it. 1 LU is what `kudio loudness --target` allows.")
CEILING_TIP = ("The true-peak ceiling. A quiet file levelled up can be at the\n"
               "target with its waveform overshooting full scale between the\n"
               "samples, and that clips in the next converter or codec it meets.\n"
               "-1 dBTP is EBU R 128's ceiling.")
DENOISE_TIP = ("Denoise every file on the way through (kudio.enhance_folder).\n"
               "A separate pass from the conversion, because it reads the whole\n"
               "file to estimate the noise — and because a folder of already-clean\n"
               "audio comes out worse, not better.")
ROOM_TIP = ("Write every converted file again, in a room, to a second tree\n"
            "with the same layout. The dry files stay where they are, so the\n"
            "two pair by path: input and target for dereverberation, or two\n"
            "conditions of one corpus.")
RT60_TIP = ("How long the tail takes to fall 60 dB. A treated studio is under\n"
            "0.3 s, a living room around 0.5, a hall over 1.5.")
DRR_TIP = ("Direct-to-reverberant ratio — distance, in a readable unit.\n"
           "A close microphone is +10 dB or more; a metre or two away is\n"
           "around 0; the far end of a hall is negative.")
SEED_TIP = ("Every file gets a room of its own, drawn from this seed and the\n"
            "file's path, so the same seed rebuilds the same rooms. Random\n"
            "draws one at Run, and the result says which.")
LAYOUT_HINT = ("Output mirrors the input's folder structure, so two files with the same "
               "name in different subfolders do not collide. Existing files are renamed "
               "rather than overwritten (kudio.check_file).")
LOUDNESS_HINT = ("With a LUFS target every written file is read back and measured, and "
                 "the ones that did not get there are listed with the reason.")
OVERLAP = ("The reverberant copies need a folder of their own — not the output folder, "
           "nor one inside it or around it.")


def convert_kwargs(rate: str, mono: bool, subtype: str, trim: bool, top_db: float,
                   normalize: str, target: float) -> Dict[str, Any]:
    """The keyword arguments for `kudio.convert_folder`, from the form."""
    kwargs: Dict[str, Any] = {
        "sr": None if rate == "keep" else int(rate),
        "mono": bool(mono),
        "subtype": subtype,
        "trim_db": top_db if trim else None,
    }
    if normalize == "peak":
        kwargs["peak"] = float(target)
    elif normalize == "LUFS":
        kwargs["lufs"] = float(target)
    return kwargs


def room_plan(rt60: float, drr_db: float, seed: int, folder: str, dst: str,
              pin: bool = False) -> RoomPlan:
    """The reverberant pass as set.

    A negative seed is "random": a ``None`` seed -- or, with *pin*, a fresh
    draw, which is what Run does so that the result can name the seed it used.
    With no folder named, the copies go beside the output.
    """
    chosen: Optional[int] = int(seed)
    if chosen < 0:
        chosen = int(np.random.default_rng().integers(2 ** 31)) if pin else None
    if not folder:
        folder = (str(reverberant_folder(dst)) if dst
                  else f"<output>{REVERBERANT_SUFFIX}")
    return RoomPlan(rt60=float(rt60), drr_db=float(drr_db), seed=chosen,
                    folder=Path(folder))


def call_text(src: str, dst: str, convert: Dict[str, Any], denoise: Optional[str],
              noise: Optional[str], room: Optional[RoomPlan], tolerance: float,
              ceiling: float) -> str:
    """What a Run does, one pass a line."""
    plan = BatchPlan(src=src, dst=dst, convert=convert, denoise=denoise)
    conversion = plan.conversion()
    arguments = ", ".join(f"{k}={v!r}" for k, v in conversion.items())
    lines = [f"kudio.convert_folder({src!r}, {dst!r}, {arguments})"]
    if denoise is not None:
        # two passes, and the caption says so -- the denoise reads whole
        # files to estimate the noise, which the conversion does not
        lines.append(f"→ kudio.enhance_folder(out, out, {denoise!r}, "
                     f"noise={noise!r}, subtype={conversion['subtype']!r}, "
                     f"overwrite=True, report=True)")
    lufs = plan.lufs
    if plan.level_last:
        # levelled after the denoise, which takes level out with the noise
        lines.append(f"→ per file: kudio.normalize_lufs(y, sr, lufs={lufs}), "
                     f"after the denoise")
    if room is not None:
        seed = "…" if room.seed is None else room.seed
        line = (f"→ per file: kudio.apply_rir(y, kudio.rir(sr, rt60={room.rt60}, "
                f"drr_db={room.drr_db}, seed=file_seed({seed}, path)))")
        if lufs is not None:
            line += f", kudio.normalize_lufs(…, lufs={lufs})"
        lines.append(f"{line} → {str(room.folder)!r}")
    if lufs is not None:
        lines.append(f"→ read back: kudio.loudness, kudio.true_peak — within "
                     f"±{tolerance:g} LU of {lufs:g} LUFS, true peak ≤ {ceiling:g} dBTP")
    return "\n".join(lines)


def problem(src: str, dst: str) -> Optional[str]:
    """What stops a Run from starting, in the words the panel says it, or None."""
    if not src or not Path(src).is_dir():
        return "Select a valid input folder."
    if not dst:
        return "Select an output folder."
    return None


# --------------------------------------------------------------- the result

def describe_rooms(rooms: RoomCopies) -> str:
    made = len(rooms.written)
    asked = made + len(rooms.failed)
    return (f"Reverberant copies: {made}/{asked} written to {rooms.folder} — each file "
            f"its own room, from seed {rooms.seed}; the same seed makes the same rooms "
            f"again.")


def describe_loudness(check: LoudnessCheck) -> str:
    holds = (f"within ±{check.tolerance:g} LU of {check.target:g} LUFS, "
             f"true peak at or under {check.ceiling:g} dBTP")
    if not check.misses:
        return f"Loudness, read back from disk: all {check.checked} file(s) {holds}."
    parts = []
    if check.off_target:
        parts.append(f"{len(check.off_target)} could not get there")
    if check.over_ceiling:
        parts.append(f"{len(check.over_ceiling)} got there with too little headroom")
    return (f"Loudness, read back from disk: {check.held} of {check.checked} file(s) "
            f"{holds}. {' and '.join(parts)} — listed below.")


def shown_path(path, dst: str, src: str) -> str:
    """*path* as the list shows it: from the folder that holds the output.

    That folder holds the output tree and, by default, the reverberant one
    beside it, so the two read as ``out/…`` and ``out_reverberant/…``. A
    source file the conversion refused is shown from above the input.
    """
    for folder in (dst, src):
        if not folder:
            continue
        try:
            return Path(path).relative_to(Path(folder).parent).as_posix()
        except ValueError:
            continue
    return str(path)


def misses_text(check: Optional[LoudnessCheck], dst: str, src: str) -> str:
    """Every file that missed, one line each, grouped by how it missed; ""
    when none did. Plain text, so the list can be copied out."""
    if check is None or not check.misses:
        return ""
    names = {miss.path: shown_path(miss.path, dst, src) for miss in check.misses}
    width = max(len(name) for name in names.values())
    lines: List[str] = []
    for heading, group in (("could not get there", check.off_target),
                           ("on target, too little headroom", check.over_ceiling)):
        if not group:
            continue
        lines.append(f"{heading}:")
        for miss in group:
            level = "—" if miss.lufs is None else f"{miss.lufs:+.1f} LUFS"
            lines.append(f"  {names[miss.path]:<{width}}  {level:>11}  {miss.reason}")
    return "\n".join(lines)


def result_lines(outcome: BatchOutcome, dst: str) -> List[str]:
    """The result, a sentence a line, "" for a blank line between parts.
    Kept apart so a translation can take one sentence at a time."""
    result = outcome.result
    lines = [f"{result.written}/{result.total} file(s) written to {dst}"]
    reduction = getattr(result, "mean_reduction_db", lambda: None)()
    if reduction is not None:
        lines.append(f"Noise floor down {reduction:.1f} dB on average — a measurement of "
                     f"what was removed, not of whether it sounds better. Open one and "
                     f"listen.")
    if outcome.rooms is not None:
        lines += ["", describe_rooms(outcome.rooms)]
    if outcome.levelled is not None:
        lines.append(f"Levelled to {outcome.levelled.target:g} LUFS after the denoise "
                     f"pass, which takes level out with the noise.")
    check = outcome.loudness
    listed: set = set()
    if check is not None:
        lines += ["", describe_loudness(check)]
        listed = {miss.path for miss in check.misses}
    # a file the loudness report already lists, with its reason in plain
    # words, is not listed a second time in kudio's
    failed = [(path, reason) for path, reason in outcome.failed
              if Path(path) not in listed]
    if failed:
        lines += ["", f"{len(failed)} failed:"]
        lines += [f"{Path(path).name}: {reason}" for path, reason in failed[:5]]
        if len(failed) > 5:
            lines.append(f"… and {len(failed) - 5} more")
    return lines


def status_text(outcome: BatchOutcome) -> str:
    """The status line once a Run is done."""
    result, check = outcome.result, outcome.loudness
    message = (f"Batch complete — {result.written}/{result.total} written, "
               f"{len(outcome.failed)} failed")
    if check is not None and check.misses:
        message += f", {len(check.misses)} missed the loudness target"
    return message + "."
