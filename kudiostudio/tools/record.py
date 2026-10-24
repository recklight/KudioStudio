# -*- coding: utf-8 -*-
"""Capture from an input device -- `kudio.record` and `kudio.StreamRecorder`.

Two ways to record, because they answer different questions. Fixed length
(`kudio.record`, sounddevice) is one blocking call: you say how long up front
and nothing is observable until it returns. Right for a scripted capture,
useless for a person at a microphone. Until stopped (`kudio.StreamRecorder`,
PyAudio) opens the stream and hands frames back as they arrive, so the level
is visible *before* the take is committed -- the only moment at which a
clipped recording can still be prevented rather than diagnosed.

The two backends number devices independently: an index from
`kudio.list_devices` is a sounddevice index and means something else to
PyAudio. So the device list follows the mode, and says which space it shows.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

import kudio

log = logging.getLogger(__name__)

COMMON_RATES = (48000, 44100, 32000, 22050, 16000, 8000)

MODE_FIXED = "fixed length"
MODE_STREAM = "until stopped"
MODES = (MODE_FIXED, MODE_STREAM)

#: Meter floor. Below -60 dBFS the difference between "quiet" and "silent"
#: stops being useful to look at.
METER_FLOOR_DB = -60.0
#: How often the level is read and the newly-arrived audio handed on, ms. The
#: display throttles itself, but the audio must be collected on every tick:
#: `drain()` hands each sample over once, and skipping a tick would lose it.
METER_INTERVAL_MS = 50

MONITOR_TIP = ("Denoise the rolling display as it arrives (kudio.StreamEnhancer).\n"
               "Monitoring only — the take is committed exactly as recorded.\n"
               "Only the per-frame methods can stream; the rest need the whole\n"
               "spectrogram and are in the Enhance panel.")
TAKE_NOTE = ("The recording replaces the current audio and becomes the new baseline for "
             "Undo and Metrics. Needs kudio[audio]. Every take is checked for clipping "
             "and level as soon as it lands — a clipped take is best redone, since "
             "nothing downstream can undo it.")
MONITOR_OFF = "off — show what the microphone sends"


def rms_db(block: np.ndarray, previous: float, smoothing: float = 0.7) -> float:
    """Smoothed RMS level in dBFS. A per-block reading alone jitters too much
    to read, and the number it feeds is a difference of two of them."""
    if block.size == 0:
        return previous
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    now = 20.0 * np.log10(rms) if rms > 0 else float("-inf")
    if not np.isfinite(previous) or not np.isfinite(now):
        return now
    return smoothing * previous + (1.0 - smoothing) * now


def input_devices() -> List[Tuple[str, Optional[int]]]:
    """sounddevice input devices -- the index space `kudio.record` accepts."""
    devices: List[Tuple[str, Optional[int]]] = [("System default", None)]
    try:
        for d in kudio.list_devices("input"):
            devices.append((f"[{d['index']}] {d['name']}", d["index"]))
    except Exception as e:  # noqa: BLE001 - no audio backend on this machine
        log.warning("device enumeration failed: %s", e)
    return devices


def stream_devices() -> List[Tuple[str, Optional[int]]]:
    """PyAudio input devices -- the index space `StreamRecorder` accepts."""
    devices: List[Tuple[str, Optional[int]]] = [("System default", None)]
    try:
        with kudio.CheckDevice() as check:
            for d in check.system_devices():
                if d.get("in", 0):
                    devices.append((f"[{d['index']}] {d['Device']}", d["index"]))
    except Exception as e:  # noqa: BLE001
        log.warning("PyAudio device enumeration failed: %s", e)
    return devices


def device_space(streaming: bool) -> str:
    return ("PyAudio device indices (CheckDevice)" if streaming
            else "sounddevice device indices (kudio.list_devices)")


def call_text(streaming: bool, seconds: float, sr: int, channels: int,
              device: Optional[int]) -> str:
    if streaming:
        return (f"kudio.StreamRecorder(device={device}, sr={sr}, "
                f"channels={channels}).start() … .stop()")
    return f"kudio.record(seconds={seconds}, sr={sr}, channels={channels}, device={device})"


def mono(y: np.ndarray) -> np.ndarray:
    """A take as the session holds it: float32, channels mixed down."""
    y = np.asarray(y, dtype=np.float32)
    return y.mean(axis=1) if y.ndim > 1 else y


def check_take(y: np.ndarray, sr: int) -> Optional[Tuple[str, List[str]]]:
    """``(summary, problems)`` for a take that has just landed, or None when
    it cannot be measured -- a check must never break a take.

    Said now, not after the session ends: a clipped take cannot be repaired,
    and the only fix, recording it again, is only possible while the
    microphone is still set up.
    """
    try:
        report = kudio.audio_report(y, sr)
    except Exception as e:  # noqa: BLE001
        log.debug("take report failed: %s", e)
        return None
    summary = (f"peak {report.peak_dbfs:+.1f} dBFS"
               if report.peak_dbfs != float("-inf") else "silent")
    if np.isfinite(report.lufs):
        summary += f"  ·  {report.lufs:.1f} LUFS"
    return summary, list(report.problems())


def take_text(summary: str, problems: List[str]) -> str:
    if problems:
        return summary + "\n· " + "\n· ".join(problems)
    return summary + "  ·  take looks clean"


def monitor_text(label: str, raw_db: float, clean_db: float, latency_s: float) -> str:
    """How much the live denoiser is taking off, and how far behind it is."""
    return (f"{label} — removing {raw_db - clean_db:.1f} dB right now  ·  "
            f"{latency_s * 1000:.0f} ms behind")


__all__ = ["COMMON_RATES", "METER_FLOOR_DB", "METER_INTERVAL_MS", "MODES", "MODE_FIXED",
           "MODE_STREAM", "MONITOR_OFF", "MONITOR_TIP", "TAKE_NOTE", "call_text",
           "check_take", "device_space", "input_devices", "mono", "monitor_text",
           "rms_db", "stream_devices", "take_text"]
