# -*- coding: utf-8 -*-
"""Objective quality metrics, with and without a clean reference.

`kudio.audio_report` needs nothing to compare against and always works, and
neither do SRMR and WADA-SNR. The reference metrics -- SNR / SI-SDR /
segmental SNR, Hu and Loizou's measures, the log-spectral distance, NCM and
CSII in numpy and scipy, STOI and BSS-SDR from `kudio[eval]`, PESQ and the
CSIG / CBAK / COVL built on it from `kudio[pesq]` -- need the clean signal
beside the degraded one, which a synthetic dataset has and a real recording
never does.

PESQ is two scales, not one number. kudio reports MOS-LQO by default; raw
P.862 is the narrowband score before the P.862.1 mapping, and raw 2.03 is
about 1.66 MOS-LQO. Papers quote both, so the table names which one it
shows.

Every function here returns rows of (label, value) and the lines to report,
so the table is worded here and the panel only shows it.
"""
from __future__ import annotations

import logging
import warnings
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import kudio

log = logging.getLogger(__name__)

Rows = List[Tuple[str, str]]

REFERENCES = ("Original (before edits)", "File…")

#: label first. Different scales, not different roundings, so the table has
#: to say which one it shows
PESQ_SCALES = (("MOS-LQO", "lqo"), ("raw P.862", "raw"))

#: 1.66 is what P.862.1 maps raw 2.03 to, the mapping kudio inverts for
#: its raw score
PESQ_TIP = ("Which PESQ scale the table reports.\n\n"
            "MOS-LQO is what kudio reports by default. raw P.862 is the\n"
            "narrowband score before the P.862.1 mapping. The two are\n"
            "different scales, not different roundings: raw 2.03 is about\n"
            "1.66 MOS-LQO. Papers quote both, so check which one a paper used.")

ALIGN_TIP = ("Every metric here but PESQ compares sample i of one signal with\n"
             "sample i of the other. A clip scored against itself reads +145 dB; one\n"
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

BLIND_TIP = ("Two more things a recording can say about itself.\n\n"
             "SRMR: how reverberant it is, as the energy of the slow\n"
             "modulations syllables make over the faster ones a room adds.\n"
             "Higher is drier, and a figure means something only next to the\n"
             "same speech somewhere else. It is scored at 8 and 16 kHz only,\n"
             "so other rates are measured on a 16 kHz copy.\n\n"
             "WADA-SNR: how far the speech sits above its noise, read from how\n"
             "peaked the samples are. Good for sorting recordings; read\n"
             "anything above 30 dB as clean rather than as a figure.")

#: Hu and Loizou's measures, and SRMR, are defined at these two rates only
QUALITY_RATES = (8000, 16000)

#: the PESQ kudio.composite puts into CSIG, CBAK and COVL by default, by rate
_COMPOSITE_PESQ = {16000: "wideband MOS-LQO", 8000: "raw P.862"}

#: CSII's bands reach 3675 Hz, so half the rate has to clear that; NCM, like
#: Hu and Loizou's measures, is defined at QUALITY_RATES only
CSII_MIN_RATE = 8000

#: CSII's rows, one for each level of the reference's frames: at or above its
#: overall RMS, within 10 dB under it, and everything quieter
CSII_LEVELS = (("CSII (high level)", "high"), ("CSII (mid level)", "mid"),
               ("CSII (low level)", "low"))

#: what kudio.csii warns about one level, and what that level's row says;
#: one warning names every such level, so each row looks for its own name
_CSII_LEVEL_WHY = (("no frame of ref is at the {} level",
                    "n/a — no frame of the reference is at this level"),
                   ("only one frame of ref is at the {} level",
                    "n/a — one frame at this level, too few"))

#: why a figure is missing, by what kudio's warning said, first match wins;
#: the window shows these short reasons rather than kudio's full sentence
NO_FIGURE = (("reference is digital silence", "n/a — the reference is silent"),
             ("ref is digital silence", "n/a — the reference is silent"),
             ("deg is digital silence throughout", "n/a — this clip is silent"),
             ("digital silence", "n/a — its pauses are digital silence"),
             ("needs at least", "n/a — too short to measure"),
             ("no more peaked than Gaussian", "n/a — the noise is not Gaussian, or "
                                              "drowns the speech"),
             ("sparser than", "n/a — next to no noise to measure"),
             ("too loud to square", "n/a — too loud to compute in float64"),
             ("is silent", "silence"),
             ("samples are zero", "silence"),
             ("empty", "silence"))

NOTHING_WRONG = ("Nothing wrong with the recording itself. This says the audio is "
                 "technically sound, not that it sounds good — for that, try the "
                 "DNSMOS button below.")


def pesq_scale(label: str) -> str:
    """kudio's name for the scale labelled *label*."""
    return dict(PESQ_SCALES)[label]


# --------------------------------------------------------- no reference

def inspect_rows(report) -> Tuple[Rows, List[str]]:
    """The table for an `audio_report`, and its problems."""
    # no figure, and the row says which of the reasons it is: silence has no
    # level, BS.1770 needs a 400 ms block, and NaN in the clip measures as NaN
    if np.isfinite(report.lufs):
        lufs = f"{report.lufs:+.2f} LUFS"
    elif report.peak_dbfs == float("-inf"):
        lufs = "silence"
    elif report.duration < 0.4:
        lufs = "n/a (shorter than 400 ms)"
    else:
        lufs = "n/a"
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


def _why_none(caught) -> str:
    """The short reason for a missing figure, from what kudio warned."""
    said = " ".join(str(w.message) for w in caught)
    for clue, reason in NO_FIGURE:
        if clue in said:
            return reason
    # a reason the table does not know yet: kudio's own sentence, from the
    # warning that says nan, without the function name it opens with
    told = [str(w.message) for w in caught]
    told = [m for m in told if "nan" in m.lower()] or told
    if told:
        return "n/a — " + told[0].split("(): ", 1)[-1]
    return "n/a"


def blind_rows(y: np.ndarray, sr: int) -> Rows:
    """SRMR and WADA-SNR, for a clip with no clean version.

    SRMR is scored at 8 and 16 kHz only, the rates its authors' toolbox was
    validated at, so at any other rate it is measured on a 16 kHz copy and
    its row says so. Run as a job, since the full SRMR
    filters the whole clip at the audio rate.
    """
    rate = int(sr) if int(sr) in QUALITY_RATES else 16000
    if rate == int(sr):
        label, copy = "SRMR (higher is drier)", y
    else:
        label = f"SRMR (on a {rate // 1000} kHz copy; higher is drier)"
        copy = kudio.resample(y, int(sr), rate)
    rows: Rows = []
    for name, measure, form in ((label, lambda: kudio.srmr(copy, rate), "{:.2f}"),
                                ("WADA-SNR", lambda: kudio.wada_snr(y, sr), "{:+.1f} dB")):
        # jobs run one at a time, so recording here does not take warnings
        # that belong to another job
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = measure()
        rows.append((name, form.format(value) if np.isfinite(value) else _why_none(caught)))
    return rows


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
    # the delay is how far the clip sits after the reference, so the clip is
    # what lags or leads
    return rows, ref, deg, [f"This clip {found}; lined up with the reference before scoring."]


def reference_rows(ref: np.ndarray, deg: np.ndarray, sr: int) -> Rows:
    """The scores numpy alone can give, for two signals of the same length;
    one kudio cannot give, against a silent reference say, says why."""
    n = len(ref)
    rows = [(label, _measured(lambda r, d, _rate, f=measure: f(r, d), ref, deg, sr,
                              "{:+.3f} dB"))
            for label, measure in (("SNR", kudio.snr), ("SI-SDR", kudio.si_sdr),
                                   ("Segmental SNR", kudio.segmental_snr))]
    return rows + [("Compared samples", f"{n} ({n / sr:.2f} s)")]


def _measured(measure, ref, deg, sr, form: str) -> str:
    """A measure, formatted, or why it has no figure.

    The newer measures run in the same job as PESQ, STOI and SDR, after them,
    so one that fails gives its own row a reason and leaves those three
    standing.
    """
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = measure(ref, deg, sr)
    except Exception as e:  # noqa: BLE001
        log.debug("%s failed on the pair: %s", getattr(measure, "__name__", measure), e)
        return "could not be computed for this pair"
    return form.format(value) if np.isfinite(value) else _why_none(caught)


def quality_rows(ref: np.ndarray, deg: np.ndarray, sr: int) -> Rows:
    """Hu and Loizou's measures and the log-spectral distance, for two signals
    of the same length. The five are defined at 8 and 16 kHz only, so at
    another rate one row says so instead of five."""
    rows: Rows = []
    if int(sr) in QUALITY_RATES:
        for label, measure, form in (
                ("LLR", kudio.llr, "{:.3f}"),
                ("Itakura-Saito", kudio.itakura_saito, "{:.3f}"),
                ("Cepstral distance", kudio.cepstral_distance, "{:.3f} dB"),
                ("WSS", kudio.wss, "{:.2f}"),
                ("fwSegSNR", kudio.fw_segmental_snr, "{:+.3f} dB")):
            rows.append((label, _measured(measure, ref, deg, sr, form)))
    else:
        rows.append(("LLR / IS / CEP / WSS / fwSegSNR",
                     f"not defined at {sr} Hz (8 or 16 kHz only)"))
    rows.append(("LSD", _measured(kudio.lsd, ref, deg, sr, "{:.3f}")))
    return rows


def composite_rows(ref: np.ndarray, deg: np.ndarray, sr: int,
                   available: Dict[str, bool],
                   whole: Optional[Tuple[np.ndarray, np.ndarray]] = None,
                   pesq: Optional[float] = None) -> Rows:
    """CSIG, CBAK and COVL, and the PESQ that went into them.

    kudio puts in what published figures were made with: wideband MOS-LQO at
    16 kHz and raw narrowband P.862 at 8 kHz, whichever scale the PESQ row
    shows, so that PESQ gets a row of its own. That PESQ is scored on
    *whole*, the pair as loaded, where lining up or the shorter length has
    changed it, and LLR, WSS and segSNR on the pair as given. *pesq* is that
    score where the caller already has it.
    """
    label = "CSIG / CBAK / COVL"
    if not available.get("PESQ", False):
        return [(label, "needs PESQ, which is not installed")]
    if int(sr) not in QUALITY_RATES:
        return [(label, f"not defined at {sr} Hz (8 or 16 kHz only)")]
    given = {} if pesq is None else {"pesq": pesq}
    if not given and _changed(whole, ref, deg):
        mode, scale = _COMPOSITE_VARIANT[int(sr)]
        value = _whole_pesq(whole, sr, mode, scale)
        if value is None:
            return [(label, "could not be computed for this pair")]
        given = {"pesq": value}
    caught = []
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rated = kudio.composite(ref, deg, sr, **given)
    except kudio.DependencyError:
        # looked for once per run, so a package removed since then ends here
        return [(label, "needs PESQ, which is not installed")]
    except Exception as e:  # noqa: BLE001
        log.debug("composite failed on the pair: %s", e)
        rated = None
    if rated is None:
        return [(label, "could not be computed for this pair")]
    if not np.isfinite(rated.csig):
        return [(label, _why_none(caught))]
    return [("CSIG (signal)", f"{rated.csig:.2f} / 5"),
            ("CBAK (background)", f"{rated.cbak:.2f} / 5"),
            ("COVL (overall)", f"{rated.covl:.2f} / 5"),
            (f"PESQ inside them ({_COMPOSITE_PESQ[int(sr)]})", f"{rated.pesq:.3f}")]


def intelligibility_rows(ref: np.ndarray, deg: np.ndarray, sr: int) -> Rows:
    """NCM and CSII, which, like STOI, predict intelligibility from the
    pair, for two signals of the same length.

    NCM is defined at 8 and 16 kHz only. CSII takes any rate from 8 kHz and
    gives one figure for each level of the reference, all three from one
    call. When the whole pair cannot be scored, one CSII row says why rather
    than three rows saying the same thing.
    """
    if int(sr) in QUALITY_RATES:
        rows = [("NCM", _measured(kudio.ncm, ref, deg, sr, "{:.3f}"))]
    else:
        rows = [("NCM", f"not defined at {sr} Hz (8 or 16 kHz only)")]
    if int(sr) < CSII_MIN_RATE:
        return rows + [("CSII", f"not defined at {sr} Hz (8 kHz or more)")]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scored = kudio.csii(ref, deg, sr)
    except Exception as e:  # noqa: BLE001
        log.debug("csii failed on the pair: %s", e)
        return rows + [("CSII", "could not be computed for this pair")]
    said = " ".join(str(w.message) for w in caught)

    def level_why(level: str) -> Optional[str]:
        return next((why for clue, why in _CSII_LEVEL_WHY if clue.format(level) in said),
                    None)

    if not any(np.isfinite(v) for v in scored) and not any(
            level_why(level) for _, level in CSII_LEVELS):
        return rows + [("CSII", _why_none(caught))]
    for label, level in CSII_LEVELS:
        value = getattr(scored, level)
        rows.append((label, f"{value:.3f}" if np.isfinite(value)
                     else level_why(level) or _why_none(caught)))
    return rows


def with_reference(sr: int, ref: np.ndarray, deg: np.ndarray, pesq_scale: str = "lqo",
                   available: Optional[Dict[str, bool]] = None,
                   whole: Optional[Tuple[np.ndarray, np.ndarray]] = None
                   ) -> Tuple[Sequence, Rows]:
    """What the evaluation runs as a job: PESQ, STOI and SDR as
    `kudio.eval_metrics` gives them, then the rows of `quality_rows`,
    `composite_rows` and `intelligibility_rows`.

    *ref* and *deg* are the pair lined up and cut to one length. PESQ finds
    the delay itself and works over both as they are, so where that changed
    the pair, it is scored on *whole*, the two as loaded, as `kudio.pesq`
    takes them.
    """
    available = available or {}
    pesq, stoi, sdr = kudio.eval_metrics(sr, ref, deg, pesq_scale=pesq_scale)
    inside = None
    if (_changed(whole, ref, deg) and available.get("PESQ", False)
            and int(sr) in QUALITY_RATES):
        pesq = _whole_pesq(whole, sr, "auto", pesq_scale)
        # the row's own score where it is the one the composite takes: mode
        # 'auto' is wideband at 16 kHz and narrowband at 8 kHz
        if pesq_scale == _COMPOSITE_VARIANT[int(sr)][1]:
            inside = pesq
    return (pesq, stoi, sdr), (quality_rows(ref, deg, sr)
                               + composite_rows(ref, deg, sr, available, whole, inside)
                               + intelligibility_rows(ref, deg, sr))


#: the PESQ kudio.composite takes by default, as (mode, scale), by rate
_COMPOSITE_VARIANT = {16000: ("wb", "lqo"), 8000: ("nb", "raw")}


def _changed(whole, ref: np.ndarray, deg: np.ndarray) -> bool:
    """Whether lining up or the shorter length changed the pair as loaded:
    either shortens a side whenever it does anything."""
    return whole is not None and (len(whole[0]), len(whole[1])) != (len(ref), len(deg))


def _whole_pesq(whole, sr: int, mode: str, scale: str) -> Optional[float]:
    """PESQ on the pair as loaded, or None where it has no score."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            value = float(kudio.pesq(*whole, sr, mode=mode, scale=scale))
    except Exception as e:  # noqa: BLE001
        log.debug("PESQ failed on the pair as loaded: %s", e)
        return None
    return value if np.isfinite(value) else None


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
    """PESQ, STOI and SDR as `with_reference` gives them, or why each is
    missing."""
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


__all__ = ["ALIGN_TIP", "BLIND_TIP", "CSII_LEVELS", "CSII_MIN_RATE", "DNSMOS_TIP",
           "INSTALL_HINT", "NO_FIGURE", "NOTHING_WRONG", "PESQ_SCALES", "PESQ_TIP",
           "QUALITY_RATES", "REFERENCES", "availability", "blind_rows", "composite_rows",
           "dnsmos_rows", "inspect_rows", "installed", "intelligibility_rows", "line_up",
           "optional_rows", "pesq_scale", "quality_rows", "reference_rows", "why_missing",
           "with_reference"]
