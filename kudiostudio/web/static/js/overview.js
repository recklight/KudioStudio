// The whole clip in one strip, with the stretch on screen marked. Drag the
// mark to move along the clip; click anywhere else to jump there.

import { data } from "./api.js";
import { roundRect } from "./timeplot.js";

export class Overview {
  constructor(canvas, view) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.view = view;
    this.version = -1;
    this.values = null;
    this.peak = 0;
    this.playhead = null;
    this.drag = null;
    this.pending = false;
    const css = getComputedStyle(document.documentElement);
    this.colors = {
      wave: css.getPropertyValue("--wave").trim(),
      primary: css.getPropertyValue("--primary").trim(),
      accent: css.getPropertyValue("--accent").trim(),
      canvas: css.getPropertyValue("--canvas").trim(),
    };
    new ResizeObserver(() => this.resize()).observe(canvas);
    view.onChange(() => this.requestDraw());
    canvas.addEventListener("pointerdown", (e) => this.down(e));
    canvas.addEventListener("pointermove", (e) => this.move(e));
    canvas.addEventListener("pointerup", () => { this.drag = null; });
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (!w || !h) return;
    const before = this.width;
    this.dpr = dpr;
    this.width = w;
    this.height = h;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    if (before !== w && this.version >= 0) this.fetch();
    this.requestDraw();
  }

  /** The whole clip's peaks, once per version; also gives the waveform its
   *  vertical scale. Resolves to the clip's peak. */
  async setAudio(version, duration) {
    this.version = version;
    this.values = null;
    this.duration = duration;
    await this.fetch();
    return this.peak;
  }

  clear() {
    this.version = -1;
    this.values = null;
    this.requestDraw();
  }

  async fetch() {
    if (this.version < 0 || !this.width) return;
    const columns = Math.max(1, Math.round(this.width * (this.dpr || 1)));
    const { meta, buffer } = await data("peaks", { t0: 0, t1: this.duration, columns });
    if (Number(meta.version) !== this.version) return;
    const values = new Float32Array(buffer);
    let peak = 0;
    for (const v of values) peak = Math.max(peak, Math.abs(v));
    this.peak = peak;
    this.values = { kind: meta.kind, values };
    this.requestDraw();
  }

  setPlayhead(seconds) { this.playhead = seconds; this.requestDraw(); }

  requestDraw() {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(() => { this.pending = false; this.draw(); });
  }

  xOf(t) { return (t / (this.view.duration || 1)) * this.width; }

  draw() {
    if (!this.width) return;
    const ctx = this.ctx;
    const { width: w, height: h } = this;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = this.colors.canvas;
    roundRect(ctx, 0, 0, w, h, 6);
    ctx.fill();
    const v = this.values;
    if (v && v.values.length) {
      const peak = this.peak || 1;
      const mid = h / 2;
      ctx.fillStyle = "rgba(56, 182, 255, 0.55)";
      if (v.kind === "peaks") {
        const n = v.values.length / 2;
        for (let i = 0; i < n; i++) {
          const x = (i / n) * w;
          const top = mid - (v.values[2 * i + 1] / peak) * (mid - 3);
          const bottom = mid - (v.values[2 * i] / peak) * (mid - 3);
          ctx.fillRect(x, top, Math.max(1, w / n), Math.max(0.6, bottom - top));
        }
      } else {
        // a clip shorter than two samples a pixel: the samples themselves
        const n = v.values.length;
        ctx.strokeStyle = this.colors.wave;
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
          const x = (i / Math.max(1, n - 1)) * w;
          const y = mid - (v.values[i] / peak) * (mid - 3);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    }
    if (this.view.duration > 0) {
      const x0 = this.xOf(this.view.t0);
      const x1 = Math.max(x0 + 4, this.xOf(this.view.t1));
      ctx.fillStyle = "rgba(31, 198, 240, 0.12)";
      roundRect(ctx, x0, 1, x1 - x0, h - 2, 5);
      ctx.fill();
      ctx.strokeStyle = this.colors.primary;
      ctx.lineWidth = 1.2;
      roundRect(ctx, x0 + 0.5, 1.5, x1 - x0 - 1, h - 3, 5);
      ctx.stroke();
    }
    if (this.playhead !== null && this.view.duration > 0) {
      const x = Math.round(this.xOf(this.playhead)) + 0.5;
      ctx.strokeStyle = this.colors.accent;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
  }

  down(e) {
    if (!this.view.duration) return;
    const x = e.clientX - this.canvas.getBoundingClientRect().left;
    const t = (x / this.width) * this.view.duration;
    this.canvas.setPointerCapture(e.pointerId);
    if (t >= this.view.t0 && t <= this.view.t1) {
      this.drag = { x, t0: this.view.t0 };
    } else {
      const span = this.view.span;
      this.view.set(t - span / 2, t + span / 2);
      this.drag = { x, t0: this.view.t0 };
    }
  }

  move(e) {
    if (!this.drag) return;
    const x = e.clientX - this.canvas.getBoundingClientRect().left;
    const dt = ((x - this.drag.x) / this.width) * this.view.duration;
    this.view.set(this.drag.t0 + dt, this.drag.t0 + dt + this.view.span);
  }
}
