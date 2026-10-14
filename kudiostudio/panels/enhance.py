# -*- coding: utf-8 -*-
"""Denoising — statistical DSP from `kudio.enhance`, plus learned models.

The seven statistical methods (`spectral subtraction` … `OM-LSA`) are the same
three decisions in different combinations: estimate the noise, estimate the a
priori SNR, turn it into a gain. Their controls are **built from
`kudio.METHODS`** rather than written out here, so a method gaining a parameter
in kudio gains a spin box here without anyone editing this file.

The **noise estimator** is a separate choice, and usually the bigger of the
two — the same gain rule can vary by several dB of SI-SDR depending on how the
noise was measured. Choosing it separately is the point.

`wavelet_low_pass_filter` and `trad_enhance` are kept as they were. Learned
models come from the optional `kudio-enhance` package and are loaded from a run
directory it produced; they are PyTorch networks, and PyTorch is imported only
when that method is actually used, so the GUI stays light for everyone else.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
from pathlib import Path
from typing import Dict, NamedTuple, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import DirPicker, Panel
from kudiostudio.theme import ACCENT_COLOR

log = logging.getLogger(__name__)

WAVELETS = ("db2", "db4", "db8", "sym4", "coif3", "haar")

TRAD, WAVELET, LEARNED = "trad", "wavelet", "learned"

#: n_fft offered for the statistical methods. Longer frames resolve the noise
#: better and smear transients more; 512 at 16 kHz is 32 ms, the usual choice.
FFT_SIZES = ("256", "512", "1024", "2048")


def build_param_widget(param) -> QDoubleSpinBox:
    """A spin box described entirely by a :class:`kudio.Param`.

    kudio ships the range, the default, the step, the unit and a sentence of
    help with every parameter, so the panel does not restate any of it — which
    is what stops the two from drifting apart.
    """
    box = QDoubleSpinBox()
    box.setRange(param.low, param.high)
    box.setSingleStep(param.step)
    box.setDecimals(param.decimals)
    box.setValue(param.default)
    if param.suffix:
        box.setSuffix(param.suffix)
    box.setToolTip(param.help)
    return box

INSTALL_HINT = ('Learned models need the kudio-enhance package and PyTorch. '
                'kudio-enhance is not on PyPI: clone '
                'github.com/recklight/KudioEnhance and pip install -e . in it, '
                'which installs PyTorch too')


def kudio_enhance_available() -> bool:
    """Can learned models run here? Checked without importing PyTorch.

    kudio-enhance imports torch only when a model is built or loaded, so the
    package importing says nothing about the framework -- an install made
    with ``--no-deps`` has the one without the other, and would fail at the
    first Denoise. Both are looked up, neither is imported.
    """
    return all(importlib.util.find_spec(name) is not None
               for name in ("kudio_enhance", "torch"))


def _model_paths(run_dir) -> Tuple[Path, ...]:
    """The files of a run that `Enhancer.load` reads: the config, the
    normalisation statistics and the checkpoint, in name order."""
    run = Path(run_dir)
    return tuple(sorted({run / "config.yaml", run / "stats.npz",
                         *run.glob("*.pt")}))


def model_fingerprint(run_dir) -> Optional[tuple]:
    """Name, size, time and inode of each model file; ``None`` if one is gone.

    Cheap -- a stat per file -- and it moves whenever the run is trained
    again, since kudio-enhance writes a checkpoint by replacing it.
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
    """What a run's model files held: their names, and one SHA-256 over all of
    them in that order."""
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
    """What :meth:`LearnedModel.enhance` does, as lines of a script.

    They leave the denoised audio in ``out``. Compare's learned row is the same
    computation, so both panels record it from here. The run directory goes in
    as an absolute path: a script runs from wherever it is run, not from where
    KudioStudio was started.

    With *state* -- the model files as they were when the edit ran -- the code
    checks them first. A run directory trained again since holds a different
    model under the same name, and the script would make different audio
    without a word.
    """
    where = str(Path(run_dir).absolute())
    lines = ["from kudio_enhance.inference import Enhancer"]
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
    """A kudio-enhance run's model, loaded once and kept for the next call.

    Loading means importing PyTorch and reading a checkpoint, seconds every
    time, so both panels that run learned models keep the last one here and
    hand their worker this object's :meth:`enhance`.

    Kept for the run directory *as it is on disk*, not for its name: a run
    trained again into the same directory is a different model, and a cache
    keyed on the path went on denoising with the old weights while the history
    recorded a load of the new ones.
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
        """The Enhancer for *run_dir*, read from disk unless it is the one kept.

        Called on the worker thread: importing PyTorch and reading a checkpoint
        takes seconds and must not block the UI.
        """
        # read once into locals: the GUI thread may forget the model meanwhile
        enhancer, loaded_from, kept = self.enhancer, self.run_dir, self.fingerprint
        # taken before the load, so files that change during it look changed
        # next time rather than looking like what was loaded
        now = model_fingerprint(run_dir)
        if enhancer is not None and loaded_from == run_dir and kept == now:
            return enhancer
        from kudio_enhance.inference import Enhancer
        enhancer = Enhancer.load(run_dir)
        self.enhancer, self.run_dir, self.fingerprint = enhancer, run_dir, now
        return enhancer

    def enhance(self, y: np.ndarray, sr: int, run_dir: str,
                allow_resample: bool = True) -> np.ndarray:
        """Denoise *y*, sampled at *sr*, with the model from *run_dir*.

        A clip at another rate goes in at the model's and comes back out at
        its own, the same length, so splicing the result into the session
        never changes its rate. :func:`learned_code` writes this out as code.
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


class EnhancePanel(Panel):
    """Denoise the current clip and score the result against the input."""

    title = "Enhance"

    #: statistical methods come from kudio, in the order kudio lists them
    STATISTICAL = tuple(kudio.METHODS)

    LEGACY_METHODS = (
        ("trad_enhance (fixed spectral subtraction)", TRAD),
        ("wavelet_low_pass_filter", WAVELET),
        ("learned model (kudio-enhance)", LEARNED),
    )

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        #: kept between runs on the same directory
        self._model = LearnedModel()
        self._label = ""
        #: the running job as code, and the audio it was started on
        self._code: Optional[str] = None
        self._ran_on: Optional[np.ndarray] = None
        #: {method: {param_name: spin box}}, built from kudio.METHODS
        self._params: dict = {}

        self.method = QComboBox()
        for name in self.STATISTICAL:
            spec = kudio.METHODS[name]
            self.method.addItem(f"{spec.label}", name)
            index = self.method.count() - 1
            tip = spec.summary + (f"\n\n{spec.reference}" if spec.reference else "")
            self.method.setItemData(index, tip, Qt.ToolTipRole)
        self.method.insertSeparator(self.method.count())
        for text, key in self.LEGACY_METHODS:
            self.method.addItem(text, key)

        self.noise = QComboBox()
        for name, summary in kudio.NOISE_ESTIMATORS.items():
            self.noise.addItem(name, name)
            self.noise.setItemData(self.noise.count() - 1, summary, Qt.ToolTipRole)
        self.noise_hint = self.hint("")

        self.n_fft = QComboBox()
        self.n_fft.addItems(FFT_SIZES)
        self.n_fft.setCurrentText("512")
        self.n_fft.setToolTip(
            "Analysis frame. Longer frames resolve the noise better and smear "
            "transients more.")

        # one page of spin boxes per statistical method, described by kudio
        self.stat_params = QStackedWidget()
        for name in self.STATISTICAL:
            page = QWidget()
            page_form = QFormLayout(page)
            page_form.setContentsMargins(0, 0, 0, 0)
            self._params[name] = {}
            for param in kudio.METHODS[name].params:
                box = build_param_widget(param)
                box.valueChanged.connect(self._update_call)
                page_form.addRow(param.label, box)
                self._params[name][param.name] = box
            self.stat_params.addWidget(page)

        self.thres = QDoubleSpinBox()
        self.thres.setRange(0.0, 5.0)
        self.thres.setSingleStep(0.05)
        self.thres.setValue(0.8)
        self.wavelet = QComboBox()
        self.wavelet.addItems(WAVELETS)
        self.run_dir = DirPicker("run directory from kudio-enhance, e.g. runs/exp1",
                                 caption="Select a kudio-enhance run directory")
        self.auto_resample = QCheckBox("resample to the model's rate if they differ")
        self.auto_resample.setChecked(True)
        self.auto_resample.setToolTip(
            "The clip is resampled to the rate the model was trained at, then "
            "the result is resampled back, so the session keeps its own rate.")

        self.preview_button = QPushButton("♪  Preview")
        self.preview_button.setCheckable(True)
        self.preview_button.setToolTip(
            "Hear the denoise without committing it. Runs on the whole clip,\n"
            "exactly as Denoise will, then loops the selected part.")
        self.preview_button.setStyleSheet(
            f"QPushButton:checked {{ background: {ACCENT_COLOR}; color: #1b1d21;"
            f" font-weight: bold; }}")
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(400)   # denoising is not free
        self._preview_timer.timeout.connect(self._render_preview)

        self.run_button = QPushButton("Denoise")
        self.undo_button = QPushButton("Undo")
        self.call = self.call_label()
        self.result = QLabel("—")
        self.result.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Method", self.method)
        self._rows = {
            "statistical": [("noise estimate", self.noise),
                            ("", self.noise_hint),
                            ("n_fft", self.n_fft),
                            ("", self.stat_params)],
            WAVELET: [("thres", self.thres), ("wavelet", self.wavelet)],
            LEARNED: [("run_dir", self.run_dir),
                      ("", self.auto_resample)],
        }
        for rows in self._rows.values():
            for label, widget in rows:
                form.addRow(label, widget)
        self._form = form

        buttons = QHBoxLayout()
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.undo_button)
        buttons.addStretch(1)

        box = QGroupBox("Denoise")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.call)
        box_layout.addLayout(buttons)

        scores = QGroupBox("How much it changed the input")
        scores_layout = QVBoxLayout(scores)
        scores_layout.addWidget(self.result)

        self.availability = self.hint("")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(scores)
        layout.addWidget(self.hint(
            "Denoising always processes the whole clip — noise estimation "
            "needs the full signal. Preview does the same, then loops whatever "
            "is selected, and commits nothing."))
        layout.addWidget(self.availability)
        layout.addStretch(1)

        self.method.currentIndexChanged.connect(self._sync_params)
        self.noise.currentIndexChanged.connect(self._on_noise_changed)
        self.n_fft.currentIndexChanged.connect(self._update_call)
        self.thres.valueChanged.connect(self._update_call)
        self.wavelet.currentIndexChanged.connect(self._update_call)
        self.run_dir.edit.textChanged.connect(self._on_run_dir_changed)
        self.preview_button.toggled.connect(self._on_preview_toggled)
        self.run_button.clicked.connect(self._run)
        self.undo_button.clicked.connect(self._undo)
        self.jobs.busy_changed.connect(self._set_busy)
        self._sync_params()
        self._refresh_availability()

    # ------------------------------------------------------------------- ui

    def _method(self) -> str:
        return self.method.currentData()

    def _is_statistical(self) -> bool:
        return self._method() in self.STATISTICAL

    def _sync_params(self) -> None:
        """Grey out the rows that do not belong to the selected method."""
        active = self._method()
        if active is None:                  # the separator row
            return
        group = "statistical" if self._is_statistical() else active
        if self._is_statistical():
            self.stat_params.setCurrentIndex(self.STATISTICAL.index(active))
        for key, rows in self._rows.items():
            for _, widget in rows:
                enabled = key == group
                widget.setEnabled(enabled)
                row_label = self._form.labelForField(widget)
                if row_label is not None:
                    row_label.setEnabled(enabled)
        self._on_noise_changed()

    def _on_noise_changed(self) -> None:
        self.noise_hint.setText(
            kudio.NOISE_ESTIMATORS.get(self.noise.currentData(), ""))
        self._update_call()

    def _statistical_params(self) -> dict:
        return {name: box.value()
                for name, box in self._params[self._method()].items()}

    def _refresh_availability(self) -> None:
        if kudio_enhance_available():
            self.availability.setText(
                "kudio-enhance is installed — point 'run_dir' at a directory "
                "it produced (it holds the model, its normalisation statistics "
                "and the STFT geometry they were trained with).")
        else:
            self.availability.setText(INSTALL_HINT)

    def _on_run_dir_changed(self) -> None:
        # a different run means a different model; drop the cached one
        if self.run_dir.path() != self._model.run_dir:
            self._model.forget()
        self._update_call()

    # --------------------------------------------------------------- preview

    def _on_preview_toggled(self, on: bool) -> None:
        if not on:
            self._preview_timer.stop()
            self.preview_stopped.emit()
            return
        if not self.session.has_audio:
            self.report("Load an audio file first.")
            self.cancel_preview()
            return
        self._render_preview()

    def _render_preview(self) -> None:
        """Denoise the whole clip on the worker; play it when it lands.

        Unlike the Effects panel this cannot re-render on every keystroke --
        a denoise is seconds, not milliseconds -- so requests are debounced,
        and one arriving while a job is running simply waits for the next tick.
        """
        if not (self.preview_button.isChecked() and self.session.has_audio):
            return
        if self.jobs.busy:
            self._preview_timer.start()
            return
        y = np.asarray(self.session.y, dtype=np.float32)
        try:
            fn, args, kwargs, label = self._operation(y, self.session.sr)
        except RuntimeError as e:
            self.report(str(e))
            self.cancel_preview()
            self.preview_stopped.emit()
            return

        self.report(f"Rendering a preview of {label} …")
        self.jobs.run(fn, *args, on_done=self._preview_done,
                      on_failed=self._preview_failed, **kwargs)

    def _preview_done(self, out: np.ndarray) -> None:
        if not self.preview_button.isChecked():
            return                          # stopped while it was rendering
        out = np.asarray(out, dtype=np.float32)[:len(self.session.y)]
        sr = self.session.sr
        selection = self.session.selection()
        if selection is not None:
            start, end = selection
            out = out[int(start * sr):int(end * sr)]
        if len(out) == 0:
            self.cancel_preview()
            return
        self.report("Previewing — nothing has been committed.")
        self.preview_ready.emit(out, sr)

    def _preview_failed(self, message: str) -> None:
        self.report(f"Preview failed — {message}")
        self.cancel_preview()
        self.preview_stopped.emit()

    def cancel_preview(self) -> None:
        self._preview_timer.stop()
        if self.preview_button.isChecked():
            self.preview_button.blockSignals(True)
            self.preview_button.setChecked(False)
            self.preview_button.blockSignals(False)

    def _update_call(self) -> None:
        if self.preview_button.isChecked():
            self._preview_timer.start()     # parameters moved: re-render
        method = self._method()
        if method is None:
            return
        if self._is_statistical():
            arguments = "".join(f", {k}={v!r}"
                                for k, v in self._statistical_params().items())
            self.call.setText(
                f"kudio.spectral_enhance(y, sr, {method!r}, "
                f"noise={self.noise.currentData()!r}, "
                f"n_fft={self.n_fft.currentText()}{arguments})")
            return
        if method == WAVELET:
            self.call.setText(
                f"kudio.wavelet_low_pass_filter(y, thres={self.thres.value()}, "
                f"wavelet={self.wavelet.currentText()!r})")
        elif method == LEARNED:
            self.call.setText(
                f"kudio_enhance.Enhancer.load({self.run_dir.path()!r}).enhance(y)")
        else:
            self.call.setText("kudio.trad_enhance(y, sr)")

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("Denoising…" if busy else "Denoise")

    # ------------------------------------------------------------------ run

    def _operation(self, y: np.ndarray, sr: int):
        """``(fn, args, kwargs, label)`` for the current method applied to *y*.

        Shared by Denoise and Preview, so an audition is the same computation
        on the same audio as the commit that follows it. Raises with a message
        worth showing when the method is not usable yet.
        """
        method = self._method()
        if method in self.STATISTICAL:
            return (kudio.spectral_enhance, (y, sr, method),
                    {"noise": self.noise.currentData(),
                     "n_fft": int(self.n_fft.currentText()),
                     **self._statistical_params()},
                    f"{kudio.METHODS[method].label} + {self.noise.currentData()}")
        if method == WAVELET:
            return (kudio.wavelet_low_pass_filter, (y,),
                    {"thres": self.thres.value(),
                     "wavelet": self.wavelet.currentText()},
                    "wavelet_low_pass_filter")
        if method == LEARNED:
            if not kudio_enhance_available():
                raise RuntimeError(INSTALL_HINT)
            run_dir = self.run_dir.path()
            if not run_dir or not Path(run_dir).is_dir():
                raise RuntimeError("Select a kudio-enhance run directory first.")
            return (self._model.enhance,
                    (y, sr, run_dir, self.auto_resample.isChecked()), {},
                    f"learned model ({Path(run_dir).name})")
        return kudio.trad_enhance, (y, sr), {}, "trad_enhance"

    def _script(self, fn, args, kwargs) -> str:
        """The step :meth:`_finished` will commit, as code.

        Written from the same ``(fn, args, kwargs)`` the worker is handed, not
        from the caption, so the history cannot describe a different call from
        the one that ran. The last line is what `_finished` does to the result.
        """
        if fn == self._model.enhance:
            call = learned_code(args[2], model_state(args[2]))
        else:
            # y and sr are the session's own; anything after them is a value
            arguments = ["y", "sr"][:len(args)] + [repr(a) for a in args[2:]]
            arguments += [f"{name}={value!r}" for name, value in kwargs.items()]
            call = f"out = kudio.{fn.__name__}({', '.join(arguments)})"
        return f"{call}\ny = np.asarray(out, dtype=np.float32)[:len(y)]"

    def _run(self) -> None:
        if not self.require_audio():
            return
        self.cancel_preview()
        # Refused before anything below is touched: the job already running
        # commits through the same attributes, and would otherwise be
        # recorded under this run's label and code.
        if self.jobs.busy:
            self.report("A denoising job is already running.")
            return
        y = np.asarray(self.session.y, dtype=np.float32)
        self._before = y.copy()
        try:
            fn, args, kwargs, label = self._operation(y, self.session.sr)
        except RuntimeError as e:
            self.report(str(e))
            return

        self._label = label
        # taken now: the controls can move while the job runs
        self._code = self._script(fn, args, kwargs)
        self._ran_on = self.session.y
        self.report(f"Running {label} …")
        started = self.jobs.run(fn, *args, on_done=self._finished,
                                on_failed=self._failed, **kwargs)
        if not started:
            self.report("A denoising job is already running.")

    def _finished(self, out: np.ndarray) -> None:
        out = np.asarray(out, dtype=np.float32)
        # wavelet reconstruction can return one extra sample on odd lengths
        n = min(len(out), len(self._before))
        out, before = out[:n], self._before[:n]
        # The code repeats this run on the audio the history has reached. If
        # that audio was edited while the job ran, this result came from
        # something the history no longer holds, and no code says what.
        code = self._code if self.session.y is self._ran_on else None
        self.session.set_audio(out, self.session.sr, undo_label=self._label,
                               keep_original=True, code=code)
        self.result.setText(
            f"SNR {kudio.snr(before, out):+.2f} dB   ·   "
            f"SI-SDR {kudio.si_sdr(before, out):+.2f} dB   ·   "
            f"segSNR {kudio.segmental_snr(before, out):+.2f} dB\n"
            f"These measure the distance from the audio as it was before this "
            f"run — they are not quality scores, and a good denoise moves them "
            f"away from zero. For quality, use the Metrics panel with a clean "
            f"reference.")
        self.report(f"{self._label} finished.")

    def _failed(self, message: str) -> None:
        self.result.setText("—")
        self.report(f"{self._label} failed — {message}")

    def _undo(self) -> None:
        if not self.session.undo():
            self.report("Nothing to undo.")
