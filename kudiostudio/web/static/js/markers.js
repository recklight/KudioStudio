// The marker list: named points and ranges to come back to.
//
// A view onto the session's markers, which own the state. Click a row to
// move the playhead there, double-click a range to select it.

import { call } from "./api.js";
import { ask, choosePath } from "./dialogs.js";
import { t } from "./i18n.js";

const $ = (id) => document.getElementById(id);

function span(start, end) {
  const one = (s) => {
    const m = Math.floor(Math.max(0, s) / 60);
    return `${m}:${(Math.max(0, s) - m * 60).toFixed(3).padStart(6, "0")}`;
  };
  return end > start ? `${one(start)} – ${one(end)}` : one(start);
}

export class MarkerList {
  /** *apply* takes the state a command returns; *seek* moves the playhead. */
  constructor({ apply, seek }) {
    this.apply = apply;
    this.seek = seek;
    this.current = -1;
    this.state = null;
    this.bind();
  }

  async run(name, args = [], kwargs = {}) {
    const state = await call(`markers.${name}`, args, kwargs).catch((e) => {
      $("status").textContent = e.message;
      return null;
    });
    if (state) this.apply(state);
  }

  bind() {
    $("marker-add").addEventListener("click", () => this.run("add"));
    $("marker-mark").addEventListener("click", () => this.run("mark_selection"));
    $("marker-vad").addEventListener("click", () => this.run("find_speech"));
    $("marker-rename").addEventListener("click", () => this.rename());
    $("marker-remove").addEventListener("click", () => {
      if (this.current >= 0) this.run("remove", [this.current]);
    });
    $("marker-clear").addEventListener("click", () => this.run("clear"));
    $("marker-save").addEventListener("click", () => this.run("save"));
    $("marker-export").addEventListener("click", async () => {
      const suggested = await call("suggest_name", ["markers"]);
      const path = await choosePath("save", {
        title: t("Export markers"), suggested,
        types: ["Audacity labels (*.txt)", "JSON (*.json)"],
      });
      if (path) this.run("export", [path]);
    });
    $("marker-import").addEventListener("click", async () => {
      const path = await choosePath("open", {
        title: t("Import markers"),
        types: ["Labels (*.txt;*.tsv;*.json)", "All files (*.*)"],
      });
      if (path) this.run("load", [path]);
    });
  }

  async rename() {
    const m = this.state?.markers[this.current];
    if (!m) return;
    const label = await ask({ title: t("Rename marker"), value: m.label, submit: t("OK") });
    if (label !== null) this.run("rename", [this.current, label]);
  }

  render(state) {
    this.state = state;
    const markers = state.markers;
    if (this.current >= markers.length) this.current = -1;
    const list = $("marker-list");
    list.replaceChildren();
    markers.forEach((m, index) => {
      const li = document.createElement("li");
      li.classList.toggle("current", index === this.current);
      li.title = t(m.end > m.start ? "Click to jump here, double-click to select the range"
                                   : "Click to jump here");
      const when = document.createElement("span");
      when.className = "when mono";
      when.textContent = span(m.start, m.end);
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = m.label || t(`marker ${index + 1}`);
      li.append(when, label);
      li.addEventListener("click", () => {
        this.current = index;
        this.render(this.state);
        this.seek(m.start);
      });
      li.addEventListener("dblclick", () => {
        if (m.end > m.start) call("set_selection", [m.start, m.end]).then(this.apply);
      });
      list.append(li);
    });
    $("markers-empty").hidden = markers.length > 0;
    $("marker-count").textContent = markers.length ? String(markers.length) : "";

    const has = markers.length > 0;
    const picked = this.current >= 0;
    $("marker-add").disabled = !state.has_audio;
    $("marker-mark").disabled = !state.selection;
    $("marker-vad").disabled = !state.has_audio;
    $("marker-rename").disabled = !(has && picked);
    $("marker-remove").disabled = !(has && picked);
    $("marker-clear").disabled = !has;
    $("marker-save").disabled = !(has && state.path);
    $("marker-export").disabled = !has;
    $("marker-import").disabled = !state.has_audio;
  }
}
