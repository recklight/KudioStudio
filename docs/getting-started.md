# Getting started

## Installing

```bash
pip install kudiostudio
```

KudioStudio needs Python 3.10 or newer, and kudio 4.1.0 or newer, which has
every measure the [Metrics](panels/metrics.md) panel shows and writes the
`.aif` that Save As offers; pip brings it along. The window is a page the program serves to itself, drawn through
[pywebview](https://pywebview.flowrl.com/) by the web view the system already
has: WebView2 on Windows 10 and 11, WebKit on macOS.
There is no GUI toolkit to install on those two.

Linux needs two things that pip cannot install:

- A web view for pywebview. It draws with GTK or Qt, which it installs as
  extras (`pip install "pywebview[gtk]"`, with the system packages its
  [installation guide](https://pywebview.flowrl.com/guide/installation.html)
  lists). With neither, `kudiostudio` opens the same page in the default
  browser instead, so the program still works, in a browser tab.
- PortAudio's library, for playback and recording:
  `sudo apt install libportaudio2`. Most desktop installs already have it.

### Extras

Everything heavy is an extra, so the base install stays light: no PyTorch
unless you ask for it.

| Extra | What it adds |
|---|---|
| `pip install "kudiostudio[eval]"` | STOI and SDR in the Metrics panel |
| `pip install "kudio[pesq]"` | PESQ (ITU-T P.862), and the CSIG, CBAK and COVL built on it. It builds from source and needs a C compiler, and on Windows the MSVC Build Tools, so I keep it out of `[eval]` and `[all]`, which would otherwise fail to install without one |
| `pip install "kudiostudio[dnsmos]"` | DNSMOS opinion scores. This installs onnxruntime; the model weights are not bundled, and the Metrics panel says where to get them |
| `pip install "kudiostudio[enhance]"` | learned denoising, on PyTorch, through [kudioenhance](https://pypi.org/project/kudioenhance/). On Linux without an NVIDIA GPU, install the CPU-only PyTorch first; kudioenhance's README says how |
| `pip install "kudiostudio[audio]"` | recording until you press Stop, on Linux and macOS. It needs PyAudio, which PyPI has prebuilt for Windows only, so elsewhere pip compiles it: install PortAudio's headers first (`sudo apt install portaudio19-dev`, or `brew install portaudio`). On Windows the plain install already has it |
| `pip install "kudiostudio[all]"` | all of the above except PESQ |

A panel that needs a missing extra says which one, rather than failing.

## Starting it

```bash
kudiostudio                    # an empty session
kudiostudio clip.wav           # open a file on start-up
kudiostudio a.wav b.wav        # several: all go into the file list, the first opens
kudiostudio manifest.json      # a corpus manifest opens in the Manifest panel
kudiostudio --browser          # the same page in the default browser
kudiostudio --no-plugins       # leave out the panels other packages add
kudiostudio --lang zh_TW       # the interface in Traditional Chinese
kudiostudio --reset            # forget the saved settings
```

`--port` picks the local port the page is served on (any free one by
default), `--debug` opens the window's developer tools, and `-v` logs
KudioStudio's and kudio's own messages at debug level, leaving other
libraries at info. `--version` prints the versions of KudioStudio and of the
kudio it runs on.

The page in a browser does what the window does, apart from two things only
the window can do. An open or save dialog becomes a box to type the path into,
and a file dropped on the page is not opened; use **Open** there. The status
line says so if you try.

In `--browser` mode there is no window to close: close the tab, and stop the
program with ++ctrl+c++ in the terminal it was started from. Like closing the
window, that waits for a running job; a second ++ctrl+c++ does not.

### Language

The interface comes in English and Traditional Chinese. **Language / 語言**,
first in **Edit → Settings…** (++ctrl+comma++), switches between them at once,
open panels included, and the choice is remembered. With no choice saved, the system's language is used when there is
a translation for it.

The kudio parameter names, the values kudio itself takes (method names, noise
estimators, codecs, sample formats) and the call captions stay as the code
spells them in both languages, so what you read on screen still matches the
API. A list of KudioStudio's own choices is translated: the Pitch panel's
preset ranges, the Record mode, the reference in Compare and Metrics, and
the like.

### Saved settings

The window remembers the language, the analysis view with its settings, the
folder you last opened or saved something in, the file last open, and
everything in [Settings](window.md#settings). They live in one small JSON
file:

- Windows: `%APPDATA%\Kudio\KudioStudio\window.json`
- elsewhere: `$XDG_CONFIG_HOME/Kudio/KudioStudio/window.json`, or
  `~/.config/Kudio/KudioStudio/window.json` when that variable is not set

`kudiostudio --reset` empties it.

## A first session

1. Open a file: drop it on the window, press **Open** at the top, or use
   **File → Open…** (++ctrl+o++). WAV, FLAC, OGG, MP3 and AIFF all open. Several
   files at once go into **Files** on the left; click one to switch to it.
2. The waveform is on top and the STFT under it, with the whole clip in the
   strip above both. The wheel zooms around the pointer, and ++shift++ + wheel
   scrolls. [The window](window.md) has the rest.
3. Drag across either plot to select a range, and press ++space++. With
   **Selection only** on, which is the default, the selection plays; with
   nothing selected, playback starts at the playhead. Click a plot to move the
   playhead.
4. Pick **Effects** on the rail, choose `highpass`, and press **♪ Preview**:
   the effect loops over the selection, and turning the cutoff renders it
   again. Nothing is changed until **Apply**. The line above the buttons is
   the kudio call Apply will make, and ++ctrl+z++ undoes it.
5. ++ctrl+b++ swaps between your edit and the file as it was opened, while it
   plays if you like, levelled to the same loudness so that neither wins by
   being louder.
6. **File → Save As…** (++ctrl+shift+s++) writes WAV, FLAC or AIFF by the
   name's suffix (a name without one gets `.wav`), 16-bit unless
   [Settings](window.md#settings) says otherwise, and the clip goes by the new
   name from then on, in the **Files** list too. In a browser, a name that is
   already a file asks before it is written over, with Cancel ready for Enter.
   **File → Export edits as Python script…** writes the steps you took as a
   script that makes the same samples from the original file.

[Editing and markers](editing.md) goes further with all of this: the
clipboard, the crossfade at every join, markers, and what the exported script
checks before it runs.

## Working on KudioStudio itself

```bash
git clone https://github.com/recklight/KudioStudio.git
cd KudioStudio
pip install -e ".[dev]"
pytest -q
```

This guide builds with `pip install -e ".[docs]"` and `mkdocs serve`. Its
screenshots come from `python tools/screenshots.py` (and `--lang zh_TW` for
the Chinese ones), which draws the window in a headless Chrome, Chromium or
Edge, so they are regenerated rather than captured by hand.
