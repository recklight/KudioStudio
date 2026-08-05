# -*- coding: utf-8 -*-
"""Folder-wide conversion — the GUI form of the `kudio` command line.

Resample, change bit depth and trim silence across a whole directory. The file
list comes from :func:`kudio.check_input`, so the same folder/manifest/glob
inputs the library accepts work here too.
"""
from __future__ import annotations

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
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import DirPicker, Panel

log = logging.getLogger(__name__)

SUBTYPES = ("PCM_16", "PCM_24", "PCM_32", "FLOAT")
RATES = ("keep", "48000", "44100", "32000", "22050", "16000", "8000")


class BatchPanel(Panel):
    """Convert every audio file in a folder."""

    title = "Batch"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)

        self.src = DirPicker("input folder")
        self.dst = DirPicker("output folder")
        self.rate = QComboBox()
        self.rate.addItems(RATES)
        self.subtype = QComboBox()
        self.subtype.addItems(SUBTYPES)
        self.mono = QCheckBox("downmix to mono")
        self.mono.setChecked(True)
        self.trim = QCheckBox("trim leading/trailing silence")
        self.top_db = QDoubleSpinBox()
        self.top_db.setRange(1.0, 90.0)
        self.top_db.setValue(30.0)
        self.top_db.setSuffix(" dB")
        self.top_db.setEnabled(False)
        self.normalize = QCheckBox("peak-normalize to 0.99")

        self.run_button = QPushButton("Run")
        self.call = self.call_label()
        self.result = QLabel("—")
        self.result.setWordWrap(True)

        form = QFormLayout()
        form.addRow("input", self.src)
        form.addRow("output", self.dst)
        form.addRow("sr", self.rate)
        form.addRow("subtype", self.subtype)
        form.addRow("top_db", self.top_db)

        box = QGroupBox("Batch conversion")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        for w in (self.mono, self.trim, self.normalize):
            box_layout.addWidget(w)
        box_layout.addWidget(self.call)
        box_layout.addWidget(self.run_button)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.result)
        layout.addWidget(self.hint(
            "Output filenames mirror the input names. Existing files are "
            "renamed rather than overwritten (kudio.check_file)."))
        layout.addStretch(1)

        self.trim.toggled.connect(self.top_db.setEnabled)
        self.run_button.clicked.connect(self._run)
        self.jobs.busy_changed.connect(self._set_busy)
        for w in (self.rate, self.subtype):
            w.currentIndexChanged.connect(self._update_call)
        self.src.edit.textChanged.connect(self._update_call)
        self._update_call()

    # ------------------------------------------------------------------- ui

    def _target_rate(self) -> Optional[int]:
        text = self.rate.currentText()
        return None if text == "keep" else int(text)

    def _update_call(self) -> None:
        self.call.setText(
            f"kudio.check_input({self.src.path()!r}) → "
            f"kudio.file_load(f, sr={self._target_rate()}) → "
            f"kudio.save_wave(out, y, sr, subtype={self.subtype.currentText()!r})")

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("Running…" if busy else "Run")

    # ------------------------------------------------------------------ run

    def _run(self) -> None:
        if not self.src.path() or not Path(self.src.path()).is_dir():
            self.report("Select a valid input folder.")
            return
        if not self.dst.path():
            self.report("Select an output folder.")
            return
        self.report("Converting …")
        started = self.jobs.run(self._convert, on_done=self._finished,
                                on_failed=self._failed)
        if not started:
            self.report("A batch job is already running.")

    def _convert(self):
        files, _ = kudio.check_input(self.src.path())
        if not files:
            raise ValueError(f"no audio files found in {self.src.path()}")
        out_dir = Path(kudio.check_path(self.dst.path(), exist_ok=True))
        target_rate = self._target_rate()
        subtype = self.subtype.currentText()
        written, failed = 0, []

        for f in files:
            try:
                y, sr = kudio.file_load(f, sr=target_rate, mono=self.mono.isChecked())
                if self.trim.isChecked():
                    y = kudio.trim_silence(y, top_db=self.top_db.value())[0]
                if self.normalize.isChecked():
                    y = kudio.normalize(y, peak=0.99)
                if len(y) == 0:
                    failed.append(f"{Path(f).name}: empty after trimming")
                    continue
                kudio.save_wave(out_dir / Path(f).with_suffix(".wav").name,
                                y, sr, subtype=subtype)
                written += 1
            except Exception as e:
                failed.append(f"{Path(f).name}: {type(e).__name__}: {e}")
        return written, len(files), failed

    def _finished(self, result) -> None:
        written, total, failed = result
        text = f"{written}/{total} file(s) written to {self.dst.path()}"
        if failed:
            preview = "\n".join(failed[:5])
            more = f"\n… and {len(failed) - 5} more" if len(failed) > 5 else ""
            text += f"\n\n{len(failed)} failed:\n{preview}{more}"
        self.result.setText(text)
        self.report(f"Batch complete — {written}/{total} converted, "
                    f"{len(failed)} failed.")

    def _failed(self, message: str) -> None:
        self.result.setText("—")
        self.report(f"Batch failed — {message}")
