// Small shared UI pieces: toasts, banner, dialogs, theme.
import { h, icon, append } from "./dom.js";

// ------------------------------------------------------------------ toasts
export function toast(message, kind = "info") {
  const box = document.getElementById("toasts");
  const el = h("div", { class: `toast toast-${kind}`, text: message });
  box.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

/** The small "Hors ligne" badge, with the number of edits waiting to be sent. */
export function setOffline(offline, pending = 0) {
  const el = document.getElementById("offline-badge");
  const waiting = pending ? ` · ${pending} modification${pending > 1 ? "s" : ""} en attente` : "";
  el.textContent = offline ? `Hors ligne${waiting}` : (pending ? `${pending} modification${pending > 1 ? "s" : ""} en attente d'envoi` : "");
  el.hidden = !el.textContent;
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

/**
 * A "Plus" button that opens a short list of actions under it.
 * `items` are {icon, text, onClick, danger}; null entries are skipped. Returns the wrapper element.
 */
export function actionMenu({ label = "Plus", ariaLabel = "Plus d'actions", items }) {
  const list = items.filter(Boolean);
  const id = `menu-${Math.random().toString(36).slice(2, 8)}`;
  const button = h("button", { class: "btn btn-secondary", type: "button", "aria-expanded": "false", "aria-controls": id, "aria-haspopup": "true", "aria-label": ariaLabel },
    icon("more", 20), h("span", { class: "btn-label", text: label }));
  const menu = h("div", { class: "action-menu", id, hidden: true });
  const box = h("div", { class: "action-menu-box" }, button, menu);
  const open = (yes) => {
    menu.hidden = !yes;
    button.setAttribute("aria-expanded", String(yes));
    if (yes) menu.querySelector("button")?.focus();
  };
  for (const item of list) {
    menu.append(h("button", { class: `action-item${item.danger ? " is-danger" : ""}`, type: "button",
      onClick: (e) => { open(false); item.onClick(e, button); } }, icon(item.icon, 20), h("span", { text: item.text })));
  }
  button.addEventListener("click", () => open(menu.hidden));
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { e.stopPropagation(); open(false); button.focus(); }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !menu.hidden) {
      e.preventDefault();
      const all = [...menu.querySelectorAll("button")];
      const i = all.indexOf(document.activeElement);
      all[(i + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length].focus();
    }
  });
  const outside = (e) => {
    if (!box.isConnected) { document.removeEventListener("click", outside, true); return; }
    if (!menu.hidden && !box.contains(e.target)) open(false);
  };
  document.addEventListener("click", outside, true);
  box.hidden = !list.length;
  return box;
}

/** Disable `button` while `task` runs, so a double tap cannot send twice. */
export async function busy(button, task) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { return await task(); } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
}

/** A "show the password" checkbox that switches every given password field between hidden and readable. */
export function showPasswordToggle(...inputs) {
  const id = `show-pw-${Math.random().toString(36).slice(2, 8)}`;
  const box = h("input", { type: "checkbox", id,
    onChange: (e) => { for (const input of inputs) input.type = e.target.checked ? "text" : "password"; } });
  return h("label", { class: "check", for: id }, box,
    h("span", { text: inputs.length > 1 ? "Afficher les mots de passe" : "Afficher le mot de passe" }));
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
