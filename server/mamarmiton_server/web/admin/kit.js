// Shared pieces of the admin dashboard: API calls, small widgets, password prompts.
import { request } from "../api.js";
import { h, icon } from "../dom.js";
import { openDialog, toast, busy } from "../ui.js";

const json = async (promise) => (await promise).json();
const u = (name) => encodeURIComponent(name);
const A = "/admin/api";

export const adminApi = {
  overview: () => json(request("GET", `${A}/overview`)),
  users: () => json(request("GET", `${A}/users`)),
  changeUser: (name, body) => json(request("PATCH", `${A}/users/${u(name)}`, { json: body })),
  sessions: (name) => json(request("GET", `${A}/users/${u(name)}/sessions`)),
  revokeSession: (name, id) => request("DELETE", `${A}/users/${u(name)}/sessions/${u(id)}`),
  signOutEverywhere: (name) => json(request("POST", `${A}/users/${u(name)}/signout`)),
  resetPassword: (name, admin_password) => json(request("POST", `${A}/users/${u(name)}/password`, { json: { admin_password } })),
  deleteUser: (name, admin_password, confirm_username) =>
    json(request("POST", `${A}/users/${u(name)}/delete`, { json: { admin_password, confirm_username } })),
  invites: () => json(request("GET", `${A}/invites`)),
  createInvite: (note) => json(request("POST", "/invites", { json: { note } })),
  revokeInvite: (id) => request("DELETE", `${A}/invites/${u(id)}`),
  cleanupInvites: () => json(request("POST", `${A}/invites/cleanup`)),
  reports: (status, category = "all") => json(request("GET",
    `${A}/reports?status=${status === "unread" ? "unread" : "all"}&category=${encodeURIComponent(category)}`)),
  reportCounts: (status) => json(request("GET", `${A}/reports/counts?status=${status === "unread" ? "unread" : "all"}`)),
  setReportCategory: (id, category, category_other = "") =>
    request("POST", `${A}/reports/${Number(id)}/category`, { json: { category, category_other } }),
  markReport: (id, read) => request("POST", `${A}/reports/${Number(id)}/read`, { json: { read } }),
  markAllReports: () => json(request("POST", `${A}/reports/read-all`)),
  deleteReport: (id) => request("DELETE", `${A}/reports/${Number(id)}`),
  usage: (days) => json(request("GET", `${A}/usage?days=${Number(days) || 56}`)),
  security: () => json(request("GET", `${A}/security`)),
  unblock: (ip, username) => request("POST", `${A}/security/unblock`, { json: { ip, username } }),
  backups: () => json(request("GET", `${A}/backups`)),
  backupNow: () => json(request("POST", `${A}/backups`)),
  drill: () => json(request("POST", `${A}/backups/drill`)),
};

export function pill(text, tone = "muted") {
  return h("span", { class: `pill pill-${tone}`, text });
}

export function pageHead(title, subtitle, ...actions) {
  return h("div", { class: "page-head" },
    h("div", { class: "grow" }, h("h1", { class: "serif", text: title }),
      subtitle ? h("p", { class: "muted", text: subtitle }) : null),
    actions.length ? h("div", { class: "row wrap" }, ...actions) : null);
}

export function panel(title, ...children) {
  return h("section", { class: "card pad panel" }, title ? h("h2", { class: "serif", text: title }) : null, ...children);
}

/** Definition list: [[label, value], …] (value may be a node). */
export function facts(pairs) {
  return h("dl", { class: "facts" }, pairs.filter(Boolean).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, v)]));
}

export function loading(main) {
  main.replaceChildren(h("p", { class: "muted loading", text: "Chargement…" }));
}

export function failed(main, error, retry) {
  main.replaceChildren(h("div", { class: "card pad stack" },
    h("p", { class: "form-error", role: "alert", text: error.message || String(error) }),
    retry ? h("div", {}, h("button", { class: "btn btn-secondary", type: "button", onClick: retry, text: "Réessayer" })) : null));
}

export async function copyText(text, done = "Copié.") {
  try {
    await navigator.clipboard.writeText(text);
    toast(done, "ok");
  } catch {
    toast("Copie impossible : sélectionnez le texte et copiez-le à la main.", "error");
  }
}

export function copyButton(getText, label = "Copier", done) {
  return h("button", { class: "btn btn-secondary btn-small", type: "button", onClick: () => copyText(getText(), done) },
    icon("copy", 18), label);
}

/** A button that runs `task` (busy while it runs) and toasts its error. */
export function action(label, task, { tone = "secondary", small = true, iconName } = {}) {
  const btn = h("button", { class: `btn btn-${tone}${small ? " btn-small" : ""}`, type: "button" },
    iconName ? icon(iconName, 18) : null, label);
  btn.addEventListener("click", () => busy(btn, async () => {
    try { await task(); } catch (e) { toast(e.message, "error"); }
  }));
  return btn;
}

let fieldSeq = 0;
export function field(label, input, hint) {
  input.id = input.id || `af-${++fieldSeq}`;
  return h("div", { class: "field" }, h("label", { for: input.id, text: label }), input,
    hint ? h("p", { class: "hint", text: hint }) : null);
}

/**
 * Ask the admin for their own password (and optionally to type a confirmation word),
 * then run `task(password, typed)`. The dialog stays open on a wrong password.
 * Resolves with the task's result, or null when cancelled.
 */
export function withPassword({ title, message, confirmText, danger = false, typeToConfirm = null, task }) {
  return new Promise((resolve) => {
    let result = null;
    openDialog({
      title,
      onClose: () => resolve(result),
      build: (close) => {
        const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
        const typed = typeToConfirm ? h("input", { type: "text", autocomplete: "off", autocapitalize: "none", spellcheck: "false" }) : null;
        const error = h("p", { class: "form-error", role: "alert" });
        const submit = h("button", { class: `btn ${danger ? "btn-danger-solid" : "btn-primary"}`, type: "submit", text: confirmText });
        const form = h("form", { class: "stack" },
          message ? (typeof message === "string" ? h("p", { text: message }) : message) : null,
          typed ? field(`Tapez « ${typeToConfirm} » pour confirmer`, typed) : null,
          field("Votre mot de passe (administrateur)", pw),
          error,
          h("div", { class: "row end wrap" },
            h("button", { class: "btn btn-secondary", type: "button", onClick: close, text: "Annuler" }), submit));
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          error.textContent = "";
          if (typed && typed.value.trim().toLowerCase() !== typeToConfirm.toLowerCase()) {
            error.textContent = `Tapez exactement « ${typeToConfirm} ».`;
            return;
          }
          if (!pw.value) { error.textContent = "Entrez votre mot de passe."; return; }
          busy(submit, async () => {
            try {
              result = await task(pw.value, typed ? typed.value.trim() : "");
              close();
            } catch (e) {
              error.textContent = e.message;
              pw.value = "";
              pw.focus();
            }
          });
        });
        setTimeout(() => (typed || pw).focus(), 0);
        return form;
      },
    });
  });
}

/** Show a secret once (invite code, temporary password) with a ready-to-send message. */
export function showSecret({ title, intro, secret, message, after }) {
  openDialog({
    title,
    onClose: after,
    build: (close) => h("div", { class: "stack" },
      intro ? h("p", { text: intro }) : null,
      h("p", { class: "secret", text: secret }),
      h("div", { class: "row wrap" },
        copyButton(() => secret, "Copier le code"),
        message ? copyButton(() => message, "Copier le message à envoyer", "Message copié : collez-le dans un SMS ou un mail.") : null),
      message ? h("pre", { class: "message-preview", text: message }) : null,
      h("p", { class: "hint", text: "Il ne sera plus affiché : le serveur n'en garde qu'une empreinte." }),
      h("div", { class: "row end" }, h("button", { class: "btn btn-primary", type: "button", onClick: close, text: "J'ai noté" }))),
  });
}
