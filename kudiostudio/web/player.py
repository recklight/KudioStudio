# -*- coding: utf-8 -*-
"""Playback for the browser window, through `kudio.play_audio`.

The audio is played here, by Python, and never sent to the page: a long clip
would be hundreds of megabytes there, and sounddevice is what the recording
side uses anyway. Neither backend reports progress, so the playhead is
estimated from a monotonic clock started when the samples were handed over,
as in the Qt window; the page runs the same clock to animate it, from the
start time it is given.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

import kudio

log = logging.getLogger(__name__)


def _sounddevice():
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


@dataclass(frozen=True)
class Playback:
    """One run of the player: what is playing, on the clip's timeline."""

    start: float      # seconds, where the first sample sits
    end: float        # seconds, where playback stops by itself
    clock: float      # time.monotonic() when the samples were handed over


class Player:
    """Plays a waveform and says where the playhead is.

    *on_finished* is called with the end position when playback reaches the
    end by itself (never by `stop`), *on_failed* with a message when the
    backend could not play. Both are called from a timer thread.
    """

    def __init__(self, on_finished: Optional[Callable[[float], None]] = None,
                 on_failed: Optional[Callable[[str], None]] = None):
        self._on_finished = on_finished
        self._on_failed = on_failed
        self._lock = threading.Lock()
        self._current: Optional[Playback] = None
        self._timer: Optional[threading.Timer] = None
        #: where the playhead stopped, when nothing is playing
        self._pos = 0.0

    @property
    def playing(self) -> bool:
        return self._current is not None

    @property
    def position(self) -> float:
        with self._lock:
            return self._position()

    def _position(self) -> float:
        now = self._current
        if now is None:
            return self._pos
        return min(now.start + (time.monotonic() - now.clock), now.end)

    def play(self, y: np.ndarray, sr: int, gain_db: float = 0.0,
             t0: float = 0.0) -> Optional[Playback]:
        """Play *y*, whose first sample sits at *t0* on the timeline.

        Returns what is now playing, or None when nothing could be.
        """
        if y is None or sr <= 0 or len(y) == 0:
            return None
        self.stop()
        out = np.asarray(y, dtype=np.float32)
        if gain_db:
            out = out * float(10 ** (gain_db / 20.0))
        out = np.clip(out, -1.0, 1.0)
        playback = Playback(float(t0), float(t0) + len(out) / float(sr),
                            time.monotonic())
        with self._lock:
            self._current = playback
            self._pos = playback.start
            self._timer = threading.Timer(playback.end - playback.start,
                                          self._finish, args=(playback,))
            self._timer.daemon = True
            self._timer.start()

        if _sounddevice() is not None:
            # non-blocking: the call returns once the samples are queued. When
            # sounddevice has no device, kudio falls back to PyAudio, which
            # raises where it is not installed rather than returning a message
            try:
                err = kudio.play_audio(out, rate=sr)
            except Exception as e:  # noqa: BLE001 - reported, never raised
                err = f"{type(e).__name__}: {e}"
            if err:
                self._fail(playback, str(err))
                return None
            return playback
        # PyAudio blocks until the end, so it gets a thread of its own
        thread = threading.Thread(target=self._blocking, args=(out, sr, playback),
                                  daemon=True)
        thread.start()
        return playback

    def _blocking(self, out: np.ndarray, sr: int, playback: Playback) -> None:
        try:
            err = kudio.play_audio(out, rate=sr)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        if err:
            self._fail(playback, str(err))

    def stop(self) -> float:
        """Stop, and return where the playhead is."""
        with self._lock:
            position = self._position()
            was_playing = self._current is not None
            self._current = None
            self._pos = position
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if was_playing:
            sd = _sounddevice()
            if sd is not None:
                try:
                    sd.stop()
                except Exception as e:  # noqa: BLE001 - nothing was playing
                    log.debug("sounddevice stop: %s", e)
        return position

    def seek(self, seconds: float) -> None:
        """Move the playhead while nothing plays."""
        with self._lock:
            if self._current is None:
                self._pos = max(0.0, float(seconds))

    def _finish(self, playback: Playback) -> None:
        with self._lock:
            if self._current is not playback:   # stopped, or replaced
                return
            self._current = None
            self._pos = playback.end
            self._timer = None
        if self._on_finished is not None:
            self._on_finished(playback.end)

    def _fail(self, playback: Playback, message: str) -> None:
        with self._lock:
            if self._current is playback:
                self._current = None
                self._pos = playback.start
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
        log.warning("playback failed: %s", message)
        if self._on_failed is not None:
            self._on_failed(message)

    def shutdown(self) -> None:
        self.stop()


__all__ = ["Player", "Playback"]
