// The waveform: per pixel column, the lowest and highest sample in it, as a
// filled band; zoomed in past two samples a column, the samples themselves.

import { data } from "./api.js";
import { ticks } from "./format.js";
import { TimePlot, monoFont } from "./timeplot.js";

export class WaveformPlot extends TimePlot {
  constructor(canvas, view, hooks) {
    super(canvas, view, hooks);
    this.version = -1;
    this.peak = 1;
    this.chunk = null;          // { kind, start, step, values }
    this.controller = null;
    this.timer = null;
    this.waveColor = getComputedStyle(document.documentElement).getPropertyValue("--wave").trim();
  }

  /** New audio (or a new version of it): forget what was drawn. */
  setAudio(version, peak) {
    this.version = version;
    this.peak = peak > 0 ? peak : 1;
    this.chunk = null;
    this.fetchSoon(0);
  }

  clear() {
    this.version = -1;
    this.chunk = null;
    this.requestDraw();
  }

  viewChanged() { this.fetchSoon(30); }
  resized() { this.fetchSoon(0); }

  fetchSoon(delay) {
    if (this.version < 0) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.fetch(), delay);
  }

  async fetch() {
    if (this.version < 0 || !this.width) return;
    this.controller?.abort();
    this.controller = new AbortController();
    const columns = Math.min(4096, Math.max(1, Math.round(this.area.w * (this.dpr || 1))));
    const asked = { t0: this.view.t0, t1: this.view.t1, columns };
    try {
      const { meta, buffer } = await data("peaks", asked, this.controller.signal);
      if (Number(meta.version) !== this.version) return;    // an edit landed meanwhile
      this.chunk = {
        kind: meta.kind,
        start: Number(meta.start),
        step: Number(meta.step),
        values: new Float32Array(buffer),
      };
      this.requestDraw();
    } catch (error) {
      if (error.name !== "AbortError") console.warn("peaks", error);
    }
  }

  yOf(value, a) {
    return a.y + a.h / 2 - (value / this.peak) * (a.h / 2) * 0.94;
  }

  valueAt(y) {
    const a = this.area;
    return ((a.y + a.h / 2 - y) / ((a.h / 2) * 0.94)) * this.peak;
  }

  drawContent(ctx, a) {
    // amplitude grid: the zero line stronger than the rest
    const { values } = ticks(-this.peak, this.peak, a.h * 0.94, 30);
    ctx.lineWidth = 1;
    for (const v of values) {
      const y = Math.round(this.yOf(v, a)) + 0.5;
      ctx.strokeStyle = v === 0 ? "rgba(127, 150, 175, 0.28)" : "rgba(127, 150, 175, 0.08)";
      ctx.beginPath();
      ctx.moveTo(a.x, y);
      ctx.lineTo(a.x + a.w, y);
      ctx.stroke();
    }
    const c = this.chunk;
    if (!c || !c.values.length) return;
    if (c.kind === "peaks") this.drawPeaks(ctx, a, c);
    else this.drawSamples(ctx, a, c);
  }

  drawPeaks(ctx, a, c) {
    const n = c.values.length / 2;
    const gradient = ctx.createLinearGradient(0, a.y, 0, a.y + a.h);
    gradient.addColorStop(0, this.waveColor);
    gradient.addColorStop(0.5, "rgba(56, 182, 255, 0.55)");
    gradient.addColorStop(1, this.waveColor);
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = this.timeToX(c.start + (i + 0.5) * c.step);
      const y = this.yOf(c.values[2 * i + 1], a);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = n - 1; i >= 0; i--) {
      const x = this.timeToX(c.start + (i + 0.5) * c.step);
      // a column with no spread still has to show: give it half a pixel
      const y = Math.max(this.yOf(c.values[2 * i], a), this.yOf(c.values[2 * i + 1], a) + 0.5);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
    ctx.strokeStyle = "rgba(122, 210, 255, 0.55)";
    ctx.lineWidth = 0.8;
    ctx.stroke();
  }

  drawSamples(ctx, a, c) {
    const n = c.values.length;
    ctx.strokeStyle = this.waveColor;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = this.timeToX(c.start + i * c.step);
      const y = this.yOf(c.values[i], a);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    // close enough to see each sample: mark them
    const spacing = (c.step / this.view.span) * a.w;
    if (spacing >= 7) {
      ctx.fillStyle = this.waveColor;
      for (let i = 0; i < n; i++) {
        ctx.beginPath();
        ctx.arc(this.timeToX(c.start + i * c.step), this.yOf(c.values[i], a), 2.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  drawValueAxis(ctx, a) {
    const { values } = ticks(-this.peak, this.peak, a.h * 0.94, 30);
    ctx.fillStyle = this.colors.axis;
    ctx.font = `11px ${monoFont()}`;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const v of values) {
      const y = this.yOf(v, a);
      if (y < a.y + 6 || y > a.y + a.h - 6) continue;
      ctx.fillText(Math.abs(v) < 1e-9 ? "0" : v.toFixed(2).replace(/0+$/, "").replace(/\.$/, ""), a.x - 8, y);
    }
  }
}
