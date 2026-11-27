// Admin dashboard: sign-in (admin accounts only), navigation, and one view per hash route.
import { api, setHandlers, token } from "../api.js";
import { h, icon, clear } from "../dom.js";
import { busy, initTheme, isDark, toggleTheme } from "../ui.js";
import { adminApi, field } from "./kit.js";
import { renderOverview } from "./overview.js";
import { renderUsers } from "./users.js";
import { renderInvites } from "./invites.js";
import { renderReports } from "./reports.js";
import { renderUsage } from "./usage-view.js";
import { renderSystem } from "./system.js";

const VIEWS = [
  { id: "", label: "Vue d'ensemble", icon: "eye", render: renderOverview },
  { id: "comptes", label: "Comptes", icon: "users", render: renderUsers },
  { id: "invitations", label: "Invitations", icon: "plus", render: renderInvites },
  { id: "messages", label: "Messages", icon: "chef", render: renderReports },
  { id: "utilisation", label: "Utilisation", icon: "search", render: renderUsage },
  { id: "systeme", label: "Système", icon: "key", render: renderSystem },
];
// overview alerts point at these
const ALIASES = { overview: "", users: "comptes", invites: "invitations", reports: "messages",
  usage: "utilisation", backups: "systeme", security: "systeme" };

const root = document.getElementById("root");
let me = null;
let main = null;
let nav = null;
let renderToken = 0;

initTheme();
setHandlers({ unauthorized: () => { if (me) showLogin("Votre session a expiré. Reconnectez-vous."); } });

export function go(view) {
  const id = ALIASES[view] ?? view;
  location.hash = `#/${id}`;
}

function showLogin(message = "", { keepToken = false } = {}) {
  me = null;
  if (!keepToken) token.clear();
  document.title = "Administration · Mamarmiton";
  const error = h("p", { class: "form-error", role: "alert", text: message });
  const username = h("input", { type: "text", autocomplete: "username", autocapitalize: "none", spellcheck: "false", required: true });
  const password = h("input", { type: "password", autocomplete: "current-password", required: true });
  const submit = h("button", { class: "btn btn-primary btn-big", type: "submit", text: "Se connecter" });
  const form = h("form", { class: "stack", novalidate: true },
    h("h2", { class: "serif", text: "Administration" }),
    h("p", { class: "muted", text: "Réservé au compte administrateur du serveur." }),
    field("Nom d'utilisateur", username), field("Mot de passe", password), error, submit,
    h("a", { class: "btn btn-link", href: "/", text: "Aller au carnet de recettes" }));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    error.textContent = "";
    if (!username.value.trim() || !password.value) { error.textContent = "Entrez le nom d'utilisateur et le mot de passe."; return; }
    busy(submit, async () => {
      try {
        const result = await api.login(username.value.trim(), password.value, "Tableau de bord admin");
        token.set(result.token);
        if (!result.user.is_admin) {
          await api.logout().catch(() => {});
          token.clear();
          error.textContent = "Ce compte n'est pas administrateur.";
          password.value = "";
          return;
        }
        start(result.user);
      } catch (e) {
        error.textContent = e.message;
        password.value = "";
        password.focus();
      }
    });
  });
  clear(root).append(h("main", { class: "login", id: "main" },
    h("div", { class: "login-brand" },
      h("img", { src: "/app/icon.svg", alt: "", width: 72, height: 72, class: "login-logo" }),
      h("h1", { class: "serif", text: "Mamarmiton" })),
    h("div", { class: "login-card" }, form)));
  username.focus();
}

function start(user) {
  me = user;
  const themeBtn = h("button", { class: "btn btn-icon btn-topbar", type: "button", "aria-label": "Changer entre thème clair et sombre" });
  const paintTheme = () => { clear(themeBtn).append(icon(isDark() ? "sun" : "moon", 22)); };
  themeBtn.addEventListener("click", () => { toggleTheme(); paintTheme(); });
  paintTheme();
  const signOut = h("button", { class: "btn btn-topbar", type: "button", "aria-label": "Se déconnecter" },
    icon("logout", 22), h("span", { class: "btn-label", text: "Déconnexion" }));
  signOut.addEventListener("click", async () => {
    await api.logout().catch(() => {});
    showLogin("Vous êtes déconnecté.");
  });
  nav = h("nav", { class: "admin-nav", "aria-label": "Sections" },
    VIEWS.map((v) => h("a", { href: `#/${v.id}`, "data-view": v.id }, icon(v.icon, 20),
      h("span", { text: v.label }), h("span", { class: "nav-badge", hidden: true }))));
  main = h("main", { id: "main", class: "container admin-main", tabindex: "-1" });
  clear(root).append(
    h("header", { class: "topbar" },
      h("a", { class: "brand", href: "#/", "aria-label": "Vue d'ensemble" },
        h("img", { src: "/app/icon.svg", alt: "", width: 36, height: 36 }),
        h("span", { class: "serif", text: "Mamarmiton" }), h("span", { class: "brand-sub", text: "admin" })),
      h("div", { class: "topbar-actions" },
        h("a", { class: "btn btn-topbar", href: "/", "aria-label": "Ouvrir le carnet de recettes" }, icon("pot", 22),
          h("span", { class: "btn-label", text: "Carnet" })),
        h("span", { class: "who", text: `@${user.username}` }), themeBtn, signOut)),
    nav, main);
  route();
}

export async function refreshBadges() {
  if (!nav) return;
  try {
    const unread = (await adminApi.reports("unread")).length;
    const badge = nav.querySelector('[data-view="messages"] .nav-badge');
    badge.textContent = String(unread);
    badge.hidden = !unread;
  } catch { /* the badge is a nicety */ }
}

async function route() {
  if (!me || !main) return;
  const id = (location.hash || "#/").replace(/^#\/?/, "").split("/")[0];
  const view = VIEWS.find((v) => v.id === id) || VIEWS[0];
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());  // a dialog belongs to the view it was opened from
  for (const link of nav.querySelectorAll("a")) {
    if (link.dataset.view === view.id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${view.label} · Administration Mamarmiton`;
  const mine = ++renderToken;
  const fresh = h("main", { id: "main", class: "container admin-main", tabindex: "-1" });
  main.replaceWith(fresh);
  main = fresh;
  await view.render(fresh, { go, refreshBadges, me, stale: () => mine !== renderToken });
  refreshBadges();
}

window.addEventListener("hashchange", route);

(async () => {
  if (!token.get()) { showLogin(); return; }
  try {
    const user = await api.me();
    if (user.is_admin) start(user);
    else showLogin(`Vous êtes connecté au carnet en tant que « ${user.username} », qui n'est pas administrateur. `
      + "Connectez-vous avec le compte administrateur (cela vous déconnecte du carnet sur ce navigateur).", { keepToken: true });
  } catch (e) {
    showLogin(e.offline ? e.message : "");
  }
})();
