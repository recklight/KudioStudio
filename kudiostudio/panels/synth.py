# -*- coding: utf-8 -*-
"""Build a noisy dataset — `kudio.Synthesizer`.

Mixes every clean file with every noise file at the requested SNRs, which is
how you generate paired training data for a denoising model.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import DirPicker, Panel

log = logging.getLogger(__name__)


def parse_snr(text: str) -> List[int]:
    """``"-5, 0, 5"`` -> ``[-5, 0, 5]``."""
    values = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            values.append(int(float(chunk)))
    if not values:
        raise ValueError("no SNR values given")
    return values


class SynthPanel(Panel):
    """Batch clean × noise × SNR mixing."""

    title = "Synthesize"

    MODES = ("regular", "inc")

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)

        self.clean = DirPicker("folder of clean audio")
        self.noise = DirPicker("folder of noise audio")
        self.out = DirPicker("output folder for the mixture")
        self.snr = QLineEdit("-5, 0, 5")
        self.mode = QComboBox()
        self.mode.addItems(self.MODES)
        self.mode.setToolTip("regular: one output per clean file\n"
                             "inc: every clean × noise × SNR combination")
        self.seed = QSpinBox()
        self.seed.setRange(-1, 99999)
        self.seed.setValue(-1)
        self.seed.setSpecialValueText("None")
        self.add_clean = QCheckBox("also copy the clean files (is_add_clean)")
        self.overwrite = QCheckBox("overwrite existing files")
        self.overwrite.setChecked(True)

        self.run_button = QPushButton("Synthesize")
        self.call = self.call_label()
        self.result = QLabel("—")
        self.result.setWordWrap(True)

        form = QFormLayout()
        form.addRow("clean", self.clean)
        form.addRow("noise", self.noise)
        form.addRow("out_path", self.out)
        form.addRow("snr_ratio", self.snr)
        form.addRow("mode", self.mode)
        form.addRow("seed", self.seed)

        box = QGroupBox("kudio.Synthesizer")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.add_clean)
        box_layout.addWidget(self.overwrite)
        box_layout.addWidget(self.call)
        box_layout.addWidget(self.run_button)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.result)
        layout.addWidget(self.hint(
            "Set a seed to make the mixture reproducible — the same seed "
            "gives the same noise offsets and file pairings."))
        layout.addStretch(1)

        self.run_button.clicked.connect(self._run)
        self.jobs.busy_changed.connect(self._set_busy)
        for w in (self.snr, self.clean.edit, self.noise.edit, self.out.edit):
            w.textChanged.connect(self._update_call)
        self.mode.currentIndexChanged.connect(self._update_call)
        self.seed.valueChanged.connect(self._update_call)
        self._update_call()

    # ------------------------------------------------------------------- ui

    def _seed(self) -> Optional[int]:
        return None if self.seed.value() < 0 else self.seed.value()

    def _update_call(self) -> None:
        try:
            snr = parse_snr(self.snr.text())
        except ValueError:
            snr = "…"
        self.call.setText(
            f"kudio.Synthesizer({self.clean.path()!r}, {self.noise.path()!r}, "
            f"out_path={self.out.path()!r}, snr_ratio={snr})"
            f".syn(mode={self.mode.currentText()!r}, seed={self._seed()})")

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("Synthesizing…" if busy else "Synthesize")

    # ------------------------------------------------------------------ run

    def _run(self) -> None:
        try:
            snr = parse_snr(self.snr.text())
        except ValueError as e:
            self.report(f"Bad SNR list — {e}")
            return
        for label, picker in (("clean", self.clean), ("noise", self.noise)):
            if not picker.path() or not Path(picker.path()).is_dir():
                self.report(f"Select a valid {label} folder.")
                return
        if not self.out.path():
            self.report("Select an output folder.")
            return

        self.report("Synthesizing …")
        started = self.jobs.run(self._synthesize, snr,
                                on_done=self._finished, on_failed=self._failed)
        if not started:
            self.report("A synthesis job is already running.")

    def _synthesize(self, snr: List[int]):
        syx = kudio.Synthesizer(self.clean.path(), self.noise.path(),
                                out_path=self.out.path(), snr_ratio=snr)
        return syx.syn(mode=self.mode.currentText(),
                       is_add_clean=self.add_clean.isChecked(),
                       overwrite=self.overwrite.isChecked(),
                       seed=self._seed())

    def _finished(self, produced) -> None:
        count = len(produced) if produced else 0
        self.result.setText(f"{count} file(s) written to {self.out.path()}")
        self.report(f"Synthesis complete — {count} file(s).")

    def _failed(self, message: str) -> None:
        self.result.setText("—")
        self.report(f"Synthesis failed — {message}")
