import { api, token } from "./api.js";
import { h, icon, clear } from "./dom.js";
import { pendingCount, state } from "./state.js";
import { busy, confirmDialog, openDialog, toast } from "./ui.js";
import { EXPLANATION, setUsageEnabled, usageEnabled } from "./usage.js";
import { buildReport } from "./diagnostics.js";

function field(label, input) {
  const id = `a-${Math.random().toString(36).slice(2, 8)}`;
  input.id = id;
  return h("div", { class: "field" }, h("label", { for: id, text: label }), input);
}

/**
 * The "Signaler un problème" form, drawn into `body`. `back` runs on the secondary button,
 * `sent` once the message is delivered.
 */
function buildReportForm(body, { back, backText, sent }) {
  const message = h("textarea", { rows: 6, maxlength: 4000, required: true, "aria-label": "Votre message",
    placeholder: "Décrivez ce qui s'est passé, avec vos mots." });
  const attach = h("input", { type: "checkbox", id: "report-log", checked: true });
  const error = h("p", { class: "form-error", role: "alert" });
  const send = h("button", { class: "btn btn-primary", type: "submit", text: "Envoyer" });
  const form = h("form", { class: "stack", novalidate: true },
    h("p", { text: "Votre message arrive directement à la personne qui s'occupe de Mamarmiton." }),
    message,
    h("label", { class: "row", for: "report-log" }, attach, h("span", { text: "Joindre le journal des erreurs (aide à comprendre)" })),
    error,
    h("div", { class: "row end wrap" }, h("button", { class: "btn btn-secondary", type: "button", onClick: back, text: backText }), send));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    if (!message.value.trim()) { error.textContent = "Écrivez quelques mots avant d'envoyer."; return; }
    await busy(send, async () => {
      try { await api.sendReport(buildReport(message.value, attach.checked)); toast("Merci, votre message a été envoyé.", "ok"); sent(); }
      catch (err) { error.textContent = err.offline ? "Pas de connexion pour le moment. Réessayez plus tard." : err.message; }
    });
  });
  clear(body).append(form);
  message.focus();
}

/** "Signaler un problème" on its own, from the floating button in the bottom-right corner. */
export function openReportDialog() {
  openDialog({
    title: "Signaler un problème",
    build: (close) => {
      const body = h("div");
      queueMicrotask(() => buildReportForm(body, { back: close, backText: "Annuler", sent: close }));
      return body;
    },
  });
}

/** "Mon compte": change password, invitation (admin), sign out. */
export function openAccountDialog({ onSignOut }) {
  openDialog({
    title: "Mon compte",
    build: (close) => {
      const body = h("div", { class: "stack" });

      function home() {
        const signOut = h("button", { class: "btn btn-secondary btn-big", type: "button" }, icon("logout", 20), "Se déconnecter");
        signOut.addEventListener("click", async () => {
          if (pendingCount > 0) {
            const ok = await confirmDialog({ title: "Modifications non envoyées", danger: true, confirmText: "Me déconnecter quand même",
              cancelText: "Rester connecté", message: `${pendingCount} modification${pendingCount > 1 ? "s" : ""} faite${pendingCount > 1 ? "s" : ""} hors ligne ne ${pendingCount > 1 ? "sont" : "est"} pas encore envoyée${pendingCount > 1 ? "s" : ""} et sera perdue.` });
            if (!ok) return;
          }
          await busy(signOut, async () => { try { await api.logout(); } catch { /* the token is dropped anyway */ } });
          close();
          onSignOut();
        });
        const share = h("input", { type: "checkbox", id: "share-usage", checked: usageEnabled() });
        share.addEventListener("change", () => setUsageEnabled(share.checked));
        const privacy = h("div", { class: "stack" },
          h("label", { class: "row", for: "share-usage" }, share, h("span", { text: "Partager des statistiques d'utilisation anonymes" })),
          h("p", { class: "muted", text: EXPLANATION }));
        clear(body).append(
          h("p", {}, "Connecté en tant que ", h("strong", { text: state.me.display_name || state.me.username }),
            h("span", { class: "muted", text: ` (@${state.me.username})` })),
          h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: passwordForm }, icon("key", 20), "Changer mon mot de passe"),
          state.me.is_admin ? h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: inviteForm }, icon("plus", 20), "Créer une invitation") : null,
          h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: reportForm }, icon("pencil", 20), "Signaler un problème"),
          privacy, signOut);
      }

      function reportForm() { buildReportForm(body, { back: home, backText: "Retour", sent: home }); }

      function passwordForm() {
        const old = h("input", { type: "password", autocomplete: "current-password", required: true });
        const next = h("input", { type: "password", autocomplete: "new-password", required: true });
        const again = h("input", { type: "password", autocomplete: "new-password", required: true });
        const error = h("p", { class: "form-error", role: "alert" });
        const save = h("button", { class: "btn btn-primary", type: "submit", text: "Changer le mot de passe" });
        const form = h("form", { class: "stack", novalidate: true },
          field("Mot de passe actuel", old), field("Nouveau mot de passe", next), field("Nouveau mot de passe (encore une fois)", again),
          error,
          h("div", { class: "row end wrap" }, h("button", { class: "btn btn-secondary", type: "button", onClick: home, text: "Retour" }), save));
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          error.textContent = "";
          if (!old.value || !next.value) { error.textContent = "Remplissez les trois champs."; return; }
          if (next.value !== again.value) { error.textContent = "Les deux nouveaux mots de passe ne sont pas identiques."; return; }
          await busy(save, async () => {
            try { await api.changePassword(old.value, next.value); toast("Mot de passe changé. Vos autres appareils sont déconnectés.", "ok"); home(); }
            catch (err) { error.textContent = err.message; }
          });
        });
        clear(body).append(form);
        old.focus();
      }

      function inviteForm(code) {
        const note = h("input", { type: "text", maxlength: 100, placeholder: "ex. pour Tata Marie" });
        const error = h("p", { class: "form-error", role: "alert" });
        const create = h("button", { class: "btn btn-primary", type: "submit", text: "Créer l'invitation" });
        const result = h("div", { class: "stack" });
        const form = h("form", { class: "stack", novalidate: true },
          h("p", { text: "Un code d'invitation permet à une personne de créer son compte. Il ne sert qu'une fois." }),
          field("Pour qui ? (facultatif)", note), error,
          h("div", { class: "row end wrap" }, h("button", { class: "btn btn-secondary", type: "button", onClick: home, text: "Retour" }), create), result);
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          error.textContent = "";
          await busy(create, async () => {
            try {
              const invite = await api.createInvite(note.value.trim());
              const box = h("input", { type: "text", readOnly: true, value: invite.code, class: "code-box", "aria-label": "Code d'invitation" });
              const copy = h("button", { class: "btn btn-secondary", type: "button" }, icon("copy", 18), "Copier");
              copy.addEventListener("click", async () => {
                try { await navigator.clipboard.writeText(invite.code); toast("Code copié.", "ok"); }
                catch { box.select(); toast("Sélectionné : copiez-le avec le menu de votre téléphone.", "info"); }
              });
              const expires = new Date(invite.expires_at);
              clear(result).append(h("h3", { class: "serif", text: "Code à donner" }),
                h("div", { class: "row" }, box, copy),
                h("p", { class: "muted", text: `Valable jusqu'au ${expires.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" })}.` }));
              box.focus(); box.select();
            } catch (err) { error.textContent = err.message; }
          });
        });
        clear(body).append(form);
        note.focus();
      }

      home();
      return body;
    },
  });
}

export { token };
