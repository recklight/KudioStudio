// Small modal boxes, and the one way the page gets a path.
//
// Paths are chosen in Python, in the window's own dialog (`choose_path`).
// When there is no window -- the page opened in a browser -- the server says
// so and the page asks for the path in a box instead.

import { call } from "./api.js";
import { t } from "./i18n.js";

function modal(build) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "prompt-backdrop";
    const form = document.createElement("form");
    form.className = "prompt";
    // the values are clamped here; the browser's own check would refuse 1
    // in a box that starts at 0.001 and steps by 0.1, and say nothing
    form.noValidate = true;
    backdrop.append(form);
    const done = (value) => {
      backdrop.remove();
      document.removeEventListener("keydown", onKey, true);
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(null); }
    };
    document.addEventListener("keydown", onKey, true);
    backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) done(null); });
    build(form, done);
    document.body.append(backdrop);
    form.querySelector("input, button[type=submit]")?.focus();
  });
}

function buttons(form, done, submitText) {
  const row = document.createElement("div");
  row.className = "button-row";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn subtle";
  cancel.textContent = t("Cancel");
  cancel.addEventListener("click", () => done(null));
  const ok = document.createElement("button");
  ok.type = "submit";
  ok.className = "btn primary";
  ok.textContent = submitText;
  row.append(cancel, ok);
  form.append(row);
}

/** A line of text; resolves to it, or null. */
export function ask({ title, value = "", placeholder = "", submit = t("OK") }) {
  return modal((form, done) => {
    const h = document.createElement("h3");
    h.textContent = title;
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = placeholder;
    form.append(h, input);
    buttons(form, done, submit);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      // a path pasted from Explorer comes in quotes
      done(input.value.trim().replace(/^"(.*)"$/, "$1") || null);
    });
    setTimeout(() => input.select(), 0);
  });
}

/** A number within bounds; resolves to it, or null. */
export function askNumber({ title, label = "", value = 0, min = 0, max = 1e9,
                            step = 1, unit = "", submit = t("OK") }) {
  return modal((form, done) => {
    const h = document.createElement("h3");
    h.textContent = title;
    const row = document.createElement("label");
    row.className = "prompt-number";
    const text = document.createElement("span");
    text.textContent = label;
    const input = document.createElement("input");
    input.type = "number";
    input.className = "num mono";
    Object.assign(input, { min, max, step, value });
    const suffix = document.createElement("span");
    suffix.className = "mono";
    suffix.textContent = unit;
    row.append(text, input, suffix);
    form.append(h, row);
    buttons(form, done, submit);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const n = Number(input.value);
      if (Number.isFinite(n)) done(Math.min(Math.max(n, min), max));
    });
    setTimeout(() => input.select(), 0);
  });
}

/** A path from the window's dialog, or typed when there is none.
 *  *kind* is open, open_many, save or folder; open_many resolves to a
 *  list. Null when cancelled. */
export async function choosePath(kind, { title = "", types = [], suggested = "" } = {}) {
  const answer = await call("choose_path", [kind],
                            { title, file_types: types, suggested });
  if (!answer.needs_path) return answer.path ?? null;
  const typed = await ask({
    title: title || t("Path"),
    value: kind === "folder" || kind === "save" ? answer.suggested : "",
    placeholder: t("Path of an audio file on this computer"),
    submit: kind === "save" ? t("Save") : t("Open"),
  });
  if (!typed) return null;
  return kind === "open_many" ? [typed] : typed;
}

/** The About box. */
export function about(info, plugins = []) {
  return modal((form, done) => {
    form.classList.add("about");
    form.innerHTML = `<img src="/assets/logo.png" alt=""><h3></h3><p class="lead"></p>` +
      `<p class="mono versions"></p><ul class="plugins"></ul>`;
    form.querySelector("h3").textContent = `KudioStudio ${info.kudiostudio}`;
    form.querySelector(".lead").textContent = t("A desktop workbench for the kudio audio toolkit.");
    form.querySelector(".versions").textContent = `kudio ${info.kudio} · MIT`;
    const list = form.querySelector(".plugins");
    for (const p of plugins) {
      const li = document.createElement("li");
      li.textContent = t(`Plugin: ${p}`);
      list.append(li);
    }
    const row = document.createElement("div");
    row.className = "button-row";
    const ok = document.createElement("button");
    ok.type = "submit";
    ok.className = "btn primary";
    ok.textContent = t("OK");
    row.append(ok);
    form.append(row);
    form.addEventListener("submit", (e) => { e.preventDefault(); done(true); });
  });
}
