# -*- coding: utf-8 -*-
"""The Compare panel: rank every enhancement method over one clip."""
import gc
import sys
import time
import types
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt

import kudio
from kudiostudio.panels.compare import (
    COLUMNS,
    REF_FILE,
    REF_NONE,
    REF_ORIGINAL,
    REFERENCE_COLUMNS,
    SWEEP_GRID,
    SWEEP_NOISES,
    ComparePanel,
)


def column(name: str) -> int:
    """Column index by name. Adding a column used to break five tests."""
    return COLUMNS.index(name)


def key(panel, row: int):
    """The (method, estimator) pair a row stands for."""
    return panel.table.item(row, 0).data(Qt.UserRole + 1)


def select_rows(panel, *rows: int) -> None:
    """Select several rows, in the order given."""
    from PySide6.QtCore import QItemSelectionModel

    model = panel.table.selectionModel()
    model.clearSelection()
    for row in rows:
        model.select(panel.table.model().index(row, 0),
                     QItemSelectionModel.Select | QItemSelectionModel.Rows)


def _run_and_wait(panel):
    """The comparison runs on a worker; drive the loop until it lands."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    panel._run()
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)


@pytest.fixture
def panel(qapp, real_session):
    p = ComparePanel(real_session)
    p.metrics.setChecked(False)          # PESQ/STOI back-ends are not installed
    yield p
    p.jobs.wait(5000)


def test_a_comparison_fills_a_row_per_method(panel):
    _run_and_wait(panel)
    assert panel.table.rowCount() == len(kudio.METHODS)
    assert len(panel._results) == len(kudio.METHODS)
    assert all(r.error is None for r in panel._results)


def test_without_a_reference_the_quality_columns_are_hidden(panel):
    assert panel.reference.currentText() == REF_NONE
    _run_and_wait(panel)
    for column in REFERENCE_COLUMNS:
        assert panel.table.isColumnHidden(column) is True
    assert all(r.si_sdr_db is None for r in panel._results)


def test_without_a_reference_the_verdict_refuses_to_call_a_winner(panel):
    """A low floor is what you also get by deleting the speech."""
    _run_and_wait(panel)
    text = panel.verdict.text()
    assert "no quality score" in text
    assert "Audition" in text


def _make_it_a_real_experiment(session, amplitude=0.15):
    """Clean signal as the baseline, noisy version as the working audio.

    This is the only arrangement in which "the original" is a *clean*
    reference. Denoising first and then comparing against the original does
    the opposite — see the not-clean test below.

    The noise has to be *heavy* (this lands near 0 dB SNR). Statistical
    enhancement is for low-SNR audio: above roughly 10 dB the speech distortion
    it introduces costs more than the noise it removes, and every method
    measures worse than doing nothing. That is the algorithms behaving
    correctly, so a fixture at 10 dB would be testing the wrong thing.
    """
    clean = np.array(session.y, copy=True)
    session.set_audio(clean, session.sr, name="clean")       # new baseline
    noise = amplitude * np.random.default_rng(3).standard_normal(len(clean))
    session.set_audio((clean + noise).astype(np.float32), session.sr,
                      undo_label="add noise", keep_original=True)


def test_with_a_clean_reference_the_quality_columns_appear(panel, real_session):
    _make_it_a_real_experiment(real_session)
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)

    for column in REFERENCE_COLUMNS[:2]:            # SI-SDR and dSNR
        assert panel.table.isColumnHidden(column) is False
    assert all(r.si_sdr_db is not None for r in panel._results)
    assert "Best against the reference" in panel.verdict.text()


def test_the_winner_is_the_best_si_sdr(panel, real_session):
    _make_it_a_real_experiment(real_session)
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)

    best = max(panel._results, key=lambda r: r.si_sdr_db)
    assert kudio.METHODS[best.method].label in panel.verdict.text()
    assert best.delta_snr_db > 0


def test_a_reference_file_that_was_never_chosen_is_reported(panel):
    panel.reference.setCurrentText(REF_FILE)
    messages = []
    panel.status.connect(messages.append)
    panel._run()
    assert "Select a reference file" in messages[-1]


def test_the_reference_file_row_is_only_live_for_that_choice(panel):
    assert panel.browse.isEnabled() is False
    panel.reference.setCurrentText(REF_FILE)
    assert panel.browse.isEnabled() is True
    panel.reference.setCurrentText(REF_NONE)
    assert panel.browse.isEnabled() is False


def test_numbers_sort_as_numbers(panel):
    """Sorted as text, "10.2" lands below "9.66" — in a ranking table."""
    from kudiostudio.panels.compare import NumericItem

    a, b = NumericItem("9.66"), NumericItem("10.2")
    a.setData(Qt.UserRole, 9.66)
    b.setData(Qt.UserRole, 10.2)
    assert (a < b) is True
    assert (b < a) is False
    assert ("10.2" < "9.66") is True             # what the default would do


def test_a_column_of_numbers_actually_sorts(panel):
    _run_and_wait(panel)
    ms = column("ms")
    panel.table.sortItems(ms, Qt.AscendingOrder)
    values = [float(panel.table.item(r, ms).text())
              for r in range(panel.table.rowCount())]
    assert values == sorted(values)


def test_without_a_reference_the_table_keeps_kudios_order(panel):
    """Alphabetical by label is not an order anybody asked for."""
    _run_and_wait(panel)
    shown = [key(panel, r)[0] for r in range(panel.table.rowCount())]
    assert shown == list(kudio.METHODS)


def test_with_a_reference_the_table_opens_ranked(panel, real_session):
    _make_it_a_real_experiment(real_session)
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)

    scores = [float(panel.table.item(r, column("SI-SDR")).text())
              for r in range(panel.table.rowCount())]
    assert scores == sorted(scores, reverse=True)
    best = max(panel._results, key=lambda r: r.si_sdr_db)
    assert key(panel, 0) == (best.method, best.noise)


def test_selecting_a_row_enables_audition_and_apply(panel):
    _run_and_wait(panel)
    assert panel.audition_button.isEnabled() is False
    panel.table.selectRow(0)
    assert panel.audition_button.isEnabled() is True
    assert panel.apply_button.isEnabled() is True


def test_auditioning_offers_the_audio_without_committing(panel):
    _run_and_wait(panel)
    before = np.array(panel.session.y, copy=True)
    heard = []
    panel.preview_ready.connect(lambda y, sr: heard.append((len(y), sr)))

    panel.table.selectRow(0)
    panel.audition_button.setChecked(True)
    assert heard, "nothing was offered for audition"
    assert np.array_equal(panel.session.y, before)
    assert panel.session.can_undo() is False


def test_switching_rows_while_auditioning_follows_the_selection(panel):
    _run_and_wait(panel)
    heard = []
    panel.preview_ready.connect(lambda y, sr: heard.append(len(y)))
    panel.table.selectRow(0)
    panel.audition_button.setChecked(True)
    first = len(heard)
    panel.table.selectRow(1)
    assert len(heard) > first


def test_apply_commits_the_selected_result(panel):
    _run_and_wait(panel)
    panel.table.selectRow(0)
    chosen = next(r for r in panel._results
                  if (r.method, r.noise) == key(panel, 0))

    panel._apply()
    assert np.allclose(panel.session.y, chosen.audio, atol=1e-6)
    assert panel.session.can_undo() is True
    assert panel.session.can_compare() is True       # A/B is now meaningful


def test_apply_releases_the_audition(panel):
    _run_and_wait(panel)
    panel.table.selectRow(0)
    panel.audition_button.setChecked(True)
    panel._apply()
    assert panel.audition_button.isChecked() is False


def test_new_audio_clears_a_stale_comparison(panel):
    """The rows describe audio that is no longer loaded."""
    _run_and_wait(panel)
    assert panel.table.rowCount() > 0
    panel.session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="new")
    panel.on_session_changed()
    assert panel.table.rowCount() == 0
    assert panel._results == []
    assert panel.save_button.isEnabled() is False


def test_save_all_names_files_by_both_choices(panel, tmp_path, monkeypatch):
    """Two rows can share a method; naming by method alone overwrites one."""
    from PySide6.QtWidgets import QFileDialog

    panel.sweep.setCurrentText(SWEEP_NOISES)
    _run_and_wait(panel)
    out = tmp_path / "results"
    out.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(out)))
    panel._save_all()
    written = sorted(p.stem.split("_", 1)[1] for p in out.glob("*.wav"))
    assert written == sorted(f"{panel.method.currentData()}_{n}"
                             for n in kudio.NOISE_ESTIMATORS)


def test_comparing_only_the_selection(panel):
    panel.selection_only.setChecked(True)
    panel.session.set_selection(0.2, 1.2)
    _run_and_wait(panel)
    expected = int(1.0 * panel.session.sr)
    assert all(abs(len(r.audio) - expected) <= 2 for r in panel._results)


def test_a_selection_too_short_to_frame_is_refused(panel):
    panel.selection_only.setChecked(True)
    panel.session.set_selection(0.0, 0.01)           # 160 samples
    messages = []
    panel.status.connect(messages.append)
    panel._run()
    assert "Too short" in messages[-1]


def test_the_call_hint_names_what_it_will_run(panel):
    assert "kudio.compare_enhancers" in panel.call.text()
    panel.noise.setCurrentIndex(panel.noise.findData("quantile"))
    assert "noise='quantile'" in panel.call.text()


def test_a_failing_method_is_shown_as_a_row_not_an_exception(panel, monkeypatch):
    import kudio.enhance.spectral as module

    real = module.spectral_enhance

    def explode(y, sr, method='logmmse', **kwargs):
        if method == 'wiener':
            raise RuntimeError("boom")
        return real(y, sr, method, **kwargs)

    monkeypatch.setattr(kudio, "compare_enhancers",
                        lambda *a, **k: module.compare_enhancers(*a, **k))
    monkeypatch.setattr(module, "spectral_enhance", explode)
    _run_and_wait(panel)

    failed = [r for r in panel._results if r.error]
    assert [r.method for r in failed] == ["wiener"]
    assert panel.table.rowCount() == len(kudio.METHODS)


def test_a_reference_that_is_not_clean_is_called_out(panel):
    """Comparing a denoised clip against the noisy original: every method
    scores worse than doing nothing, and the panel must say why rather than
    announcing a 'winner' that is -1 dB better than leaving it alone."""
    session = panel.session
    session.set_audio(kudio.spectral_enhance(session.y, session.sr, 'logmmse'),
                      session.sr, undo_label="logmmse", keep_original=True)
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)

    assert all(r.delta_snr_db < 0 for r in panel._results if r.error is None)
    text = panel.verdict.text()
    assert "No method beat leaving it alone" in text
    assert "not clean" in text


# ------------------------------------------------------------- sweeping axes

def test_the_default_sweep_is_gain_rules(panel):
    from kudiostudio.panels.compare import SWEEP_METHODS
    assert panel.sweep.currentText() == SWEEP_METHODS
    assert panel.noise.isEnabled() is True       # held fixed, so it is a choice
    assert panel.method.isEnabled() is False     # varied, so it is not


def test_sweeping_estimators_flips_which_picker_is_live(panel):
    panel.sweep.setCurrentText(SWEEP_NOISES)
    assert panel.method.isEnabled() is True
    assert panel.noise.isEnabled() is False


def test_the_grid_sweep_fixes_neither(panel):
    panel.sweep.setCurrentText(SWEEP_GRID)
    assert panel.method.isEnabled() is False
    assert panel.noise.isEnabled() is False


def test_sweeping_estimators_gives_a_row_per_estimator(panel):
    panel.sweep.setCurrentText(SWEEP_NOISES)
    panel.method.setCurrentIndex(panel.method.findData("logmmse"))
    _run_and_wait(panel)

    assert len(panel._results) == len(kudio.NOISE_ESTIMATORS)
    assert {r.method for r in panel._results} == {"logmmse"}
    assert {r.noise for r in panel._results} == set(kudio.NOISE_ESTIMATORS)


def test_the_grid_sweep_runs_every_combination(panel):
    panel.sweep.setCurrentText(SWEEP_GRID)
    _run_and_wait(panel)

    expected = len(kudio.METHODS) * len(kudio.NOISE_ESTIMATORS)
    assert len(panel._results) == expected
    assert panel.table.rowCount() == expected


def test_the_estimator_column_is_filled(panel):
    panel.sweep.setCurrentText(SWEEP_NOISES)
    _run_and_wait(panel)
    shown = {panel.table.item(r, column("noise")).text()
             for r in range(panel.table.rowCount())}
    assert shown == set(kudio.NOISE_ESTIMATORS)


def test_a_row_is_identified_by_both_choices(panel):
    """Seven rows shared a method the moment the grid could hold both."""
    panel.sweep.setCurrentText(SWEEP_GRID)
    _run_and_wait(panel)
    keys = [key(panel, r) for r in range(panel.table.rowCount())]
    assert len(set(keys)) == len(keys)


def test_the_grid_verdict_compares_the_two_axes(panel, real_session):
    """Which of the two decisions to spend effort on, answered per clip."""
    _make_it_a_real_experiment(real_session)
    panel.sweep.setCurrentText(SWEEP_GRID)
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)

    text = panel.verdict.text()
    assert "moved this clip" in text
    assert "noise estimator" in text and "gain rule" in text


def test_two_close_axes_are_called_a_tie(panel):
    """Crowning an axis on a 0.1 dB difference is inventing a finding."""
    from kudiostudio.panels.compare import ComparePanel

    def rows(scores):
        return [kudio.EnhanceResult(
            method=m, noise=n, audio=np.zeros(0, dtype=np.float32),
            seconds=0.0, lufs=0.0, noise_floor_db=0.0, si_sdr_db=v)
            for (m, n), v in scores.items()]

    tied = rows({("a", "x"): 0.0, ("a", "y"): 4.0,
                 ("b", "x"): 4.0, ("b", "y"): 8.0})
    assert "about equally" in ComparePanel._axis_spread(tied)

    lopsided = rows({("a", "x"): 0.0, ("a", "y"): 10.0,
                     ("b", "x"): 0.1, ("b", "y"): 10.1})
    assert "noise estimator moved this clip more" in \
        ComparePanel._axis_spread(lopsided)


def test_a_custom_row_is_left_out_of_the_axis_comparison(panel):
    """It sits on neither axis, so it cannot speak to which one matters."""
    from kudiostudio.panels.compare import ComparePanel

    scored = [kudio.EnhanceResult(
        method=m, noise=n, audio=np.zeros(0, dtype=np.float32),
        seconds=0.0, lufs=0.0, noise_floor_db=0.0, si_sdr_db=v)
        for (m, n), v in {("a", "x"): 0.0, ("a", "y"): 4.0,
                          ("b", "x"): 4.0, ("b", "y"): 8.0}.items()]
    with_custom = scored + [kudio.EnhanceResult(
        method="model", noise=kudio.CUSTOM_NOISE,
        audio=np.zeros(0, dtype=np.float32), seconds=0.0, lufs=0.0,
        noise_floor_db=0.0, si_sdr_db=999.0)]
    assert ComparePanel._axis_spread(with_custom) == \
        ComparePanel._axis_spread(scored)


def test_a_single_axis_sweep_makes_no_claim_about_which_matters(panel,
                                                                real_session):
    """With one estimator there is nothing to compare it against."""
    _make_it_a_real_experiment(real_session)
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)
    assert "moved this clip more than" not in panel.verdict.text()


def test_the_call_hint_follows_the_sweep(panel):
    panel.sweep.setCurrentText(SWEEP_NOISES)
    text = panel.call.text()
    assert "noises=list(kudio.NOISE_ESTIMATORS)" in text
    assert repr([panel.method.currentData()]) in text

    panel.sweep.setCurrentText(SWEEP_GRID)
    assert "noises=list(kudio.NOISE_ESTIMATORS)" in panel.call.text()


# ------------------------------------------------- a learned model in the table

def test_no_run_directory_means_no_extra_row(panel):
    assert panel.run_dir.path() == ""
    assert panel._learned_entry() is None
    _run_and_wait(panel)
    assert len(panel._results) == len(kudio.METHODS)


def test_a_run_directory_adds_one_row(panel, tmp_path, monkeypatch):
    """Ranked in the same table as the statistical methods, because that is
    the question — 'is the model actually better than log-MMSE'."""
    import kudiostudio.panels.compare as module

    run_dir = tmp_path / "runs" / "exp1"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "kudioenhance_available", lambda: True)
    monkeypatch.setattr(
        module.ComparePanel, "_learned_entry",
        lambda self: ("exp1", lambda y, sr: kudio.gain(y, -3.0)))

    panel.run_dir.edit.setText(str(run_dir))
    _run_and_wait(panel)

    assert len(panel._results) == len(kudio.METHODS) + 1
    learned = [r for r in panel._results if r.method == "exp1"]
    assert len(learned) == 1
    assert learned[0].noise == kudio.CUSTOM_NOISE


def test_a_learned_row_is_not_swept_over_estimators(panel, tmp_path, monkeypatch):
    import kudiostudio.panels.compare as module

    monkeypatch.setattr(
        module.ComparePanel, "_learned_entry",
        lambda self: ("exp1", lambda y, sr: y))
    panel.sweep.setCurrentText(SWEEP_GRID)
    _run_and_wait(panel)

    assert len([r for r in panel._results if r.method == "exp1"]) == 1
    assert len(panel._results) == \
        len(kudio.METHODS) * len(kudio.NOISE_ESTIMATORS) + 1


def test_a_learned_row_shows_its_own_name_in_the_table(panel, monkeypatch):
    import kudiostudio.panels.compare as module

    monkeypatch.setattr(
        module.ComparePanel, "_learned_entry",
        lambda self: ("exp1", lambda y, sr: y))
    _run_and_wait(panel)
    labels = {panel.table.item(r, 0).text()
              for r in range(panel.table.rowCount())}
    assert "exp1" in labels                    # not looked up in kudio.METHODS


def test_a_learned_row_can_win(panel, real_session, monkeypatch):
    import kudiostudio.panels.compare as module

    _make_it_a_real_experiment(real_session)
    clean = np.array(real_session.original()[0], copy=True)
    monkeypatch.setattr(
        module.ComparePanel, "_learned_entry",
        lambda self: ("oracle", lambda y, sr: clean[:len(y)]))
    panel.reference.setCurrentText(REF_ORIGINAL)
    _run_and_wait(panel)

    best = max(panel._results, key=lambda r: r.si_sdr_db)
    assert best.method == "oracle"
    assert "oracle" in panel.verdict.text()


def test_a_missing_kudioenhance_is_reported_not_raised(panel, tmp_path,
                                                        monkeypatch):
    import kudiostudio.panels.compare as module

    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    monkeypatch.setattr(module, "kudioenhance_available", lambda: False)
    panel.run_dir.edit.setText(str(run_dir))

    assert "pip install" in panel.call.text()
    messages = []
    panel.status.connect(messages.append)
    panel._run()
    assert "pip install" in messages[-1]


# -------------------------------------------- the learned row's model, kept

#: long enough that a row charged for it cannot pass for a fast one
SLOW_SECONDS = 0.6


def install_fake_model(monkeypatch):
    """A model that is slow to load, as a real one is, and instant to run.

    It sits where ``from kudioenhance.inference import Enhancer`` finds it,
    so the panel's own loading code is what runs, with kudioenhance
    installed or not. Set ``first_pass`` to make its first run slow too.
    """
    import kudiostudio.panels.compare as module

    class Enhancer:
        loads = []
        load_seconds = SLOW_SECONDS
        first_pass = 0.0
        refuse = None

        def __init__(self):
            self.cfg = SimpleNamespace(audio=SimpleNamespace(sr=16000))
            self.passes = 0

        @classmethod
        def load(cls, run_dir):
            cls.loads.append(str(run_dir))
            time.sleep(cls.load_seconds)
            if cls.refuse is not None:
                raise cls.refuse
            return cls()

        def enhance(self, y):
            self.passes += 1
            if self.passes == 1:
                time.sleep(self.first_pass)
            return 0.5 * np.asarray(y, dtype=np.float32)

    inference = types.ModuleType("kudioenhance.inference")
    inference.Enhancer = Enhancer
    monkeypatch.setitem(sys.modules, "kudioenhance.inference", inference)
    monkeypatch.setattr(module, "kudioenhance_available", lambda: True)
    return Enhancer


@pytest.fixture
def slow_model(panel, tmp_path, monkeypatch):
    """The panel, pointed at a run directory that loads the fake model."""
    model = install_fake_model(monkeypatch)
    run_dir = tmp_path / "runs" / "exp1"
    run_dir.mkdir(parents=True)
    panel.run_dir.edit.setText(str(run_dir))
    return model


def learned_row(panel):
    rows = [r for r in panel._results if r.noise == kudio.CUSTOM_NOISE]
    assert len(rows) == 1
    return rows[0]


def test_a_learned_rows_time_leaves_out_loading_the_model(panel, slow_model):
    """The column ranks rows against each other; a load inside the row was
    charged at seconds against milliseconds of denoising."""
    _run_and_wait(panel)

    row = learned_row(panel)
    assert row.error is None
    assert slow_model.loads == [panel.run_dir.path()]
    assert row.seconds < SLOW_SECONDS / 2


def test_a_learned_rows_time_leaves_out_its_first_pass(panel, slow_model):
    """A network's first pass pays one-time set-up, as kudio's own methods
    do before compare_enhancers warms them up."""
    slow_model.load_seconds = 0.0
    slow_model.first_pass = SLOW_SECONDS
    _run_and_wait(panel)

    row = learned_row(panel)
    assert row.error is None
    assert row.seconds < SLOW_SECONDS / 2


def test_the_model_is_loaded_once_per_run_directory(panel, slow_model,
                                                    tmp_path):
    slow_model.load_seconds = 0.0
    first = panel.run_dir.path()
    _run_and_wait(panel)
    _run_and_wait(panel)
    assert slow_model.loads == [first]

    other = tmp_path / "runs" / "exp2"
    other.mkdir()
    panel.run_dir.edit.setText(str(other))
    assert panel._model.enhancer is None        # the old model is let go
    _run_and_wait(panel)
    assert slow_model.loads == [first, str(other)]
    assert learned_row(panel).method == "exp2"


def test_a_model_kept_from_another_run_is_not_used(panel, slow_model):
    """A directory changed while a comparison is still loading leaves the old
    run's model in the cache after the change has cleared it."""
    slow_model.load_seconds = 0.0
    panel._model.enhancer, panel._model.run_dir = slow_model(), "runs/old"
    _run_and_wait(panel)
    assert slow_model.loads == [panel.run_dir.path()]
    assert panel._model.run_dir == panel.run_dir.path()


def test_a_model_that_will_not_load_fails_its_own_row(panel, slow_model):
    """Loading ahead of the comparison must not change what a failure looks
    like: one row with its error, beside every row that worked."""
    slow_model.load_seconds = 0.0
    slow_model.refuse = FileNotFoundError("no model at runs/exp1/ddae.pt")
    messages = []
    panel.status.connect(messages.append)
    _run_and_wait(panel)

    assert "no model at" in learned_row(panel).error
    others = [r for r in panel._results if r.noise != kudio.CUSTOM_NOISE]
    assert len(others) == len(kudio.METHODS)
    assert all(r.error is None for r in others)
    assert not any("Comparison failed" in m for m in messages)


def test_a_panel_let_go_after_a_learned_comparison_is_freed_quietly(
        real_session, tmp_path, monkeypatch):
    """A finished worker used to await deletion holding whatever it was
    handed. Handed a method of its panel, it held the panel's last reference:
    deleting the worker deleted the panel, which deleted the worker again --
    a segfault, and the end of the test run. Now it lets go once the event
    loop has run past the job's end."""
    from PySide6.QtCore import QCoreApplication, QEvent

    install_fake_model(monkeypatch).load_seconds = 0.0
    run_dir = tmp_path / "exp1"
    run_dir.mkdir()
    panel = ComparePanel(real_session)
    panel.metrics.setChecked(False)
    panel.run_dir.edit.setText(str(run_dir))
    _run_and_wait(panel)
    assert learned_row(panel).error is None

    gone = weakref.ref(panel)
    del panel
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()
    gc.collect()
    assert gone() is None


def test_a_model_trained_again_in_the_same_directory_is_loaded_again(
        panel, slow_model):
    """The cache was keyed on the directory's name. Trained again in place,
    the run went on being ranked with the old weights, while Apply recorded a
    load of the new ones."""
    slow_model.load_seconds = 0.0
    run_dir = Path(panel.run_dir.path())
    for name in ("config.yaml", "stats.npz", "ddae.pt"):
        (run_dir / name).write_bytes(b"first")
    _run_and_wait(panel)
    _run_and_wait(panel)
    assert slow_model.loads == [str(run_dir)], "unchanged: kept"

    # kudioenhance writes a checkpoint by replacing it
    retrained = run_dir / "ddae.tmp"
    retrained.write_bytes(b"second, and longer")
    retrained.replace(run_dir / "ddae.pt")
    _run_and_wait(panel)
    assert slow_model.loads == [str(run_dir)] * 2


def _held_comparison(panel, monkeypatch):
    """Make the next comparison wait for the returned event before running."""
    import threading

    import kudiostudio.panels.compare as module

    release = threading.Event()
    real = module.compare_with_model

    def held(*args, **kwargs):
        release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "compare_with_model", held)
    return release


def _wait(panel):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    panel.jobs.busy_changed.connect(lambda busy: None if busy else loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    if panel.jobs.busy:
        loop.exec()
    panel.jobs.wait(60_000)


def test_results_of_audio_edited_while_they_ran_are_dropped(panel, real_session,
                                                            monkeypatch):
    """They were ranked as if from the edit, and Apply recorded them as code
    that would compute something else from it -- with no warning at export."""
    messages = []
    panel.status.connect(messages.append)
    real_session.changed.connect(panel.on_session_changed)
    release = _held_comparison(panel, monkeypatch)
    panel._run()
    real_session.replace_selection(kudio.gain(real_session.y, -12.0),
                                   undo_label="gain",
                                   code="y = kudio.gain(y, db=-12.0)")
    release.set()
    _wait(panel)

    assert panel.table.rowCount() == 0
    assert panel._results == []
    assert "audio changed while the comparison ran" in messages[-1]
    assert [step.label for step in real_session.history] == ["gain"]
    assert real_session.edit_script_warnings() == []


def test_rows_on_screen_are_applied_as_the_run_that_made_them(
        panel, real_session, monkeypatch):
    """A second run started while the first one's rows were on screen used to
    lend them its range: a row computed from a selection was recorded as
    whole-clip code, and the script wrote twice the audio."""
    real_session.changed.connect(panel.on_session_changed)
    panel.selection_only.setChecked(True)
    real_session.set_selection(0.5, 1.5)
    _run_and_wait(panel)
    assert len(panel._results[0].audio) == real_session.sr

    panel.selection_only.setChecked(False)
    release = _held_comparison(panel, monkeypatch)
    panel._run()
    panel.table.selectRow(0)
    panel._apply()
    release.set()
    _wait(panel)

    assert real_session.history[-1].code is None
    assert any("was not recorded as code" in w
               for w in real_session.edit_script_warnings())


#: skipped without kudioenhance and PyTorch -- see conftest.py
requires_enhance = pytest.mark.learned


@requires_enhance
def test_a_trained_model_is_ranked_and_kept_for_the_next_comparison(
        panel, trained_run):
    run_dir, sr = trained_run
    assert panel.session.sr == sr
    panel.run_dir.edit.setText(str(run_dir))

    _run_and_wait(panel)
    row = learned_row(panel)
    assert row.error is None
    assert row.method == run_dir.name
    assert len(row.audio) == len(panel.session.y)
    assert np.isfinite(row.audio).all()
    loaded = panel._model.enhancer
    assert loaded is not None
    assert panel._model.run_dir == str(run_dir)

    _run_and_wait(panel)
    assert learned_row(panel).error is None
    assert panel._model.enhancer is loaded      # not read from disk again


# --------------------------------------------------- auditioning two at once

def test_two_rows_play_back_to_back(panel):
    """Clicking between them puts a gap and a mouse movement inside the only
    thing being judged."""
    from kudiostudio.panels.compare import AB_GAP_SECONDS

    _run_and_wait(panel)
    heard = []
    panel.preview_ready.connect(lambda y, sr: heard.append(y))

    select_rows(panel, 0, 1)
    panel.audition_button.setChecked(True)

    first, second = panel._selected_rows()[:2]
    gap = int(AB_GAP_SECONDS * panel.session.sr)
    assert len(heard[-1]) == len(first.audio) + gap + len(second.audio)


def test_the_button_says_what_it_will_do(panel):
    _run_and_wait(panel)
    panel.table.selectRow(0)
    assert panel.audition_button.text() == "♪  Audition"

    panel.table.selectAll()
    assert panel.audition_button.text() == "♪  Audition A→B"


def test_apply_needs_exactly_one_row(panel):
    """Two rows is a comparison, not a decision."""
    _run_and_wait(panel)
    panel.table.selectRow(0)
    assert panel.apply_button.isEnabled() is True

    panel.table.selectAll()
    assert panel.apply_button.isEnabled() is False
    assert "one row" in panel.apply_button.toolTip()


def test_rows_are_heard_in_the_tables_order_not_the_click_order(panel):
    """The table is ranked; hearing them ranked is what makes "the one above
    sounds better" a sentence about the ranking."""
    _run_and_wait(panel)
    select_rows(panel, 3, 1)          # the lower row clicked first
    rows = panel._selected_rows()
    assert [r.method for r in rows] == [
        key(panel, 1)[0], key(panel, 3)[0]]


def _break_wiener(monkeypatch) -> None:
    """Make the Wiener row fail, the way a method that raises leaves a row."""
    import kudio.enhance.spectral as module

    real = module.spectral_enhance

    def explode(y, sr, method='logmmse', **kwargs):
        if method == 'wiener':
            raise RuntimeError("boom")
        return real(y, sr, method, **kwargs)

    monkeypatch.setattr(kudio, "compare_enhancers",
                        lambda *a, **k: module.compare_enhancers(*a, **k))
    monkeypatch.setattr(module, "spectral_enhance", explode)


def test_a_failed_row_is_not_auditioned(panel, monkeypatch):
    _break_wiener(monkeypatch)
    _run_and_wait(panel)

    panel.table.selectAll()
    heard = []
    panel.preview_ready.connect(lambda y, sr: heard.append(y))
    panel.audition_button.setChecked(True)

    usable = [r for r in panel._results if r.error is None]
    from kudiostudio.panels.compare import AB_GAP_SECONDS
    gap = int(AB_GAP_SECONDS * panel.session.sr)
    expected = sum(len(r.audio) for r in usable) + gap * (len(usable) - 1)
    assert len(heard[-1]) == expected


def test_apply_takes_the_one_usable_row_when_a_failed_one_sits_above_it(
        panel, monkeypatch):
    """The button is enabled for one usable row, so that is what it applies --
    it used to take the top selected row, find it failed, and do nothing."""
    _break_wiener(monkeypatch)
    _run_and_wait(panel)
    failed = next(row for row in range(panel.table.rowCount())
                  if key(panel, row)[0] == "wiener")
    below = failed + 1
    select_rows(panel, failed, below)
    assert panel.apply_button.isEnabled() is True

    chosen = next(r for r in panel._results
                  if (r.method, r.noise) == key(panel, below))
    panel._apply()
    assert panel.session.can_undo() is True
    assert np.allclose(panel.session.y, chosen.audio, atol=1e-6)


def test_a_resort_while_auditioning_replays_in_the_new_order(panel):
    """Heard top to bottom is the promise, and a sort changes what the top is
    without changing which rows are selected -- so the selection signal alone
    never noticed."""
    _run_and_wait(panel)
    heard = []
    panel.preview_ready.connect(lambda y, sr: heard.append(y))
    select_rows(panel, 0, 1)
    panel.audition_button.setChecked(True)
    before = len(heard)

    panel.table.sortItems(0, Qt.DescendingOrder)          # the two swap
    first = panel._selected_rows()[0]
    assert len(heard) == before + 1
    assert np.array_equal(heard[-1][:len(first.audio)],
                          np.asarray(first.audio, dtype=np.float32))


def test_a_sort_that_keeps_the_order_does_not_restart_the_loop(panel):
    _run_and_wait(panel)
    heard = []
    panel.preview_ready.connect(lambda y, sr: heard.append(y))
    select_rows(panel, 0, 1)
    panel.audition_button.setChecked(True)
    before = len(heard)

    panel.table.sortItems(0, Qt.AscendingOrder)           # already this order
    assert len(heard) == before
    panel.audition_button.setChecked(False)
    panel.table.sortItems(0, Qt.DescendingOrder)          # not auditioning
    assert len(heard) == before
