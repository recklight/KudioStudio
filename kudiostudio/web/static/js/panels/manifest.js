// Manifest: a synthesised corpus read back from its manifest.json, checked
// against the disk, filtered, and listened to a row at a time.
//
// A corpus is thousands of rows. The table keeps none of them for good: it
// draws the rows in view and asks the server for more as it scrolls, so a
// filter or a sort is one call and the page holds nothing that can drift
// from what the server has.

const ROW_HEIGHT = 25;           // px; matches .manifest-rows td
const CHUNK = 100;               // rows asked for at a time
const OVERSCAN = 12;             // rows drawn above and below the view
// widths of noisy, clean, noise, SNR, RT60, link, target, in px
const WIDTHS = [210, 150, 90, 62, 62, 84, 150];

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");

  // ------------------------------------------------------------- source
  const source = ui.section("kudio.load_manifest");
  const pathBox = ui.text({ placeholder: "no manifest open" });
  pathBox.readOnly = true;
  const openFile = ui.button("Open…", { onClick: pick });
  const reload = ui.button("Reload", { tip: info.tips.reload,
                                       onClick: async () => applyView(await ctx.call("reload")) });
  const picker = document.createElement("div");
  picker.className = "path-field";
  picker.append(pathBox, openFile, reload);
  const call = ui.callText();
  const summary = ui.readout("");
  const filterForm = ui.form();
  const filters = {};
  for (const field of ["noise", "snr_db", "rt60", "channel"]) {
    filters[field] = ui.select([], { onChange: setFilters });
  }
  const missingOnly = ui.check("only rows with a file missing", { onChange: setFilters });
  const showing = document.createElement("span");
  showing.className = "scope-line";
  const filterRow = document.createElement("div");
  filterRow.className = "manifest-filter-row";
  filterRow.append(missingOnly, showing);
  source.body.append(picker, call, summary, filterForm.el, filterRow);

  // -------------------------------------------------------------- table
  const scroller = document.createElement("div");
  scroller.className = "table-wrap manifest-rows";
  scroller.tabIndex = 0;
  const table = document.createElement("table");
  table.className = "data selectable";
  const cols = document.createElement("colgroup");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const body = document.createElement("tbody");
  info.columns.forEach((name, column) => {
    const col = document.createElement("col");
    col.style.width = `${WIDTHS[column]}px`;
    cols.append(col);
    const th = document.createElement("th");
    th.className = info.numeric.includes(column) ? "num sortable" : "sortable";
    th.dataset.label = t(name);
    th.textContent = th.dataset.label;
    th.addEventListener("click", () => sortBy(column));
    headRow.append(th);
  });
  head.append(headRow);
  table.append(cols, head, body);
  table.style.width = `${WIDTHS.reduce((a, b) => a + b, 0)}px`;
  scroller.append(table);

  const openRow = ui.button("Open", { tip: info.tips.open, onClick: open });
  const listen = {};
  for (const side of info.sides) {
    listen[side] = ui.button(`♪  ${side}`, {
      toggle: true,
      onClick: async (_, on) => applySelection(await ctx.call("listen", [side, on])),
    });
  }
  const actions = ui.buttons(openRow, ...info.sides.map((s) => listen[s]));

  // ----------------------------------------------------------- coverage
  const covered = ui.section("Rows per noise × SNR", { tip: info.tips.coverage });
  const grid = document.createElement("div");
  grid.className = "table-wrap coverage-grid";
  grid.title = t(info.tips.coverage);
  const note = ui.readout("");
  covered.body.append(grid, note);

  root.append(source.el, scroller, actions, covered.el, ui.hint(info.hint));

  // -------------------------------------------------------------- state
  let view = null;
  let rows = new Map();          // table position -> row from the server
  let asked = new Set();         // chunk starts asked for under this view
  let generation = 0;            // bumped by every new view; late replies are dropped
  let selection = null;

  function applyView(next, { reset = false } = {}) {
    if (!next) return;
    view = next;
    generation += 1;
    rows = new Map();
    asked = new Set();
    pathBox.value = view.path || "";
    pathBox.title = view.path || "";
    call.text = view.call;
    summary.show(view.summary[0], view.summary[1]);
    reload.disabled = !view.loaded || view.busy;
    filterForm.clear();
    if (view.loaded) {
      for (const f of view.filters) {
        const box = filters[f.field];
        box.replaceChildren(...f.choices.map((text, i) => new Option(t(text), String(i))));
        box.value = String(f.index);
        box.disabled = !f.enabled;
        filterForm.row(f.label, box);
      }
      missingOnly.value = view.missing_only;
      showing.textContent = view.showing;
      for (const r of view.rows) rows.set(r.p, r);
      asked.add(0);
      headRow.querySelectorAll("th").forEach((th, column) => {
        const [sorted, descending] = view.sort;
        th.textContent = th.dataset.label + (sorted === column ? (descending ? " ▼" : " ▲") : "");
        th.title = view.header_tips[column] || "";
      });
    }
    filterRow.hidden = !view.loaded;
    scroller.hidden = !view.loaded;
    actions.hidden = !view.loaded;
    covered.el.hidden = !view.loaded;
    if (reset) scroller.scrollTop = 0;
    drawCoverage(view.coverage);
    applySelection(view.selection || null);
    draw();
  }

  // ------------------------------------------------------- virtual rows

  function draw() {
    if (!view || !view.loaded) { body.replaceChildren(); return; }
    const total = view.shown;
    const first = Math.max(0, Math.floor(scroller.scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visible = Math.ceil(scroller.clientHeight / ROW_HEIGHT) + 2 * OVERSCAN;
    const last = Math.min(total, first + visible);
    const parts = [];
    parts.push(spacer(first * ROW_HEIGHT));
    for (let p = first; p < last; p += 1) parts.push(line(p, rows.get(p)));
    parts.push(spacer((total - last) * ROW_HEIGHT));
    body.replaceChildren(...parts);
    fetchRange(first, last);
  }

  function spacer(height) {
    const tr = document.createElement("tr");
    tr.className = "spacer";
    const td = document.createElement("td");
    td.colSpan = info.columns.length;
    td.style.height = `${height}px`;
    tr.append(td);
    return tr;
  }

  function line(position, row) {
    const tr = document.createElement("tr");
    tr.dataset.p = position;
    tr.classList.toggle("selected", selection?.position === position);
    info.columns.forEach((_, column) => {
      const td = document.createElement("td");
      if (info.numeric.includes(column)) td.className = "num mono";
      if (row) {
        const text = row.cells[column];
        td.textContent = elide(text, WIDTHS[column]);
        td.title = row.tips[column] || (td.textContent !== text ? text : "");
        if (row.missing.includes(column)) td.classList.add("missing");
      }
      tr.append(td);
    });
    tr.addEventListener("click", () => select(position));
    tr.addEventListener("dblclick", open);
    return tr;
  }

  /** A synthesised name starts with the clean file and ends with the SNR and
   *  the room; the middle is the part that can go. */
  function elide(text, width) {
    const fits = Math.max(6, Math.floor((width - 16) / 6.6));
    if (text.length <= fits) return text;
    const head = Math.ceil((fits - 1) / 2);
    return `${text.slice(0, head)}…${text.slice(text.length - (fits - 1 - head))}`;
  }

  async function fetchRange(first, last) {
    const mine = generation;
    for (let start = Math.floor(first / CHUNK) * CHUNK; start < last; start += CHUNK) {
      if (asked.has(start)) continue;
      asked.add(start);
      const got = await ctx.call("rows", [start, CHUNK]).catch(() => []);
      if (mine !== generation) return;
      for (const r of got) rows.set(r.p, r);
      draw();
    }
  }

  // a scroll event comes at most once a frame already, and drawing three
  // dozen rows costs less than waiting for the next one
  scroller.addEventListener("scroll", draw);
  new ResizeObserver(() => draw()).observe(scroller);

  // --------------------------------------------------------- selection

  async function select(position) {
    if (!view || position < 0 || position >= view.shown) return;
    applySelection(await ctx.call("select", [position]));
    const top = position * ROW_HEIGHT;
    const header = head.offsetHeight;
    if (top < scroller.scrollTop) scroller.scrollTop = top;
    else if (top + ROW_HEIGHT > scroller.scrollTop + scroller.clientHeight - header) {
      scroller.scrollTop = top + ROW_HEIGHT - scroller.clientHeight + header;
    }
  }

  function applySelection(next) {
    selection = next;
    const position = next ? next.position : -1;
    for (const tr of body.querySelectorAll("tr[data-p]")) {
      tr.classList.toggle("selected", Number(tr.dataset.p) === position);
    }
    openRow.disabled = !next?.can_open;
    for (const side of info.sides) {
      const s = next?.sides?.[side];
      listen[side].disabled = !s?.enabled;
      listen[side].title = s ? s.tip : "";
      listen[side].on = next?.listening === side;
    }
  }

  scroller.addEventListener("keydown", (e) => {
    if (!view?.loaded || !["ArrowDown", "ArrowUp", "Enter"].includes(e.key)) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Enter") { open(); return; }
    const at = selection?.position ?? -1;
    select(Math.min(view.shown - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1))));
  });

  // ---------------------------------------------------- filters and sort

  async function setFilters() {
    const picked = {};
    for (const [field, box] of Object.entries(filters)) picked[field] = Number(box.value || 0);
    applyView(await ctx.call("set_filters", [picked, missingOnly.value]));
  }

  async function sortBy(column) {
    // ascending, then descending, then the manifest's own order again
    const [sorted, descending] = view.sort;
    const next = sorted !== column ? [column, false] : !descending ? [column, true] : [-1, false];
    applyView(await ctx.call("sort", next));
  }

  function drawCoverage(cov) {
    grid.replaceChildren();
    note.show("");
    if (!cov) return;
    const cells = document.createElement("table");
    cells.className = "data coverage";
    const hr = document.createElement("tr");
    hr.append(document.createElement("th"));
    for (const snr of cov.snrs) {
      const th = document.createElement("th");
      th.className = "num";
      th.textContent = snr;
      hr.append(th);
    }
    const thead = document.createElement("thead");
    thead.append(hr);
    const tbody = document.createElement("tbody");
    cov.noises.forEach((noise, r) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = noise;
      tr.append(th);
      cov.counts[r].forEach((count, c) => {
        const td = document.createElement("td");
        td.className = "num mono";
        td.textContent = String(count);
        if (count === 0 && cov.holes) td.classList.add("hole");
        td.addEventListener("dblclick", async () => applyView(await ctx.call("focus", [r, c])));
        tr.append(td);
      });
      tbody.append(tr);
    });
    cells.append(thead, tbody);
    grid.append(cells);
    note.show(cov.note[0], cov.note[1]);
  }

  // ------------------------------------------------------------ opening

  async function pick() {
    const path = await ctx.dialogs.choosePath("open", { title: t("Open a corpus manifest"),
                                                        types: info.types });
    if (path) applyView(await ctx.call("load", [path]));
  }

  async function open() {
    if (selection?.can_open) await ctx.call("open_row");
  }

  applyView(info.view, { reset: true });

  return {
    event(e) {
      if (e.view) applyView(e.view, { reset: e.reset });
      if (e.selection) applySelection(e.selection);
    },
    busy(on) {
      openFile.disabled = on;
      reload.disabled = on || !view?.loaded;
      reload.textContent = t(on ? "Reading…" : "Reload");
    },
    preview(on) {
      if (!on) for (const side of info.sides) listen[side].on = false;
    },
  };
}
