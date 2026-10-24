// The controls a tool panel is built from.
//
// Every panel uses these rather than its own markup, so the panels look
// like one window and a change of style is made here once. Every label and
// tooltip is given in English and goes through `t`, since the English is what
// the translation tables are keyed on.

import { choosePath } from "./dialogs.js";
import { t } from "./i18n.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** *node* with *extra*'s properties, getters and setters included --
 *  Object.assign would copy what a getter returns once, and drop setters. */
function extend(node, extra) {
  return Object.defineProperties(node, Object.getOwnPropertyDescriptors(extra));
}

/** A titled box in the panel. Returns { el, body, setBusy(label) }. */
export function section(title, { tip = "" } = {}) {
  const box = el("section", "panel-section");
  const head = el("div", "section-title");
  const h = el("h3", "", t(title));
  if (tip) h.title = t(tip);
  const busy = el("span", "busy-chip mono");
  busy.hidden = true;
  head.append(h, busy);
  const body = el("div", "section-body");
  box.append(head, body);
  return {
    el: box,
    body,
    setBusy(label) {
      busy.hidden = !label;
      busy.textContent = label ? t(label) : "";
    },
  };
}

/** Label/control rows, two columns. */
export function form() {
  const grid = el("div", "form");
  return {
    el: grid,
    row(label, control, { tip = "", wide = false } = {}) {
      const name = el("label", "form-label mono", label ? t(label) : "");
      if (tip) { name.title = t(tip); control.title = t(tip); }
      const holder = el("div", "form-control");
      holder.append(control);
      if (wide || !label) holder.classList.add("wide");
      if (label) grid.append(name);
      grid.append(holder);
      return holder;
    },
    clear() { grid.replaceChildren(); },
  };
}

/** A number box. *special* is shown in place of the unit at the minimum,
 *  where the box means a choice rather than a number: a seed of -1 is
 *  "random" or "None". */
export function number({ value = 0, min = -1e9, max = 1e9, step = 1, decimals = 0,
                         suffix = "", special = "", onChange } = {}) {
  const wrap = el("div", "num-wrap");
  const input = el("input", "num mono");
  input.type = "number";
  Object.assign(input, { min, max, step });
  input.value = Number(value).toFixed(decimals);
  const unit = el("span", "num-unit mono");
  let unitText = suffix.trim();
  const showUnit = () => {
    unit.textContent = special && Number(input.value) <= Number(input.min) ? t(special) : unitText;
  };
  showUnit();
  const read = () => {
    let n = Number(input.value);
    if (!Number.isFinite(n)) n = Number(value);
    n = Math.min(Math.max(n, Number(input.min)), Number(input.max));
    return decimals ? Number(n.toFixed(decimals)) : Math.round(n);
  };
  input.addEventListener("input", () => { showUnit(); onChange?.(read()); });
  input.addEventListener("change", () => {
    input.value = read().toFixed(decimals);
    showUnit();
    onChange?.(read());
  });
  wrap.append(input, unit);
  return extend(wrap, {
    get value() { return read(); },
    set value(v) { input.value = Number(v).toFixed(decimals); showUnit(); },
    setMax(m) { input.max = m; if (Number(input.value) > m) { input.value = m; showUnit(); } },
    /** A new range, and a new value and unit with it: a box whose meaning
     *  changes with another choice, as Batch's normalise target does. */
    setRange(lo, hi, { value: v, suffix: s } = {}) {
      Object.assign(input, { min: lo, max: hi });
      if (s !== undefined) unitText = s.trim();
      if (v !== undefined) input.value = Number(v).toFixed(decimals);
      showUnit();
    },
    get disabled() { return input.disabled; },
    set disabled(v) { input.disabled = Boolean(v); wrap.classList.toggle("disabled", Boolean(v)); },
    input,
  });
}

/** A line of text, such as a list of values typed by hand. *onChange*
 *  hears every keystroke rather than waiting for the box to lose focus. */
export function text({ value = "", placeholder = "", onChange } = {}) {
  const input = el("input", "text mono");
  input.type = "text";
  input.value = value;
  input.placeholder = t(placeholder);
  input.spellcheck = false;
  input.addEventListener("input", () => onChange?.(input.value));
  return input;
}

/** A drop-down of *options* (strings, or [value, label] pairs). */
export function select(options, { value, onChange, translate = false } = {}) {
  const box = el("select", "mono");
  for (const option of options) {
    const [v, label] = Array.isArray(option) ? option : [option, option];
    const o = el("option", "", translate ? t(label) : label);
    o.value = v;
    box.append(o);
  }
  if (value !== undefined) box.value = value;
  box.addEventListener("change", () => onChange?.(box.value));
  return box;
}

/** A row of buttons, one of them chosen, as the spectrogram's STFT / Mel /
 *  MFCC switch is: for pages within a panel. *options* are [value, label]
 *  pairs; the labels go through `t`. */
export function segmented(options, { value, onChange } = {}) {
  const row = el("div", "segmented");
  row.setAttribute("role", "tablist");
  let chosen = value ?? options[0]?.[0];
  const show = () => {
    for (const b of row.children) b.classList.toggle("active", b.dataset.value === chosen);
  };
  for (const [v, label] of options) {
    const b = el("button", "", t(label));
    b.type = "button";
    b.dataset.value = v;
    b.addEventListener("click", () => {
      if (chosen === v) return;
      chosen = v;
      show();
      onChange?.(v);
    });
    row.append(b);
  }
  show();
  return extend(row, {
    get value() { return chosen; },
    set value(v) { chosen = v; show(); },
  });
}

/** A check box with its text beside it. */
export function check(text, { checked = false, onChange } = {}) {
  const label = el("label", "check");
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  const box = el("span", "check-box");
  const words = el("span", "", t(text));
  label.append(input, box, words);
  input.addEventListener("change", () => onChange?.(input.checked));
  return extend(label, {
    get value() { return input.checked; },
    set value(v) { input.checked = Boolean(v); },
    input,
  });
}

/** A path with a Browse… button; the dialog is the window's own. */
export function path({ kind = "open", title = "", types = [], placeholder = "",
                       onChange, readOnly = true } = {}) {
  const wrap = el("div", "path-field");
  const input = el("input", "mono");
  input.type = "text";
  input.readOnly = readOnly;
  input.placeholder = t(placeholder);
  const browse = el("button", "btn subtle", t("Browse…"));
  browse.type = "button";
  let full = "";
  const set = (p) => {
    full = p || "";
    input.value = full ? (readOnly ? full.split(/[\\/]/).pop() : full) : "";
    input.title = full;
  };
  browse.addEventListener("click", async () => {
    const chosen = await choosePath(kind, { title: t(title), types });
    if (chosen) { set(chosen); onChange?.(full); }
  });
  if (!readOnly) input.addEventListener("change", () => { set(input.value.trim()); onChange?.(full); });
  wrap.append(input, browse);
  return extend(wrap, {
    get value() { return full; },
    set value(p) { set(p); },
    get disabled() { return input.disabled; },
    set disabled(v) { input.disabled = Boolean(v); browse.disabled = Boolean(v); },
    button: browse,
  });
}

export function button(text, { primary = false, toggle = false, tip = "", onClick } = {}) {
  const b = el("button", `btn ${primary ? "primary" : "subtle"}`, t(text));
  b.type = "button";
  if (tip) b.title = t(tip);
  if (toggle) b.classList.add("toggle-btn");
  b.addEventListener("click", (e) => {
    if (toggle) b.classList.toggle("on");
    onClick?.(e, toggle ? b.classList.contains("on") : undefined);
  });
  return extend(b, {
    get on() { return b.classList.contains("on"); },
    set on(v) { b.classList.toggle("on", Boolean(v)); },
  });
}

export function buttons(...items) {
  const row = el("div", "button-row wrap");
  row.append(...items);
  return row;
}

/** The kudio call a control set will make, in the mono call colour. */
export function callText() {
  const code = el("code", "call-text mono");
  return extend(code, { set text(v) { code.textContent = v; } });
}

export function hint(text) {
  return el("p", "hint", t(text));
}

/** A line of result text. *tone* is true or "warn" for a problem, "ok" for
 *  a good result, anything else for neither. */
export function readout(text = "—") {
  const p = el("p", "readout-line mono", text);
  return extend(p, {
    show(value, tone = false) {
      p.textContent = value;
      p.classList.toggle("warn", tone === true || tone === "warn");
      p.classList.toggle("ok", tone === "ok");
    },
  });
}

/** A table with a header. setRows(rows) takes arrays of cell text. */
export function table(columns, { numeric = [] } = {}) {
  const wrap = el("div", "table-wrap");
  const tableEl = el("table", "data");
  const head = el("thead");
  const tr = el("tr");
  columns.forEach((c, i) => {
    const th = el("th", numeric.includes(i) ? "num" : "", t(c));
    tr.append(th);
  });
  head.append(tr);
  const body = el("tbody");
  tableEl.append(head, body);
  wrap.append(tableEl);
  return extend(wrap, {
    setRows(rows, classes = []) {
      body.replaceChildren();
      rows.forEach((row, r) => {
        const line = el("tr", classes[r] || "");
        row.forEach((cell, i) => line.append(el("td", numeric.includes(i) ? "num mono" : "", cell)));
        body.append(line);
      });
    },
    body,
  });
}

/** Controls for a list of parameter descriptions, as the Python side
 *  serves them (see kudiostudio/tools). Returns { el, values(), set(v),
 *  setNyquist(hz) }. */
export function params(list, { onChange } = {}) {
  const f = form();
  const controls = {};
  for (const p of list) {
    let control;
    if (p.kind === "float" || p.kind === "int") {
      control = number({ value: p.default, min: p.min, max: p.max, step: p.step,
                         decimals: p.kind === "int" ? 0 : p.decimals, suffix: p.suffix,
                         special: p.special, onChange: () => onChange?.() });
    } else if (p.kind === "choice") {
      control = select(p.choices, { value: p.default, onChange: () => onChange?.() });
    } else if (p.kind === "bool") {
      control = check(p.text || p.label, { checked: p.default, onChange: () => onChange?.() });
    } else if (p.kind === "file") {
      control = path({ kind: "open", title: p.title || "", types: p.types || [],
                       placeholder: p.placeholder || "", onChange: () => onChange?.() });
    } else if (p.kind === "note") {
      f.row("", hint(p.text), { wide: true });
      continue;
    } else {
      continue;
    }
    f.row(p.kind === "bool" ? "" : p.label, control, { tip: p.tip });
    controls[p.name] = { p, control };
  }
  return {
    el: f.el,
    values() {
      const out = {};
      for (const [name, { control }] of Object.entries(controls)) out[name] = control.value;
      return out;
    },
    set(values) {
      for (const [name, v] of Object.entries(values || {})) {
        if (controls[name]) controls[name].control.value = v;
      }
    },
    setNyquist(hz) {
      for (const { p, control } of Object.values(controls)) {
        if (p.nyquist && control.setMax) control.setMax(Math.max(1, hz - 1));
      }
    },
    controls,
  };
}
