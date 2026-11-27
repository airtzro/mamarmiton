import { api, setHandlers, token } from "./api.js";
import { h, icon, clear } from "./dom.js";
import { hydrate, pendingCount, persist, resetState, state, sync } from "./state.js";
import * as store from "./offline.js";
import { hasOpenDialog, initTheme, isDark, setOffline, toast, toggleTheme } from "./ui.js";
import { renderLogin } from "./login.js";
import { renderHome } from "./home.js";
import { leaveCookMode, renderDetail } from "./detail.js";
import { renderEditor } from "./editor.js";
import { openAccountDialog } from "./account.js";

const root = document.getElementById("root");
let view = null;       // {update()} of the screen showing
let main = null;
let topbar = null;
let timer = null;
let lastSync = 0;
let loggedIn = false;

initTheme();

// ------------------------------------------------------------------ session
let offline = false;
const paintOffline = () => setOffline(offline, pendingCount);
function setOnline(ok) {
  const wasOffline = offline;
  offline = !ok;
  paintOffline();
  if (wasOffline && ok && loggedIn) { lastSync = 0; refresh(); }
}
window.addEventListener("mamarmiton:pending", paintOffline);
setHandlers({
  network: setOnline,
  unauthorized: (message) => { if (loggedIn) showLogin("Votre session a expiré. Reconnectez-vous."); void message; },
});
window.addEventListener("offline", () => setOnline(false));
window.addEventListener("online", () => { lastSync = 0; refresh(); });

// the service worker keeps the page itself and the photos available without a connection
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/app/sw.js", { scope: "/" }).catch(() => {}); });
}
function forgetPhotosOnDevice() {
  try { if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ type: "clear-photos" }); } catch { /* ignore */ }
}

function showLogin(message = "") {
  loggedIn = false;
  clearInterval(timer);
  token.clear();
  resetState();
  leaveCookMode();
  view = null;
  document.querySelectorAll("dialog").forEach((d) => d.close());
  if (location.hash && location.hash !== "#/") history.replaceState(null, "", location.pathname);
  renderLogin(root, { message, onDone: (user) => start(user) });
}

async function signOut() {
  const name = state.me && state.me.username;
  if (name) await store.clearUser(name);  // this user's copy and unsent edits leave the device
  forgetPhotosOnDevice();
  showLogin("");
}

async function start(user) {
  state.me = user;
  loggedIn = true;
  void store.saveMe(user);
  buildShell();
  // show the copy kept on the device straight away, then catch up with the server
  const cached = await hydrate(user.username);
  if (!loggedIn) return;
  if (cached) route();
  paintOffline();
  try {
    state.users = await api.users();
  } catch { /* shared lists simply stay empty until the next refresh */ }
  let changed = null;
  try {
    changed = await sync();
    await persist();
  } catch (e) {
    if (!loggedIn) return;
    if (!e.offline) toast(e.message, "error");
  }
  lastSync = Date.now();
  clearInterval(timer);
  timer = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 60000);
  if (!cached) route();
  else afterChange(changed);
}

// ------------------------------------------------------------------- shell
function buildShell() {
  const themeBtn = h("button", { class: "btn btn-icon btn-topbar", type: "button", "aria-label": "Changer entre thème clair et sombre" });
  const paintTheme = () => { clear(themeBtn).append(icon(isDark() ? "sun" : "moon", 22)); };
  themeBtn.addEventListener("click", () => { toggleTheme(); paintTheme(); });
  paintTheme();
  topbar = h("header", { class: "topbar" },
    h("a", { class: "brand", href: "#/", "aria-label": "Mamarmiton, retour aux recettes" },
      h("img", { src: "/app/icon.svg", alt: "", width: 36, height: 36 }), h("span", { class: "serif", text: "Mamarmiton" })),
    h("div", { class: "topbar-actions" },
      h("a", { class: "btn btn-accent", href: "#/nouvelle", "aria-label": "Nouvelle recette" }, icon("plus", 20), h("span", { class: "btn-label", text: "Nouvelle recette" })),
      themeBtn,
      h("button", { class: "btn btn-topbar", type: "button", "aria-label": "Mon compte", onClick: () => openAccountDialog({ onSignOut: signOut }) },
        icon("user", 22), h("span", { class: "btn-label", text: state.me.display_name || state.me.username }))));
  main = h("main", { id: "main", tabindex: "-1" });
  clear(root).append(topbar, main);
}

// ----------------------------------------------------------------- routing
function route() {
  if (!loggedIn || !main) return;
  const hash = location.hash || "#/";
  const [, kind, id] = hash.split("/");
  leaveCookMode();
  window.scrollTo(0, 0);
  if (kind === "r" && id) {
    view = renderDetail(main, id, {
      onEdit: (rid) => { location.hash = `#/modifier/${rid}`; },
      onGone: () => { toast("Cette recette n'est plus disponible.", "info"); location.hash = "#/"; },
      onChange: () => view && view.update(),
    });
    view.kind = "detail";
    view.id = id;
  } else if (kind === "nouvelle") {
    view = renderEditor(main, null, { done: (rid) => { location.hash = `#/r/${rid}`; }, cancel: () => { location.hash = "#/"; } });
    view.kind = "editor";
  } else if (kind === "modifier" && id) {
    view = renderEditor(main, id, { done: (rid) => { location.hash = `#/r/${rid}`; }, cancel: () => { location.hash = `#/r/${id}`; } });
    view.kind = "editor";
  } else {
    view = renderHome(main, { onNew: () => { location.hash = "#/nouvelle"; } });
    view.kind = "home";
  }
  if (view.kind !== "editor") main.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

// -------------------------------------------------------------------- sync
async function refresh() {
  if (!loggedIn || Date.now() - lastSync < 5000) return;
  lastSync = Date.now();
  try {
    const changed = await sync();
    afterChange(changed);
  } catch { /* offline or session ended: the banner / login screen already say so */ }
}

function afterChange(changed) {
  if (!view || !changed || !changed.size) return;
  if (view.kind === "home") view.update();
  else if (view.kind === "detail" && changed.has(view.id) && !hasOpenDialog()) view.update();
}
window.addEventListener("mamarmiton:data", () => view && view.kind === "home" && view.update());
window.addEventListener("focus", refresh);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });

// -------------------------------------------------------------------- boot
(async function boot() {
  if (!token.get()) { showLogin(); return; }
  try {
    // a known user on this device opens at once; the server is asked in the background
    const known = await store.loadMe();
    if (known && known.username) {
      void api.me().then((user) => { if (loggedIn && user.username === known.username) state.me = user; }).catch(() => {});
      await start(known);
      return;
    }
    const user = await api.me();
    await start(user);
  } catch (e) {
    if (e.status === 401) { showLogin("Votre session a expiré. Reconnectez-vous."); return; }
    if (e.offline || e.status === 0) {
      clear(root).append(h("main", { class: "login", id: "main" }, h("div", { class: "login-card stack" },
        h("h1", { class: "serif", text: "Mamarmiton" }),
        h("p", { text: e.message }),
        h("button", { class: "btn btn-primary btn-big", type: "button", onClick: () => location.reload(), text: "Réessayer" }))));
      return;
    }
    showLogin(e.message);
  }
})();
