# -*- coding: utf-8 -*-
"""End-to-end checks across kudio and KudioStudio, from one process.

Not a substitute for either unit suite -- this runs the combinations they do
not: real files on disk, non-default sample rates, minute-long feeds, the
`kudio` command line, and the whole window driving a real worker thread.

Two of this project's worst bugs were invisible to the unit tests and obvious
here: an `AudioConfig` field that stopped being true once `n_fft` changed, and
a windowed overlap-add whose tail divided by a near-zero window sum. Both were
end-to-end failures of things that were individually correct.

    ./.venv/Scripts/python.exe tools/smoke.py

Exits non-zero if any check fails, so it drops into a shell test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# the CLI prints middle dots and arrows; a cp950 console cannot show them
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np                                              # noqa: E402

import kudio                                                    # noqa: E402

CHECKS = {"ok": 0, "fail": 0}


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    CHECKS["ok" if condition else "fail"] += 1
    print(f"  [{mark}] {name}" + (f"   {detail}" if detail else ""))


def voice(sr: int, seconds: float, f0_hz: float, seed: int = 0) -> np.ndarray:
    """Harmonic bursts with pauses, over a noise floor."""
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    t = np.arange(n) / sr
    harmonics = sum(np.sin(2 * np.pi * f0_hz * k * t) / k for k in range(1, 12))
    gate = ((t % 1.0) < 0.6).astype(np.float64)          # 0.6 s on, 0.4 s off
    y = harmonics / 6 * gate + 0.005 * rng.standard_normal(n)
    return (y / np.max(np.abs(y)) * 0.7).astype(np.float32)


# ---------------------------------------------------------------- streaming

def stream_checks(tmp: Path) -> None:
    print("\nSpectrogramStream")
    for sr in (8000, 16000, 44100):
        y = voice(sr, 2.0, 150.0)
        spec = kudio.SpectrogramStream(sr, seconds=10.0)
        whole = spec.push(y)

        piecemeal = kudio.SpectrogramStream(sr, seconds=10.0)
        rng = np.random.default_rng(1)
        i, parts = 0, []
        while i < len(y):                       # ragged blocks, like a device
            step = int(rng.integers(1, 5000))
            got = piecemeal.push(y[i:i + step])
            if len(got):
                parts.append(got)
            i += step
        check(f"sr={sr}: ragged blocks match one push",
              np.array_equal(np.concatenate(parts), whole),
              f"{len(whole)} columns")

    print("\n  against the offline transform")
    sr = 16000
    y = voice(sr, 1.5, 200.0)
    spec = kudio.SpectrogramStream(sr, n_fft=512, hop_length=256, seconds=10.0)
    live = spec.push(y)
    import librosa
    offline = np.abs(librosa.stft(y, n_fft=512, hop_length=256, window='hann',
                                  center=False)).T
    scale = 2.0 / np.sum(np.hanning(513)[:-1])
    want = np.maximum(20 * np.log10(np.maximum(offline * scale, 1e-10)),
                      spec.floor_db)
    err = float(np.max(np.abs(live[:, 1:-1] - want[:, 1:-1])))
    check("matches librosa center=False", err < 0.05, f"max |diff| {err:.4f} dB")

    print("\n  a long take does not drift or grow")
    spec = kudio.SpectrogramStream(16000, seconds=3.0)
    block = voice(16000, 0.1, 180.0)
    for _ in range(600):                                # 60 seconds of audio
        spec.push(block)
    check("window stays one width", spec.columns().shape ==
          (spec.n_columns, spec.n_bins), str(spec.columns().shape))
    check("filled saturates", spec.filled == spec.n_columns)
    want = (600 * len(block) - spec.n_fft) // spec.hop_length + 1
    check("every column of 60 s was computed", spec.frames_seen == want,
          f"{spec.frames_seen} of {want}")
    check("nothing non-finite", bool(np.all(np.isfinite(spec.columns()))))
    check("latest is the right edge",
          np.array_equal(spec.latest(), spec.columns()[-1]))

    print("\n  mel")
    mel = kudio.SpectrogramStream(16000, n_mels=64, fmin=50, fmax=7000)
    cols = mel.push(voice(16000, 1.0, 150.0))
    check("64 rows", cols.shape[1] == 64)
    check("bands ascend", bool(np.all(np.diff(mel.freqs) > 0)))
    check("within fmin..fmax", mel.freqs[0] >= 50 and mel.freqs[-1] <= 7000)


# ------------------------------------------------------------------- pitch

def pitch_checks(tmp: Path) -> None:
    print("\nkudio.f0")
    for sr in (8000, 16000, 44100):
        y = voice(sr, 3.0, 140.0)
        track = kudio.f0(y, sr)
        check(f"sr={sr}: finds 140 Hz",
              abs(track.median_hz - 140.0) < 4.0, f"{track.median_hz:.1f} Hz")
        check(f"sr={sr}: hop is 10 ms", track.hop_length == sr // 100)
        check(f"sr={sr}: times reach the end",
              abs(track.times[-1] - len(y) / sr) < 0.05,
              f"{track.times[-1]:.2f}s of {len(y) / sr:.2f}s")

    print("\n  the pauses are found")
    sr = 16000
    track = kudio.f0(voice(sr, 4.0, 140.0), sr)
    spans = track.voiced_segments()
    check("one span per burst", len(spans) == 4, f"{len(spans)} spans")
    check("spans are ~0.6 s",
          all(0.45 < b - a < 0.75 for a, b in spans),
          ", ".join(f"{b - a:.2f}" for a, b in spans))
    check("voiced ratio near 0.6",
          0.5 < track.voiced_ratio < 0.7, f"{track.voiced_ratio:.2f}")

    print("\n  through a telephone band, the fundamental is gone from the")
    print("  spectrum and still found")
    band = kudio.bandpass(voice(sr, 2.0, 120.0), sr, 300.0, 3400.0)
    check("still 120 Hz", abs(kudio.f0(band, sr).median_hz - 120.0) < 5.0,
          f"{kudio.f0(band, sr).median_hz:.1f} Hz")

    print("\n  labels round-trip through a file")
    path = tmp / "voiced.txt"
    kudio.save_labels(path, track.to_labels())
    back = kudio.load_labels(path)
    check("same number of labels", len(back) == len(spans))
    check("first label lands", abs(back[0].start - spans[0][0]) < 1e-3)


# --------------------------------------------------------------------- CLI

def cli_checks(tmp: Path) -> None:
    print("\nkudio pitch (CLI)")
    clip = tmp / "speech.wav"
    kudio.save_wave(clip, voice(16000, 3.0, 160.0), 16000)
    labels = tmp / "cli.txt"

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "pitch", str(clip),
         "--segments", "--labels", str(labels)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("exit 0", out.returncode == 0, out.stderr.strip()[:120])
    check("prints a median", "median 1" in out.stdout, out.stdout.splitlines()[0])
    check("lists spans", out.stdout.count("s)") >= 3)
    check("wrote labels", labels.exists() and len(kudio.load_labels(labels)) >= 3)

    silence = tmp / "silence.wav"
    kudio.save_wave(silence, np.zeros(16000, dtype=np.float32), 16000)
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "pitch", str(silence)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("silence exits 1", out.returncode == 1, out.stdout.strip())


# ---------------------------------------------------------------- the app

def app_checks(tmp: Path) -> None:
    print("\nKudioStudio, driving the real widgets")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from kudiostudio.app import KudioStudio

    app = QApplication.instance() or QApplication(sys.argv[:1])

    def settle(ms=30):
        app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    clip = tmp / "app.wav"
    kudio.save_wave(clip, voice(16000, 3.0, 160.0), 16000)

    window = KudioStudio(reset=True)
    window.open_files([str(clip)])
    check("clip loaded", window.session.has_audio)

    pitch = next(p for p in window.panels if p.title == "Pitch")
    window.tools.setCurrentWidget(pitch)
    pitch._run()
    for _ in range(400):
        settle(20)
        if not pitch.jobs.busy and pitch._track is not None:
            break
    check("the worker finished", pitch._track is not None)
    check("median is right",
          abs(pitch._track.median_hz - 160.0) < 5.0,
          f"{pitch._track.median_hz:.1f} Hz")
    check("contour is on the spectrogram",
          window.analysis.spectrogram.overlay_visible)

    pitch._add_markers()
    check("markers added", len(window.session.markers) >= 3,
          f"{len(window.session.markers)} markers")

    window.analysis.mode.setCurrentIndex(window.analysis.MODE_MEL)
    check("hidden on the mel axis",
          not window.analysis.spectrogram.overlay_visible)
    window.analysis.mode.setCurrentIndex(window.analysis.MODE_STFT)
    check("back on the STFT axis", window.analysis.spectrogram.overlay_visible)

    # a real edit must drop it: it describes audio that no longer exists
    window.session.set_selection(0.5, 1.0)
    window.session.delete()
    settle()
    check("an edit drops the contour",
          not window.analysis.spectrogram.overlay_visible)
    check("and the panel knows", pitch._track is None)

    print("\n  a take, through the real signal path")
    record = next(p for p in window.panels if p.title == "Record")
    y, sr = kudio.file_load(str(clip), sr=None, mono=True)
    record.monitor_started.emit(sr)
    for i in range(0, len(y), 800):                 # 50 ms at a time
        record.monitor_chunk.emit(y[i:i + 800], sr)
    settle()
    stream = window.analysis._monitor_stream
    check("the view built a stream", stream is not None)
    expected = (len(y) - stream.n_fft) // stream.hop_length + 1
    check("every column computed once", stream.frames_seen == expected,
          f"{stream.frames_seen} vs {expected}")
    # a 3.00 s clip cannot fill a 3.008 s window (the window is a column
    # count, not a time), so what must hold is that nothing was dropped
    check("nothing dropped on the way in",
          stream.filled == min(stream.frames_seen, stream.n_columns),
          f"{stream.filled} of {stream.n_columns} columns held")

    record.monitor_stopped.emit()
    check("stopping releases the stream", window.analysis._monitor_stream is None)

    # committing a take must take the live picture down
    window.analysis.start_monitor(sr)
    window.analysis.feed_monitor(y[:8000], sr)
    window.session.set_audio(y, sr, name="take")
    settle()
    check("committing ends the live view", window.analysis._monitor_sr == 0)

    print("\n  crossfade, per edit")
    window.transport.crossfade.setValue(25.0)
    check("the bar writes the session", window.session.crossfade_ms == 25.0)
    messages = []
    window.session.message.connect(messages.append)
    window.session.set_selection(0.2, 0.4)
    window.session.delete(crossfade_ms=120.0)
    check("one edit can override", "120 ms" in messages[-1], messages[-1])
    check("without moving the default", window.session.crossfade_ms == 25.0)

    window.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        stream_checks(tmp)
        pitch_checks(tmp)
        cli_checks(tmp)
        app_checks(tmp)

    total = CHECKS["ok"] + CHECKS["fail"]
    print(f"\n{CHECKS['ok']}/{total} checks passed, {CHECKS['fail']} failed")
    return 1 if CHECKS["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
