// The interface language, with the same rules as kudiostudio/i18n.py:
// the English is the key; "{}" in a key stands for a value, and a value
// that is itself a phrase with a translation is translated too.

let exact = new Map();
let templates = [];

function compile(key) {
  const parts = key.split("{}");
  if (parts.length === 1) return null;
  const escaped = parts.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return { pattern: new RegExp(`^${escaped.join("([\\s\\S]*?)")}$`), staticLength: parts.join("").length };
}

/** Make *table* (English -> translation) the one `t` uses. */
export function load(table) {
  exact = new Map();
  const found = [];
  for (const [key, value] of Object.entries(table || {})) {
    const compiled = compile(key);
    if (compiled === null) exact.set(key, value);
    else found.push({ ...compiled, value });
  }
  // the most specific template first: "{} failed — {}: {}" before "{} — {}"
  templates = found.sort((a, b) => b.staticLength - a.staticLength);
}

/** *text* in the current language; unchanged when there is no translation. */
export function t(text) {
  if (!text) return text;
  const hit = exact.get(text);
  if (hit !== undefined) return hit;
  for (const { pattern, value } of templates) {
    const m = text.match(pattern);
    if (m) {
      const values = m.slice(1).map((v) => exact.get(v) ?? v);
      // "{}" takes the values in order; "{1}" names one, where the
      // translation needs them in another order than the English
      let i = 0;
      return value.replace(/\{(\d*)\}/g, (_, n) => values[n === "" ? i++ : Number(n)] ?? "");
    }
  }
  if (text.includes("\n")) return text.split("\n").map(t).join("\n");
  return text;
}

/** Translate everything marked in *root*: `data-t` for an element's own
 *  text, `data-t-title` for its tooltip. The English is kept on the element
 *  so the language can change again. */
export function translatePage(root = document) {
  for (const el of root.querySelectorAll("[data-t]")) {
    if (!el.dataset.en) el.dataset.en = el.textContent.trim();
    el.textContent = t(el.dataset.en);
  }
  for (const el of root.querySelectorAll("[data-t-title]")) {
    el.title = t(el.dataset.tTitle);
  }
}
