// The window: wires the plots, the transport, the menus and the panels to
// the Studio behind the local server, and keeps them in step with its events.

import { call, listen } from "./api.js";
import { about, ask, askNumber, choosePath } from "./dialogs.js";
import { clock, hertz, level } from "./format.js";
import { load as loadTable, t, translatePage } from "./i18n.js";
import { fillIcons, loadIcons, svg } from "./icons.js";
import { MarkerList } from "./markers.js";
import { MenuBar, menuText } from "./menus.js";
import { Overview } from "./overview.js";
import { Panels } from "./panels.js";
import { SpectrogramPlot } from "./spectrogram.js";
import { TimeView } from "./view.js";
import { WaveformPlot } from "./waveform.js";

const $ = (id) => document.getElementById(id);
const app = $("app");

/** Tooltips of more than one line, set from here because a line break
 *  inside an HTML attribute becomes CRLF on a Windows checkout and then
 *  fails to match its translation. */
const TIPS = {
  "loop-wrap": "Repeat what was last played (Ctrl+L).\nA panel's Preview always loops; this is for ordinary playback.",
  "selection-only-wrap": "Play just the highlighted range when there is a selection.",
  compare: "Hold the edit against the audio you started with (Ctrl+B).\nViews and playback follow; the tools keep working on the edit.",
  "match-wrap": "Level the original to the edit (ITU-R BS.1770) before comparing.\nOf two versions of a clip the louder is preferred whatever its\nquality, so an unmatched A/B is partly a loudness test.",
  "xf-wrap": "Milliseconds blended at each cut, paste and insert.\n0 is a hard cut, at the cost of a click where two pieces meet.\nEvery edit says in the status bar how much it applied.",
  "marker-save": "Write them beside the audio as clip.wav.labels.json, which is picked up automatically next time the file is opened.",
};

const view = new TimeView();
let state = null;
let playback = null;          // { start, end, at } while playing
let playhead = 0;
let levelsVersion = -1;
let loudness = null;          // { curve, shortTerm } for the audio on screen
let live = null;              // { span, call } while a take is running
let liveEnded = false;        // a take has just ended; its audio is on its way
const busy = new Map();       // owner -> label, while a job runs

// A length change below this is the same clip: frame-based processing does
// not hand back exactly what it was given (kudio's trad_enhance comes back
// 8 ms short), and calling that a new clip would reset the zoom after every
// denoise. The server holds the colour scale by the same rule (SAME_CLIP_S).
const SAME_CLIP_S = 0.05;

// ------------------------------------------------------------------ plots

const plotHooks = (which) => ({
  onSelect: (a, b) => command("set_selection", [a, b]),
  onSelecting: (sel) => showSelection(sel, which),
  onSeek: (time) => { setPlayhead(time); command("seek", [time]); },
  onHover: (time, value) => hover(which, time, value),
  onDoubleClick: () => (state?.selection ? zoomToSelection() : view.full()),
});
const wave = new WaveformPlot($("wave"), view, plotHooks("wave"));
const spec = new SpectrogramPlot($("spec"), view, plotHooks("spec"));
const overview = new Overview($("overview"), view);
const plots = [wave, spec];

const markers = new MarkerList({
  apply: (s) => applyState(s),
  seek: (time) => { setPlayhead(time); command("seek", [time]); view.follow(time); },
});
const panels = new Panels({ apply: (s) => applyState(s), status, state: () => state });

// --------------------------------------------------------------- commands

async function command(name, args = [], kwargs = {}) {
  try {
    const result = await call(name, args, kwargs);
    if (result && typeof result === "object" && "version" in result && "has_audio" in result) {
      applyState(result);
    }
    return result;
  } catch (error) {
    status(error.message, true);
    return null;
  }
}

async function open() {
  const result = await command("open_dialog");
  if (result && result.needs_path) {
    const path = await ask({ title: t("Open audio"), submit: t("Open"),
                             placeholder: t("Path of an audio file on this computer") });
    if (path) await command("open_files", [[path]]);
  }
}

async function openManifest() {
  const path = await choosePath("open", { title: t("Open a corpus manifest"),
                                          types: ["Manifest (*.json)", "All files (*.*)"] });
  if (path) await command("open_manifest", [path]);
}

function togglePlay() {
  if (state?.has_audio) command("toggle_play");
}

function seekHome() {
  if (!state?.has_audio) return;
  setPlayhead(0);
  command("seek", [0]);
  view.follow(0);
}

function zoomToSelection() {
  const s = state?.selection;
  if (s) view.set(s[0] - (s[1] - s[0]) * 0.02, s[1] + (s[1] - s[0]) * 0.02);
}

// ------------------------------------------------------------ edit + file

async function insertSilence() {
  if (!state?.has_audio) return;
  const seconds = await askNumber({ title: menuText("Insert silence")[0], label: t("Seconds:"),
                                    value: 1, min: 0.001, max: 600, step: 0.1, unit: "s" });
  if (seconds !== null) command("insert_silence", [seconds]);
}

async function crossfadeDialog() {
  const ms = await askNumber({
    title: menuText("Splice crossfade")[0],
    label: t("Milliseconds blended at each cut and paste.\n0 disables it, at the cost of a click where two pieces meet."),
    value: state?.crossfade_ms ?? 5, min: 0, max: 200, step: 1, unit: "ms",
  });
  if (ms !== null) command("set_crossfade", [ms]);
}

async function saveTo(kind, commandName, title, types) {
  const suggested = await call("suggest_name", [kind]);
  const path = await choosePath("save", { title: t(title), suggested, types });
  if (path) await command(commandName, [path]);
}

const saveAs = () => state?.has_audio && saveTo("audio", "save_as", "Save audio as", ["WAV (*.wav)"]);
const saveSelection = () => state?.can_copy
  && saveTo("selection", "save_selection", "Save selection as", ["WAV (*.wav)"]);
const exportScript = () => state?.has_history
  && saveTo("script", "export_script", "Export edits as Python script", ["Python (*.py)"]);

/** The plots as they are on screen, into one PNG: the zoom, the selection
 *  and the playhead included. */
async function exportImage() {
  if (!state?.has_audio) { status(t("Nothing to export.")); return; }
  const suggested = await call("suggest_name", ["image"]);
  const path = await choosePath("save", { title: t("Export analysis image"), suggested,
                                          types: ["PNG (*.png)"] });
  if (!path) return;
  const parts = [$("wave"), $("spec")];
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(...parts.map((c) => c.width));
  const gap = Math.round(10 * dpr);
  const height = parts.reduce((sum, c) => sum + c.height, 0) + gap * (parts.length + 1);
  const out = document.createElement("canvas");
  out.width = width + gap * 2;
  out.height = height;
  const ctx = out.getContext("2d");
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--base").trim();
  ctx.fillRect(0, 0, out.width, out.height);
  let y = gap;
  for (const c of parts) {
    ctx.drawImage(c, gap, y);
    y += c.height + gap;
  }
  await command("write_image", [path, out.toDataURL("image/png")]);
}

async function showAbout() {
  const info = await call("about");
  about(info, info.plugins);
}

async function quit() {
  const result = await command("quit");
  if (result && !result.closing) status(t("Close the browser tab to leave; the server stops with Ctrl+C."));
}

function edit(name) {
  return () => state?.has_audio && command(name);
}

// ------------------------------------------------------------------ menus

const has = () => Boolean(state?.has_audio);
const MENUS = [
  { title: "&File", items: [
    { label: "&Open…", keys: "Ctrl+O", run: open },
    { label: "Open &manifest…", run: openManifest },
    { label: "Save &As…", keys: "Ctrl+Shift+S", run: saveAs, enabled: has },
    { label: "Save &selection as…", keys: "Ctrl+Shift+E", run: saveSelection,
      enabled: () => Boolean(state?.can_copy) },
    "-",
    { label: "&Export analysis image…", run: exportImage, enabled: has },
    { label: "Export edits as &Python script…", run: exportScript,
      enabled: () => Boolean(state?.has_history) },
    "-",
    { label: "&Quit", keys: "Ctrl+Q", run: quit },
  ] },
  { title: "&Edit", items: [
    { label: "&Undo", keys: "Ctrl+Z", run: () => command("undo"), enabled: () => Boolean(state?.can_undo) },
    { label: "&Revert to original", run: () => command("revert"), enabled: () => Boolean(state?.can_revert) },
    "-",
    { label: "Cu&t\tCtrl+X", run: edit("cut"), enabled: () => Boolean(state?.can_copy) },
    { label: "&Copy\tCtrl+C", run: edit("copy"), enabled: () => Boolean(state?.can_copy) },
    { label: "&Paste\tCtrl+V", run: edit("paste"), enabled: () => Boolean(state?.can_paste) },
    { label: "&Delete\tDel", run: edit("delete"), enabled: () => Boolean(state?.can_copy) },
    "-",
    { label: "Crop to selectio&n", keys: "Ctrl+T", run: edit("crop"), enabled: () => Boolean(state?.can_copy) },
    { label: "&Insert silence…", run: insertSilence, enabled: has },
    { label: "Splice cross&fade…", run: crossfadeDialog,
      text: (label) => `${label} (${(state?.crossfade_ms ?? 0).toString()} ms)` },
    "-",
    { label: "Select &All", keys: "Ctrl+A", run: () => command("select_all"), enabled: has },
    { label: "&Clear selection", keys: "Esc", run: () => command("clear_selection"), enabled: has },
  ] },
  { title: "&Playback", items: [
    { label: "&Play / Stop\tSpace", run: togglePlay, enabled: has },
    { label: "Playhead to &start\tHome", run: seekHome, enabled: has },
    "-",
    { label: "&Loop", keys: "Ctrl+L", run: () => command("set_transport", [], { loop: !state?.loop }),
      enabled: has, checked: () => Boolean(state?.loop) },
    { label: "&A/B compare original", keys: "Ctrl+B", run: toggleCompare,
      enabled: () => Boolean(state?.can_compare), checked: () => Boolean(state?.comparing) },
  ] },
  { title: "&View", items: [
    { label: "Zoom to &selection", keys: "Ctrl+E", run: zoomToSelection,
      enabled: () => Boolean(state?.selection) },
    { label: "Zoom &out to whole clip", keys: "Ctrl+F", run: () => view.full(), enabled: has },
  ] },
  { title: "&Help", items: [
    { label: "&About", run: showAbout },
  ] },
];
const menuBar = new MenuBar($("menubar"), MENUS);

function toggleCompare() {
  if (state?.can_compare) command("set_compare", [!state.comparing]);
}

// ------------------------------------------------------------------ state

function applyState(next) {
  const previous = state;
  state = next;
  app.dataset.state = next.has_audio ? "ready" : "empty";

  $("file-chip").hidden = !next.has_audio;
  if (next.has_audio) {
    $("file-name").textContent = next.name;
    $("file-meta").textContent =
      `${(next.sr / 1000).toFixed(next.sr % 1000 ? 1 : 0)} kHz · ${next.duration.toFixed(2)} s`;
    $("file-chip").title = `${next.name}  ·  ${next.duration.toFixed(2)}s  ·  ${next.sr} Hz  ·  ` +
      `${next.samples} samples`;
    document.title = `${next.name} — KudioStudio`;
  } else {
    document.title = "KudioStudio";
  }
  const v = next.version_info;
  $("status-right").textContent = `KudioStudio ${v.kudiostudio} · kudio ${v.kudio}`;
  $("total").textContent = `/ ${clock(next.duration)}`;

  $("save").disabled = !next.has_audio;
  $("undo").disabled = !next.can_undo;
  $("revert").disabled = !next.can_revert;
  $("zoom-full").disabled = !next.has_audio;
  $("zoom-selection").disabled = !next.selection;
  $("play").disabled = !next.has_audio;
  $("compare").disabled = !next.can_compare;
  $("compare").classList.toggle("on", next.comparing);
  $("compare").textContent = menuText(next.comparing ? "A / B  ·  original" : "A / B  ·  edited")[0];
  $("match-loudness").checked = next.match_loudness;
  $("match-loudness").disabled = !next.can_compare;
  $("match-wrap").classList.toggle("disabled", !next.can_compare);
  $("loop").checked = next.loop;
  $("selection-only").checked = next.selection_only;
  if (document.activeElement !== $("crossfade")) $("crossfade").value = next.crossfade_ms;
  setGain(next.gain_db, false);

  renderFiles(next);
  markers.render(next);
  // while a take runs the plots show it, and the clip's marks are not on it
  for (const plot of plots) plot.setMarkers(live ? [] : next.markers);
  renderAnalysis(next);
  showSelection(next.selection, null);
  panels.state(next);

  if (!next.has_audio) {
    wave.clear();
    spec.clear();
    overview.clear();
    if (previous?.has_audio) clearLevels();
    return;
  }
  const newAudio = !previous || previous.version !== next.version;
  if (newAudio) {
    // An edit that keeps the clip -- a denoise, a gain, an A/B swap -- keeps
    // the zoom and the amplitude axis, since that is when two versions are
    // being compared: a quieter version has to look quieter. Another file, a
    // take that has just been recorded, or a trim start on the whole of it.
    const newFile = !previous || !previous.has_audio || previous.path !== next.path || liveEnded;
    const sameClip = !newFile && Math.abs(next.duration - previous.duration) <= SAME_CLIP_S;
    liveEnded = false;
    if (!sameClip) {
      view.reset(next.duration, next.sr);
    } else {
      view.duration = next.duration;
      view.sampleRate = next.sr;
      view.set(view.t0, view.t1);
    }
    // the axis only ever grows, so nothing is drawn off the plot
    const held = sameClip ? wave.peak : 0;
    overview.setAudio(next.version, next.duration)
      .then((peak) => wave.setAudio(next.version, Math.max(peak, held)));
    spec.setAudio(next.version);
    // measured from audio that is no longer there; the new one is on its way
    loudness = null;
    applyLoudness();
    refreshLevels();
  } else if (previous.call !== next.call) {
    spec.refresh();
  }
  if (!playback) setPlayhead(next.playhead);
}

function renderFiles(s) {
  const list = $("file-list");
  list.replaceChildren();
  for (const file of s.files) {
    const li = document.createElement("li");
    li.classList.toggle("current", file.path === s.path);
    li.title = file.path;
    li.innerHTML = `<span class="name"></span><button class="btn ghost icon-only small remove"></button>`;
    li.querySelector(".name").textContent = file.name;
    const remove = li.querySelector(".remove");
    remove.innerHTML = svg("close", 2.2);
    remove.title = t("Remove from the list");
    remove.addEventListener("click", (e) => { e.stopPropagation(); command("remove_file", [file.path]); });
    li.addEventListener("click", () => { if (file.path !== s.path) command("load", [file.path]); });
    list.append(li);
  }
  $("files-empty").hidden = s.files.length > 0;
}

function renderAnalysis(s) {
  const a = s.analysis;
  for (const button of $("view-mode").querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.mode === a.mode);
  }
  for (const name of ["n_fft", "hop_length", "n_mels", "n_mfcc"]) {
    const input = $(name);
    if (document.activeElement !== input) input.value = a[name];
  }
  const spectral = a.mode === "stft" || a.mode === "mel";
  $("n_fft").disabled = !spectral;
  $("hop_length").disabled = !spectral;
  $("n_mels").disabled = a.mode !== "mel";
  $("n_mfcc").disabled = a.mode !== "mfcc";
  const call = live?.call || s.call;
  $("call-hint").textContent = call;
  $("call-hint").title = call;
}

function showSelection(sel, from) {
  for (const plot of plots) {
    if (plot !== (from === "wave" ? wave : from === "spec" ? spec : null)) {
      plot.setSelection(live ? null : sel);
    }
  }
  const start = $("sel-start");
  const end = $("sel-end");
  if (document.activeElement !== start) start.value = sel ? sel[0].toFixed(3) : "";
  if (document.activeElement !== end) end.value = sel ? sel[1].toFixed(3) : "";
  $("sel-length").textContent = sel
    ? t(`${(sel[1] - sel[0]).toFixed(3)} s selected`) : t("nothing selected");
  $("zoom-selection").disabled = !sel;
}

function setPlayhead(seconds) {
  playhead = seconds;
  const shown = state?.has_audio && !live ? seconds : null;
  for (const plot of plots) plot.setPlayhead(shown);
  overview.setPlayhead(shown);
  $("time").textContent = clock(seconds);
}

function setGain(db, send) {
  const slider = $("gain");
  slider.value = db;
  const fraction = (db - Number(slider.min)) / (Number(slider.max) - Number(slider.min));
  slider.style.setProperty("--fill", `${(fraction * 100).toFixed(1)}%`);
  $("gain-value").textContent = db ? `${db > 0 ? "+" : ""}${db} dB` : "0 dB";
  if (send) command("set_transport", [], { gain_db: Number(db) });
}

function hover(which, time, value) {
  const out = $(which === "wave" ? "wave-readout" : "spec-readout");
  if (time === null || !state?.has_audio) { out.textContent = ""; return; }
  if (which === "wave") {
    out.textContent = `${time.toFixed(3)} s · ${value.toFixed(3)}`;
  } else if (spec.yUnits === "Hz") {
    out.textContent = `${time.toFixed(3)} s · ${hertz(value)}Hz`;
  } else {
    // a band or coefficient number, counted from 0 as the arrays are
    out.textContent = `${time.toFixed(3)} s · ${t(spec.label)} ${Math.floor(value)}`;
  }
}

// ------------------------------------------------------------------ takes

/** The plots while a take runs: the last few seconds, right-aligned on
 *  "now", drawn from the server's snapshots. There is no timeline to select
 *  on yet, so the plots stop taking the mouse until the take lands. */
function onMonitor(e) {
  if (!e.on) {
    if (live) {
      live = null;
      liveEnded = true;
      app.classList.remove("live");
      if (state) renderAnalysis(state);
    }
    return;
  }
  if (!live) {
    live = { span: e.span || 3, call: "" };
    app.classList.add("live");
    for (const plot of plots) {
      plot.setMarkers([]);
      plot.setSelection(null);
    }
    setPlayhead(playhead);
    view.reset(live.span, e.sr || 0);
    overview.clear();
    loudness = null;
    applyLoudness();
    spec.setOverlay(null);
  }
  if (e.call) {
    live.call = e.call;
    $("call-hint").textContent = e.call;
    $("call-hint").title = e.call;
  }
  if (e.span && e.span !== live.span) {
    live.span = e.span;
    view.reset(live.span, 0);
  }
  if ("wave" in e) wave.live(e.wave);
  if ("image" in e) spec.live(e.image, live.span);
}

// --------------------------------------------------------------- playback

function onPlayback(event) {
  if (event.playing) {
    // a seek while playing restarts playback; the frame loop already runs
    const running = playback !== null;
    playback = { start: event.start, end: event.end, at: performance.now() };
    app.classList.add("playing");
    app.classList.toggle("previewing", Boolean(event.preview));
    if (!running) requestAnimationFrame(animate);
  } else {
    playback = null;
    app.classList.remove("playing", "previewing");
    setPlayhead(event.position);
  }
}

function animate() {
  if (!playback) return;
  const position = Math.min(playback.start + (performance.now() - playback.at) / 1000, playback.end);
  setPlayhead(position);
  view.follow(position);
  requestAnimationFrame(animate);
}

// ----------------------------------------------------------------- levels

async function refreshLevels() {
  if (!state?.has_audio || levelsVersion === state.version) return;
  levelsVersion = state.version;
  const measured = await command("levels");
  if (!measured || measured.empty || measured.version !== state.version) return;
  loudness = { curve: measured.curve, shortTerm: measured.short_term_s };
  applyLoudness();
  const put = (id, text, cls = "") => {
    const el = $(id);
    el.textContent = text ?? t("n/a");
    el.className = `mono ${cls}`;
  };
  put("lv-peak", measured.peak_dbfs === null ? t("silence") : level(measured.peak_dbfs, "dBFS"),
      measured.clipped ? "warn" : "ok");
  put("lv-true-peak", measured.true_peak_db === null ? t("silence") : level(measured.true_peak_db, "dBTP"),
      measured.true_peak_over ? "warn" : "");
  put("lv-loudness", measured.lufs === null ? null : `${measured.lufs.toFixed(1)} LUFS`);
  put("lv-range", measured.range_lu === null ? null : `${measured.range_lu.toFixed(1)} LU`);
  put("lv-bandwidth", measured.bandwidth_hz === null ? null : `${(measured.bandwidth_hz / 1000).toFixed(1)} kHz`,
      measured.band_limited ? "warn" : "");
  const problems = $("lv-problems");
  problems.replaceChildren();
  problems.classList.toggle("clean", measured.problems.length === 0);
  for (const text of measured.problems.length ? measured.problems
                                               : [t("nothing wrong with the recording itself")]) {
    const li = document.createElement("li");
    li.textContent = text;
    problems.append(li);
  }
}

/** Nothing to measure: the Levels box goes back to how it starts, rather
 *  than keep the figures of a clip that is gone. */
function clearLevels() {
  levelsVersion = -1;
  loudness = null;
  applyLoudness();
  for (const id of ["lv-peak", "lv-true-peak", "lv-loudness", "lv-range", "lv-bandwidth"]) {
    $(id).textContent = "—";
    $(id).className = "mono";
  }
  $("lv-problems").replaceChildren();
  $("lv-problems").classList.remove("clean");
}

/** The short-term loudness over the waveform, when it is ticked and there
 *  is one. A box that ticks and does nothing is worse than one that says why
 *  it cannot: a clip shorter than the window has no curve, not a poor one. */
function applyLoudness() {
  const box = $("show-loudness");
  const curve = loudness?.curve ?? null;
  box.disabled = !curve;
  $("loudness-wrap").classList.toggle("disabled", !curve);
  $("loudness-wrap").title = curve || !loudness
    ? t("Short-term loudness (EBU Tech 3341) over the waveform, on its own LUFS\naxis. The integrated figure in the Levels box is one number for the whole\nclip; this is where it moved.")
    : t(`The clip is shorter than the ${loudness.shortTerm} s short-term window (EBU Tech 3341), so there is no curve to draw.`);
  const on = Boolean(curve && box.checked);
  wave.setLoudness(on ? curve : null);
  spec.setRightGutter(on);
}

// ----------------------------------------------------------------- chrome

function status(text, warn = false) {
  $("status").textContent = text;
  $("status").style.color = warn ? "var(--warn)" : "";
}

function onJob(event) {
  if (event.busy) busy.set(event.owner, event.label);
  else busy.delete(event.owner);
  const labels = [...busy.values()].filter(Boolean).map((l) => t(l));
  $("status-busy").textContent = labels.join(" · ");
  $("status-dot").classList.toggle("busy", busy.size > 0);
}

function applyTips() {
  for (const [id, tip] of Object.entries(TIPS)) {
    const el = $(id);
    if (el) el.title = t(tip);
  }
}

// ----------------------------------------------------------------- wiring

function bind() {
  $("open").addEventListener("click", open);
  $("save").addEventListener("click", saveAs);
  $("add-files").addEventListener("click", open);
  $("empty-open").addEventListener("click", open);
  $("undo").addEventListener("click", () => command("undo"));
  $("revert").addEventListener("click", () => command("revert"));
  $("zoom-full").addEventListener("click", () => view.full());
  $("zoom-selection").addEventListener("click", zoomToSelection);
  $("play").addEventListener("click", togglePlay);
  $("compare").addEventListener("click", toggleCompare);
  $("match-loudness").addEventListener("change", (e) => command("set_match_loudness", [e.target.checked]));
  $("loop").addEventListener("change", (e) => command("set_transport", [], { loop: e.target.checked }));
  $("selection-only").addEventListener("change",
    (e) => command("set_transport", [], { selection_only: e.target.checked }));
  $("crossfade").addEventListener("change", (e) => {
    const ms = Number(e.target.value);
    if (Number.isFinite(ms)) command("set_crossfade", [ms]);
  });
  $("gain").addEventListener("input", (e) => setGain(Number(e.target.value), false));
  $("gain").addEventListener("change", (e) => setGain(Number(e.target.value), true));
  $("select-all").addEventListener("click", () => command("select_all"));
  $("show-loudness").addEventListener("change", applyLoudness);
  $("clear-selection").addEventListener("click", () => command("clear_selection"));

  for (const button of $("view-mode").querySelectorAll("button")) {
    button.addEventListener("click", () => command("set_analysis", [], { mode: button.dataset.mode }));
  }
  for (const name of ["n_fft", "hop_length", "n_mels", "n_mfcc"]) {
    $(name).addEventListener("change", (e) => command("set_analysis", [], { [name]: Number(e.target.value) }));
  }
  for (const id of ["sel-start", "sel-end"]) {
    $(id).addEventListener("change", () => {
      const a = Number($("sel-start").value);
      const b = Number($("sel-end").value);
      if (Number.isFinite(a) && Number.isFinite(b) && b > a) command("set_selection", [a, b]);
    });
  }
  $("language").addEventListener("change", async (e) => {
    const table = await command("set_language", [e.target.value]);
    if (table) applyLanguage(table);
  });

  document.addEventListener("keydown", keyDown);

  // files dropped on the window: the window gives their full paths, a plain
  // browser does not
  document.addEventListener("dragover", (e) => { e.preventDefault(); app.classList.add("dragging"); });
  document.addEventListener("dragleave", (e) => {
    if (!e.relatedTarget) app.classList.remove("dragging");
  });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    app.classList.remove("dragging");
    const paths = [...(e.dataTransfer?.files || [])].map((f) => f.pywebviewFullPath).filter(Boolean);
    if (paths.length) command("open_files", [paths]);
    else if (e.dataTransfer?.files?.length) status(t("Dropping files needs the KudioStudio window; use Open here."));
  });
}

function keyDown(e) {
  if (document.querySelector(".prompt-backdrop")) return;     // a box is open
  // a check box or the gain slider keeps focus after a click; Space should
  // still play then, so only fields that take text keep their keys
  const typing = e.target.matches?.("input[type=text], input[type=number], select, textarea");
  if (typing && e.key !== "Escape") return;
  const ctrl = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();
  const handled = () => e.preventDefault();
  if (e.repeat && (e.key === " " || ctrl)) { handled(); return; }
  if (e.key === " " && !ctrl) { handled(); togglePlay(); }
  else if (e.key === "Home") { handled(); seekHome(); }
  else if (e.key === "Escape") { handled(); if (!typing) command("clear_selection"); else e.target.blur(); }
  else if (e.key === "Delete" && !ctrl) { handled(); edit("delete")(); }
  else if (ctrl && e.shiftKey && key === "s") { handled(); saveAs(); }
  else if (ctrl && e.shiftKey && key === "e") { handled(); saveSelection(); }
  else if (ctrl && key === "o") { handled(); open(); }
  else if (ctrl && key === "q") { handled(); quit(); }
  else if (ctrl && key === "a") { handled(); command("select_all"); }
  else if (ctrl && key === "z") { handled(); command("undo"); }
  else if (ctrl && key === "x") { handled(); edit("cut")(); }
  else if (ctrl && key === "c") { handled(); edit("copy")(); }
  else if (ctrl && key === "v") { handled(); edit("paste")(); }
  else if (ctrl && key === "t") { handled(); edit("crop")(); }
  else if (ctrl && key === "l") { handled(); if (state?.has_audio) command("set_transport", [], { loop: !state.loop }); }
  else if (ctrl && key === "e") { handled(); zoomToSelection(); }
  else if (ctrl && key === "f") { handled(); view.full(); }
  else if (ctrl && key === "b") { handled(); toggleCompare(); }
  else if (!ctrl && (e.key === "+" || e.key === "=")) { handled(); view.zoomAt(playhead, 0.5); }
  else if (!ctrl && e.key === "-") { handled(); view.zoomAt(playhead, 2); }
  else if (e.key === "ArrowLeft") { handled(); view.pan(-view.span * 0.15); }
  else if (e.key === "ArrowRight") { handled(); view.pan(view.span * 0.15); }
}

async function applyLanguage(table) {
  loadTable(table.messages);
  document.documentElement.lang = table.language.replace("_", "-");
  const select = $("language");
  select.replaceChildren();
  for (const [code, name] of Object.entries(table.languages)) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = name;
    option.selected = code === table.language;
    select.append(option);
  }
  translatePage();
  applyTips();
  menuBar.render();
  // the lists and readouts are built in code; build them again in the new
  // language (same version, so nothing is fetched)
  if (state) {
    applyState(state);
    levelsVersion = -1;
    refreshLevels();
  }
  await panels.translate();
}

async function boot() {
  await Promise.all([loadIcons(), spec.loadColormap()]);
  fillIcons();
  await applyLanguage(await call("translations"));
  bind();
  listen((event) => {
    if (event.type === "state") applyState(event.state);
    else if (event.type === "message") status(event.text);
    else if (event.type === "playback") onPlayback(event);
    else if (event.type === "job") { onJob(event); panels.event(event); }
    else if (event.type === "overlay") spec.setOverlay(event.curve);
    else if (event.type === "monitor") onMonitor(event);
    else if (event.type === "tool") panels.select(event.id);
    else panels.event(event);
  }, (connected) => $("status-dot").classList.toggle("offline", !connected));
  applyState(await call("state"));
  spec.setOverlay(await call("overlay"));
  for (const owner of state.busy) onJob({ owner, busy: true, label: "" });
  await panels.load();
  status(t("Ready."));
  const { left_out: leftOut } = await call("about");
  if (leftOut.length) status(leftOut.join("; "), true);
}

boot().catch((error) => {
  console.error(error);
  status(String(error), true);
});
