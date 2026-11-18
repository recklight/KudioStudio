// The interface size from Settings, a CSS zoom on the whole page.
//
// Under a zoom an element is laid out in CSS pixels and shown that many
// times larger: a canvas `clientWidth` 400 wide covers 500 pixels of the
// window at 125%. Its bitmap has to have the larger size, or the plot is
// stretched and soft, and a pointer's distance from its left edge, which the
// browser gives in window pixels, has to be brought back to CSS pixels before
// it is turned into a time. Both use the ratio measured on the element itself,
// which is 1 with no zoom and in a browser that zooms in some other way.

/** How many window pixels one CSS pixel of *el* covers. */
export function scaleOf(el) {
  const shown = el.getBoundingClientRect().width;
  return el.clientWidth > 0 && shown > 0 ? shown / el.clientWidth : 1;
}

/** Device pixels per CSS pixel of *el*: what a canvas bitmap is sized by. */
export function pixelRatio(el) {
  return (window.devicePixelRatio || 1) * scaleOf(el);
}

/** Where pointer event *e* is on *el*, in *el*'s CSS pixels. */
export function pointIn(e, el) {
  const r = el.getBoundingClientRect();
  const scale = scaleOf(el);
  return { x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale };
}
