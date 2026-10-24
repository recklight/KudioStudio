// The waveform: per pixel column, the lowest and highest sample in it, as a
// filled band; zoomed in past two samples a column, the samples themselves.
// Over it, when asked, the short-term loudness on an axis of its own.

import { data } from "./api.js";
import { ticks } from "./format.js";
import { TimePlot, monoFont } from "./timeplot.js";

/** Smallest loudness span the right axis shows, in LU. A steady take varies
 *  by a fraction of a dB, and scaling to that magnifies its own dither into
 *  a level that looks all over the place. */
const MIN_LOUDNESS_SPAN = 12;

export class WaveformPlot extends TimePlot {
  constructor(canvas, view, hooks) {
    super(canvas, view, hooks);
    this.version = -1;
    this.peak = 1;
    this.chunk = null;          // { kind, start, step, values }
    this.controller = null;
    this.timer = null;
    this.waveColor = getComputedStyle(document.documentElement).getPropertyValue("--wave").trim();
    this.loudnessColor = getComputedStyle(document.documentElement).getPropertyValue("--loudness").trim();
    this.loudness = null;       // { times, lufs, lo, hi } while shown
  }

  /** Short-term loudness over the waveform, or null to take it down. */
  setLoudness(curve) {
    if (!curve || !curve.times.length) {
      this.loudness = null;
    } else {
      const finite = curve.lufs.filter((v) => v !== null);
      let lo = Math.min(...finite);
      let hi = Math.max(...finite);
      if (!finite.length) { lo = -70; hi = 0; }
      if (hi - lo < MIN_LOUDNESS_SPAN) {
        const middle = (hi + lo) / 2;
        lo = middle - MIN_LOUDNESS_SPAN / 2;
        hi = middle + MIN_LOUDNESS_SPAN / 2;
      }
      this.loudness = { ...curve, lo: lo - 1, hi: hi + 1 };
    }
    this.setRightGutter(Boolean(this.loudness));
    this.requestDraw();
  }

  lufsY(value, a) {
    const { lo, hi } = this.loudness;
    return a.y + a.h - ((value - lo) / (hi - lo)) * a.h;
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

  /** A take's rolling waveform, from a `monitor` event: drawn as it is
   *  sent, on full scale so the level reads as a level, and nothing is
   *  fetched while it is up. The next setAudio replaces it. */
  live(wave) {
    this.version = -1;
    this.peak = 1;
    this.chunk = wave ? { kind: wave.kind, start: wave.start, step: wave.step,
                          values: new Float32Array(wave.values) } : null;
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
    if (c && c.values.length) {
      if (c.kind === "peaks") this.drawPeaks(ctx, a, c);
      else this.drawSamples(ctx, a, c);
    }
    this.drawLoudness(ctx, a);
  }

  /** -inf, digital silence, comes as null: a gap in the line, which is what
   *  silence should look like, rather than a drop to some floor. */
  drawLoudness(ctx, a) {
    const curve = this.loudness;
    if (!curve) return;
    ctx.save();
    ctx.strokeStyle = this.loudnessColor;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    let open = false;
    for (let i = 0; i < curve.times.length; i++) {
      const v = curve.lufs[i];
      if (v === null) { open = false; continue; }
      const x = this.timeToX(curve.times[i]);
      const y = this.lufsY(v, a);
      if (open) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      open = true;
    }
    ctx.stroke();
    ctx.restore();
  }

  drawRightAxis(ctx, a) {
    if (!this.loudness) return;
    const { lo, hi } = this.loudness;
    const { values } = ticks(lo, hi, a.h, 26);
    ctx.fillStyle = this.loudnessColor;
    ctx.font = `11px ${monoFont()}`;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const v of values) {
      const y = this.lufsY(v, a);
      if (y < a.y + 14 || y > a.y + a.h - 6) continue;
      ctx.fillText(String(Math.round(v)), a.x + a.w + 8, y);
    }
    ctx.textBaseline = "top";
    ctx.fillText("LUFS", a.x + a.w + 8, a.y);
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
