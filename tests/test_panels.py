# -*- coding: utf-8 -*-
"""Panels build, describe the call they will make, and actually make it."""
from __future__ import annotations

import pathlib

import numpy as np
import pytest


def test_effects_apply_gain_to_selection(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("gain")
    panel.gain_db.setValue(-6.0)
    assert "kudio.gain(y, db=-6.0)" in panel.call.text()

    session.set_selection(0.0, 0.5)
    before = session.y.copy()
    panel._apply()

    expected = before[:8000] * 10 ** (-6.0 / 20.0)
    assert np.allclose(session.y[:8000], expected, atol=1e-6)
    assert np.allclose(session.y[8000:], before[8000:])
    assert session.can_undo()


def test_effects_trim_silence_shortens_clip(session):
    from kudiostudio.panels.effects import EffectsPanel

    padded = np.concatenate([np.zeros(4000, dtype=np.float32), session.y])
    session.set_audio(padded, session.sr)

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("trim_silence")
    panel._apply()
    assert len(session.y) < len(padded)


def test_effects_split_reports_without_editing(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("split_on_silence")
    before = session.y.copy()
    panel._apply()

    assert np.allclose(session.y, before)
    assert "segment" in panel.split_result.text()


def test_effects_reports_failure_instead_of_raising(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("add_noise_snr")   # no noise file picked
    messages = []
    panel.status.connect(messages.append)
    panel._apply()

    assert messages and "failed" in messages[0]


def test_analysis_view_renders_every_mode(qapp, session):
    """Each mode names the kudio call it makes, and actually makes it."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)

    expected = {AnalysisView.MODE_STFT: "kudio.STFT",
                AnalysisView.MODE_MEL: "kudio.melspectrogram",
                AnalysisView.MODE_MFCC: "kudio.mfcc"}
    assert len(expected) == len(AnalysisView.MODES)

    for mode, call in expected.items():
        view.mode.setCurrentIndex(mode)
        assert call in view.call_hint.text()
        data, y_max, _, _ = view._analyse()
        assert data.ndim == 2 and data.shape[0] > 1     # (frames, bins)
        assert y_max > 0


def test_analysis_view_greys_out_the_parameters_a_mode_ignores(qapp, session):
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.mode.setCurrentIndex(AnalysisView.MODE_MEL)
    assert view.n_mels.isEnabled() is True
    assert view.n_mfcc.isEnabled() is False

    view.mode.setCurrentIndex(AnalysisView.MODE_MFCC)
    assert view.n_mels.isEnabled() is False
    assert view.n_mfcc.isEnabled() is True
    assert view.n_fft.isEnabled() is False


def test_selection_boxes_and_the_region_stay_in_step(qapp, session):
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)

    # session -> boxes
    view.set_selection(0.2, 0.6)
    assert view.sel_start.value() == pytest.approx(0.2)
    assert view.sel_end.value() == pytest.approx(0.6)
    assert "0.400 s" in view.sel_info.text()
    assert view.zoom_button.isEnabled() is True

    # boxes -> session
    emitted = []
    view.selection_changed.connect(lambda a, b: emitted.append((a, b)))
    view.sel_end.setValue(0.8)
    assert emitted[-1] == pytest.approx((0.2, 0.8))

    view.set_selection(0.0, 0.0)
    assert view.sel_info.text() == "no selection"
    assert view.zoom_button.isEnabled() is False


def test_a_half_typed_range_is_ignored_rather_than_swapped(qapp, session):
    """Dragging `start` past `end` must not flip the selection under the user."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    view.set_selection(0.2, 0.4)

    emitted = []
    view.selection_changed.connect(lambda a, b: emitted.append((a, b)))
    view.sel_start.setValue(0.9)            # now start > end
    assert emitted == []


def test_zoom_to_selection_narrows_both_plots(qapp, session):
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    view.set_selection(0.4, 0.5)
    view.zoom_to_selection()

    low, high = view.waveform.viewRange()[0]
    assert low > 0.3 and high < 0.6
    assert view.spectrogram.viewRange()[0][1] < 0.6

    view.zoom_full()
    assert view.waveform.viewRange()[0] == pytest.approx([0.0, 1.0])


def test_a_same_length_edit_keeps_the_zoom(qapp, session):
    """The A/B loop is worthless if comparing throws you back to the full clip."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    view.set_selection(0.4, 0.5)
    view.zoom_to_selection()
    zoomed = view.waveform.viewRange()[0]

    view.set_audio(session.y * 0.5, session.sr)
    assert view.waveform.viewRange()[0] == pytest.approx(zoomed)

    view.set_audio(session.y[:4000], session.sr)     # length changed -> refit
    assert view.waveform.viewRange()[0] == pytest.approx([0.0, 0.25])


def test_frame_length_wobble_still_counts_as_the_same_clip(qapp, session):
    """Frame-based processing does not return exactly what it was given.

    `kudio.trad_enhance` comes back 128 samples short of its input. Treating
    that as a new clip reset the zoom, the amplitude axis and the colour scale
    after every denoise — exactly when you most want them held still.
    """
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    view.set_selection(0.4, 0.5)
    view.zoom_to_selection()
    zoomed = view.waveform.viewRange()[0]
    levels = view.spectrogram.levels

    view.set_audio(session.y[:-128] * 0.3, session.sr)      # 8 ms shorter
    assert view.waveform.viewRange()[0] == pytest.approx(zoomed)
    assert view.spectrogram.levels == pytest.approx(levels)

    # a real trim is still a new clip
    view.set_audio(session.y[:8000], session.sr)
    assert view.waveform.viewRange()[0] == pytest.approx([0.0, 0.5])


def test_a_same_length_edit_shares_the_amplitude_axis(qapp, session):
    """A quieter version must look quieter, not be rescaled to fill the plot."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    loud = view.waveform.viewRange()[1]

    view.set_audio(session.y * 0.1, session.sr)
    assert view.waveform.viewRange()[1] == pytest.approx(loud)

    # ...but a louder one must still fit on screen
    view.set_audio(session.y * 4.0, session.sr)
    assert view.waveform.viewRange()[1][1] > loud[1]

    # a different clip altogether gets its own scale again
    view.set_audio(session.y[:4000] * 0.1, session.sr)
    assert view.waveform.viewRange()[1][1] < loud[1]


def test_both_plots_show_the_selection_and_either_can_drag_it(qapp, session):
    """A zoomed spectrogram that does not show what is selected is useless."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    view.set_selection(0.2, 0.6)

    for plot in (view.waveform, view.spectrogram):
        assert plot._region.isVisible()
        assert plot._region.getRegion() == pytest.approx((0.2, 0.6))

    emitted = []
    view.selection_changed.connect(lambda a, b: emitted.append((a, b)))
    view.spectrogram._region.setRegion((0.1, 0.3))
    view.spectrogram._emit_region()
    assert emitted[-1] == pytest.approx((0.1, 0.3))


def test_setting_the_selection_does_not_bounce_between_the_plots(qapp, session):
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)

    emitted = []
    view.selection_changed.connect(lambda a, b: emitted.append((a, b)))
    view.set_selection(0.2, 0.6)
    assert emitted == [], "programmatic updates must stay silent"


def test_the_spectrogram_keeps_its_colour_scale_across_a_same_length_edit(qapp, session):
    """Rescaling colour per image would make a denoiser look like a no-op."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    levels = view.spectrogram.levels
    assert levels is not None

    view.set_audio(session.y * 0.01, session.sr)        # 40 dB quieter
    assert view.spectrogram.levels == pytest.approx(levels)

    view.set_audio(session.y[:4000], session.sr)        # a different clip
    assert view.spectrogram.levels != pytest.approx(levels)


def test_changing_an_analysis_parameter_rescales_colour(qapp, session):
    """Different n_fft is different data, not the same clip seen again."""
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    levels = view.spectrogram.levels

    view.n_fft.setValue(2048)
    assert view.spectrogram.levels != pytest.approx(levels)


def test_clicking_the_plot_area_reports_the_time_under_the_pointer(qapp, session):
    from PySide6.QtCore import QPointF, Qt
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.resize(800, 600)
    view.set_audio(session.y, session.sr)
    qapp.processEvents()

    wave = view.waveform
    scene_pos = wave.plotItem.vb.mapViewToScene(QPointF(0.3, 0.0))

    class _Click:
        def __init__(self, button, accepted=False):
            self._button, self._accepted = button, accepted

        def button(self):
            return self._button

        def isAccepted(self):
            return self._accepted

        def scenePos(self):
            return scene_pos

    seen = []
    wave.seek_requested.connect(seen.append)

    wave.scene().sigMouseClicked.emit(_Click(Qt.RightButton))       # context menu
    wave.scene().sigMouseClicked.emit(_Click(Qt.LeftButton, True))  # region took it
    assert seen == []

    wave.scene().sigMouseClicked.emit(_Click(Qt.LeftButton))
    assert seen == pytest.approx([0.3], abs=1e-3)


def test_metrics_scores_against_the_original(session):
    from kudiostudio.panels.metrics import MetricsPanel

    panel = MetricsPanel(session)
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    panel.jobs.run = lambda *a, **k: True        # skip the optional-metric thread
    panel._run()

    labels = [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())]
    # the alignment is reported first, because a bad score from a misalignment
    # and a bad score from bad processing look identical and mean the opposite
    assert labels[0] == "Alignment"
    assert labels[1:4] == ["SNR", "SI-SDR", "Segmental SNR"]


@pytest.mark.parametrize("text, expected", [
    ("-5, 0, 5", [-5, 0, 5]),
    ("0", [0]),
    ("3;6;9", [3, 6, 9]),
])
def test_parse_snr(text, expected):
    from kudiostudio.panels.synth import parse_snr
    assert parse_snr(text) == expected


def test_parse_snr_rejects_empty():
    from kudiostudio.panels.synth import parse_snr
    with pytest.raises(ValueError):
        parse_snr("  ")


def test_batch_converts_a_folder(qapp, tmp_path, tone):
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession
    import kudio

    src = tmp_path / "in"
    src.mkdir()
    for i in range(3):
        y, sr = kudio.file_load(tone)
        kudio.save_wave(src / f"clip{i}.wav", y, sr)
    out = tmp_path / "out"

    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(out))
    panel.rate.setCurrentText("8000")

    result = panel._convert()
    assert (result.written, result.total, result.failed) == (3, 3, [])
    y, sr = kudio.file_load(next(out.glob("*.wav")))
    assert sr == 8000


def test_batch_can_normalize_loudness(qapp, tmp_path, tone):
    """Peak normalisation says nothing about how loud a file sounds."""
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession
    import kudio

    src = tmp_path / "in"
    src.mkdir()
    y, sr = kudio.file_load(tone)
    for i, gain in enumerate((0.0, -12.0)):          # two very different levels
        kudio.save_wave(src / f"clip{i}.wav", kudio.gain(y, gain), sr)
    out = tmp_path / "out"

    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(out))
    panel.normalize_mode.setCurrentText("LUFS")
    assert panel.normalize_target.value() == -23.0    # EBU R 128 default
    assert "lufs=-23.0" in panel.call.text()

    result = panel._convert()
    assert result.written == 2
    for path in out.glob("*.wav"):
        clip, rate = kudio.file_load(path)
        assert kudio.loudness(clip, rate) == pytest.approx(-23.0, abs=0.5)


def test_batch_mirrors_subfolders(qapp, tmp_path, tone):
    """Two files with the same name in different folders must not collide."""
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession
    import kudio

    src = tmp_path / "in"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir(parents=True)
    y, sr = kudio.file_load(tone)
    kudio.save_wave(src / "a" / "clip.wav", y, sr)
    kudio.save_wave(src / "b" / "clip.wav", y, sr)
    out = tmp_path / "out"

    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(out))
    result = panel._convert()

    assert result.written == 2
    written = {p.relative_to(out).as_posix() for p in result.outputs}
    assert written == {"a/clip.wav", "b/clip.wav"}


def test_the_level_readout_measures_off_thread_and_reports(qapp, session):
    from PySide6.QtCore import QEventLoop, QTimer
    from kudiostudio.widgets import LevelReadout

    readout = LevelReadout()
    readout.set_audio(session.y, session.sr)

    seen = []
    readout.reported.connect(seen.append)
    loop = QEventLoop()
    QTimer.singleShot(1500, loop.quit)
    readout.reported.connect(lambda _: loop.quit())
    loop.exec()
    readout.jobs.wait(2000)

    assert seen, "the report never arrived"
    assert readout.report is not None
    assert "dBFS" in readout.peak.text()
    assert "LUFS" in readout.loudness.text()
    assert not readout.problems.isHidden()


def test_the_level_readout_clears_when_the_audio_goes_away(qapp, session):
    from kudiostudio.widgets import LevelReadout

    readout = LevelReadout()
    readout.set_audio(None, 0)
    assert readout.report is None
    assert readout.peak.text() == "—"


def test_a_clipped_take_is_reported_rather_than_accepted_quietly(qapp, session):
    import numpy as np
    from kudiostudio.panels.record import RecordPanel

    panel = RecordPanel(session)
    messages = []
    panel.status.connect(messages.append)

    clipped = np.clip(session.y * 8.0, -1.0, 1.0).astype(np.float32)
    panel._finished(clipped, session.sr)

    assert "clipped" in panel.take.text()
    assert any("clipped" in m for m in messages)


def test_a_clean_take_says_so(qapp, real_session):
    from kudiostudio.panels.record import RecordPanel

    panel = RecordPanel(real_session)
    panel._finished(real_session.y, real_session.sr)
    assert "clean" in panel.take.text()
    assert "dBFS" in panel.take.text()


def test_metrics_can_inspect_without_any_reference(qapp, real_session):
    from kudiostudio.panels.metrics import MetricsPanel

    panel = MetricsPanel(real_session)
    panel.inspect_button.click()

    rows = {panel.table.item(r, 0).text(): panel.table.item(r, 1).text()
            for r in range(panel.table.rowCount())}
    assert "Peak" in rows and "dBFS" in rows["Peak"]
    assert "Loudness (BS.1770)" in rows
    assert "Bandwidth" in rows
    assert not panel.inspect_result.isHidden()
    assert "Nothing wrong" in panel.inspect_result.text()


def test_inspecting_an_upsampled_clip_names_the_problem(qapp, real_session):
    """The finding that quietly wastes half a dataset, surfaced in the panel."""
    import kudio
    upsampled = kudio.resample(
        kudio.resample(real_session.y, real_session.sr, 8000), 8000,
        real_session.sr)
    real_session.set_audio(upsampled, real_session.sr)

    from kudiostudio.panels.metrics import MetricsPanel
    panel = MetricsPanel(real_session)
    panel.inspect_button.click()
    assert "upsampled from 8000 Hz" in panel.inspect_result.text()


# ------------------------------------------------------ new effects (kudio 3.4)

@pytest.mark.parametrize("name,attrs", [
    ("fade_in", {}),
    ("fade_out", {}),
    ("fade", {}),
    ("reverse", {}),
    ("remove_dc", {}),
    ("highpass", {}),
    ("lowpass", {}),
    ("bandpass", {}),
    ("bandstop", {}),
    ("vad_trim", {}),
])
def test_every_new_effect_renders(qapp, session, name, attrs):
    """Each entry must line up with its own parameter page and actually run."""
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText(name)
    assert panel.params.currentIndex() == panel.EFFECTS.index(name)
    call_text, out = panel._render()
    assert call_text.startswith(f"kudio.{name}(")
    assert np.asarray(out).size > 0


def test_reverse_actually_reverses(qapp, session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("reverse")
    _, out = panel._render()
    assert np.array_equal(out, np.asarray(session.y)[::-1])


def test_fade_reaches_silence_at_the_edges(qapp, session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("fade")
    panel.fade_both_in.setValue(0.05)
    panel.fade_both_out.setValue(0.05)
    _, out = panel._render()
    assert out[0] == pytest.approx(0.0, abs=1e-6)
    assert out[-1] == pytest.approx(0.0, abs=1e-6)


def test_filter_cutoffs_are_capped_at_nyquist(qapp, session):
    """A cutoff above Nyquist is a FeatureError, not a filter."""
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.on_session_changed()
    for box in panel._cutoff_boxes():
        assert box.maximum() <= session.sr / 2

    session.set_audio(np.zeros(8000, dtype=np.float32), 8000, name="8k")
    panel.on_session_changed()
    assert panel.lp_cutoff.maximum() <= 4000


def test_the_caption_follows_a_combo_box_not_just_a_spin_box(qapp, session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("fade_in")
    panel.fade_in_shape.setCurrentText("cosine")
    assert "shape='cosine'" in panel.call.text()


def test_split_segments_can_be_written_out(qapp, session, tmp_path, monkeypatch):
    """They used to be counted and thrown away."""
    from PySide6.QtWidgets import QFileDialog
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("split_on_silence")
    assert panel.split_save_button.isEnabled() is False
    panel._apply()
    assert panel._segments
    assert panel.split_save_button.isEnabled() is True

    out = tmp_path / "segments"          # not tmp_path itself: the fixture
    out.mkdir()                          # already wrote tone.wav in there
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(out)))
    panel._save_segments()
    assert len(list(out.glob("*.wav"))) == len(panel._segments)


def test_new_audio_forgets_the_old_segments(qapp, session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("split_on_silence")
    panel._apply()
    assert panel._segments
    session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="new")
    panel.on_session_changed()
    assert panel._segments == []
    assert panel.split_save_button.isEnabled() is False


def test_metrics_dnsmos_reports_the_missing_model(qapp, session):
    """kudio does not ship the weights; the panel has to say where to get them."""
    from kudiostudio.panels.metrics import MetricsPanel

    panel = MetricsPanel(session)
    panel._dnsmos_failed("DNSMOS model not found. kudio does not ship it — "
                         "clone https://github.com/microsoft/DNS-Challenge")
    assert panel.dnsmos_result.isVisible() or panel.dnsmos_result.text()
    assert "DNS-Challenge" in panel.dnsmos_result.text()


def test_metrics_dnsmos_fills_the_table(qapp, session):
    import kudio
    from kudiostudio.panels.metrics import MetricsPanel

    panel = MetricsPanel(session)
    panel._dnsmos_done(kudio.DnsmosScore(sig=3.1, bak=4.2, ovrl=3.4, segments=2))
    values = [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())]
    assert values[:3] == ["SIG (speech)", "BAK (background)", "OVRL (overall)"]
    assert "intrusive" not in panel.dnsmos_result.text()


# ------------------------------------------------ batch denoising (kudio 3.5)

def test_batch_denoise_is_off_by_default(qapp, tmp_path, tone):
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession

    panel = BatchPanel(AudioSession())
    assert panel.denoise.currentData() is None
    assert panel.denoise_noise.isEnabled() is False
    assert "enhance_folder" not in panel.call.text()


def test_choosing_a_denoiser_shows_the_second_pass(qapp):
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession

    panel = BatchPanel(AudioSession())
    panel.denoise.setCurrentIndex(panel.denoise.findData("logmmse"))
    assert panel.denoise_noise.isEnabled() is True
    text = panel.call.text()
    assert "kudio.convert_folder(" in text
    assert "kudio.enhance_folder(" in text and "'logmmse'" in text


def test_batch_denoises_a_folder(qapp, tmp_path):
    """A second pass in place over what the conversion just wrote."""
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession
    import kudio

    sr = 16000
    rng = np.random.default_rng(0)
    t = np.arange(int(3 * sr)) / sr
    speech = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 14))
    speech *= 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)
    clean = np.zeros(len(t), dtype=np.float32)
    clean[sr // 2:2 * sr] = (speech[:int(1.5 * sr)] / 7).astype(np.float32)
    noisy = (clean + 0.05 * rng.standard_normal(len(clean))).astype(np.float32)

    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    kudio.save_wave(src / "a.wav", noisy, sr)

    panel = BatchPanel(AudioSession())
    panel.src.edit.setText(str(src))
    panel.dst.edit.setText(str(out))
    panel.denoise.setCurrentIndex(panel.denoise.findData("logmmse"))
    result = panel._convert()

    assert result.written == 1
    assert result.mean_reduction_db() > 5.0
    before = kudio.audio_report(*kudio.file_load(src / "a.wav")[::1])
    after = kudio.audio_report(*kudio.file_load(next(out.glob("*.wav")))[::1])
    assert after.noise_floor_dbfs < before.noise_floor_dbfs - 5.0


def test_the_denoise_summary_refuses_to_call_it_better(qapp, tmp_path):
    from kudiostudio.panels.batch import BatchPanel
    from kudiostudio.session import AudioSession
    import kudio

    panel = BatchPanel(AudioSession())
    panel.dst.edit.setText(str(tmp_path))
    panel._finished(kudio.EnhanceFolderResult(
        written=2, total=2, outputs=[], failed=[],
        floors=((tmp_path / "a.wav", -30.0, -45.0),
                (tmp_path / "b.wav", -32.0, -46.0))))
    text = panel.result.text()
    assert "14.5 dB on average" in text
    assert "not of whether it sounds better" in text


def test_the_room_effect_uses_a_real_impulse_response(session):
    """`reverb` is a comb filter; `room` convolves with an impulse response
    that can be measured afterwards."""
    import kudio
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("room")
    panel.room_rt60.setValue(0.7)
    panel.room_drr.setValue(6.0)
    panel.room_seed.setValue(3)

    call, operation = panel._operation()
    assert "kudio.apply_rir" in call and "rt60=0.7" in call and "seed=3" in call

    wet = operation(session.y, session.sr)
    assert len(wet) == len(session.y), "the length is kept"
    assert not np.allclose(wet, session.y, atol=1e-3)

    # the room the panel asked for is the room that came out
    measured = kudio.rt60(kudio.rir(session.sr, rt60=0.7, drr_db=6.0, seed=3),
                          session.sr)
    assert measured.t30 == pytest.approx(0.7, rel=0.06)
    assert measured.drr == pytest.approx(6.0, abs=0.2)


def test_the_room_effect_is_reproducible_from_its_seed(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("room")
    panel.room_seed.setValue(7)
    first = panel._operation()[1](session.y, session.sr)
    second = panel._operation()[1](session.y, session.sr)
    assert np.array_equal(first, second)

    panel.room_seed.setValue(8)
    assert not np.array_equal(panel._operation()[1](session.y, session.sr), first)


def test_every_effect_has_a_parameter_page(session):
    """The two lists are indexed together, so a new effect inserted in one and
    not the other silently shows the wrong controls."""
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    assert panel.params.count() == len(EffectsPanel.EFFECTS)
    for index, name in enumerate(EffectsPanel.EFFECTS):
        panel.effect.setCurrentIndex(index)
        assert panel.effect.currentText() == name
        assert panel.params.currentIndex() == index


# ------------------------------------------------- synthesis with rooms

@pytest.mark.parametrize("text, expected", [
    ("", None),
    ("0.3", [0.3]),
    ("0.3, 0.6, 0.9", [0.3, 0.6, 0.9]),
    ("0.4;0.8", [0.4, 0.8]),
])
def test_parse_rt60(text, expected):
    """Empty means something here — no rooms at all, which is the default and
    has to stay reachable."""
    from kudiostudio.panels.synth import parse_rt60
    assert parse_rt60(text) == expected


@pytest.mark.parametrize("text", ["0", "-0.5", "0.3, 0"])
def test_parse_rt60_refuses_an_impossible_room(text):
    from kudiostudio.panels.synth import parse_rt60
    with pytest.raises(ValueError, match="positive"):
        parse_rt60(text)


def test_the_synth_panel_names_the_rooms_it_will_build(qapp, session):
    from kudiostudio.panels.synth import SynthPanel

    panel = SynthPanel(session)
    assert "rt60" not in panel.call.text(), "no rooms by default"

    panel.rt60.setText("0.3, 0.8")
    panel.drr.setValue(3.0)
    assert "rt60=[0.3, 0.8]" in panel.call.text()
    assert "drr_db=3.0" in panel.call.text()


def test_the_synth_panel_refuses_a_bad_room_list(qapp, session):
    from kudiostudio.panels.synth import SynthPanel

    panel = SynthPanel(session)
    panel.clean.edit.setText(".")
    panel.noise.edit.setText(".")
    panel.out.edit.setText(".")
    panel.rt60.setText("0.3, -1")

    messages = []
    panel.status.connect(messages.append)
    panel._run()
    assert messages and "Bad rt60" in messages[-1]


def test_the_synth_panel_writes_a_manifest_with_the_rooms(qapp, session,
                                                          tmp_path):
    """The four-tuples cannot carry the room; the manifest can, and a training
    run needs to know what it was given."""
    import kudio
    from kudiostudio.panels.synth import SynthPanel

    clean, noise = tmp_path / "clean", tmp_path / "noise"
    clean.mkdir()
    noise.mkdir()
    sr = 16000
    t = np.arange(sr) / sr
    kudio.save_wave(clean / "a.wav",
                    (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), sr)
    kudio.save_wave(noise / "n.wav",
                    (0.05 * np.random.default_rng(0)
                     .standard_normal(sr)).astype(np.float32), sr)

    panel = SynthPanel(session)
    panel.clean.edit.setText(str(clean))
    panel.noise.edit.setText(str(noise))
    panel.out.edit.setText(str(tmp_path / "mixed"))
    panel.rt60.setText("0.5")

    produced = panel._synthesize([0], [0.5])
    assert produced

    manifest = kudio.load_manifest(tmp_path / "mixed" / "manifest.json")
    assert manifest and all(p.rt60 == 0.5 for p in manifest)
    assert all("rt500ms" in p.noisy for p in manifest)


# ------------------------------------------------------------- the channel

@pytest.mark.parametrize("name, fragment", [
    ("telephone", "kudio.telephone(y, sr, codec='mu_law')"),
    ("bit_depth", "kudio.bit_depth(y, bits=8"),
    ("dropouts", "kudio.dropouts(y, sr, loss=0.05"),
])
def test_the_channel_effects_name_the_call_they_make(session, name, fragment):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText(name)
    call, operation = panel._operation()
    assert fragment in call

    out = operation(session.y, session.sr)
    assert len(out) == len(session.y)
    assert np.all(np.isfinite(out))


def test_the_phone_line_narrows_the_band(session):
    import kudio
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("telephone")
    out = panel._operation()[1](session.y, session.sr)

    assert kudio.audio_report(out, session.sr).band_limited
    assert kudio.audio_report(out, session.sr).bandwidth_hz < 4200


def test_packet_loss_is_reproducible_from_the_panel(session):
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("dropouts")
    panel.loss_pct.setValue(10.0)
    panel.loss_seed.setValue(4)

    first = panel._operation()[1](session.y, session.sr)
    second = panel._operation()[1](session.y, session.sr)
    assert np.array_equal(first, second)
    assert np.count_nonzero(first == 0.0) > 0

    panel.loss_conceal.setCurrentText("hold")
    held = panel._operation()[1](session.y, session.sr)
    assert np.count_nonzero(held == 0.0) < np.count_nonzero(first == 0.0)


def test_the_burst_control_is_independent_at_zero_rather_than_a_run_of_none(
        session):
    """`burst=0` is not a run length kudio accepts, and `independent` is not
    `burst=1`. The spin box's special value has to become None, not 0."""
    from kudiostudio.panels.effects import EffectsPanel

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("dropouts")
    assert panel.loss_burst.value() == 0
    assert "burst=None" in panel._operation()[0]

    panel.loss_burst.setValue(6)
    assert "burst=6" in panel._operation()[0]
    out = panel._operation()[1](session.y, session.sr)
    assert len(out) == len(session.y) and np.all(np.isfinite(out))


def test_a_burst_from_the_panel_leaves_one_long_hole(session):
    """The same number of packets, arranged so the longest gap is far
    longer — which is what makes burst loss hard to conceal."""
    from kudiostudio.panels.effects import EffectsPanel

    def longest_hole(y):
        longest = run = 0
        for value in y == 0.0:
            run = run + 1 if value else 0
            longest = max(longest, run)
        return longest

    # 20 seconds, not the fixture's one: 50 packets lose five at 10%, which
    # is one or two runs and far too few to say anything about their length
    long = np.tile(session.y, 20)

    panel = EffectsPanel(session)
    panel.effect.setCurrentText("dropouts")
    panel.loss_pct.setValue(10.0)
    panel.loss_seed.setValue(2)
    scattered = panel._operation()[1](long, session.sr)
    panel.loss_burst.setValue(8)
    bursty = panel._operation()[1](long, session.sr)

    # the same number of packets either way, to within the short last one
    packet = int(0.02 * session.sr)
    assert abs(np.count_nonzero(scattered == 0.0)
               - np.count_nonzero(bursty == 0.0)) < packet
    assert longest_hole(bursty) > 2 * longest_hole(scattered)


# ------------------------------------------- the link, and the wet target

def test_the_synth_panel_leaves_the_link_out_of_the_call_when_there_is_none(
        qapp, session):
    """`none` is a name in the box and not a name in the call, so what is
    shown stays copy-pasteable."""
    from kudiostudio.panels.synth import SynthPanel

    panel = SynthPanel(session)
    assert "channel" not in panel.call.text()
    assert "write_targets" not in panel.call.text()

    panel.channel.setCurrentText("telephone")
    assert "channel='telephone'" in panel.call.text()
    panel.targets.setChecked(True)
    assert "write_targets=True" in panel.call.text()


def test_the_synth_panel_refuses_targets_without_a_room(qapp, session,
                                                        tmp_path):
    """With no room the clean file already is the reverberant-clean signal,
    and a copy of it would be a second name for one file."""
    from kudiostudio.panels.synth import SynthPanel

    panel = SynthPanel(session)
    panel.clean.edit.setText(str(tmp_path))
    panel.noise.edit.setText(str(tmp_path))
    panel.out.edit.setText(str(tmp_path / "out"))
    panel.targets.setChecked(True)

    messages = []
    panel.status.connect(messages.append)
    panel._run()
    assert messages and "need rt60" in messages[-1]


def test_the_synth_panel_records_the_link_and_the_target(qapp, session,
                                                         tmp_path):
    """Everything the four-tuples cannot carry, in one manifest: the room,
    the link the mixture travelled down, and where the wet target went."""
    import kudio
    from kudiostudio.panels.synth import SynthPanel

    clean, noise = tmp_path / "clean", tmp_path / "noise"
    clean.mkdir()
    noise.mkdir()
    sr = 16000
    t = np.arange(sr) / sr
    kudio.save_wave(clean / "a.wav",
                    (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), sr)
    kudio.save_wave(noise / "n.wav",
                    (0.05 * np.random.default_rng(0)
                     .standard_normal(sr)).astype(np.float32), sr)

    panel = SynthPanel(session)
    panel.clean.edit.setText(str(clean))
    panel.noise.edit.setText(str(noise))
    panel.out.edit.setText(str(tmp_path / "mixed"))
    panel.rt60.setText("0.5")
    panel.channel.setCurrentText("telephone")
    panel.targets.setChecked(True)

    produced, link, targets = panel._synthesize([0], [0.5])
    assert produced and link == "telephone" and targets == len(produced)

    manifest = kudio.load_manifest(tmp_path / "mixed" / "manifest.json")
    assert manifest
    for pair in manifest:
        assert pair.rt60 == 0.5
        assert pair.channel == "telephone"
        assert pathlib.Path(pair.target).is_file()
        # the link is constant for the run, so it is not in the names
        assert "telephone" not in pathlib.Path(pair.noisy).name
        y, _ = kudio.file_load(pair.noisy, sr=None, mono=True)
        assert kudio.audio_report(y, sr).band_limited
