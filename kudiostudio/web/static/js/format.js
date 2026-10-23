// Numbers as the window shows them.

/** 12.5 -> "0:12.500". Milliseconds matter when picking a splice. */
export function clock(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(s / 60);
  const rest = s - minutes * 60;
  return `${minutes}:${rest.toFixed(3).padStart(6, "0")}`;
}

/** A step of 1, 2 or 5 times a power of ten giving about *target* steps
 *  over *span*. */
export function niceStep(span, target) {
  const raw = span / Math.max(1, target);
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) {
    if (m * power >= raw) return m * power;
  }
  return 10 * power;
}

/** Tick positions between *lo* and *hi* for an axis *pixels* long, at
 *  least *spacing* pixels apart. */
export function ticks(lo, hi, pixels, spacing = 80) {
  const span = hi - lo;
  if (!(span > 0) || !(pixels > 0)) return { step: 1, values: [] };
  const step = niceStep(span, pixels / spacing);
  const values = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
    values.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return { step, values };
}

/** Enough decimals to tell neighbouring ticks apart, and no more. */
export function decimals(step) {
  return Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
}

export function timeTick(value, step) {
  if (step >= 60 && value >= 60) return clock(value).replace(/\.\d+$/, "");
  return value.toFixed(decimals(step));
}

export function hertz(value) {
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)}k`;
  return `${Math.round(value)}`;
}

/** A level with its sign and unit, or a word when there is no number. */
export function level(value, unit, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)} ${unit}`;
}
