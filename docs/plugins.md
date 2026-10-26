# Writing a panel

A tool that has no place in KudioStudio itself can live in a package of your
own and still sit on the rail, below a rule under the built-in tools. It is
built the way every panel here is, in two halves: a Python class whose
commands the page calls, and a page module that draws the controls and calls
them. Both lean on the same pieces the built-in panels use, so a panel from
another package looks and behaves like one of mine.

This page builds a small one from start to finish, **Window RMS**: the level
of the selection, or of the whole clip, measured window by window.

## How the window finds a panel

A package lists its panel under the `kudiostudio.panels` entry point group,
as `name = "module:Class"`. When the window starts, it imports each one in the
order of those names, takes its icon and its translations on board, and then
builds it as `Class(studio)`, after the built-in panels. Plugin panels come
last on the rail, in the same order.

A panel is left out, and the window opens without it, when:

- its module fails to import;
- the entry point names something that is not a `WebPanel` subclass;
- its `icon_svg` does not parse as SVG, comes with no `icon` name, or goes
  under a built-in icon's name;
- its constructor raises;
- it has no `id`, or one already on the rail: another plugin's, or a
  built-in tool's, `inspect` included;
- its `id` is `markers`, the name the window's own marker commands go by.

The status bar names each one as the window opens, in the warning colour,
with the error:

```text
Plugin rms was left out — ModuleNotFoundError: No module named 'rms_panel'
```

**Help → About** lists the plugins that did load. `kudiostudio --no-plugins`,
or the `KUDIOSTUDIO_NO_PLUGINS` environment variable set to anything but an
empty string, starts the window with none at all, which is the first thing to
try when one of them is the trouble.

## The example

```text
rms-panel/
├── pyproject.toml
└── src/rms_panel/
    ├── __init__.py       (empty)
    ├── panel.py          the Python half
    └── static/
        └── rms.js        the page half
```

### pyproject.toml

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "rms-panel"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["kudiostudio>=2.0", "numpy"]

[project.entry-points."kudiostudio.panels"]
rms = "rms_panel.panel:RmsPanel"

# the page module is not Python, so the wheel only carries it if asked to
[tool.setuptools.package-data]
rms_panel = ["static/*.js"]
```

The `package-data` line matters more than it looks. Without it a wheel of this
package holds `panel.py` and no `rms.js`, the class loads, the rail shows the
tool, and choosing it shows the panel's title and the web view's import error
where the controls should be.

### The Python half

```python
from pathlib import Path

import numpy as np

from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


def rms_per_window(y, sr, seconds, offset=0.0):
    """[start, level] for each window of *seconds*, as the table shows them."""
    step = max(1, int(round(seconds * sr)))
    rows = []
    for start in range(0, len(y), step):
        part = np.asarray(y[start:start + step], dtype=np.float64)
        rms = float(np.sqrt(np.mean(part ** 2)))
        level = f"{20 * np.log10(rms):+.1f} dBFS" if rms > 0 else "digital silence"
        rows.append([f"{offset + start / sr:.2f} s", level])
    return rows


class RmsPanel(WebPanel):
    id = "rms"
    title = "Window RMS"
    icon = "rms"
    icon_svg = ('<path d="M4 18v-6M8 18V7M12 18v-4M16 18V9M20 18v-6"/>'
                '<path d="M3 10.5h18" stroke-dasharray="1.5 2.5"/>')
    static_dir = Path(__file__).parent / "static"
    script = "rms.js"
    messages = {"zh_TW": {
        "Window RMS": "分段 RMS",
        "window": "視窗長度",
        "Measure": "量測",
        "Start": "起點",
        "Level": "音量",
        "digital silence": "數位靜音",
        "Measuring RMS …": "正在量測 RMS…",
        "Measured {} window(s).": "量測了 {} 個視窗。",
        "The audio changed while it was measured; measure again.":
            "量測期間音訊改變了，請重新量測。",
    }}

    def __init__(self, studio):
        super().__init__(studio)
        self._rows = []

    @command
    def describe(self):
        return {**super().describe(), "rows": self._rows}

    @command
    def measure(self, seconds=1.0):
        if not self.require_audio():
            return {"rows": self._rows}
        y, sr = self.session.selected_audio(), self.session.sr
        selection = self.session.selection()
        ran_on = self.session.y

        def done(rows):
            # the job ran off the lock; the clip may have been edited since
            if self.session.y is not ran_on:
                self.report("The audio changed while it was measured; measure again.")
                return
            self._rows = rows
            self.publish(rows=rows)
            self.report(f"Measured {len(rows)} window(s).")

        self.start_job("Measuring RMS …", rms_per_window, y, sr, float(seconds),
                       selection[0] if selection else 0.0, on_done=done)
        return {"rows": self._rows}

    def on_session_changed(self):
        self._rows = []
        self.publish(rows=[])
```

The class attributes are what the window reads before it builds anything:

| Attribute | What it is |
|---|---|
| `id` | the panel's key: its commands are `rms.describe` and `rms.measure` on the wire. Pick one of your own; the built-in ids, `inspect` among them, are taken, and so is `markers`, which the window's own commands use |
| `title` | its label on the rail, in English; the page translates it |
| `icon` | the name its rail icon goes by |
| `icon_svg` | the icon itself; [below](#icon-and-translations) |
| `static_dir` | the folder that holds the page module. The window serves it at `/plugins/<id>/`, and nothing above it |
| `script` | the page module, relative to `static_dir` |
| `messages` | its translations; [below](#icon-and-translations) |

#### Commands

A method marked with `@command` is callable from the page as `<id>.<name>`,
and nothing else is. `@command(name="...")` exposes it under another name.
The arguments arrive as JSON, and what a command returns goes back as JSON:
dicts, lists, strings and numbers, with numpy scalars, numpy arrays and
`Path`s converted on the way. A command that raises shows its error in the
status bar, in the warning colour, and the window carries on.

`describe` is a command every panel has. The base class returns the `id` and
whether the panel is busy; a panel adds whatever its page needs to draw
itself, here the rows of the last measurement.

The helpers a panel gets from `WebPanel`:

| Helper | What it does |
|---|---|
| `self.session` | the clip: `y`, `sr`, `selection()` in seconds or `None`, `selected_audio()`, the selection or the whole clip |
| `self.report(text)` | a line in the status bar, translated on the way |
| `self.require_audio()` | False, with `Load an audio file first.` in the status bar, when nothing is open |
| `self.publish(**data)` | an event for this panel's page module, which its `event` hook receives |
| `self.start_job(label, fn, *args, on_done=, on_failed=, **kwargs)` | `fn` on a thread of its own; False while this panel already has a job running |
| `self.busy` | whether this panel has a job running |

#### Jobs

Anything that takes longer than a moment goes on a job, or the window stops
answering until it is done. `fn` runs off the window's lock, so it must not
touch the session: hand it the audio it needs, as `measure` hands over `y`.
`on_done` gets the result and `on_failed` the error as one line, and both run
under the lock, where the session is safe to read and change. Without
`on_failed`, the error goes to the status bar.

While a job runs, the dot at the left of the status bar lights up with its
label. Each panel has one job at a time. A job cannot be stopped half-way,
since a kudio call has no point to stop it at, so quitting waits for it. For
a long one, `self.studio.jobs.progress(self.id, text)` sends a line of how far
it has got to the page module's `progress` hook.

The audio can change while a job runs. `done` above compares the array it
measured with the one the session holds, and edits replace the array rather
than writing into it, so a different object means different audio.

#### Hooks

`on_session_changed()`
:   the clip changed: an edit, an undo, another file. Anything measured
    before it describes audio that is not on screen any more, so the example
    drops its rows.

`cancel_preview()`
:   something else took over playback. For a panel that auditions audio with
    `self.studio.start_preview(self.id, y, sr)`, which loops it until then.

`shutdown()`
:   the window is closing; let go of any device the panel holds open.

#### Changing the audio

A panel that edits the clip calls `set_audio` from a command or from
`on_done`, under the lock:

```python
self.session.set_audio(out, sr, undo_label="halve", keep_original=True,
                       code="y = y * 0.5")
```

`undo_label` puts the step on the undo list under that name, and
`keep_original=True` leaves the file as it was opened in place for A/B and
**Revert to original**. `code` is the step as Python, for the
[exported script](editing.md#your-edits-as-a-script): it runs with `y` and
`sr` bound and `np` and `kudio` imported, and leaves its result in `y`. An
edit without it can still be undone, but the exported script stops at that
step with a `raise` rather than write a file without it.

### The page half

```js
// Window RMS: the level of the selection, or of the whole clip, window by window.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  ctx.memory.seconds ??= 1.0;

  const box = ui.section("Window RMS");
  const form = ui.form();
  const seconds = ui.number({ value: ctx.memory.seconds, min: 0.05, max: 60, step: 0.05,
                              decimals: 2, suffix: " s",
                              onChange: (v) => { ctx.memory.seconds = v; } });
  form.row("window", seconds);
  const measure = ui.button("Measure", {
    primary: true,
    onClick: () => ctx.call("measure", [seconds.value]),
  });
  const table = ui.table(["Start", "Level"], { numeric: [1] });
  box.body.append(form.el, ui.buttons(measure));
  root.append(box.el, table);

  const show = (rows) => table.setRows(rows.map(([start, level]) => [start, t(level)]));
  show(info.rows);

  return {
    state(s) { measure.disabled = !s.has_audio || s.busy.includes(ctx.id); },
    event(e) { if (e.rows) show(e.rows); },
    busy(on) { measure.disabled = on; },
  };
}
```

The module exports `mount(root, ctx)`. The window calls it the first time the
tool is chosen, with an empty box to build into, and again whenever the
language changes, so the panel is drawn afresh in the other one. It imports
nothing: everything it needs comes in `ctx`, and since it is loaded from
`/plugins/<id>/`, a relative import would look in your own folder anyway.

| `ctx.` | What it is |
|---|---|
| `call(name, args, kwargs)` | runs this panel's command `name` and resolves to what it returned |
| `window(name, args, kwargs)` | runs one of the window's own commands, such as `set_selection` with `[1.0, 2.5]` |
| `ui` | the controls, below |
| `t(text)` | the English *text* in the window's language |
| `memory` | an object the module keeps what it likes in. It outlives a change of language and a switch to another tool, not the window |
| `state()` | the window's latest state |
| `status(text, warn)` | a line in the status bar from the page itself |
| `view` | a layer over the plots, below |
| `dialogs` | the window's prompts: `ask`, `askNumber` and `choosePath` |
| `id` | the panel's `id` |

`mount` returns the hooks it wants called, any of:

| Hook | Called with |
|---|---|
| `state(s)` | the window's state, whenever it changes: `has_audio`, `sr`, `duration`, `selection`, `playing`, `busy` (the ids of the panels with a job running), `language` and more |
| `event(e)` | whatever this panel's Python half published |
| `busy(on, label)` | this panel's job starting and ending |
| `progress(text)` | a line from `jobs.progress` |
| `preview(on)` | `false` when this panel's audition has ended because something else took playback |

#### The controls

The built-in panels are made of these and nothing else, which is what keeps
them looking like one window. Every label and tooltip is given in English and
goes through `t`.

| `ui.` | What it makes |
|---|---|
| `section(title, { tip })` | a titled box: `.el`, `.body` to fill, `.setBusy(label)` |
| `form()` | rows of label and control: `.el`, `.row(label, control, { tip, wide })` |
| `number({ value, min, max, step, decimals, suffix, special, onChange })` | a number box. `special` is shown in place of the unit at the minimum, as a seed of −1 shows `random` |
| `text({ value, placeholder, onChange })` | a line of text |
| `select(options, { value, translate, onChange })` | a drop-down of strings or `[value, label]` pairs |
| `segmented(options, { value, onChange })` | a row of buttons, one chosen, like STFT / Mel / MFCC |
| `check(text, { checked, onChange })` | a check box; `.value` |
| `path({ kind, title, types, placeholder, onChange })` | a path with **Browse…**, through the window's own dialog. `kind` is `open`, `open_many`, `save` or `folder` |
| `button(text, { primary, toggle, tip, onClick })` | a button |
| `buttons(...items)` | a row of them |
| `callText()` | the kudio call in the call colour; set `.text` |
| `hint(text)` | a note in small print |
| `readout(text)` | a line of result; `.show(text, tone)` with `"warn"` or `"ok"` |
| `table(columns, { numeric })` | a table; `.setRows(rows, classes)` |
| `params(list, { onChange })` | a whole form from a list of parameter descriptions, as the Effects panel builds its controls |

#### A view over the plots

Results that need more room than the panel column, a set of plots or a wide
table, can go over the stage: `ctx.view.show(title, node, { onClose })` puts
`node` there, `ctx.view.hide()` takes it away, and `ctx.view.open` says
whether this panel holds it. One panel holds the view at a time, and it stays
when another tool is chosen. Its close button gives the plots back and calls
`onClose`.

### Icon and translations

`icon_svg` is the body of an SVG on the same 24-unit grid as the built-in
icons, drawn with a 2-unit round stroke in the text colour. An element with
`fill="currentColor"` is solid instead. The built-in Metrics icon, for
comparison, is `<path d="M4 20h16M7 16.5v-4M12 16.5V6M17 16.5v-7"/>`. The body
has to parse as SVG, and a built-in icon's name is refused, so a plugin cannot
redraw one of mine. Either problem leaves the panel out, and so does an
`icon_svg` with no `icon` name to register it under. Leave `icon_svg` out to
use a built-in icon by its name.

`messages` maps a language to a table of translations, keyed by the English
exactly as the code writes it. A message put together at run time is keyed by
its template, with `{}` where each value goes, so `"Measured {} window(s)."`
covers every count; a translation that needs the values in another order
numbers them, `{1}` before `{0}`. The page's labels and the status bar's lines
are looked up in the same table. A string with no translation is shown in
English, and where KudioStudio's own table already translates a string, its
translation is the one used.

## Trying it

```bash
cd rms-panel
pip install -e .
kudiostudio clip.wav
```

**Window RMS** is the last tool on the rail. Press **Measure** and the table
fills:

```text
Start     Level
0.00 s    -9.0 dBFS
1.00 s    -21.1 dBFS
2.00 s    digital silence
```

That is a 3 s clip: a second of a sine at half scale, a second at an eighth,
and a second of zeros. Select from 1.0 s to 2.5 s with a 0.5 s window and the
rows start at 1.00 s, on the clip's own timeline.

A change to `panel.py` needs `kudiostudio` started again. A change to `rms.js`
only needs the page loaded again, which is quickest in `--browser` mode:
reload the tab. `--debug` opens the window's developer tools, where a page
module that failed to load or mount leaves its error in the console.
