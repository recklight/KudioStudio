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

from PySide6.QtCore import Qt
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

SUBTYPES = ("PCM_16", "PCM_24", "PCM_32", "FLOAT")
RATES = ("keep", "48000", "44100", "32000", "22050", "16000", "8000")
NORMALIZE_MODES = ("none", "peak", "LUFS")
#: (range, default, suffix) per mode -- a peak of -23 or a loudness of 0.99
#: are both nonsense, so the box changes with the choice
_NORMALIZE_RANGES = {
    "peak": ((0.01, 1.0), 0.99, ""),
    "LUFS": ((-60.0, 0.0), -23.0, " LUFS"),      # -23 is EBU R 128
}


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

        self.normalize_mode = QComboBox()
        self.normalize_mode.addItems(NORMALIZE_MODES)
        self.normalize_mode.setToolTip(
            "Peak scales by the loudest sample, which says nothing about how\n"
            "loud a file sounds: one stray transient normalises a whole clip\n"
            "to a whisper. LUFS (ITU-R BS.1770) levels by perceived loudness,\n"
            "which is what a dataset of mixed sources actually needs.")
        self.normalize_target = QDoubleSpinBox()
        self.normalize_target.setDecimals(2)
        self.normalize_target.setEnabled(False)
        self._sync_normalize_target()

        self.denoise = QComboBox()
        self.denoise.addItem("off", None)
        for name in kudio.METHODS:
            self.denoise.addItem(kudio.METHODS[name].label, name)
        self.denoise.setToolTip(
            "Denoise every file on the way through (kudio.enhance_folder).\n"
            "A separate pass from the conversion, because it reads the whole\n"
            "file to estimate the noise — and because a folder of already-clean\n"
            "audio comes out worse, not better.")
        self.denoise_noise = QComboBox()
        for name, summary in kudio.NOISE_ESTIMATORS.items():
            self.denoise_noise.addItem(name, name)
            self.denoise_noise.setItemData(self.denoise_noise.count() - 1,
                                           summary, Qt.ToolTipRole)
        self.denoise_noise.setEnabled(False)

        self.run_button = QPushButton("Run")
        self.call = self.call_label()
        self.result = QLabel("—")
        self.result.setWordWrap(True)

        normalize_row = QHBoxLayout()
        normalize_row.addWidget(self.normalize_mode)
        normalize_row.addWidget(self.normalize_target)
        denoise_row = QHBoxLayout()
        denoise_row.addWidget(self.denoise)
        denoise_row.addWidget(self.denoise_noise)

        form = QFormLayout()
        form.addRow("input", self.src)
        form.addRow("output", self.dst)
        form.addRow("sr", self.rate)
        form.addRow("subtype", self.subtype)
        form.addRow("top_db", self.top_db)
        form.addRow("normalize", normalize_row)
        form.addRow("denoise", denoise_row)

        box = QGroupBox("Batch conversion")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        for w in (self.mono, self.trim):
            box_layout.addWidget(w)
        box_layout.addWidget(self.call)
        box_layout.addWidget(self.run_button)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(self.result)
        layout.addWidget(self.hint(
            "Output mirrors the input's folder structure, so two files with "
            "the same name in different subfolders do not collide. Existing "
            "files are renamed rather than overwritten (kudio.check_file)."))
        layout.addStretch(1)

        self.trim.toggled.connect(self.top_db.setEnabled)
        self.trim.toggled.connect(self._update_call)
        self.run_button.clicked.connect(self._run)
        self.jobs.busy_changed.connect(self._set_busy)
        for w in (self.rate, self.subtype):
            w.currentIndexChanged.connect(self._update_call)
        self.normalize_mode.currentIndexChanged.connect(self._on_normalize_changed)
        self.denoise.currentIndexChanged.connect(self._on_denoise_changed)
        self.denoise_noise.currentIndexChanged.connect(self._update_call)
        self.normalize_target.valueChanged.connect(self._update_call)
        self.mono.toggled.connect(self._update_call)
        self.src.edit.textChanged.connect(self._update_call)
        self._update_call()

    # ------------------------------------------------------------------- ui

    def _target_rate(self) -> Optional[int]:
        text = self.rate.currentText()
        return None if text == "keep" else int(text)

    def _sync_normalize_target(self) -> None:
        mode = self.normalize_mode.currentText()
        spec = _NORMALIZE_RANGES.get(mode)
        self.normalize_target.setEnabled(spec is not None)
        if spec is None:
            self.normalize_target.setSuffix("")
            return
        (low, high), default, suffix = spec
        self.normalize_target.blockSignals(True)
        self.normalize_target.setRange(low, high)
        self.normalize_target.setSuffix(suffix)
        self.normalize_target.setValue(default)
        self.normalize_target.blockSignals(False)

    def _on_normalize_changed(self) -> None:
        self._sync_normalize_target()
        self._update_call()

    def _normalize_kwargs(self) -> dict:
        mode = self.normalize_mode.currentText()
        if mode == "peak":
            return {"peak": float(self.normalize_target.value())}
        if mode == "LUFS":
            return {"lufs": float(self.normalize_target.value())}
        return {}

    def _convert_kwargs(self) -> dict:
        kwargs = {
            "sr": self._target_rate(),
            "mono": self.mono.isChecked(),
            "subtype": self.subtype.currentText(),
            "trim_db": self.top_db.value() if self.trim.isChecked() else None,
        }
        kwargs.update(self._normalize_kwargs())
        return kwargs

    def _on_denoise_changed(self) -> None:
        self.denoise_noise.setEnabled(self.denoise.currentData() is not None)
        self._update_call()

    def _update_call(self) -> None:
        arguments = ", ".join(f"{k}={v!r}" for k, v in self._convert_kwargs().items())
        text = (f"kudio.convert_folder({self.src.path()!r}, "
                f"{self.dst.path()!r}, {arguments})")
        method = self.denoise.currentData()
        if method is not None:
            # two passes, and the caption says so -- the denoise reads whole
            # files to estimate the noise, which the conversion does not
            text += (f"\n→ kudio.enhance_folder(out, out, {method!r}, "
                     f"noise={self.denoise_noise.currentData()!r}, "
                     f"overwrite=True, report=True)")
        self.call.setText(text)

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
        # the loop this used to run by hand now lives in kudio, where the CLI
        # (`kudio convert --recursive`) reaches it too
        result = kudio.convert_folder(self.src.path(), self.dst.path(),
                                      **self._convert_kwargs())
        method = self.denoise.currentData()
        if method is None or not result.written:
            return result
        # a second pass over what the first one wrote, in place: denoising has
        # to read whole files to estimate the noise, so it cannot ride along
        return kudio.enhance_folder(
            self.dst.path(), self.dst.path(), method,
            noise=self.denoise_noise.currentData(),
            overwrite=True, report=True)

    def _finished(self, result) -> None:
        text = (f"{result.written}/{result.total} file(s) written to "
                f"{self.dst.path()}")
        reduction = getattr(result, "mean_reduction_db", lambda: None)()
        if reduction is not None:
            text += (f"\nNoise floor down {reduction:.1f} dB on average — "
                     f"a measurement of what was removed, not of whether it "
                     f"sounds better. Open one and listen.")
        if result.failed:
            lines = [f"{path.name}: {reason}" for path, reason in result.failed[:5]]
            more = (f"\n… and {len(result.failed) - 5} more"
                    if len(result.failed) > 5 else "")
            text += f"\n\n{len(result.failed)} failed:\n" + "\n".join(lines) + more
        self.result.setText(text)
        self.report(f"Batch complete — {result.written}/{result.total} written, "
                    f"{len(result.failed)} failed.")

    def _failed(self, message: str) -> None:
        self.result.setText("—")
        self.report(f"Batch failed — {message}")
