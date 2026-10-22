# -*- coding: utf-8 -*-
"""Run every enhancement method over the same clip and rank them.

Choosing a denoiser by applying one, listening, undoing and applying the next
is how you end up with whichever one you happened to try last. This runs them
all on identical audio with identical framing, measures each, and lets you
audition any row without committing to it.

**What the numbers mean depends on whether there is a reference.**

With a clean reference — a synthetic mixture, a studio take — SI-SDR and PESQ
say how close the result is to the truth, and `ΔSNR` says how much better that
is than doing nothing. Those are quality scores.

Without one — which is every real recording — all that is left is the residual
noise floor, and **a lower floor is not automatically a better result**. The
easiest way to reach a very low floor is to delete the speech along with the
noise. That is why the table always offers `♪ Audition`: the ranking is a
shortlist, not a verdict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt
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
from kudiostudio.panels.base import DirPicker, Panel
from kudiostudio.panels.enhance import (
    INSTALL_HINT,
    LearnedModel,
    ModelState,
    kudio_enhance_available,
    learned_code,
    model_state,
)
from kudiostudio.theme import ACCENT_COLOR, HINT_COLOR, OK_COLOR, WARN_COLOR

log = logging.getLogger(__name__)

REF_NONE = "none — a real recording has no clean version"
REF_ORIGINAL = "the original, before this session's edits"
REF_FILE = "a file…"
REFERENCES = (REF_NONE, REF_ORIGINAL, REF_FILE)

COLUMNS = ("method", "noise", "ms", "floor dBFS", "LUFS",
           "SI-SDR", "ΔSNR", "PESQ", "STOI")
#: columns that only mean anything with a clean reference
REFERENCE_COLUMNS = tuple(COLUMNS.index(c) for c in ("SI-SDR", "ΔSNR",
                                                     "PESQ", "STOI"))

#: Silence between two results auditioned back to back. Long enough that
#: the switch is heard as a switch, short enough not to lose the thread.
AB_GAP_SECONDS = 0.35

SWEEP_METHODS = "gain rules, one noise estimator"
SWEEP_NOISES = "noise estimators, one gain rule"
SWEEP_GRID = "everything — the full grid"
SWEEPS = (SWEEP_METHODS, SWEEP_NOISES, SWEEP_GRID)


@dataclass(frozen=True, eq=False)
class RunContext:
    """What one comparison ran on, kept with the results it produced.

    Apply writes a row into the history as the code that computes it again,
    and that code is only true of the audio, the range and the model the row
    came from. Kept per run rather than in the panel's last settings: a run
    started while another's rows are on screen used to lend those rows its
    range, and a result that landed after the audio was edited was recorded
    as if made from the edit.
    """
    #: the session's audio array itself, compared by identity
    y: object
    #: the samples compared, ``None`` for the whole clip
    ran_on: Optional[Tuple[int, int]]
    #: the learned row's run directory, and its model files as they were
    learned_dir: Optional[str] = None
    model: Optional[ModelState] = None


class NumericItem(QTableWidgetItem):
    """A cell that sorts by its value and displays its formatting.

    `QTableWidgetItem` compares the displayed *text*, so a ranking table would
    put "10.2" below "9.66" — in the one column anyone actually sorts on.
    Storing the number in `Qt.UserRole` alone changes nothing, because nothing
    reads it; the comparison has to be overridden.
    """

    def __lt__(self, other) -> bool:
        mine = self.data(Qt.UserRole)
        theirs = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if mine is None or theirs is None:
            return super().__lt__(other)
        return mine < theirs


class ComparePanel(Panel):
    """Score every `kudio` enhancement method against the same clip."""

    title = "Compare"
    icon = "compare"

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._results: List = []
        self._ref_path: Optional[Path] = None
        #: what the running comparison was started on, and what the rows on
        #: screen were computed from -- what Apply needs to write a result
        #: into the history as code
        self._pending: Optional[RunContext] = None
        self._shown: Optional[RunContext] = None
        #: the learned row's model, kept for the next comparison on the same
        #: run directory, as the Enhance panel keeps its own
        self._model = LearnedModel()
        #: the rows the audition loop is playing, as (method, estimator)
        #: pairs in the order it plays them
        self._heard: Tuple[Tuple[str, str], ...] = ()

        self.reference = QComboBox()
        self.reference.addItems(REFERENCES)
        self.ref_edit = QLineEdit()
        self.ref_edit.setReadOnly(True)
        self.ref_edit.setPlaceholderText("no reference file selected")
        self.browse = QPushButton("Browse…")
        picker = QWidget()
        picker_layout = QHBoxLayout(picker)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.addWidget(self.ref_edit)
        picker_layout.addWidget(self.browse)

        self.sweep = QComboBox()
        self.sweep.addItems(SWEEPS)
        self.sweep.setToolTip(
            "Which axis to vary. Enhancement is two choices — a gain rule and "
            "a noise estimator — and the estimator is usually the wider of the "
            "two, so 'gain rules only' is the narrower experiment.")

        self.method = QComboBox()
        for name in kudio.METHODS:
            spec = kudio.METHODS[name]
            self.method.addItem(spec.label, name)
            self.method.setItemData(self.method.count() - 1, spec.summary,
                                    Qt.ToolTipRole)
        self.method.setCurrentIndex(self.method.findData("logmmse"))

        self.noise = QComboBox()
        for name, summary in kudio.NOISE_ESTIMATORS.items():
            self.noise.addItem(name, name)
            self.noise.setItemData(self.noise.count() - 1, summary, Qt.ToolTipRole)

        # Its own picker rather than reaching into the Enhance panel: panels
        # talk to the session, never to each other.
        self.run_dir = DirPicker(
            "optional: a kudio-enhance run directory",
            caption="Select a kudio-enhance run directory")
        self.run_dir.setToolTip(
            "Rank a trained model in the same table as the statistical "
            "methods. 'Is the model actually better than log-MMSE' is not a "
            "question two separate printouts answer.")

        self.metrics = QCheckBox("PESQ / STOI (slow; STOI needs kudio[eval], "
                                 "PESQ needs kudio[pesq])")
        self.selection_only = QCheckBox("compare only the selected range")
        self.selection_only.setToolTip(
            "Faster, and lets you judge one difficult passage. Noise estimation "
            "sees only that range, which is a real difference in the result.")

        self.run_button = QPushButton("Run comparison")
        self.call = self.call_label()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # two rows can be selected and heard back to back; clicking between
        # them puts a gap and a mouse movement inside the comparison
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)

        self.audition_button = QPushButton("♪  Audition")
        self.audition_button.setCheckable(True)
        self.audition_button.setEnabled(False)
        self.audition_button.setStyleSheet(
            f"QPushButton:checked {{ background: {ACCENT_COLOR}; color: #1b1d21;"
            f" font-weight: bold; }}")
        self.apply_button = QPushButton("Apply this one")
        self.apply_button.setEnabled(False)
        self.save_button = QPushButton("Save all…")
        self.save_button.setEnabled(False)

        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Reference", self.reference)
        form.addRow("File", picker)
        form.addRow("Compare", self.sweep)
        form.addRow("Gain rule", self.method)
        form.addRow("Noise estimate", self.noise)
        form.addRow("Learned model", self.run_dir)
        self._form = form

        setup = QGroupBox("kudio.compare_enhancers")
        setup_layout = QVBoxLayout(setup)
        setup_layout.addLayout(form)
        setup_layout.addWidget(self.metrics)
        setup_layout.addWidget(self.selection_only)
        setup_layout.addWidget(self.call)
        setup_layout.addWidget(self.run_button)

        actions = QHBoxLayout()
        actions.addWidget(self.audition_button)
        actions.addWidget(self.apply_button)
        actions.addWidget(self.save_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(setup)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(actions)
        layout.addWidget(self.verdict)
        layout.addWidget(self.hint(
            "Every method sees identical audio and identical framing, so the "
            "differences are the methods. Click a row to hear it — a low noise "
            "floor is easiest to reach by deleting the speech too.\n\n"
            "These are for noisy audio. Above roughly 10 dB SNR they all "
            "measure worse than doing nothing, because the distortion they add "
            "costs more than the noise they remove. They also need to hear the "
            "noise on its own: a clip with no pauses gives the noise estimator "
            "almost nothing to work from."))

        self.browse.clicked.connect(self._pick_reference)
        self.reference.currentIndexChanged.connect(self._sync)
        self.sweep.currentIndexChanged.connect(self._sync)
        self.method.currentIndexChanged.connect(self._update_call)
        self.noise.currentIndexChanged.connect(self._update_call)
        self.run_dir.edit.textChanged.connect(self._on_run_dir_changed)
        self.run_button.clicked.connect(self._run)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        # A re-sort moves the selected rows without changing which rows they
        # are, so the selection signal stays quiet -- but the loop plays them
        # in the table's order, and that order has just changed.
        self.table.model().layoutChanged.connect(self._on_rows_moved)
        self.audition_button.toggled.connect(self._on_audition_toggled)
        self.apply_button.clicked.connect(self._apply)
        self.save_button.clicked.connect(self._save_all)
        self.jobs.busy_changed.connect(self._set_busy)
        self._sync()

    # ------------------------------------------------------------------- ui

    def _sync(self) -> None:
        by_file = self.reference.currentText() == REF_FILE
        self.ref_edit.setEnabled(by_file)
        self.browse.setEnabled(by_file)

        sweep = self.sweep.currentText()
        # whichever axis is being swept, its picker is the thing being varied,
        # so holding it at one value would be a contradiction
        for widget, fixed in ((self.method, sweep == SWEEP_NOISES),
                              (self.noise, sweep == SWEEP_METHODS)):
            widget.setEnabled(fixed)
            label = self._form.labelForField(widget)
            if label is not None:
                label.setEnabled(fixed)
        self._update_call()

    def _on_run_dir_changed(self) -> None:
        # a different run means a different model; drop the cached one
        if self.run_dir.path() != self._model.run_dir:
            self._model.forget()
        self._update_call()

    def _learned_entry(self):
        """``(name, callable)`` for the picked run directory, or ``None``.

        The model is not loaded here: importing PyTorch and reading a
        checkpoint takes seconds and must not block the UI.
        :func:`compare_with_model` loads it on the worker thread, before the
        comparison starts. The callable holds the model and nothing of the
        panel's: it is all the worker needs.
        """
        run_dir = self.run_dir.path()
        if not run_dir or not Path(run_dir).is_dir():
            return None
        if not kudio_enhance_available():
            raise RuntimeError(INSTALL_HINT)

        model = self._model

        def enhance(y, sr):
            return model.enhance(y, sr, run_dir)

        return (Path(run_dir).name, enhance)

    def _sweep_arguments(self) -> dict:
        """``methods=`` / ``noises=`` / ``noise=`` for the chosen sweep."""
        sweep = self.sweep.currentText()
        if sweep == SWEEP_METHODS:
            arguments = {"methods": None, "noise": self.noise.currentData()}
        elif sweep == SWEEP_NOISES:
            arguments = {"methods": [self.method.currentData()],
                         "noises": list(kudio.NOISE_ESTIMATORS)}
        else:
            arguments = {"methods": None,
                         "noises": list(kudio.NOISE_ESTIMATORS)}

        learned = self._learned_entry()
        if learned is not None:
            base = arguments["methods"] or list(kudio.METHODS)
            arguments["methods"] = list(base) + [learned]
        return arguments

    def _update_call(self) -> None:
        try:
            arguments = self._sweep_arguments()
        except RuntimeError as e:               # kudio-enhance not installed
            self.call.setText(str(e))
            return
        parts = []
        methods = arguments["methods"]
        if methods is not None:
            parts.append("[" + ", ".join(
                repr(m) if isinstance(m, str) else f"({m[0]!r}, model.enhance)"
                for m in methods) + "]")
        parts.append("reference=None"
                     if self.reference.currentText() == REF_NONE
                     else "reference=ref")
        parts.append("noises=list(kudio.NOISE_ESTIMATORS)" if "noises" in arguments
                     else f"noise={arguments['noise']!r}")
        parts.append(f"metrics={self.metrics.isChecked()}")
        self.call.setText(
            f"kudio.compare_enhancers(y, sr, {', '.join(parts)})")

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("Comparing…" if busy else "Run comparison")

    def _pick_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the clean reference", "",
            "Audio (*.wav *.flac *.mp3 *.ogg)")
        if path:
            self._ref_path = Path(path)
            self.ref_edit.setText(self._ref_path.name)

    def on_session_changed(self) -> None:
        """New audio means the stored results describe something else."""
        self.cancel_preview()
        self._results = []
        self._shown = None
        self.table.setRowCount(0)
        self.verdict.setText("")
        for button in (self.audition_button, self.apply_button, self.save_button):
            button.setEnabled(False)

    # ------------------------------------------------------------------ run

    def _reference_audio(self) -> Optional[np.ndarray]:
        choice = self.reference.currentText()
        if choice == REF_NONE:
            return None
        if choice == REF_FILE:
            if self._ref_path is None:
                raise RuntimeError("Select a reference file first.")
            return kudio.file_load(self._ref_path, sr=self.session.sr)[0]
        original = self.session.original()
        if original is None:
            raise RuntimeError("This session has no original to compare against.")
        y0, sr0 = original
        if sr0 != self.session.sr:
            return kudio.resample(y0, sr0, self.session.sr)
        return y0

    def _run(self) -> None:
        if not self.require_audio():
            return
        self.cancel_preview()
        try:
            reference = self._reference_audio()
        except RuntimeError as e:
            self.report(str(e))
            return

        y = np.asarray(self.session.y, dtype=np.float32)
        sel = self.session.selection() if self.selection_only.isChecked() else None
        if sel is not None:
            sl = self.session.selected_slice()
            y = y[sl]
            if reference is not None:
                reference = np.asarray(reference)[sl]
        if len(y) < 512:
            self.report("Too short to compare — select at least 512 samples.")
            return

        try:
            arguments = self._sweep_arguments()
        except RuntimeError as e:
            self.report(str(e))
            return
        methods = arguments.pop("methods")
        entries = methods or list(kudio.METHODS)
        # a custom entry brings its own noise estimate, so it runs once
        swept = len(arguments.get("noises") or [1])
        runs = sum(swept if isinstance(m, str) else 1 for m in entries)
        learned_dir = self.run_dir.path() if any(
            not isinstance(m, str) for m in entries) else None
        self.report(f"Running {runs} combination(s) …")
        started = self.jobs.run(
            compare_with_model, y, self.session.sr, methods,
            model=self._model, learned_dir=learned_dir,
            reference=reference, metrics=self.metrics.isChecked(),
            **arguments, on_done=self._finished, on_failed=self._failed)
        if not started:
            self.report("A comparison is already running.")
            return
        self._pending = RunContext(
            y=self.session.y,
            ran_on=(sl.start, sl.stop) if sel is not None else None,
            learned_dir=learned_dir,
            model=model_state(learned_dir) if learned_dir else None)

    def _finished(self, results) -> None:
        context, self._pending = self._pending, None
        if context is None or self.session.y is not context.y:
            # computed from audio the session no longer holds: ranked, it
            # would describe something else, and applied, it would put the
            # old audio back over the edit
            self.report("The audio changed while the comparison ran, so its "
                        "results describe audio that is no longer there — "
                        "run it again.")
            return
        self._shown = context
        self._results = list(results)
        self._fill_table()
        self.save_button.setEnabled(any(r.error is None for r in self._results))
        self._announce_winner()

    def _failed(self, message: str) -> None:
        self._pending = None
        self.report(f"Comparison failed — {message}")

    # --------------------------------------------------------------- results

    def _fill_table(self) -> None:
        has_reference = any(r.si_sdr_db is not None for r in self._results)
        self.table.setSortingEnabled(False)      # or rows move while filling
        self.table.setRowCount(len(self._results))
        for row, result in enumerate(self._results):
            label = kudio.METHODS[result.method].label \
                if result.method in kudio.METHODS else result.method
            cells = [label, result.noise]
            if result.error:
                cells += [result.error] + [""] * (len(COLUMNS) - 3)
            else:
                cells += [
                    f"{result.seconds * 1000:.0f}",
                    f"{result.noise_floor_db:+.1f}",
                    f"{result.lufs:+.1f}" if np.isfinite(result.lufs) else "—",
                    _fmt(result.si_sdr_db, "{:+.2f}"),
                    _fmt(result.delta_snr_db, "{:+.2f}"),
                    _fmt(result.pesq, "{:.2f}"),
                    _fmt(result.stoi, "{:.3f}"),
                ]
            for column, text in enumerate(cells):
                item = NumericItem(text)
                # sort by value, not by text: "9.66" must beat "10.2".
                # The two label columns sort in kudio's own order rather than
                # alphabetically -- the library lists them cheapest-first.
                value = float(row) if column < 2 else _as_number(text)
                if value is not None:
                    item.setData(Qt.UserRole, value)
                # a row is a (method, estimator) pair; the method alone stopped
                # identifying one the moment the grid could hold both
                item.setData(Qt.UserRole + 1, (result.method, result.noise))
                self.table.setItem(row, column, item)

        for column in REFERENCE_COLUMNS:
            self.table.setColumnHidden(column, not has_reference)
        self.table.setSortingEnabled(True)
        # A ranking table should open ranked. With a reference that means best
        # first; without one there is nothing to rank by, so keep kudio's order.
        if has_reference:
            self.table.sortItems(COLUMNS.index("SI-SDR"), Qt.DescendingOrder)
        else:
            self.table.sortItems(0, Qt.AscendingOrder)
        self.table.resizeColumnsToContents()

    def _announce_winner(self) -> None:
        usable = [r for r in self._results if r.error is None]
        if not usable:
            self.verdict.setText("Every method failed.")
            self.verdict.setStyleSheet(f"color: {HINT_COLOR};")
            self.report("Comparison finished — nothing succeeded.")
            return

        scored = [r for r in usable if r.si_sdr_db is not None]
        if scored:
            best = max(scored, key=lambda r: r.si_sdr_db)
            spread = self._axis_spread(scored)
            improved = [r for r in scored
                        if r.delta_snr_db is not None and r.delta_snr_db > 0]
            if not improved:
                # Every method moved the audio *away* from the reference. The
                # usual cause is that the reference is the noisy recording
                # itself, which is the one thing it must not be -- scoring
                # against it measures how much you changed the audio, not
                # whether you improved it.
                self.verdict.setText(
                    f"<b>No method beat leaving it alone</b> — the closest was "
                    f"{self._label(best.method)} at "
                    f"{best.delta_snr_db:+.2f} dB. That usually means the "
                    f"reference is not clean: scoring against the noisy "
                    f"recording measures how much you changed it, not whether "
                    f"you improved it.")
                self.verdict.setStyleSheet(f"color: {WARN_COLOR};")
            else:
                self.verdict.setText(
                    f"Best against the reference: <b>"
                    f"{self._label(best.method)} + {best.noise}</b> at "
                    f"{best.si_sdr_db:+.2f} dB SI-SDR, "
                    f"{best.delta_snr_db:+.2f} dB better than leaving it alone."
                    + spread)
                self.verdict.setStyleSheet(f"color: {OK_COLOR};")
        else:
            quietest = min(usable, key=lambda r: r.noise_floor_db)
            self.verdict.setText(
                f"No reference, so there is no quality score. The quietest "
                f"residue is <b>{self._label(quietest.method)} + "
                f"{quietest.noise}</b> at {quietest.noise_floor_db:+.1f} dBFS — "
                f"which is also what you get by deleting the speech. Audition "
                f"before believing it.")
            self.verdict.setStyleSheet(f"color: {HINT_COLOR};")
        self.report(f"Comparison finished — {len(usable)}/{len(self._results)} "
                    f"combination(s) succeeded.")

    @staticmethod
    def _label(method: str) -> str:
        """A custom enhancer has no entry in kudio.METHODS; its name is its label."""
        spec = kudio.METHODS.get(method)
        return spec.label if spec is not None else method

    #: Below this relative gap the two axes are called comparable rather than
    #: one being declared the winner. Measured on the repo's own fixture they
    #: land within 1%, and a table that crowns an axis on a 0.1 dB difference
    #: is inventing a finding.
    AXIS_TIE = 0.20

    @staticmethod
    def _axis_spread(scored) -> str:
        """Say which of the two choices moved the result more — or that neither did.

        Enhancement is two decisions, and which one to spend effort on is a
        real question. It is answered per clip: on some the estimator dominates,
        on some the gain rule, and on many they are the same size.
        """
        # a custom enhancer sits on neither axis, so it cannot say anything
        # about which of the two moved the result
        scored = [r for r in scored if r.noise != kudio.CUSTOM_NOISE]
        methods = {r.method for r in scored}
        noises = {r.noise for r in scored}
        if len(methods) < 2 or len(noises) < 2:
            return ""

        def spread(group_by) -> float:
            # the *mean* spread, not the widest: one lucky combination should
            # not decide which axis matters
            groups = [[r.si_sdr_db for r in scored if group_by(r) == key]
                      for key in {group_by(r) for r in scored}]
            return float(np.mean([max(g) - min(g) for g in groups if len(g) > 1]))

        across_rules = spread(lambda r: r.noise)     # varying rule, fixed noise
        across_noise = spread(lambda r: r.method)    # varying noise, fixed rule
        wider = max(across_rules, across_noise)
        narrower = min(across_rules, across_noise)

        if wider - narrower <= ComparePanel.AXIS_TIE * max(wider, 1e-9):
            return (f"<br>The gain rule and the noise estimator moved this clip "
                    f"about equally ({across_rules:.1f} dB against "
                    f"{across_noise:.1f} dB), so neither is the one to spend "
                    f"the effort on.")
        name = "noise estimator" if across_noise > across_rules else "gain rule"
        other = "gain rule" if name == "noise estimator" else "noise estimator"
        return (f"<br>The {name} moved this clip more than the {other} did "
                f"({wider:.1f} dB against {narrower:.1f} dB).")

    def _selected_rows(self) -> List:
        """Every selected row's result, in the table's own top-to-bottom order.

        Not selection order: the table is ranked, and hearing them in the order
        they are listed is what makes "the one above sounds better" a sentence
        about the ranking rather than about which cell was clicked first.
        """
        model = self.table.selectionModel()
        rows = sorted(index.row() for index in model.selectedRows()) if model else []
        by_key = {(r.method, r.noise): r for r in self._results}
        found = []
        for row in rows:
            item = self.table.item(row, 0)
            result = by_key.get(item.data(Qt.UserRole + 1)) if item else None
            if result is not None:
                found.append(result)
        return found

    def _on_selection_changed(self) -> None:
        usable = [r for r in self._selected_rows() if r.error is None]
        self.audition_button.setEnabled(bool(usable))
        self.apply_button.setEnabled(len(usable) == 1)
        self.apply_button.setToolTip(
            "Select one row to apply it." if len(usable) > 1 else "")
        self.audition_button.setText(
            "♪  Audition A→B" if len(usable) > 1 else "♪  Audition")
        if self.audition_button.isChecked():
            self._start_audition()          # follow the selection while playing

    def _on_rows_moved(self) -> None:
        """Replay the selection in its new order, when a sort gave it one.

        Only then: a sort that leaves the selected rows in the same order
        would otherwise restart the loop for nothing, from the top of the
        first row.
        """
        if not self.audition_button.isChecked():
            return
        playing = tuple((r.method, r.noise) for r in self._audible_rows())
        if playing != self._heard:
            self._start_audition()

    # ------------------------------------------------------------- audition

    def _on_audition_toggled(self, on: bool) -> None:
        if not on:
            self.preview_stopped.emit()
            return
        self._start_audition()

    def _start_audition(self) -> None:
        """Play the selected rows, back to back, on a loop.

        Two rows joined into one loop is the comparison people actually make —
        clicking between them puts a gap and a mouse movement in the middle of
        the only thing being judged. A short silence marks the seam so the
        switch is audible as a switch.
        """
        usable = self._audible_rows()
        self._heard = tuple((r.method, r.noise) for r in usable)
        if not usable:
            self.cancel_preview()
            return

        if len(usable) == 1:
            audio = np.asarray(usable[0].audio, dtype=np.float32)
            self.report(f"Auditioning {usable[0].label} — "
                        f"nothing has been committed.")
        else:
            gap = np.zeros(int(AB_GAP_SECONDS * self.session.sr),
                           dtype=np.float32)
            pieces = []
            for result in usable:
                pieces.extend([np.asarray(result.audio, dtype=np.float32), gap])
            audio = np.concatenate(pieces[:-1])
            self.report("Auditioning " + " → ".join(r.label for r in usable)
                        + " — nothing has been committed.")
        self.preview_ready.emit(audio, self.session.sr)

    def _audible_rows(self) -> List:
        """The selected rows that have audio to play, in the table's order."""
        return [r for r in self._selected_rows()
                if r.error is None and r.audio.size]

    def cancel_preview(self) -> None:
        if self.audition_button.isChecked():
            self.audition_button.blockSignals(True)
            self.audition_button.setChecked(False)
            self.audition_button.blockSignals(False)

    # ----------------------------------------------------------------- apply

    def _apply(self) -> None:
        # The button is enabled for exactly one usable row, so that is the row
        # applied -- not whichever selected row is on top, which can be a
        # failed one sitting above it.
        usable = [r for r in self._selected_rows() if r.error is None]
        if len(usable) != 1:
            return
        result = usable[0]
        self.cancel_preview()
        audio = np.asarray(result.audio, dtype=np.float32)
        into_selection = (self.selection_only.isChecked()
                          and self.session.selection() is not None)
        target = None
        if into_selection:
            sl = self.session.selected_slice()
            target = (sl.start, sl.stop)
        # The code recomputes the result from the audio it replaces. Put
        # somewhere other than where it was computed -- the selection moved
        # after the run -- it came from audio the step's code never sees.
        shown = self._shown
        code = (self._script(result, shown)
                if shown is not None and self.session.y is shown.y
                and target == shown.ran_on else None)
        if into_selection:
            self.session.replace_selection(audio, undo_label=result.label,
                                           code=code)
        else:
            self.session.set_audio(audio, self.session.sr,
                                   undo_label=result.label, keep_original=True,
                                   code=code)
        self.report(f"Applied {result.label}. A/B it against the original "
                    f"with Ctrl+B.")

    @staticmethod
    def _script(result, context: RunContext) -> Optional[str]:
        """The code that computes *result* again, the way compare_enhancers did.

        A statistical row is one `spectral_enhance` call with the library's
        defaults, since the panel passes nothing else; the learned row is the
        same computation the Enhance panel records, from the run directory
        the row was computed with.
        """
        if result.noise == kudio.CUSTOM_NOISE:
            if (context.learned_dir is None
                    or result.method != Path(context.learned_dir).name):
                return None
            return (learned_code(context.learned_dir, context.model)
                    + "\ny = np.asarray(out, dtype=np.float32)")
        return (f"y = np.asarray(kudio.spectral_enhance(y, sr, "
                f"{result.method!r}, noise={result.noise!r}), "
                f"dtype=np.float32)")

    def _save_all(self) -> None:
        """Write every result, so they can be compared outside this window."""
        usable = [r for r in self._results if r.error is None]
        if not usable:
            return
        folder = QFileDialog.getExistingDirectory(self, "Save every result to")
        if not folder:
            return
        stem = Path(self.session.name or "audio").stem
        written = 0
        try:
            for result in usable:
                # the estimator is part of the identity; two rows can share a
                # method and would otherwise overwrite each other
                name = f"{stem}_{result.method}_{result.noise}.wav"
                kudio.save_wave(Path(folder) / name, result.audio,
                                self.session.sr)
                written += 1
        except Exception as e:                                  # noqa: BLE001
            self.report(f"Saved {written} of {len(usable)} — "
                        f"{type(e).__name__}: {e}")
            return
        self.report(f"Wrote {written} file(s) to {folder}")


def compare_with_model(y: np.ndarray, sr: int, methods, *,
                       model: LearnedModel, learned_dir: Optional[str],
                       **kwargs):
    """`kudio.compare_enhancers`, with the learned row's model ready first.

    Run on the worker thread. compare_enhancers times each row from the
    moment it is called, so a model loaded inside its row is charged for
    importing PyTorch and reading the checkpoint -- for a small model,
    hundreds of times what the denoise costs -- in a column whose only use is
    setting rows against each other. Loaded here instead, and kept in *model*
    for the next comparison on the same run, the row's time is the denoise.

    The model also runs once over a short excerpt: the first pass through a
    network pays one-time set-up that no later pass does, which is the same
    reason compare_enhancers warms up its own methods before timing any of
    them.
    """
    if learned_dir:
        try:
            model.enhance(y[:sr // 2], sr, learned_dir)
        except Exception:                                       # noqa: BLE001
            # left for the row to raise again, so a model that will not load
            # fails its own row, as any method does, rather than the whole
            # comparison
            log.debug("preparing %s failed", learned_dir, exc_info=True)
    return kudio.compare_enhancers(y, sr, methods, **kwargs)


def _fmt(value, spec: str) -> str:
    return "—" if value is None or not np.isfinite(value) else spec.format(value)


def _as_number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None
