// Keeps every open tab on the code the server serves now.
// The page is served with the build it belongs to (<meta name="mamarmiton-build">); GET /version
// says which build the server runs. When they differ, the tab reloads itself, unless that would
// lose something (an unsaved edit, a running timer, cook mode, an open dialog): then it says so
// in the banner and reloads as soon as that is over.
import { reloadOnceForUpgrade } from "./api.js";

const CHECK_EVERY_MS = 60_000;
const MIN_ASK_GAP_MS = 15_000;  // moving between screens checks too, but not on every click
const SW_UPDATE_WAIT_MS = 3000;

export const WAITING_TEXT = "Une nouvelle version de Mamarmiton est prête : elle s'installera dès que vous aurez fini.";

/** The build this page was served with ("" when unknown, e.g. the file opened without the server). */
export function pageBuild(doc) {
  const value = doc.querySelector('meta[name="mamarmiton-build"]')?.getAttribute("content") || "";
  return value === "__BUILD__" ? "" : value;
}

/** True when the server answers with a build that is not the one this page runs. */
export const isOutdated = (mine, theirs) => !!mine && typeof theirs === "string" && !!theirs && mine !== theirs;

/**
 * What to do now: "stay" (up to date or unknown), "wait" (newer, but the person is in the middle
 * of something) or "reload".
 */
export function decide(mine, theirs, busy) {
  if (!isOutdated(mine, theirs)) return "stay";
  return busy ? "wait" : "reload";
}

/**
 * Start watching. `isBusy()` says whether a reload would lose something; `onWaiting(bool)` shows or
 * hides the "new version is ready" notice. Returns `check()`: call it when the person finishes
 * something (leaves the editor, closes a dialog…) so a pending update installs right away.
 */
export function watchForUpdates({ isBusy, onWaiting }) {
  const mine = pageBuild(document);
  if (!mine) return () => {};
  let theirs = "";
  let asking = false;
  let askedAt = -Infinity;
  let waiting = false;

  async function ask() {
    if (asking || Date.now() - askedAt < MIN_ASK_GAP_MS) return;
    asking = true;
    askedAt = Date.now();
    try {
      const response = await fetch("/version", { cache: "no-store", headers: { "X-Mamarmiton-Client": "web/1" } });
      if (response.ok) theirs = (await response.json()).web || "";
    } catch { /* offline: ask again later */ } finally { asking = false; }
  }

  async function reload() {
    // fetch the new service worker first, so the reloaded page does not come from the old shell cache
    try {
      const registration = await navigator.serviceWorker?.getRegistration();
      if (registration) {
        await Promise.race([registration.update(), new Promise((resolve) => setTimeout(resolve, SW_UPDATE_WAIT_MS))]);
      }
    } catch { /* the network-first shell still serves the new files */ }
    reloadOnceForUpgrade(window.sessionStorage, () => window.location.reload());
  }

  function act() {
    const next = decide(mine, theirs, isBusy());
    if ((next === "wait") !== waiting) { waiting = next === "wait"; onWaiting(waiting); }
    if (next === "reload") void reload();
  }

  async function check() {
    // once a newer build is known there is nothing more to ask: only whether it is a good moment
    if (!isOutdated(mine, theirs)) await ask();
    act();
  }

  setInterval(() => { if (document.visibilityState === "visible") void check(); }, CHECK_EVERY_MS);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void check(); });
  window.addEventListener("focus", () => void check());
  void check();
  return () => void check();
}
