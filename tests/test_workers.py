# -*- coding: utf-8 -*-
"""A job's panel can be let go of the moment the job is over.

What a worker is handed is usually its panel's -- a bound method, a lambda
over ``self`` -- and the worker is a Qt child of the panel's host. A worker
that kept those until Qt deleted it held the panel's last reference: the
deferred delete dropped it, the panel's destructor deleted the host, and the
host deleted the same worker a second time. That is a segfault, which takes
the test run with it, so each case runs in an interpreter of its own and the
test reads the exit code.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

CHILD = textwrap.dedent('''
    import gc, os, sys, threading, time, weakref
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {repo!r})

    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    import kudio
    from kudiostudio.panels.base import Panel
    from kudiostudio.session import AudioSession

    app = QApplication([])
    case = sys.argv[1]


    class Probe(Panel):
        def work(self, x):
            time.sleep(0.2)
            return 2 * x

        def done(self, result):
            self.result = result


    def build():
        session = AudioSession()
        if case == "record":
            from kudiostudio.panels.record import RecordPanel
            # no microphone here: a take of silence, after a moment
            kudio.record = lambda seconds, sr, **kw: (
                time.sleep(0.2) or np.zeros(int(seconds * sr), np.float32))
            panel = RecordPanel(session)
            panel.seconds.setValue(0.5)
            panel._record()
        elif case == "synth":
            from kudiostudio.panels.synth import SynthPanel
            root = sys.argv[2]
            panel = SynthPanel(session)
            panel.clean.edit.setText(os.path.join(root, "clean"))
            panel.noise.edit.setText(os.path.join(root, "noise"))
            panel.out.edit.setText(os.path.join(root, "out"))
            panel.snr.setText("0")
            panel._run()
        else:
            panel = Probe(session)
            if case in ("bound", "midjob"):
                panel.jobs.run(panel.work, 21, on_done=panel.done)
            elif case == "lambda":
                panel.jobs.run(abs, -21, on_done=lambda r: panel.done(r))
            elif case == "failed":
                panel.jobs.run(int, "x",
                               on_failed=lambda message: panel.done(message))
        assert panel.jobs.busy
        return panel


    panel = build()
    where = []
    panel.destroyed.connect(
        lambda *_: where.append(threading.current_thread().name))
    gone = weakref.ref(panel)

    if case == "midjob":
        # let go while the job is still running: the job is all that keeps
        # the panel, until it finishes
        pass
    else:
        loop = QEventLoop()
        panel.jobs.busy_changed.connect(
            lambda busy: None if busy else loop.quit())
        QTimer.singleShot(20000, loop.quit)
        if panel.jobs.busy:
            loop.exec()
        panel.jobs.wait(20000)
        del loop
    del panel
    end = time.time() + 3.0
    while gone() is not None and time.time() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        gc.collect()
        time.sleep(0.01)
    print("freed" if gone() is None else "alive", where, flush=True)
''')


def run_child(tmp_path, *args) -> subprocess.CompletedProcess:
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(repo=str(REPO)), encoding="utf-8")
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, timeout=120,
                          cwd=tmp_path)


@pytest.mark.parametrize("case", ["bound", "lambda", "failed", "midjob",
                                  "record"])
def test_a_panel_let_go_after_its_job_is_freed_on_the_gui_thread(tmp_path,
                                                                 case):
    done = run_child(tmp_path, case)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "freed ['MainThread']", done.stdout


def test_a_synthesis_panel_let_go_after_its_run_is_freed(tmp_path):
    """The Synthesize panel used to hand its worker a method of its own."""
    import numpy as np

    import kudio

    for folder in ("clean", "noise"):
        (tmp_path / folder).mkdir()
    t = np.arange(8000) / 16000
    kudio.save_wave(tmp_path / "clean" / "c.wav",
                    (0.05 * np.sin(2 * np.pi * 200 * t)).astype(np.float32),
                    16000)
    kudio.save_wave(tmp_path / "noise" / "n.wav",
                    (0.01 * np.random.default_rng(0).standard_normal(16000)
                     ).astype(np.float32), 16000)
    done = run_child(tmp_path, "synth", str(tmp_path))
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "freed ['MainThread']", done.stdout
    assert (tmp_path / "out" / "manifest.json").is_file()


CHAIN = textwrap.dedent('''
    import os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {repo!r})
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from kudiostudio.workers import WorkerHost

    app = QApplication([])
    host = WorkerHost()
    results, busy = [], []
    host.busy_changed.connect(busy.append)
    loop = QEventLoop()

    def second(result):
        results.append(result)
        loop.quit()

    def first(result):
        results.append(result)
        host.wait(5000)         # the thread is over; its clean-up is not yet
        assert host.run(lambda: time.sleep(0.3) or "two", on_done=second)

    assert host.run(lambda: "one", on_done=first)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    host.wait(10000)
    app.processEvents()
    print(results, host.busy, busy[-1], flush=True)
''')


def test_a_callback_can_start_the_next_job(tmp_path):
    """The first job's clean-up arrives after its callback has started the
    second, and must not take the second one -- still running -- with it:
    a thread destroyed while it runs aborts the process."""
    script = tmp_path / "chain.py"
    script.write_text(CHAIN.format(repo=str(REPO)), encoding="utf-8")
    done = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, timeout=120, cwd=tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "['one', 'two'] False False", done.stdout


def test_cycles_are_collected_on_the_gui_thread_and_nowhere_else(qapp):
    """A job allocates enough to trigger the cycle collector on its own
    thread, which destroyed whatever widget had become garbage -- a panel a
    test had let go of -- there, mid-job. The collector runs from the GUI
    thread's event loop instead."""
    import gc
    import threading

    from PySide6.QtCore import QEventLoop, QTimer

    from kudiostudio.workers import WorkerHost

    threads = []

    def note(phase, info):
        if phase == "start":
            threads.append(threading.current_thread().name)

    def allocate():
        # containers are what the collector counts
        for _ in range(20):
            junk = [[] for _ in range(50_000)]
            del junk
        return "done"

    gc.callbacks.append(note)
    try:
        host = WorkerHost()
        assert host.run(allocate)
        host.wait(60_000)
        assert threads == [], "collected on the worker's thread"

        cycles = [[] for _ in range(50_000)]
        for item in cycles:
            item.append(item)
        del cycles
        loop = QEventLoop()
        QTimer.singleShot(2500, loop.quit)
        loop.exec()
    finally:
        gc.callbacks.remove(note)
    assert threads and set(threads) == {threading.main_thread().name}
