// The window: wires the plots, the transport, the menus and the panels to
// the Studio behind the local server, and keeps them in step with its events.

import { call, listen } from "./api.js";
import { plotMenuItems, showContextMenu } from "./contextmenu.js";
import { about, ask, askNumber, choosePath, confirm, confirmReplace } from "./dialogs.js";
import { clock, hertz, level } from "./format.js";
import { load as loadTable, t, translatePage } from "./i18n.js";
import { fillIcons, loadIcons, svg } from "./icons.js";
import { MarkerList } from "./markers.js";
import { MenuBar, menuText } from "./menus.js";
import { Overview } from "./overview.js";
import { Panels } from "./panels.js";
import { applyOptions, nextZoom, openSettings, setLanguageHandler } from "./settings.js";
import { SpectrogramPlot } from "./spectrogram.js";
import { monoFont } from "./timeplot.js";
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
  onContextMenu: (e, at) => plotMenu(e, { ...at, which }),
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

/** Whether to go on with opening a file over the clip: at once when a file
 *  holds everything in it, and after asking when something would be lost. */
async function mayOpenOver() {
  if (!state?.unsaved) return true;
  return confirmReplace({ title: t("Open another file?"), submit: t("Open") });
}

async function open() {
  // asked before the dialog: the window's own dialog opens the files itself
  if (!(await mayOpenOver())) return;
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

async function insertSilence(at = null) {
  if (!state?.has_audio) return;
  const seconds = await askNumber({ title: menuText("Insert silence")[0], label: t("Seconds:"),
                                    value: 1, min: 0.001, max: 600, step: 0.1, unit: "s" });
  if (seconds !== null) command("insert_silence", at === null ? [seconds] : [seconds, at]);
}

/** The right-click menu of the waveform and the spectrogram. */
function plotMenu(e, at) {
  if (!state?.has_audio) return;
  const s = { ...state, mode: state.analysis.mode, nFft: state.analysis.n_fft,
              hop: state.analysis.hop_length, yZoomed: spec.yZoomed, units: spec.yUnits,
              label: t(spec.label) };
  showContextMenu(e, plotMenuItems(at, s, {
    play: (a, b) => command("play_range", b === undefined ? [a] : [a, b]),
    stop: () => command("stop"),
    seek: (time) => { setPlayhead(time); command("seek", [time]); },
    edit: (name) => edit(name)(),
    saveSelection,
    pasteAt: (time) => command("paste", [time]),
    silenceAt: (time) => insertSilence(time),
    markAt: (time) => command("markers.add", [time]),
    markSelection: () => command("markers.mark_selection"),
    zoomToSelection,
    zoomFull: () => view.full(),
    selectAll: () => command("select_all"),
    clearSelection: () => command("clear_selection"),
    copy: async (text) => {
      try {
        await navigator.clipboard.writeText(text);
        status(t(`Copied ${text}`));
      } catch (error) {
        status(error.message, true);
      }
    },
    resetY: () => spec.setYRange(spec.yBottom, spec.yMax),
    // n_fft and the hop together, so the frames overlap as much as before
    resolution: (factor) => command("set_analysis", [], {
      n_fft: Math.round(s.nFft * factor),
      hop_length: Math.min(Math.max(Math.round(s.hop * factor), 16), 16384) }),
    mode: (mode) => command("set_analysis", [], { mode }),
  }));
}

async function crossfadeDialog() {
  const ms = await askNumber({
    title: menuText("Splice crossfade")[0],
    label: t("Milliseconds blended at each cut and paste.\n0 disables it, at the cost of a click where two pieces meet."),
    value: state?.crossfade_ms ?? 5, min: 0, max: 200, step: 1, unit: "ms",
  });
  if (ms !== null) command("set_crossfade", [ms]);
}

// what Save As writes; the format follows the name's suffix
const AUDIO_SAVE_TYPES = ["WAV (*.wav)", "FLAC (*.flac)", "AIFF (*.aiff;*.aif)"];

async function saveTo(kind, commandName, title, types) {
  const suggested = await call("suggest_name", [kind]);
  const path = await choosePath("save", { title: t(title), suggested, types });
  if (!path) return;
  const answer = await command(commandName, [path]);
  // the path typed in a browser comes with no question before a file is
  // written over, so the server asks for one
  const target = answer?.confirm_replace;
  if (!target) return;
  const name = target.path.split(/[\\/]/).pop();
  const yes = await confirm({
    title: t(`Replace ${name}?`), submit: t("Replace"),
    text: target.source ? t("It is the file this audio was opened from.") : "",
    focusCancel: true,
  });
  if (yes) await command(commandName, [target.path, true]);
}

const saveAs = () => state?.has_audio && saveTo("audio", "save_as", "Save audio as", AUDIO_SAVE_TYPES);
const saveSelection = () => state?.can_copy
  && saveTo("selection", "save_selection", "Save selection as", AUDIO_SAVE_TYPES);
const exportScript = () => state?.has_history
  && saveTo("script", "export_script", "Export edits as Python script", ["Python (*.py)"]);

/** The lines over the exported image: what the file is, the kudio call
 *  behind the picture, and what its colours and time axis mean. */
function imageHeader() {
  const lines = [`${state.name} — ${state.sr} Hz, ${state.duration.toFixed(3)} s`,
                 live?.call || state.call];
  const lv = spec.levels;
  const map = state.options?.spectrum_map || "";
  lines.push(t(lv ? `colour: ${map}, ${lv.lo.toFixed(1)} to ${lv.hi.toFixed(1)}`
                      + `${lv.unit ? ` ${lv.unit}` : ""}; time in seconds`
                    : "time in seconds"));
  return lines;
}

/** The plots as they are on screen, into one PNG: the zoom, the selection,
 *  the markers and the playhead included, under a header that says what
 *  file, what analysis and what colour scale they are. */
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
  const lineHeight = Math.round(16 * dpr);
  const header = imageHeader();
  const top = gap + header.length * lineHeight + gap;
  const height = top + parts.reduce((sum, c) => sum + c.height, 0) + gap * parts.length;
  const font = `${Math.round(12 * dpr)}px ${monoFont()}`;
  const out = document.createElement("canvas");
  let ctx = out.getContext("2d");
  ctx.font = font;
  // as wide as the longest line of the header, or the plots, whichever is wider
  const textWidth = Math.max(...header.map((line) => ctx.measureText(line).width));
  const barRoom = Math.round(180 * dpr);
  out.width = Math.ceil(Math.max(width, textWidth + barRoom)) + gap * 2;
  out.height = height;
  ctx = out.getContext("2d");
  const css = getComputedStyle(document.documentElement);
  ctx.fillStyle = css.getPropertyValue("--base").trim();
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.fillStyle = css.getPropertyValue("--text").trim() || "#ddd";
  ctx.font = font;
  ctx.textBaseline = "top";
  header.forEach((line, i) => ctx.fillText(line, gap, gap + i * lineHeight));
  if (spec.levels && spec.lut) {
    // the colour bar, low to high, after the last line
    const barWidth = Math.round(160 * dpr);
    const x0 = out.width - gap - barWidth;
    const y0 = gap + (header.length - 1) * lineHeight;
    for (let i = 0; i < barWidth; i++) {
      const k = Math.round((i / (barWidth - 1)) * 255) * 3;
      ctx.fillStyle = `rgb(${spec.lut[k]}, ${spec.lut[k + 1]}, ${spec.lut[k + 2]})`;
      ctx.fillRect(x0 + i, y0, 1, Math.round(12 * dpr));
    }
  }
  let y = top;
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
    { label: "Open &folder…", run: openFolder },
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
    { label: "R&edo", keys: "Ctrl+Y", run: () => command("redo"), enabled: () => Boolean(state?.can_redo) },
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
    { label: "Se&ttings…", keys: "Ctrl+,", run: openSettings },
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
    { label: "&User guide", keys: "F1", run: () => command("open_guide") },
    "-",
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
  // a reply that leaves the Files list out has not changed it; one whose
  // list is not the one this page holds sends for it
  if (!("files" in next)) {
    next.files = previous?.files ?? [];
    if (previous && next.files_version !== previous.files_version) {
      next.files_version = previous.files_version;
      call("state").then(applyState);
    }
  }
  state = next;
  app.dataset.state = next.has_audio ? "ready" : "empty";
  applyOptions(next.options, {
    // the plots draw their own labels, in the size and font just set
    resized: () => {
      for (const plot of [wave, spec, overview]) {
        plot.readColors?.();          // the axis and its figures may have a new colour
        plot.resize();
      }
    },
    colormap: (name) => spec.loadColormap(name),
    cramped: (shown, chosen) => status(t(
      `Size ${Math.round(chosen * 100)}% needs a larger window; this one has room for ${Math.round(shown * 100)}%.`)),
  });

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

  if (!previous || previous.files !== next.files || previous.path !== next.path
      || previous.language !== next.language) renderFiles(next);
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
  } else if (previous.call !== next.call
             || previous.analysis.log_freq !== next.analysis.log_freq) {
    spec.refresh();
  }
  if (!playback) setPlayhead(next.playhead);
}

/** The folder part of *path*, and its last two folders for a heading. */
function folderOf(path) {
  const cut = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  const folder = cut > 0 ? path.slice(0, cut) : "";
  const parts = folder.split(/[\\/]/).filter(Boolean);
  return { folder, short: parts.slice(-2).join("/") || folder };
}

function renderFiles(s) {
  const list = $("file-list");
  list.replaceChildren();
  // a heading for each folder, with its count, when the list spans more
  // than one: a corpus folder after folder of takes named alike otherwise
  // reads as one long list of the same name
  const counts = new Map();
  for (const file of s.files) {
    const { folder } = folderOf(file.path);
    counts.set(folder, (counts.get(folder) || 0) + 1);
  }
  const grouped = counts.size > 1;
  let heading = null;
  let current = null;
  for (const file of s.files) {
    const { folder, short } = folderOf(file.path);
    if (grouped && folder !== heading) {
      heading = folder;
      const head = document.createElement("li");
      head.className = "folder";
      head.title = folder;
      head.innerHTML = `<span class="name"></span><span class="count mono"></span>`;
      head.querySelector(".name").textContent = short;
      head.querySelector(".count").textContent = String(counts.get(folder));
      list.append(head);
    }
    const li = document.createElement("li");
    li.classList.toggle("current", file.path === s.path);
    if (file.path === s.path) current = li;
    li.title = file.path;
    li.innerHTML = `<span class="name"></span><button class="btn ghost icon-only small remove"></button>`;
    // under its folder's heading the file's own name is enough
    li.querySelector(".name").textContent = grouped ? file.path.slice(folder.length + 1) : file.name;
    const remove = li.querySelector(".remove");
    remove.innerHTML = svg("close", 2.2);
    remove.title = t("Remove from the list");
    remove.addEventListener("click", (e) => { e.stopPropagation(); command("remove_file", [file.path]); });
    li.addEventListener("click", async () => {
      if (file.path !== s.path && await mayOpenOver()) command("load", [file.path]);
    });
    list.append(li);
  }
  $("files-empty").hidden = s.files.length > 0;
  current?.scrollIntoView?.({ block: "nearest" });
}

/** The file *step* places down the list from the open one, or up. */
async function stepFile(step) {
  const files = state?.files || [];
  if (!files.length) return;
  const at = files.findIndex((f) => f.path === state.path);
  const next = files[Math.min(Math.max((at < 0 ? -1 : at) + step, 0), files.length - 1)];
  if (next && next.path !== state.path && await mayOpenOver()) command("load", [next.path]);
}

async function openFolder() {
  if (!(await mayOpenOver())) return;
  const path = await choosePath("folder", { title: t("Open a folder of audio") });
  if (path) command("open_files", [[path]]);
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
  // the MFCC takes the frame too, where the kudio installed passes it on
  const spectral = a.mode === "stft" || a.mode === "mel" || (a.mode === "mfcc" && s.mfcc_takes_fft);
  $("n_fft").disabled = !spectral;
  $("hop_length").disabled = !spectral;
  $("log-freq").checked = Boolean(a.log_freq);
  $("log-freq").disabled = a.mode !== "stft";
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
 *  on yet, so the plots stop taking the mouse until the take is over. */
function onMonitor(e) {
  if (!e.on) {
    if (live) {
      const { clip } = live;
      live = null;
      app.classList.remove("live");
      if (state) renderAnalysis(state);
      // a take that lands keeps its last picture until its audio replaces
      // it; one that lands nothing leaves the clip open, so it comes back
      if (e.landed) liveEnded = true;
      else showClipAgain(clip);
    }
    return;
  }
  if (!live) {
    live = { span: e.span || 3, call: "",
             clip: { version: state?.version, t0: view.t0, t1: view.t1 } };
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

/** The clip after a take that landed nothing: drawn again from the server,
 *  with the marks the take hid and, if it is still the same audio, the zoom
 *  it had before. */
function showClipAgain(clip) {
  const s = state;
  if (!s?.has_audio) {
    wave.clear();
    spec.clear();
    overview.clear();
    return;
  }
  view.reset(s.duration, s.sr);
  if (clip.version === s.version) view.set(clip.t0, clip.t1);
  for (const plot of plots) plot.setMarkers(s.markers);
  showSelection(s.selection, null);
  setPlayhead(playhead);
  overview.setAudio(s.version, s.duration).then((peak) => wave.setAudio(s.version, peak));
  spec.setAudio(s.version);
  levelsVersion = -1;              // the loudness curve came down with the take
  refreshLevels();
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
  $("log-freq").addEventListener("change", (e) => command("set_analysis", [], { log_freq: e.target.checked }));
  for (const id of ["sel-start", "sel-end"]) {
    $(id).addEventListener("change", () => {
      const a = Number($("sel-start").value);
      const b = Number($("sel-end").value);
      if (Number.isFinite(a) && Number.isFinite(b) && b > a) command("set_selection", [a, b]);
    });
  }

  document.addEventListener("keydown", keyDown);

  // files dropped on the window: the window gives their full paths, a plain
  // browser does not
  document.addEventListener("dragover", (e) => { e.preventDefault(); app.classList.add("dragging"); });
  document.addEventListener("dragleave", (e) => {
    if (!e.relatedTarget) app.classList.remove("dragging");
  });
  document.addEventListener("drop", async (e) => {
    e.preventDefault();
    app.classList.remove("dragging");
    const paths = [...(e.dataTransfer?.files || [])].map((f) => f.pywebviewFullPath).filter(Boolean);
    // a manifest goes to its panel and leaves the clip alone
    const audio = paths.some((p) => !p.toLowerCase().endsWith(".json"));
    if (paths.length && audio && !(await mayOpenOver())) return;
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
  else if (e.key === "F1") { handled(); command("open_guide"); }
  else if (e.key === "Escape") { handled(); if (!typing) command("clear_selection"); else e.target.blur(); }
  else if (e.key === "Delete" && !ctrl) { handled(); edit("delete")(); }
  else if (ctrl && e.shiftKey && key === "s") { handled(); saveAs(); }
  else if (ctrl && e.shiftKey && key === "e") { handled(); saveSelection(); }
  else if (ctrl && key === "o") { handled(); open(); }
  else if (ctrl && key === "q") { handled(); quit(); }
  else if (ctrl && key === "a") { handled(); command("select_all"); }
  // Ctrl+Shift+Z as well as Ctrl+Y: both are Redo somewhere, and either one
  // taken as Undo would take a second edit back instead of restoring the first
  else if (ctrl && (key === "y" || (e.shiftKey && key === "z"))) { handled(); command("redo"); }
  else if (ctrl && key === "z") { handled(); command("undo"); }
  else if (ctrl && key === "x") { handled(); edit("cut")(); }
  else if (ctrl && key === "c") { handled(); edit("copy")(); }
  else if (ctrl && key === "v") { handled(); edit("paste")(); }
  else if (ctrl && key === "t") { handled(); edit("crop")(); }
  else if (ctrl && key === "l") { handled(); if (state?.has_audio) command("set_transport", [], { loop: !state.loop }); }
  else if (ctrl && key === "e") { handled(); zoomToSelection(); }
  else if (ctrl && key === "f") { handled(); view.full(); }
  else if (ctrl && key === "b") { handled(); toggleCompare(); }
  else if (ctrl && key === ",") { handled(); openSettings(); }
  // the interface size, as a browser's own Ctrl+ and Ctrl- would have it
  else if (ctrl && (e.key === "=" || e.key === "+")) { handled(); stepZoom(1); }
  else if (ctrl && e.key === "-") { handled(); stepZoom(-1); }
  else if (ctrl && e.key === "0") { handled(); command("options.set", ["zoom", 1]); }
  else if (!ctrl && (e.key === "+" || e.key === "=")) { handled(); view.zoomAt(playhead, 0.5); }
  else if (!ctrl && e.key === "-") { handled(); view.zoomAt(playhead, 2); }
  else if (e.key === "ArrowDown" && !ctrl) { handled(); stepFile(1); }
  else if (e.key === "ArrowUp" && !ctrl) { handled(); stepFile(-1); }
  else if (e.key === "ArrowLeft") { handled(); view.pan(-view.span * 0.15); }
  else if (e.key === "ArrowRight") { handled(); view.pan(view.span * 0.15); }
}

async function stepZoom(step) {
  const { zooms, values } = await call("options.describe");
  const zoom = nextZoom(zooms, values.zoom, step);
  if (zoom !== values.zoom) await command("options.set", ["zoom", zoom]);
}

async function applyLanguage(table) {
  loadTable(table.messages);
  document.documentElement.lang = table.language.replace("_", "-");
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
  // the language is chosen in Settings, which hands the choice back here
  setLanguageHandler(async (code) => {
    const table = await command("set_language", [code]);
    if (table) await applyLanguage(table);
  });
  bind();
  // how many lines the window has sent the status bar since this page
  // opened, so a line said before it opened cannot replace a newer one
  let heard = 0;
  listen((event) => {
    if (event.type === "state") applyState(event.state);
    else if (event.type === "message") { heard += 1; status(event.text); }
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
  // what the window said before this page was there to hear it -- a file
  // named on the command line that would not open, say. A line that
  // arrives while the question is out is newer than the answer to it
  const before = heard;
  const { left_out: leftOut, said } = await call("about");
  if (said && heard === before) status(said);
  if (leftOut.length) status(leftOut.join("; "), true);
}

boot().catch((error) => {
  console.error(error);
  status(String(error), true);
});
