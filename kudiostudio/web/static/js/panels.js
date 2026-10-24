// The tool rail and the panels behind it.
//
// The server lists the tools in rail order (`rail`). A tool's page module is
// imported the first time it is chosen, mounted into its own box in the
// panel column, and from then on hears the window's state and the events
// addressed to it. A module exports `mount(root, ctx)` and returns an object
// with any of: state(s), event(e), busy(on, label), progress(text),
// preview(on). A change of language mounts it again, in the new one; what
// it keeps in `ctx.memory` survives that.
//
// A panel whose results need more room than the panel column -- a set of
// plots, a large table -- can put a view of its own over the stage with
// `ctx.view.show(title, node, { onClose })` and take it away with
// `ctx.view.hide()`. One panel holds the view at a time; it stays when
// another tool is chosen, and the close button gives the plots back.

import { call } from "./api.js";
import * as dialogs from "./dialogs.js";
import { t, translatePage } from "./i18n.js";
import { fillIcons } from "./icons.js";
import * as ui from "./ui.js";

const $ = (id) => document.getElementById(id);

/** The layer over the stage that `ctx.view` shows and hides. */
class PanelView {
  constructor() {
    this.el = $("panel-view");
    this.title = $("panel-view-title");
    this.body = $("panel-view-body");
    this.owner = null;
    this.onClose = null;
    $("panel-view-close")?.addEventListener("click", () => this.close());
  }

  show(owner, title, node, onClose) {
    if (this.owner !== null && this.owner !== owner) this.close();
    this.owner = owner;
    this.onClose = onClose || null;
    this.title.textContent = title;
    if (node && node.parentNode !== this.body) this.body.replaceChildren(node);
    this.el.hidden = false;
  }

  /** Take the view away without telling its panel: the panel asked. */
  hide(owner) {
    if (this.owner === owner) this.clear();
  }

  /** The close button, or another panel taking the view: its panel hears. */
  close() {
    const told = this.onClose;
    this.clear();
    told?.();
  }

  clear() {
    this.owner = null;
    this.onClose = null;
    this.el.hidden = true;
    this.body.replaceChildren();
  }
}

export class Panels {
  constructor({ apply, status, state }) {
    this.apply = apply;           // takes a state a command returned
    this.status = status;         // a line for the status bar
    this.getState = state;        // the latest window state
    this.entries = [];
    this.mounted = new Map();     // id -> { root, api }
    this.memories = new Map();    // id -> what the panel keeps across mounts
    this.current = "inspect";
    this.view = new PanelView();
  }

  async load() {
    this.entries = await call("rail");
    const rail = $("rail");
    rail.replaceChildren();
    this.entries.forEach((entry, index) => {
      if (index === 1 || (entry.extra && !this.entries[index - 1].extra)) {
        rail.append(Object.assign(document.createElement("div"), { className: "rule" }));
      }
      const button = document.createElement("button");
      button.dataset.tool = entry.id;
      button.innerHTML = `<i data-icon="${entry.icon}"></i><span data-t>${entry.title}</span>`;
      button.addEventListener("click", () => this.select(entry.id));
      rail.append(button);
    });
    fillIcons(rail);
    translatePage(rail);
    await this.select(this.current);
  }

  async select(id) {
    const entry = this.entries.find((e) => e.id === id) || this.entries[0];
    this.current = entry.id;
    for (const button of $("rail").querySelectorAll("button")) {
      button.classList.toggle("active", button.dataset.tool === entry.id);
    }
    for (const section of $("panel").querySelectorAll("[data-panel]")) {
      section.hidden = section.dataset.panel !== entry.id;
    }
    for (const [key, { root }] of this.mounted) root.hidden = key !== entry.id;
    if (entry.script && !this.mounted.has(entry.id)) await this.mount(entry);
  }

  async mount(entry) {
    const root = document.createElement("div");
    root.className = "panel-host";
    root.dataset.panelHost = entry.id;
    $("panel").append(root);
    const record = { root, api: null };
    this.mounted.set(entry.id, record);
    await this.build(entry, record);
  }

  async build(entry, record) {
    try {
      const module = await import(entry.url);
      // a module mounted again shows its view again if it wants it; the one
      // on screen belongs to the module being replaced
      this.view.hide(entry.id);
      record.root.replaceChildren();
      record.api = (await module.mount(record.root, this.context(entry))) || {};
      const state = this.getState();
      if (state) record.api.state?.(state);
    } catch (error) {
      console.error(error);
      record.root.replaceChildren(ui.hint(`${entry.title}: ${error.message}`));
    }
  }

  context(entry) {
    const prefix = `${entry.id}.`;
    const panels = this;
    if (!this.memories.has(entry.id)) this.memories.set(entry.id, {});
    return {
      id: entry.id,
      memory: this.memories.get(entry.id),
      t,
      ui,
      dialogs,
      call: (name, args = [], kwargs = {}) => this.run(prefix + name, args, kwargs),
      window: (name, args = [], kwargs = {}) => this.run(name, args, kwargs),
      view: {
        show: (title, node, { onClose } = {}) => this.view.show(entry.id, title, node, onClose),
        hide: () => this.view.hide(entry.id),
        get open() { return panels.view.owner === entry.id; },
      },
      status: this.status,
      state: this.getState,
    };
  }

  async run(name, args, kwargs) {
    try {
      const result = await call(name, args, kwargs);
      if (result && typeof result === "object" && "has_audio" in result && "version" in result) {
        this.apply(result);
      }
      return result;
    } catch (error) {
      this.status(error.message, true);
      throw error;
    }
  }

  // ------------------------------------------------------------ fan-out

  state(s) {
    for (const { api } of this.mounted.values()) api?.state?.(s);
  }

  event(e) {
    if (e.type === "panel") this.mounted.get(e.panel)?.api?.event?.(e);
    else if (e.type === "job") this.mounted.get(e.owner)?.api?.busy?.(e.busy, e.label);
    else if (e.type === "progress") this.mounted.get(e.owner)?.api?.progress?.(e.text);
    else if (e.type === "preview") this.mounted.get(e.owner)?.api?.preview?.(e.on);
  }

  /** Everything again in the new language: the rail's labels, and every
   *  panel built so far, mounted afresh. */
  async translate() {
    translatePage($("rail"));
    for (const [id, record] of this.mounted) {
      const entry = this.entries.find((e) => e.id === id);
      if (entry) await this.build(entry, record);
    }
  }
}
