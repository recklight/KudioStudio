// The time-frequency image: the server sends the visible stretch as bytes,
// already scaled to its colour range, and this colours them with the
// spectrogram's map and draws them under the shared overlays.

import { data } from "./api.js";
import { hertz, ticks } from "./format.js";
import { TimePlot, monoFont } from "./timeplot.js";

export class SpectrogramPlot extends TimePlot {
  constructor(canvas, view, hooks) {
    super(canvas, view, hooks);
    this.version = -1;
    this.lut = null;            // 256 x RGB
    this.image = null;          // { canvas, t0, t1 }
    this.yMax = 1;
    this.yUnits = "";
    this.label = "";
    this.controller = null;
    this.timer = null;
    this.error = "";
    this.offscreen = document.createElement("canvas");
  }

  async loadColormap() {
    const response = await fetch("/colormap.bin");
    this.lut = new Uint8Array(await response.arrayBuffer());
  }

  setAudio(version) {
    this.version = version;
    this.image = null;
    this.error = "";
    this.fetchSoon(0);
  }

  /** The analysis changed but not the audio: keep the old picture up until
   *  the new one arrives, so the plot does not flash empty. */
  refresh() { this.fetchSoon(0); }

  clear() {
    this.version = -1;
    this.image = null;
    this.requestDraw();
  }

  viewChanged() { this.fetchSoon(40); }
  resized() { this.fetchSoon(0); }

  fetchSoon(delay) {
    if (this.version < 0) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.fetch(), delay);
  }

  async fetch() {
    if (this.version < 0 || !this.width || !this.lut) return;
    this.controller?.abort();
    this.controller = new AbortController();
    const dpr = this.dpr || 1;
    const asked = {
      t0: this.view.t0,
      t1: this.view.t1,
      columns: Math.min(4096, Math.max(1, Math.round(this.area.w * dpr))),
      rows: Math.min(1024, Math.max(1, Math.round(this.area.h * dpr))),
    };
    try {
      const { meta, buffer } = await data("image", asked, this.controller.signal);
      if (Number(meta.version) !== this.version || meta.kind !== "image") return;
      const rows = Number(meta.rows);
      const columns = Number(meta.columns);
      this.yMax = Number(meta.ymax);
      this.yUnits = meta.units || "";
      this.label = meta.label || "";
      this.error = "";
      if (!rows || !columns) { this.image = null; this.requestDraw(); return; }
      const pixels = new Uint8Array(buffer);
      const off = this.offscreen;
      off.width = columns;
      off.height = rows;
      const octx = off.getContext("2d");
      const rgba = octx.createImageData(columns, rows);
      const out = rgba.data;
      const lut = this.lut;
      for (let i = 0, j = 0; i < pixels.length; i++, j += 4) {
        const k = pixels[i] * 3;
        out[j] = lut[k];
        out[j + 1] = lut[k + 1];
        out[j + 2] = lut[k + 2];
        out[j + 3] = 255;
      }
      octx.putImageData(rgba, 0, 0);
      this.image = { t0: Number(meta.t0), t1: Number(meta.t1) };
      this.requestDraw();
    } catch (error) {
      if (error.name === "AbortError") return;
      this.error = error.message;
      this.image = null;
      this.requestDraw();
    }
  }

  selectionFill() { return "rgba(255, 176, 46, 0.07)"; }

  drawContent(ctx, a) {
    if (this.image) {
      const x0 = this.timeToX(this.image.t0);
      const x1 = this.timeToX(this.image.t1);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(this.offscreen, x0, a.y, x1 - x0, a.h);
    }
    if (this.error) {
      ctx.fillStyle = this.colors.hint;
      ctx.font = `12px ${monoFont()}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(this.error, a.x + a.w / 2, a.y + a.h / 2);
    }
  }

  yOf(value, a) { return a.y + a.h - (value / this.yMax) * a.h; }

  valueAt(y) {
    const a = this.area;
    return ((a.y + a.h - y) / a.h) * this.yMax;
  }

  drawValueAxis(ctx, a) {
    const { values } = ticks(0, this.yMax, a.h, 34);
    ctx.fillStyle = this.colors.axis;
    ctx.font = `11px ${monoFont()}`;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const v of values) {
      const y = this.yOf(v, a);
      if (y < a.y + 6 || y > a.y + a.h - 4) continue;
      ctx.fillText(this.yUnits === "Hz" ? hertz(v) : String(Math.round(v)), a.x - 8, y);
    }
    if (this.yUnits === "Hz") {
      ctx.save();
      ctx.fillStyle = this.colors.hint;
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillText("Hz", a.x - 8, a.y);
      ctx.restore();
    }
  }
}
