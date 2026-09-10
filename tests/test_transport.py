# -*- coding: utf-8 -*-
"""Player: the playhead neither backend reports, estimated from the clock."""
from __future__ import annotations

import numpy as np
import pytest


def _wait(ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def player(qapp, monkeypatch):
    """A Player whose audio goes nowhere, so the suite stays silent."""
    import kudio
    from kudiostudio.widgets import Player

    monkeypatch.setattr(kudio, "play_audio", lambda *a, **k: None)
    p = Player()
    yield p
    p.shutdown()


def test_playhead_advances_and_stops_at_the_end(player):
    positions = []
    finished = []
    player.position_changed.connect(positions.append)
    player.finished.connect(lambda: finished.append(True))

    player.play(np.zeros(1600, dtype=np.float32), 16000, t0=0.5)  # 0.1 s
    assert player.playing is True
    assert positions == [0.5]

    _wait(300)

    assert finished == [True]
    assert player.playing is False
    assert len(positions) > 2, "the cursor should have moved more than once"
    assert positions == sorted(positions)
    assert positions[-1] == pytest.approx(0.6, abs=5e-3)
    assert player.position == pytest.approx(0.6, abs=5e-3)


def test_stop_freezes_the_playhead_without_claiming_it_finished(player):
    finished = []
    player.finished.connect(lambda: finished.append(True))

    player.play(np.zeros(16000, dtype=np.float32), 16000)   # 1 s
    _wait(120)
    player.stop()

    assert finished == []
    assert player.playing is False
    frozen = player.position
    assert 0.0 < frozen < 1.0
    _wait(120)
    assert player.position == frozen     # the clock is not still running


def test_playing_again_rebases_the_timeline(player):
    player.play(np.zeros(16000, dtype=np.float32), 16000, t0=0.0)
    _wait(80)
    player.play(np.zeros(1600, dtype=np.float32), 16000, t0=0.25)

    # `position` is read off a live monotonic clock, so whatever the scheduler
    # spends between play() and here is included. The claim under test is that
    # the timeline moved to 0.25 rather than continuing from 0.08 -- a tight
    # tolerance here only measures how busy the machine is.
    assert player.position == pytest.approx(0.25, abs=0.05)


def test_a_backend_error_is_reported_and_stops_the_clock(qapp, monkeypatch):
    import kudio
    from kudiostudio.widgets import Player

    monkeypatch.setattr(kudio, "play_audio", lambda *a, **k: "no output device")
    player = Player()
    failures = []
    player.failed.connect(failures.append)

    player.play(np.zeros(16000, dtype=np.float32), 16000)
    assert failures == ["no output device"]
    assert player.playing is False
    player.shutdown()
