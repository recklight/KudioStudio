// The window: wires the plots, the transport and the panels to the Studio
// behind the local server, and keeps them in step with its events.

import { call, listen } from "./api.js";
import { clock, hertz, level } from "./format.js";
import { load as loadTable, t, translatePage } from "./i18n.js";
import { fillIcons, loadIcons, svg } from "./icons.js";
import { Overview } from "./overview.js";
import { SpectrogramPlot } from "./spectrogram.js";
import { TimeView } from "./view.js";
import { WaveformPlot } from "./waveform.js";

const $ = (id) => document.getElementById(id);
const app = $("app");

/** The tool rail. Only Inspect is in this window so far; the rest still
 *  live in the Qt one. */
const TOOLS = [
  { id: "inspect", icon: "inspect", title: "Inspect", ready: true },
  { id: "effects", icon: "effects", title: "Effects" },
  { id: "enhance", icon: "enhance", title: "Enhance" },
  { id: "compare", icon: "compare", title: "Compare" },
  { id: "record", icon: "record", title: "Record" },
  { id: "synthesize", icon: "synthesize", title: "Synthesize" },
  { id: "manifest", icon: "manifest", title: "Manifest" },
  { id: "pitch", icon: "pitch", title: "Pitch" },
  { id: "metrics", icon: "metrics", title: "Metrics" },
  { id: "batch", icon: "batch", title: "Batch" },
];

const view = new TimeView();
let state = null;
let playback = null;          // { start, end, at } while playing
let playhead = 0;
let levelsVersion = -1;

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
    const path = await ask(t("Open audio"), t("Path of an audio file on this computer"));
    if (path) await command("open_files", [[path]]);
  }
}

function togglePlay() {
  if (state?.has_audio) command("toggle_play");
}

function zoomToSelection() {
  const s = state?.selection;
  if (s) view.set(s[0] - (s[1] - s[0]) * 0.02, s[1] + (s[1] - s[0]) * 0.02);
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
    document.title = `${next.name} — KudioStudio`;
  } else {
    document.title = "KudioStudio";
  }
  const v = next.version_info;
  $("status-right").textContent = `KudioStudio ${v.kudiostudio} · kudio ${v.kudio}`;
  $("total").textContent = `/ ${clock(next.duration)}`;

  $("undo").disabled = !next.can_undo;
  $("revert").disabled = !next.can_undo;
  $("zoom-full").disabled = !next.has_audio;
  $("zoom-selection").disabled = !next.selection;
  $("play").disabled = !next.has_audio;
  $("compare").disabled = !next.can_compare;
  $("compare").classList.toggle("on", next.comparing);
  $("loop").checked = next.loop;
  $("selection-only").checked = next.selection_only;
  setGain(next.gain_db, false);

  renderFiles(next);
  renderMarkers(next);
  renderAnalysis(next);
  showSelection(next.selection, null);

  if (!next.has_audio) {
    wave.clear();
    spec.clear();
    overview.clear();
    return;
  }
  const newAudio = !previous || previous.version !== next.version;
  if (newAudio) {
    // an edit keeps the view where it is; another file starts on the whole
    // of it
    const newFile = !previous || !previous.has_audio || previous.path !== next.path;
    if (newFile) {
      view.reset(next.duration, next.sr);
    } else {
      view.duration = next.duration;
      view.sampleRate = next.sr;
      view.set(view.t0, view.t1);
    }
    overview.setAudio(next.version, next.duration).then((peak) => wave.setAudio(next.version, peak));
    spec.setAudio(next.version);
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

function renderMarkers(s) {
  const list = $("marker-list");
  list.replaceChildren();
  for (const m of s.markers) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="when mono"></span><span class="label"></span>`;
    li.querySelector(".when").textContent = m.end > m.start
      ? `${m.start.toFixed(2)}–${m.end.toFixed(2)}` : m.start.toFixed(3);
    li.querySelector(".label").textContent = m.label || "—";
    li.addEventListener("click", () => {
      setPlayhead(m.start);
      command("seek", [m.start]);
      if (m.end > m.start) command("set_selection", [m.start, m.end]);
    });
    list.append(li);
  }
  $("markers-empty").hidden = s.markers.length > 0;
  $("marker-count").textContent = s.markers.length ? String(s.markers.length) : "";
  for (const plot of plots) plot.setMarkers(s.markers);
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
  $("call-hint").textContent = s.call;
  $("call-hint").title = s.call;
}

function showSelection(sel, from) {
  for (const plot of plots) {
    if (plot !== (from === "wave" ? wave : from === "spec" ? spec : null)) plot.setSelection(sel);
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
  for (const plot of plots) plot.setPlayhead(state?.has_audio ? seconds : null);
  overview.setPlayhead(state?.has_audio ? seconds : null);
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

// --------------------------------------------------------------- playback

function onPlayback(event) {
  if (event.playing) {
    // a seek while playing restarts playback; the frame loop already runs
    const running = playback !== null;
    playback = { start: event.start, end: event.end, at: performance.now() };
    app.classList.add("playing");
    if (!running) requestAnimationFrame(animate);
  } else {
    playback = null;
    app.classList.remove("playing");
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

// ----------------------------------------------------------------- chrome

function status(text, warn = false) {
  $("status").textContent = text;
  $("status").style.color = warn ? "var(--warn)" : "";
}

function buildRail() {
  const rail = $("rail");
  rail.replaceChildren();
  TOOLS.forEach((tool, index) => {
    if (index === 1) {
      const rule = document.createElement("div");
      rule.className = "rule";
      rail.append(rule);
    }
    const button = document.createElement("button");
    button.dataset.tool = tool.id;
    button.classList.toggle("later", !tool.ready);
    button.innerHTML = `<i data-icon="${tool.icon}"></i><span data-t>${tool.title}</span>`;
    button.addEventListener("click", () => selectTool(tool));
    rail.append(button);
  });
  selectTool(TOOLS[0]);
}

function selectTool(tool) {
  for (const button of $("rail").querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.tool === tool.id);
  }
  for (const section of $("panel").querySelectorAll("[data-panel]")) {
    section.hidden = tool.ready ? section.dataset.panel !== "inspect" : section.dataset.panel !== "later";
  }
  if (!tool.ready) $("later-title").textContent = t(tool.title);
}

/** A small box asking for a line of text; resolves to it, or null. */
function ask(title, placeholder) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "prompt-backdrop";
    backdrop.innerHTML = `<form class="prompt"><h3></h3><input type="text">` +
      `<div class="button-row"><button type="button" class="btn subtle cancel"></button>` +
      `<button type="submit" class="btn primary"></button></div></form>`;
    backdrop.querySelector("h3").textContent = title;
    const input = backdrop.querySelector("input");
    input.placeholder = placeholder;
    backdrop.querySelector(".cancel").textContent = t("Cancel");
    backdrop.querySelector("[type=submit]").textContent = t("Open");
    const done = (value) => { backdrop.remove(); resolve(value); };
    backdrop.querySelector("form").addEventListener("submit", (e) => {
      e.preventDefault();
      done(input.value.trim().replace(/^"(.*)"$/, "$1") || null);
    });
    backdrop.querySelector(".cancel").addEventListener("click", () => done(null));
    backdrop.addEventListener("keydown", (e) => { if (e.key === "Escape") done(null); });
    document.body.append(backdrop);
    input.focus();
  });
}

// ----------------------------------------------------------------- wiring

function bind() {
  $("open").addEventListener("click", open);
  $("add-files").addEventListener("click", open);
  $("empty-open").addEventListener("click", open);
  $("undo").addEventListener("click", () => command("undo"));
  $("revert").addEventListener("click", () => command("revert"));
  $("zoom-full").addEventListener("click", () => view.full());
  $("zoom-selection").addEventListener("click", zoomToSelection);
  $("play").addEventListener("click", togglePlay);
  $("compare").addEventListener("click", () => command("set_compare", [!state?.comparing]));
  $("loop").addEventListener("change", (e) => command("set_transport", [], { loop: e.target.checked }));
  $("selection-only").addEventListener("change",
    (e) => command("set_transport", [], { selection_only: e.target.checked }));
  $("gain").addEventListener("input", (e) => setGain(Number(e.target.value), false));
  $("gain").addEventListener("change", (e) => setGain(Number(e.target.value), true));
  $("select-all").addEventListener("click", () => command("select_all"));
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
  // a check box or the gain slider keeps focus after a click; Space should
  // still play then, so only fields that take text keep their keys
  const typing = e.target.matches?.("input[type=text], input[type=number], select, textarea");
  if (typing && e.key !== "Escape") return;
  const ctrl = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();
  const handled = () => e.preventDefault();
  if (e.key === " " && !ctrl) { handled(); togglePlay(); }
  else if (e.key === "Home") { handled(); setPlayhead(0); command("seek", [0]); view.follow(0); }
  else if (e.key === "Escape") { handled(); command("clear_selection"); }
  else if (ctrl && key === "o") { handled(); open(); }
  else if (ctrl && key === "a") { handled(); command("select_all"); }
  else if (ctrl && key === "z") { handled(); command("undo"); }
  else if (ctrl && key === "e") { handled(); zoomToSelection(); }
  else if (ctrl && key === "f") { handled(); view.full(); }
  else if (ctrl && key === "b") { handled(); if (state?.can_compare) command("set_compare", [!state.comparing]); }
  else if (!ctrl && (e.key === "+" || e.key === "=")) { handled(); view.zoomAt(playhead, 0.5); }
  else if (!ctrl && e.key === "-") { handled(); view.zoomAt(playhead, 2); }
  else if (e.key === "ArrowLeft") { handled(); view.pan(-view.span * 0.15); }
  else if (e.key === "ArrowRight") { handled(); view.pan(view.span * 0.15); }
}

function applyLanguage(table) {
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
  // the lists and readouts are built in code; build them again in the new
  // language (same version, so nothing is fetched)
  if (state) {
    applyState(state);
    levelsVersion = -1;
    refreshLevels();
  }
}

async function boot() {
  await Promise.all([loadIcons(), spec.loadColormap()]);
  buildRail();
  fillIcons();
  applyLanguage(await call("translations"));
  bind();
  listen((event) => {
    if (event.type === "state") applyState(event.state);
    else if (event.type === "message") status(event.text);
    else if (event.type === "playback") onPlayback(event);
  }, (connected) => $("status-dot").classList.toggle("busy", !connected));
  applyState(await call("state"));
  status(t("Ready."));
}

boot().catch((error) => {
  console.error(error);
  status(String(error), true);
});
