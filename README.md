# KudioStudio

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Qt](https://img.shields.io/badge/GUI-PySide6-41cd52.svg)](https://doc.qt.io/qtforpython/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.txt)
[![kudio](https://img.shields.io/pypi/v/kudio.svg?label=kudio)](https://pypi.org/project/kudio/)

**A desktop workbench for the [`kudio`](https://pypi.org/project/kudio/) audio toolkit.**

Open a clip, look at it, change it, measure it, and build datasets from it —
without writing a script first. Every control maps to one documented `kudio`
function, and the caption under each panel shows the exact call it will make,
so anything you work out here translates straight into code.

![KudioStudio](docs/images/main.png)

## Install

```bash
pip install kudiostudio
kudiostudio
```

From a checkout:

```bash
pip install -e ".[dev]"
python -m kudiostudio
```

Requires **Python 3.9+**. Everything heavy is an opt-in extra:

| Extra | Adds |
|---|---|
| `kudiostudio[theme]` | dark theme (QDarkStyle) |
| `kudiostudio[eval]` | PESQ / STOI / SDR metrics |
| `kudiostudio[enhance]` | learned denoising — pulls in TensorFlow |
| `kudiostudio[all]` | all of the above |

The base install stays light: no TensorFlow unless you ask for it.

```bash
kudiostudio                    # empty session
kudiostudio clip.wav           # open a file on start-up
kudiostudio --no-theme         # native Qt style
kudiostudio --reset            # forget saved window geometry
```

## What each panel does

| Panel | `kudio` behind it | What you get |
|---|---|---|
| **Analysis** (centre) | `waveform_to_spectrogram`, `mfcc` | Waveform + log-power STFT or MFCC, x-linked, live `n_fft` / `hop_length` / `n_mfcc` |
| **Effects** | `kudio.effects` | `gain`, `random_gain`, `pitch_shift`, `time_stretch`, `reverb`, `add_noise_snr`, `trim_silence`, `split_on_silence` |
| **Enhance** | `kudio.enhance` + `kudio-enhance` | `trad_enhance` (spectral subtraction, MCRA), `wavelet_low_pass_filter`, and **learned models** from a [kudio-enhance](https://github.com/recklight/KudioEnhance) run |
| **Record** | `kudio.record` | Device picker, sample rate, channels, duration |
| **Synthesize** | `kudio.Synthesizer` | clean × noise × SNR dataset generation, seeded and reproducible |
| **Metrics** | `kudio.core.evaluator` | SNR / SI-SDR / segmental SNR always; PESQ / STOI / SDR when installed |
| **Batch** | `check_input` + `file_load` + `save_wave` | Folder-wide resample, bit depth, mono downmix, trim, normalize |

Edits apply to the **selected range** when there is one, otherwise to the whole
clip — and every edit is undoable. `Revert to original` drops the lot.

<img src="docs/images/panel_effects.png" width="330" alt="Effects panel">
<img src="docs/images/panel_synthesize.png" width="330" alt="Synthesize panel">

### Analysis views

STFT log-power spectrogram and MFCC, from the same `kudio` calls a training
script would use:

<img src="docs/images/analysis_stft.png" width="420" alt="STFT view">
<img src="docs/images/analysis_mfcc.png" width="420" alt="MFCC view">

### Learned denoising

The Enhance panel runs classic DSP out of the box. With
`pip install "kudiostudio[enhance]"` it also loads models trained by
[kudio-enhance](https://github.com/recklight/KudioEnhance): point `run_dir` at
a run directory and it picks up the model, its normalisation statistics and the
STFT geometry they were trained with — so inference cannot silently disagree
with training.

<img src="docs/images/panel_enhance.png" width="330" alt="Enhance panel with a learned model selected">

TensorFlow is imported only when that method is actually used, and the panel
tells you how to install it rather than failing if it is missing.

### Dataset synthesis

Point it at a folder of clean audio and a folder of noise, pick your SNRs, and
it writes the mixture. `regular` gives one output per clean file; `inc` writes
every clean × noise × SNR combination. Set a seed and the result is byte-for-byte
reproducible — the same noise offsets, the same pairings.

### Objective metrics

SNR, SI-SDR and segmental SNR are pure numpy and always available. PESQ, STOI
and BSS-SDR need the optional back-ends:

```bash
pip install "kudio[eval]"
```

The panel reports which ones are present rather than failing.

## Shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` / `Ctrl+Shift+S` | Open / Save As |
| `Ctrl+Z` | Undo the last edit |
| `Ctrl+A` / `Esc` | Select all / clear selection |

Drag on the waveform to select. Both plots share the time axis, so zooming one
zooms the other.

## Layout

```
kudiostudio/
├── __main__.py       # CLI entry point, theme, argument parsing
├── app.py            # main window: docks, menus, wiring
├── session.py        # AudioSession — current audio, undo stack, selection
├── workers.py        # QThread helpers, so long kudio calls never block the UI
├── theme.py          # shared colours
├── widgets/
│   ├── views.py      # waveform + spectrogram/MFCC (pyqtgraph)
│   └── transport.py  # play / stop / gain
└── panels/
    ├── base.py       # Panel base class
    ├── browse.py     # file list
    ├── effects.py    ├── enhance.py   ├── record.py
    └── synth.py      ├── metrics.py   └── batch.py
```

Panels never talk to each other. They read and mutate the shared
`AudioSession`; the views repaint on its `changed` signal. Adding a panel means
subclassing `Panel` and appending it to `PANEL_CLASSES`.

The UI is built in Python — there are no `.ui` files and no `uic` step, so what
you read is what runs.

## Tests

```bash
pytest -q
```

33 tests covering the session's undo/selection arithmetic, each panel's
behaviour, and the main window's wiring. Qt runs headless (`offscreen`), so the
suite works in CI. The learned-denoising tests train a one-epoch model for real
and skip cleanly when the `enhance` extra is not installed.

## Related

- **[kudio](https://pypi.org/project/kudio/)** — the audio toolkit underneath: I/O,
  features, effects, synthesis, enhancement, metrics.
- **[kudio-enhance](https://github.com/recklight/KudioEnhance)** — train the
  denoising models this app loads.

## Licence

MIT — see [LICENSE.txt](LICENSE.txt).
