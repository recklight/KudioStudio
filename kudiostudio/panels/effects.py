# -*- coding: utf-8 -*-
"""Waveform effects and augmentation — `kudio.effects`.

Each entry is one `kudio` function. The caption under the parameters is the
exact call that Apply will make, and Apply records it in the session's history,
so a session here translates straight into a script -- File → Export edits as
Python script writes that script out.
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
from kudiostudio.theme import ACCENT_COLOR, INK_COLOR, WARN_COLOR

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
    """Gain, pitch, time, rooms, channels, noise mixing and silence trimming.

    Several of these degrade rather than improve, and that is the point: a
    training set needs the degradation its model will meet, and all three
    kinds are here. `add_noise_snr` is **additive**, `room` is
    **convolutive** -- a real impulse response, reproducible from its seed --
    and `telephone` / `bit_depth` / `dropouts` are the **channel**, which is
    neither and cannot be written as arithmetic on the signal at all.

    `measured_room` is the same convolution with a room somebody recorded.
    `room` is statistical by design and has no early reflections, so when the
    shape of the early energy is what matters, a measured response is the
    only honest stand-in.
    """

    title = "Effects"
    icon = "effects"

    #: The order here is the order of the parameter pages in ``self.params``;
    #: the two are indexed together, so a new effect needs its page inserted at
    #: the matching position in :meth:`_build_param_pages`.
    EFFECTS = ("gain", "random_gain",
               "fade_in", "fade_out", "fade", "reverse", "remove_dc",
               "highpass", "lowpass", "bandpass", "bandstop",
               "pitch_shift", "time_stretch", "reverb", "room", "measured_room",
               "add_noise_snr", "telephone", "bit_depth", "dropouts",
               "trim_silence", "vad_trim", "split_on_silence")

    #: fade shapes offered, in kudio's own vocabulary
    FADE_SHAPES = ("linear", "cosine", "exponential")

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)
        self._noise_path: Optional[Path] = None
        #: the measured impulse response: the file, its samples, and their
        #: rate -- the clip's, or the file's own while no clip is open. Held
        #: rather than read at each Apply, because Preview re-renders on every
        #: knob turn.
        self._ir_path: Optional[Path] = None
        self._ir: Optional[np.ndarray] = None
        self._ir_sr: Optional[int] = None
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
            f"QPushButton:checked {{ background: {ACCENT_COLOR}; color: {INK_COLOR};"
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

        # room -- a real impulse response, unlike `reverb` above
        self.room_rt60 = _spin(0.05, 5.0, 0.5, 0.05, 2, " s")
        self.room_rt60.setToolTip(
            "How long the tail takes to fall 60 dB. A treated studio is under\n"
            "0.3 s, a living room around 0.5, a hall over 1.5.")
        self.room_drr = _spin(-20.0, 30.0, 0.0, 1.0, 1, " dB")
        self.room_drr.setToolTip(
            "Direct-to-reverberant ratio — distance, in a readable unit.\n"
            "A close microphone is +10 dB or more; a metre or two away is\n"
            "around 0; the far end of a hall is negative.")
        self.room_seed = QSpinBox()
        self.room_seed.setRange(-1, 10_000)
        self.room_seed.setValue(0)
        self.room_seed.setSpecialValueText("random")
        self.room_seed.setToolTip(
            "The same seed is the same room, so a dataset built from it can\n"
            "be rebuilt. -1 draws a new one each time.")
        self.params.addWidget(self._page([("rt60", self.room_rt60),
                                          ("drr_db", self.room_drr),
                                          ("seed", self.room_seed)]))

        # measured_room -- the same convolution, with a room someone recorded
        self.ir_edit = QLineEdit()
        self.ir_edit.setReadOnly(True)
        self.ir_edit.setPlaceholderText("pick an impulse response .wav …")
        ir_browse = QPushButton("Browse…")
        ir_browse.clicked.connect(self._pick_ir)
        ir_picker = QWidget()
        ir_picker_layout = QHBoxLayout(ir_picker)
        ir_picker_layout.setContentsMargins(0, 0, 0, 0)
        ir_picker_layout.addWidget(self.ir_edit)
        ir_picker_layout.addWidget(ir_browse)
        self.ir_align = QCheckBox("start at the direct sound")
        self.ir_align.setChecked(True)
        self.ir_align.setToolTip(
            "A measured response begins with the time the sound took to reach\n"
            "the microphone. Left in, it delays the audio by that much, and a\n"
            "processed selection comes back late against its neighbours.\n"
            "This starts the response at its loudest sample, which is the\n"
            "direct sound in any ordinary room -- where kudio.rir puts it.")
        self.ir_align.toggled.connect(self._show_ir)
        self.ir_measured = QLabel("—")
        self.ir_measured.setWordWrap(True)
        self.ir_measured.setToolTip(
            "kudio.rt60 on the response as it will be applied: at this clip's\n"
            "rate, in mono, from where it starts. Read the fit before the\n"
            "rest -- a file that is not an impulse response still produces a\n"
            "T30, and says so here.")
        self.params.addWidget(self._page([("ir", ir_picker),
                                          ("", self.ir_align),
                                          ("measured", self.ir_measured)]))

        # telephone / bit_depth / dropouts -- the channel
        self.phone_codec = QComboBox()
        self.phone_codec.addItems(["mu_law", "a_law", "none"])
        self.phone_codec.setToolTip(
            "G.711 companding. `none` band-limits and resamples without it,\n"
            "which separates what the line did from what the codec did.")
        self.params.addWidget(self._page([("codec", self.phone_codec)]))

        self.depth_bits = QSpinBox()
        self.depth_bits.setRange(2, 24)
        self.depth_bits.setValue(8)
        self.depth_dither = QCheckBox("dither")
        self.depth_dither.setToolTip(
            "Add a triangular dither of one step before rounding. It raises\n"
            "the noise floor and decorrelates the error from the signal,\n"
            "which is what stops quiet passages sounding granular.")
        self.params.addWidget(self._page([("bits", self.depth_bits),
                                          ("", self.depth_dither)]))

        self.loss_pct = _spin(0.0, 100.0, 5.0, 1.0, 1, " %")
        self.loss_pct.setToolTip(
            "Fraction of packets to drop. Achieved exactly, so a corpus\n"
            "labelled 5% carries 5%.")
        self.loss_packet = _spin(5.0, 200.0, 20.0, 5.0, 0, " ms")
        self.loss_packet.setToolTip(
            "Packet length. 20 ms is G.711 over RTP. The loss rate is a\n"
            "fraction of packets, so this changes whether the damage arrives\n"
            "as many short holes or a few long ones — not how much of it.")
        # whole packets: a run of 1.5 is a mean nobody types on purpose, and
        # anything under 1 is not a run length at all
        self.loss_burst = _spin(0.0, 50.0, 0.0, 1.0, 0, " pkt")
        self.loss_burst.setSpecialValueText("independent")
        self.loss_burst.setToolTip(
            "Mean run length. Real links lose packets in runs — a fade, a\n"
            "queue overflowing — and a run is far harder to conceal than the\n"
            "same packets scattered about, because there is no recent speech\n"
            "left to repeat.\n\n"
            "`independent` is not runs of 1: independent draws land side by\n"
            "side sometimes, so they already average 1/(1-loss) — 1.05\n"
            "packets at 5%. Asking for 1 is asking for something less\n"
            "clustered than leaving it alone.")
        self.loss_conceal = QComboBox()
        self.loss_conceal.addItems(["silence", "hold"])
        self.loss_conceal.setToolTip(
            "`hold` repeats the last packet that arrived, the crudest\n"
            "concealment a decoder does. Whether it measures better or worse\n"
            "than a hole is decided by whether the repeat lands in phase —\n"
            "an accident of the talker's pitch against the packet length —\n"
            "so a waveform metric cannot choose a concealment strategy.\n\n"
            "Across a burst it tiles one 20 ms packet over the whole gap,\n"
            "and there it is unambiguously worse.")
        self.loss_seed = QSpinBox()
        self.loss_seed.setRange(-1, 10_000)
        self.loss_seed.setValue(0)
        self.loss_seed.setSpecialValueText("random")
        self.params.addWidget(self._page([("loss", self.loss_pct),
                                          ("packet_ms", self.loss_packet),
                                          ("burst", self.loss_burst),
                                          ("conceal", self.loss_conceal),
                                          ("seed", self.loss_seed)]))

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
        # The response is held at the clip's rate. A clip at another rate --
        # or the first clip, after one picked with nothing open -- needs it
        # read again from the file: resampling the held copy would be a second
        # resampling, and not the read the recorded call makes.
        if (self._ir_path is not None and self.session.has_audio
                and self.session.sr != self._ir_sr
                and not self.load_ir(self._ir_path)):
            self._forget_ir()
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

    def _pick_ir(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select an impulse response", "",
            "Audio (*.wav *.flac *.mp3 *.ogg)")
        if path:
            self.load_ir(path)

    def load_ir(self, path) -> bool:
        """Read a measured impulse response for `measured_room`, and measure it.

        It is read the way the recorded call reads it -- ``kudio.file_load``
        at the clip's rate, in mono -- so the response Preview and Apply use
        is, sample for sample, the one an exported script loads. With no clip
        open it keeps the file's own rate until one arrives.

        Returns ``False``, leaving any response already loaded in place, when
        the file cannot be read or holds nothing but silence.
        """
        path = Path(path)
        rate = self.session.sr if self.session.has_audio else None
        try:
            ir, sr = kudio.file_load(path, sr=rate)
        except Exception as e:
            self.report(f"Could not read {path.name} — {type(e).__name__}: {e}")
            return False
        if ir.size < 2 or not np.any(ir):
            self.report(f"{path.name} is silent — there is no room in it to "
                        f"apply.")
            return False
        self._ir_path, self._ir, self._ir_sr = path, ir, int(sr)
        self.ir_edit.setText(path.name)
        self._show_ir()
        self._update_call()
        return True

    def _forget_ir(self) -> None:
        self._ir_path = self._ir = self._ir_sr = None
        self.ir_edit.clear()
        self._show_ir()
        self._update_call()

    def _ir_onset(self) -> int:
        """Where the applied response starts: the direct sound, or sample 0."""
        if self._ir is None or not self.ir_align.isChecked():
            return 0
        return int(np.argmax(np.abs(self._ir)))

    def _show_ir(self) -> None:
        """Say what the loaded room does, measured the way it will be applied.

        The response that is measured is the one convolved: resampled to this
        clip, downmixed, trimmed to the direct sound when asked. A hall
        measured at 48 kHz and applied at 8 kHz has lost its top octaves, and
        the early decay of a response that still starts with its travel time
        is partly a measurement of the silence before it.
        """
        if self._ir is None:
            self.ir_measured.setText("—")
            self.ir_measured.setStyleSheet("")
            return
        onset = self._ir_onset()
        direct_ms = 1000.0 * int(np.argmax(np.abs(self._ir))) / self._ir_sr
        head = (f"{len(self._ir) / self._ir_sr:.2f} s at {self._ir_sr} Hz, "
                f"direct sound at {direct_ms:.1f} ms")
        if onset:
            head += " — applied from there"
        try:
            room = kudio.rt60(self._ir[onset:], self._ir_sr)
        except Exception as e:
            self.ir_measured.setText(f"{head}\ncannot be measured — {e}")
            self.ir_measured.setStyleSheet(f"color: {WARN_COLOR};")
            return
        self.ir_measured.setText(f"{head}\n{room}")
        # an unreliable measurement says why in its own text; the colour is so
        # that it is not read past
        self.ir_measured.setStyleSheet(
            "" if room.reliable() else f"color: {WARN_COLOR};")

    # ----------------------------------------------------------------- apply

    def _seed(self, box: QSpinBox, pin: bool = False) -> Optional[int]:
        """The box's seed. "Random" is ``None`` -- or, with *pin*, a fresh draw.

        Apply pins it. Random still means a different result every time, but
        the call that goes into the history names the seed that was drawn, so
        the exported script repeats that result rather than rolling again.
        """
        if box.value() >= 0:
            return box.value()
        return int(np.random.default_rng().integers(2 ** 31)) if pin else None

    def _operation(self, exact: bool = False
                   ) -> Tuple[str, Callable[[np.ndarray, int], np.ndarray]]:
        """Return ``(call_text, fn(y, sr) -> y)`` for the selected effect.

        The caption shows the call as it reads best. *exact* asks for the call
        exactly as it will be made, runnable as it stands: a random seed
        pinned, a noise file or an impulse response named by its full path.
        That is the version Apply commits and records.
        """
        name = self.effect.currentText()

        if name == "gain":
            db = self.gain_db.value()
            return (f"kudio.gain(y, db={db})",
                    lambda y, sr: kudio.gain(y, db))

        if name == "random_gain":
            rng = (self.rg_lo.value(), self.rg_hi.value())
            seed = self._seed(self.rg_seed, exact)
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

        if name == "room":
            rt60 = self.room_rt60.value()
            drr = self.room_drr.value()
            seed = self._seed(self.room_seed, exact)
            return (f"kudio.apply_rir(y, kudio.rir(sr, rt60={rt60}, "
                    f"drr_db={drr}, seed={seed}))",
                    lambda y, sr: kudio.apply_rir(
                        y, kudio.rir(sr, rt60=rt60, drr_db=drr, seed=seed)))

        if name == "measured_room":
            return self._measured_room(exact)

        if name == "telephone":
            codec = self.phone_codec.currentText()
            return (f"kudio.telephone(y, sr, codec={codec!r})",
                    lambda y, sr: kudio.telephone(y, sr, codec=codec))

        if name == "bit_depth":
            bits = self.depth_bits.value()
            dither = self.depth_dither.isChecked()
            seed = self._seed(self.loss_seed, exact)
            # the seed only matters with dither, but it is passed either way,
            # and a caption that leaves out an argument is not the call
            return (f"kudio.bit_depth(y, bits={bits}, dither={dither}, "
                    f"seed={seed})",
                    lambda y, sr: kudio.bit_depth(y, bits, dither=dither,
                                                  seed=seed))

        if name == "dropouts":
            # one decimal of a percentage is three of a fraction; rounding
            # keeps 12.3 % from reaching kudio, and the caption, as
            # 0.12300000000000001
            loss = round(self.loss_pct.value() / 100.0, 6)
            packet = self.loss_packet.value()
            conceal = self.loss_conceal.currentText()
            # 0 is the spin box's "independent", which is `burst=None` and
            # not `burst=0` — the two mean different things and only one of
            # them is a number kudio accepts
            burst = self.loss_burst.value() or None
            seed = self._seed(self.loss_seed, exact)
            return (f"kudio.dropouts(y, sr, loss={loss!r}, "
                    f"packet_ms={packet!r}, burst={burst}, "
                    f"conceal={conceal!r}, seed={seed})",
                    lambda y, sr: kudio.dropouts(
                        y, sr, loss=loss, packet_ms=packet, burst=burst,
                        conceal=conceal, seed=seed))

        if name == "add_noise_snr":
            snr, seed = self.snr_db.value(), self._seed(self.noise_seed, exact)
            fn = (lambda y, sr: kudio.add_noise_snr(
                y, self._load_noise(sr), snr_db=snr, seed=seed))
            if exact and self._noise_path is not None:
                # what _load_noise does, spelled out, so the line runs anywhere
                noise = (f"kudio.file_load({str(self._noise_path.absolute())!r}, "
                         f"sr=sr)[0]")
                return (f"kudio.add_noise_snr(y, {noise}, snr_db={snr}, "
                        f"seed={seed})", fn)
            noise_name = self._noise_path.name if self._noise_path else "…"
            return (f"kudio.add_noise_snr(y, noise={noise_name!r}, "
                    f"snr_db={snr}, seed={seed})", fn)

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

    def _measured_room(self, exact: bool
                       ) -> Tuple[str, Callable[[np.ndarray, int], np.ndarray]]:
        """`apply_rir` with the loaded response, and the call that loads it.

        The call reads the file with ``kudio.file_load(..., sr=sr)`` -- what
        :meth:`load_ir` did -- and starts it at the direct sound with a plain
        slice, so it is one expression that runs as it stands. The slice is a
        sample count at this clip's rate, which is the rate the call loads it
        at.
        """
        onset = self._ir_onset()
        ir = None if self._ir is None else self._ir[onset:]
        held_at = self._ir_sr

        def fn(y, sr):
            if ir is None:
                raise ValueError("no impulse response loaded")
            if sr != held_at:
                raise ValueError(f"the impulse response is at {held_at} Hz "
                                 f"and the audio at {sr} Hz")
            return kudio.apply_rir(y, ir)

        if self._ir_path is None:
            source = "…"
        elif exact:
            source = str(self._ir_path.absolute())
        else:
            source = self._ir_path.name
        load = f"kudio.file_load({source!r}, sr=sr)[0]"
        if onset:
            load += f"[{onset}:]"
        return f"kudio.apply_rir(y, {load})", fn

    def _load_noise(self, sr: int) -> np.ndarray:
        if self._noise_path is None:
            raise ValueError("no noise file selected")
        noise, _ = kudio.file_load(self._noise_path, sr=sr)
        return noise

    def _render(self, exact: bool = False) -> Tuple[str, object]:
        """Run the current effect over the selection. Raises on failure.

        The single place the effect is actually computed, so Preview and Apply
        cannot drift apart -- what you hear is what Apply will commit. With
        *exact*, the call text is the one :meth:`_operation` makes runnable.
        """
        call_text, fn = self._operation(exact)
        y = np.asarray(self.session.selected_audio(), dtype=np.float32)
        return call_text, fn(y, self.session.sr)

    def _apply(self) -> None:
        if not self.require_audio():
            return
        try:
            call_text, out = self._render(exact=True)
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

        self.session.replace_selection(out, undo_label=self.effect.currentText(),
                                       code=f"y = {call_text}")
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
