// Survey: kudio.audio_report, the narrowband lines, a voice share and the
// seams between takes, over the Files list or a folder. The table opens over
// the plots, where it has the room; a click on a row opens that file. So do
// the folder spectra and the timeline, where a click on a take opens it.

import { chart, clockText, clockTick, clockTicks } from "../chart.js";
import { hertz } from "../format.js";

const baseName = (path) => path.replace(/\\/g, "/").split("/").pop();

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.source ??= "";
  memory.clipRun ??= String(info.clip_runs[0]);
  memory.lines ??= true;
  memory.voice ??= false;
  memory.detector ??= "kudio";
  if (!info.detectors.some(([d]) => d === memory.detector)) memory.detector = "kudio";
  memory.seams ??= false;

  const box = ui.section("Survey");
  const form = ui.form();
  const source = ui.path({ kind: "folder", title: "A folder to survey",
                           onChange: (p) => { memory.source = p; } });
  source.value = memory.source;
  const useList = ui.button("Files list", {
    tip: "Survey the files in the Files list instead of a folder.",
    onClick: () => { source.value = ""; memory.source = ""; },
  });
  const clipRun = ui.select(info.clip_runs.map(String),
                            { value: memory.clipRun, onChange: (v) => { memory.clipRun = v; } });
  form.row("source", source);
  form.row("", useList);
  form.row("clip_run", clipRun, { tip: "Samples in a row at full scale that count as clipping: three is kudio's own; two or one match tools that count shorter runs." });
  const lines = ui.check("Narrowband lines and combs", { checked: memory.lines,
                                                          onChange: (on) => { memory.lines = on; } });
  const voice = ui.check("Voice share, beside a phase-scrambled copy", {
    checked: memory.voice, onChange: (on) => { memory.voice = on; } });
  voice.title = t("The copy keeps the take's spectrum but not how it rises and falls. A share well above the copy's means the take rises and falls more than a steady sound: speech does, and so do knocks and a machine that cycles, so listen before calling it a voice.");
  // a choice only where Silero VAD is installed; kudio's own detector otherwise
  const detector = ui.select(info.detectors, { value: memory.detector,
                                               onChange: (v) => { memory.detector = v; } });
  const detectorForm = ui.form();
  detectorForm.row("detector", detector, { tip: "kudio.speech_ratio is kudio's own frame-by-frame judgement. Silero VAD is a trained model, used here at 16 kHz on 100–4000 Hz brought to one level." });
  detectorForm.el.hidden = info.detectors.length < 2;
  const seams = ui.check("Seams between neighbouring takes", { checked: memory.seams,
                                                              onChange: (on) => { memory.seams = on; } });
  seams.title = t("For one recording cut into files. Takes recorded one at a time do not join, and smooth ones would all show as seams.");
  const run = ui.button("Survey", { primary: true, onClick: start });
  const stop = ui.button("Stop", { onClick: () => ctx.call("stop") });
  const progress = ui.readout("");
  const summary = ui.readout("");
  box.body.append(form.el, lines, voice, detectorForm.el, seams, ui.buttons(run, stop),
                  progress, summary);

  const results = ui.section("Results");
  const filterForm = ui.form();
  const filter = ui.select([["all", "all"]], { value: "all", translate: true,
                                               onChange: async (v) => show(await ctx.call("show", [v])) });
  filterForm.row("show", filter);
  const table = ui.button("Show the table", { onClick: openTable });
  const toList = ui.button("Put these in the Files list", {
    tip: "The files on show replace the Files list, to go through one by one with the arrow keys.",
    onClick: () => ctx.call("to_files"),
  });
  const exportCsv = ui.button("Export CSV…", { onClick: exportRows });
  const spectraButton = ui.button("Folder spectra", {
    tip: "Each folder's takes averaged into one spectrum, laid over each other or taken from one of them.",
    onClick: openSpectra,
  });
  const timelineButton = ui.button("Timeline", {
    tip: "The takes at the time their names give, one value each, with the gaps between them.",
    onClick: openTimeline,
  });
  results.body.append(filterForm.el, ui.buttons(table, toList, exportCsv),
                      ui.buttons(spectraButton, timelineButton));
  root.append(box.el, results.el,
              ui.hint("Nothing here changes the files. A finding is one of audio_report's; the lines, the voice share and the seams are columns to sort the takes by: a click on a heading sorts by it, the numbers largest first, and a second click turns the order round."));

  const grid = ui.table(info.columns, {
    numeric: [1, 2, 3, 4, 5, 6, 8, 9],
    onSort: async (column) => show(await ctx.call("sort", [column])),
  });
  grid.classList.add("survey-table");
  let current = info;

  async function start() {
    const args = [source.value || "", Number(clipRun.value), lines.value, voice.value,
                  seams.value, memory.detector];
    show(await ctx.call("run", args));
  }

  async function exportRows() {
    const path = await ctx.dialogs.choosePath("save", { title: t("Export the survey"),
                                                        suggested: "survey.csv",
                                                        types: ["CSV (*.csv)"] });
    if (path) await ctx.call("export", [path]);
  }

  function openTable() {
    ctx.view.show(t("Survey"), grid);
    fill();
  }

  // ------------------------------------------------------- folder spectra
  memory.statistic ??= "mean";
  memory.reference ??= "";
  memory.logFreq ??= true;

  async function openSpectra() {
    const page = document.createElement("div");
    page.className = "chart-page";
    const bar = ui.form();
    const statistic = ui.select([["mean", "mean power"], ["median", "median of the takes"]],
                                { value: memory.statistic, translate: true,
                                  onChange: (v) => { memory.statistic = v; draw(); } });
    const reference = ui.select([["", "none"]], { translate: true,
                                                  onChange: (v) => { memory.reference = v; draw(); } });
    const logFreq = ui.check("Log frequency axis", { checked: memory.logFreq,
                                                     onChange: (on) => { memory.logFreq = on; draw(); } });
    bar.row("average", statistic, { tip: "Mean power: the loud takes weigh most, as in a model trained on the folder. Median: the typical take, which one loud take cannot move." });
    bar.row("less", reference, { tip: "Take one folder's spectrum from the others: what one class of recording holds that the other does not." });
    const note = ui.readout("");
    const place = document.createElement("div");
    page.append(bar.el, logFreq, place, note);
    ctx.view.show(t("Folder spectra"), page);

    async function draw() {
      const got = await ctx.call("spectra", [memory.statistic, memory.reference]);
      const options = [["", t("none")], ...got.keys.map((k, i) => [k, got.names[i]])];
      reference.replaceChildren(...options.map(([v, label]) => new Option(label, v)));
      if (!got.keys.includes(memory.reference)) memory.reference = "";
      reference.value = memory.reference;
      const yLabel = got.reference ? t("dB over {}").replace("{}", got.reference)
                                   : t("dB re full scale per Hz");
      place.replaceChildren(chart(got.series, {
        logX: memory.logFreq, xLabel: "Hz", yLabel,
        xTick: (v) => hertz(v), xRead: (v) => `${Math.round(v)} Hz`,
        yRead: (v) => (v === null ? "—" : `${v.toFixed(1)} dB`),
      }));
      note.show(got.note ? t(got.note) : "");
    }
    await draw();
  }

  // ------------------------------------------------------------ timeline
  memory.metric ??= "RMS";
  memory.clock ??= "name";
  memory.pattern ??= "";
  memory.bandLow ??= 0;
  memory.bandHigh ??= 0;

  async function openTimeline() {
    const page = document.createElement("div");
    page.className = "chart-page";
    const bar = ui.form();
    const metric = ui.select(info.metrics, { value: memory.metric, translate: true,
                                             onChange: (v) => { memory.metric = v; showBand(); draw(); } });
    const low = ui.number({ value: memory.bandLow, min: 0, max: 1e6, step: 100, suffix: "Hz",
                            onChange: (v) => { memory.bandLow = v; } });
    const high = ui.number({ value: memory.bandHigh, min: 0, max: 1e6, step: 100, suffix: "Hz",
                             special: "Nyquist", onChange: (v) => { memory.bandHigh = v; } });
    const clock = ui.select([["name", "the file name"], ["modified", "when the file was written"]],
                            { value: memory.clock, translate: true,
                              onChange: (v) => { memory.clock = v; draw(); } });
    const pattern = ui.text({ value: memory.pattern, placeholder: info.time_pattern,
                              onChange: (v) => { memory.pattern = v; } });
    const redraw = ui.button("Draw", { primary: true, onClick: () => draw() });
    bar.row("value", metric);
    const lowRow = bar.row("band from", low);
    const highRow = bar.row("band to", high);
    bar.row("time from", clock, { tip: "The file name: the date and time it holds. When the file was written: the take's end, so its length is taken off." });
    bar.row("name pattern", pattern, { tip: "A regular expression whose groups are year, month, day, hour, minute, second and, if there is one, milliseconds. Empty, it reads 20261111_143111, 2026-11-11T14:31:11 and the like." });
    const summary = ui.readout("");
    const place = document.createElement("div");
    page.append(bar.el, ui.buttons(redraw), place, summary,
                ui.hint("A click on a take opens it. Shaded: a gap, where at least a whole take and two seconds are missing. Ringed: a take with a finding or a seam."));
    ctx.view.show(t("Timeline"), page);

    function showBand() {
      // the box and its name, which the form puts beside it
      for (const row of [lowRow, highRow]) {
        row.hidden = row.previousElementSibling.hidden = memory.metric !== "Band level";
      }
    }

    async function draw() {
      let got;
      try {
        got = await ctx.call("timeline", [memory.metric, memory.clock, memory.pattern,
                                          memory.bandLow, memory.bandHigh]);
      } catch (e) {
        summary.show(t(String(e.message ?? e)), "error");
        return;
      }
      const series = got.series.map((s) => ({ ...s, label: s.paths.map(baseName) }));
      const unit = got.unit ? ` ${t(got.unit)}` : "";
      place.replaceChildren(chart(series, {
        points: true, spans: got.gaps, xLabel: t("time"), yLabel: t(memory.metric) + unit,
        xTicks: clockTicks, xTick: clockTick,
        xRead: (v) => clockText(v, { millis: true }),
        yRead: (v) => (v === null ? "—" : `${v.toFixed(memory.metric === "Lines" ? 0 : 1)}${unit}`),
        onPick: (i, j) => ctx.call("open_path", [series[i].paths[j]]),
      }));
      summary.show(t(got.summary), "ok");
    }
    showBand();
    await draw();
  }

  function fill() {
    // the words in the table, "none" and "repeat", in the window's language
    grid.setRows(current.rows.map((row) => row.map((cell, i) => (i ? t(cell) : cell))),
                 current.flags.map((f) => (f ? "warn" : "")));
    grid.setSorted(...(current.sort ?? [-1, false]));
    [...grid.body.children].forEach((tr, i) => {
      tr.addEventListener("click", () => ctx.call("open_row", [i]));
    });
  }

  function show(state) {
    if (!state) return;
    current = state;
    const options = [["all", `${t("all")} (${state.total})`],
                     ...Object.entries(state.counts).map(([f, n]) => [f, `${t(f)} (${n})`])];
    filter.replaceChildren(...options.map(([v, label]) => new Option(label, v)));
    filter.value = state.filter;
    summary.show(state.summary ? t(state.summary) : "", state.summary ? "ok" : false);
    summary.hidden = !state.summary;
    toList.disabled = exportCsv.disabled = table.disabled = !state.total;
    spectraButton.disabled = timelineButton.disabled = !state.total;
    if (ctx.view.open) fill();
    busy(state.busy);
  }

  function busy(on) {
    run.disabled = on;
    stop.disabled = !on;
    run.textContent = t(on ? "Surveying…" : "Survey");
    if (!on) progress.show("");
  }

  show(info);

  return {
    event(e) { if (e.info) show(e.info); },
    busy(on) { busy(on); },
    progress(text) { progress.show(t(text)); },
  };
}
