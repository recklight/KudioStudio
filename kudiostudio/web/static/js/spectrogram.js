// The time-frequency image: the server sends the visible stretch as bytes,
// already scaled to its colour range, and this colours them with the
// spectrogram's map and draws them under the shared overlays.
//
// The vertical axis zooms too: the wheel over the axis (or Ctrl and the
// wheel anywhere) narrows it around the pointer, and a double-click on the
// axis shows all of it again. The server cuts the bins before it pools them,
// so a zoom into the lowest few hundred hertz gets those bins at full
// resolution rather than a smear of a few rows.
//
// With the log axis on, the server spaces the rows evenly in log frequency
// from the first bin above DC, and the axis, the zoom and the pitch curve
// follow. A mel image is labelled with the centre frequency of its bands.

import { data } from "./api.js";
import { hertz, ticks } from "./format.js";
import { TimePlot, monoFont } from "./timeplot.js";
import { pointIn } from "./zoom.js";

/** How far the vertical axis can be narrowed: this share of its height. */
const MIN_Y_SHARE = 1 / 256;

export class SpectrogramPlot extends TimePlot {
  constructor(canvas, view, hooks) {
    super(canvas, view, hooks);
    this.version = -1;
    this.lut = null;            // 256 x RGB
    this.image = null;          // { t0, t1, y0, y1 } of what is in `offscreen`
    this.yMax = 1;
    this.yLo = 0;               // the stretch of the vertical axis on screen
    this.yHi = 1;
    this.yUnits = "";
    this.label = "";
    this.log = false;           // rows spaced evenly in log frequency
    this.yMin = 0;              // where a log axis starts
    this.bandsHz = null;        // a mel image's band centres, bottom first
    this.levels = null;         // { lo, hi, unit } of the colour scale
    this.controller = null;
    this.timer = null;
    this.error = "";
    this.offscreen = document.createElement("canvas");
    this.overlay = null;        // { times, hz } -- a panel's curve, in Hz
    this.pitchColor = getComputedStyle(document.documentElement).getPropertyValue("--pitch").trim();
  }

  /** A curve over the image, or null to take it down. */
  setOverlay(curve) {
    this.overlay = curve && curve.times.length ? curve : null;
    this.requestDraw();
  }

  /** The colours: the map Settings has, or *name*, after which the image
   *  is fetched and coloured again. */
  async loadColormap(name) {
    const response = await fetch(name ? `/colormap.bin?map=${encodeURIComponent(name)}`
                                      : "/colormap.bin");
    this.lut = new Uint8Array(await response.arrayBuffer());
    if (name && this.version >= 0) this.fetchSoon(0);
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

  /** A take's rolling image, from a `monitor` event, coloured on its fixed
   *  range by the server. Nothing is fetched while it is up; the next
   *  setAudio replaces it. */
  live(image, span) {
    this.version = -1;
    if (!image || !this.lut) { this.image = null; this.requestDraw(); return; }
    this.yMax = image.y_max;
    this.yLo = 0;
    this.yHi = image.y_max;
    this.yUnits = image.units;
    this.label = image.label;
    this.paint(Uint8Array.from(atob(image.data), (c) => c.charCodeAt(0)),
               image.rows, image.columns);
    this.image = { t0: 0, t1: span, y0: 0, y1: image.y_max };
    this.requestDraw();
  }

  /** Colour *pixels*, rows x columns of bytes, through the map into the
   *  offscreen canvas. */
  paint(pixels, rows, columns) {
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
  }

  viewChanged() { this.fetchSoon(40); }
  resized() { this.fetchSoon(0); }

  fetchSoon(delay) {
    if (this.version < 0) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.fetch(), delay);
  }

  /** Show *lo*..*hi* of the vertical axis, kept inside it. On a log axis
   *  the same, in log frequency. */
  setYRange(lo, hi) {
    let start, end;
    if (this.log) {
      const bottom = Math.log(this.yMin), top = Math.log(this.yMax);
      const whole = top - bottom;
      const span = Math.min(Math.max(Math.log(hi) - Math.log(Math.max(lo, this.yMin)),
                                     whole * MIN_Y_SHARE), whole);
      const from = Math.min(Math.max(Math.log(Math.max(lo, this.yMin)), bottom), top - span);
      start = Math.exp(from);
      end = Math.exp(from + span);
    } else {
      const span = Math.min(Math.max(hi - lo, this.yMax * MIN_Y_SHARE), this.yMax);
      start = Math.min(Math.max(lo, 0), this.yMax - span);
      end = start + span;
    }
    if (start === this.yLo && end === this.yHi) return;
    this.yLo = start;
    this.yHi = end;
    this.requestDraw();
    this.fetchSoon(40);
  }

  get yBottom() { return this.log ? this.yMin : 0; }

  get yZoomed() { return this.yLo > this.yBottom * (1 + 1e-9) || this.yHi < this.yMax; }

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
    if (this.yZoomed) Object.assign(asked, { y0: this.yLo, y1: this.yHi });
    try {
      const { meta, buffer } = await data("image", asked, this.controller.signal);
      if (Number(meta.version) !== this.version || meta.kind !== "image") return;
      const rows = Number(meta.rows);
      const columns = Number(meta.columns);
      const yMax = Number(meta.ymax);
      if (yMax !== this.yMax) {
        // another view (STFT to mel, say): its axis is another quantity, so
        // a zoom into this one means nothing there
        this.yMax = yMax;
        this.yLo = 0;
        this.yHi = yMax;
      }
      this.yUnits = meta.units || "";
      this.label = meta.label || "";
      const log = meta.scale === "log";
      const yMin = Number(meta.ymin || 0);
      if (log !== this.log || (log && yMin !== this.yMin)) {
        // the axis changed its scale: show all of it
        this.log = log;
        this.yMin = yMin;
        this.yLo = log ? yMin : 0;
        this.yHi = this.yMax;
      }
      this.bandsHz = meta["bands-hz"] ? meta["bands-hz"].split(",").map(Number) : null;
      if (meta.levels) {
        const [lo, hi] = meta.levels.split(",").map(Number);
        this.levels = { lo, hi, unit: meta["level-unit"] || "" };
      }
      this.error = "";
      if (!rows || !columns) { this.image = null; this.requestDraw(); return; }
      this.paint(new Uint8Array(buffer), rows, columns);
      this.image = {
        t0: Number(meta.t0), t1: Number(meta.t1),
        y0: Number(meta.y0 ?? 0), y1: Number(meta.y1 ?? yMax),
      };
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
      const top = this.yOf(this.image.y1, a);
      const bottom = this.yOf(this.image.y0, a);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(this.offscreen, x0, top, x1 - x0, bottom - top);
    }
    this.drawOverlay(ctx, a);
    if (this.error) {
      ctx.fillStyle = this.colors.hint;
      ctx.font = `12px ${monoFont()}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(this.error, a.x + a.w / 2, a.y + a.h / 2);
    }
  }

  /** Only where the vertical axis is Hz: on a mel or MFCC image the same
   *  number is a band index, and a curve at "120" would land wherever band
   *  120 is -- a plausible line in the wrong place, worse than none. Null is
   *  "no pitch here", a gap rather than a line joined across it. */
  drawOverlay(ctx, a) {
    const curve = this.overlay;
    if (!curve || this.yUnits !== "Hz") return;
    ctx.save();
    ctx.strokeStyle = this.pitchColor;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.shadowColor = this.pitchColor;
    ctx.shadowBlur = 6;
    ctx.beginPath();
    let open = false;
    const { t0, t1 } = this.view;
    for (let i = 0; i < curve.times.length; i++) {
      const time = curve.times[i];
      const hz = curve.hz[i];
      if (hz === null || time < t0 - 1 || time > t1 + 1) { open = false; continue; }
      const x = this.timeToX(time);
      const y = this.yOf(hz, a);
      if (open) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      open = true;
    }
    ctx.stroke();
    ctx.restore();
  }

  yOf(value, a) {
    if (this.log) {
      const lo = Math.log(this.yLo), hi = Math.log(this.yHi);
      return a.y + a.h - ((Math.log(Math.max(value, 1e-9)) - lo) / (hi - lo)) * a.h;
    }
    return a.y + a.h - ((value - this.yLo) / (this.yHi - this.yLo)) * a.h;
  }

  valueAt(y) {
    const a = this.area;
    const share = (a.y + a.h - y) / a.h;
    if (this.log) return Math.exp(Math.log(this.yLo) + share * (Math.log(this.yHi) - Math.log(this.yLo)));
    return this.yLo + share * (this.yHi - this.yLo);
  }

  /** A mel band position, 0 at the bottom edge, as the centre frequency of
   *  the band under it, interpolated between centres. */
  bandHz(v) {
    const c = this.bandsHz;
    const i = Math.min(Math.max(v - 0.5, 0), c.length - 1);
    const k = Math.min(Math.floor(i), c.length - 2);
    return k < 0 ? c[0] : c[k] + (c[k + 1] - c[k]) * (i - k);
  }

  /** 1, 2 and 5 of each decade, thinned to one a decade when that is all
   *  that fits. */
  logTicks(lo, hi, pixels) {
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

  drawValueAxis(ctx, a) {
    const values = this.log ? this.logTicks(this.yLo, this.yHi, a.h)
                            : ticks(this.yLo, this.yHi, a.h, 34).values;
    ctx.fillStyle = this.colors.axis;
    ctx.font = `11px ${monoFont()}`;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const mel = this.bandsHz && this.bandsHz.length > 1;
    for (const v of values) {
      const y = this.yOf(v, a);
      if (y < a.y + 6 || y > a.y + a.h - 4) continue;
      const text = this.yUnits === "Hz" ? hertz(v) : mel ? hertz(this.bandHz(v)) : String(Math.round(v));
      ctx.fillText(text, a.x - 8, y);
    }
    if (this.yUnits === "Hz" || mel) {
      ctx.save();
      ctx.fillStyle = this.yZoomed ? this.colors.accent : this.colors.hint;
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillText("Hz", a.x - 8, a.y);
      ctx.restore();
    }
  }

  // ------------------------------------------------------------- input

  wheel(e) {
    const { x, y } = pointIn(e, this.canvas);
    const onAxis = x < this.area.x;
    if (!(onAxis || e.ctrlKey) || !this.view.duration) { super.wheel(e); return; }
    e.preventDefault();
    const at = Math.min(Math.max(this.valueAt(y), this.yLo), this.yHi);
    const factor = Math.exp(e.deltaY * 0.0015);
    if (this.log) {
      const lo = Math.log(this.yLo), hi = Math.log(this.yHi), here = Math.log(at);
      const fraction = (here - lo) / (hi - lo);
      const span = (hi - lo) * factor;
      this.setYRange(Math.exp(here - fraction * span), Math.exp(here - fraction * span + span));
      return;
    }
    const fraction = (at - this.yLo) / (this.yHi - this.yLo);
    const span = (this.yHi - this.yLo) * factor;
    this.setYRange(at - fraction * span, at - fraction * span + span);
  }

  doubleClick(e) {
    const { x } = pointIn(e, this.canvas);
    if (x < this.area.x) this.setYRange(this.yBottom, this.yMax);
    else super.doubleClick(e);
  }
}
