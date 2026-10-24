# -*- coding: utf-8 -*-
"""Waveform effects and augmentation -- `kudio.effects`.

Each entry is one kudio function, its parameters, and `operation`, which
turns a set of values into the call text and the function that makes the
call. The text is what the panel shows and what Apply records in the
session's history, so an exported script makes the same call.

Several of these degrade rather than improve, and that is the point: a
training set needs the degradation its model will meet. `add_noise_snr` is
additive, `room` is convolutive -- a real impulse response, reproducible from
its seed -- and `telephone`, `bit_depth` and `dropouts` are the channel,
which cannot be written as arithmetic on the signal at all. `measured_room`
is the same convolution with a room somebody recorded: `room` is statistical
and has no early reflections, so where the early energy matters a measured
response is the only honest stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple

import numpy as np

import kudio
from kudiostudio.tools.params import Param, coerce_all

#: in the order the panels list them
EFFECTS = ("gain", "random_gain",
           "fade_in", "fade_out", "fade", "reverse", "remove_dc",
           "highpass", "lowpass", "bandpass", "bandstop",
           "pitch_shift", "time_stretch", "reverb", "room", "measured_room",
           "add_noise_snr", "telephone", "bit_depth", "dropouts",
           "trim_silence", "vad_trim", "split_on_silence")

#: fade shapes, in kudio's own vocabulary
FADE_SHAPES = ("linear", "cosine", "exponential")

#: nothing to audition: it reports, it does not change the audio
NOT_PREVIEWABLE = ("split_on_silence",)

AUDIO_TYPES = ("Audio (*.wav;*.flac;*.mp3;*.ogg)",)


def _f(name, label, default, lo, hi, step=0.1, decimals=2, suffix="", **kw):
    return Param(name, label, "float", default, lo, hi, step, decimals, suffix, **kw)


def _i(name, label, default, lo, hi, step=1, **kw):
    return Param(name, label, "int", default, lo, hi, step, 0, **kw)


def _shape(name="shape"):
    return Param(name, "shape", "choice", "linear", choices=FADE_SHAPES)


def _zero_phase():
    return Param("zero_phase", "", "bool", True, text="zero-phase (no time shift)")


def _note(text):
    return Param("note", "", "note", text=text)


_ROOM_SEED_TIP = ("The same seed is the same room, so a dataset built from it can\n"
                  "be rebuilt. -1 draws a new one each time.")

PARAMS: Dict[str, Tuple[Param, ...]] = {
    "gain": (_f("db", "db", 0.0, -60, 24, 0.5, 1, " dB"),),
    "random_gain": (
        _f("low", "db_range low", -6.0, -60, 24, 0.5, 1, " dB"),
        _f("high", "db_range high", 6.0, -60, 24, 0.5, 1, " dB"),
        _i("seed", "seed", -1, -1, 99999, special="None"),
    ),
    "fade_in": (_f("duration", "duration", 0.01, 0.0, 60.0, 0.01, 3, " s"), _shape()),
    "fade_out": (_f("duration", "duration", 0.01, 0.0, 60.0, 0.01, 3, " s"), _shape()),
    "fade": (
        _f("fade_in_s", "fade_in_s", 0.01, 0.0, 60.0, 0.01, 3, " s"),
        _f("fade_out_s", "fade_out_s", 0.01, 0.0, 60.0, 0.01, 3, " s"),
        _shape(),
    ),
    "reverse": (_note("Plays the selection backwards."),),
    "remove_dc": (_note("Subtracts the mean, per channel. An offset that drifts over a "
                        "long recording is a very low frequency — use highpass for that."),),
    # the cutoffs' upper bound follows the clip's Nyquist: a cutoff above it
    # is not a filter but a FeatureError, arriving at Apply instead of here
    "highpass": (_f("cutoff", "cutoff", 80.0, 1.0, 96000.0, 10.0, 1, " Hz", nyquist=True),
                 _i("order", "order", 4, 1, 10), _zero_phase()),
    "lowpass": (_f("cutoff", "cutoff", 8000.0, 1.0, 96000.0, 100.0, 1, " Hz", nyquist=True),
                _i("order", "order", 4, 1, 10), _zero_phase()),
    "bandpass": (_f("low", "low", 300.0, 1.0, 96000.0, 10.0, 1, " Hz", nyquist=True),
                 _f("high", "high", 3400.0, 1.0, 96000.0, 100.0, 1, " Hz", nyquist=True),
                 _i("order", "order", 4, 1, 10)),
    "bandstop": (_f("low", "low", 45.0, 1.0, 96000.0, 1.0, 1, " Hz", nyquist=True),
                 _f("high", "high", 55.0, 1.0, 96000.0, 1.0, 1, " Hz", nyquist=True),
                 _i("order", "order", 4, 1, 10)),
    "pitch_shift": (_f("n_steps", "n_steps", 2.0, -24, 24, 0.5, 2, " semitones"),),
    "time_stretch": (_f("rate", "rate", 1.0, 0.1, 4.0, 0.05, 2, "x"),),
    "reverb": (_f("decay", "decay", 0.3, 0.0, 0.99, 0.05, 2),
               _f("delay_ms", "delay_ms", 50.0, 1.0, 500.0, 5.0, 1, " ms")),
    "room": (
        _f("rt60", "rt60", 0.5, 0.05, 5.0, 0.05, 2, " s",
           tip="How long the tail takes to fall 60 dB. A treated studio is under\n"
               "0.3 s, a living room around 0.5, a hall over 1.5."),
        _f("drr_db", "drr_db", 0.0, -20.0, 30.0, 1.0, 1, " dB",
           tip="Direct-to-reverberant ratio — distance, in a readable unit.\n"
               "A close microphone is +10 dB or more; a metre or two away is\n"
               "around 0; the far end of a hall is negative."),
        _i("seed", "seed", 0, -1, 10_000, special="random", tip=_ROOM_SEED_TIP),
    ),
    "measured_room": (
        Param("ir", "ir", "file", "", title="Select an impulse response",
              types=AUDIO_TYPES, placeholder="pick an impulse response .wav …"),
        Param("align", "", "bool", True, text="start at the direct sound",
              tip="A measured response begins with the time the sound took to reach\n"
                  "the microphone. Left in, it delays the audio by that much, and a\n"
                  "processed selection comes back late against its neighbours.\n"
                  "This starts the response at its loudest sample, which is the\n"
                  "direct sound in any ordinary room -- where kudio.rir puts it."),
    ),
    "add_noise_snr": (
        Param("noise", "noise", "file", "", title="Select a noise file",
              types=AUDIO_TYPES, placeholder="pick a noise .wav …"),
        _f("snr_db", "snr_db", 5.0, -30, 40, 1.0, 1, " dB"),
        _i("seed", "seed", -1, -1, 99999, special="None"),
    ),
    "telephone": (Param("codec", "codec", "choice", "mu_law",
                        choices=("mu_law", "a_law", "none"),
                        tip="G.711 companding. `none` band-limits and resamples without it,\n"
                            "which separates what the line did from what the codec did."),),
    "bit_depth": (
        _i("bits", "bits", 8, 2, 24),
        Param("dither", "", "bool", False, text="dither",
              tip="Add a triangular dither of one step before rounding. It raises\n"
                  "the noise floor and decorrelates the error from the signal,\n"
                  "which is what stops quiet passages sounding granular."),
        _i("seed", "seed", 0, -1, 10_000, special="random"),
    ),
    "dropouts": (
        _f("loss", "loss", 5.0, 0.0, 100.0, 1.0, 1, " %",
           tip="Fraction of packets to drop. Achieved exactly, so a corpus\n"
               "labelled 5% carries 5%."),
        _f("packet_ms", "packet_ms", 20.0, 5.0, 200.0, 5.0, 0, " ms",
           tip="Packet length. 20 ms is G.711 over RTP. The loss rate is a\n"
               "fraction of packets, so this changes whether the damage arrives\n"
               "as many short holes or a few long ones — not how much of it."),
        # whole packets: a run of 1.5 is a mean nobody types on purpose, and
        # anything under 1 is not a run length at all
        _f("burst", "burst", 0.0, 0.0, 50.0, 1.0, 0, " pkt", special="independent",
           tip="Mean run length. Real links lose packets in runs — a fade, a\n"
               "queue overflowing — and a run is far harder to conceal than the\n"
               "same packets scattered about, because there is no recent speech\n"
               "left to repeat.\n\n"
               "`independent` is not runs of 1: independent draws land side by\n"
               "side sometimes, so they already average 1/(1-loss) — 1.05\n"
               "packets at 5%. Asking for 1 is asking for something less\n"
               "clustered than leaving it alone."),
        Param("conceal", "conceal", "choice", "silence", choices=("silence", "hold"),
              tip="`hold` repeats the last packet that arrived, the crudest\n"
                  "concealment a decoder does. Whether it measures better or worse\n"
                  "than a hole is decided by whether the repeat lands in phase —\n"
                  "an accident of the talker's pitch against the packet length —\n"
                  "so a waveform metric cannot choose a concealment strategy.\n\n"
                  "Across a burst it tiles one 20 ms packet over the whole gap,\n"
                  "and there it is unambiguously worse."),
        _i("seed", "seed", 0, -1, 10_000, special="random"),
    ),
    "trim_silence": (_f("top_db", "top_db", 30.0, 1, 90, 1.0, 1, " dB"),
                     _i("frame_length", "frame_length", 2048, 64, 16384, 64),
                     _i("hop_length", "hop_length", 512, 16, 8192, 16)),
    "vad_trim": (_f("threshold_db", "threshold_db", 8.0, 0.0, 40.0, 0.5, 1, " dB"),
                 _f("pad", "pad", 0.05, 0.0, 2.0, 0.01, 3, " s"),
                 _note("Measured against this recording's own noise floor, so it "
                       "survives a room trim_silence cannot handle.")),
    "split_on_silence": (_f("top_db", "top_db", 30.0, 1, 90, 1.0, 1, " dB"),
                         _i("frame_length", "frame_length", 2048, 64, 16384, 64),
                         _i("hop_length", "hop_length", 512, 16, 8192, 16)),
}


def values_for(name: str, values: Mapping) -> Dict:
    """*values* for effect *name*, coerced as its controls would hold them."""
    if name not in PARAMS:
        raise ValueError(f"unknown effect {name!r}")
    return coerce_all(PARAMS[name], values)


def schema() -> Dict:
    """Every effect's parameters, for the page to build from."""
    return {"effects": list(EFFECTS),
            "params": {n: [p.as_dict() for p in PARAMS[n]] for n in EFFECTS},
            "not_previewable": list(NOT_PREVIEWABLE)}


def pin_seed(value: int, pin: bool) -> Optional[int]:
    """A seed control's value. Below zero is "random": ``None`` -- or, with
    *pin*, a fresh draw.

    Apply pins it. Random still means a different result every time, but the
    call that goes into the history names the seed that was drawn, so the
    exported script repeats that result rather than rolling again.
    """
    if value >= 0:
        return int(value)
    return int(np.random.default_rng().integers(2 ** 31)) if pin else None


# -------------------------------------------------------- measured rooms

@dataclass(frozen=True)
class Response:
    """A measured impulse response, read the way the recorded call reads it."""

    path: Path
    samples: np.ndarray
    sr: int

    def direct(self) -> int:
        """The loudest sample: the direct sound in any ordinary room."""
        return int(np.argmax(np.abs(self.samples)))

    def onset(self, align: bool) -> int:
        """Where the applied response starts."""
        return self.direct() if align else 0

    def describe(self, align: bool) -> Tuple[str, bool]:
        """What the room does, measured as it will be applied, and whether
        that measurement is one to doubt.

        The response measured is the one convolved: resampled to the clip,
        downmixed, trimmed to the direct sound when asked. A hall measured at
        48 kHz and applied at 8 kHz has lost its top octaves, and the early
        decay of a response that still starts with its travel time is partly
        a measurement of the silence before it.
        """
        onset = self.onset(align)
        direct_ms = 1000.0 * self.direct() / self.sr
        head = (f"{len(self.samples) / self.sr:.2f} s at {self.sr} Hz, "
                f"direct sound at {direct_ms:.1f} ms")
        if onset:
            head += " — applied from there"
        try:
            room = kudio.rt60(self.samples[onset:], self.sr)
        except Exception as e:  # noqa: BLE001 - said, not raised
            return f"{head}\ncannot be measured — {e}", True
        # an unreliable measurement says why in its own text; the flag is so
        # that it is not read past
        return f"{head}\n{room}", not room.reliable()


def read_response(path, rate: Optional[int]) -> Tuple[Optional[Response], str]:
    """Read *path* as an impulse response at *rate* (its own when None).

    Returns ``(response, "")``, or ``(None, why)`` when the file cannot be
    read or holds nothing but silence.
    """
    path = Path(path)
    try:
        ir, sr = kudio.file_load(path, sr=rate)
    except Exception as e:  # noqa: BLE001 - said, not raised
        return None, f"Could not read {path.name} — {type(e).__name__}: {e}"
    if ir.size < 2 or not np.any(ir):
        return None, f"{path.name} is silent — there is no room in it to apply."
    return Response(path, ir, int(sr)), ""


# ------------------------------------------------------------- the call

Operation = Tuple[str, Callable[[np.ndarray, int], object]]


def operation(name: str, values: Mapping, *, exact: bool = False,
              noise: Optional[Path] = None,
              response: Optional[Response] = None) -> Operation:
    """``(call_text, fn(y, sr))`` for effect *name* with *values*.

    The text shows the call as it reads best. *exact* asks for it exactly as
    it will be made, runnable as it stands: a random seed pinned, a noise
    file or an impulse response named by its full path. That is the version
    Apply commits and records.

    *noise* is the noise file for `add_noise_snr` and *response* the loaded
    room for `measured_room`; the values' own ``noise`` and ``ir`` are only
    what the controls show.
    """
    v = values_for(name, values)

    if name == "gain":
        db = v["db"]
        return f"kudio.gain(y, db={db})", lambda y, sr: kudio.gain(y, db)

    if name == "random_gain":
        rng = (v["low"], v["high"])
        seed = pin_seed(v["seed"], exact)
        return (f"kudio.random_gain(y, db_range={rng}, seed={seed})",
                lambda y, sr: kudio.random_gain(y, db_range=rng, seed=seed))

    if name in ("fade_in", "fade_out"):
        seconds, shape = v["duration"], v["shape"]
        fn = getattr(kudio, name)
        return (f"kudio.{name}(y, sr, duration={seconds}, shape={shape!r})",
                lambda y, sr: fn(y, sr, duration=seconds, shape=shape))

    if name == "fade":
        fade_in_s, fade_out_s, shape = v["fade_in_s"], v["fade_out_s"], v["shape"]
        return (f"kudio.fade(y, sr, fade_in_s={fade_in_s}, "
                f"fade_out_s={fade_out_s}, shape={shape!r})",
                lambda y, sr: kudio.fade(y, sr, fade_in_s=fade_in_s,
                                         fade_out_s=fade_out_s, shape=shape))

    if name == "reverse":
        return "kudio.reverse(y)", lambda y, sr: kudio.reverse(y)

    if name == "remove_dc":
        return "kudio.remove_dc(y)", lambda y, sr: kudio.remove_dc(y)

    if name in ("highpass", "lowpass"):
        cutoff, order, zero_phase = v["cutoff"], v["order"], v["zero_phase"]
        fn = getattr(kudio, name)
        return (f"kudio.{name}(y, sr, cutoff={cutoff}, order={order}, "
                f"zero_phase={zero_phase})",
                lambda y, sr: fn(y, sr, cutoff=cutoff, order=order,
                                 zero_phase=zero_phase))

    if name in ("bandpass", "bandstop"):
        low, high, order = v["low"], v["high"], v["order"]
        fn = getattr(kudio, name)
        return (f"kudio.{name}(y, sr, low={low}, high={high}, order={order})",
                lambda y, sr: fn(y, sr, low=low, high=high, order=order))

    if name == "vad_trim":
        threshold, pad = v["threshold_db"], v["pad"]
        return (f"kudio.vad_trim(y, sr, threshold_db={threshold}, pad={pad})[0]",
                lambda y, sr: kudio.vad_trim(y, sr, threshold_db=threshold,
                                             pad=pad)[0])

    if name == "pitch_shift":
        steps = v["n_steps"]
        return (f"kudio.pitch_shift(y, sr, n_steps={steps})",
                lambda y, sr: kudio.pitch_shift(y, sr, n_steps=steps))

    if name == "time_stretch":
        rate = v["rate"]
        return (f"kudio.time_stretch(y, rate={rate})",
                lambda y, sr: kudio.time_stretch(y, rate=rate))

    if name == "reverb":
        decay, delay = v["decay"], v["delay_ms"]
        return (f"kudio.reverb(y, sr, decay={decay}, delay_ms={delay})",
                lambda y, sr: kudio.reverb(y, sr, decay=decay, delay_ms=delay))

    if name == "room":
        rt60, drr = v["rt60"], v["drr_db"]
        seed = pin_seed(v["seed"], exact)
        return (f"kudio.apply_rir(y, kudio.rir(sr, rt60={rt60}, "
                f"drr_db={drr}, seed={seed}))",
                lambda y, sr: kudio.apply_rir(
                    y, kudio.rir(sr, rt60=rt60, drr_db=drr, seed=seed)))

    if name == "measured_room":
        return _measured_room(response, v["align"], exact)

    if name == "telephone":
        codec = v["codec"]
        return (f"kudio.telephone(y, sr, codec={codec!r})",
                lambda y, sr: kudio.telephone(y, sr, codec=codec))

    if name == "bit_depth":
        bits, dither = v["bits"], v["dither"]
        seed = pin_seed(v["seed"], exact)
        # the seed only matters with dither, but it is passed either way, and
        # a caption that leaves out an argument is not the call
        return (f"kudio.bit_depth(y, bits={bits}, dither={dither}, seed={seed})",
                lambda y, sr: kudio.bit_depth(y, bits, dither=dither, seed=seed))

    if name == "dropouts":
        # one decimal of a percentage is three of a fraction; rounding keeps
        # 12.3 % from reaching kudio, and the caption, as 0.12300000000000001
        loss = round(v["loss"] / 100.0, 6)
        packet, conceal = v["packet_ms"], v["conceal"]
        # 0 is "independent", which is `burst=None` and not `burst=0` -- the
        # two mean different things and only one is a number kudio accepts
        burst = v["burst"] or None
        seed = pin_seed(v["seed"], exact)
        return (f"kudio.dropouts(y, sr, loss={loss!r}, packet_ms={packet!r}, "
                f"burst={burst}, conceal={conceal!r}, seed={seed})",
                lambda y, sr: kudio.dropouts(y, sr, loss=loss, packet_ms=packet,
                                             burst=burst, conceal=conceal, seed=seed))

    if name == "add_noise_snr":
        snr = v["snr_db"]
        seed = pin_seed(v["seed"], exact)
        path = Path(noise) if noise else None

        def fn(y, sr):
            if path is None:
                raise ValueError("no noise file selected")
            return kudio.add_noise_snr(y, kudio.file_load(path, sr=sr)[0],
                                       snr_db=snr, seed=seed)

        if exact and path is not None:
            # what fn does, spelled out, so the line runs anywhere
            loaded = f"kudio.file_load({str(path.absolute())!r}, sr=sr)[0]"
            return f"kudio.add_noise_snr(y, {loaded}, snr_db={snr}, seed={seed})", fn
        shown = path.name if path is not None else "…"
        return (f"kudio.add_noise_snr(y, noise={shown!r}, snr_db={snr}, "
                f"seed={seed})", fn)

    if name == "trim_silence":
        top, frame, hop = v["top_db"], v["frame_length"], v["hop_length"]
        return (f"kudio.trim_silence(y, top_db={top}, frame_length={frame}, "
                f"hop_length={hop})[0]",
                lambda y, sr: kudio.trim_silence(y, top_db=top, frame_length=frame,
                                                 hop_length=hop)[0])

    # split_on_silence
    top, frame, hop = v["top_db"], v["frame_length"], v["hop_length"]
    return (f"kudio.split_on_silence(y, top_db={top}, frame_length={frame}, "
            f"hop_length={hop})",
            lambda y, sr: kudio.split_on_silence(y, top_db=top, frame_length=frame,
                                                 hop_length=hop))


def _measured_room(response: Optional[Response], align: bool, exact: bool) -> Operation:
    """`apply_rir` with the loaded response, and the call that loads it.

    The call reads the file with ``kudio.file_load(..., sr=sr)`` -- what
    `read_response` did -- and starts it at the direct sound with a plain
    slice, so it is one expression that runs as it stands. The slice is a
    sample count at the clip's rate, which is the rate the call loads it at.
    """
    onset = response.onset(align) if response is not None else 0
    ir = None if response is None else response.samples[onset:]
    held_at = response.sr if response is not None else None

    def fn(y, sr):
        if ir is None:
            raise ValueError("no impulse response loaded")
        if sr != held_at:
            raise ValueError(f"the impulse response is at {held_at} Hz "
                             f"and the audio at {sr} Hz")
        return kudio.apply_rir(y, ir)

    if response is None:
        source = "…"
    elif exact:
        source = str(response.path.absolute())
    else:
        source = response.path.name
    load = f"kudio.file_load({source!r}, sr=sr)[0]"
    if onset:
        load += f"[{onset}:]"
    return f"kudio.apply_rir(y, {load})", fn


__all__ = ["EFFECTS", "FADE_SHAPES", "NOT_PREVIEWABLE", "PARAMS", "Response",
           "operation", "pin_seed", "read_response", "schema", "values_for"]
