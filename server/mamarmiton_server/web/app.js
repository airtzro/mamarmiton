import { install as watchErrors } from "./diagnostics.js";
import { api, setHandlers, token } from "./api.js";
import { flushUsage } from "./usage.js";
import { h, icon, clear } from "./dom.js";
import { hydrate, pendingCount, persist, resetState, state, sync } from "./state.js";
import * as store from "./offline.js";
import { hasOpenDialog, initTheme, isDark, setOffline, toast, toggleTheme } from "./ui.js";
import { renderLogin } from "./login.js";
import { renderHome } from "./home.js";
import { renderDetail } from "./detail.js";
import { leaveCookMode } from "./kitchen.js";
import { clearShopping, renderCourses } from "./courses.js";
import { renderEditor } from "./editor.js";
import { openAccountDialog, openReportDialog } from "./account.js";
import { forgetImportStatus, importStatus, loadImportStatus, openImportDialog } from "./importer.js";
import { setPendingDraft, takePendingDraft } from "./importlogic.js";

const root = document.getElementById("root");
let view = null;       // {update()} of the screen showing
let main = null;
let topbar = null;
let importBtn = null;
let timer = null;
let lastSync = 0;
let loggedIn = false;

initTheme();

// ------------------------------------------------------------------ session
let offline = false;
// "Importer" needs a connection (the server reads the recipe) and a server recent enough to do it
const canImport = () => !offline && !!importStatus();
const paintImport = () => { if (importBtn) importBtn.hidden = !canImport(); };
const paintOffline = () => { setOffline(offline, pendingCount); paintImport(); };
function checkImport() {
  const before = importStatus();
  void loadImportStatus().then((now) => {
    if (!loggedIn) return;
    paintImport();
    if (now !== before && view && view.kind === "home") view.update();  // the empty book offers it too
  });
}
function openImport() {
  openImportDialog({
    onDone: (draft) => {
      setPendingDraft(draft);
      if (location.hash === "#/nouvelle") route(); else location.hash = "#/nouvelle";
    },
  });
}
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
  forgetImportStatus();
  setPendingDraft(null);
  paintOffline();  // the pending-edits badge belongs to the user who just left
  leaveCookMode();
  view = null;
  document.querySelectorAll("dialog").forEach((d) => d.close());
  if (location.hash && location.hash !== "#/") history.replaceState(null, "", location.pathname);
  renderLogin(root, { message, onDone: (user) => start(user) });
}

watchErrors();
const sendUsage = () => void flushUsage((days) => api.sendUsage(days));  // silent; at most every 5 minutes

async function signOut() {
  const name = state.me && state.me.username;
  showLogin("");  // first: an in-flight sync must not write the copy back after it is erased
  if (name) { await store.clearUser(name); clearShopping(name); }  // this user's copy, unsent edits and shopping list leave the device
  forgetPhotosOnDevice();
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
  checkImport();  // in parallel with the sync below
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
  sendUsage();
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
    h("a", { class: "brand", href: "#/", "aria-label": "Mamarmiton, retour aux recettes", onClick: goHome },
      h("img", { src: "/app/icon.svg", alt: "", width: 36, height: 36 }), h("span", { class: "serif", text: "Mamarmiton" })),
    h("div", { class: "topbar-actions" },
      h("a", { class: "btn btn-accent", href: "#/nouvelle", "aria-label": "Nouvelle recette" }, icon("plus", 20), h("span", { class: "btn-label", text: "Nouvelle recette" })),
      importBtn = h("button", { class: "btn btn-topbar", type: "button", hidden: !canImport(), "aria-label": "Importer une recette",
        title: "Importer une recette (photo, fichier ou texte)", onClick: openImport }, icon("scan", 22), h("span", { class: "btn-label", text: "Importer" })),
      h("a", { class: "btn btn-topbar", href: "#/courses", "aria-label": "Courses" }, icon("cart", 22), h("span", { class: "btn-label", text: "Courses" })),
      themeBtn,
      h("button", { class: "btn btn-topbar", type: "button", "aria-label": "Mon compte", onClick: () => openAccountDialog({ onSignOut: signOut }) },
        icon("user", 22), h("span", { class: "btn-label", text: state.me.display_name || state.me.username }))));
  main = h("main", { id: "main", tabindex: "-1" });
  // bottom-right, where websites put their help button
  const report = h("button", { class: "report-fab", type: "button", "aria-label": "Signaler un problème", title: "Signaler un problème",
    onClick: openReportDialog }, icon("message", 24), h("span", { class: "report-fab-label", text: "Signaler un problème" }));
  clear(root).append(topbar, main, report);
}

// the logo: back to the full recipe list (search, filters and open book cleared, sort kept)
function goHome() {
  state.filters = { ...state.filters, text: "", category: "", tags: [], book: null };
  const onHome = !location.hash || location.hash === "#/";
  if (onHome && view?.kind === "home") route();  // no hashchange fires, so redraw by hand
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
  } else if (kind === "courses") {
    view = renderCourses(main);
    view.kind = "courses";
  } else if (kind === "nouvelle") {
    view = renderEditor(main, null, { done: (rid) => { location.hash = `#/r/${rid}`; }, cancel: () => { location.hash = "#/"; },
      draft: takePendingDraft() });
    view.kind = "editor";
  } else if (kind === "modifier" && id) {
    view = renderEditor(main, id, { done: (rid) => { location.hash = `#/r/${rid}`; }, cancel: () => { location.hash = `#/r/${id}`; } });
    view.kind = "editor";
  } else {
    view = renderHome(main, { onNew: () => { location.hash = "#/nouvelle"; }, onImport: openImport, canImport });
    view.kind = "home";
  }
  if (view.kind !== "editor") main.focus({ preventScroll: true });
}
// leaving the editor with unsaved changes (Courses, logo, back button…) asks first instead of losing them
window.addEventListener("hashchange", async (e) => {
  if (view?.kind === "editor" && view.dirty?.()) {
    const target = location.hash;
    history.replaceState(null, "", new URL(e.oldURL).hash);  // stay put without re-rendering the form
    if (!(await view.confirmLeave())) return;
    view.discard();
    location.hash = target;
    return;
  }
  route();
});
window.addEventListener("beforeunload", (e) => {
  if (view?.kind === "editor" && view.dirty?.()) e.preventDefault();  // reload / close tab
});

// -------------------------------------------------------------------- sync
async function refresh() {
  if (!loggedIn || Date.now() - lastSync < 5000) return;
  lastSync = Date.now();
  try {
    const changed = await sync();
    afterChange(changed);
    sendUsage();
    checkImport();  // asked again only while the last try had no connection
  } catch { /* offline or session ended: the banner / login screen already say so */ }
}

function afterChange(changed) {
  if (!view || !changed || !changed.size) return;
  if (view.kind === "home") view.update();
  else if (view.kind === "courses") view.update();
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
