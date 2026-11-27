// "Taille du texte": a per-device choice, applied as a data attribute that CSS turns into a bigger root font size.
export const TEXT_SIZES = [["normal", "Normale"], ["large", "Grande"], ["xlarge", "Très grande"]];
const KEY = "mamarmiton.textsize";

/** The saved value if it is one we know, else "normal" (a stale or hand-edited value must never break the page). */
export function parseTextSize(value) {
  return TEXT_SIZES.some(([id]) => id === value) ? value : "normal";
}

export function textSize() {
  try { return parseTextSize(localStorage.getItem(KEY)); } catch { return "normal"; }
}

/** Applies `value` to the page now; with `save`, remembers it on this device. */
export function applyTextSize(value, save = false) {
  const size = parseTextSize(value);
  if (size === "normal") delete document.documentElement.dataset.textSize;
  else document.documentElement.dataset.textSize = size;
  if (save) { try { localStorage.setItem(KEY, size); } catch { /* ignore */ } }
  return size;
}

export const initTextSize = () => applyTextSize(textSize());
