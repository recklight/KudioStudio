# -*- coding: utf-8 -*-
"""Waveform effects and augmentation — `kudio.effects`.

Each entry is one `kudio` function. The caption under the parameters is the
exact call that Apply will make, so a session here translates straight into a
script.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel

log = logging.getLogger(__name__)


def _spin(lo: float, hi: float, value: float, step: float = 0.1,
          decimals: int = 2, suffix: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def _int_spin(lo: int, hi: int, value: int, step: int = 1) -> QSpinBox:
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setValue(value)
    return box


class EffectsPanel(Panel):
    """Gain, pitch, time, reverb, noise mixing and silence trimming."""

    title = "Effects"

    EFFECTS = ("gain", "random_gain", "pitch_shift", "time_stretch",
               "reverb", "add_noise_snr", "trim_silence", "split_on_silence")

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._noise_path: Optional[Path] = None

        self.effect = QComboBox()
        self.effect.addItems(self.EFFECTS)
        self.params = QStackedWidget()
        self._build_param_pages()

        self.apply_button = QPushButton("Apply")
        self.undo_button = QPushButton("Undo")
        self.revert_button = QPushButton("Revert to original")
        self.call = self.call_label()

        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.revert_button)
        buttons.addStretch(1)

        box = QGroupBox("kudio.effects")
        box_layout = QVBoxLayout(box)
        form = QFormLayout()
        form.addRow("Effect", self.effect)
        box_layout.addLayout(form)
        box_layout.addWidget(self.params)
        box_layout.addWidget(self.call)
        box_layout.addLayout(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.hint(
            "Applies to the selected range when there is a selection, "
            "otherwise to the whole clip. Every Apply is undoable."))
        layout.addStretch(1)

        self.effect.currentIndexChanged.connect(self.params.setCurrentIndex)
        self.effect.currentIndexChanged.connect(self._update_call)
        self.apply_button.clicked.connect(self._apply)
        self.undo_button.clicked.connect(self._undo)
        self.revert_button.clicked.connect(self._revert)
        self._update_call()

    # ------------------------------------------------------------ parameters

    def _build_param_pages(self) -> None:
        # gain
        self.gain_db = _spin(-60, 24, 0.0, 0.5, 1, " dB")
        self.params.addWidget(self._page([("db", self.gain_db)]))

        # random_gain
        self.rg_lo = _spin(-60, 24, -6.0, 0.5, 1, " dB")
        self.rg_hi = _spin(-60, 24, 6.0, 0.5, 1, " dB")
        self.rg_seed = _int_spin(-1, 99999, -1)
        self.rg_seed.setSpecialValueText("None")
        self.params.addWidget(self._page([("db_range low", self.rg_lo),
                                          ("db_range high", self.rg_hi),
                                          ("seed", self.rg_seed)]))

        # pitch_shift
        self.pitch_steps = _spin(-24, 24, 2.0, 0.5, 2, " semitones")
        self.params.addWidget(self._page([("n_steps", self.pitch_steps)]))

        # time_stretch
        self.stretch_rate = _spin(0.1, 4.0, 1.0, 0.05, 2, "x")
        self.params.addWidget(self._page([("rate", self.stretch_rate)]))

        # reverb
        self.rev_decay = _spin(0.0, 0.99, 0.3, 0.05, 2)
        self.rev_delay = _spin(1.0, 500.0, 50.0, 5.0, 1, " ms")
        self.params.addWidget(self._page([("decay", self.rev_decay),
                                          ("delay_ms", self.rev_delay)]))

        # add_noise_snr
        self.noise_edit = QLineEdit()
        self.noise_edit.setReadOnly(True)
        self.noise_edit.setPlaceholderText("pick a noise .wav …")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_noise)
        picker = QWidget()
        picker_layout = QHBoxLayout(picker)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.addWidget(self.noise_edit)
        picker_layout.addWidget(browse)
        self.snr_db = _spin(-30, 40, 5.0, 1.0, 1, " dB")
        self.noise_seed = _int_spin(-1, 99999, -1)
        self.noise_seed.setSpecialValueText("None")
        self.params.addWidget(self._page([("noise", picker),
                                          ("snr_db", self.snr_db),
                                          ("seed", self.noise_seed)]))

        # trim_silence / split_on_silence share the same three parameters
        self.trim_top_db = _spin(1, 90, 30.0, 1.0, 1, " dB")
        self.trim_frame = _int_spin(64, 16384, 2048, 64)
        self.trim_hop = _int_spin(16, 8192, 512, 16)
        self.params.addWidget(self._page([("top_db", self.trim_top_db),
                                          ("frame_length", self.trim_frame),
                                          ("hop_length", self.trim_hop)]))

        self.split_top_db = _spin(1, 90, 30.0, 1.0, 1, " dB")
        self.split_frame = _int_spin(64, 16384, 2048, 64)
        self.split_hop = _int_spin(16, 8192, 512, 16)
        self.split_result = QLabel("—")
        self.params.addWidget(self._page([("top_db", self.split_top_db),
                                          ("frame_length", self.split_frame),
                                          ("hop_length", self.split_hop),
                                          ("segments", self.split_result)]))

        # PySide6's findChildren takes one type at a time, unlike PyQt5
        for page in range(self.params.count()):
            for cls in (QDoubleSpinBox, QSpinBox):
                for child in self.params.widget(page).findChildren(cls):
                    child.valueChanged.connect(self._update_call)

    @staticmethod
    def _page(rows) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        for label, widget in rows:
            form.addRow(label, widget)
        return page

    def _pick_noise(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a noise file", "", "Audio (*.wav *.flac *.mp3 *.ogg)")
        if not path:
            return
        self._noise_path = Path(path)
        self.noise_edit.setText(self._noise_path.name)
        self._update_call()

    # ----------------------------------------------------------------- apply

    def _seed(self, box: QSpinBox) -> Optional[int]:
        return None if box.value() < 0 else box.value()

    def _operation(self) -> Tuple[str, Callable[[np.ndarray, int], np.ndarray]]:
        """Return ``(call_text, fn(y, sr) -> y)`` for the selected effect."""
        name = self.effect.currentText()

        if name == "gain":
            db = self.gain_db.value()
            return (f"kudio.gain(y, db={db})",
                    lambda y, sr: kudio.gain(y, db))

        if name == "random_gain":
            rng = (self.rg_lo.value(), self.rg_hi.value())
            seed = self._seed(self.rg_seed)
            return (f"kudio.random_gain(y, db_range={rng}, seed={seed})",
                    lambda y, sr: kudio.random_gain(y, db_range=rng, seed=seed))

        if name == "pitch_shift":
            steps = self.pitch_steps.value()
            return (f"kudio.pitch_shift(y, sr, n_steps={steps})",
                    lambda y, sr: kudio.pitch_shift(y, sr, n_steps=steps))

        if name == "time_stretch":
            rate = self.stretch_rate.value()
            return (f"kudio.time_stretch(y, rate={rate})",
                    lambda y, sr: kudio.time_stretch(y, rate=rate))

        if name == "reverb":
            decay, delay = self.rev_decay.value(), self.rev_delay.value()
            return (f"kudio.reverb(y, sr, decay={decay}, delay_ms={delay})",
                    lambda y, sr: kudio.reverb(y, sr, decay=decay, delay_ms=delay))

        if name == "add_noise_snr":
            snr, seed = self.snr_db.value(), self._seed(self.noise_seed)
            noise_name = self._noise_path.name if self._noise_path else "…"
            return (f"kudio.add_noise_snr(y, noise={noise_name!r}, "
                    f"snr_db={snr}, seed={seed})",
                    lambda y, sr: kudio.add_noise_snr(
                        y, self._load_noise(sr), snr_db=snr, seed=seed))

        if name == "trim_silence":
            top, frame, hop = (self.trim_top_db.value(), self.trim_frame.value(),
                               self.trim_hop.value())
            return (f"kudio.trim_silence(y, top_db={top}, frame_length={frame}, "
                    f"hop_length={hop})[0]",
                    lambda y, sr: kudio.trim_silence(
                        y, top_db=top, frame_length=frame, hop_length=hop)[0])

        top, frame, hop = (self.split_top_db.value(), self.split_frame.value(),
                           self.split_hop.value())
        return (f"kudio.split_on_silence(y, top_db={top}, frame_length={frame}, "
                f"hop_length={hop})",
                lambda y, sr: kudio.split_on_silence(
                    y, top_db=top, frame_length=frame, hop_length=hop))

    def _load_noise(self, sr: int) -> np.ndarray:
        if self._noise_path is None:
            raise ValueError("no noise file selected")
        noise, _ = kudio.file_load(self._noise_path, sr=sr)
        return noise

    def _apply(self) -> None:
        if not self.require_audio():
            return
        call_text, fn = self._operation()
        y = self.session.selected_audio()
        try:
            out = fn(np.asarray(y, dtype=np.float32), self.session.sr)
        except Exception as e:
            self.report(f"{self.effect.currentText()} failed — {type(e).__name__}: {e}")
            return

        if self.effect.currentText() == "split_on_silence":
            self.split_result.setText(f"{len(out)} segment(s)")
            total = sum(len(seg) for seg in out) / self.session.sr
            self.report(f"split_on_silence → {len(out)} segment(s), "
                        f"{total:.2f}s of non-silent audio (nothing modified)")
            return

        self.session.replace_selection(out, undo_label=self.effect.currentText())
        self.report(f"Applied {call_text}")

    def _undo(self) -> None:
        if not self.session.undo():
            self.report("Nothing to undo.")

    def _revert(self) -> None:
        if not self.session.revert():
            self.report("No original to revert to.")

    def _update_call(self) -> None:
        self.call.setText(self._operation()[0])
