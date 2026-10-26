# -*- coding: utf-8 -*-
"""Build a noisy dataset -- `kudio.Synthesizer`.

Mixes every clean file with every noise file at the requested SNRs, which is
how you generate paired training data for a denoising model. The whole chain
between a talker and a file is available::

    talker -> room (rt60) -> + noise (snr_ratio) -> link (channel) -> file

`snr_ratio` and `rt60` are swept and multiply the output count. `channel` is
not: a corpus is recorded over a phone line or it is not, so it is constant
across the run and lives in the manifest rather than in the file names.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple, Union

import kudio

#: What a mixture can be written as. PCM_16 clips at full scale; FLOAT keeps
#: whatever is past it.
SUBTYPES = ("PCM_16", "PCM_24", "FLOAT")

MODES = ("regular", "inc")

#: kudio's own preset names, `none` first so the default is no link
LINKS = ("none", "telephone", "telephone_a", "voip", "mu_law", "a_law")

SNR_TIP = ("Signal-to-noise ratios in dB, one corpus axis per value.\n\n"
           "Fractional values are allowed: 2.5 is written `_2p5dB`, because\n"
           "a decimal point in a file name reads as the start of the suffix.\n"
           "A whole number keeps its plain name, so `0` is still `_0dB`.")
RT60_TIP = ("Reverberation times in seconds. A fourth axis: every output the\n"
            "mode writes is written once per room, so the count multiplies by\n"
            "however many you give, in regular mode as in inc.\n\n"
            "The room acts on the speech before the noise is mixed in, which\n"
            "is the order a microphone meets them — so the SNR is measured\n"
            "against the reverberant speech. The clean file stays the target,\n"
            "so a model trained on this is asked to undo both.")
DRR_TIP = ("Direct-to-reverberant ratio — distance, in a readable unit.\n"
           "+10 and above is a close microphone; 0 is a metre or two;\n"
           "negative is the far end of a hall.")
SUBTYPE_TIP = ("What the mixtures are written as.\n\n"
               "PCM_16 is the usual choice and clips anything past full scale —\n"
               "kudio says how many files and how far over, and they are listed\n"
               "under the result.\n\n"
               "FLOAT keeps values past 1.0 instead of clipping them, which is\n"
               "what you want when a loud talker at a low SNR would otherwise\n"
               "lose its peaks. PCM_24 still clips, with a finer step.")
CHANNEL_TIP = ("Send every mixture down a link — the third kind of degradation,\n"
               "and the one that is neither additive nor convolutive.\n\n"
               "telephone: 300–3400 Hz at 8 kHz through G.711\n"
               "voip: G.711 with 5% of packets lost in runs of 4, held\n"
               "mu_law / a_law: the companding without the band-limiting\n\n"
               "Applied to the mixture, noise and all, because a wire carries\n"
               "whatever reached the microphone. Not an axis: it is constant\n"
               "for the run and is recorded in manifest.json, not in the names.")
TARGETS_TIP = ("Write the reverberant-but-clean signal beside each mixture.\n\n"
               "The clean file is the dry target, so a model trained on it is\n"
               "asked to undo the room as well as the noise. This is the same\n"
               "speech with the room left on, for training that should remove\n"
               "the noise and leave the room — a different and easier problem.\n\n"
               "Needs rt60: with no room the clean file already is that signal.")
MODE_TIP = "regular: one output per clean file\ninc: every clean × noise × SNR combination"
OVERWRITE_TIP = ("syn(overwrite=True): if out_path holds anything at all, the whole\n"
                 "folder is deleted before the run, files that have nothing to do\n"
                 "with the corpus included. One folder holds one run, and the\n"
                 "manifest beside it describes every file in it.\n\n"
                 "Off, a folder that is not empty is left as it is and nothing is\n"
                 "written. kudio will not empty a drive's root, or a folder that\n"
                 "holds the working directory, your home folder or an input.")
SEED_NOTE = ("Set a seed to make the mixture reproducible — the same seed gives the "
             "same noise offsets, rooms and lost packets. Which noise and SNR each "
             "clean file gets follows the file order, not the seed. Left at None, a "
             "seed is drawn and the result names it.")

#: How kudio words its clipping warning. The window says the same from
#: `SynthResult.clipped`, with what to do about it, so kudio's is not repeated.
_CLIPPED = "went past full scale"


def parse_snr(text: str) -> List[Union[int, float]]:
    """``"-5, 0, 5"`` -> ``[-5, 0, 5]``; ``"2.5, 2.7"`` -> ``[2.5, 2.7]``.

    A whole number comes back as an ``int`` and a fractional one as a
    ``float``. kudio normalises either into the same output name and the same
    manifest value, so the distinction is only so that the call shown stays
    ``[-5, 0, 5]`` rather than ``[-5.0, 0.0, 5.0]``. Never truncated: 2.5 and
    2.7 as 2 and 2 would write one file name twice, and one would overwrite
    the other.
    """
    values: List[Union[int, float]] = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value = float(chunk)
        if value != value or value in (float('inf'), float('-inf')):
            raise ValueError(f"{chunk!r} is not a usable SNR")
        values.append(int(value) if value.is_integer() else value)
    if not values:
        raise ValueError("no SNR values given")
    return values


def parse_rt60(text: str) -> Optional[List[float]]:
    """``"0.3, 0.8"`` -> ``[0.3, 0.8]``; empty -> ``None`` (no rooms).

    Separate from `parse_snr` because empty means something here: no
    reverberation at all, which is the default and has to stay reachable.
    """
    values = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            value = float(chunk)
            if value <= 0:
                raise ValueError(f"reverberation times must be positive, got {value:g}")
            values.append(value)
    return values or None


@dataclass(frozen=True)
class SynthPlan:
    """Everything one run needs, read before it starts.

    The run takes seconds to hours on a job's thread, and one that read the
    controls as it went would read them while they can still change. The
    output folder is needed twice, once to write the mixtures and again to
    put the manifest beside them; read from the form each time, a folder
    retyped mid-run would put the corpus in one place and its manifest in
    another.
    """
    clean: str
    noise: str
    out: str
    snr: Tuple[Union[int, float], ...]
    rooms: Optional[Tuple[float, ...]] = None
    drr_db: float = 0.0
    channel: Optional[str] = None
    write_targets: bool = False
    subtype: str = "PCM_16"
    mode: str = "regular"
    add_clean: bool = False
    # off unless asked: kudio's own default empties out_path, whatever is in it
    overwrite: bool = False
    seed: Optional[int] = None


class SynthResult(NamedTuple):
    """What one synthesis run produced, by name.

    `wanted` is what `syn()` was asked for and `on_disk` is what exists: two
    outputs that share a name leave one file, so the two differ exactly when
    something was overwritten.
    """

    produced: Optional[list]
    link: str
    targets: int
    wanted: int
    on_disk: int
    # Optional[...] = None rather than {} / (): a NamedTuple evaluates its
    # defaults once, so a mutable one is shared by every instance that omits it.
    clipped: Optional[dict] = None
    #: what kudio warned about, as it said it; a run kudio refused has
    #: nothing else to say why
    notices: Sequence[str] = ()
    subtype: str = "PCM_16"
    #: the folder the run wrote to, as it was when the run started
    out: str = ""
    #: the seed the run used, `Synthesizer.seed`; None when nothing ran
    seed: Optional[int] = None
    #: whether that seed was drawn for the run rather than given
    drawn: bool = False


def synthesize(plan: SynthPlan) -> SynthResult:
    """Run *plan*: mix, write the manifest beside the mixtures, count them.

    Given only the plan, so the job never touches a control -- nor holds a
    panel, which it would through a method of one.
    """
    # kudio warns about clipping, about names that overwrite each other, about
    # inputs it skipped, and about an output folder it would not empty, in
    # which case it writes nothing and the warning is the only word of why.
    # Nothing else in KudioStudio routes warnings anywhere, so without this they
    # reach stderr and the person never hears any of it.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        syx = kudio.Synthesizer(plan.clean, plan.noise, out_path=plan.out,
                                snr_ratio=list(plan.snr),
                                rt60=list(plan.rooms) if plan.rooms else None,
                                drr_db=plan.drr_db, channel=plan.channel,
                                write_targets=plan.write_targets, subtype=plan.subtype)
        produced = syx.syn(mode=plan.mode, is_add_clean=plan.add_clean,
                           overwrite=plan.overwrite, seed=plan.seed)
    notices = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    # the manifest carries the room, the link and the target, none of which
    # the four-tuples can; written beside the mixture so a training run can
    # read what it was given
    if produced:
        kudio.save_manifest(Path(plan.out) / "manifest.json", syx.manifest())

    # What `syn` returns is what was *asked for*. Two outputs that share a
    # name leave one file, so counting the request would report a corpus that
    # is not there. Count the distinct paths that exist instead, on the job's
    # thread, where walking the disk costs the window nothing.
    wanted = len(produced or [])
    paths = {Path(out) for out, *_ in (produced or [])}
    return SynthResult(produced=produced, link=syx.channel_tag,
                       targets=len(syx.targets), wanted=wanted,
                       on_disk=sum(1 for p in paths if p.is_file()),
                       clipped=dict(syx.clipped), notices=notices,
                       subtype=plan.subtype, out=plan.out,
                       seed=syx.seed, drawn=plan.seed is None and syx.seed is not None)


def link(name: str) -> Optional[str]:
    """The chosen preset, or None: `none` is a name in the list, not in the
    call, so what is shown stays copy-pasteable."""
    return None if name == "none" else name


def call_text(clean: str, noise: str, out: str, snr_text: str, rt60_text: str,
              drr_db: float, subtype: str, channel: str, write_targets: bool,
              mode: str, seed: Optional[int], add_clean: bool = False,
              overwrite: bool = False) -> str:
    try:
        snr = parse_snr(snr_text)
    except ValueError:
        snr = "…"
    try:
        rooms = parse_rt60(rt60_text)
    except ValueError:
        rooms = "…"
    extra = "" if rooms is None else f", rt60={rooms}, drr_db={drr_db}"
    if link(channel) is not None:
        extra += f", channel={link(channel)!r}"
    if write_targets:
        extra += ", write_targets=True"
    if subtype != "PCM_16":
        extra += f", subtype={subtype!r}"
    run = f"mode={mode!r}"
    if add_clean:
        run += ", is_add_clean=True"
    if not overwrite:
        run += ", overwrite=False"
    return (f"kudio.Synthesizer({clean!r}, {noise!r}, out_path={out!r}, "
            f"snr_ratio={snr}{extra}).syn({run}, seed={seed})")


def problem(clean: str, noise: str, out: str, snr_text: str, rt60_text: str,
            write_targets: bool) -> Optional[str]:
    """What stops a run from starting, in the words the panel says it, or None."""
    try:
        parse_snr(snr_text)
    except ValueError as e:
        return f"Bad SNR list — {e}"
    try:
        rooms = parse_rt60(rt60_text)
    except ValueError as e:
        return f"Bad rt60 list — {e}"
    for label, path in (("clean", clean), ("noise", noise)):
        if not path or not Path(path).is_dir():
            return f"Select a valid {label} folder."
    if not out:
        return "Select an output folder."
    if write_targets and rooms is None:
        return ("Reverberant targets need rt60 — with no room the clean file already "
                "is that signal.")
    return None


def result_notes(result: SynthResult) -> List[str]:
    """The pieces after the count on the result line, each its own sentence
    so a translation can take them one at a time."""
    notes = [] if result.on_disk == 0 else ["  ·  manifest.json beside them"]
    if result.link:
        notes.append(f"  ·  down a {result.link} link")
    if result.targets:
        notes.append(f"  ·  {result.targets} reverberant target(s) in TARGETS/")
    if result.clipped:
        worst = max(result.clipped.values())
        notes.append(f"  ·  {len(result.clipped)} clipped (worst peak {worst:.2f})")
    if result.seed is not None:
        # named whether it was given or drawn, as Batch names its rooms' seed:
        # the result says what made the corpus beside it
        notes.append(f"  ·  seed {result.seed}")
    return notes


def result_warnings(result: SynthResult) -> List[str]:
    """kudio's warnings as the result lists them, a line each: all but the
    one about clipping, which the window gives from `clipped` already."""
    return [n for n in result.notices if _CLIPPED not in n]


def result_text(result: SynthResult) -> Tuple[str, str]:
    """``(lines under the form, line for the status bar)``.

    kudio's warnings follow the count. The status line is made of them when
    kudio wrote nothing, since that is all there is to say.
    """
    told = result_warnings(result)
    line = "\n".join([f"{result.on_disk} file(s) written to {result.out}"
                      f"{''.join(result_notes(result))}", *told])

    if not result.produced and told:
        # one line in the status bar, which shows a line break as a space;
        # kept as lines so that each is translated on its own
        return line, "\n".join(told)
    if result.clipped:
        worst = max(result.clipped.values())
        over_db = 20 * math.log10(worst) if worst > 0 else 0.0
        return line, (f"{len(result.clipped)} of {result.on_disk} file(s) went past full "
                      f"scale and were clipped as {result.subtype} (worst peak {worst:.2f}, "
                      f"+{over_db:.1f} dBFS). Lower the clean level, raise the SNR, or "
                      f"write subtype='FLOAT' to keep them.")
    wanted, on_disk = result.wanted, result.on_disk
    if wanted > on_disk:
        # the only way to ask for N and get fewer is a name collision, and it
        # is worth saying out loud rather than quietly reporting N
        return line, (f"Synthesis complete — {on_disk} file(s), but {wanted} were asked "
                      f"for: {wanted - on_disk} output(s) shared a name and overwrote each "
                      f"other. Give the clashing files distinct names, or keep "
                      f"mkdir_parents on so the folder layout separates them.")
    if told:
        return line, (f"Synthesis complete — {on_disk} file(s), and {len(told)} "
                      f"warning(s) from kudio, listed under the form.")
    if result.drawn:
        return line, (f"Synthesis complete — {on_disk} file(s) from seed {result.seed}; "
                      f"the same seed writes the same corpus again.")
    return line, f"Synthesis complete — {on_disk} file(s)."


__all__ = ["LINKS", "MODES", "SUBTYPES", "SynthPlan", "SynthResult", "call_text", "link",
           "parse_rt60", "parse_snr", "problem", "result_notes", "result_text",
           "result_warnings", "synthesize"]
