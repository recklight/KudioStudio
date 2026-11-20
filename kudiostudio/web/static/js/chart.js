// A line or point chart in SVG, for what does not run along the open
// clip's time: a folder's averaged spectrum, one value per take over a day.
// The waveform and the spectrogram draw on canvas because they redraw on
// every drag; these redraw only on a zoom or a click in the legend, and SVG
// keeps the text sharp and every point something the mouse can find.

import { decimals, ticks } from "./format.js";
import { t } from "./i18n.js";

const NS = "http://www.w3.org/2000/svg";

/** Series colours: the window's cyan first, then hues far enough from it
 *  and from each other to tell a dozen folders apart on the dark canvas. */
export const SERIES_COLORS = ["#1fc6f0", "#f5a623", "#8bd346", "#ef6f9a", "#b18cff",
                              "#4fd1c5", "#ff8a5c", "#d9d65a", "#7aa7ff", "#c0c8d2"];

/** The drawing's own units; the SVG scales to its box and keeps this shape. */
const W = 900;
const H = 400;
const M = { left: 64, right: 16, top: 12, bottom: 34 };
/** How near, in drawing units, a point has to be to the mouse to be found. */
const PICK = 10;
/** A drag narrower than this is a click. */
const DRAG = 6;

let clips = 0;

function shape(tag, attrs = {}, parent = null, text = null) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== null) n.textContent = text;
  parent?.append(n);
  return n;
}

/** 1, 2 and 5 of each decade between *lo* and *hi*, or only the decades
 *  when there is not the room. */
export function logTicks(lo, hi, pixels) {
  const out = [];
  const decades = Math.log10(hi / lo);
  const steps = pixels / Math.max(decades, 1e-9) > 90 ? [1, 2, 5] : [1];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of steps) {
      const v = m * 10 ** e;
      if (v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9)) out.push(v);
    }
  }
  return out;
}

/** The index in sorted *xs* nearest to *x*. */
function nearest(xs, x) {
  let lo = 0;
  let hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] < x) lo = mid; else hi = mid;
  }
  return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
}

/**
 * @param series  [{ name, x: [], y: [] with null for nothing, flags?: [],
 *                  label?: [] }], each x sorted; label names a point in the
 *                readout
 * @param opts    { xLabel, yLabel, logX, points, spans: [[x0, x1]],
 *                  xTicks(lo, hi, pixels, spacing), xTick(v, step), xRead(v),
 *                  yRead(v), onPick(series, index) }
 *                points draws a dot per value, flagged ones ringed, rather
 *                than a line; spans are shaded, the timeline's gaps
 * @returns       a div: the legend, the drawing and a line that reads out
 *                what is under the mouse. A drag across the drawing zooms
 *                to the stretch dragged over, a double-click zooms out, and
 *                a click on a name in the legend hides or shows its series,
 *                the axes fitting what is left.
 */
export function chart(series, opts = {}) {
  const el = document.createElement("div");
  el.className = "chart";
  const legend = document.createElement("div");
  legend.className = "chart-legend";
  const root = shape("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg" });
  const readout = document.createElement("div");
  readout.className = "chart-readout mono";
  el.append(legend, root, readout);
  const xTick = opts.xTick ?? ((v, step) => v.toFixed(decimals(step)));
  const xRead = opts.xRead ?? ((v) => String(v));
  const yRead = opts.yRead ?? ((v) => (v === null ? "—" : v.toFixed(1)));
  const idle = t("Point at the chart to read it, drag across it to zoom in and double-click to see it all; a click on a name in the legend hides or shows it.");
  const a = { x: M.left, y: M.top, w: W - M.left - M.right, h: H - M.top - M.bottom };
  const clipId = `chart-clip-${++clips}`;
  const hidden = new Set();
  let range = null;                 // [x0, x1] once zoomed in
  let geo = null;                   // the scales of the drawing on screen
  let overlay = null;               // the cursor, the ring and the drag box

  series.forEach((s, i) => {
    const key = document.createElement("button");
    key.type = "button";
    key.className = "chart-key";
    key.style.setProperty("--key", SERIES_COLORS[i % SERIES_COLORS.length]);
    key.textContent = s.name;
    key.addEventListener("click", () => {
      if (hidden.has(i)) hidden.delete(i); else hidden.add(i);
      key.classList.toggle("off", hidden.has(i));
      draw();
    });
    legend.append(key);
  });

  const visible = (i) => !hidden.has(i) && series[i].x.length > 0;
  const usable = (x) => !opts.logX || x > 0;
  const inRange = (x) => usable(x) && (!range || (x >= range[0] && x <= range[1]));

  function draw() {
    root.replaceChildren();
    geo = null;
    readout.textContent = idle;
    let [x0, x1, y0, y1] = [Infinity, -Infinity, Infinity, -Infinity];
    series.forEach((s, i) => {
      if (!visible(i)) return;
      s.x.forEach((x, j) => {
        if (!inRange(x)) return;
        x0 = Math.min(x0, x);
        x1 = Math.max(x1, x);
        const y = s.y[j];
        if (y === null || !Number.isFinite(y)) return;
        y0 = Math.min(y0, y);
        y1 = Math.max(y1, y);
      });
    });
    if (range) [x0, x1] = range;
    if (!Number.isFinite(x0)) {
      readout.textContent = t("Nothing to draw.");
      return;
    }
    if (!Number.isFinite(y0)) [y0, y1] = [0, 1];
    if (!(x1 > x0)) [x0, x1] = opts.logX ? [x0 / 2, x0 * 2] : [x0 - 1, x0 + 1];
    if (!(y1 > y0)) [y0, y1] = [y0 - 1, y0 + 1];
    const pad = (y1 - y0) * 0.05;
    y0 -= pad;
    y1 += pad;
    const lx = (v) => (opts.logX ? Math.log10(v) : v);
    const X = (v) => a.x + ((lx(v) - lx(x0)) / (lx(x1) - lx(x0))) * a.w;
    const Y = (v) => a.y + a.h - ((v - y0) / (y1 - y0)) * a.h;
    const fromX = (px) => {
      const u = lx(x0) + ((px - a.x) / a.w) * (lx(x1) - lx(x0));
      return opts.logX ? 10 ** u : u;
    };
    geo = { X, Y, fromX };

    const clip = shape("clipPath", { id: clipId }, shape("defs", {}, root));
    shape("rect", { x: a.x, y: a.y, width: a.w, height: a.h }, clip);
    shape("rect", { x: a.x, y: a.y, width: a.w, height: a.h, rx: 6, class: "chart-area" }, root);
    for (const [s0, s1] of opts.spans ?? []) {
      const p = X(Math.max(s0, x0));
      const q = X(Math.min(s1, x1));
      if (q > p) shape("rect", { x: p, y: a.y, width: Math.max(q - p, 1.5), height: a.h,
                               class: "chart-span" }, root);
    }
    const xt = opts.logX ? { values: logTicks(x0, x1, a.w), step: 0 }
                         : (opts.xTicks ?? ticks)(x0, x1, a.w, 110);
    for (const v of xt.values) {
      const x = X(v);
      shape("line", { x1: x, x2: x, y1: a.y, y2: a.y + a.h, class: "chart-grid" }, root);
      shape("text", { x, y: a.y + a.h + 16, "text-anchor": "middle", class: "chart-tick" }, root,
          xTick(v, xt.step));
    }
    const yt = ticks(y0, y1, a.h, 40);
    for (const v of yt.values) {
      const y = Y(v);
      shape("line", { x1: a.x, x2: a.x + a.w, y1: y, y2: y, class: "chart-grid" }, root);
      shape("text", { x: a.x - 8, y: y + 4, "text-anchor": "end", class: "chart-tick" }, root,
          v.toFixed(decimals(yt.step)));
    }
    if (opts.xLabel) {
      shape("text", { x: a.x + a.w, y: H - 2, "text-anchor": "end", class: "chart-label" }, root,
          opts.xLabel);
    }
    if (opts.yLabel) {
      shape("text", { x: 0, y: 0, "text-anchor": "middle", class: "chart-label",
                    transform: `translate(14 ${a.y + a.h / 2}) rotate(-90)` }, root, opts.yLabel);
    }

    const plotted = shape("g", { "clip-path": `url(#${clipId})` }, root);
    series.forEach((s, i) => {
      if (!visible(i)) return;
      const color = SERIES_COLORS[i % SERIES_COLORS.length];
      const g = shape("g", { class: "chart-series" }, plotted);
      if (opts.points) {
        s.x.forEach((x, j) => {
          const y = s.y[j];
          if (y === null || !inRange(x)) return;
          shape("circle", { cx: X(x), cy: Y(y), r: s.flags?.[j] ? 3.6 : 2.4, fill: color,
                          class: s.flags?.[j] ? "chart-flag" : "" }, g);
        });
      } else {
        let d = "";
        let open = false;
        s.x.forEach((x, j) => {
          const y = s.y[j];
          if (y === null || !usable(x)) { open = false; return; }
          d += `${open ? "L" : "M"}${X(x).toFixed(1)} ${Y(y).toFixed(1)}`;
          open = true;
        });
        shape("path", { d, stroke: color, class: "chart-line" }, g);
      }
    });

    overlay = {
      cursor: shape("line", { y1: a.y, y2: a.y + a.h, class: "chart-cursor" }, root),
      ring: shape("circle", { r: 6, class: "chart-ring" }, root),
      box: shape("rect", { y: a.y, height: a.h, class: "chart-drag" }, root),
    };
    for (const n of Object.values(overlay)) n.style.display = "none";
  }

  const point = (e) => {
    const r = root.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H];
  };
  const inside = (px, py) => px >= a.x && px <= a.x + a.w && py >= a.y && py <= a.y + a.h;
  /** The point nearest the mouse within PICK, as [series, index], or null. */
  const pick = (px, py) => {
    if (!geo) return null;
    const { X, Y, fromX } = geo;
    let best = null;
    let bestD = PICK * PICK;
    series.forEach((s, i) => {
      if (!visible(i)) return;
      // the dots near the mouse in x, then the nearest in the plane
      const lo = nearest(s.x, fromX(px - PICK));
      const hi = nearest(s.x, fromX(px + PICK));
      for (let j = lo; j <= hi; j++) {
        if (s.y[j] === null || !inRange(s.x[j])) continue;
        const d = (X(s.x[j]) - px) ** 2 + (Y(s.y[j]) - py) ** 2;
        if (d < bestD) { bestD = d; best = [i, j]; }
      }
    });
    return best;
  };
  const quiet = () => {
    if (overlay) overlay.cursor.style.display = overlay.ring.style.display = "none";
    readout.textContent = idle;
  };
  let dragFrom = null;

  root.addEventListener("pointerdown", (e) => {
    const [px, py] = point(e);
    if (!geo || !inside(px, py) || e.button !== 0) return;
    dragFrom = px;
    root.setPointerCapture?.(e.pointerId);
  });
  root.addEventListener("pointermove", (e) => {
    const [px, py] = point(e);
    if (!geo) return;
    const { X, Y, fromX } = geo;
    if (dragFrom !== null && Math.abs(px - dragFrom) > DRAG) {
      const left = Math.max(a.x, Math.min(px, dragFrom));
      const right = Math.min(a.x + a.w, Math.max(px, dragFrom));
      Object.assign(overlay.box.style, { display: "" });
      overlay.box.setAttribute("x", left);
      overlay.box.setAttribute("width", right - left);
      readout.textContent = `${xRead(fromX(left))} – ${xRead(fromX(right))}`;
      return;
    }
    if (!inside(px, py)) { quiet(); return; }
    if (opts.points) {
      const hit = pick(px, py);
      overlay.cursor.style.display = "none";
      if (!hit) { overlay.ring.style.display = "none"; readout.textContent = idle; return; }
      const [i, j] = hit;
      const s = series[i];
      overlay.ring.setAttribute("cx", X(s.x[j]));
      overlay.ring.setAttribute("cy", Y(s.y[j]));
      overlay.ring.style.display = "";
      readout.textContent = `${xRead(s.x[j])}   ${s.label?.[j] ?? s.name}   ${yRead(s.y[j])}`;
      return;
    }
    const x = fromX(px);
    overlay.cursor.setAttribute("x1", px);
    overlay.cursor.setAttribute("x2", px);
    overlay.cursor.style.display = "";
    const parts = series.map((s, i) => (visible(i) ? `${s.name} ${yRead(s.y[nearest(s.x, x)])}`
                                                   : null)).filter(Boolean);
    readout.textContent = `${xRead(x)}   ${parts.join("   ")}`;
  });
  root.addEventListener("pointerup", (e) => {
    if (dragFrom === null || !geo) return;
    const [px, py] = point(e);
    const from = dragFrom;
    dragFrom = null;
    overlay.box.style.display = "none";
    if (Math.abs(px - from) > DRAG) {
      const left = Math.max(a.x, Math.min(px, from));
      const right = Math.min(a.x + a.w, Math.max(px, from));
      range = [geo.fromX(left), geo.fromX(right)];
      draw();
      return;
    }
    const hit = opts.onPick && pick(px, py);
    if (hit) opts.onPick(...hit);
  });
  root.addEventListener("pointerleave", () => { if (dragFrom === null) quiet(); });
  root.addEventListener("dblclick", () => {
    if (range) { range = null; draw(); }
  });
  if (opts.onPick) root.classList.add("pickable");
  draw();
  return el;
}

/** A clock reading of *seconds* on a clock that reads the file names' own
 *  figures, so UTC, which applies no zone of its own. */
export function clockText(seconds, { date = true, millis = false } = {}) {
  const d = new Date(seconds * 1000);
  const two = (n) => String(n).padStart(2, "0");
  let out = `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}:${two(d.getUTCSeconds())}`;
  if (millis) out += `.${String(d.getUTCMilliseconds()).padStart(3, "0")}`;
  if (date) out = `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())} ${out}`;
  return out;
}

/** Steps a clock is read in, from a second to a week. */
const CLOCK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800,
                     21600, 43200, 86400, 172800, 604800];

/** Ticks on a clock axis at whole minutes, quarter hours, hours and days
 *  rather than at the round numbers of seconds that ticks() would give. */
export function clockTicks(lo, hi, pixels, spacing = 110) {
  const most = Math.max(1, pixels / spacing);
  const step = CLOCK_STEPS.find((s) => (hi - lo) / s <= most) ?? CLOCK_STEPS.at(-1);
  const values = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) values.push(v);
  return { step, values };
}

/** A tick on a clock axis: the seconds only when the ticks are seconds
 *  apart, the date only when they are a day or more apart. */
export function clockTick(seconds, step) {
  if (step >= 86400) return clockText(seconds).slice(5, 10);
  const text = clockText(seconds, { date: false });
  return step >= 60 ? text.slice(0, 5) : text;
}
