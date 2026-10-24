// Enhance: denoise the whole clip -- a statistical method from kudio, the
// two older filters, or a learned model from kudioenhance -- and say how far
// that moved it.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.method ??= info.statistical[0].name;
  memory.values ??= {};

  const box = ui.section("Denoise");
  const top = ui.form();
  const method = document.createElement("select");
  method.className = "mono";
  for (const m of info.statistical) {
    const option = new Option(m.label, m.name);
    option.title = t(m.tip);
    method.append(option);
  }
  method.append(Object.assign(new Option("──────────", ""), { disabled: true }));
  for (const m of info.others) method.append(new Option(t(m.label), m.name));
  method.value = memory.method;
  top.row("Method", method);

  // the controls of each kind of method; only the chosen kind's are shown
  const statistical = ui.form();
  const noise = ui.select(info.noise.map((n) => n.name), {
    value: memory.values.noise ?? info.noise[0].name, onChange: changed,
  });
  const noiseHint = ui.hint("");
  const nFft = ui.select(info.fft_sizes, { value: String(memory.values.n_fft ?? 512), onChange: changed });
  statistical.row("noise estimate", noise);
  statistical.row("", noiseHint, { wide: true });
  statistical.row("n_fft", nFft, { tip: info.n_fft_tip });
  const methodParams = document.createElement("div");

  const wavelet = ui.form();
  const thres = ui.number({ value: memory.values.thres ?? 0.8, min: 0, max: 5, step: 0.05,
                            decimals: 2, onChange: changed });
  const waveletName = ui.select(info.wavelets, { value: memory.values.wavelet ?? info.wavelets[0],
                                                 onChange: changed });
  wavelet.row("thres", thres);
  wavelet.row("wavelet", waveletName);

  const learned = ui.form();
  const runDir = ui.path({ kind: "folder", title: "Select a kudioenhance run directory",
                           placeholder: "run directory from kudioenhance, e.g. runs/exp1",
                           readOnly: false, onChange: changed });
  if (memory.values.run_dir) runDir.value = memory.values.run_dir;
  const resample = ui.check("resample to the model's rate if they differ", {
    checked: memory.values.resample ?? true, onChange: changed,
  });
  resample.title = t(info.resample_tip);
  learned.row("run_dir", runDir);
  learned.row("", resample);

  const call = ui.callText();
  const preview = ui.button("♪  Preview", {
    toggle: true,
    tip: "Hear the denoise without committing it. Runs on the whole clip,\nexactly as Denoise will, then loops the selected part.",
    onClick: (_, on) => (on ? startPreview() : stopPreview()),
  });
  const run = ui.button("Denoise", { primary: true, onClick: denoise });
  const undo = ui.button("Undo", { onClick: () => ctx.window("undo") });
  box.body.append(top.el, statistical.el, methodParams, wavelet.el, learned.el, call,
                  ui.buttons(preview, run, undo));

  const scores = ui.section("How much it changed the input");
  const result = ui.readout("—");
  scores.body.append(result);
  const note = ui.hint("");
  root.append(box.el, scores.el,
    ui.hint("Denoising always processes the whole clip — noise estimation needs the full signal. Preview does the same, then loops whatever is selected, and commits nothing."),
    note);

  let params = null;
  let previewing = false;
  let timer = 0;

  const kind = () => (info.statistical.some((m) => m.name === method.value) ? "statistical"
    : method.value);

  function values() {
    const v = { noise: noise.value, n_fft: Number(nFft.value), thres: thres.value,
                wavelet: waveletName.value, run_dir: runDir.value, resample: resample.value };
    if (params) Object.assign(v, params.values());
    return v;
  }

  function choose() {
    memory.method = method.value;
    const k = kind();
    statistical.el.hidden = k !== "statistical";
    methodParams.hidden = k !== "statistical";
    wavelet.el.hidden = k !== "wavelet";
    learned.el.hidden = k !== "learned";
    params = null;
    methodParams.replaceChildren();
    const spec = info.statistical.find((m) => m.name === method.value);
    if (spec && spec.params.length) {
      params = ui.params(spec.params, { onChange: changed });
      params.set(memory.values[`params:${spec.name}`]);
      methodParams.append(params.el);
    }
    changed();
  }

  async function changed() {
    const v = values();
    Object.assign(memory.values, { noise: v.noise, n_fft: v.n_fft, thres: v.thres,
                                   wavelet: v.wavelet, run_dir: v.run_dir, resample: v.resample });
    if (params) memory.values[`params:${method.value}`] = params.values();
    noiseHint.textContent = t(info.noise.find((n) => n.name === noise.value)?.tip ?? "");
    call.text = await ctx.call("call_text", [method.value, v]).catch(() => "");
    // a denoise is seconds, not milliseconds: wait for the controls to settle
    if (previewing) {
      clearTimeout(timer);
      timer = setTimeout(startPreview, 400);
    }
  }

  async function startPreview() {
    const answer = await ctx.call("preview", [method.value, values()]).catch(() => ({ on: false }));
    previewing = Boolean(answer.on);
    preview.on = previewing;
  }

  function stopPreview() {
    previewing = false;
    preview.on = false;
    clearTimeout(timer);
    ctx.call("stop_preview");
  }

  async function denoise() {
    previewing = false;
    preview.on = false;
    show(await ctx.call("denoise", [method.value, values()]));
  }

  function show(state) {
    if (!state) return;
    result.show(t(state.result));
    note.textContent = t(state.note);
    busy(state.busy);
  }

  function busy(on) {
    run.disabled = on;
    run.textContent = t(on ? "Denoising…" : "Denoise");
  }

  method.addEventListener("change", choose);
  choose();
  show(info);

  return {
    state(s) {
      if (!s.busy.includes("enhance")) run.disabled = !s.has_audio;
      undo.disabled = !s.can_undo;
    },
    event(e) {
      if (e.info) show(e.info);
      if (e.preview === false) { previewing = false; preview.on = false; }
    },
    busy(on) { busy(on); },
    preview(on) { if (!on) { previewing = false; preview.on = false; } },
  };
}
