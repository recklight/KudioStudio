// Batch: a folder converted, levelled, denoised and re-roomed in one Run,
// with every file that missed the loudness target listed afterwards.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.rate ??= info.rates[0];
  memory.subtype ??= info.subtypes[0];
  memory.mono ??= true;
  memory.trim ??= false;
  memory.topDb ??= 30;
  memory.normalize ??= "none";
  memory.target ??= null;          // the mode's own default until one is typed
  memory.tolerance ??= info.tolerance;
  memory.ceiling ??= info.ceiling;
  memory.denoise ??= "";
  memory.noise ??= info.noise[0][0];
  memory.room ??= false;
  memory.rt60 ??= 0.5;
  memory.drr ??= 0;
  memory.seed ??= 0;

  const box = ui.section("Batch conversion");
  const form = ui.form();
  const folder = (placeholder, key) => {
    const field = ui.path({ kind: "folder", title: placeholder, placeholder, readOnly: false,
                            onChange: (p) => { memory[key] = p; changed(); } });
    if (memory[key]) field.value = memory[key];
    return field;
  };
  const src = folder("input folder", "src");
  const dst = folder("output folder", "dst");
  const rate = ui.select(info.rates, { value: memory.rate, onChange: changed });
  const subtype = ui.select(info.subtypes, { value: memory.subtype, onChange: changed });
  const topDb = ui.number({ value: memory.topDb, min: 1, max: 90, decimals: 2, suffix: " dB",
                            onChange: changed });

  const normalize = ui.select(Object.keys(info.normalize),
                              { value: memory.normalize, onChange: onNormalize });
  const target = ui.number({ value: memory.target ?? 0, min: -60, max: 1, step: 0.01,
                             decimals: 2, onChange: changed });
  normalize.classList.add("short");
  const normalizeRow = document.createElement("div");
  normalizeRow.className = "inline-controls";
  normalizeRow.append(normalize, target);

  const tolerance = ui.number({ value: memory.tolerance, min: 0.1, max: 6, step: 0.1,
                                decimals: 1, suffix: " LU", onChange: changed });
  const ceiling = ui.number({ value: memory.ceiling, min: -20, max: 0, step: 0.5, decimals: 1,
                              suffix: " dBTP", onChange: changed });
  // one line each: side by side, the two boxes are too narrow to read
  const word = (text) => Object.assign(document.createElement("span"),
                                       { className: "inline-word", textContent: text });
  const line = (...items) => {
    const row = document.createElement("div");
    row.className = "inline-controls";
    row.append(...items);
    return row;
  };
  const checkRow = document.createElement("div");
  checkRow.className = "stacked-controls";
  checkRow.append(line(word(`${t("within")} ±`), tolerance),
                  line(word(t("true peak ≤")), ceiling));

  const denoise = ui.select([["", "off"], ...info.denoise],
                            { value: memory.denoise, translate: true, onChange: changed });
  const noise = ui.select(info.noise.map(([name]) => name), { value: memory.noise,
                                                              onChange: changed });
  info.noise.forEach(([, summary], i) => { noise.options[i].title = summary; });
  const denoiseRow = document.createElement("div");
  denoiseRow.className = "inline-controls";
  denoiseRow.append(denoise, noise);

  form.row("input", src);
  form.row("output", dst);
  form.row("sr", rate);
  form.row("subtype", subtype);
  form.row("top_db", topDb);
  form.row("normalize", normalizeRow, { tip: info.tips.normalize });
  form.row("check", checkRow);
  form.row("denoise", denoiseRow, { tip: info.tips.denoise });
  tolerance.title = t(info.tips.tolerance);
  ceiling.title = t(info.tips.ceiling);

  const mono = ui.check("downmix to mono", { checked: memory.mono, onChange: changed });
  const trim = ui.check("trim leading/trailing silence", { checked: memory.trim, onChange: changed });

  // the reverberant copy: a box of its own that is either on or not there
  const room = ui.check("Reverberant copy — keep the dry tree too",
                        { checked: memory.room, onChange: changed });
  room.title = t(info.tips.room);
  const roomForm = ui.form();
  const rt60 = ui.number({ value: memory.rt60, min: 0.05, max: 5, step: 0.05, decimals: 2,
                           suffix: " s", onChange: changed });
  const drr = ui.number({ value: memory.drr, min: -20, max: 30, step: 1, decimals: 1,
                          suffix: " dB", onChange: changed });
  const seed = ui.number({ value: memory.seed, min: -1, max: 99999, special: "random",
                           onChange: changed });
  const roomDst = folder(`beside the output: <output>${info.suffix}`, "roomDst");
  roomForm.row("rt60", rt60, { tip: info.tips.rt60 });
  roomForm.row("drr_db", drr, { tip: info.tips.drr });
  roomForm.row("seed", seed, { tip: info.tips.seed });
  roomForm.row("to", roomDst);
  const roomBox = document.createElement("div");
  roomBox.className = "sub-box";
  roomBox.append(room, roomForm.el);

  const call = ui.callText();
  const run = ui.button("Run", { primary: true, onClick: start });
  box.body.append(form.el, mono, trim, roomBox, call, ui.buttons(run));

  const result = ui.readout("—");
  const misses = document.createElement("pre");
  misses.className = "misses mono";
  root.append(box.el, result, misses, ...info.hints.map((h) => ui.hint(h)));

  const settings = () => ({
    src: src.value, dst: dst.value, rate: rate.value, subtype: subtype.value,
    mono: mono.value, trim: trim.value, top_db: topDb.value,
    normalize: normalize.value, target: target.value,
    tolerance: tolerance.value, ceiling: ceiling.value,
    denoise: denoise.value || null, noise: noise.value,
    room: room.value, rt60: rt60.value, drr_db: drr.value, seed: seed.value,
    room_dst: roomDst.value,
  });

  function onNormalize() {
    const spec = info.normalize[normalize.value];
    if (spec) {
      const [[lo, hi], fallback, suffix] = spec;
      // a peak of -23 or a loudness of 0.99 are both nonsense, so the box
      // starts over at the mode's own default
      target.setRange(lo, hi, { value: fallback, suffix });
    }
    changed();
  }

  function sync() {
    topDb.disabled = !trim.value;
    target.disabled = !info.normalize[normalize.value];
    // the read-back only has a target to hold files to in LUFS
    tolerance.disabled = ceiling.disabled = normalize.value !== "LUFS";
    noise.disabled = !denoise.value;
    for (const control of [rt60, drr, seed, roomDst]) control.disabled = !room.value;
  }

  async function changed() {
    sync();
    Object.assign(memory, {
      rate: rate.value, subtype: subtype.value, mono: mono.value, trim: trim.value,
      topDb: topDb.value, normalize: normalize.value, target: target.value,
      tolerance: tolerance.value, ceiling: ceiling.value, denoise: denoise.value,
      noise: noise.value, room: room.value, rt60: rt60.value, drr: drr.value, seed: seed.value,
    });
    call.text = await ctx.call("call_text", [settings()]).catch(() => "");
  }

  async function start() {
    show(await ctx.call("run", [settings()]));
  }

  function show(state) {
    if (!state) return;
    result.show(state.result);
    misses.textContent = state.misses;
    misses.hidden = !state.misses;
  }

  function busy(on, label) {
    run.disabled = on;
    run.textContent = t(on ? "Running…" : "Run");
    box.setBusy(on ? label : "");
  }

  if (info.normalize[memory.normalize]) {
    const [[lo, hi], fallback, suffix] = info.normalize[memory.normalize];
    target.setRange(lo, hi, { value: memory.target ?? fallback, suffix });
  }
  await changed();
  show(info);
  busy(info.busy, "Running…");

  return {
    state(s) { busy(s.busy.includes("batch"), "Running…"); },
    event(e) { if (e.info) show(e.info); },
    busy,
  };
}
