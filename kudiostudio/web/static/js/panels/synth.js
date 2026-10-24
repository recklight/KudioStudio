// Synthesize: a noisy corpus from a folder of clean speech and a folder of
// noise, through an optional room and an optional link. Nothing here touches
// the open audio; the run reads folders and writes one.

export async function mount(root, ctx) {
  const { ui } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.snr ??= "-5, 0, 5";
  memory.rt60 ??= "";
  memory.drr ??= 0;
  memory.subtype ??= info.subtypes[0];
  memory.channel ??= info.links[0];
  memory.mode ??= info.modes[0];
  memory.seed ??= -1;
  memory.targets ??= false;
  memory.addClean ??= false;
  memory.overwrite ??= true;

  const box = ui.section("kudio.Synthesizer");
  const form = ui.form();
  const folder = (placeholder, key) => {
    const field = ui.path({ kind: "folder", title: placeholder, placeholder, readOnly: false,
                            onChange: (p) => { memory[key] = p; changed(); } });
    if (memory[key]) field.value = memory[key];
    return field;
  };
  const clean = folder("folder of clean audio", "clean");
  const noise = folder("folder of noise audio", "noise");
  const out = folder("output folder for the mixture", "out");
  const snr = ui.text({ value: memory.snr, onChange: changed });
  const rt60 = ui.text({ value: memory.rt60, placeholder: "no rooms — e.g. 0.3, 0.6, 0.9",
                         onChange: changed });
  const drr = ui.number({ value: memory.drr, min: -20, max: 30, step: 1, decimals: 1,
                          suffix: " dB", onChange: changed });
  const subtype = ui.select(info.subtypes, { value: memory.subtype, onChange: changed });
  const channel = ui.select(info.links, { value: memory.channel, onChange: changed });
  const mode = ui.select(info.modes, { value: memory.mode, onChange: changed });
  const seed = ui.number({ value: memory.seed, min: -1, max: 99999, special: "None",
                           onChange: changed });
  form.row("clean", clean);
  form.row("noise", noise);
  form.row("out_path", out);
  form.row("snr_ratio", snr, { tip: info.tips.snr });
  form.row("rt60", rt60, { tip: info.tips.rt60 });
  form.row("drr_db", drr, { tip: info.tips.drr });
  form.row("subtype", subtype, { tip: info.tips.subtype });
  form.row("channel", channel, { tip: info.tips.channel });
  form.row("mode", mode, { tip: info.tips.mode });
  form.row("seed", seed);

  const targets = ui.check("also write reverberant targets (TARGETS/)",
                           { checked: memory.targets, onChange: changed });
  targets.title = ctx.t(info.tips.targets);
  const addClean = ui.check("also copy the clean files (is_add_clean)",
                            { checked: memory.addClean, onChange: changed });
  const overwrite = ui.check("overwrite existing files",
                             { checked: memory.overwrite, onChange: changed });
  const call = ui.callText();
  const run = ui.button("Synthesize", { primary: true, onClick: start });
  const result = ui.readout("—");
  box.body.append(form.el, targets, addClean, overwrite, call, ui.buttons(run));
  root.append(box.el, result, ui.hint(info.seed_note));

  const values = () => [clean.value, noise.value, out.value, snr.value, rt60.value,
                        drr.value, subtype.value, channel.value, targets.value,
                        mode.value, seed.value];

  async function changed() {
    Object.assign(memory, {
      snr: snr.value, rt60: rt60.value, drr: drr.value, subtype: subtype.value,
      channel: channel.value, mode: mode.value, seed: seed.value,
      targets: targets.value, addClean: addClean.value, overwrite: overwrite.value,
    });
    call.text = await ctx.call("call_text", values()).catch(() => "");
  }

  async function start() {
    show(await ctx.call("run", [...values(), addClean.value, overwrite.value]));
  }

  function show(state) {
    if (!state) return;
    result.show(state.result);
  }

  function busy(on, label) {
    run.disabled = on;
    run.textContent = ctx.t(on ? "Synthesizing…" : "Synthesize");
    box.setBusy(on ? label : "");
  }

  await changed();
  show(info);
  busy(info.busy, "Synthesizing…");

  return {
    state(s) { busy(s.busy.includes("synthesize"), "Synthesizing…"); },
    event(e) { if (e.info) show(e.info); },
    busy,
  };
}
