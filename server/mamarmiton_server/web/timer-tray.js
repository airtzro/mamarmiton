// Kitchen timers. The state lives in this module, so timers keep running while the
// user moves between views. Each timer stores its END timestamp (Date.now), never a
// counter: a phone that throttles a background tab still shows the right time.
import { h, icon } from "./dom.js";
import { findDurations, formatClock, formatShort, secondsLeft } from "./timers.js";

const timers = [];            // {id, label, seconds, end, done}
const labels = new Map();     // id -> text node element, refreshed every tick
let nextId = 1;
let tray = null;
let ticker = null;
let audio = null;

/** Create (or resume) the audio context. Must run inside a click so autoplay rules allow sound later. */
function unlockAudio() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!audio) audio = new Ctx();
    if (audio.state === "suspended") audio.resume().catch(() => {});
  } catch { /* no sound: the vibration and the tray still tell */ }
}

function beep() {
  try {
    if (!audio) return;
    if (audio.state === "suspended") audio.resume().catch(() => {});
    const start = audio.currentTime + 0.02;
    for (let i = 0; i < 4; i++) {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      const t = start + i * 0.3;
      osc.type = "sine";
      osc.frequency.value = i % 2 ? 660 : 880;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.4, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
      osc.connect(gain).connect(audio.destination);
      osc.start(t);
      osc.stop(t + 0.25);
    }
  } catch { /* ignore */ }
}

function alarm() {
  try { if (typeof navigator.vibrate === "function") navigator.vibrate([300, 150, 300, 150, 300]); } catch { /* ignore */ }
  beep();
}

const textOf = (t) => (t.done ? `${t.label} · Terminé !` : `${t.label} · ${formatClock(secondsLeft(t.end))}`);

function ensureTray() {
  if (!tray) {
    tray = h("div", { class: "timer-tray", role: "region", "aria-label": "Minuteurs" });
    document.body.append(tray);
  }
  return tray;
}

function render() {
  labels.clear();
  if (!timers.length) { if (tray) tray.replaceChildren(); return; }
  ensureTray().replaceChildren(...timers.map((t) => {
    const text = h("span", { class: "timer-text", text: textOf(t) });
    labels.set(t.id, text);
    const close = h("button", { class: "btn btn-icon timer-close", type: "button",
      "aria-label": t.done ? `Fermer le minuteur ${t.label}` : `Annuler le minuteur ${t.label}`,
      onClick: () => cancelTimer(t.id) }, icon("close", 22));
    return h("div", { class: `timer-item${t.done ? " is-done" : ""}`, role: t.done ? "alert" : null },
      icon("clock", 22), text, close);
  }));
}

function tick() {
  let finished = false;
  for (const t of timers) {
    if (!t.done && secondsLeft(t.end) <= 0) { t.done = true; finished = true; }
  }
  if (finished) { render(); alarm(); } else {
    for (const t of timers) { const el = labels.get(t.id); if (el) el.textContent = textOf(t); }
  }
  if (!timers.some((t) => !t.done)) { clearInterval(ticker); ticker = null; }
}

/** Start a timer. Call it from a click handler (the audio context is created there). */
export function startTimer(label, seconds) {
  unlockAudio();
  const t = { id: nextId++, label, seconds, end: Date.now() + seconds * 1000, done: false };
  timers.push(t);
  render();
  if (!ticker) ticker = setInterval(tick, 250);
  return t;
}

export function cancelTimer(id) {
  const i = timers.findIndex((t) => t.id === id);
  if (i >= 0) timers.splice(i, 1);
  render();
}

export const activeTimers = () => timers.slice();

// a backgrounded tab may have missed the end: check as soon as it is visible again
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && timers.length) tick(); });

/** Buttons "▶ 25 min" for each duration found in a step's text (null when there is none). */
export function timerButtons(stepIndex, text, extraClass = "") {
  const found = findDurations(text);
  if (!found.length) return null;
  const label = `Étape ${stepIndex + 1}`;
  return h("div", { class: `step-timers ${extraClass}`.trim() }, found.map((d) =>
    h("button", { class: "btn btn-secondary timer-btn", type: "button",
      "aria-label": `Lancer un minuteur de ${formatShort(d.seconds)} pour l'étape ${stepIndex + 1}`,
      onClick: () => startTimer(label, d.seconds) }, icon("play", 20), h("span", { text: formatShort(d.seconds) }))));
}
