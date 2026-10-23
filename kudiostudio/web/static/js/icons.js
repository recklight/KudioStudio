// The line icons, from kudiostudio/shapes.py by way of /icons.json: one set
// for both windows. Each is drawn in currentColor, so CSS colours it.

let shapes = {};

export async function loadIcons() {
  shapes = await (await fetch("/icons.json")).json();
}

export function svg(name, stroke = 2) {
  const body = shapes[name] || "";
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round" ` +
    `aria-hidden="true">${body}</svg>`;
}

/** Fill every `<i data-icon="name">` under *root*. */
export function fillIcons(root = document) {
  for (const el of root.querySelectorAll("i[data-icon]")) {
    el.innerHTML = svg(el.dataset.icon);
  }
}
