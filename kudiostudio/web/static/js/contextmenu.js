// The menu a right click on the waveform or the spectrogram opens: what can
// be done at that point, and with the selection when there is one. Every
// item that has a key shows it, so the menu also teaches the keys, and each
// runs the window's own command, so it can be undone and goes into the
// exported script like the same edit made any other way.
//
// On the spectrogram's frequency axis the menu is the axis's own: its range,
// the trade between time and frequency detail, and the view.

import { hertz } from "./format.js";
import { menuText } from "./menus.js";

let open = null;

export function closeContextMenu() {
  open?.remove();
  open = null;
}

document.addEventListener("mousedown", (e) => {
  if (open && !open.contains(e.target)) closeContextMenu();
}, true);
document.addEventListener("keydown", (e) => {
  if (open && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeContextMenu(); }
}, true);
for (const type of ["blur", "resize", "wheel"]) window.addEventListener(type, closeContextMenu);

/** Show *items*, as the menu bar takes them ({ label, keys, run, enabled,
 *  checked } or "-"), at the pointer of event *e*. */
export function showContextMenu(e, items) {
  closeContextMenu();
  const list = document.createElement("div");
  list.className = "menu context-menu";
  list.setAttribute("role", "menu");
  let rule = false;
  for (const item of items) {
    // a rule only between two groups that are both there
    if (item === "-") { rule = list.children.length > 0; continue; }
    if (rule) {
      const line = document.createElement("div");
      line.className = "menu-rule";
      list.append(line);
      rule = false;
    }
    const [label, keys] = menuText(item.label);
    const row = document.createElement("button");
    row.className = "menu-item";
    row.setAttribute("role", "menuitem");
    row.disabled = item.enabled ? !item.enabled() : false;
    const mark = document.createElement("span");
    mark.className = "menu-check";
    if (item.checked) mark.textContent = item.checked() ? "✓" : "";
    const text = document.createElement("span");
    text.className = "menu-label";
    text.textContent = label;
    const shortcut = document.createElement("span");
    shortcut.className = "menu-keys mono";
    shortcut.textContent = item.keys || keys;
    row.append(mark, text, shortcut);
    row.addEventListener("click", () => { closeContextMenu(); item.run(); });
    list.append(row);
  }
  if (!list.children.length) return;
  document.body.append(list);
  open = list;
  // the pointer is in the window's pixels and the menu is placed in the
  // page's, which differ under the interface size from Settings; kept on
  // screen by opening up or to the left near an edge
  const zoom = parseFloat(document.documentElement.style.zoom) || 1;
  const box = list.getBoundingClientRect();
  const left = e.clientX + box.width > window.innerWidth ? e.clientX - box.width : e.clientX;
  const top = e.clientY + box.height > window.innerHeight
    ? Math.max(4, window.innerHeight - box.height - 4) : e.clientY;
  list.style.left = `${Math.max(4, left) / zoom}px`;
  list.style.top = `${top / zoom}px`;
}

/** The items for a right click: *at* is { which: "wave" | "spec", time,
 *  value, onAxis }, *s* the window's state with the spectrogram's own
 *  { mode, nFft, hop, yZoomed, units, label }, and *act* the functions that
 *  do the work. Items that cannot apply are left out rather than greyed, so
 *  the menu stays short. */
export function plotMenuItems(at, s, act) {
  const sel = s.selection;
  if (at.onAxis && at.which === "spec") {
    const n = s.nFft;
    return [
      { label: "Reset the frequency range", run: act.resetY, enabled: () => s.yZoomed },
      "-",
      ...(s.mode === "mfcc" ? [] : [
        { label: `Finer frequency detail (n_fft ${n * 2})`, run: () => act.resolution(2),
          enabled: () => n * 2 <= 8192 },
        { label: `Finer time detail (n_fft ${n / 2})`, run: () => act.resolution(0.5),
          enabled: () => n / 2 >= 64 },
      ]),
      "-",
      ...["stft", "mel", "mfcc"].map((mode) => ({
        label: { stft: "STFT", mel: "Mel", mfcc: "MFCC" }[mode],
        run: () => act.mode(mode), checked: () => s.mode === mode,
      })),
    ];
  }
  const t = at.onAxis ? null : at.time;
  const here = t !== null;
  const items = [];
  if (s.playing) items.push({ label: "Stop", keys: "Space", run: act.stop });
  else if (here) items.push({ label: "Play from here", run: () => act.play(t) });
  if (sel) items.push({ label: "Play the selection", run: () => act.play(sel[0], sel[1]) });
  if (here) items.push({ label: "Move the playhead here", run: () => act.seek(t) });
  items.push("-");
  if (sel) {
    items.push({ label: "Cu&t\tCtrl+X", run: () => act.edit("cut") },
               { label: "&Copy\tCtrl+C", run: () => act.edit("copy") },
               { label: "&Delete\tDel", run: () => act.edit("delete") },
               { label: "Crop to selectio&n", keys: "Ctrl+T", run: () => act.edit("crop") },
               { label: "Save &selection as…", keys: "Ctrl+Shift+E", run: act.saveSelection });
  }
  if (sel && s.can_paste) {
    items.push({ label: "Paste over the selection", keys: "Ctrl+V", run: () => act.edit("paste") });
  }
  if (here && s.can_paste) items.push({ label: "Paste here", run: () => act.pasteAt(t) });
  if (here) items.push({ label: "Insert silence here…", run: () => act.silenceAt(t) });
  items.push("-");
  if (here) items.push({ label: "Add a marker here", run: () => act.markAt(t) });
  if (sel) items.push({ label: "Mark the selection", run: act.markSelection });
  items.push("-");
  if (sel) items.push({ label: "Zoom to &selection", keys: "Ctrl+E", run: act.zoomToSelection });
  items.push({ label: "Zoom &out to whole clip", keys: "Ctrl+F", run: act.zoomFull });
  items.push({ label: "Select &All", keys: "Ctrl+A", run: act.selectAll });
  if (sel) items.push({ label: "&Clear selection", keys: "Esc", run: act.clearSelection });
  if (here) {
    const point = at.which === "wave"
      ? `${t.toFixed(3)} s, ${at.value.toFixed(3)}`
      : s.units === "Hz" ? `${t.toFixed(3)} s, ${hertz(at.value)}Hz`
        : `${t.toFixed(3)} s, ${s.label} ${Math.floor(at.value)}`;
    items.push("-", { label: `Copy ${point}`, run: () => act.copy(point) });
  }
  if (at.which === "spec" && s.yZoomed) {
    items.push({ label: "Reset the frequency range", run: act.resetY });
  }
  return items;
}
