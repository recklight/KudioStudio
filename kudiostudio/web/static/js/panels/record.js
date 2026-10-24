// Record: a fixed-length capture, or an open-ended take with a live meter
// and the plots rolling while it runs.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.mode ??= info.modes[0];
  memory.sr ??= 16000;
  memory.channels ??= 1;
  memory.seconds ??= 3;
  memory.monitor ??= "";

  const box = ui.section("kudio.record / kudio.StreamRecorder");
  const form = ui.form();
  const mode = ui.select(info.modes, { value: memory.mode, translate: true, onChange: onMode });
  const device = document.createElement("select");
  device.className = "mono";
  device.addEventListener("change", () => { memory.device = device.value; changed(); });
  const space = ui.hint("");
  // any rate can be typed; the usual ones are offered
  const rate = document.createElement("input");
  rate.className = "num mono";
  rate.setAttribute("list", "record-rates");
  rate.value = memory.sr;
  const rates = document.createElement("datalist");
  rates.id = "record-rates";
  for (const r of info.rates) rates.append(new Option(String(r)));
  rate.addEventListener("change", changed);
  const channels = ui.number({ value: memory.channels, min: 1, max: 8, onChange: changed });
  const seconds = ui.number({ value: memory.seconds, min: 0.1, max: 3600, step: 0.5, decimals: 1,
                              suffix: " s", onChange: changed });
  const meter = document.createElement("div");
  meter.className = "meter-row";
  meter.innerHTML = `<div class="meter"><span class="meter-fill"></span><span class="meter-text mono"></span></div>` +
    `<span class="elapsed mono">0.00 s</span>`;
  const monitor = document.createElement("select");
  monitor.className = "mono";
  for (const m of info.monitor_methods) {
    monitor.append(Object.assign(new Option(m.name ? m.label : t(m.label), m.name),
                                 { title: t(m.tip) }));
  }
  monitor.value = memory.monitor;
  monitor.title = t(info.monitor_tip);
  monitor.addEventListener("change", () => { memory.monitor = monitor.value; });
  const monitorText = ui.hint("");

  form.row("Mode", mode);
  form.row("Device", device);
  form.row("", space, { wide: true });
  form.row("sr", rate);
  form.row("channels", channels);
  form.row("seconds", seconds);
  form.row("level", meter);
  form.row("monitor through", monitor);
  form.row("", monitorText, { wide: true });

  const call = ui.callText();
  const record = ui.button("● Record", { primary: true, onClick: go });
  const refresh = ui.button("Refresh devices", { onClick: () => loadDevices() });
  const take = ui.readout("");
  box.body.append(form.el, rates, call, ui.buttons(record, refresh), take);
  root.append(box.el, ui.hint(info.note));

  let streaming = info.streaming;
  const isStream = () => mode.value === info.modes[1];

  function params() {
    const sr = Number.parseInt(rate.value, 10);
    return {
      mode: mode.value,
      seconds: seconds.value,
      sr: Number.isFinite(sr) && sr > 0 ? sr : 16000,
      channels: channels.value,
      device: device.value === "" ? null : Number(device.value),
      monitor: monitor.value,
    };
  }

  async function loadDevices() {
    const answer = await ctx.call("devices", [isStream()]);
    const keep = memory.device ?? "";
    device.replaceChildren();
    for (const [label, index] of answer.devices) {
      device.append(new Option(t(label), index === null ? "" : String(index)));
    }
    if ([...device.options].some((o) => o.value === keep)) device.value = keep;
    space.textContent = t(answer.space);
    changed();
  }

  function onMode() {
    memory.mode = mode.value;
    syncButtons();
    loadDevices();
  }

  async function changed() {
    const p = params();
    Object.assign(memory, { sr: p.sr, channels: p.channels, seconds: p.seconds });
    call.text = await ctx.call("call_text", [isStream(), p.seconds, p.sr, p.channels, p.device])
      .catch(() => "");
  }

  function syncButtons(busy = false) {
    const stream = isStream();
    seconds.input.disabled = stream;
    // only an open take has a stream to monitor, and its method is fixed
    // while it runs
    monitor.disabled = !stream || streaming;
    mode.disabled = streaming;
    record.classList.toggle("recording", streaming || busy);
    record.textContent = t(streaming ? "■ Stop" : busy ? "Recording…" : stream ? "● Start" : "● Record");
    record.disabled = busy && !streaming;
  }

  async function go() {
    const p = params();
    show(await ctx.call("record", [p.mode, p.seconds, p.sr, p.channels, p.device, p.monitor]));
  }

  function level(db, elapsed) {
    const floor = info.meter_floor;
    const share = Math.max(0, Math.min(1, (db - floor) / -floor));
    meter.querySelector(".meter-fill").style.width = `${(share * 100).toFixed(1)}%`;
    meter.querySelector(".meter-text").textContent = `${Math.round(db)} dBFS`;
    meter.querySelector(".elapsed").textContent = `${elapsed.toFixed(2)} s`;
  }

  function show(state) {
    if (!state) return;
    streaming = state.streaming;
    take.hidden = !state.take[0];
    take.show(t(state.take[0]), state.take[1]);
    monitorText.textContent = t(state.monitor[0]);
    monitorText.classList.toggle("warn", state.monitor[1] === "warn");
    syncButtons(state.busy);
  }

  level(info.meter_floor, 0);
  show(info);
  await loadDevices();

  return {
    event(e) {
      if (e.info) show(e.info);
      if ("meter" in e) level(e.meter, e.elapsed);
      if (e.monitor_text) monitorText.textContent = t(e.monitor_text);
    },
    busy(on) { syncButtons(on); },
  };
}
