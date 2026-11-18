// Metrics: what can be said about a recording with no clean version of it,
// and what can be measured against one when there is.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.reference ??= info.references[0];
  memory.pesq ??= info.pesq_scales[0];
  memory.align ??= true;

  // ------------------------------------------------------- no reference
  const free = ui.section("No reference needed");
  const inspect = ui.button("Inspect this audio", { onClick: async () => show(await ctx.call("inspect")) });
  const inspected = ui.readout("");
  const blind = ui.button("Estimate reverberation and SNR (SRMR, WADA-SNR)", {
    tip: info.blind_tip,
    onClick: async () => show(await ctx.call("blind")),
  });
  const blindCall = ui.callText();
  blindCall.text = "kudio.srmr(y, sr)  +  kudio.wada_snr(y, sr)";
  const dnsmos = ui.button("Predict opinion score (DNSMOS)", {
    tip: info.dnsmos_tip,
    onClick: async () => show(await ctx.call("dnsmos")),
  });
  const scored = ui.readout("");
  const reportCall = ui.callText();
  reportCall.text = "kudio.audio_report(y, sr)";
  const dnsmosCall = ui.callText();
  dnsmosCall.text = "kudio.dnsmos(y, sr)";
  free.body.append(
    ui.hint("Clipping, level, DC offset, noise floor and the bandwidth the content really occupies — the questions you can still ask about a recording that has no clean version anywhere."),
    reportCall, ui.buttons(inspect), inspected, blindCall, ui.buttons(blind),
    dnsmosCall, ui.buttons(dnsmos), scored);

  // ------------------------------------------------------ with reference
  const against = ui.section("Against a clean reference");
  const form = ui.form();
  const file = ui.path({ kind: "open", title: "Select the clean reference",
                         types: ["Audio (*.wav;*.flac;*.mp3;*.ogg;*.aiff;*.aif)"],
                         placeholder: "no reference file selected",
                         onChange: (p) => { memory.path = p; } });
  if (memory.path) file.value = memory.path;
  const reference = ui.select(info.references, {
    value: memory.reference, translate: true,
    onChange: (v) => { memory.reference = v; sync(); },
  });
  const pesq = ui.select(info.pesq_scales, { value: memory.pesq, onChange: (v) => { memory.pesq = v; } });
  form.row("Reference", reference);
  form.row("File", file);
  form.row("PESQ scale", pesq, { tip: info.pesq_tip });
  const align = ui.check("Line up with the reference first", {
    checked: memory.align, onChange: (on) => { memory.align = on; },
  });
  align.title = t(info.align_tip);
  const evaluate = ui.button("Evaluate", {
    primary: true,
    onClick: async () => show(await ctx.call("evaluate",
      [reference.value, file.value, align.value, pesq.value])),
  });
  const call = ui.callText();
  // find_delay rather than kudio.align: the panel reports the offset even
  // when it is not taken out, and goes on where kudio.align would refuse
  call.text = "kudio.find_delay(ref, deg, sr).apply(ref, deg)  +  kudio.snr / si_sdr / segmental_snr  +  kudio.eval_metrics(sr, ref, deg, pesq_scale=...)  +  kudio.pesq on the pair as loaded  +  kudio.llr / itakura_saito / cepstral_distance / wss / fw_segmental_snr / lsd  +  kudio.composite(ref, deg, sr, pesq=...)  +  kudio.ncm / csii";
  against.body.append(form.el, align, ui.buttons(evaluate), call);

  const table = ui.table(["Metric", "Value"], { numeric: [1] });
  const availability = ui.hint(info.availability);
  root.append(free.el, against.el, table, availability);

  function sync() {
    const byFile = reference.value === info.references[1];
    file.querySelector("input").disabled = !byFile;
    file.button.disabled = !byFile;
  }

  function show(state) {
    if (!state) return;
    table.setRows(state.rows.map(([name, value]) => [t(name), t(value)]),
                  state.rows.map(([name]) => (name.startsWith("⚠") ? "warn" : "")));
    // already in the window's language: each finding was translated on its own
    inspected.hidden = !state.inspect;
    if (state.inspect) inspected.show(state.inspect[0], state.inspect[1] ? "warn" : "");
    scored.hidden = !state.dnsmos;
    if (state.dnsmos) scored.show(t(state.dnsmos[0]), state.dnsmos[1] ? "warn" : "");
  }

  sync();
  show(info);
  // From here on the job events say when the buttons work. The answer to a
  // click says "busy" as the job starts, and a job that fails at once, as
  // DNSMOS does without its models, has ended before that answer arrives.
  evaluate.disabled = info.busy;

  return {
    state(s) {
      inspect.disabled = !s.has_audio;
      blind.disabled = !s.has_audio || s.busy.includes("metrics");
      dnsmos.disabled = !s.has_audio || s.busy.includes("metrics");
      evaluate.disabled = !s.has_audio || s.busy.includes("metrics");
    },
    event(e) { if (e.info) show(e.info); },
    busy(on) {
      evaluate.disabled = on;
      blind.disabled = on;
      dnsmos.disabled = on;
    },
  };
}
