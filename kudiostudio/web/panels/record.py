# -*- coding: utf-8 -*-
"""The Record panel: ``record.*`` on the wire.

A fixed-length take is one job. An open-ended one keeps the device open and
a ticker thread reading it every 50 ms (`tools.METER_INTERVAL_MS`): the level
and the time go to the page, and the audio that has arrived goes to the live
monitor -- through a streaming denoiser first, when one is chosen, which
changes only what is shown, never what is recorded.
"""
from __future__ import annotations

import logging
import threading
from functools import partial
from typing import Dict, Optional

import numpy as np

import kudio
from kudiostudio import i18n
from kudiostudio.tools import record as tools
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel

log = logging.getLogger(__name__)


class RecordPanel(WebPanel):
    id = "record"
    title = "Record"
    icon = "record"
    script = "js/panels/record.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        #: the open take, while one is running
        self._recorder = None
        self._enhancer = None
        self._method_label = ""
        self._ticker: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._raw_db = self._clean_db = float("-inf")
        #: (text, tone) under the buttons: the last take's check
        self._take = ("no take yet", "hint")
        self._monitor = ("", "hint")

    @property
    def streaming(self) -> bool:
        return self._recorder is not None

    def info(self) -> Dict:
        return {"busy": self.busy, "streaming": self.streaming,
                "take": [self._take_text(), self._take[1]], "monitor": list(self._monitor)}

    def _take_text(self) -> str:
        """The take's check in the window's language. Translated piece by
        piece, as the table keeps it: the summary's parts, then either the
        problems or the all-clear."""
        text, _ = self._take
        if not isinstance(text, tuple):
            return i18n.translate(text, self.studio.language)
        summary, problems = text
        say = partial(i18n.translate, language=self.studio.language)
        head = "  ·  ".join(say(part) for part in summary.split("  ·  "))
        if problems:
            return head + "\n· " + "\n· ".join(say(p) for p in problems)
        return head + say("  ·  take looks clean")

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **self.info(),
                "modes": list(tools.MODES), "rates": list(tools.COMMON_RATES),
                "monitor_methods": [{"name": "", "label": tools.MONITOR_OFF, "tip": ""}] + [
                    {"name": n, "label": kudio.METHODS[n].label,
                     "tip": kudio.METHODS[n].summary} for n in kudio.STREAMABLE_METHODS],
                "monitor_tip": tools.MONITOR_TIP, "note": tools.TAKE_NOTE,
                "meter_floor": tools.METER_FLOOR_DB}

    @command
    def devices(self, streaming: bool) -> Dict:
        found = tools.stream_devices() if streaming else tools.input_devices()
        if len(found) <= 1:
            self.report("No input devices found — is kudio[audio] installed?")
        return {"devices": [[label, index] for label, index in found],
                "space": tools.device_space(bool(streaming))}

    @command
    def call_text(self, streaming: bool, seconds: float, sr: int, channels: int,
                  device: Optional[int] = None) -> str:
        return tools.call_text(bool(streaming), float(seconds), int(sr), int(channels), device)

    # ------------------------------------------------------------------ run

    @command
    def record(self, mode: str, seconds: float = 3.0, sr: int = 16000, channels: int = 1,
               device: Optional[int] = None, monitor: str = "") -> Dict:
        """Fixed length: record and commit. Until stopped: start, or stop
        the take already running -- the same button."""
        sr, channels = int(sr), int(channels)
        if mode == tools.MODE_STREAM:
            if self._recorder is not None:
                self._stop_stream()
            else:
                self._start_stream(sr, channels, device, monitor)
            return self.info()
        seconds = float(seconds)
        self.report(f"Recording {seconds:g}s at {sr} Hz …")
        if not self.start_job("Recording…", kudio.record, seconds=seconds, sr=sr,
                              channels=channels, device=device,
                              on_done=lambda y: self._finished(y, sr),
                              on_failed=self._failed):
            self.report("A recording is already in progress.")
        return self.info()

    def _start_stream(self, sr: int, channels: int, device, monitor: str) -> None:
        try:
            self._recorder = kudio.StreamRecorder(device=device, sr=sr,
                                                  channels=channels).start()
        except Exception as e:  # noqa: BLE001
            self._recorder = None
            self._failed(f"{type(e).__name__}: {e}")
            return
        self._enhancer = None
        if monitor:
            try:
                self._enhancer = kudio.StreamEnhancer(self._recorder.sr, monitor)
                self._method_label = kudio.METHODS[monitor].label
            except Exception as e:  # noqa: BLE001
                self.report(f"Live monitoring unavailable — {type(e).__name__}: {e}")
        self._raw_db = self._clean_db = float("-inf")
        self._monitor = ("", "hint")
        self.studio.monitor.start(self._recorder.sr)
        self._stopping.clear()
        self._ticker = threading.Thread(target=self._tick_loop, name="record-meter",
                                        daemon=True)
        self._ticker.start()
        self.report(f"Recording at {self._recorder.sr} Hz — press Stop when done.")
        self.publish(info=self.info())

    def _tick_loop(self) -> None:
        while not self._stopping.wait(tools.METER_INTERVAL_MS / 1000.0):
            with self.studio.lock:
                if self._recorder is None:
                    return
                self.tick()

    def tick(self) -> None:
        """One reading: the level, the time, and the audio that arrived."""
        recorder = self._recorder
        if recorder is None:
            return
        level = recorder.level_db()
        self.publish(meter=max(tools.METER_FLOOR_DB, min(0.0, level))
                     if np.isfinite(level) else tools.METER_FLOOR_DB,
                     elapsed=recorder.elapsed())
        # Exactly one drain() per tick. It hands each sample over once and
        # never again, so a second caller in the same tick would take half the
        # audio away from the first -- the denoiser and the display are fed
        # from the same call, not from two
        new = recorder.drain()
        if new.size:
            new = tools.mono(new)
            if self._enhancer is not None:
                new = self._through_monitor(new)
            if new.size:
                self.studio.monitor.push(new, recorder.sr)
        if recorder.capped:
            self.report("Reached the recorder's length cap — stopping.")
            self._stop_stream()

    def _through_monitor(self, new: np.ndarray) -> np.ndarray:
        """Denoise a block on its way to the display, and say how much came
        off. Returns the block unchanged if the denoiser gave up: a monitor
        that stops updating looks like a dead microphone, which is a worse
        thing to believe than "the denoising stopped"."""
        try:
            clean = self._enhancer.process(new)
        except Exception as e:  # noqa: BLE001
            log.warning("live monitoring failed: %s", e)
            self._enhancer = None
            self._monitor = ("live monitoring stopped — see the log", "warn")
            self.publish(info=self.info())
            return new
        self._raw_db = tools.rms_db(new, self._raw_db)
        if clean.size:
            self._clean_db = tools.rms_db(clean, self._clean_db)
        if np.isfinite(self._raw_db) and np.isfinite(self._clean_db):
            text = tools.monitor_text(self._method_label, self._raw_db, self._clean_db,
                                      self._enhancer.latency_seconds)
            self.publish(monitor_text=text)
        return clean

    def _stop_stream(self) -> None:
        recorder, self._recorder = self._recorder, None
        self._enhancer = None
        self._stopping.set()
        self.studio.monitor.stop()
        self.publish(info=self.info(), meter=tools.METER_FLOOR_DB, elapsed=0.0)
        if recorder is None:
            return
        try:
            y = recorder.stop()
        except Exception as e:  # noqa: BLE001
            self._failed(f"{type(e).__name__}: {e}")
            return
        if y.size == 0:
            self.report("Nothing was captured — check the input device.")
            return
        self._finished(y, recorder.sr)

    def _finished(self, y: np.ndarray, sr: int) -> None:
        y = tools.mono(y)                   # keep the session mono
        self.session.set_audio(y, sr, name="recording")
        self.report(f"Recorded {len(y) / sr:.2f}s at {sr} Hz.")
        checked = tools.check_take(y, sr)
        if checked is None:                 # never let a check break a take
            self._take = ("", "hint")
        else:
            summary, problems = checked
            self._take = ((summary, problems), "warn" if problems else "ok")
            if problems:
                self.report(f"Recorded, but: {problems[0]}")
        self.publish(info=self.info())

    def _failed(self, message: str) -> None:
        self.report(f"Recording failed — {message}")
        self.publish(info=self.info())

    def shutdown(self) -> None:
        """Close the device if the window shuts while a take is running."""
        self._enhancer = None
        self._stopping.set()
        self.studio.monitor.stop()
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            try:
                recorder.stop()
            except Exception as e:  # pragma: no cover - teardown only
                log.debug("recorder shutdown: %s", e)


__all__ = ["RecordPanel"]
