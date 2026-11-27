import { api, token } from "./api.js";
import { h, icon, clear } from "./dom.js";
import { pendingCount, state, sync } from "./state.js";
import { busy, confirmDialog, openDialog, toast } from "./ui.js";
import { EXPLANATION, hit, setUsageEnabled, usageEnabled } from "./usage.js";
import { BACKUP_ACCEPT, OFFLINE_MESSAGE, backupFileName, importSummary } from "./backuplogic.js";
import { buildReport } from "./diagnostics.js";
import { TEXT_SIZES, applyTextSize, textSize } from "./textsize.js";

function field(label, input) {
  const id = `a-${Math.random().toString(36).slice(2, 8)}`;
  input.id = id;
  return h("div", { class: "field" }, h("label", { for: id, text: label }), input);
}

/**
 * The "Signaler un problème" form, drawn into `body`. `back` runs on the secondary button,
 * `sent` once the message is delivered.
 */
// What the message is about. "other" asks the person to say what it is.
const KINDS = [
  ["bug", "Un problème", "Décrivez ce qui s'est passé, avec vos mots."],
  ["idea", "Une idée", "Qu'aimeriez-vous que Mamarmiton sache faire ?"],
  ["other", "Autre chose", "Votre message, avec vos mots."],
];

function buildReportForm(body, { back, backText, sent }) {
  let kind = "bug";
  const message = h("textarea", { rows: 6, maxlength: 4000, required: true, "aria-label": "Votre message",
    placeholder: KINDS[0][2] });
  const other = h("input", { type: "text", maxlength: 80, placeholder: "Par exemple : une question, un merci…" });
  const otherField = field("De quoi s'agit-il ?", other);
  otherField.hidden = true;
  const kindButtons = KINDS.map(([value, label, placeholder]) => h("button", {
    class: `chip-btn chip${value === kind ? " is-on" : ""}`, type: "button", "aria-pressed": String(value === kind),
    text: label,
    onClick: (e) => {
      kind = value;
      for (const b of kindButtons) { const on = b === e.currentTarget; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", String(on)); }
      message.placeholder = placeholder;
      otherField.hidden = value !== "other";
      if (value === "other") other.focus();
    } }));
  const attach = h("input", { type: "checkbox", id: "report-log", checked: true });
  const error = h("p", { class: "form-error", role: "alert" });
  const send = h("button", { class: "btn btn-primary", type: "submit", text: "Envoyer" });
  const form = h("form", { class: "stack", novalidate: true },
    h("p", { text: "Votre message arrive directement à la personne qui s'occupe de Mamarmiton." }),
    h("div", { class: "row wrap", role: "group", "aria-label": "C'est à propos de" }, kindButtons),
    otherField,
    message,
    h("label", { class: "row", for: "report-log" }, attach, h("span", { text: "Joindre le journal des erreurs (aide à comprendre)" })),
    error,
    h("div", { class: "row end wrap" }, h("button", { class: "btn btn-secondary", type: "button", onClick: back, text: backText }), send));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    if (kind === "other" && !other.value.trim()) { error.textContent = "Dites en quelques mots de quoi il s'agit."; other.focus(); return; }
    if (!message.value.trim()) { error.textContent = "Écrivez quelques mots avant d'envoyer."; return; }
    await busy(send, async () => {
      try { await api.sendReport(buildReport(message.value, attach.checked, window, { category: kind, categoryOther: other.value })); toast("Merci, votre message a été envoyé.", "ok"); sent(); }
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

/** "Mes recettes": download a backup of my book, or add the recipes of a backup file. */
function backupSection() {
  const message = h("p", { class: "form-error", role: "alert" });
  const say = (err) => { message.textContent = err.offline ? OFFLINE_MESSAGE : err.message; };
  const download = h("button", { class: "btn btn-secondary btn-big", type: "button" }, icon("download", 20), "Télécharger une sauvegarde");
  download.addEventListener("click", () => busy(download, async () => {
    message.textContent = "";
    try {
      const url = URL.createObjectURL(await api.exportBackup());
      const link = h("a", { href: url, download: backupFileName() });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);  // after the browser has started the download
      hit("backup_export");
      toast("Votre sauvegarde est téléchargée.", "ok");
    } catch (err) { say(err); }
  }));
  const picker = h("input", { type: "file", accept: BACKUP_ACCEPT, hidden: true });
  const upload = h("button", { class: "btn btn-secondary btn-big", type: "button" }, icon("upload", 20), "Importer une sauvegarde");
  upload.addEventListener("click", () => { message.textContent = ""; picker.click(); });
  picker.addEventListener("change", () => {
    const file = picker.files && picker.files[0];
    picker.value = "";
    if (!file) return;
    busy(upload, async () => {
      try {
        const result = await api.importBackup(file);
        hit("backup_import");
        toast(importSummary(result), "ok");
        try { await sync(); } catch { /* the next sync picks the new recipes up */ }
        window.dispatchEvent(new Event("mamarmiton:data"));
      } catch (err) { say(err); }
    });
  });
  return h("div", { class: "stack" },
    h("h3", { class: "serif", text: "Mes recettes" }),
    h("p", { class: "muted", text: "Si vous aviez Mamarmiton sur ordinateur, choisissez le fichier « recette.json » de votre dossier Mamarmiton." }),
    download, upload, picker, message);
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
        // three big pills, like the "Un problème / Une idée" choice; the page grows as soon as one is chosen
        const sizeButtons = TEXT_SIZES.map(([value, label]) => h("button", {
          class: `chip-btn chip-big${value === textSize() ? " is-on" : ""}`, type: "button", "aria-pressed": String(value === textSize()), text: label,
          onClick: (e) => {
            applyTextSize(value, true);
            for (const b of sizeButtons) { const on = b === e.currentTarget; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", String(on)); }
          } }));
        const sizes = h("div", { class: "stack", role: "group", "aria-labelledby": "text-size-label" },
          h("p", { id: "text-size-label", class: "label", text: "Taille du texte" }),
          h("div", { class: "row wrap" }, sizeButtons));
        clear(body).append(
          h("p", {}, "Connecté en tant que ", h("strong", { text: state.me.display_name || state.me.username }),
            h("span", { class: "muted", text: ` (@${state.me.username})` })),
          h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: passwordForm }, icon("key", 20), "Changer mon mot de passe"),
          state.me.is_admin ? h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: inviteForm }, icon("plus", 20), "Créer une invitation") : null,
          h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: reportForm }, icon("pencil", 20), "Signaler un problème"),
          sizes, backupSection(), privacy, signOut);
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
