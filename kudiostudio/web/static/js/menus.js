// The menu bar: File, Edit, Playback, View, Help, as a desktop window has.
//
// The labels keep their mnemonics ("&File"), because the translation table
// is keyed on them that way; the ampersands are taken out for display.
// Whether an item is enabled or ticked is asked again every time its menu
// opens.

import { t } from "./i18n.js";

/** "Cu&t\tCtrl+X" in the current language, as [label, keys]. */
export function menuText(key) {
  const [label, keys = ""] = t(key).split("\t");
  return [label.replace(/\(&.\)/g, "").replace(/&(?=\S)/g, "").trim(), keys];
}

export class MenuBar {
  /** *menus*: [{ title, items: [{ label, keys, run, enabled, checked } | "-"] }] */
  constructor(root, menus) {
    this.root = root;
    this.menus = menus;
    this.open = null;              // index of the open menu
    this.render();
    document.addEventListener("mousedown", (e) => {
      if (this.open !== null && !this.root.contains(e.target)) this.close();
    });
    document.addEventListener("keydown", (e) => {
      if (this.open !== null && e.key === "Escape") { e.preventDefault(); this.close(); }
    }, true);
    window.addEventListener("blur", () => this.close());
  }

  render() {
    this.root.replaceChildren();
    this.menus.forEach((menu, index) => {
      const wrap = document.createElement("div");
      wrap.className = "menu-wrap";
      const title = document.createElement("button");
      title.className = "menu-title";
      title.textContent = menuText(menu.title)[0];
      title.addEventListener("mousedown", (e) => {
        e.preventDefault();
        this.open === index ? this.close() : this.show(index);
      });
      title.addEventListener("mouseenter", () => {
        if (this.open !== null && this.open !== index) this.show(index);
      });
      wrap.append(title);
      this.root.append(wrap);
    });
  }

  show(index) {
    this.close();
    this.open = index;
    const wrap = this.root.children[index];
    wrap.classList.add("open");
    const list = document.createElement("div");
    list.className = "menu";
    for (const item of this.menus[index].items) {
      if (item === "-") {
        const rule = document.createElement("div");
        rule.className = "menu-rule";
        list.append(rule);
        continue;
      }
      const [label, keys] = menuText(item.label);
      const row = document.createElement("button");
      row.className = "menu-item";
      const enabled = item.enabled ? item.enabled() : true;
      row.disabled = !enabled;
      const mark = document.createElement("span");
      mark.className = "menu-check";
      if (item.checked) mark.textContent = item.checked() ? "✓" : "";
      const text = document.createElement("span");
      text.className = "menu-label";
      text.textContent = item.text ? item.text(label) : label;
      const shortcut = document.createElement("span");
      shortcut.className = "menu-keys mono";
      shortcut.textContent = item.keys || keys;
      row.append(mark, text, shortcut);
      row.addEventListener("click", () => { this.close(); item.run(); });
      list.append(row);
    }
    wrap.append(list);
  }

  close() {
    if (this.open === null) return;
    const wrap = this.root.children[this.open];
    wrap?.classList.remove("open");
    wrap?.querySelector(".menu")?.remove();
    this.open = null;
  }
}
