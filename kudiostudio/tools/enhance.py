# -*- coding: utf-8 -*-
"""Denoising -- statistical DSP from `kudio.enhance`, plus learned models.

The seven statistical methods (`spectral subtraction` ... `OM-LSA`) are the
same three decisions in different combinations: estimate the noise, estimate
the a priori SNR, turn it into a gain. Their parameters are described by
`kudio.METHODS`, which ships the range, default, step, unit and a sentence of
help with each, so the panel restates none of it.

The noise estimator is a separate choice, and usually the bigger of the two:
the same gain rule can vary by several dB of SI-SDR depending on how the
noise was measured.

Learned models come from the optional `kudioenhance` package and are loaded
from a run directory it produced. They are PyTorch networks, and PyTorch is
imported only when one is actually used.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Dict, Mapping, NamedTuple, Optional, Tuple

import numpy as np

import kudio

WAVELETS = ("db2", "db4", "db8", "sym4", "coif3", "haar")

TRAD, WAVELET, LEARNED = "trad", "wavelet", "learned"

#: n_fft offered for the statistical methods. Longer frames resolve the noise
#: better and smear transients more; 512 at 16 kHz is 32 ms, the usual choice.
FFT_SIZES = ("256", "512", "1024", "2048")

#: the methods that are not kudio.METHODS, as the method list names them
OTHER_METHODS = (
    ("trad_enhance (fixed spectral subtraction)", TRAD),
    ("wavelet_low_pass_filter", WAVELET),
    ("learned model (kudioenhance)", LEARNED),
)

INSTALL_HINT = ('Learned models need the kudioenhance package and PyTorch: '
                'pip install "kudiostudio[enhance]" installs both')

N_FFT_TIP = "Analysis frame. Longer frames resolve the noise better and smear transients more."
RESAMPLE_TIP = ("The clip is resampled to the rate the model was trained at, then "
                "the result is resampled back, so the session keeps its own rate.")
INSTALLED_NOTE = ("kudioenhance is installed — point 'run_dir' at a directory it "
                  "produced (it holds the model, its normalisation statistics and "
                  "the STFT geometry they were trained with).")


def kudioenhance_available() -> bool:
    """Can learned models run here? Checked without importing PyTorch.

    kudioenhance imports torch only when a model is built or loaded, so the
    package importing says nothing about the framework -- an install made
    with ``--no-deps`` has the one without the other, and would fail at the
    first Denoise. Both are looked up, neither is imported.
    """
    return all(importlib.util.find_spec(name) is not None
               for name in ("kudioenhance", "torch"))


# --------------------------------------------------------- learned models

def _model_paths(run_dir) -> Tuple[Path, ...]:
    """The files of a run that `Enhancer.load` reads: the config, the
    normalisation statistics and the checkpoint, in name order."""
    run = Path(run_dir)
    return tuple(sorted({run / "config.yaml", run / "stats.npz", *run.glob("*.pt")}))


def model_fingerprint(run_dir) -> Optional[tuple]:
    """Name, size, time and inode of each model file; ``None`` if one is gone.

    Cheap -- a stat per file -- and it moves whenever the run is trained
    again, since kudioenhance writes a checkpoint by replacing it.
    """
    found = []
    for path in _model_paths(run_dir):
        try:
            st = path.stat()
        except OSError:
            return None
        found.append((path.name, st.st_size, st.st_mtime_ns, st.st_ino))
    return tuple(found)


class ModelState(NamedTuple):
    """What a run's model files held: their names, and one SHA-256 over all
    of them in that order."""
    names: Tuple[str, ...]
    digest: str


#: digests already taken, by run directory and fingerprint: a checkpoint is
#: megabytes, and Denoise is pressed far more often than a model is trained
_STATES: Dict[tuple, ModelState] = {}


def model_state(run_dir) -> Optional[ModelState]:
    """The run's model as it is on disk now, or ``None`` when it cannot be
    read -- in which case the load that follows fails with the reason."""
    fingerprint = model_fingerprint(run_dir)
    if fingerprint is None:
        return None
    key = (str(Path(run_dir).absolute()), fingerprint)
    state = _STATES.get(key)
    if state is None:
        digest = hashlib.sha256()
        try:
            for path in _model_paths(run_dir):
                with open(path, "rb") as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(block)
        except OSError:
            return None
        state = _STATES[key] = ModelState(
            tuple(name for name, *_ in fingerprint), digest.hexdigest())
    return state


def learned_code(run_dir: str, state: Optional[ModelState] = None) -> str:
    """What `LearnedModel.enhance` does, as lines of a script.

    They leave the denoised audio in ``out``. Compare's learned row is the
    same computation, so both record it from here. The run directory goes in
    as an absolute path: a script runs from wherever it is run, not from where
    KudioStudio was started.

    With *state* -- the model files as they were when the edit ran -- the code
    checks them first. A run directory trained again since holds a different
    model under the same name, and the script would make different audio
    without a word.
    """
    where = str(Path(run_dir).absolute())
    lines = ["from kudioenhance.inference import Enhancer"]
    if state is not None:
        lines = [
            "import hashlib",
            "from pathlib import Path",
            *lines,
            f"run_dir = Path({where!r})",
            "model = hashlib.sha256()",
            f"for name in {list(state.names)!r}:",
            "    model.update((run_dir / name).read_bytes())",
            f"if model.hexdigest() != {state.digest!r}:",
            "    raise RuntimeError(f'{run_dir} has been trained again since "
            "this edit; its model is not the one that made it')",
            "enhancer = Enhancer.load(run_dir)",
        ]
    else:
        lines.append(f"enhancer = Enhancer.load({where!r})")
    return "\n".join(lines + [
        "model_sr = enhancer.cfg.audio.sr",
        "if sr == model_sr:",
        "    out = enhancer.enhance(y)",
        "else:  # in at the model's rate, back out at the clip's",
        "    out = kudio.resample(enhancer.enhance(kudio.resample(y, sr, model_sr)),",
        "                         model_sr, sr)",
        "    out = out[:len(y)] if len(out) >= len(y) else np.pad(",
        "        out, (0, len(y) - len(out)), mode='edge')",
    ])


class LearnedModel:
    """A kudioenhance run's model, loaded once and kept for the next call.

    Loading means importing PyTorch and reading a checkpoint, seconds every
    time, so the panels that run learned models keep the last one here and
    hand their job this object's `enhance`.

    Kept for the run directory *as it is on disk*, not for its name: a run
    trained again into the same directory is a different model, and a cache
    keyed on the path would go on denoising with the old weights while the
    history records a load of the new ones.
    """

    def __init__(self) -> None:
        self.enhancer = None
        self.run_dir: Optional[str] = None
        #: `model_fingerprint` of the files the kept model was read from
        self.fingerprint: Optional[tuple] = None

    def forget(self) -> None:
        """Drop the model: a different run directory means a different one."""
        self.enhancer, self.run_dir, self.fingerprint = None, None, None

    def load(self, run_dir: str):
        """The Enhancer for *run_dir*, read from disk unless it is the one
        kept. Called on a job's thread: importing PyTorch and reading a
        checkpoint takes seconds."""
        # read once into locals: the window may forget the model meanwhile
        enhancer, loaded_from, kept = self.enhancer, self.run_dir, self.fingerprint
        # taken before the load, so files that change during it look changed
        # next time rather than looking like what was loaded
        now = model_fingerprint(run_dir)
        if enhancer is not None and loaded_from == run_dir and kept == now:
            return enhancer
        from kudioenhance.inference import Enhancer
        enhancer = Enhancer.load(run_dir)
        self.enhancer, self.run_dir, self.fingerprint = enhancer, run_dir, now
        return enhancer

    def enhance(self, y: np.ndarray, sr: int, run_dir: str,
                allow_resample: bool = True) -> np.ndarray:
        """Denoise *y*, sampled at *sr*, with the model from *run_dir*.

        A clip at another rate goes in at the model's and comes back out at
        its own, the same length, so splicing the result into the session
        never changes its rate. `learned_code` writes this out as code.
        """
        enhancer = self.load(run_dir)
        model_sr = enhancer.cfg.audio.sr
        if sr == model_sr:
            return enhancer.enhance(y)
        if not allow_resample:
            raise ValueError(
                f"this model was trained at {model_sr} Hz but the clip is "
                f"{sr} Hz — enable resampling, or convert it in the Batch panel")
        enhanced = enhancer.enhance(kudio.resample(y, sr, model_sr))
        out = kudio.resample(enhanced, model_sr, sr)
        return out[:len(y)] if len(out) >= len(y) else np.pad(
            out, (0, len(y) - len(out)), mode="edge")


# ------------------------------------------------------------ the methods

STATISTICAL = tuple(kudio.METHODS)

#: the estimator the list starts on: the first kudio names
DEFAULT_NOISE = next(iter(kudio.NOISE_ESTIMATORS))


def schema() -> Dict:
    """The methods, their parameters and the noise estimators, for the
    page to build its controls from."""
    methods = []
    for name in STATISTICAL:
        spec = kudio.METHODS[name]
        methods.append({
            "name": name, "label": spec.label,
            "tip": spec.summary + (f"\n\n{spec.reference}" if spec.reference else ""),
            "params": [{"name": p.name, "label": p.label, "kind": "float",
                        "default": p.default, "min": p.low, "max": p.high,
                        "step": p.step, "decimals": p.decimals, "suffix": p.suffix,
                        "tip": p.help} for p in spec.params],
        })
    return {"statistical": methods,
            "others": [{"name": key, "label": text} for text, key in OTHER_METHODS],
            "noise": [{"name": n, "tip": s} for n, s in kudio.NOISE_ESTIMATORS.items()],
            "fft_sizes": list(FFT_SIZES), "wavelets": list(WAVELETS),
            "n_fft_tip": N_FFT_TIP, "resample_tip": RESAMPLE_TIP}


def method_params(method: str, values: Mapping) -> Dict[str, float]:
    """A statistical method's parameters from *values*, defaults filled in,
    rounded and bounded as its controls hold them."""
    out = {}
    for p in kudio.METHODS[method].params:
        v = values.get(p.name, p.default)
        out[p.name] = round(min(max(float(v), p.low), p.high), p.decimals)
    return out


def call_text(method: str, values: Mapping) -> str:
    """The call the controls describe, as the panel shows it.

    *values*: noise, n_fft and the method's parameters for a statistical
    method; thres and wavelet for the wavelet filter; run_dir for a model.
    """
    if method in STATISTICAL:
        arguments = "".join(f", {k}={v!r}" for k, v in method_params(method, values).items())
        return (f"kudio.spectral_enhance(y, sr, {method!r}, "
                f"noise={values.get('noise', DEFAULT_NOISE)!r}, "
                f"n_fft={int(values.get('n_fft', 512))}{arguments})")
    if method == WAVELET:
        return (f"kudio.wavelet_low_pass_filter(y, thres={float(values.get('thres', 0.8))}, "
                f"wavelet={values.get('wavelet', WAVELETS[0])!r})")
    if method == LEARNED:
        return f"kudioenhance.Enhancer.load({values.get('run_dir', '')!r}).enhance(y)"
    return "kudio.trad_enhance(y, sr)"


def operation(method: str, values: Mapping, y: np.ndarray, sr: int,
              model: LearnedModel, available=None):
    """``(fn, args, kwargs, label)`` for *method* applied to *y*.

    The one place Denoise and Preview get their computation from, so an
    audition is the same call on the same audio as the commit after it.
    Raises RuntimeError, with a message worth showing, when the method is
    not usable yet.
    """
    if method in STATISTICAL:
        noise = values.get("noise", DEFAULT_NOISE)
        return (kudio.spectral_enhance, (y, sr, method),
                {"noise": noise, "n_fft": int(values.get("n_fft", 512)),
                 **method_params(method, values)},
                f"{kudio.METHODS[method].label} + {noise}")
    if method == WAVELET:
        return (kudio.wavelet_low_pass_filter, (y,),
                {"thres": float(values.get("thres", 0.8)),
                 "wavelet": values.get("wavelet", WAVELETS[0])},
                "wavelet_low_pass_filter")
    if method == LEARNED:
        if not (available or kudioenhance_available)():
            raise RuntimeError(INSTALL_HINT)
        run_dir = values.get("run_dir") or ""
        if not run_dir or not Path(run_dir).is_dir():
            raise RuntimeError("Select a kudioenhance run directory first.")
        return (model.enhance, (y, sr, run_dir, bool(values.get("resample", True))), {},
                f"learned model ({Path(run_dir).name})")
    return kudio.trad_enhance, (y, sr), {}, "trad_enhance"


def script(fn, args, kwargs, model: LearnedModel) -> str:
    """The step a finished denoise commits, as code.

    Written from the same ``(fn, args, kwargs)`` the job is handed, not from
    the caption, so the history cannot describe a different call from the one
    that ran. The last line is what the commit does to the result.
    """
    if fn == model.enhance:
        call = learned_code(args[2], model_state(args[2]))
    else:
        # y and sr are the session's own; anything after them is a value
        arguments = ["y", "sr"][:len(args)] + [repr(a) for a in args[2:]]
        arguments += [f"{name}={value!r}" for name, value in kwargs.items()]
        call = f"out = kudio.{fn.__name__}({', '.join(arguments)})"
    return f"{call}\ny = np.asarray(out, dtype=np.float32)[:len(y)]"


def distance(before: np.ndarray, out: np.ndarray) -> str:
    """How far the run moved the audio -- not how good it is."""
    return (f"SNR {kudio.snr(before, out):+.2f} dB   ·   "
            f"SI-SDR {kudio.si_sdr(before, out):+.2f} dB   ·   "
            f"segSNR {kudio.segmental_snr(before, out):+.2f} dB\n"
            f"These measure the distance from the audio as it was before this "
            f"run — they are not quality scores, and a good denoise moves them "
            f"away from zero. For quality, use the Metrics panel with a clean "
            f"reference.")


__all__ = ["FFT_SIZES", "INSTALL_HINT", "LEARNED", "LearnedModel", "ModelState",
           "OTHER_METHODS", "STATISTICAL", "TRAD", "WAVELET", "WAVELETS", "call_text",
           "distance", "kudioenhance_available", "learned_code", "method_params",
           "model_fingerprint", "model_state", "operation", "schema", "script"]
