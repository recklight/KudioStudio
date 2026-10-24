# -*- coding: utf-8 -*-
"""Work that takes seconds to minutes, off the request threads.

A panel hands a function to `Jobs.run`; it runs on a thread of its own, and
its result comes back to the panel under the Studio's lock, where touching
the session is safe. One job per owner at a time, and none can be stopped
half-way -- a kudio call has no point to stop it at -- so closing the window
waits for them (see `running`).

The page hears about it through events: ``job`` when an owner starts or
stops being busy, ``progress`` for a line of how far along it is.
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class Jobs:
    """The jobs of one Studio, keyed by owner (a panel's id)."""

    def __init__(self, studio) -> None:
        self.studio = studio
        self._lock = threading.Lock()
        #: owner -> (label, thread)
        self._running: Dict[str, Tuple[str, threading.Thread]] = {}
        self._idle = threading.Condition(self._lock)

    def busy(self, owner: str) -> bool:
        with self._lock:
            return owner in self._running

    def running(self) -> List[Tuple[str, str]]:
        """(owner, label) of every job still going."""
        with self._lock:
            return [(owner, label) for owner, (label, _) in self._running.items()]

    def run(self, owner: str, label: str, fn: Callable[..., Any], *args,
            on_done: Optional[Callable[[Any], None]] = None,
            on_failed: Optional[Callable[[str], None]] = None,
            **kwargs) -> bool:
        """Start ``fn(*args, **kwargs)``; False when *owner* is already busy.

        *on_done* gets the result and *on_failed* the error as one line,
        both under the Studio's lock. Either may return a function to call
        once this job is over -- the way to start the next job for the same
        owner, which while the callback runs is still busy.
        """
        with self._lock:
            if owner in self._running:
                return False
            thread = threading.Thread(
                target=self._work, args=(owner, fn, args, kwargs, on_done, on_failed),
                name=f"job-{owner}", daemon=True)
            self._running[owner] = (label, thread)
        self.studio.events.publish("job", owner=owner, busy=True, label=label)
        thread.start()
        return True

    def progress(self, owner: str, text: str) -> None:
        """A line saying how far *owner*'s job has got."""
        from kudiostudio import i18n
        self.studio.events.publish("progress", owner=owner,
                                   text=i18n.translate(text, self.studio.language))

    def _work(self, owner, fn, args, kwargs, on_done, on_failed) -> None:
        try:
            result, error = fn(*args, **kwargs), None
        except Exception as e:  # noqa: BLE001 - reported, never raised
            log.debug("job %s failed:\n%s", owner, traceback.format_exc())
            result, error = None, f"{type(e).__name__}: {e}"
        after = None
        try:
            with self.studio.lock:
                if error is None:
                    if on_done is not None:
                        after = on_done(result)
                elif on_failed is not None:
                    after = on_failed(error)
                else:
                    self.studio.say(error)
        except Exception as e:  # noqa: BLE001 - a callback that broke
            log.exception("job %s: the result could not be used", owner)
            self.studio.say(f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._running.pop(owner, None)
                self._idle.notify_all()
            self.studio.events.publish("job", owner=owner, busy=False, label="")
        if callable(after):
            try:
                with self.studio.lock:
                    after()
            except Exception as e:  # noqa: BLE001
                log.exception("job %s: what was to follow it failed", owner)
                self.studio.say(f"{type(e).__name__}: {e}")

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until no job runs; False if *timeout* ran out first."""
        with self._lock:
            return self._idle.wait_for(lambda: not self._running, timeout)


__all__ = ["Jobs"]
