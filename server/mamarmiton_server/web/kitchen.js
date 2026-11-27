// Kitchen mode: one big step at a time, readable from across the counter.
// Port of mamarmiton/ui/kitchen.py. Full screen overlay, swipe / arrows / buttons,
// ingredients panel (scaled to the chosen servings), timers, and a screen wake lock.
import { h, icon } from "./dom.js";
import { frac, scaleQuantity } from "./quantities.js";
import { timerButtons } from "./timer-tray.js";

let current = null;   // {close()} of the overlay on screen

/** Text size class like kitchen.py's step_font_size (36 / 30 / 25). */
export function stepSizeClass(text) {
  const n = text.length;
  return n < 140 ? "k-large" : n < 280 ? "k-medium" : "k-small";
}

export function leaveCookMode() {
  if (current) current.close();
}

export function openKitchen(recipe, { servings, start = 0, onClose } = {}) {
  leaveCookMode();
  const steps = recipe.etapes;
  const people = servings || recipe.pour;
  const factor = frac(people, Math.max(1, recipe.pour));
  let index = Math.max(0, Math.min(start, steps.length - 1));
  let showIngredients = false;
  const opener = document.activeElement;
  const appRoot = document.getElementById("root");

  // ---- screen wake lock (re-acquired when the tab comes back, the browser drops it when hidden)
  let wakeLock = null;
  let closed = false;
  async function keepAwake() {
    try {
      if (!closed && "wakeLock" in navigator && !wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      }
    } catch { wakeLock = null; /* refused or unsupported: the screen may simply dim */ }
  }
  const onVisible = () => { if (document.visibilityState === "visible") keepAwake(); };

  // ---- DOM
  const counter = h("p", { class: "k-counter serif", "aria-live": "polite" });
  const text = h("p", { class: "k-text" });
  const timersBox = h("div", { class: "k-timers" });
  const stage = h("div", { class: "k-stage" }, text, timersBox);
  const prev = h("button", { class: "btn btn-secondary k-nav", type: "button", onClick: () => go(-1) }, icon("back", 30), h("span", { text: "Précédent" }));
  const nextLabel = h("span", { text: "Suivant" });
  const next = h("button", { class: "btn btn-primary k-nav", type: "button", onClick: () => go(1) }, nextLabel, icon("next", 30));
  const ingBtn = h("button", { class: "btn btn-secondary", type: "button", "aria-pressed": "false", onClick: toggleIngredients },
    icon("pot", 22), h("span", { class: "btn-label", text: "Ingrédients" }));
  const quit = h("button", { class: "btn btn-secondary", type: "button", onClick: () => close() },
    icon("close", 22), h("span", { class: "btn-label", text: "Quitter" }));

  const ingList = recipe.ingredients.length
    ? h("ul", { class: "k-ing-list" }, recipe.ingredients.map((ing) => {
      const qty = scaleQuantity(ing.quantite, factor);
      return h("li", {}, qty ? h("strong", { text: `${qty} ` }) : null, ing.nom);
    }))
    : h("p", { class: "muted", text: "Aucun ingrédient." });
  const panel = h("aside", { class: "k-panel", id: "k-ingredients", hidden: true, "aria-label": "Ingrédients" },
    h("h2", { class: "serif", text: "Ingrédients" }),
    h("p", { class: "muted", text: `pour ${people} personne${people > 1 ? "s" : ""}` }),
    ingList);
  ingBtn.setAttribute("aria-controls", "k-ingredients");

  const overlay = h("div", { class: "kitchen", role: "dialog", "aria-modal": "true", "aria-label": `Mode cuisine : ${recipe.nom}`, tabindex: "-1" },
    h("header", { class: "k-top" }, counter, h("p", { class: "k-title muted", text: recipe.nom }), ingBtn, quit),
    h("div", { class: "k-body" }, stage, panel),
    h("nav", { class: "k-bottom", "aria-label": "Étapes" }, prev, next));

  function render() {
    const n = steps.length;
    timersBox.replaceChildren();
    if (!n) {
      counter.textContent = "Aucune étape";
      text.textContent = "Cette recette n'a pas encore d'étapes.";
      text.className = "k-text k-medium";
      prev.disabled = next.disabled = true;
      return;
    }
    const step = steps[index];
    counter.textContent = `Étape ${index + 1} / ${n}`;
    text.textContent = step;
    text.className = `k-text ${stepSizeClass(step)}`;
    prev.disabled = index === 0;
    nextLabel.textContent = index === n - 1 ? "Terminer" : "Suivant";
    const buttons = timerButtons(index, step, "k-timer-buttons");
    if (buttons) timersBox.append(buttons);
    stage.scrollTop = 0;
  }

  function go(delta) {
    const target = index + delta;
    if (target >= steps.length) { close(); return; }
    if (target < 0 || !steps.length) return;
    index = target;
    render();
  }

  function toggleIngredients() {
    showIngredients = !showIngredients;
    panel.hidden = !showIngredients;
    ingBtn.setAttribute("aria-pressed", String(showIngredients));
    ingBtn.classList.toggle("is-on", showIngredients);
  }

  // ---- keyboard and swipe
  const onKey = (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = (e.target && e.target.tagName) || "";
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(1); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(-1); }
    else if (e.key === "Escape" && tag !== "SELECT") { e.preventDefault(); close(); }
  };
  let touch = null;
  overlay.addEventListener("touchstart", (e) => {
    touch = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
  }, { passive: true });
  overlay.addEventListener("touchend", (e) => {
    if (!touch || !e.changedTouches.length) return;
    const dx = e.changedTouches[0].clientX - touch.x, dy = e.changedTouches[0].clientY - touch.y;
    touch = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? 1 : -1);
  }, { passive: true });

  function close() {
    if (closed) return;
    closed = true;
    current = null;
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("visibilitychange", onVisible);
    overlay.remove();
    document.body.classList.remove("kitchen-open");
    if (appRoot) appRoot.inert = false;
    try { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); } catch { /* ignore */ }
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus({ preventScroll: true });
    if (onClose) onClose();
  }

  document.body.append(overlay);
  document.body.classList.add("kitchen-open");
  if (appRoot) appRoot.inert = true;
  document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", onVisible);
  current = { close };
  render();
  overlay.focus({ preventScroll: true });
  keepAwake();
  try {  // called from the click that opened the mode, as browsers require
    const el = document.documentElement;
    if (el.requestFullscreen && !document.fullscreenElement) el.requestFullscreen().catch(() => {});
  } catch { /* not supported (iPhone Safari): the overlay already fills the page */ }
  return { close };
}
