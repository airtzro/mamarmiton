// Small shared UI pieces: toasts, banner, dialogs, theme.
import { h, icon, append } from "./dom.js";

// ------------------------------------------------------------------ toasts
export function toast(message, kind = "info") {
  const box = document.getElementById("toasts");
  const el = h("div", { class: `toast toast-${kind}`, text: message });
  box.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

export function setBanner(text) {
  const el = document.getElementById("banner");
  el.textContent = text || "";
  el.hidden = !text;
}

// ----------------------------------------------------------------- dialogs
let openCount = 0;

/** A modal dialog. `build(close)` returns the body node. Returns {close, el}. */
export function openDialog({ title, build, wide = false, label, onClose }) {
  const dlg = h("dialog", { class: `dialog${wide ? " dialog-wide" : ""}`, "aria-label": label || title });
  let finished = false;
  // cleanup runs from close() directly and from the "close" event (Escape key); the event alone is not reliable
  const finish = () => {
    if (finished) return;
    finished = true;
    openCount--;
    dlg.remove();
    if (onClose) onClose();
  };
  const close = () => { if (dlg.open) dlg.close(); finish(); };
  const head = h("div", { class: "dialog-head" },
    h("h2", { class: "serif", text: title }),
    h("button", { class: "btn btn-icon btn-ghost", type: "button", "aria-label": "Fermer", onClick: close }, icon("close")));
  dlg.append(head, h("div", { class: "dialog-body" }, build(close)));
  dlg.addEventListener("close", finish);
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });  // click on the backdrop
  document.body.append(dlg);
  openCount++;
  dlg.showModal();
  return { close, el: dlg };
}

export const hasOpenDialog = () => openCount > 0;

export function confirmDialog({ title, message, confirmText = "Confirmer", danger = false, cancelText = "Annuler" }) {
  return new Promise((resolve) => {
    let answer = false;
    openDialog({
      title,
      onClose: () => resolve(answer),
      build: (close) => h("div", { class: "stack" },
        h("p", { text: message }),
        h("div", { class: "row end wrap" },
          h("button", { class: "btn btn-secondary", type: "button", onClick: close, text: cancelText }),
          h("button", { class: `btn ${danger ? "btn-danger-solid" : "btn-primary"}`, type: "button",
            onClick: () => { answer = true; close(); }, text: confirmText }))),
    });
  });
}

/** Disable `button` while `task` runs, so a double tap cannot send twice. */
export async function busy(button, task) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { return await task(); } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
}

// ------------------------------------------------------------------- theme
const THEME_KEY = "mamarmiton.theme";
const dark = window.matchMedia("(prefers-color-scheme: dark)");

export function isDark() {
  const set = document.documentElement.dataset.theme;
  return set ? set === "dark" : dark.matches;
}

function applyThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", isDark() ? "#0F0B09" : "#2B201C");
}

export function initTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch { /* ignore */ }
  applyThemeColor();
  dark.addEventListener("change", applyThemeColor);
}

export function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
  applyThemeColor();
}

export { append };
