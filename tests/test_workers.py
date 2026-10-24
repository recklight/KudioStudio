# -*- coding: utf-8 -*-
"""A job's callback can start the next job for the same owner, and the
first one's clean-up leaves the second alone."""
from __future__ import annotations

import queue
import threading
import time

import pytest

from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture
def studio(tmp_path):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    yield s
    s.shutdown()


def test_a_callback_can_start_the_next_job(studio):
    """The callback hands back what starts the second job, which runs once
    the first is cleaned up, so that clean-up cannot take the second one --
    still running -- with it: the owner stays busy until the second is over."""
    events = studio.events.subscribe()
    results = []
    started, release = threading.Event(), threading.Event()

    def two():
        started.set()
        release.wait(10)
        return "two"

    def first(result):
        results.append(result)
        # the owner is busy while this runs; what it returns runs after
        return lambda: studio.jobs.run("probe", "second", two, on_done=results.append)

    assert studio.jobs.run("probe", "first", lambda: "one", on_done=first)
    assert started.wait(10)
    assert studio.jobs.busy("probe")
    release.set()
    assert studio.jobs.wait(10)

    # the last job event is published just after the owner is let go
    busy = []
    deadline = time.monotonic() + 10
    while len(busy) < 4 and time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.1)
        except queue.Empty:
            continue
        if event["type"] == "job" and event["owner"] == "probe":
            busy.append(event["busy"])
    assert results == ["one", "two"]
    assert not studio.jobs.busy("probe")
    assert busy == [True, False, True, False]
