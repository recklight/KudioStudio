# Troubleshooting

Each entry is what you see, why it happens, and what to do about it. Messages
are quoted as the window shows them, with `…` where a path, a number or an
error from another library goes. Most of them are in the status bar; the rest
are in the terminal `kudiostudio` was started from, which is worth keeping
open while something is wrong.

## Starting

### The page opens in a browser, not a window

The default browser opens a tab with the window in it, and the terminal says:

```text
no window to draw in (…); opening the browser instead
KudioStudio … on http://127.0.0.1:…/?token=…
Ctrl+C to stop.
```

pywebview found no web view to draw with. Windows and macOS have one built
in; on Linux, pywebview draws through a GUI toolkit that has to be installed
as one of its extras. Rather than no window at all, you get the same page in
the browser, and everything works there except what is listed
[below](#in-a-browser-tab).

To get the window, install the GTK one, `pip install "pywebview[gtk]"`, with
the system packages pywebview's installation guide lists. The Linux part of
[Installing](getting-started.md#installing) links to it.

### A browser tab that does nothing

The page is there, but nothing opens or plays, and the dot at the left of the
status bar is in the warning colour.

Every run serves the page on a port of its own, with a token made for that
run, and the program refuses any request without it. A tab left over from an
earlier run is talking to a server that has gone, and an address typed
without its `?token=…` is turned away by the one that is running.

Use the tab the run opened, or the whole address it printed, token included.
In the window itself, the warning-coloured dot means the program behind the
page has stopped: the terminal says why.

### The port is already in use

`kudiostudio --port …` stops at start-up, before any window, with a traceback
that ends in an `OSError` saying the address is already in use. Another
program holds that port, or another KudioStudio does.

Without `--port` the system picks a free port, so this cannot happen. Leave it
out, or pick another.

### Seeing what went wrong

The status bar has room for one line, and a failure is often more than that.
Start `kudiostudio -v` from a terminal for debug logging: a job that failed
shows its full traceback there, where the status bar showed only its last
line. `--debug` opens the window's developer tools, whose console has the
errors from the page itself, such as a panel module that failed to load. In
a browser tab, the browser's own developer tools do the same.

## In a browser tab

With `--browser`, or when there is no web view, a few things work differently
because there is no window around the page.

Dropping a file on the page says:

```text
Dropping files needs the KudioStudio window; use Open here.
```

A browser does not tell a page where on disk a dropped file is, and the
program opens files by their path. Use **Open** (++ctrl+o++) instead.

The open and save dialogs are the window's own, so in a tab each one becomes a
box asking for a path, with `Path of an audio file on this computer` in it.
It takes one path at a time: to put several files in **Files**, open them one
after another.

**File → Quit** (++ctrl+q++) answers:

```text
Close the browser tab to leave; the server stops with Ctrl+C.
```

Closing the tab leaves the program running; ++ctrl+c++ in its terminal stops
it. Like closing the window, it waits for a running job to finish first, and
says so in the terminal and the status bar:

```text
Stopping when Batch finishes — a running job cannot be stopped half-way. Ctrl+C again stops now, with the job unfinished.
```

A second ++ctrl+c++ stops at once. Only do that when waiting is worse: a
Batch run stopped half-way leaves its output tree half-written.

## Files and settings

### A file that will not open

```text
Could not open clip.m4a — AudioIOError: Could not load audio file: … (… Format not recognised.)
```

Files are read with soundfile, which handles WAV, FLAC, OGG, MP3 and AIFF,
among others, and those are what the open dialog lists. M4A, which is AAC, is
not one of them; chosen under `All files (*.*)` or dropped on the window, it
fails like this. So does a file that is damaged, or named `.wav` without being
one.

Convert it to WAV or FLAC with another program and open that.

### Starting from clean settings

The window remembers the language, the analysis view and its settings, and
the folders you last opened and saved in. They are in one small JSON file:

- Windows: `%APPDATA%\Kudio\KudioStudio\window.json`
- elsewhere: `$XDG_CONFIG_HOME/Kudio/KudioStudio/window.json`, or
  `~/.config/Kudio/KudioStudio/window.json` when that variable is not set

`kudiostudio --reset` empties it, and the window starts as it did the first
time. A file that is not valid JSON is ignored and written again at the next
change. A folder that cannot be written to leaves `settings not saved: …` in
the terminal, and the window goes on without remembering.

## Playback and recording

### Playback fails

Press play, and the status bar says:

```text
Playback failed — DependencyError: 'PyAudio' is required for this feature. Install it with: pip install kudio[audio]
```

Playback goes through sounddevice. When sounddevice cannot play, because
there is no output device or, on Linux, because PortAudio's library is
missing, kudio tries PyAudio next, and on Linux and macOS PyAudio is only
there with the `[audio]` extra. So the message names PyAudio while the trouble
is usually the device. With PyAudio installed and still nothing to play on,
the message ends in `can't be played by pyaudio` instead.

Check that the system has an output device that other programs play through.
On Linux, install PortAudio's library: `sudo apt install libportaudio2`.
Installing the `[audio]` extra changes the message, not the missing device.

### Recording until you stop fails

With **Mode** on `until stopped`, **● Start** says:

```text
Recording failed — DependencyError: 'PyAudio' is required for this feature. Install it with: pip install kudio[audio]
```

and the **Device** list holds a single choice, with
`No input devices found — is kudio[audio] installed?` in the status bar.

An open-ended take runs on `kudio.StreamRecorder`, which reads the microphone
through PyAudio. PyPI has PyAudio built for Windows only, so the plain install
there already has it; on Linux and macOS it comes with the `[audio]` extra,
and pip compiles it against PortAudio's headers. `fixed length` records
through sounddevice and works without it.

```bash
sudo apt install portaudio19-dev     # macOS: brew install portaudio
pip install "kudiostudio[audio]"
```

The compile needs a C compiler as well.

### Nothing was captured

`Nothing was captured — check the input device.` A take stopped with no audio
in it: the device chosen delivered nothing.

The two modes number the devices differently, sounddevice's way for
`fixed length` and PyAudio's for `until stopped`, so the **Device** list
follows the mode, the line under it says which numbering it shows, and each
mode remembers its own choice. Check that the one chosen is the microphone
you meant, and press **Refresh devices** after plugging one in.

## Metrics

### DNSMOS is unavailable

**Predict opinion score (DNSMOS)** fills its line, in the warning colour, with:

```text
FeatureError: DNSMOS model not found. kudio does not ship it — clone
https://github.com/microsoft/DNS-Challenge and pass model_dir='DNS-Challenge/DNSMOS',
or set KUDIO_DNSMOS_DIR to that directory.
```

and the status bar says
`DNSMOS unavailable — see the panel for what is missing.`

The model weights belong to Microsoft's DNS-Challenge and come from there.
The panel has no field for their folder, so it finds them through the
`KUDIO_DNSMOS_DIR` environment variable, which has to be set before
`kudiostudio` starts:

```bash
git clone https://github.com/microsoft/DNS-Challenge
export KUDIO_DNSMOS_DIR="$PWD/DNS-Challenge/DNSMOS"
kudiostudio
```

In PowerShell the second line is
`$env:KUDIO_DNSMOS_DIR = "$PWD\DNS-Challenge\DNSMOS"`.

With the model found and onnxruntime missing, the line says instead:

```text
DependencyError: 'onnxruntime' is required for this feature. Install it with:
pip install kudio[dnsmos]
```

`pip install "kudiostudio[dnsmos]"` installs it.

### PESQ will not install

`pip install "kudio[pesq]"` stops with a compiler error. The `pesq` package is
built from source when it installs, which takes a C compiler: on Windows the
Microsoft C++ Build Tools, on macOS the command line tools
(`xcode-select --install`), on Debian and Ubuntu `build-essential` and
`python3-dev`. Install one and run pip again.

The rest of the Metrics panel does not need it. STOI and SDR come from
`kudiostudio[eval]`, which installs without a compiler.

### Installed, and still not installed

The Metrics panel still lists a back-end as unavailable after `pip install`.
The back-ends are looked for once per run, so restart `kudiostudio`. If that
does not help, pip installed into another Python than the one that runs
`kudiostudio`: install with that one's `python -m pip`.

`not defined at 44100 Hz (PESQ is 8 or 16 kHz)` in the PESQ row is not an
install problem. PESQ is defined at those two rates only, and a clip at any
other rate gets no PESQ.

## Enhance

### Learned models are unavailable

The Enhance panel's note, and the status bar on **Denoise**, say:

```text
Learned models need the kudioenhance package and PyTorch: pip install "kudiostudio[enhance]" installs both
```

Both are looked for, and neither is imported to check. kudioenhance imports
PyTorch only when it loads a model, so one without the other, kudioenhance
installed with `--no-deps` say, would fail at the first denoise; it counts as
missing instead.

`pip install "kudiostudio[enhance]"`. On Linux without an NVIDIA GPU, install
the CPU-only PyTorch first, as [Extras](getting-started.md#extras) says.

### A run directory that will not load

`Select a kudioenhance run directory first.` means no folder is chosen in
`run_dir`, or the one chosen is not there.

A folder that is there but is not a run fails when the model loads:

```text
learned model (exp1) failed — FileNotFoundError: …
```

A run directory is the folder a kudioenhance training run wrote: its
`config.yaml`, the normalisation statistics in `stats.npz`, and the checkpoint,
a `.pt` file. Choose that folder itself, not the one above it. The Compare
panel's **Learned model** field takes the same folder.

A model trained at another sample rate than the clip's fails the same way
when resampling is off:

```text
learned model (exp1) failed — ValueError: this model was trained at 16000 Hz but the clip is 48000 Hz — enable resampling, or convert it in the Batch panel
```

Tick **resample to the model's rate if they differ**, and the clip goes in at
the model's rate and comes back at its own.

## Manifest

### Rows with a file missing

The line under the manifest's path says so, in the warning colour:

```text
12000 row(s) · 12000 with a file missing
```

and hovering a marked cell names the file that is not there:

```text
not on disk: noisy/0001.wav
```

Relative paths in a manifest are relative to the folder the synthesis ran in,
and nothing records which folder that was. The panel looks under the current
folder, the one `kudiostudio` was started in, then the manifest's own folder
and every folder above it, and reads the whole manifest against the first
folder that holds every file, or failing that the one that holds the most.
Once it has found any, it names that folder at the end of the line,
`· relative paths found under …`, and a missing file's tooltip ends in
`, looked for under …`. When neither names a folder, as above, not one
relative path was found under any of them, which usually means none of them
is the right one. Absolute paths are looked up exactly as written, so a
manifest made on another machine with absolute paths finds nothing here.

Start `kudiostudio` in the folder the synthesis ran in and open the manifest
again. When only some rows are missing, the files themselves have gone: tick
**only rows with a file missing** to list them, and press **Reload** once they
are back. **Open** on such a row says `… is not on disk — nothing to open.`

## Plugins

### A plugin was left out

As the window opens, the status bar says, in the warning colour:

```text
Plugin spectra was left out — ModuleNotFoundError: No module named 'mypackage'
```

with one entry per plugin, separated by semicolons. The terminal has the same
as `plugin spectra left out -- …`.

A panel from another package is left off the rail when its module will not
import, when what it names is not a panel, when its icon cannot be
registered, when building it raises, or when its id will not do. The icon
fails when `icon_svg` is not valid SVG, when it comes with no `icon` name, or
when that name is a built-in icon's. The id fails when there is none, when it
is already on the rail, Inspect's `inspect` included, or when it is
`markers`, which the window's own commands use:

```text
Plugin spectra was left out — ValueError: its id 'inspect' is already on the rail
```

The window opens without it either way. The error after the dash says which;
update or reinstall the package that provides it, or uninstall it.
[Writing a panel](plugins.md) has the rules a plugin follows.

### Starting without plugins

When something is wrong and a plugin might be the cause, start without any:

```bash
kudiostudio --no-plugins
```

The `KUDIOSTUDIO_NO_PLUGINS` environment variable, set to anything but an
empty string, does the same for every start where it is set:

```bash
KUDIOSTUDIO_NO_PLUGINS=1 kudiostudio
```

In PowerShell, `$env:KUDIOSTUDIO_NO_PLUGINS = "1"` before `kudiostudio`.
