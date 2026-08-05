# -*- coding: utf-8 -*-
"""Denoising — classic DSP from `kudio.enhance`, plus learned models.

`trad_enhance` (spectral subtraction with MCRA noise estimation) and
`wavelet_low_pass_filter` need nothing beyond kudio. Learned models come from
the optional `kudio-enhance` package and are loaded from a run directory it
produced; TensorFlow is imported only when that method is actually used, so the
GUI stays light for everyone else.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import DirPicker, Panel

log = logging.getLogger(__name__)

WAVELETS = ("db2", "db4", "db8", "sym4", "coif3", "haar")

TRAD, WAVELET, LEARNED = "trad", "wavelet", "learned"

INSTALL_HINT = ('Learned models need the kudio-enhance package: '
                'pip install "kudiostudio[enhance]"')


def kudio_enhance_available() -> bool:
    """Is `kudio-enhance` importable? Checked without importing TensorFlow."""
    return importlib.util.find_spec("kudio_enhance") is not None


class EnhancePanel(Panel):
    """Denoise the current clip and score the result against the input."""

    title = "Enhance"

    METHODS = (
        ("trad_enhance (spectral subtraction)", TRAD),
        ("wavelet_low_pass_filter", WAVELET),
        ("learned model (kudio-enhance)", LEARNED),
    )

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._enhancer = None
        self._enhancer_dir: Optional[str] = None
        self._label = ""

        self.method = QComboBox()
        for text, key in self.METHODS:
            self.method.addItem(text, key)

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

        self.run_button = QPushButton("Denoise")
        self.undo_button = QPushButton("Undo")
        self.call = self.call_label()
        self.result = QLabel("—")
        self.result.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Method", self.method)
        self._rows = {
            WAVELET: [("thres", self.thres), ("wavelet", self.wavelet)],
            LEARNED: [("run_dir", self.run_dir),
                      ("", self.auto_resample)],
        }
        for rows in self._rows.values():
            for label, widget in rows:
                form.addRow(label, widget)
        self._form = form

        buttons = QHBoxLayout()
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
            "needs the full signal."))
        layout.addWidget(self.availability)
        layout.addStretch(1)

        self.method.currentIndexChanged.connect(self._sync_params)
        self.thres.valueChanged.connect(self._update_call)
        self.wavelet.currentIndexChanged.connect(self._update_call)
        self.run_dir.edit.textChanged.connect(self._on_run_dir_changed)
        self.run_button.clicked.connect(self._run)
        self.undo_button.clicked.connect(self._undo)
        self.jobs.busy_changed.connect(self._set_busy)
        self._sync_params()
        self._refresh_availability()

    # ------------------------------------------------------------------- ui

    def _method(self) -> str:
        return self.method.currentData()

    def _sync_params(self) -> None:
        """Grey out the rows that do not belong to the selected method."""
        active = self._method()
        for key, rows in self._rows.items():
            for _, widget in rows:
                enabled = key == active
                widget.setEnabled(enabled)
                row_label = self._form.labelForField(widget)
                if row_label is not None:
                    row_label.setEnabled(enabled)
        self._update_call()

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
        if self.run_dir.path() != self._enhancer_dir:
            self._enhancer = None
            self._enhancer_dir = None
        self._update_call()

    def _update_call(self) -> None:
        method = self._method()
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

    # --------------------------------------------------------------- learned

    def _load_enhancer(self, run_dir: str):
        """Load (and cache) the Enhancer for *run_dir*.

        Called on the worker thread: importing TensorFlow and reading a model
        takes seconds and must not block the UI.
        """
        if self._enhancer is not None and self._enhancer_dir == run_dir:
            return self._enhancer
        from kudio_enhance.inference import Enhancer
        enhancer = Enhancer.load(run_dir)
        self._enhancer, self._enhancer_dir = enhancer, run_dir
        return enhancer

    def _enhance_learned(self, y: np.ndarray, sr: int, run_dir: str,
                         allow_resample: bool = True) -> np.ndarray:
        enhancer = self._load_enhancer(run_dir)
        model_sr = enhancer.cfg.audio.sr
        if sr == model_sr:
            return enhancer.enhance(y)

        if not allow_resample:
            raise ValueError(
                f"this model was trained at {model_sr} Hz but the clip is "
                f"{sr} Hz — enable resampling, or convert it in the Batch panel")

        # in at the model's rate, back out at the session's, so splicing the
        # result into the session never changes its rate
        enhanced = enhancer.enhance(kudio.resample(y, sr, model_sr))
        out = kudio.resample(enhanced, model_sr, sr)
        return out[:len(y)] if len(out) >= len(y) else np.pad(
            out, (0, len(y) - len(out)), mode="edge")

    # ------------------------------------------------------------------ run

    def _run(self) -> None:
        if not self.require_audio():
            return
        method = self._method()
        y = np.asarray(self.session.y, dtype=np.float32)
        sr = self.session.sr
        self._before = y.copy()

        if method == WAVELET:
            fn, args = kudio.wavelet_low_pass_filter, (y,)
            kwargs = {"thres": self.thres.value(),
                      "wavelet": self.wavelet.currentText()}
            label = "wavelet_low_pass_filter"
        elif method == LEARNED:
            if not kudio_enhance_available():
                self.report(INSTALL_HINT)
                return
            run_dir = self.run_dir.path()
            if not run_dir or not Path(run_dir).is_dir():
                self.report("Select a kudio-enhance run directory first.")
                return
            fn = self._enhance_learned
            args = (y, sr, run_dir, self.auto_resample.isChecked())
            kwargs = {}
            label = f"learned model ({Path(run_dir).name})"
        else:
            fn, args, kwargs = kudio.trad_enhance, (y, sr), {}
            label = "trad_enhance"

        self._label = label
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
        self.session.set_audio(out, self.session.sr,
                               undo_label=self._label, keep_original=True)
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
