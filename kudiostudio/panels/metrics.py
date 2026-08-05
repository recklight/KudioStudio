# -*- coding: utf-8 -*-
"""Objective quality metrics — `kudio.core.evaluator`.

SNR / SI-SDR / segmental SNR are pure numpy and always available. PESQ, STOI
and BSS-SDR need `kudio[eval]`; the panel reports which back-ends are present
instead of failing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel

log = logging.getLogger(__name__)


class MetricsPanel(Panel):
    """Score the current audio against a clean reference."""

    title = "Metrics"

    REFERENCES = ("Original (before edits)", "File…")

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._ref_path: Optional[Path] = None

        self.reference = QComboBox()
        self.reference.addItems(self.REFERENCES)
        self.ref_edit = QLineEdit()
        self.ref_edit.setReadOnly(True)
        self.ref_edit.setPlaceholderText("no reference file selected")
        self.browse = QPushButton("Browse…")
        picker = QWidget()
        picker_layout = QHBoxLayout(picker)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.addWidget(self.ref_edit)
        picker_layout.addWidget(self.browse)

        self.run_button = QPushButton("Evaluate")
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        form = QFormLayout()
        form.addRow("Reference", self.reference)
        form.addRow("File", picker)

        box = QGroupBox("Reference")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.run_button)
        box_layout.addWidget(self.call_label(
            "kudio.snr / si_sdr / segmental_snr  +  kudio.eval_metrics(sr, ref, deg)"))

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.table, stretch=1)
        self._availability = self.hint("")
        layout.addWidget(self._availability)

        self.browse.clicked.connect(self._pick_reference)
        self.reference.currentIndexChanged.connect(self._sync)
        self.run_button.clicked.connect(self._run)
        self.jobs.busy_changed.connect(lambda b: self.run_button.setEnabled(not b))
        self._sync()
        self._refresh_availability()

    # ------------------------------------------------------------------- ui

    def _sync(self) -> None:
        by_file = self.reference.currentIndex() == 1
        self.ref_edit.setEnabled(by_file)
        self.browse.setEnabled(by_file)

    def _pick_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the clean reference", "",
            "Audio (*.wav *.flac *.mp3 *.ogg)")
        if path:
            self._ref_path = Path(path)
            self.ref_edit.setText(self._ref_path.name)

    def _refresh_availability(self) -> None:
        pesq_ok, stoi_ok, sdr_ok = kudio.check_metrics_install()
        missing = [n for n, ok in (("PESQ", pesq_ok), ("STOI", stoi_ok),
                                   ("SDR", sdr_ok)) if not ok]
        if missing:
            self._availability.setText(
                f"{', '.join(missing)} unavailable — install with: "
                f"pip install \"kudio[eval]\"")
        else:
            self._availability.setText("PESQ, STOI and SDR back-ends available.")

    # ------------------------------------------------------------------ run

    def _reference_audio(self) -> Tuple[np.ndarray, int]:
        if self.reference.currentIndex() == 1:
            if self._ref_path is None:
                raise ValueError("no reference file selected")
            return kudio.file_load(self._ref_path, sr=self.session.sr)
        original = self.session.original()
        if original is None:
            raise ValueError("no original audio in this session")
        return original

    def _run(self) -> None:
        if not self.require_audio():
            return
        try:
            ref, ref_sr = self._reference_audio()
        except Exception as e:
            self.report(f"Reference unavailable — {e}")
            return
        if ref_sr != self.session.sr:
            self.report(f"Sample-rate mismatch: reference {ref_sr} Hz vs "
                        f"current {self.session.sr} Hz.")
            return

        deg = np.asarray(self.session.y, dtype=np.float32)
        ref = np.asarray(ref, dtype=np.float32)
        n = min(len(ref), len(deg))
        if n == 0:
            self.report("Nothing to compare.")
            return
        ref, deg = ref[:n], deg[:n]

        rows = [("SNR", f"{kudio.snr(ref, deg):+.3f} dB"),
                ("SI-SDR", f"{kudio.si_sdr(ref, deg):+.3f} dB"),
                ("Segmental SNR", f"{kudio.segmental_snr(ref, deg):+.3f} dB"),
                ("Compared samples", f"{n} ({n / self.session.sr:.2f} s)")]
        self._fill(rows)
        self.report("Computing PESQ / STOI / SDR …")
        started = self.jobs.run(kudio.eval_metrics, self.session.sr, ref, deg,
                                on_done=lambda r: self._optional_done(rows, r),
                                on_failed=self._failed)
        if not started:
            self.report("An evaluation is already running.")

    def _optional_done(self, rows, result) -> None:
        pesq, stoi, sdr = result
        extra = [("PESQ", f"{pesq:.3f}" if pesq is not None else "not installed"),
                 ("STOI", f"{stoi:.3f}" if stoi is not None else "not installed"),
                 ("SDR", f"{sdr:+.3f} dB" if sdr is not None else "not installed")]
        self._fill(rows + extra)
        self.report("Evaluation complete.")

    def _failed(self, message: str) -> None:
        self.report(f"Evaluation failed — {message}")

    def _fill(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for r, (name, value) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(value))
