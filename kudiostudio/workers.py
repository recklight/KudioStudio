# -*- coding: utf-8 -*-
"""Run blocking `kudio` calls off the GUI thread.

Batch synthesis, file conversion and playback can take seconds to minutes;
everything that is not instantaneous goes through :class:`Worker` so the UI
keeps repainting.
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

log = logging.getLogger(__name__)


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

    def run(self) -> None:  # noqa: D102 - QThread hook
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as e:  # surfaced in the UI, never crashes the app
            log.debug("worker failed:\n%s", traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")


class WorkerHost(QObject):
    """Keeps one worker alive at a time and cleans it up when it finishes.

    Without a reference the QThread would be garbage-collected mid-run; this
    is the usual Qt footgun, wrapped once so no panel has to remember it.
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
        if on_done is not None:
            worker.done.connect(on_done)
        if on_failed is not None:
            worker.failed.connect(on_failed)
        worker.finished.connect(self._clear)
        self._worker = worker
        self.busy_changed.emit(True)
        worker.start()
        return True

    def _clear(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self.busy_changed.emit(False)

    def wait(self, msecs: int = 5000) -> None:
        """Block until the current job ends -- used on shutdown and in tests."""
        if self._worker is not None:
            self._worker.wait(msecs)
