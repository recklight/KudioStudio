// Edit → Settings…, and putting the settings into effect on the page.
//
// The server keeps the values (web/options.py) and every state it sends
// carries them; `applyOptions` puts the page's share into effect whenever
// they change: the size, the fonts, the spectrogram's colours and the
// question before unsaved audio is replaced. A change in the dialog takes
// effect at once, so there is nothing to confirm and Close only closes.

import { call } from "./api.js";
import { modal, setAskBeforeReplacing } from "./dialogs.js";
import { t } from "./i18n.js";
import * as ui from "./ui.js";

const root = document.documentElement;
let applied = {};
let changeLanguage = null;
// the zoom on the page, which a small window holds below the one chosen,
// and what to tell when the window's size changes it
let shownZoom = null;
let lastHooks = {};

// the page the layout is drawn for, in CSS pixels: app.py's least window
// less its frame. Below it the plots get squeezed between the side panels
// and Chinese labels go one character to a line
export const ROOM = { width: 1100, height: 640 };

/** The zoom the page gets for *zoom* in a window *width* x *height*: up to
 *  what still leaves it ROOM, and never below 100% on that account. */
export function fittingZoom(zoom, width, height) {
  if (!(zoom > 1) || !(width > 0) || !(height > 0)) return zoom;
  const room = Math.min(width / ROOM.width, height / ROOM.height);
  return Math.max(1, Math.min(zoom, Math.floor(room * 100) / 100));
}

function showZoom(zoom) {
  const fitted = fittingZoom(zoom, globalThis.innerWidth, globalThis.innerHeight);
  if (fitted === shownZoom) return false;
  // --zoom is for what is sized by the window rather than laid out:
  // 100vh at 125% would be a quarter taller than the window
  root.style.zoom = String(fitted);
  root.style.setProperty("--zoom", String(fitted));
  shownZoom = fitted;
  return true;
}

// a larger window shows more of the size chosen, a smaller one less
globalThis.addEventListener?.("resize", () => {
  if (applied.zoom !== undefined && showZoom(applied.zoom)) lastHooks.resized?.();
});

/** What a language chosen in the dialog is handed to: main.js, which asks
 *  the server for it and translates the window. */
export function setLanguageHandler(fn) {
  changeLanguage = fn;
}
// the stylesheet's own font lists, which a chosen font goes in front of
let stacks = null;

/** Put the state's *options* into effect. *hooks.resized* is called when
 *  the size or a font changed, for what draws its own text,
 *  *hooks.colormap(name)* when the spectrogram's colours did, and
 *  *hooks.cramped(shown, chosen)* when a size was chosen that the window
 *  has no room for. */
export function applyOptions(options, hooks = {}) {
  if (!options) return;
  lastHooks = hooks;
  if (stacks === null) {
    const style = getComputedStyle(root);
    stacks = { ui: style.getPropertyValue("--font-ui").trim(),
               mono: style.getPropertyValue("--font-mono").trim() };
  }
  let redraw = false;
  if (options.zoom !== applied.zoom) {
    if (showZoom(options.zoom)) redraw = true;
    // said when the size is changed, not at every start in a small window
    if (applied.zoom !== undefined && shownZoom < options.zoom) {
      hooks.cramped?.(shownZoom, options.zoom);
    }
  }
  if (options.text_colors !== applied.text_colors) {
    // theme.css holds each set under :root[data-text="…"]; standard is
    // the plain :root
    if (options.text_colors && options.text_colors !== "standard") {
      root.dataset.text = options.text_colors;
    } else if (applied.text_colors) {
      delete root.dataset.text;
    }
    redraw = true;
  }
  for (const [key, variable, stack] of [["ui_font", "--font-ui", "ui"],
                                        ["mono_font", "--font-mono", "mono"]]) {
    if (options[key] === applied[key]) continue;
    if (options[key]) root.style.setProperty(variable, `"${options[key]}", ${stacks[stack]}`);
    else if (applied[key]) root.style.removeProperty(variable);
    redraw = true;
  }
  if (options.spectrum_map !== applied.spectrum_map && applied.spectrum_map !== undefined) {
    hooks.colormap?.(options.spectrum_map);
  }
  setAskBeforeReplacing(options.ask_before_replacing);
  applied = { ...options };
  if (redraw) hooks.resized?.();
}

/** The next size up (*step* 1) or down (-1) from *zoom*, among *zooms*. */
export function nextZoom(zooms, zoom, step) {
  const at = zooms.findIndex((z) => Math.abs(z - zoom) < 1e-9);
  const i = at < 0 ? zooms.indexOf(1) : at + step;
  return zooms[Math.min(Math.max(i, 0), zooms.length - 1)];
}

const DEPTHS = [["PCM_16", "16-bit PCM"], ["PCM_24", "24-bit PCM"], ["FLOAT", "32-bit float"]];
const TEXT_SETS = { standard: "Standard", bright: "Bright", warm: "Warm", high: "High contrast" };

function fontBox(info, key, show) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text";
  input.spellcheck = false;
  input.placeholder = t("Default");
  const list = document.createElement("datalist");
  list.id = `settings-${key}`;
  for (const name of info.fonts[key]) list.append(new Option(name, name));
  input.setAttribute("list", list.id);
  // the box is the font itself, so a name typed is seen in it
  const look = () => { input.style.fontFamily = input.value ? `"${input.value}"` : ""; };
  input.addEventListener("change", () => { look(); show(key, input.value.trim()); });
  const wrap = document.createElement("div");
  wrap.className = "font-field";
  wrap.append(input, list);
  return Object.assign(wrap, { set(value) { input.value = value; look(); } });
}

/** Edit → Settings…: resolves when it is closed. */
export async function openSettings() {
  let info = await call("options.describe");
  let claude = await call("claude.describe");
  const words = await call("translations");
  let language = null;
  await modal((form, done) => {
    form.classList.add("settings");
    const controls = {};
    const put = async (key, value) => {
      info = await call("options.set", [key, value]);
      fill();
    };
    const fill = () => {
      for (const [key, control] of Object.entries(controls)) {
        const value = info.values[key];
        if (control.set) control.set(value);
        else control.value = typeof value === "number" && control.tagName === "SELECT"
          ? String(value) : value;
      }
    };

    const h = document.createElement("h3");
    h.textContent = t("Settings");
    form.append(h);
    const group = (title) => {
      const heading = document.createElement("h4");
      heading.textContent = t(title);
      const rows = ui.form();
      form.append(heading, rows.el);
      return rows;
    };

    let rows = group("Interface");
    // named in both languages: whoever cannot read the one on screen can
    // still find the way to the other
    const languages = ui.select(Object.entries(words.languages), {
      value: words.language,
      onChange: (code) => { language = code; done(true); },
    });
    rows.row("Language / 語言", languages);
    controls.zoom = ui.select(info.zooms.map((z) => [String(z), `${Math.round(z * 100)}%`]),
                              { onChange: (v) => put("zoom", Number(v)) });
    rows.row("Size", controls.zoom, { tip: "Ctrl+= and Ctrl+- step through the sizes; Ctrl+0 is 100%. A window too small for the size shows as much of it as fits." });
    controls.text_colors = ui.select(info.text_colors.map((name) => [name, TEXT_SETS[name] || name]),
                                     { translate: true, onChange: (v) => put("text_colors", v) });
    rows.row("Text colour", controls.text_colors,
             { tip: "Brighter text and figures for a dim screen, warmer for the evening, or the most contrast. Warnings keep their red and passes their green." });
    controls.ui_font = fontBox(info, "ui_font", put);
    rows.row("Font", controls.ui_font, { tip: "Any installed font; empty is the window's own." });
    controls.mono_font = fontBox(info, "mono_font", put);
    rows.row("Numbers font", controls.mono_font,
             { tip: "For figures, times and code; one with digits of even width keeps columns lined up." });

    rows = group("Editing");
    // a number goes to the server when the box is left or Enter is pressed:
    // typing 50 must not trim the undo history to 5 on the way
    const onDone = (key) => controls[key].input.addEventListener(
      "change", () => put(key, controls[key].value));
    controls.crossfade_ms = ui.number({ min: info.crossfade_ms[0], max: info.crossfade_ms[1],
                                        suffix: "ms" });
    onDone("crossfade_ms");
    rows.row("Splice crossfade", controls.crossfade_ms,
             { tip: "The fade at every cut, paste and inserted silence; 0 is a hard cut." });
    controls.undo_depth = ui.number({ min: info.undo_steps[0], max: info.undo_steps[1] });
    onDone("undo_depth");
    rows.row("Undo steps", controls.undo_depth,
             { tip: "Each step holds a copy of the clip: an hour of 48 kHz mono is 690 MB." });

    rows = group("Saving");
    controls.bit_depth = ui.select(DEPTHS, { translate: true, onChange: (v) => put("bit_depth", v) });
    rows.row("Bit depth", controls.bit_depth,
             { tip: "What Save As writes. FLAC holds no float and gets 24-bit instead." });
    controls.output_dir = ui.path({ kind: "folder", title: "Output folder",
                                    onChange: (v) => put("output_dir", v) });
    controls.output_dir.set = (v) => { controls.output_dir.value = v || ""; };
    rows.row("Output folder", controls.output_dir,
             { tip: "Where every save dialog starts: exports, images, scripts and the plugins' files. Empty, it is the folder last saved to." });
    const clearOutput = ui.button("Last saved to", { onClick: () => put("output_dir", "") });
    rows.row("", clearOutput);

    rows = group("Spectrogram");
    controls.spectrum_map = ui.select(info.spectrum_maps, { onChange: (v) => put("spectrum_map", v) });
    rows.row("Colours", controls.spectrum_map,
             { tip: "inferno, magma, viridis and cividis are even in lightness from end to end; gray prints." });

    rows = group("Start and questions");
    controls.reopen_last = ui.check("Open the last file at start",
                                    { onChange: (v) => put("reopen_last", v) });
    rows.row("", controls.reopen_last, { tip: "Only when the window is started with no file named." });
    controls.ask_before_replacing = ui.check("Ask before unsaved audio is replaced",
                                             { onChange: (v) => put("ask_before_replacing", v) });
    rows.row("", controls.ask_before_replacing,
             { tip: "Off, opening a file or recording over an edit no file holds goes ahead without a question." });

    rows = group("Claude");
    const keyLine = ui.readout("");
    rows.row("", keyLine);
    const keyBox = document.createElement("input");
    keyBox.type = "password";
    keyBox.className = "text";
    keyBox.placeholder = "sk-ant-…";
    keyBox.autocomplete = "off";
    keyBox.spellcheck = false;
    const saveKey = async () => {
      if (!keyBox.value.trim()) return;
      claude = await call("claude.save_key", [keyBox.value]);
      keyBox.value = "";
      showClaude();
    };
    // Enter in the box saves the key; it would otherwise submit the form,
    // which closes the dialog with the key unsaved
    keyBox.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); saveKey(); }
    });
    rows.row("API key", keyBox,
             { tip: "Your own key from the Claude Console. It is kept in the system's credential store, not in KudioStudio's settings file." });
    const save = ui.button("Save the key", { onClick: saveKey });
    const test = ui.button("Test", {
      tip: "Lists the models the key can use; it costs nothing.",
      onClick: async () => {
        test.disabled = true;
        keyLine.show(t("Checking the key …"), "");
        claude = await call("claude.test");
        showClaude();
      },
    });
    const remove = ui.button("Remove the key", {
      onClick: async () => { claude = await call("claude.remove_key"); showClaude(); },
    });
    const get = ui.button("Get a key…", { onClick: () => call("claude.open_console") });
    rows.row("", ui.buttons(save, test, remove, get));
    const model = ui.select([], { onChange: (v) => put("claude_model", v) });
    // the models the key can use, as the Models API listed them at the last
    // Test, behind Automatic: what this release recommends
    controls.claude_model = Object.assign(model, {
      set(value) {
        const s = claude.status;
        const choices = [["auto", `${t("Automatic")} (${s.recommended_name})`],
                         ...s.models.map((m) => [m.id, m.name])];
        if (value !== "auto" && !s.models.some((m) => m.id === value)) choices.push([value, value]);
        model.replaceChildren(...choices.map(([id, name]) => new Option(name, id)));
        model.value = value;
      },
    });
    rows.row("Model", model,
             { tip: "Automatic is the model this version of KudioStudio recommends, and moves on with it. Test lists the others the key can use." });
    const showClaude = () => {
      const s = claude.status;
      if (s.missing) keyLine.show(t(s.missing), "warn");
      else if (s.source === "saved") keyLine.show(`${t("Key saved in the system's credential store:")} ${s.hint}`, "ok");
      else if (s.source === "environment") keyLine.show(t("Key from the ANTHROPIC_API_KEY variable."), "ok");
      else keyLine.show(t("No key yet. Paste yours and save it, or set ANTHROPIC_API_KEY."), "");
      for (const control of [keyBox, save, test, remove, model]) control.disabled = Boolean(s.missing);
      remove.disabled = Boolean(s.missing) || s.source !== "saved";
      controls.claude_model.set(info.values.claude_model);
    };
    showClaude();
    fill();

    const row = document.createElement("div");
    row.className = "button-row";
    const reset = ui.button("Restore defaults", {
      onClick: async () => { info = await call("options.reset"); fill(); },
    });
    const close = document.createElement("button");
    close.type = "submit";
    close.className = "btn primary";
    close.textContent = t("Close");
    close.dataset.focus = "";
    row.append(reset, close);
    form.append(row);
    form.addEventListener("submit", (e) => { e.preventDefault(); done(true); });
  });
  // a new language closes the dialog, and it opens again in that language
  if (language && changeLanguage) {
    await changeLanguage(language);
    await openSettings();
  }
}
