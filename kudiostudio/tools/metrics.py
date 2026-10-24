# -*- coding: utf-8 -*-
"""Objective quality metrics, with and without a clean reference.

`kudio.audio_report` needs nothing to compare against and always works. The
reference metrics -- SNR / SI-SDR / segmental SNR in pure numpy, STOI and
BSS-SDR from `kudio[eval]`, PESQ from `kudio[pesq]` -- need the clean signal
beside the degraded one, which a synthetic dataset has and a real recording
never does.

PESQ is two scales, not one number. kudio reports MOS-LQO by default;
enhancement papers mostly quote the raw narrowband P.862 score, and raw 2.03
is about 1.65 MOS-LQO. The table names which one it shows.

Every function here returns rows of (label, value) and the lines to report,
so the table is worded here and the panel only shows it.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple

import numpy as np

import kudio

log = logging.getLogger(__name__)

Rows = List[Tuple[str, str]]

REFERENCES = ("Original (before edits)", "File…")

#: label first. Different scales, not different roundings, so the table has
#: to say which one it shows
PESQ_SCALES = (("MOS-LQO", "lqo"), ("raw P.862", "raw"))

PESQ_TIP = ("Which PESQ scale the table reports.\n\n"
            "MOS-LQO is what kudio has always shown. raw P.862 is the\n"
            "narrowband score most speech-enhancement papers quote. They are\n"
            "different scales, not different roundings: raw 2.03 is about\n"
            "1.65 MOS-LQO.")

ALIGN_TIP = ("Every metric here compares sample i of one signal with sample i\n"
             "of the other. A clip scored against itself reads +145 dB; one\n"
             "sample of delay costs 134 of them, and a millisecond scores worse\n"
             "than not processing at all — with nothing in the number to say\n"
             "which of the two happened.\n\n"
             "The delay is measured and reported either way. This decides\n"
             "whether it is taken out before scoring.")

DNSMOS_TIP = ("ITU-T P.835: what a listener would say, with no reference.\n"
              "Needs onnxruntime and the DNS-Challenge weights, which kudio\n"
              "does not ship.")

#: Which extra installs each back-end. PESQ has its own because it builds
#: from source, so it is deliberately not in [eval] or [all].
INSTALL_HINT = {
    "PESQ": 'pip install "kudio[pesq]" (builds from source: needs a C '
            'compiler — on Windows, the MSVC Build Tools)',
    "STOI": 'pip install "kudio[eval]"',
    "SDR": 'pip install "kudio[eval]"',
}

NOTHING_WRONG = ("Nothing wrong with the recording itself. This says the audio is "
                 "technically sound, not that it sounds good — for that, try the "
                 "DNSMOS button below.")


def pesq_scale(label: str) -> str:
    """kudio's name for the scale labelled *label*."""
    return dict(PESQ_SCALES)[label]


# --------------------------------------------------------- no reference

def inspect_rows(report) -> Tuple[Rows, List[str]]:
    """The table for an `audio_report`, and its problems."""
    lufs = ("n/a (shorter than 400 ms)" if not np.isfinite(report.lufs)
            else f"{report.lufs:+.2f} LUFS")
    rows = [
        ("Peak", "silence" if report.peak_dbfs == float("-inf")
         else f"{report.peak_dbfs:+.2f} dBFS"),
        ("RMS", f"{report.rms_dbfs:+.2f} dBFS"),
        ("Loudness (BS.1770)", lufs),
        ("Clipped samples", f"{report.clipped_samples} "
                            f"({report.clipped_ratio * 100:.3f}%)"),
        ("DC offset", f"{report.dc_offset:+.5f}"),
        ("Silence", f"{report.silence_ratio * 100:.1f}%"),
        ("Noise floor", f"{report.noise_floor_dbfs:+.2f} dBFS"),
        ("Estimated SNR", f"{report.estimated_snr_db:+.1f} dB"),
        ("Bandwidth", f"{report.bandwidth_hz:.0f} Hz of "
                      f"{report.sr / 2:.0f} Hz available"),
    ]
    return rows, list(report.problems())


def dnsmos_rows(score) -> Rows:
    """SIG and BAK move in opposite directions under denoising, so all three
    are shown rather than their average."""
    return [("SIG (speech)", f"{score.sig:.2f} / 5"),
            ("BAK (background)", f"{score.bak:.2f} / 5"),
            ("OVRL (overall)", f"{score.ovrl:.2f} / 5"),
            ("Windows averaged", str(score.segments))]


# ------------------------------------------------------- with a reference

def line_up(ref: np.ndarray, deg: np.ndarray, sr: int,
            align: bool) -> Tuple[Rows, np.ndarray, np.ndarray, List[str]]:
    """Measure the offset, report it, and take it out if *align*.

    Always measured, because a bad score from a misalignment and a bad score
    from bad processing look identical and mean opposite things. Returns the
    rows, the two signals as they will be scored, and the lines to report.
    """
    try:
        found = kudio.find_delay(ref, deg, sr)
    except Exception as e:  # noqa: BLE001
        log.debug("alignment failed: %s", e)
        return [], ref, deg, []

    rows = [("Alignment", str(found))]
    if not found.confident():
        rows.append(("⚠ Reference", "these do not look like the same recording — "
                                    "the scores below are about two unrelated signals"))
        return rows, ref, deg, ["The reference does not look like this recording."]
    if found.delay == 0:
        return rows, ref, deg, []
    if not align:
        rows.append(("⚠ Not aligned",
                     f"{abs(found.milliseconds):.1f} ms of offset is left in; "
                     f"the scores below are mostly measuring it"))
        return rows, ref, deg, []
    ref, deg = found.apply(ref, deg)
    return rows, ref, deg, [f"Reference {found}; lined up before scoring."]


def reference_rows(ref: np.ndarray, deg: np.ndarray, sr: int) -> Rows:
    """The scores numpy alone can give, for two signals of the same length."""
    n = len(ref)
    return [("SNR", f"{kudio.snr(ref, deg):+.3f} dB"),
            ("SI-SDR", f"{kudio.si_sdr(ref, deg):+.3f} dB"),
            ("Segmental SNR", f"{kudio.segmental_snr(ref, deg):+.3f} dB"),
            ("Compared samples", f"{n} ({n / sr:.2f} s)")]


def installed() -> Dict[str, bool]:
    return dict(zip(("PESQ", "STOI", "SDR"), kudio.check_metrics_install()))


def why_missing(name: str, available: Dict[str, bool], sr: int) -> str:
    """None means two different things and they need different answers.

    An absent back-end returns None, and so does an installed one that cannot
    run on this pair -- PESQ is defined at 8 and 16 kHz only -- so labelling
    every None "not installed" would tell people to install what they have.
    """
    if not available.get(name, False):
        return "not installed"
    if name == "PESQ" and int(sr) not in (8000, 16000):
        return f"not defined at {sr} Hz (PESQ is 8 or 16 kHz)"
    return "could not be computed for this pair"


def optional_rows(result: Sequence, pesq_label: str, available: Dict[str, bool],
                  sr: int) -> Rows:
    """PESQ, STOI and SDR from `kudio.eval_metrics`, or why each is missing."""
    pesq, stoi, sdr = result
    return [(f"PESQ ({pesq_label})",
             f"{pesq:.3f}" if pesq is not None else why_missing("PESQ", available, sr)),
            ("STOI", f"{stoi:.3f}" if stoi is not None else why_missing("STOI", available, sr)),
            ("SDR", f"{sdr:+.3f} dB" if sdr is not None else why_missing("SDR", available, sr))]


def availability(available: Dict[str, bool]) -> str:
    """One line on which back-ends are there, and the extra for each missing
    one -- a single `kudio[eval]` for all three would be a dead end for
    PESQ, which that extra does not carry."""
    missing = [n for n, ok in available.items() if not ok]
    if not missing:
        return "PESQ, STOI and SDR back-ends available."
    return "  ·  ".join(f"{name} unavailable — {INSTALL_HINT[name]}" for name in missing)


__all__ = ["ALIGN_TIP", "DNSMOS_TIP", "INSTALL_HINT", "NOTHING_WRONG", "PESQ_SCALES",
           "PESQ_TIP", "REFERENCES", "availability", "dnsmos_rows", "inspect_rows",
           "installed", "line_up", "optional_rows", "pesq_scale", "reference_rows",
           "why_missing"]
