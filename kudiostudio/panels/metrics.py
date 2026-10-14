# -*- coding: utf-8 -*-
"""Objective quality metrics, with and without a clean reference.

`kudio.audio_report` needs nothing to compare against and always works. The
reference metrics — SNR / SI-SDR / segmental SNR in pure numpy, STOI and
BSS-SDR from `kudio[eval]`, PESQ from `kudio[pesq]` — need the clean signal
beside the degraded one, which a synthetic dataset has and a real recording
never does.

**PESQ is two scales, not one number.** kudio reports MOS-LQO by default;
enhancement papers mostly quote the raw narrowband P.862 score, and raw 2.03 is
about 1.65 MOS-LQO. The panel names which one it is showing, and lets you pick.
"""
from __future__ import annotations

from functools import partial

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import kudio
from kudiostudio.panels.base import Panel
from kudiostudio.theme import HINT_COLOR, WARN_COLOR

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

        self.align = QCheckBox("Line up with the reference first")
        self.align.setChecked(True)
        self.align.setToolTip(
            "Every metric here compares sample i of one signal with sample i\n"
            "of the other. A clip scored against itself reads +145 dB; one\n"
            "sample of delay costs 134 of them, and a millisecond scores worse\n"
            "than not processing at all — with nothing in the number to say\n"
            "which of the two happened.\n\n"
            "The delay is measured and reported either way. This decides\n"
            "whether it is taken out before scoring.")

        self.run_button = QPushButton("Evaluate")
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        form = QFormLayout()
        form.addRow("Reference", self.reference)
        form.addRow("File", picker)

        box = QGroupBox("Against a clean reference")
        box_layout = QVBoxLayout(box)
        self.pesq_scale = QComboBox()
        self.pesq_scale.addItems([name for name, _ in MetricsPanel.PESQ_SCALES])
        self.pesq_scale.setToolTip(
            "Which PESQ scale the table reports.\n\n"
            "MOS-LQO is what kudio has always shown. raw P.862 is the\n"
            "narrowband score most speech-enhancement papers quote. They are\n"
            "different scales, not different roundings: raw 2.03 is about\n"
            "1.65 MOS-LQO.")
        form.addRow("PESQ scale", self.pesq_scale)
        box_layout.addLayout(form)
        box_layout.addWidget(self.align)
        box_layout.addWidget(self.run_button)
        box_layout.addWidget(self.call_label(
            "kudio.align(ref, deg, sr)  +  kudio.snr / si_sdr / segmental_snr"
            "  +  kudio.eval_metrics(sr, ref, deg, pesq_scale=...)"))

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_inspect_box())
        layout.addWidget(box)
        layout.addWidget(self.table, stretch=1)
        self._availability = self.hint("")
        layout.addWidget(self._availability)

        self.browse.clicked.connect(self._pick_reference)
        self.reference.currentIndexChanged.connect(self._sync)
        self.run_button.clicked.connect(self._run)
        self.jobs.busy_changed.connect(self._set_busy)
        self._sync()
        self._refresh_availability()

    # --------------------------------------------------------- no reference

    def _build_inspect_box(self) -> QGroupBox:
        """The half of quality assessment that needs nothing to compare to.

        Everything below this box needs the clean signal beside the degraded
        one. A synthetic dataset has that. A real recording never does -- its
        "original" *is* the noisy one, so scoring against it measures how much
        you changed the audio, not whether you improved it.
        """
        self.inspect_button = QPushButton("Inspect this audio")
        self.inspect_result = QLabel("")
        self.inspect_result.setWordWrap(True)
        self.inspect_result.setVisible(False)
        self.inspect_button.clicked.connect(self._inspect)

        self.dnsmos_button = QPushButton("Predict opinion score (DNSMOS)")
        self.dnsmos_button.setToolTip(
            "ITU-T P.835: what a listener would say, with no reference.\n"
            "Needs onnxruntime and the DNS-Challenge weights, which kudio\n"
            "does not ship.")
        self.dnsmos_result = QLabel("")
        self.dnsmos_result.setWordWrap(True)
        self.dnsmos_result.setVisible(False)
        self.dnsmos_button.clicked.connect(self._dnsmos)

        box = QGroupBox("No reference needed")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(self.hint(
            "Clipping, level, DC offset, noise floor and the bandwidth the "
            "content really occupies — the questions you can still ask about "
            "a recording that has no clean version anywhere."))
        box_layout.addWidget(self.call_label("kudio.audio_report(y, sr)"))
        box_layout.addWidget(self.inspect_button)
        box_layout.addWidget(self.inspect_result)
        box_layout.addWidget(self.call_label("kudio.dnsmos(y, sr, model_dir=…)"))
        box_layout.addWidget(self.dnsmos_button)
        box_layout.addWidget(self.dnsmos_result)
        return box

    def _dnsmos(self) -> None:
        """Ask the model what a listener would say about this clip.

        `audio_report` answers "is this recording technically sound"; this is
        the other half — "does it sound good" — and the only way to get it
        without a listening panel. SIG and BAK move in opposite directions
        under denoising, so all three numbers are shown rather than the average.
        """
        if not self.require_audio():
            return
        y = np.asarray(self.session.y, dtype=np.float32)
        sr = self.session.sr
        self.report("Running DNSMOS …")
        self.dnsmos_button.setEnabled(False)
        started = self.jobs.run(kudio.dnsmos, y, sr,
                                on_done=self._dnsmos_done,
                                on_failed=self._dnsmos_failed)
        if not started:
            self.dnsmos_button.setEnabled(True)
            self.report("Another job is already running.")

    def _dnsmos_done(self, score) -> None:
        self.dnsmos_button.setEnabled(True)
        self._fill([
            ("SIG (speech)", f"{score.sig:.2f} / 5"),
            ("BAK (background)", f"{score.bak:.2f} / 5"),
            ("OVRL (overall)", f"{score.ovrl:.2f} / 5"),
            ("Windows averaged", str(score.segments)),
        ])
        self.dnsmos_result.setText(score.summary())
        self.dnsmos_result.setStyleSheet(f"color: {HINT_COLOR};")
        self.dnsmos_result.setVisible(True)
        self.report(f"DNSMOS — {score}")

    def _dnsmos_failed(self, message: str) -> None:
        self.dnsmos_button.setEnabled(True)
        # the useful failure here is "you do not have the model", and the
        # message from kudio says where to get it -- so show it in full
        self.dnsmos_result.setText(message)
        self.dnsmos_result.setStyleSheet(f"color: {WARN_COLOR};")
        self.dnsmos_result.setVisible(True)
        self.report("DNSMOS unavailable — see the panel for what is missing.")

    def _inspect(self) -> None:
        if not self.require_audio():
            return
        try:
            report = kudio.audio_report(np.asarray(self.session.y,
                                                   dtype=np.float32),
                                        self.session.sr)
        except Exception as e:
            self.report(f"Inspection failed — {type(e).__name__}: {e}")
            return

        lufs = "n/a (shorter than 400 ms)" if not np.isfinite(report.lufs) \
            else f"{report.lufs:+.2f} LUFS"
        self._fill([
            ("Peak", "silence" if report.peak_dbfs == float("-inf")
             else f"{report.peak_dbfs:+.2f} dBFS"),
            ("RMS", f"{report.rms_dbfs:+.2f} dBFS"),
            ("Loudness (BS.1770)", lufs),
            ("Clipped samples", f"{report.clipped_samples} "
                                f"({report.clipped_ratio * 100:.3f}%)"),
            ("DC offset", f"{report.dc_offset:+.5f}"),
            ("Silence", f"{report.silence_ratio * 100:.1f}%"),
            ("Noise floor", f"{report.noise_floor_dbfs:+.2f} dBFS"),
            ("Estimated SNR", f"{report.estimated_snr_db:+.1f} dB"),
            ("Bandwidth", f"{report.bandwidth_hz:.0f} Hz of "
                          f"{report.sr / 2:.0f} Hz available"),
        ])

        problems = report.problems()
        if problems:
            self.inspect_result.setText("· " + "\n· ".join(problems))
            self.inspect_result.setStyleSheet(f"color: {WARN_COLOR};")
        else:
            self.inspect_result.setText(
                "Nothing wrong with the recording itself. This says the audio "
                "is technically sound, not that it sounds good — for that, "
                "try the DNSMOS button below.")
            self.inspect_result.setStyleSheet(f"color: {HINT_COLOR};")
        self.inspect_result.setVisible(True)
        self.report("Inspection complete.")

    # ------------------------------------------------------------------- ui

    def _set_busy(self, busy: bool) -> None:
        # a method rather than a lambda over ``self``: the host holding that
        # lambda is the panel's own child, and the cycle kept the panel alive
        self.run_button.setEnabled(not busy)

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

    #: The two PESQ scales, label first. They are different scales, not
    #: different roundings, so the table has to say which one it shows.
    PESQ_SCALES = (("MOS-LQO", "lqo"), ("raw P.862", "raw"))

    #: Which extra actually installs each back-end. PESQ has its own because
    #: it builds from source, so it is deliberately not in [eval] or [all].
    INSTALL_HINT = {
        "PESQ": 'pip install "kudio[pesq]" (builds from source: needs a C '
                'compiler — on Windows, the MSVC Build Tools)',
        "STOI": 'pip install "kudio[eval]"',
        "SDR": 'pip install "kudio[eval]"',
    }

    def _pesq_scale(self) -> str:
        return self.PESQ_SCALES[self.pesq_scale.currentIndex()][1]

    def _pesq_label(self) -> str:
        return self.PESQ_SCALES[self.pesq_scale.currentIndex()][0]

    def _why_missing(self, name: str) -> str:
        """`None` means two different things and they need different answers.

        Before 3.7.0 a metric came back None only when its back-end was
        absent. Now an installed back-end also returns None when it cannot run
        on this pair -- PESQ is defined at 8 and 16 kHz only -- so labelling
        every None 'not installed' told people to install what they had.
        """
        if not getattr(self, "_installed", {}).get(name, False):
            return "not installed"
        if name == "PESQ" and int(self.session.sr) not in (8000, 16000):
            return f"not defined at {self.session.sr} Hz (PESQ is 8 or 16 kHz)"
        return "could not be computed for this pair"

    def _refresh_availability(self) -> None:
        self._installed = dict(zip(("PESQ", "STOI", "SDR"),
                                   kudio.check_metrics_install()))
        missing = [n for n, ok in self._installed.items() if not ok]
        if not missing:
            self._availability.setText("PESQ, STOI and SDR back-ends available.")
            return
        # One hint per back-end. A single `kudio[eval]` for all three was a
        # dead end for PESQ, which that extra has never carried.
        self._availability.setText("  ·  ".join(
            f"{name} unavailable — {self.INSTALL_HINT[name]}"
            for name in missing))

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
        if min(len(ref), len(deg)) == 0:
            self.report("Nothing to compare.")
            return

        rows, ref, deg = self._line_up(ref, deg)
        n = min(len(ref), len(deg))
        ref, deg = ref[:n], deg[:n]

        rows += [("SNR", f"{kudio.snr(ref, deg):+.3f} dB"),
                 ("SI-SDR", f"{kudio.si_sdr(ref, deg):+.3f} dB"),
                 ("Segmental SNR", f"{kudio.segmental_snr(ref, deg):+.3f} dB"),
                 ("Compared samples", f"{n} ({n / self.session.sr:.2f} s)")]
        self._fill(rows)
        self.report("Computing PESQ / STOI / SDR …")
        started = self.jobs.run(
            partial(kudio.eval_metrics, pesq_scale=self._pesq_scale()),
            self.session.sr, ref, deg,
            on_done=lambda r: self._optional_done(rows, r),
            on_failed=self._failed)
        if not started:
            self.report("An evaluation is already running.")

    def _line_up(self, ref, deg):
        """Measure the offset, report it, and take it out if asked.

        Always measured, because a bad score from a misalignment and a bad
        score from bad processing look identical and mean opposite things.
        """
        try:
            found = kudio.find_delay(ref, deg, self.session.sr)
        except Exception as e:                          # noqa: BLE001
            log.debug("alignment failed: %s", e)
            return [], ref, deg

        rows = [("Alignment", str(found))]
        if not found.confident():
            rows.append(("⚠ Reference", "these do not look like the same "
                                        "recording — the scores below are "
                                        "about two unrelated signals"))
            self.report("The reference does not look like this recording.")
            return rows, ref, deg

        if found.delay == 0:
            return rows, ref, deg
        if not self.align.isChecked():
            rows.append(("⚠ Not aligned",
                         f"{abs(found.milliseconds):.1f} ms of offset is left "
                         f"in; the scores below are mostly measuring it"))
            return rows, ref, deg

        self.report(f"Reference {found}; lined up before scoring.")
        ref, deg = found.apply(ref, deg)
        return rows, ref, deg

    def _optional_done(self, rows, result) -> None:
        pesq, stoi, sdr = result
        extra = [(f"PESQ ({self._pesq_label()})",
                  f"{pesq:.3f}" if pesq is not None
                  else self._why_missing("PESQ")),
                 ("STOI", f"{stoi:.3f}" if stoi is not None
                  else self._why_missing("STOI")),
                 ("SDR", f"{sdr:+.3f} dB" if sdr is not None
                  else self._why_missing("SDR"))]
        self._fill(rows + extra)
        self.report("Evaluation complete.")

    def _failed(self, message: str) -> None:
        self.report(f"Evaluation failed — {message}")

    def _fill(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for r, (name, value) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(value))
