# -*- coding: utf-8 -*-
"""A whole folder's health at once: `kudio.audio_report` on every file.

The Metrics panel inspects the clip that is open. A corpus of a few
thousand takes needs the same check on each, laid out as a table that sorts
and filters, so that the clipped ones, the band-limited ones or the ones
with a hum can be picked out and opened one by one. Three columns go past
what `audio_report` says:

* **Lines.** `kudio.tonal_lines`: how many narrowband lines stand 20 dB
  above their surroundings, and the comb they make, if any. Not a finding of
  its own, since a held note is a comb as much as a hum is.
* **Voice.** The share of the take a voice detector calls speech, beside
  the same share on a copy of the take with its phases scrambled. The
  detector is `kudio.speech_ratio`, judged on 100-4000 Hz where the kudio
  installed can (its ``vad`` takes ``band``), or Silero VAD where the
  silero-vad package is installed.
  The copy keeps the take's long-term spectrum and loses how it rises and
  falls, so a share well above the copy's says the take rises and falls
  more than a steady sound of that spectrum. Speech does; so do knocks, a
  machine that cycles and noise switched on and off. It picks takes out to
  listen to, and is not a finding of a voice.
* **Join.** For one recording cut into files: whether a take carries on
  from the one before it in the same folder. The step from the last sample
  of the one to the first of the next, against the steps inside the end of
  the first, and whether the next one opens by repeating the end of the
  first. A clean cut carries on near x1; a jump or a repeat means samples
  were lost or written twice. Takes recorded one at a time do not join, so
  the check is off unless asked for.

Two views go across the files rather than down the table. The folder
spectra average each folder's takes, so that two classes of recording, or
two sites, can be laid over each other or one taken from the other. The
timeline puts the takes at the time their names give, or the time the
files were written, with one value per take and the gaps between them: a
recorder that stopped, or a setting that changed every few hundred takes,
shows there and nowhere in the table.

Nothing here touches the files.
"""
from __future__ import annotations

import calendar
import csv
import importlib.util
import inspect
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

import kudio

log = logging.getLogger(__name__)

#: clip_run choices: three is audio_report's own; two and one match tools
#: that count shorter runs, or every sample at full scale
CLIP_RUNS = (3, 2, 1)

#: the band the voice share is judged on, where speech is
VOICE_BAND = (100.0, 4000.0)

#: what the table can be narrowed to, and the test for each. "rises and
#: falls" is the voice share standing over its scrambled copy's: speech does
#: that, and so does anything else that comes and goes
FILTERS = ("any finding", "clipped", "band-limited", "coarse grid", "narrowband lines",
           "rises and falls", "seam")

#: a voice share stands out this far above the scrambled copy's
VOICE_MARGIN = 0.10

#: the voice detectors: kudio's own, and Silero VAD when the silero-vad
#: package is there (MIT; it brings PyTorch, so it is never required)
DETECTORS = ("kudio", "silero")
#: Silero VAD's rate and window: it scores 512 samples at 16 kHz at a time
SILERO_RATE, SILERO_WINDOW = 16000, 512
#: the RMS the band is brought to before Silero hears it; voices recorded
#: 40-60 dB under full scale are otherwise too quiet for the model to call
SILERO_LEVEL = 0.05
#: a window counts as speech from this probability up
SILERO_THRESHOLD = 0.5

#: the step into the next file counts as a seam this many times the median
#: step inside the end of the one before
SEAM_RATIO = 20.0
#: the Welch segment of the folder spectra: 4096 samples is 47 Hz a bin at
#: 192 kHz and 3.9 Hz at 16 kHz, and a take of a second still gives dozens
#: of segments to average
SPECTRUM_SIZE = 4096

#: a time in a file name: 20261111_143111, 2026-11-11T14:31:11 or
#: 2026-11-11 14.31.11, each with an optional .123 for the milliseconds.
#: Exactly three digits, so that a take counter after the time
#: (_143111_0001) is not read as a fraction of a second
TIME_PATTERN = (r"(\d{4})-?(\d{2})-?(\d{2})[ T_-]?(\d{2})[:.-]?(\d{2})[:.-]?(\d{2})"
                r"(?:[.,_-](\d{3})(?!\d))?")

#: what the timeline can show for each take, and its unit
TIMELINE_METRICS = (("RMS", "dBFS"), ("Peak", "dBFS"), ("Band level", "dBFS"),
                    ("Lines", ""), ("Comb", "Hz"), ("Voice", "%"), ("Join", "×"))

#: the samples compared for a repeat: 64 at 192 kHz is a third of a
#: millisecond, and noise does not repeat itself over that by chance
REPEAT_SAMPLES = 64

COLUMNS = ("File", "Rate", "Seconds", "Peak", "Clipped", "Full scale", "Grid bits",
           "Band limit", "Lines", "Comb", "Voice", "Join", "Findings")


@dataclass
class SurveyRow:
    """One file's line of the table."""

    path: str
    folder: str
    sr: int = 0
    seconds: float = 0.0
    peak_dbfs: float = float("nan")
    rms_dbfs: float = float("nan")
    #: when the file was last written, seconds since the epoch
    modified: float = float("nan")
    clipped: int = 0
    full_scale: int = -1
    grid_bits: float = float("nan")
    band_limited: bool = False
    band_edge_hz: float = float("nan")
    source_rates: Tuple[int, ...] = ()
    lines: int = -1
    strongest_hz: float = float("nan")
    comb_hz: float = float("nan")
    voice: float = float("nan")
    voice_control: float = float("nan")
    #: the step into this file over the median step at the end of the one
    #: before; nan for the first of a folder
    join_ratio: float = float("nan")
    repeats: bool = False
    problems: List[str] = field(default_factory=list)
    error: str = ""
    #: power per Hz in each bin of a SPECTRUM_SIZE Welch spectrum, for the
    #: folder spectra and the timeline's band level; None for a take
    #: shorter than one segment
    spectrum: Optional[np.ndarray] = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def seam(self) -> bool:
        return self.repeats or (math.isfinite(self.join_ratio) and self.join_ratio > SEAM_RATIO)

    @property
    def rises_and_falls(self) -> bool:
        """The voice share stands more than VOICE_MARGIN over its copy's."""
        return (math.isfinite(self.voice) and math.isfinite(self.voice_control)
                and self.voice > self.voice_control + VOICE_MARGIN)


def _vad_takes_band() -> bool:
    return "band" in inspect.signature(kudio.vad).parameters


def scrambled(y: np.ndarray, seed: int = 0) -> np.ndarray:
    """*y* with the phase of every bin drawn at random: the same spectrum,
    and nothing that rises and falls the way speech does."""
    spectrum = np.fft.rfft(y)
    phase = np.random.default_rng(seed).uniform(0, 2 * np.pi, spectrum.size)
    # DC, and Nyquist for an even length, are real bins; a phase there would
    # change their size
    phase[0] = 0.0
    if len(y) % 2 == 0:
        phase[-1] = 0.0
    out = np.fft.irfft(np.abs(spectrum) * np.exp(1j * phase), len(y))
    return out.astype(np.float32)


def silero_available() -> bool:
    """Whether the silero-vad package is installed; looked up, not imported,
    since importing it imports PyTorch."""
    return importlib.util.find_spec("silero_vad") is not None


def detectors() -> List[str]:
    """The voice detectors this installation has."""
    return ["kudio"] + (["silero"] if silero_available() else [])


@lru_cache(maxsize=1)
def _silero_model():
    from silero_vad import load_silero_vad
    return load_silero_vad(onnx=False)


def silero_ratio(y: np.ndarray, sr: int) -> float:
    """The share of *y* Silero VAD calls speech: *y* brought to 16 kHz,
    band-passed to VOICE_BAND, scaled to an RMS of SILERO_LEVEL, and scored
    one 512-sample window after another, from a fresh model state at the
    start of the take. nan for less than one window."""
    import torch
    from scipy.signal import butter, resample_poly, sosfiltfilt

    common = math.gcd(int(sr), SILERO_RATE)
    x = resample_poly(np.asarray(y, dtype=np.float64), SILERO_RATE // common,
                      int(sr) // common)
    if len(x) < SILERO_WINDOW:
        return math.nan
    x = sosfiltfilt(butter(4, VOICE_BAND, "bandpass", fs=SILERO_RATE, output="sos"), x)
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms == 0.0:
        return 0.0
    x = (x * (SILERO_LEVEL / rms)).astype(np.float32)
    model = _silero_model()
    model.reset_states()
    with torch.no_grad():
        scores = [float(model(torch.from_numpy(x[i:i + SILERO_WINDOW]), SILERO_RATE))
                  for i in range(0, len(x) - SILERO_WINDOW + 1, SILERO_WINDOW)]
    return float(np.mean(np.array(scores) >= SILERO_THRESHOLD))


def voice_share(y: np.ndarray, sr: int, detector: str = "kudio") -> Tuple[float, float]:
    """The share of *y* called speech by *detector*, and of its scrambled
    copy."""
    if detector == "silero":
        return silero_ratio(y, sr), silero_ratio(scrambled(y), sr)
    if detector != "kudio":
        raise ValueError(f"no voice detector {detector!r}")
    kwargs = {"band": VOICE_BAND} if _vad_takes_band() and sr / 2 > VOICE_BAND[0] else {}
    return (kudio.speech_ratio(y, sr, **kwargs),
            kudio.speech_ratio(scrambled(y), sr, **kwargs))


def survey_file(path: str, *, clip_run: int = 3, lines: bool = True, voice: bool = False,
                detector: str = "kudio") -> Tuple[SurveyRow, Optional[np.ndarray]]:
    """The row for *path*, and its samples for the join with the next file;
    a row with *error* set when the file will not load."""
    row = SurveyRow(path=str(path), folder=str(Path(path).parent))
    try:
        row.modified = os.stat(path).st_mtime
        y, sr = kudio.file_load(str(path), sr=None, mono=True)
    except Exception as e:  # noqa: BLE001 - one bad file is a row, not a stop
        row.error = f"{type(e).__name__}: {e}"
        return row, None
    y = np.asarray(y, dtype=np.float32)
    row.sr, row.seconds = int(sr), len(y) / sr
    kwargs = {"clip_run": clip_run} if "clip_run" in inspect.signature(
        kudio.audio_report).parameters else {}
    report = kudio.audio_report(y, sr, **kwargs)
    row.peak_dbfs = report.peak_dbfs
    row.rms_dbfs = report.rms_dbfs
    row.clipped = int(report.clipped_samples)
    row.full_scale = int(getattr(report, "full_scale_samples", -1))
    row.grid_bits = float(getattr(report, "grid_bits", float("nan")))
    row.band_limited = bool(report.band_limited)
    row.band_edge_hz = float(getattr(report, "band_edge_hz", float("nan")))
    row.source_rates = tuple(getattr(report, "source_rates", ()))
    row.problems = list(report.problems())
    finite = not report.non_finite_samples
    if lines and finite and hasattr(kudio, "tonal_lines"):
        found = kudio.tonal_lines(y, sr)
        row.lines = len(found.lines)
        if found.strongest is not None:
            row.strongest_hz = found.strongest.hz
        if found.combs:
            row.comb_hz = found.combs[0].fundamental_hz
    if voice and finite and len(y) >= sr // 10:
        row.voice, row.voice_control = voice_share(y, sr, detector)
    if finite:
        row.spectrum = take_spectrum(y, sr)
    return row, y


def take_spectrum(y: np.ndarray, sr: int) -> Optional[np.ndarray]:
    """Power per Hz in each bin of a Welch spectrum of *y*, Hann windows of
    SPECTRUM_SIZE samples overlapping by half; None when *y* is shorter
    than one. Per Hz, so that folders at two rates sit at the same height
    for the same sound."""
    from scipy.signal import welch

    if len(y) < SPECTRUM_SIZE:
        return None
    _, power = welch(np.asarray(y, dtype=np.float64), sr, window="hann",
                     nperseg=SPECTRUM_SIZE, noverlap=SPECTRUM_SIZE // 2)
    return power.astype(np.float32)


def band_level(row: SurveyRow, low: float, high: float) -> float:
    """The level of *row* in *low*..*high* Hz, in dB re full scale: the
    mean square the band holds, from the take's spectrum, on the same
    scale as its RMS level. *high* 0 is Nyquist; nan without a spectrum
    or with no bin in the band."""
    if row.spectrum is None:
        return math.nan
    freqs = np.fft.rfftfreq(SPECTRUM_SIZE, 1.0 / row.sr)
    high = row.sr / 2.0 if not high else high
    inside = (freqs >= low) & (freqs <= high)
    if not inside.any():
        return math.nan
    power = float(np.sum(row.spectrum[inside], dtype=np.float64)) * row.sr / SPECTRUM_SIZE
    return 10.0 * math.log10(power) if power > 0 else -math.inf


#: how much of the end of a take a repeat is looked for in
REPEAT_WINDOW = 4 * 1024 * 1024


def join(previous: np.ndarray, following: np.ndarray) -> Tuple[float, bool]:
    """``(ratio, repeats)``: the step from the end of *previous* to the start
    of *following* over the median step in the last 1024 samples of
    *previous*, and whether *following* opens with samples the last
    REPEAT_WINDOW samples of *previous* already hold. The ratio is nan when
    more than half the steps in that tail are zero, as in digital silence or
    a quiet ending on a coarse grid: the median step is zero then, and any
    step out of it would read as infinite."""
    tail = np.asarray(previous[-1024:], dtype=np.float64)
    steps = np.abs(np.diff(tail))
    typical = float(np.median(steps)) if steps.size else 0.0
    jump = abs(float(following[0]) - float(previous[-1]))
    ratio = jump / typical if typical > 0 else float("nan")
    head = np.asarray(following[:REPEAT_SAMPLES])
    repeats = False
    if len(head) == REPEAT_SAMPLES and np.any(head != head[0]):
        window = np.asarray(previous[-min(len(previous), REPEAT_WINDOW):])
        # every start where the first sample matches, narrowed sample by
        # sample: exact, and never more than one pass over the candidates
        found = np.flatnonzero(window[:len(window) - REPEAT_SAMPLES + 1] == head[0])
        for k in range(1, REPEAT_SAMPLES):
            if not found.size:
                break
            found = found[window[found + k] == head[k]]
        repeats = bool(found.size)
    return ratio, repeats


def run_survey(files: Sequence[str], *, clip_run: int = 3, lines: bool = True,
               voice: bool = False, detector: str = "kudio", seams: bool = False,
               progress: Optional[Callable[[int, int, str], None]] = None,
               stop: Optional[Callable[[], bool]] = None) -> List[SurveyRow]:
    """Every file of *files*, in order. The join is taken between neighbours
    in the same folder at the same rate."""
    rows: List[SurveyRow] = []
    last: Optional[Tuple[str, int, np.ndarray]] = None
    for i, path in enumerate(files):
        if stop is not None and stop():
            break
        if progress is not None:
            progress(i, len(files), Path(path).name)
        row, y = survey_file(path, clip_run=clip_run, lines=lines, voice=voice,
                             detector=detector)
        if seams and y is not None and len(y):
            if last is not None and last[0] == row.folder and last[1] == row.sr:
                row.join_ratio, row.repeats = join(last[2], y)
            # the end of this file is all the next one needs
            last = (row.folder, row.sr, y[-4 * 1024 * 1024:])
        elif y is None:
            last = None
        rows.append(row)
    return rows


def matches(row: SurveyRow, which: str) -> bool:
    """Whether *row* belongs under the filter *which*."""
    if which == "any finding":
        return bool(row.problems or row.error or row.seam)
    if which == "clipped":
        return row.clipped > 0
    if which == "band-limited":
        return row.band_limited
    if which == "coarse grid":
        return math.isfinite(row.grid_bits) and row.grid_bits < 15.95
    if which == "narrowband lines":
        return row.lines > 0
    if which == "rises and falls":
        return row.rises_and_falls
    if which == "seam":
        return row.seam
    raise ValueError(f"no filter {which!r}")


def _sort_value(row: SurveyRow, column: int) -> float:
    """What column *column* of the table sorts *row* by; nan when the row
    has nothing there, and those go last either way."""
    if column == 1:
        return float(row.sr)
    if column == 2:
        return row.seconds
    if column == 3:
        return row.peak_dbfs
    if column == 4:
        return float(row.clipped)
    if column == 5:
        return float(row.full_scale) if row.full_scale >= 0 else math.nan
    if column == 6:
        return row.grid_bits
    if column == 7:
        return row.band_edge_hz if row.band_limited else math.nan
    if column == 8:
        return float(row.lines) if row.lines >= 0 else math.nan
    if column == 9:
        return row.comb_hz
    if column == 10:
        # the share over its scrambled copy: what the voice column is for
        return row.voice - row.voice_control
    if column == 11:
        return math.inf if row.repeats else row.join_ratio
    if column == 12:
        return float(len(row.problems))
    raise ValueError(f"no column {column}")


def sort_rows(rows: Sequence[SurveyRow], column: int,
              descending: bool = False) -> List[SurveyRow]:
    """*rows* in the order of one column of the table. Rows that failed to
    load, or have nothing in that column, keep their order at the end."""
    if not 0 <= column < len(COLUMNS):
        raise ValueError(f"no column {column}")
    if column == 0:
        ordered = sorted(rows, key=lambda r: r.path.lower(), reverse=descending)
        return [r for r in ordered if not r.error] + [r for r in ordered if r.error]
    keyed = [(r, _sort_value(r, column)) for r in rows]
    present = [(r, v) for r, v in keyed if not r.error and not math.isnan(v)]
    present.sort(key=lambda pair: pair[1], reverse=descending)
    return [r for r, _ in present] + [r for r, v in keyed if r.error or math.isnan(v)]


def _number(value: float, form: str = "{:.1f}") -> str:
    return form.format(value) if math.isfinite(value) else "—"


def cells(row: SurveyRow, shown_name: str) -> List[str]:
    """*row* as the table shows it."""
    if row.error:
        return [shown_name, "", "", "", "", "", "", "", "", "", "", "", row.error]
    if row.band_limited:
        rates = " or ".join(str(r) for r in row.source_rates)
        band = f"{row.band_edge_hz:.0f} Hz" + (f" ({rates} Hz)" if rates else "")
    else:
        # a kudio that reports the edge says so when there is none; 4.1 does not
        band = "none" if row.full_scale >= 0 else "—"
    if row.repeats:
        joined = "repeat"
    elif math.isfinite(row.join_ratio):
        joined = f"×{row.join_ratio:.1f}"
    else:
        joined = "—"
    voice = ("—" if math.isnan(row.voice)
             else f"{row.voice * 100:.0f}% / {row.voice_control * 100:.0f}%")
    return [shown_name, str(row.sr), f"{row.seconds:.2f}", _number(row.peak_dbfs),
            str(row.clipped), "—" if row.full_scale < 0 else str(row.full_scale),
            _number(row.grid_bits), band, "—" if row.lines < 0 else str(row.lines),
            _number(row.comb_hz, "{:.2f}"), voice, joined, str(len(row.problems))]


def folder_summary(rows: Sequence[SurveyRow]) -> List[Dict[str, object]]:
    """One line per folder: how many files, and how many under each filter."""
    out: Dict[str, Dict[str, object]] = {}
    for row in rows:
        line = out.setdefault(row.folder, {"folder": row.folder, "files": 0, "failed": 0,
                                           **{f: 0 for f in FILTERS}})
        line["files"] += 1
        line["failed"] += bool(row.error)
        if row.error:
            continue
        for f in FILTERS:
            line[f] += matches(row, f)
    return list(out.values())


@dataclass
class FolderSpectrum:
    """One folder's takes at one rate, averaged."""

    folder: str
    sr: int
    files: int
    freqs: np.ndarray
    #: dB re full scale per Hz
    db: np.ndarray


def folder_spectra(rows: Sequence[SurveyRow], statistic: str = "mean") -> List[FolderSpectrum]:
    """A spectrum per folder and rate, from the takes' own. ``"mean"``
    averages the power, so the loud takes weigh most, as they would in a
    model trained on the folder; ``"median"`` is the median of the takes'
    dB in each bin, the typical take, which one loud take cannot move."""
    if statistic not in ("mean", "median"):
        raise ValueError(f"statistic is mean or median, got {statistic!r}")
    groups: Dict[Tuple[str, int], List[np.ndarray]] = {}
    for row in rows:
        if row.spectrum is not None and not row.error:
            groups.setdefault((row.folder, row.sr), []).append(row.spectrum)
    out = []
    for (folder, sr), spectra in groups.items():
        stack = np.vstack(spectra).astype(np.float64)
        floor = np.finfo(np.float64).tiny
        if statistic == "mean":
            db = 10.0 * np.log10(np.maximum(stack.mean(axis=0), floor))
        else:
            db = np.median(10.0 * np.log10(np.maximum(stack, floor)), axis=0)
        out.append(FolderSpectrum(folder, sr, len(spectra),
                                  np.fft.rfftfreq(SPECTRUM_SIZE, 1.0 / sr), db))
    return out


def name_time(path: str, pattern: str = TIME_PATTERN) -> float:
    """The time *path*'s name gives, as seconds on a clock that reads the
    name's own figures (no time zone is applied); nan when the name holds
    none. *pattern* is a regular expression whose groups are year, month,
    day, hour, minute and second, and optionally milliseconds."""
    found = re.search(pattern, Path(path).name)
    if not found:
        return math.nan
    parts = [int(g) if g else 0 for g in found.groups()[:7]]
    if len(parts) < 6:
        raise ValueError("the pattern needs six groups: year, month, day, hour, minute, second")
    try:
        whole = calendar.timegm(datetime(*parts[:6]).timetuple())
    except ValueError:
        return math.nan                     # a 13th month: not a time after all
    return whole + (parts[6] / 1000.0 if len(parts) > 6 else 0.0)


def _clock_of_modified(row: SurveyRow) -> float:
    """When *row*'s take began on the same clock as name_time: the time
    the file was written, which is when the take ended, less its length."""
    if math.isnan(row.modified):
        return math.nan
    local = datetime.fromtimestamp(row.modified)
    end = calendar.timegm(local.timetuple()) + local.microsecond / 1e6
    return end - row.seconds


def timeline_value(row: SurveyRow, metric: str, band: Tuple[float, float] = (0.0, 0.0)) -> float:
    """What the timeline plots for *row*."""
    if metric == "RMS":
        return row.rms_dbfs
    if metric == "Peak":
        return row.peak_dbfs
    if metric == "Band level":
        return band_level(row, *band)
    if metric == "Lines":
        return float(row.lines) if row.lines >= 0 else math.nan
    if metric == "Comb":
        return row.comb_hz
    if metric == "Voice":
        # over the scrambled copy, the margin the voice filter uses
        return 100.0 * (row.voice - row.voice_control)
    if metric == "Join":
        # a repeat has its step too; the ring the timeline puts on a seam
        # says it is one
        return row.join_ratio
    raise ValueError(f"no metric {metric!r}")


@dataclass
class Timeline:
    """The takes on one clock, a series per folder."""

    #: per folder: the takes' start times, values and paths, in time order
    series: List[Dict[str, object]]
    #: the spans with no take, within a folder, as (start, end)
    gaps: List[Tuple[float, float]]
    #: takes whose time could not be read
    untimed: int


def timeline(rows: Sequence[SurveyRow], metric: str = "RMS", *, clock: str = "name",
             pattern: str = TIME_PATTERN, band: Tuple[float, float] = (0.0, 0.0)) -> Timeline:
    """Every take that loaded at the time it began: from its name with
    *pattern*, or (*clock* ``"modified"``) the time its file was written
    less its length. A gap is a span in a folder where at least a whole
    take, and at least two seconds, is missing between the end of one take
    and the start of the next; the two-second floor absorbs names that
    give whole seconds only."""
    if clock not in ("name", "modified"):
        raise ValueError(f"clock is name or modified, got {clock!r}")
    pattern = pattern or TIME_PATTERN
    # a bad pattern fails here, once, rather than file by file or not at all
    if re.compile(pattern).groups < 6:
        raise ValueError("the pattern needs six groups: year, month, day, hour, minute, second")
    folders: Dict[str, List[Tuple[float, SurveyRow]]] = {}
    untimed = 0
    for row in rows:
        if row.error:
            continue
        start = name_time(row.path, pattern) if clock == "name" else _clock_of_modified(row)
        if math.isnan(start):
            untimed += 1
            continue
        folders.setdefault(row.folder, []).append((start, row))
    series, gaps = [], []
    for folder, timed in folders.items():
        timed.sort(key=lambda pair: pair[0])
        for (a, before), (b, _) in zip(timed, timed[1:]):
            end = a + before.seconds
            if b - end > max(2.0, before.seconds):
                gaps.append((end, b))
        series.append({"folder": folder,
                       "x": [t for t, _ in timed],
                       "y": [timeline_value(r, metric, band) for _, r in timed],
                       "seconds": [r.seconds for _, r in timed],
                       "paths": [r.path for _, r in timed],
                       "flags": [bool(r.problems or r.seam) for _, r in timed]})
    return Timeline(series, sorted(gaps), untimed)


CSV_FIELDS = ("path", "folder", "sr", "seconds", "peak_dbfs", "clipped", "full_scale",
              "grid_bits", "band_limited", "band_edge_hz", "source_rates", "lines",
              "strongest_hz", "comb_hz", "voice", "voice_control", "join_ratio",
              "repeats", "problems", "error", "rms_dbfs", "modified")


def write_csv(rows: Sequence[SurveyRow], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in rows:
            w.writerow([r.path, r.folder, r.sr, f"{r.seconds:.3f}", r.peak_dbfs, r.clipped,
                        r.full_scale, r.grid_bits, r.band_limited, r.band_edge_hz,
                        " ".join(map(str, r.source_rates)), r.lines, r.strongest_hz,
                        r.comb_hz, r.voice, r.voice_control, r.join_ratio, r.repeats,
                        " | ".join(r.problems), r.error, r.rms_dbfs,
                        "" if math.isnan(r.modified)
                        else datetime.fromtimestamp(r.modified).isoformat(timespec="seconds")])


def write_summary(rows: Sequence[SurveyRow], path: str) -> None:
    lines = folder_summary(rows)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["folder", "files", "failed", *FILTERS])
        w.writeheader()
        w.writerows(lines)


__all__ = ["CLIP_RUNS", "COLUMNS", "FILTERS", "SurveyRow", "survey_file", "run_survey",
           "join", "matches", "cells", "sort_rows", "folder_summary", "write_csv",
           "write_summary", "scrambled", "voice_share", "take_spectrum", "band_level",
           "FolderSpectrum", "folder_spectra", "name_time", "Timeline", "timeline",
           "timeline_value", "TIME_PATTERN", "TIMELINE_METRICS", "SPECTRUM_SIZE"]
