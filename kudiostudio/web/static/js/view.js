// The stretch of time on screen, shared by every plot.
//
// The plots never keep a range of their own: they all read this one and
// redraw when it changes, so "1.2 seconds" is in the same place on each.

export class TimeView {
  constructor() {
    this.t0 = 0;
    this.t1 = 1;
    this.duration = 0;
    this.sampleRate = 0;
    this.listeners = new Set();
  }

  get span() { return this.t1 - this.t0; }

  /** The narrowest view: 64 samples across, so even a single click has a
   *  shape, and never less than a tenth of a millisecond. */
  get minSpan() {
    return Math.max(1e-4, this.sampleRate > 0 ? 64 / this.sampleRate : 1e-3);
  }

  onChange(fn) { this.listeners.add(fn); }

  emit() { for (const fn of this.listeners) fn(this); }

  reset(duration, sampleRate) {
    this.duration = Math.max(0, duration);
    this.sampleRate = sampleRate || 0;
    this.t0 = 0;
    this.t1 = this.duration > 0 ? this.duration : 1;
    this.emit();
  }

  /** Show *t0*..*t1*, kept inside the clip and no narrower than minSpan. */
  set(t0, t1) {
    const total = this.duration || 1;
    let span = Math.min(Math.max(t1 - t0, this.minSpan), total);
    let start = Math.min(Math.max(t0, 0), total - span);
    if (!Number.isFinite(start)) start = 0;
    if (!(span > 0)) span = total;
    if (start === this.t0 && start + span === this.t1) return;
    this.t0 = start;
    this.t1 = start + span;
    this.emit();
  }

  /** Zoom by *factor* (below 1 zooms in) keeping *time* where it is. */
  zoomAt(time, factor) {
    const span = this.span * factor;
    const fraction = this.span > 0 ? (time - this.t0) / this.span : 0.5;
    this.set(time - fraction * span, time - fraction * span + span);
  }

  pan(seconds) { this.set(this.t0 + seconds, this.t1 + seconds); }

  full() { this.set(0, this.duration || 1); }

  /** Bring *time* into view, paging so it lands near the left edge. */
  follow(time) {
    if (time >= this.t0 && time <= this.t1) return;
    const span = this.span;
    this.set(time - span * 0.08, time - span * 0.08 + span);
  }
}
