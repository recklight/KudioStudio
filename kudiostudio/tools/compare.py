# -*- coding: utf-8 -*-
"""Run every enhancement method over the same clip and rank them.

Choosing a denoiser by applying one, listening, undoing and applying the next
is how you end up with whichever one you happened to try last. This runs them
all on identical audio with identical framing, measures each, and lets any
row be auditioned without committing to it.

What the numbers mean depends on whether there is a reference. With a clean
one -- a synthetic mixture, a studio take -- SI-SDR and PESQ say how close the
result is to the truth, and ΔSNR how much better that is than doing nothing.
Without one, which is every real recording, all that is left is the residual
noise floor, and a lower floor is not automatically better: the easiest way
to reach a very low one is to delete the speech along with the noise. So the
ranking is a shortlist, not a verdict, and auditioning is always offered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

import kudio
from kudiostudio.tools.enhance import INSTALL_HINT, LearnedModel, ModelState, learned_code

log = logging.getLogger(__name__)

REF_NONE = "none — a real recording has no clean version"
REF_ORIGINAL = "the original, before this session's edits"
REF_FILE = "a file…"
REFERENCES = (REF_NONE, REF_ORIGINAL, REF_FILE)

COLUMNS = ("method", "noise", "ms", "floor dBFS", "LUFS",
           "SI-SDR", "ΔSNR", "PESQ", "STOI")
#: columns that only mean anything with a clean reference
REFERENCE_COLUMNS = tuple(COLUMNS.index(c) for c in ("SI-SDR", "ΔSNR", "PESQ", "STOI"))

#: Silence between two results auditioned back to back. Long enough that the
#: switch is heard as a switch, short enough not to lose the thread.
AB_GAP_SECONDS = 0.35

SWEEP_METHODS = "gain rules, one noise estimator"
SWEEP_NOISES = "noise estimators, one gain rule"
SWEEP_GRID = "everything — the full grid"
SWEEPS = (SWEEP_METHODS, SWEEP_NOISES, SWEEP_GRID)

#: the gain rule the list starts on
DEFAULT_METHOD = "logmmse"

#: Below this relative gap the two axes are called comparable rather than one
#: being declared the winner. Measured on the repo's own fixture they land
#: within 1%, and a table that crowns an axis on a 0.1 dB difference is
#: inventing a finding.
AXIS_TIE = 0.20

SWEEP_TIP = ("Which axis to vary. Enhancement is two choices — a gain rule and a "
             "noise estimator — and the estimator is usually the wider of the two, "
             "so 'gain rules only' is the narrower experiment.")
RUN_DIR_TIP = ("Rank a trained model in the same table as the statistical methods. "
               "'Is the model actually better than log-MMSE' is not a question two "
               "separate printouts answer.")
SELECTION_TIP = ("Faster, and lets you judge one difficult passage. Noise estimation "
                 "sees only that range, which is a real difference in the result.")
METRICS_TEXT = "PESQ / STOI (slow; STOI needs kudio[eval], PESQ needs kudio[pesq])"
RUN_NOTE = ("Every method sees identical audio and identical framing, so the "
            "differences are the methods. Click a row to hear it — a low noise floor "
            "is easiest to reach by deleting the speech too.\n\n"
            "These are for noisy audio. Above roughly 10 dB SNR they all measure worse "
            "than doing nothing, because the distortion they add costs more than the "
            "noise they remove. They also need to hear the noise on its own: a clip "
            "with no pauses gives the noise estimator almost nothing to work from.")


@dataclass(frozen=True, eq=False)
class RunContext:
    """What one comparison ran on, kept with the results it produced.

    Apply writes a row into the history as the code that computes it again,
    and that code is only true of the audio, the range and the model the row
    came from. Kept per run rather than in the panel's last settings, so a
    run started while another's rows are on screen cannot lend those rows
    its range, and a result that lands after the audio was edited is not
    recorded as if made from the edit.
    """
    #: the session's audio array itself, compared by identity
    y: object
    #: the samples compared, ``None`` for the whole clip
    ran_on: Optional[Tuple[int, int]]
    #: the learned row's run directory, and its model files as they were
    learned_dir: Optional[str] = None
    model: Optional[ModelState] = None


def label(method: str) -> str:
    """A custom enhancer has no entry in kudio.METHODS; its name is its label."""
    spec = kudio.METHODS.get(method)
    return spec.label if spec is not None else method


def learned_entry(run_dir: str, model: LearnedModel, available: Callable[[], bool]):
    """``(name, callable)`` for a run directory, or ``None`` when there is
    none. The model is not loaded here: `compare_with_model` loads it on the
    job's thread before the comparison starts."""
    if not run_dir or not Path(run_dir).is_dir():
        return None
    if not available():
        raise RuntimeError(INSTALL_HINT)

    def enhance(y, sr):
        return model.enhance(y, sr, run_dir)

    return (Path(run_dir).name, enhance)


def sweep_arguments(sweep: str, method: str, noise: str, learned) -> Dict:
    """``methods=`` / ``noises=`` / ``noise=`` for the chosen sweep, with the
    learned row added when there is one."""
    if sweep == SWEEP_METHODS:
        arguments = {"methods": None, "noise": noise}
    elif sweep == SWEEP_NOISES:
        arguments = {"methods": [method], "noises": list(kudio.NOISE_ESTIMATORS)}
    else:
        arguments = {"methods": None, "noises": list(kudio.NOISE_ESTIMATORS)}
    if learned is not None:
        base = arguments["methods"] or list(kudio.METHODS)
        arguments["methods"] = list(base) + [learned]
    return arguments


def call_text(arguments: Dict, reference: str, metrics: bool) -> str:
    parts = []
    methods = arguments["methods"]
    if methods is not None:
        parts.append("[" + ", ".join(
            repr(m) if isinstance(m, str) else f"({m[0]!r}, model.enhance)"
            for m in methods) + "]")
    parts.append("reference=None" if reference == REF_NONE else "reference=ref")
    parts.append("noises=list(kudio.NOISE_ESTIMATORS)" if "noises" in arguments
                 else f"noise={arguments['noise']!r}")
    parts.append(f"metrics={metrics}")
    return f"kudio.compare_enhancers(y, sr, {', '.join(parts)})"


def reference_audio(choice: str, path: Optional[str], session) -> Optional[np.ndarray]:
    """The clean signal for *choice*, at the session's rate, or None."""
    if choice == REF_NONE:
        return None
    if choice == REF_FILE:
        if not path:
            raise RuntimeError("Select a reference file first.")
        return kudio.file_load(Path(path), sr=session.sr)[0]
    original = session.original()
    if original is None:
        raise RuntimeError("This session has no original to compare against.")
    y0, sr0 = original
    if sr0 != session.sr:
        return kudio.resample(y0, sr0, session.sr)
    return y0


def count_runs(methods, arguments: Dict) -> int:
    """How many rows a run will produce: a custom entry brings its own noise
    estimate, so it runs once."""
    entries = methods or list(kudio.METHODS)
    swept = len(arguments.get("noises") or [1])
    return sum(swept if isinstance(m, str) else 1 for m in entries)


def compare_with_model(y: np.ndarray, sr: int, methods, *, model: LearnedModel,
                       learned_dir: Optional[str], **kwargs):
    """`kudio.compare_enhancers`, with the learned row's model ready first.

    Run on a job's thread. compare_enhancers times each row from the moment
    it is called, so a model loaded inside its row is charged for importing
    PyTorch and reading the checkpoint -- for a small model, hundreds of times
    what the denoise costs -- in a column whose only use is setting rows
    against each other. Loaded here instead, and kept in *model* for the next
    comparison on the same run, the row's time is the denoise.

    The model also runs once over a short excerpt: the first pass through a
    network pays one-time set-up that no later pass does, which is the same
    reason compare_enhancers warms up its own methods before timing them.
    """
    if learned_dir:
        try:
            model.enhance(y[:sr // 2], sr, learned_dir)
        except Exception:  # noqa: BLE001
            # left for the row to raise again, so a model that will not load
            # fails its own row, as any method does, rather than the whole run
            log.debug("preparing %s failed", learned_dir, exc_info=True)
    return kudio.compare_enhancers(y, sr, methods, **kwargs)


# ------------------------------------------------------------- the table

def _fmt(value, spec: str) -> str:
    return "—" if value is None or not np.isfinite(value) else spec.format(value)


def as_number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def cells(result) -> List[str]:
    """A result's row, as text, in `COLUMNS` order."""
    row = [label(result.method), result.noise]
    if result.error:
        return row + [result.error] + [""] * (len(COLUMNS) - 3)
    return row + [
        f"{result.seconds * 1000:.0f}",
        f"{result.noise_floor_db:+.1f}",
        f"{result.lufs:+.1f}" if np.isfinite(result.lufs) else "—",
        _fmt(result.si_sdr_db, "{:+.2f}"),
        _fmt(result.delta_snr_db, "{:+.2f}"),
        _fmt(result.pesq, "{:.2f}"),
        _fmt(result.stoi, "{:.3f}"),
    ]


def has_reference(results: Sequence) -> bool:
    return any(r.si_sdr_db is not None for r in results)


def axis_spread(scored: Sequence) -> str:
    """Which of the two choices moved the result more -- or that neither did.

    Enhancement is two decisions, and which one to spend effort on is a real
    question. It is answered per clip: on some the estimator dominates, on
    some the gain rule, and on many they are the same size.
    """
    # a custom enhancer sits on neither axis, so it says nothing about them
    scored = [r for r in scored if r.noise != kudio.CUSTOM_NOISE]
    methods = {r.method for r in scored}
    noises = {r.noise for r in scored}
    if len(methods) < 2 or len(noises) < 2:
        return ""

    def spread(group_by) -> float:
        # the *mean* spread, not the widest: one lucky combination should not
        # decide which axis matters
        groups = [[r.si_sdr_db for r in scored if group_by(r) == key]
                  for key in {group_by(r) for r in scored}]
        return float(np.mean([max(g) - min(g) for g in groups if len(g) > 1]))

    across_rules = spread(lambda r: r.noise)     # varying rule, fixed noise
    across_noise = spread(lambda r: r.method)    # varying noise, fixed rule
    wider = max(across_rules, across_noise)
    narrower = min(across_rules, across_noise)
    if wider - narrower <= AXIS_TIE * max(wider, 1e-9):
        return (f"<br>The gain rule and the noise estimator moved this clip about "
                f"equally ({across_rules:.1f} dB against {across_noise:.1f} dB), so "
                f"neither is the one to spend the effort on.")
    name = "noise estimator" if across_noise > across_rules else "gain rule"
    other = "gain rule" if name == "noise estimator" else "noise estimator"
    return (f"<br>The {name} moved this clip more than the {other} did "
            f"({wider:.1f} dB against {narrower:.1f} dB).")


def verdict(results: Sequence) -> Tuple[str, str, str]:
    """``(text, tone, line for the status bar)``. The text may hold <b> and
    <br> and nothing else; tone is ok, warn or hint."""
    usable = [r for r in results if r.error is None]
    if not usable:
        return "Every method failed.", "hint", "Comparison finished — nothing succeeded."
    said = (f"Comparison finished — {len(usable)}/{len(results)} "
            f"combination(s) succeeded.")
    scored = [r for r in usable if r.si_sdr_db is not None]
    if scored:
        best = max(scored, key=lambda r: r.si_sdr_db)
        improved = [r for r in scored if r.delta_snr_db is not None and r.delta_snr_db > 0]
        if not improved:
            # Every method moved the audio *away* from the reference. The usual
            # cause is that the reference is the noisy recording itself, which
            # is the one thing it must not be: scoring against it measures how
            # much the audio changed, not whether it improved.
            return (f"<b>No method beat leaving it alone</b> — the closest was "
                    f"{label(best.method)} at {best.delta_snr_db:+.2f} dB. That "
                    f"usually means the reference is not clean: scoring against the "
                    f"noisy recording measures how much you changed it, not whether "
                    f"you improved it.", "warn", said)
        return (f"Best against the reference: <b>{label(best.method)} + {best.noise}</b> "
                f"at {best.si_sdr_db:+.2f} dB SI-SDR, {best.delta_snr_db:+.2f} dB better "
                f"than leaving it alone." + axis_spread(scored), "ok", said)
    quietest = min(usable, key=lambda r: r.noise_floor_db)
    return (f"No reference, so there is no quality score. The quietest residue is "
            f"<b>{label(quietest.method)} + {quietest.noise}</b> at "
            f"{quietest.noise_floor_db:+.1f} dBFS — which is also what you get by "
            f"deleting the speech. Audition before believing it.", "hint", said)


def audition(results: Sequence, sr: int) -> Tuple[np.ndarray, str]:
    """The rows' audio back to back, a short silence at each seam, and the
    line saying what is playing. Two rows joined into one loop is the
    comparison people actually make -- clicking between them puts a gap and
    a mouse movement in the middle of the only thing being judged."""
    if len(results) == 1:
        return (np.asarray(results[0].audio, dtype=np.float32),
                f"Auditioning {results[0].label} — nothing has been committed.")
    gap = np.zeros(int(AB_GAP_SECONDS * sr), dtype=np.float32)
    pieces = []
    for result in results:
        pieces.extend([np.asarray(result.audio, dtype=np.float32), gap])
    return (np.concatenate(pieces[:-1]),
            "Auditioning " + " → ".join(r.label for r in results)
            + " — nothing has been committed.")


def script(result, context: RunContext) -> Optional[str]:
    """The code that computes *result* again, the way compare_enhancers did.

    A statistical row is one `spectral_enhance` call with the library's
    defaults, since nothing else is passed; the learned row is the same
    computation the Enhance panel records, from the run directory the row was
    computed with.
    """
    if result.noise == kudio.CUSTOM_NOISE:
        if context.learned_dir is None or result.method != Path(context.learned_dir).name:
            return None
        return learned_code(context.learned_dir, context.model) + \
            "\ny = np.asarray(out, dtype=np.float32)"
    return (f"y = np.asarray(kudio.spectral_enhance(y, sr, {result.method!r}, "
            f"noise={result.noise!r}), dtype=np.float32)")


def file_name(stem: str, result) -> str:
    """The estimator is part of a row's identity; two rows can share a
    method and would otherwise overwrite each other."""
    return f"{stem}_{result.method}_{result.noise}.wav"


__all__ = ["AB_GAP_SECONDS", "AXIS_TIE", "COLUMNS", "REFERENCES", "REFERENCE_COLUMNS",
           "REF_FILE", "REF_NONE", "REF_ORIGINAL", "RunContext", "SWEEPS", "SWEEP_GRID",
           "SWEEP_METHODS", "SWEEP_NOISES", "as_number", "audition", "axis_spread",
           "call_text", "cells", "compare_with_model", "count_runs", "file_name",
           "has_reference", "label", "learned_entry", "reference_audio", "script",
           "sweep_arguments", "verdict"]
