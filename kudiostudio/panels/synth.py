# -*- coding: utf-8 -*-
"""Build a noisy dataset — `kudio.Synthesizer`.

Mixes every clean file with every noise file at the requested SNRs, which is
how you generate paired training data for a denoising model. The whole chain
between a talker and a file is available here::

    talker → room (rt60) → + noise (snr_ratio) → link (channel) → file

`snr_ratio` and `rt60` are swept and multiply the output count. `channel` is
not: a corpus is recorded over a phone line or it is not, so it is constant
across the run and lives in the manifest rather than in the file names.
"""
from __future__ import annotations

import logging
import math
import warnings
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Union

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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

#: What a mixture can be written as. PCM_16 clips at full
#: scale; FLOAT keeps whatever is past it.
SUBTYPES = ("PCM_16", "PCM_24", "FLOAT")

log = logging.getLogger(__name__)


def parse_snr(text: str) -> List[Union[int, float]]:
    """``"-5, 0, 5"`` -> ``[-5, 0, 5]``; ``"2.5, 2.7"`` -> ``[2.5, 2.7]``.

    A whole number comes back as an ``int`` and a fractional one as a
    ``float``. kudio normalises either into the same output name and the same
    manifest value, so the distinction is purely so that the call shown under
    the form stays ``[-5, 0, 5]`` rather than ``[-5.0, 0.0, 5.0]``.

    This used to be ``int(float(chunk))``, which silently truncated: ``2.5``
    became ``2``, and ``"2.5, 2.7"`` became ``[2, 2]`` -- two SNRs that then
    wrote the same file name, so one overwrote the other and the corpus came
    out half the size the panel claimed.
    """
    values: List[Union[int, float]] = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value = float(chunk)
        if value != value or value in (float('inf'), float('-inf')):
            raise ValueError(f"{chunk!r} is not a usable SNR")
        values.append(int(value) if value.is_integer() else value)
    if not values:
        raise ValueError("no SNR values given")
    return values


def parse_rt60(text: str) -> Optional[List[float]]:
    """``"0.3, 0.8"`` -> ``[0.3, 0.8]``; empty -> ``None`` (no rooms).

    Separate from :func:`parse_snr` because empty means something here: no
    reverberation at all, which is the default and has to stay reachable.
    """
    values = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            value = float(chunk)
            if value <= 0:
                raise ValueError(f"reverberation times must be positive, "
                                 f"got {value:g}")
            values.append(value)
    return values or None


class SynthResult(NamedTuple):
    """What one synthesis run produced, by name.

    `wanted` is what `syn()` was asked for and `on_disk` is what exists: two
    outputs that share a name leave one file, so the two differ exactly when
    something was overwritten. Named rather than positional because this grew
    from three fields to five and broke a caller doing so — and `.clipped` and
    the write subtype are still to be added.
    """

    produced: Optional[list]
    link: str
    targets: int
    wanted: int
    on_disk: int
    # Optional[...] = None rather than {} / (): a NamedTuple evaluates its
    # defaults once, so a mutable one is shared by every instance that omits it.
    clipped: Optional[dict] = None
    notices: Sequence[str] = ()
    subtype: str = "PCM_16"


class SynthPanel(Panel):
    """Batch clean × noise × SNR × room mixing, down an optional link.

    Noise is additive, a room is convolutive and the channel is neither, and
    a real recording has all three. Leaving `rt60` empty and `channel` at
    `none` keeps the original behaviour exactly.
    """

    title = "Synthesize"

    MODES = ("regular", "inc")

    #: kudio's own preset names, `none` first so the default is no link
    LINKS = ("none", "telephone", "telephone_a", "voip", "mu_law", "a_law")

    def __init__(self, session, parent: Optional[QWidget] = None):
        super().__init__(session, parent)

        self.clean = DirPicker("folder of clean audio")
        self.noise = DirPicker("folder of noise audio")
        self.out = DirPicker("output folder for the mixture")
        self.snr = QLineEdit("-5, 0, 5")
        self.snr.setToolTip(
            "Signal-to-noise ratios in dB, one corpus axis per value.\n\n"
            "Fractional values are allowed: 2.5 is written `_2p5dB`, because\n"
            "a decimal point in a file name reads as the start of the suffix.\n"
            "A whole number keeps its plain name, so `0` is still `_0dB`.")
        self.rt60 = QLineEdit("")
        self.rt60.setPlaceholderText("no rooms — e.g. 0.3, 0.6, 0.9")
        self.rt60.setToolTip(
            "Reverberation times in seconds. A fourth axis: in inc mode the\n"
            "output count multiplies by however many you give.\n\n"
            "The room acts on the speech before the noise is mixed in, which\n"
            "is the order a microphone meets them — so the SNR is measured\n"
            "against the reverberant speech. The clean file stays the target,\n"
            "so a model trained on this is asked to undo both.")
        self.drr = QDoubleSpinBox()
        self.drr.setRange(-20.0, 30.0)
        self.drr.setValue(0.0)
        self.drr.setSingleStep(1.0)
        self.drr.setDecimals(1)
        self.drr.setSuffix(" dB")
        self.drr.setToolTip(
            "Direct-to-reverberant ratio — distance, in a readable unit.\n"
            "+10 and above is a close microphone; 0 is a metre or two;\n"
            "negative is the far end of a hall.")
        self.subtype = QComboBox()
        self.subtype.addItems(SUBTYPES)
        self.subtype.setToolTip(
            "What the mixtures are written as.\n\n"
            "PCM_16 is the usual choice and clips anything past full scale —\n"
            "kudio says how many files and how far over, and they are listed\n"
            "under the result.\n\n"
            "FLOAT keeps values past 1.0 instead of clipping them, which is\n"
            "what you want when a loud talker at a low SNR would otherwise\n"
            "lose its peaks. PCM_24 still clips, with a finer step.")
        self.channel = QComboBox()
        self.channel.addItems(self.LINKS)
        self.channel.setToolTip(
            "Send every mixture down a link — the third kind of degradation,\n"
            "and the one that is neither additive nor convolutive.\n\n"
            "telephone: 300–3400 Hz at 8 kHz through G.711\n"
            "voip: G.711 with 5% of packets lost in runs of 4, held\n"
            "mu_law / a_law: the companding without the band-limiting\n\n"
            "Applied to the mixture, noise and all, because a wire carries\n"
            "whatever reached the microphone. Not an axis: it is constant\n"
            "for the run and is recorded in manifest.json, not in the names.")
        self.targets = QCheckBox("also write reverberant targets (TARGETS/)")
        self.targets.setToolTip(
            "Write the reverberant-but-clean signal beside each mixture.\n\n"
            "The clean file is the dry target, so a model trained on it is\n"
            "asked to undo the room as well as the noise. This is the same\n"
            "speech with the room left on, for training that should remove\n"
            "the noise and leave the room — a different and easier problem.\n\n"
            "Needs rt60: with no room the clean file already is that signal.")
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
        form.addRow("rt60", self.rt60)
        form.addRow("drr_db", self.drr)
        form.addRow("subtype", self.subtype)
        form.addRow("channel", self.channel)
        form.addRow("mode", self.mode)
        form.addRow("seed", self.seed)

        box = QGroupBox("kudio.Synthesizer")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(self.targets)
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
        self.drr.valueChanged.connect(self._update_call)
        for w in (self.snr, self.rt60, self.clean.edit, self.noise.edit,
                  self.out.edit):
            w.textChanged.connect(self._update_call)
        self.mode.currentIndexChanged.connect(self._update_call)
        self.subtype.currentIndexChanged.connect(self._update_call)
        self.channel.currentIndexChanged.connect(self._update_call)
        self.targets.toggled.connect(self._update_call)
        self.seed.valueChanged.connect(self._update_call)
        self._update_call()

    # ------------------------------------------------------------------- ui

    def _seed(self) -> Optional[int]:
        return None if self.seed.value() < 0 else self.seed.value()

    def _link(self) -> Optional[str]:
        """The chosen preset, or None. `none` is a name in the box and not a
        name in the call, so what is shown stays copy-pasteable."""
        name = self.channel.currentText()
        return None if name == "none" else name

    def _update_call(self) -> None:
        try:
            snr = parse_snr(self.snr.text())
        except ValueError:
            snr = "…"
        try:
            rooms = parse_rt60(self.rt60.text())
        except ValueError:
            rooms = "…"
        room_args = ("" if rooms is None
                     else f", rt60={rooms}, drr_db={self.drr.value()}")
        link = self._link()
        if link is not None:
            room_args += f", channel={link!r}"
        if self.targets.isChecked():
            room_args += ", write_targets=True"
        if self.subtype.currentText() != "PCM_16":
            room_args += f", subtype={self.subtype.currentText()!r}"
        self.call.setText(
            f"kudio.Synthesizer({self.clean.path()!r}, {self.noise.path()!r}, "
            f"out_path={self.out.path()!r}, snr_ratio={snr}{room_args})"
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
        try:
            rooms = parse_rt60(self.rt60.text())
        except ValueError as e:
            self.report(f"Bad rt60 list — {e}")
            return
        for label, picker in (("clean", self.clean), ("noise", self.noise)):
            if not picker.path() or not Path(picker.path()).is_dir():
                self.report(f"Select a valid {label} folder.")
                return
        if not self.out.path():
            self.report("Select an output folder.")
            return

        if self.targets.isChecked() and rooms is None:
            self.report("Reverberant targets need rt60 — with no room the "
                        "clean file already is that signal.")
            return

        self.report("Synthesizing …")
        started = self.jobs.run(self._synthesize, snr, rooms,
                                on_done=self._finished, on_failed=self._failed)
        if not started:
            self.report("A synthesis job is already running.")

    def _synthesize(self, snr: Sequence[Union[int, float]],
                    rooms: Optional[List[float]]):
        syx = kudio.Synthesizer(self.clean.path(), self.noise.path(),
                                out_path=self.out.path(), snr_ratio=snr,
                                rt60=rooms, drr_db=self.drr.value(),
                                channel=self._link(),
                                write_targets=self.targets.isChecked(),
                                subtype=self.subtype.currentText())
        # kudio warns about clipping and about names that overwrite each other.
        # Nothing in this app routes warnings anywhere, so without this they
        # reach stderr and a GUI user never learns their corpus was clipped.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            produced = syx.syn(mode=self.mode.currentText(),
                               is_add_clean=self.add_clean.isChecked(),
                               overwrite=self.overwrite.isChecked(),
                               seed=self._seed())
            notices = [str(w.message) for w in caught
                       if issubclass(w.category, UserWarning)]
        # the manifest carries the room, the link and the target, none of
        # which the four-tuples can; written beside the mixture so a training
        # run can read what it was given
        if produced:
            kudio.save_manifest(Path(self.out.path()) / "manifest.json",
                                syx.manifest())

        # What `syn` returns is what was *asked for*. Two outputs that share a
        # name leave one file, so counting the request would report a corpus
        # that is not there -- and the manifest written above would agree with
        # the wrong number. Count the distinct paths that exist instead, on
        # this thread, where walking the disk costs the UI nothing.
        wanted = len(produced or [])
        paths = {Path(out) for out, *_ in (produced or [])}
        return SynthResult(produced=produced, link=syx.channel_tag,
                           targets=len(syx.targets), wanted=wanted,
                           on_disk=sum(1 for p in paths if p.is_file()),
                           clipped=dict(syx.clipped), notices=notices,
                           subtype=self.subtype.currentText())

    def _finished(self, result: SynthResult) -> None:
        note = "" if result.on_disk == 0 else "  ·  manifest.json beside them"
        if result.link:
            note += f"  ·  down a {result.link} link"
        if result.targets:
            note += f"  ·  {result.targets} reverberant target(s) in TARGETS/"
        if result.clipped:
            worst = max(result.clipped.values())
            note += (f"  ·  {len(result.clipped)} clipped "
                     f"(worst peak {worst:.2f})")
        self.result.setText(
            f"{result.on_disk} file(s) written to {self.out.path()}{note}")

        if result.clipped:
            worst = max(result.clipped.values())
            over_db = 20 * math.log10(worst) if worst > 0 else 0.0
            self.report(
                f"{len(result.clipped)} of {result.on_disk} file(s) went past "
                f"full scale and were clipped as {result.subtype} (worst peak "
                f"{worst:.2f}, +{over_db:.1f} dBFS). Lower the clean level, "
                f"raise the SNR, or write subtype='FLOAT' to keep them.")
            return

        wanted, on_disk = result.wanted, result.on_disk
        if wanted > on_disk:
            # the only way to ask for N and get fewer is a name collision, and
            # it is worth saying out loud rather than quietly reporting N
            short = wanted - on_disk
            self.report(
                f"Synthesis complete — {on_disk} file(s), but {wanted} were "
                f"asked for: {short} output(s) shared a name and overwrote "
                f"each other. Give the clashing files distinct names, or keep "
                f"mkdir_parents on so the folder layout separates them.")
        else:
            self.report(f"Synthesis complete — {on_disk} file(s).")

    def _failed(self, message: str) -> None:
        self.result.setText("—")
        self.report(f"Synthesis failed — {message}")
