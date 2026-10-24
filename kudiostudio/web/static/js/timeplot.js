// A canvas whose x axis is time, with a selection, a playhead and markers.
//
// The waveform and the spectrogram show different things but behave the same
// along time: the same drag to select, the same click to seek, the same wheel
// to zoom. That behaviour lives here once, so the two cannot disagree about
// where "1.2 seconds" is.

import { ticks, timeTick } from "./format.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** A drag shorter than this is a click. */
const CLICK_PIXELS = 3;
/** How close to a selection edge grabs the edge instead of starting anew. */
const EDGE_PIXELS = 6;

/** The right margin, and the wider one a second axis needs there. Both
 *  plots take the same one at the same time: they share a time axis, and a
 *  gutter on one and not the other would put one instant in two places. */
const RIGHT = 14;
export const RIGHT_GUTTER = 62;

export class TimePlot {
  /**
   * @param canvas   the element to draw on
   * @param view     the shared TimeView
   * @param hooks    { onSelect(t0, t1), onSeek(t), onHover(t, y) | onHover(null) }
   */
  constructor(canvas, view, hooks = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.view = view;
    this.hooks = hooks;
    this.margin = { left: 64, right: RIGHT, top: 10, bottom: 24 };
    this.selection = null;      // [t0, t1] or null
    this.playhead = null;       // seconds or null
    this.markers = [];          // [{ start, end, label }]
    this.hoverX = null;
    this.drag = null;
    this.colors = {};
    this.pending = false;
    this.readColors();

    new ResizeObserver(() => this.resize()).observe(canvas);
    view.onChange(() => { this.viewChanged(); this.requestDraw(); });
    canvas.addEventListener("pointerdown", (e) => this.pointerDown(e));
    canvas.addEventListener("pointermove", (e) => this.pointerMove(e));
    canvas.addEventListener("pointerup", (e) => this.pointerUp(e));
    canvas.addEventListener("pointerleave", () => this.leave());
    canvas.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    canvas.addEventListener("dblclick", (e) => this.doubleClick(e));
  }

  readColors() {
    for (const name of ["canvas", "border", "edge", "axis", "accent", "marker", "text", "hint"]) {
      this.colors[name] = css(`--${name}`);
    }
  }

  // ------------------------------------------------------------ geometry

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (w === 0 || h === 0) return;
    this.dpr = dpr;
    this.width = w;
    this.height = h;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.resized();
    this.requestDraw();
  }

  /** The drawing area inside the axes, in CSS pixels. */
  get area() {
    const m = this.margin;
    return { x: m.left, y: m.top, w: Math.max(1, this.width - m.left - m.right),
             h: Math.max(1, this.height - m.top - m.bottom) };
  }

  timeToX(t) {
    const a = this.area;
    return a.x + ((t - this.view.t0) / this.view.span) * a.w;
  }

  xToTime(x) {
    const a = this.area;
    return this.view.t0 + ((x - a.x) / a.w) * this.view.span;
  }

  inArea(x, y) {
    const a = this.area;
    return x >= a.x && x <= a.x + a.w && y >= a.y && y <= a.y + a.h;
  }

  // ------------------------------------------------------------ drawing

  requestDraw() {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(() => {
      this.pending = false;
      this.draw();
    });
  }

  draw() {
    if (!this.width) return;
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    const a = this.area;
    ctx.fillStyle = this.colors.canvas;
    roundRect(ctx, a.x, a.y, a.w, a.h, 6);
    ctx.fill();

    ctx.save();
    roundRect(ctx, a.x, a.y, a.w, a.h, 6);
    ctx.clip();
    this.drawContent(ctx, a);
    this.drawTimeGrid(ctx, a);
    this.drawMarkers(ctx, a);
    this.drawSelection(ctx, a);
    this.drawPlayhead(ctx, a);
    this.drawHover(ctx, a);
    ctx.restore();

    this.drawTimeAxis(ctx, a);
    this.drawValueAxis(ctx, a);
    this.drawRightAxis(ctx, a);
    ctx.strokeStyle = this.colors.border;
    ctx.lineWidth = 1;
    roundRect(ctx, a.x + 0.5, a.y + 0.5, a.w - 1, a.h - 1, 6);
    ctx.stroke();
  }

  /** What the plot is of; drawn under the grid and the overlays. */
  drawContent() {}

  /** The vertical axis's labels, in the left margin. */
  drawValueAxis() {}

  /** A second axis in the right gutter, when the plot has one. */
  drawRightAxis() {}

  /** Make room on the right for a second axis, or give it back. */
  setRightGutter(on) {
    const right = on ? RIGHT_GUTTER : RIGHT;
    if (this.margin.right === right) return;
    this.margin.right = right;
    this.resized();
    this.requestDraw();
  }

  drawTimeGrid(ctx, a) {
    const { values } = ticks(this.view.t0, this.view.t1, a.w, 90);
    ctx.strokeStyle = "rgba(127, 150, 175, 0.10)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const v of values) {
      const x = Math.round(this.timeToX(v)) + 0.5;
      ctx.moveTo(x, a.y);
      ctx.lineTo(x, a.y + a.h);
    }
    ctx.stroke();
  }

  drawTimeAxis(ctx, a) {
    const { step, values } = ticks(this.view.t0, this.view.t1, a.w, 90);
    ctx.fillStyle = this.colors.axis;
    ctx.font = `11px ${monoFont()}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const v of values) {
      const x = this.timeToX(v);
      if (x < a.x + 12 || x > a.x + a.w - 12) continue;
      ctx.fillText(timeTick(v, step), x, a.y + a.h + 6);
    }
  }

  drawMarkers(ctx, a) {
    if (!this.markers.length) return;
    ctx.save();
    for (const m of this.markers) {
      const x0 = this.timeToX(m.start);
      if (m.end !== null && m.end !== undefined && m.end > m.start) {
        const x1 = this.timeToX(m.end);
        ctx.fillStyle = "rgba(169, 147, 245, 0.10)";
        ctx.fillRect(x0, a.y, x1 - x0, a.h);
      }
      ctx.strokeStyle = this.colors.marker;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(Math.round(x0) + 0.5, a.y);
      ctx.lineTo(Math.round(x0) + 0.5, a.y + a.h);
      ctx.stroke();
    }
    ctx.restore();
  }

  drawSelection(ctx, a) {
    const s = this.selection;
    if (!s) return;
    const x0 = this.timeToX(s[0]);
    const x1 = this.timeToX(s[1]);
    ctx.fillStyle = this.selectionFill();
    ctx.fillRect(x0, a.y, x1 - x0, a.h);
    ctx.strokeStyle = this.colors.accent;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (const x of [x0, x1]) {
      ctx.moveTo(x, a.y);
      ctx.lineTo(x, a.y + a.h);
    }
    ctx.stroke();
  }

  /** A wash that reads as "highlighted" over a dark trace; plots that draw
   *  a bright image turn it down. */
  selectionFill() { return "rgba(255, 176, 46, 0.16)"; }

  drawPlayhead(ctx, a) {
    if (this.playhead === null) return;
    const x = Math.round(this.timeToX(this.playhead)) + 0.5;
    if (x < a.x || x > a.x + a.w) return;
    ctx.save();
    ctx.strokeStyle = this.colors.accent;
    ctx.shadowColor = this.colors.accent;
    ctx.shadowBlur = 8;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, a.y);
    ctx.lineTo(x, a.y + a.h);
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = this.colors.accent;
    ctx.beginPath();
    ctx.moveTo(x - 5, a.y);
    ctx.lineTo(x + 5, a.y);
    ctx.lineTo(x, a.y + 6);
    ctx.closePath();
    ctx.fill();
  }

  drawHover(ctx, a) {
    if (this.hoverX === null || this.drag) return;
    ctx.strokeStyle = "rgba(214, 226, 238, 0.25)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const x = Math.round(this.hoverX) + 0.5;
    ctx.moveTo(x, a.y);
    ctx.lineTo(x, a.y + a.h);
    ctx.stroke();
  }

  // ------------------------------------------------------------ state

  setSelection(selection) { this.selection = selection; this.requestDraw(); }
  setPlayhead(seconds) { this.playhead = seconds; this.requestDraw(); }
  setMarkers(markers) { this.markers = markers || []; this.requestDraw(); }

  /** Called when the view changes; a subclass fetches new data here. */
  viewChanged() {}
  resized() {}

  // ------------------------------------------------------------ input

  pointerDown(e) {
    if (!this.view.duration) return;
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    if (!this.inArea(x, y)) return;
    this.canvas.setPointerCapture(e.pointerId);
    if (e.button === 1 || (e.button === 0 && e.altKey)) {
      this.drag = { mode: "pan", x, t0: this.view.t0 };
      return;
    }
    if (e.button !== 0) return;
    let mode = "pending";
    let anchor = this.xToTime(x);
    if (this.selection) {
      const xa = this.timeToX(this.selection[0]);
      const xb = this.timeToX(this.selection[1]);
      if (Math.abs(x - xa) <= EDGE_PIXELS) { mode = "edge"; anchor = this.selection[1]; }
      else if (Math.abs(x - xb) <= EDGE_PIXELS) { mode = "edge"; anchor = this.selection[0]; }
    }
    this.drag = { mode, x, anchor };
  }

  pointerMove(e) {
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const d = this.drag;
    if (!d) {
      const inside = this.inArea(x, y) && this.view.duration > 0;
      this.hoverX = inside ? x : null;
      this.canvas.style.cursor = inside && this.nearEdge(x) ? "ew-resize" : "";
      this.hooks.onHover?.(inside ? this.xToTime(x) : null, inside ? this.valueAt(y) : null);
      this.requestDraw();
      return;
    }
    if (d.mode === "pan") {
      const dt = ((x - d.x) / this.area.w) * this.view.span;
      this.view.set(d.t0 - dt, d.t0 - dt + this.view.span);
      return;
    }
    if (d.mode === "pending" && Math.abs(x - d.x) < CLICK_PIXELS) return;
    if (d.mode === "pending") d.mode = "select";
    const t = Math.min(Math.max(this.xToTime(x), 0), this.view.duration);
    this.selection = [Math.min(d.anchor, t), Math.max(d.anchor, t)];
    this.hooks.onSelecting?.(this.selection);
    this.requestDraw();
  }

  pointerUp(e) {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (d.mode === "pending") {
      const r = this.canvas.getBoundingClientRect();
      this.hooks.onSeek?.(Math.min(Math.max(this.xToTime(e.clientX - r.left), 0), this.view.duration));
    } else if ((d.mode === "select" || d.mode === "edge") && this.selection) {
      this.hooks.onSelect?.(this.selection[0], this.selection[1]);
    }
  }

  leave() {
    this.hoverX = null;
    this.hooks.onHover?.(null, null);
    this.requestDraw();
  }

  nearEdge(x) {
    if (!this.selection) return false;
    return Math.abs(x - this.timeToX(this.selection[0])) <= EDGE_PIXELS ||
           Math.abs(x - this.timeToX(this.selection[1])) <= EDGE_PIXELS;
  }

  wheel(e) {
    if (!this.view.duration) return;
    e.preventDefault();
    const r = this.canvas.getBoundingClientRect();
    const time = this.xToTime(e.clientX - r.left);
    // a trackpad's sideways swipe, or Shift with a wheel, scrolls; the plain
    // wheel zooms around the pointer, as in every audio editor
    const sideways = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : (e.shiftKey ? e.deltaY : 0);
    if (sideways) {
      this.view.pan((sideways / this.area.w) * this.view.span);
      return;
    }
    this.view.zoomAt(time, Math.exp(e.deltaY * 0.0015));
  }

  /** The vertical axis's value at *y*, for the hover readout. */
  valueAt() { return null; }

  /** Zoom to the selection or out to the whole clip; a plot with a
   *  vertical axis of its own can take double-clicks on that instead. */
  doubleClick() { this.hooks.onDoubleClick?.(); }
}

export function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export function monoFont() {
  return getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim() || "monospace";
}
