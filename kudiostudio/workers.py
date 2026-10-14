# -*- coding: utf-8 -*-
"""Run blocking `kudio` calls off the GUI thread.

Batch synthesis, file conversion and playback can take seconds to minutes;
everything that is not instantaneous goes through :class:`Worker` so the UI
keeps repainting.

Running Python on a second thread has one consequence nothing in a panel can
see: the cycle collector runs on whichever thread happens to allocate past its
threshold, and a job allocates a great deal. A widget that has become
garbage is then destroyed on the worker's thread, mid-job -- which is not a
thing Qt survives. :func:`collect_on_gui_thread` moves collection to the GUI
thread's event loop, where widgets are created and have to die.
"""
from __future__ import annotations

import gc
import logging
import traceback
from typing import Any, Callable, Optional, Tuple

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, Slot

log = logging.getLogger(__name__)

#: Every worker whose thread has not been cleaned up yet. A job handed its
#: panel's method is a reference cycle -- panel, host, worker, method, panel
#: -- and one the garbage collector can see: with nothing else holding the
#: panel, a collection while the job runs would free all four and destroy a
#: thread that is still running. Held here, the cycle is not garbage until
#: the job is over.
_RUNNING: set = set()


class Worker(QThread):
    """Call ``fn(*args, **kwargs)`` in a thread and report back.

    >>> w = Worker(kudio.file_load, "clip.wav")
    >>> w.done.connect(lambda result: ...)
    >>> w.failed.connect(lambda msg: ...)
    >>> w.start()
    """

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, parent: QObject = None, **kwargs):
        super().__init__(parent)
        self._fn, self._args, self._kwargs = fn, args, kwargs
        #: what :class:`WorkerHost` calls with the outcome, on the GUI thread
        self.on_done: Optional[Callable] = None
        self.on_failed: Optional[Callable] = None

    def run(self) -> None:  # noqa: D102 - QThread hook
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as e:  # surfaced in the UI, never crashes the app
            log.debug("worker failed:\n%s", traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")

    def release(self) -> Tuple[Any, ...]:
        """Let go of the job and its callbacks, and hand them back.

        For the GUI thread, once the job is over -- never for :meth:`run`,
        which would drop them on the worker thread. Handed back rather than
        dropped here, so that the caller decides where the last reference
        goes: it can be the only thing still keeping a panel alive.
        """
        held = (self._fn, self._args, self._kwargs, self.on_done, self.on_failed)
        self._fn = self._args = self._kwargs = None
        self.on_done = self.on_failed = None
        return held


class WorkerHost(QObject):
    """Keeps one worker alive at a time and cleans it up when it finishes.

    Without a reference the QThread would be garbage-collected mid-run; this
    is the usual Qt footgun, wrapped once so no panel has to remember it.

    **A finished worker holds nothing.** What a job is handed is usually its
    panel's -- a bound method, a lambda over ``self`` -- and the worker is a Qt
    child of this host, which is a child of the panel. A worker that kept
    those until Qt got round to deleting it was a reference cycle Python could
    not see: the panel outlived every other reference to it, and when the
    deferred delete finally dropped the last one, the panel's destructor
    deleted this host, which deleted the same worker a second time from
    inside its own destructor -- a segfault, or a hang on the thread's lock.
    So no callback is ever connected to the worker: it signals this host, the
    host calls them, and once the thread has finished the worker lets go of
    everything, on the GUI thread.
    """

    busy_changed = Signal(bool)

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._worker: Worker = None

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def run(self, fn: Callable[..., Any], *args, on_done=None, on_failed=None, **kwargs) -> bool:
        """Start *fn*; returns False when a job is already running."""
        if self.busy:
            return False
        worker = Worker(fn, *args, parent=self, **kwargs)
        worker.on_done, worker.on_failed = on_done, on_failed
        worker.done.connect(self._done)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._clear)
        self._worker = worker
        _RUNNING.add(worker)
        self.busy_changed.emit(True)
        worker.start()
        return True

    @Slot(object)
    def _done(self, result) -> None:
        worker = self.sender()
        if isinstance(worker, Worker) and worker.on_done is not None:
            worker.on_done(result)

    @Slot(str)
    def _failed(self, message: str) -> None:
        worker = self.sender()
        if isinstance(worker, Worker) and worker.on_failed is not None:
            worker.on_failed(message)

    @Slot()
    def _clear(self) -> None:
        # the worker that finished -- not necessarily the current one, since
        # a callback can start the next job before this arrives
        worker = self.sender()
        if not isinstance(worker, Worker):
            return
        current = worker is self._worker
        if current:
            self._worker = None
        held = worker.release()
        _RUNNING.discard(worker)
        worker.deleteLater()
        if current:
            self.busy_changed.emit(False)
        # Dropped from the event loop, not here. When the job was all that
        # still referred to its panel, the last reference goes with it, and a
        # panel destroyed from inside this slot takes this host with it while
        # Qt is still delivering to it.
        QTimer.singleShot(0, _Release(held))

    def wait(self, msecs: Optional[int] = 5000) -> None:
        """Block until the current job ends -- used on shutdown and in tests.

        ``None`` waits for as long as the job takes.
        """
        if self._worker is not None:
            if msecs is None:
                self._worker.wait()
            else:
                self._worker.wait(msecs)


class _Release:
    """What a finished worker let go of, until the event loop calls this."""

    __slots__ = ("_held",)

    def __init__(self, held) -> None:
        self._held = held

    def __call__(self) -> None:
        self._held = None


class GuiThreadCollector(QObject):
    """Python's cycle collector, run from the GUI thread's event loop only.

    Automatic collection is switched off and done here instead, on a timer,
    by the same per-generation thresholds Python would have used -- the
    approach pyqtgraph's own GarbageCollector takes, for the same reason.
    Python's reference counting still frees everything that is not in a
    cycle the moment it is let go of, on whatever thread lets go of it; only
    the cycles wait, for a second at most.
    """

    #: how often the counts are looked at, in milliseconds
    INTERVAL_MS = 1000

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._threshold = gc.get_threshold()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)
        gc.disable()
        self._timer.start(self.INTERVAL_MS)

    def check(self) -> None:
        counts = gc.get_count()
        if counts[0] > self._threshold[0]:
            generation = 0
            for level in (1, 2):
                if level < len(counts) and level < len(self._threshold) \
                        and counts[level] > self._threshold[level] > 0:
                    generation = level
            gc.collect(generation)


_collector: Optional[GuiThreadCollector] = None


def collect_on_gui_thread() -> GuiThreadCollector:
    """Collect cycles on the GUI thread from now on; call once a
    `QApplication` exists. Calling it again changes nothing."""
    global _collector
    if _collector is None:
        _collector = GuiThreadCollector(QCoreApplication.instance())
    return _collector

