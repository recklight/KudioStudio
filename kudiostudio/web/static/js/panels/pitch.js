// Pitch: kudio.f0 (pYIN) over the clip or the selection, drawn over the
// spectrogram, with the voiced spans one button away from the marker list.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.range ??= 0;
  memory.fmin ??= info.ranges[0].fmin;
  memory.fmax ??= info.ranges[0].fmax;
  memory.frame ??= info.frame_lengths[1];
  memory.hop ??= 10;

  const custom = info.ranges.length - 1;
  let syncing = false;                // a preset writing the bounds is not typing

  const box = ui.section("kudio.f0 (pYIN)");
  const form = ui.form();
  const range = ui.select(info.ranges.map((r, i) => [String(i), r.label]),
                          { value: String(memory.range), translate: true, onChange: onRange });
  const fmin = ui.number({ value: memory.fmin, min: info.fmin_range[0], max: info.fmin_range[1],
                           suffix: " Hz", onChange: onBound });
  const fmax = ui.number({ value: memory.fmax, min: info.fmax_range[0], max: info.fmax_range[1],
                           suffix: " Hz", onChange: onBound });
  const frame = ui.select(info.frame_lengths.map(String),
                          { value: String(memory.frame), onChange: changed });
  const hop = ui.number({ value: memory.hop, min: info.hop_ms_range[0], max: info.hop_ms_range[1],
                          suffix: " ms", onChange: changed });
  form.row("range", range);
  form.row("fmin", fmin);
  form.row("fmax", fmax);
  form.row("frame_length", frame, { tip: info.frame_tip });
  form.row("hop", hop);

  const overlay = ui.check("Draw the contour over the spectrogram", {
    checked: info.overlay,
    onChange: async (on) => show(await ctx.call("set_overlay", [on])),
  });
  overlay.title = t("Only on the STFT view — on a mel or MFCC image the vertical axis\nis a band index, so a curve in Hz would land in the wrong place.");
  const call = ui.callText();
  const run = ui.button("Track pitch", { primary: true, onClick: track });
  const markers = ui.button("Voiced spans → markers", {
    tip: "Add each voiced run to the marker list, where it can be renamed,\nsaved beside the file, or exported as a label track.",
    onClick: async () => show(await ctx.call("add_markers")),
  });
  const scope = document.createElement("p");
  scope.className = "scope-line";
  const result = ui.readout("");

  box.body.append(form.el, overlay, call, ui.buttons(run, markers), scope, result);
  root.append(box.el, ui.hint("A selection is tracked on its own if there is one, so you can ask about one phrase rather than the whole take. The contour is measured from the audio as it is now — any edit drops it, because a curve left on screen describing audio that has been replaced is worse than no curve."));

  const params = () => [fmin.value, fmax.value, Number(frame.value), hop.value];

  function onRange(value) {
    const index = Number(value);
    memory.range = index;
    const preset = info.ranges[index];
    if (index !== custom) {
      syncing = true;
      fmin.value = preset.fmin;
      fmax.value = preset.fmax;
      syncing = false;
    }
    changed();
  }

  function onBound() {
    // typing a bound is a way of choosing Custom, not of fighting it
    if (!syncing && Number(range.value) !== custom) {
      range.value = String(custom);
      memory.range = custom;
    }
    changed();
  }

  async function changed() {
    [memory.fmin, memory.fmax, memory.frame, memory.hop] = params();
    call.text = await ctx.call("call_text", params()).catch(() => "");
  }

  async function track() {
    show(await ctx.call("track", params()));
  }

  function show(state) {
    if (!state) return;
    scope.textContent = state.scope ? t(state.scope) : "";
    result.hidden = !state.result;
    result.show(state.result ? t(state.result) : "", state.ok ? "ok" : "warn");
    markers.disabled = !state.has_track;
    busy(state.busy);
  }

  function busy(on) {
    run.disabled = on;
    run.textContent = t(on ? "Tracking…" : "Track pitch");
  }

  show(info);
  changed();

  return {
    state(s) {
      if (!s.busy.includes("pitch")) run.disabled = !s.has_audio;
    },
    event(e) { if (e.info) show(e.info); },
    busy(on) { busy(on); },
  };
}
