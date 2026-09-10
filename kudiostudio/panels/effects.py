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
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
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
from kudiostudio.theme import ACCENT_COLOR

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

    #: The order here is the order of the parameter pages in ``self.params``;
    #: the two are indexed together, so a new effect needs its page inserted at
    #: the matching position in :meth:`_build_param_pages`.
    EFFECTS = ("gain", "random_gain",
               "fade_in", "fade_out", "fade", "reverse", "remove_dc",
               "highpass", "lowpass", "bandpass", "bandstop",
               "pitch_shift", "time_stretch", "reverb", "add_noise_snr",
               "trim_silence", "vad_trim", "split_on_silence")

    #: fade shapes offered, in kudio's own vocabulary
    FADE_SHAPES = ("linear", "cosine", "exponential")

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._noise_path: Optional[Path] = None
        #: what the last split_on_silence produced, so it can be written out
        #: instead of being counted and thrown away
        self._segments: list = []

        self.effect = QComboBox()
        self.effect.addItems(self.EFFECTS)

        # built before the parameter pages, which wire their spin boxes to
        # _update_call and through it to the preview
        self.preview_button = QPushButton("♪  Preview")
        self.preview_button.setCheckable(True)
        self.preview_button.setToolTip(
            "Hear the effect on the selection without applying it.\n"
            "Loops, and re-renders as you change the parameters.")
        self.preview_button.setStyleSheet(
            f"QPushButton:checked {{ background: {ACCENT_COLOR}; color: #1b1d21;"
            f" font-weight: bold; }}")
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)     # coalesce a spin box drag
        self._preview_timer.timeout.connect(self._render_preview)

        self.params = QStackedWidget()
        self._build_param_pages()

        self.apply_button = QPushButton("Apply")
        self.undo_button = QPushButton("Undo")
        self.revert_button = QPushButton("Revert to original")
        self.call = self.call_label()

        buttons = QHBoxLayout()
        buttons.addWidget(self.preview_button)
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
            "otherwise to the whole clip. Every Apply is undoable — "
            "Preview does not change anything at all."))
        layout.addStretch(1)

        self.effect.currentIndexChanged.connect(self.params.setCurrentIndex)
        self.effect.currentIndexChanged.connect(self._update_call)
        self.preview_button.toggled.connect(self._on_preview_toggled)
        self.apply_button.clicked.connect(self._apply)
        self.undo_button.clicked.connect(self._undo)
        self.revert_button.clicked.connect(self._revert)
        # clamp the cutoffs to this clip's Nyquist straight away: the 8 kHz
        # low-pass default is exactly Nyquist on a 16 kHz clip, so an untouched
        # panel would otherwise offer a value that cannot be applied
        self.on_session_changed()
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

        # fade_in / fade_out / fade
        self.fade_in_s = _spin(0.0, 60.0, 0.01, 0.01, 3, " s")
        self.fade_in_shape = QComboBox()
        self.fade_in_shape.addItems(self.FADE_SHAPES)
        self.params.addWidget(self._page([("duration", self.fade_in_s),
                                          ("shape", self.fade_in_shape)]))

        self.fade_out_s = _spin(0.0, 60.0, 0.01, 0.01, 3, " s")
        self.fade_out_shape = QComboBox()
        self.fade_out_shape.addItems(self.FADE_SHAPES)
        self.params.addWidget(self._page([("duration", self.fade_out_s),
                                          ("shape", self.fade_out_shape)]))

        self.fade_both_in = _spin(0.0, 60.0, 0.01, 0.01, 3, " s")
        self.fade_both_out = _spin(0.0, 60.0, 0.01, 0.01, 3, " s")
        self.fade_both_shape = QComboBox()
        self.fade_both_shape.addItems(self.FADE_SHAPES)
        self.params.addWidget(self._page([("fade_in_s", self.fade_both_in),
                                          ("fade_out_s", self.fade_both_out),
                                          ("shape", self.fade_both_shape)]))

        # reverse / remove_dc take nothing beyond the audio
        self.params.addWidget(self._page(
            [("", self.hint("Plays the selection backwards."))]))
        self.params.addWidget(self._page(
            [("", self.hint("Subtracts the mean, per channel. An offset that "
                            "drifts over a long recording is a very low "
                            "frequency — use highpass for that."))]))

        # filters. The upper bounds follow the clip's Nyquist; see
        # _sync_filter_ranges, which runs whenever the audio changes.
        self.hp_cutoff = _spin(1.0, 96000.0, 80.0, 10.0, 1, " Hz")
        self.hp_order = _int_spin(1, 10, 4)
        self.hp_zero_phase = QCheckBox("zero-phase (no time shift)")
        self.hp_zero_phase.setChecked(True)
        self.params.addWidget(self._page([("cutoff", self.hp_cutoff),
                                          ("order", self.hp_order),
                                          ("", self.hp_zero_phase)]))

        self.lp_cutoff = _spin(1.0, 96000.0, 8000.0, 100.0, 1, " Hz")
        self.lp_order = _int_spin(1, 10, 4)
        self.lp_zero_phase = QCheckBox("zero-phase (no time shift)")
        self.lp_zero_phase.setChecked(True)
        self.params.addWidget(self._page([("cutoff", self.lp_cutoff),
                                          ("order", self.lp_order),
                                          ("", self.lp_zero_phase)]))

        self.bp_low = _spin(1.0, 96000.0, 300.0, 10.0, 1, " Hz")
        self.bp_high = _spin(1.0, 96000.0, 3400.0, 100.0, 1, " Hz")
        self.bp_order = _int_spin(1, 10, 4)
        self.params.addWidget(self._page([("low", self.bp_low),
                                          ("high", self.bp_high),
                                          ("order", self.bp_order)]))

        self.bs_low = _spin(1.0, 96000.0, 45.0, 1.0, 1, " Hz")
        self.bs_high = _spin(1.0, 96000.0, 55.0, 1.0, 1, " Hz")
        self.bs_order = _int_spin(1, 10, 4)
        self.params.addWidget(self._page([("low", self.bs_low),
                                          ("high", self.bs_high),
                                          ("order", self.bs_order)]))

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

        # vad_trim -- the noise-adaptive counterpart of trim_silence
        self.vad_threshold = _spin(0.0, 40.0, 8.0, 0.5, 1, " dB")
        self.vad_pad = _spin(0.0, 2.0, 0.05, 0.01, 3, " s")
        self.params.addWidget(self._page([
            ("threshold_db", self.vad_threshold),
            ("pad", self.vad_pad),
            ("", self.hint("Measured against this recording's own noise "
                           "floor, so it survives a room trim_silence "
                           "cannot handle."))]))

        self.split_top_db = _spin(1, 90, 30.0, 1.0, 1, " dB")
        self.split_frame = _int_spin(64, 16384, 2048, 64)
        self.split_hop = _int_spin(16, 8192, 512, 16)
        self.split_result = QLabel("—")
        self.split_save_button = QPushButton("Save segments…")
        self.split_save_button.setEnabled(False)
        self.split_save_button.setToolTip(
            "Write each non-silent segment as its own .wav.")
        self.split_save_button.clicked.connect(self._save_segments)
        self.params.addWidget(self._page([("top_db", self.split_top_db),
                                          ("frame_length", self.split_frame),
                                          ("hop_length", self.split_hop),
                                          ("segments", self.split_result),
                                          ("", self.split_save_button)]))

        # PySide6's findChildren takes one type at a time, unlike PyQt5
        for page in range(self.params.count()):
            for cls in (QDoubleSpinBox, QSpinBox):
                for child in self.params.widget(page).findChildren(cls):
                    child.valueChanged.connect(self._update_call)
            for child in self.params.widget(page).findChildren(QComboBox):
                child.currentIndexChanged.connect(self._update_call)
            for child in self.params.widget(page).findChildren(QCheckBox):
                child.toggled.connect(self._update_call)

    #: every cutoff box, so the Nyquist clamp is applied in one place
    def _cutoff_boxes(self):
        return (self.hp_cutoff, self.lp_cutoff, self.bp_low, self.bp_high,
                self.bs_low, self.bs_high)

    def _sync_filter_ranges(self) -> None:
        """Cap the cutoff boxes at the clip's Nyquist.

        A cutoff above Nyquist is not a filter, it is a `FeatureError`. Letting
        the box offer 20 kHz on an 8 kHz clip means the failure arrives at
        Apply, several seconds after the mistake was made.
        """
        if not self.session.has_audio:
            return
        nyquist = self.session.sr / 2.0
        for box in self._cutoff_boxes():
            box.blockSignals(True)
            box.setMaximum(max(1.0, nyquist - 1.0))
            box.blockSignals(False)
        self._update_call()

    def on_session_changed(self) -> None:
        self._sync_filter_ranges()
        # the segments were cut out of audio that is no longer current; saving
        # them under the new clip's name would file them under the wrong take
        self._segments = []
        self.split_result.setText("—")
        self.split_save_button.setEnabled(False)

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

        if name == "fade_in":
            seconds, shape = self.fade_in_s.value(), self.fade_in_shape.currentText()
            return (f"kudio.fade_in(y, sr, duration={seconds}, shape={shape!r})",
                    lambda y, sr: kudio.fade_in(y, sr, duration=seconds,
                                                shape=shape))

        if name == "fade_out":
            seconds = self.fade_out_s.value()
            shape = self.fade_out_shape.currentText()
            return (f"kudio.fade_out(y, sr, duration={seconds}, shape={shape!r})",
                    lambda y, sr: kudio.fade_out(y, sr, duration=seconds,
                                                 shape=shape))

        if name == "fade":
            fade_in_s, fade_out_s = self.fade_both_in.value(), self.fade_both_out.value()
            shape = self.fade_both_shape.currentText()
            return (f"kudio.fade(y, sr, fade_in_s={fade_in_s}, "
                    f"fade_out_s={fade_out_s}, shape={shape!r})",
                    lambda y, sr: kudio.fade(y, sr, fade_in_s=fade_in_s,
                                             fade_out_s=fade_out_s, shape=shape))

        if name == "reverse":
            return "kudio.reverse(y)", lambda y, sr: kudio.reverse(y)

        if name == "remove_dc":
            return "kudio.remove_dc(y)", lambda y, sr: kudio.remove_dc(y)

        if name == "highpass":
            cutoff, order = self.hp_cutoff.value(), self.hp_order.value()
            zero_phase = self.hp_zero_phase.isChecked()
            return (f"kudio.highpass(y, sr, cutoff={cutoff}, order={order}, "
                    f"zero_phase={zero_phase})",
                    lambda y, sr: kudio.highpass(y, sr, cutoff=cutoff,
                                                 order=order,
                                                 zero_phase=zero_phase))

        if name == "lowpass":
            cutoff, order = self.lp_cutoff.value(), self.lp_order.value()
            zero_phase = self.lp_zero_phase.isChecked()
            return (f"kudio.lowpass(y, sr, cutoff={cutoff}, order={order}, "
                    f"zero_phase={zero_phase})",
                    lambda y, sr: kudio.lowpass(y, sr, cutoff=cutoff,
                                                order=order,
                                                zero_phase=zero_phase))

        if name == "bandpass":
            low, high = self.bp_low.value(), self.bp_high.value()
            order = self.bp_order.value()
            return (f"kudio.bandpass(y, sr, low={low}, high={high}, order={order})",
                    lambda y, sr: kudio.bandpass(y, sr, low=low, high=high,
                                                 order=order))

        if name == "bandstop":
            low, high = self.bs_low.value(), self.bs_high.value()
            order = self.bs_order.value()
            return (f"kudio.bandstop(y, sr, low={low}, high={high}, order={order})",
                    lambda y, sr: kudio.bandstop(y, sr, low=low, high=high,
                                                 order=order))

        if name == "vad_trim":
            threshold, pad = self.vad_threshold.value(), self.vad_pad.value()
            return (f"kudio.vad_trim(y, sr, threshold_db={threshold}, "
                    f"pad={pad})[0]",
                    lambda y, sr: kudio.vad_trim(y, sr, threshold_db=threshold,
                                                 pad=pad)[0])

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

    def _render(self) -> Tuple[str, object]:
        """Run the current effect over the selection. Raises on failure.

        The single place the effect is actually computed, so Preview and Apply
        cannot drift apart -- what you hear is what Apply will commit.
        """
        call_text, fn = self._operation()
        y = np.asarray(self.session.selected_audio(), dtype=np.float32)
        return call_text, fn(y, self.session.sr)

    def _apply(self) -> None:
        if not self.require_audio():
            return
        try:
            call_text, out = self._render()
        except Exception as e:
            self.report(f"{self.effect.currentText()} failed — {type(e).__name__}: {e}")
            return

        if self.effect.currentText() == "split_on_silence":
            self._segments = list(out)
            self.split_result.setText(f"{len(out)} segment(s)")
            self.split_save_button.setEnabled(bool(out))
            total = sum(len(seg) for seg in out) / self.session.sr
            self.report(f"split_on_silence → {len(out)} segment(s), "
                        f"{total:.2f}s of non-silent audio (nothing modified) "
                        f"— 'Save segments…' writes them out")
            return

        self.session.replace_selection(out, undo_label=self.effect.currentText())
        self.report(f"Applied {call_text}")

    def _save_segments(self) -> None:
        """Write the last split's segments as numbered files in one folder."""
        if not self._segments:
            self.report("Run split_on_silence first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Save segments to")
        if not folder:
            return
        stem = Path(self.session.name or "audio").stem
        written = 0
        try:
            for n, segment in enumerate(self._segments, 1):
                kudio.save_wave(Path(folder) / f"{stem}_{n:03d}.wav",
                                segment, self.session.sr)
                written += 1
        except Exception as e:
            self.report(f"Saved {written} of {len(self._segments)} — "
                        f"{type(e).__name__}: {e}")
            return
        self.report(f"Wrote {written} segment(s) to {folder}")

    def _undo(self) -> None:
        if not self.session.undo():
            self.report("Nothing to undo.")

    def _revert(self) -> None:
        if not self.session.revert():
            self.report("No original to revert to.")

    def _update_call(self) -> None:
        self.call.setText(self._operation()[0])
        if self.preview_button.isChecked():
            self._preview_timer.start()      # the call changed, so re-render

    # --------------------------------------------------------------- preview

    #: nothing to audition: it reports, it does not modify
    _NOT_PREVIEWABLE = ("split_on_silence",)

    def _on_preview_toggled(self, on: bool) -> None:
        if not on:
            self._preview_timer.stop()
            self.preview_stopped.emit()
            return
        if not self.session.has_audio:
            self.report("Load an audio file first.")
            self.cancel_preview()
            return
        if self.effect.currentText() in self._NOT_PREVIEWABLE:
            self.report(f"{self.effect.currentText()} does not change the audio, "
                        f"so there is nothing to preview.")
            self.cancel_preview()
            return
        self._render_preview()

    def _render_preview(self) -> None:
        if not (self.preview_button.isChecked() and self.session.has_audio):
            return
        try:
            _, out = self._render()
        except Exception as e:
            # a half-typed parameter should not spam the status bar, but a
            # preview that silently stops is worse
            self.report(f"Preview failed — {type(e).__name__}: {e}")
            self.cancel_preview()
            self.preview_stopped.emit()
            return
        self.preview_ready.emit(np.asarray(out, dtype=np.float32), self.session.sr)

    def cancel_preview(self) -> None:
        self._preview_timer.stop()
        if self.preview_button.isChecked():
            self.preview_button.blockSignals(True)
            self.preview_button.setChecked(False)
            self.preview_button.blockSignals(False)
