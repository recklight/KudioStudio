# -*- coding: utf-8 -*-
"""Regenerate every image the README embeds.

The screenshots drift out of date silently -- a caption changes, a control
moves, and nobody notices because nothing fails. Run this after any change to
the window and commit whatever moved::

    QT_QPA_PLATFORM=offscreen python tools/screenshots.py

Offscreen Qt ships no fonts of its own, so on Windows this points the font
database at the system directory; text renders as empty boxes without it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# the README shows the window a clone of this repository builds, not the
# plugins this machine happens to have installed
os.environ["KUDIOSTUDIO_NO_PLUGINS"] = "1"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, str(ROOT))

import numpy as np                                          # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer               # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

import kudio                                                # noqa: E402
from kudiostudio.__main__ import _apply_dark_theme          # noqa: E402
from kudiostudio.app import KudioStudio                     # noqa: E402
from kudiostudio.panels.pitch import CUSTOM_RANGE           # noqa: E402

WINDOW_SIZE = (1600, 860)
SR = 16000


def demo_clip(path: Path) -> Path:
    """2.5 s of something with structure worth looking at: a rising chirp,
    three transients and a noise bed."""
    t = np.arange(int(2.5 * SR), dtype=np.float32) / SR
    chirp = 0.35 * np.sin(2 * np.pi * (300 + 700 * t / t[-1]) * t)
    y = chirp + 0.06 * np.random.default_rng(7).standard_normal(len(t))
    for at in (0.45, 1.45, 2.15):
        i = int(at * SR)
        y[i:i + 200] += np.linspace(0.9, 0.0, 200)
    kudio.save_wave(path, (y / np.max(np.abs(y)) * 0.85).astype(np.float32), SR)
    return path


def drifting_clip(path: Path) -> Path:
    """20 s of speech-like audio whose level wanders, for the loudness shot.

    A steady tone would draw a flat line, which demonstrates nothing; this is
    a take where somebody leaned away from the microphone in the middle and
    came back, which is what the curve exists to find.
    """
    t = np.arange(int(20 * SR), dtype=np.float64) / SR
    syllables = 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)
    # down 13 dB between 7 s and 13 s, easing in and out over half a second
    away = np.clip((t - 7.0) / 0.5, 0, 1) * np.clip((13.0 - t) / 0.5, 0, 1)
    level = 10 ** (-13.0 * away / 20.0)
    voice = sum(np.sin(2 * np.pi * 180 * k * t) / k for k in range(1, 12)) / 6
    y = voice * syllables * level
    y = y / np.max(np.abs(y)) * 0.7
    y += 0.004 * np.random.default_rng(3).standard_normal(len(t))
    kudio.save_wave(path, y.astype(np.float32), SR)
    return path


def settle(app, ms: int = 60) -> None:
    app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def save(app, widget, name: str) -> None:
    settle(app)
    path = IMAGES / f"{name}.png"
    widget.grab().save(str(path))
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} kB")


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("KudioStudio")
    app.setOrganizationName("Kudio")
    _apply_dark_theme(app)

    kudio.play_audio = lambda *a, **k: None     # never make noise from a script

    window = KudioStudio(reset=True)
    window.resize(*WINDOW_SIZE)
    window.show()
    window.open_files([str(demo_clip(IMAGES / "demo.wav"))])
    # the three transients the demo clip plants, so the marker overlay and the
    # list both have something real in them
    window.session.add_marker(0.45, label="transient 1")
    window.session.add_marker(1.45, 1.60, "transient 2")
    window.session.add_marker(2.15, label="transient 3")
    window.session.set_selection(0.8, 1.6)
    settle(app, 150)

    print("window and analysis views")
    save(app, window, "main")
    save(app, window.markers, "dock_markers")
    save(app, window.analysis, "analysis_stft")
    for mode, name in ((window.analysis.MODE_MEL, "analysis_mel"),
                       (window.analysis.MODE_MFCC, "analysis_mfcc")):
        window.analysis.mode.setCurrentIndex(mode)
        save(app, window.analysis, name)
    window.analysis.mode.setCurrentIndex(window.analysis.MODE_STFT)

    print("tool panels")
    for panel in window.panels:
        window.tools.setCurrentWidget(panel)
        if panel.title in ("Compare", "Pitch"):
            continue                    # captured below, with results in them
        save(app, panel, f"panel_{panel.title.lower()}")

    print("pitch")
    pitch = next(p for p in window.panels if p.title == "Pitch")
    window.tools.setCurrentWidget(pitch)
    # The demo clip sweeps 300 -> 1700 Hz. Left on the speech preset the top
    # of that is outside the range, and pYIN answers with the sub-octave --
    # a plausible line in the wrong place, which is exactly what the panel
    # warns about. Naming the real range is the point of the control.
    pitch.range_choice.setCurrentIndex(CUSTOM_RANGE)
    pitch.fmin.setValue(250)
    pitch.fmax.setValue(1800)
    window.session.clear_selection()
    pitch._run()
    for _ in range(600):                                # pYIN runs on a worker
        settle(app, 20)
        if not pitch.jobs.busy and pitch._track is not None:
            break
    else:
        raise SystemExit("the Pitch panel never finished")
    save(app, pitch, "panel_pitch")
    window.session.set_selection(0.8, 1.6)
    save(app, window.analysis, "analysis_pitch")

    print("loudness over time")
    # A 2.5 s clip is shorter than the 3 s short-term window, so it has no
    # curve at all -- and a picture of this feature wants material that
    # actually drifts. Loaded on its own, then put back.
    window.open_files([str(drifting_clip(IMAGES / "drift.wav"))])
    for _ in range(200):            # the readout measures on a worker
        settle(app, 30)
        if window.analysis._loudness is not None:
            break
    else:
        raise SystemExit("the loudness curve never arrived")
    window.analysis.show_loudness.setChecked(True)
    settle(app, 150)
    save(app, window.analysis, "analysis_loudness")
    save(app, window.analysis.levels, "dock_levels")
    window.analysis.show_loudness.setChecked(False)

    (IMAGES / "drift.wav").unlink(missing_ok=True)
    window.open_files([str(demo_clip(IMAGES / "demo.wav"))])
    window.session.set_selection(0.8, 1.6)
    settle(app, 150)

    print("a take in progress")
    # the real path: the Record panel emits arriving audio and the view builds
    # the rolling picture from it. Driving the view directly avoids needing a
    # microphone, and is the same code either way.
    y, sr = window.session.y, window.session.sr
    window.analysis.start_monitor(sr)
    for i in range(0, len(y), 1600):
        window.analysis.feed_monitor(y[i:i + 1600], sr)
    window.analysis._draw_monitor()
    save(app, window.analysis, "analysis_live")
    window.analysis.stop_monitor()
    window.analysis.set_audio(y, sr)
    window.session.set_selection(0.8, 1.6)

    print("comparison")
    compare = next(p for p in window.panels if p.title == "Compare")
    window.tools.setCurrentWidget(compare)
    compare.metrics.setChecked(False)   # the back-ends may not be installed
    compare._run()
    for _ in range(600):                # it runs on a worker
        settle(app, 20)
        if not compare.jobs.busy and compare._results:
            break
    else:
        raise SystemExit("the Compare panel never finished")
    compare.table.selectRow(0)
    save(app, compare, "panel_compare")

    print("A/B pair")
    window.tools.setCurrentIndex(1)                     # Enhance
    enhance = next(p for p in window.panels if p.title == "Enhance")
    enhance.method.setCurrentIndex(0)                   # trad_enhance
    enhance._run()
    for _ in range(300):                                # it runs on a worker
        settle(app, 20)
        if window.session.can_undo():
            break
    else:
        raise SystemExit("the Enhance panel never finished")

    window.session.set_selection(0.8, 1.6)
    window.seek(1.1)
    save(app, window.analysis, "ab_edited")
    window.toggle_compare()
    save(app, window.analysis, "ab_original")

    (IMAGES / "demo.wav").unlink(missing_ok=True)
    window.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
