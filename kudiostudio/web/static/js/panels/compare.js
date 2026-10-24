// Compare: every enhancement method on the same clip, ranked, with any row
// (or two, back to back) to hear before one is applied.

/** The verdict holds <b> and <br> and nothing else; everything in it is
 *  escaped first, then those two are let back in. */
function markup(text) {
  const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped.replace(/&lt;(\/?b|br)&gt;/g, "<$1>");
}

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.reference ??= info.references[0];
  memory.sweep ??= info.sweeps[0];
  memory.method ??= info.default_method;
  memory.noise ??= info.noises[0].name;
  memory.sort ??= null;               // [column, descending], or null for the default

  // ---------------------------------------------------------- the set-up
  const setup = ui.section("kudio.compare_enhancers");
  const form = ui.form();
  const reference = ui.select(info.references, { value: memory.reference, translate: true,
                                                 onChange: (v) => { memory.reference = v; sync(); } });
  const file = ui.path({ kind: "open", title: "Select the clean reference",
                         types: ["Audio (*.wav;*.flac;*.mp3;*.ogg)"],
                         placeholder: "no reference file selected",
                         onChange: (p) => { memory.path = p; } });
  if (memory.path) file.value = memory.path;
  const sweep = ui.select(info.sweeps, { value: memory.sweep, translate: true,
                                         onChange: (v) => { memory.sweep = v; sync(); } });
  const method = document.createElement("select");
  method.className = "mono";
  for (const m of info.methods) method.append(Object.assign(new Option(m.label, m.name), { title: t(m.tip) }));
  method.value = memory.method;
  method.addEventListener("change", () => { memory.method = method.value; sync(); });
  const noise = document.createElement("select");
  noise.className = "mono";
  for (const n of info.noises) noise.append(Object.assign(new Option(n.name, n.name), { title: t(n.tip) }));
  noise.value = memory.noise;
  noise.addEventListener("change", () => { memory.noise = noise.value; sync(); });
  const runDir = ui.path({ kind: "folder", title: "Select a kudioenhance run directory",
                           placeholder: "optional: a kudioenhance run directory",
                           readOnly: false, onChange: (p) => { memory.runDir = p; sync(); } });
  if (memory.runDir) runDir.value = memory.runDir;
  form.row("Reference", reference);
  form.row("File", file);
  form.row("Compare", sweep, { tip: info.tips.sweep });
  form.row("Gain rule", method);
  form.row("Noise estimate", noise);
  form.row("Learned model", runDir, { tip: info.tips.run_dir });
  const metrics = ui.check(info.metrics_text, { checked: memory.metrics ?? false,
                                               onChange: (on) => { memory.metrics = on; sync(); } });
  const selectionOnly = ui.check("compare only the selected range", {
    checked: memory.selectionOnly ?? false, onChange: (on) => { memory.selectionOnly = on; },
  });
  selectionOnly.title = t(info.tips.selection);
  const call = ui.callText();
  const run = ui.button("Run comparison", { primary: true, onClick: start });
  setup.body.append(form.el, metrics, selectionOnly, call, ui.buttons(run));

  // ------------------------------------------------------------ the table
  const wrap = document.createElement("div");
  wrap.className = "table-wrap compare-table";
  const table = document.createElement("table");
  table.className = "data selectable";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const body = document.createElement("tbody");
  info.columns.forEach((name, column) => {
    const th = document.createElement("th");
    th.textContent = t(name);
    th.className = column >= 2 ? "num sortable" : "sortable";
    th.addEventListener("click", () => {
      const [current, descending] = memory.sort ?? [-1, false];
      const heard = JSON.stringify(chosen());
      memory.sort = [column, current === column ? !descending : column >= 5];
      render();
      // the loop plays the rows top to bottom, so a sort that moved them
      // plays them again in their new order; one that did not, leaves it be
      if (auditioning && JSON.stringify(chosen()) !== heard) play();
    });
    headRow.append(th);
  });
  head.append(headRow);
  table.append(head, body);
  wrap.append(table);

  const audition = ui.button("♪  Audition", { toggle: true, onClick: (_, on) => (on ? play() : stop()) });
  const apply = ui.button("Apply this one", { onClick: applyRow });
  const saveAll = ui.button("Save all…", {
    onClick: async () => {
      const folder = await ctx.dialogs.choosePath("folder", { title: t("Save every result to") });
      if (folder) show(await ctx.call("save_all", [folder]));
    },
  });
  const verdict = document.createElement("p");
  verdict.className = "verdict";
  root.append(setup.el, wrap, ui.buttons(audition, apply, saveAll), verdict, ui.hint(info.note));

  let state = info;
  let selected = new Set();           // "method|noise"
  let anchor = null;                  // for Shift-click ranges
  let auditioning = false;

  const keyOf = (row) => row.key.join("|");

  function order() {
    const rows = [...state.rows];
    let sort = memory.sort;
    // a ranking table opens ranked: best first with a reference, kudio's
    // order without one, since then there is nothing to rank by
    if (!sort) sort = state.has_reference ? [info.columns.indexOf("SI-SDR"), true] : [0, false];
    const [column, descending] = sort;
    rows.sort((a, b) => {
      const x = a.sort[column];
      const y = b.sort[column];
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return descending ? y - x : x - y;
    });
    return rows;
  }

  function render() {
    const rows = order();
    body.replaceChildren();
    const hidden = new Set(state.has_reference ? [] : info.reference_columns);
    [...headRow.children].forEach((th, i) => { th.hidden = hidden.has(i); });
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.classList.toggle("selected", selected.has(keyOf(row)));
      tr.classList.toggle("warn", row.error);
      row.cells.forEach((cell, i) => {
        const td = document.createElement("td");
        td.textContent = i === 0 ? t(cell) : cell;
        td.className = i >= 2 ? "num mono" : "";
        td.hidden = hidden.has(i);
        tr.append(td);
      });
      tr.addEventListener("click", (e) => pick(row, rows, e));
      body.append(tr);
    }
    const usable = rows.filter((r) => selected.has(keyOf(r)) && !r.error);
    audition.disabled = usable.length === 0;
    audition.textContent = t(usable.length > 1 ? "♪  Audition A→B" : "♪  Audition");
    apply.disabled = usable.length !== 1;
    apply.title = usable.length > 1 ? t("Select one row to apply it.") : "";
    saveAll.disabled = !state.rows.some((r) => !r.error);
  }

  function pick(row, rows, e) {
    const key = keyOf(row);
    if (e.shiftKey && anchor) {
      const keys = rows.map(keyOf);
      const [a, b] = [keys.indexOf(anchor), keys.indexOf(key)].sort((x, y) => x - y);
      selected = new Set(keys.slice(a, b + 1));
    } else if (e.ctrlKey || e.metaKey) {
      if (selected.has(key)) selected.delete(key); else selected.add(key);
      anchor = key;
    } else {
      selected = new Set([key]);
      anchor = key;
    }
    render();
    if (auditioning) play();          // follow the selection while playing
  }

  /** The selected rows' keys, in the table's own top-to-bottom order: the
   *  table is ranked, and hearing them in that order is what makes "the one
   *  above sounds better" a sentence about the ranking. */
  const chosen = () => order().filter((r) => selected.has(keyOf(r)) && !r.error).map((r) => r.key);

  async function play() {
    const answer = await ctx.call("audition", [chosen()]).catch(() => ({ on: false }));
    auditioning = Boolean(answer.on);
    audition.on = auditioning;
  }

  function stop() {
    auditioning = false;
    audition.on = false;
    ctx.window("stop_preview");
  }

  async function applyRow() {
    const keys = chosen();
    if (keys.length !== 1) return;
    auditioning = false;
    audition.on = false;
    show(await ctx.call("apply", [keys[0], selectionOnly.value]));
  }

  async function start() {
    auditioning = false;
    audition.on = false;
    show(await ctx.call("run", [reference.value, file.value, sweep.value, method.value,
                                noise.value, runDir.value, metrics.value, selectionOnly.value]));
  }

  function sync() {
    const byFile = reference.value === info.references[2];
    file.querySelector("input").disabled = !byFile;
    file.button.disabled = !byFile;
    // whichever axis is swept, its picker is the thing being varied, so
    // holding it at one value would be a contradiction
    method.disabled = sweep.value !== info.sweeps[1];
    noise.disabled = sweep.value !== info.sweeps[0];
    ctx.call("call_text", [sweep.value, method.value, noise.value, reference.value,
                           metrics.value, runDir.value])
      .then((text) => { call.text = text; }).catch(() => {});
  }

  function show(next) {
    if (!next) return;
    const before = state.rows.map(keyOf).join();
    state = next;
    if (state.rows.map(keyOf).join() !== before) {
      selected = new Set();
      memory.sort = null;
    }
    verdict.innerHTML = markup(t(state.verdict.text));
    verdict.className = `verdict ${state.verdict.tone}`;
    busy(state.busy);
    render();
  }

  function busy(on) {
    run.disabled = on;
    run.textContent = t(on ? "Comparing…" : "Run comparison");
  }

  sync();
  show(info);

  return {
    state(s) { if (!s.busy.includes("compare")) run.disabled = !s.has_audio; },
    event(e) { if (e.info) show(e.info); },
    busy(on) { busy(on); },
    preview(on) { if (!on) { auditioning = false; audition.on = false; } },
  };
}
