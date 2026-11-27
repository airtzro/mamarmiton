import { h } from "../dom.js";
import { confirmDialog, openDialog, toast } from "../ui.js";
import { adminApi, action, copyButton, failed, loading, pageHead, pill } from "./kit.js";
import { ago, dateTime, plural } from "./format.js";

let filter = "unread";
let category = "all";

// The labels people pick when they send a message (web and desktop use the same three).
const CATEGORIES = [["bug", "Problèmes", "Problème", "bad"], ["idea", "Idées", "Idée", "accent"], ["other", "Autres", "Autre", "muted"]];

function categoryPill(r) {
  const [, , label, tone] = CATEGORIES.find(([key]) => key === r.category) || CATEGORIES[0];
  return pill(r.category === "other" && r.category_other ? `${label} : ${r.category_other}` : label, tone);
}

/** Let the admin move a message to another label. */
function relabel(r, done) {
  openDialog({
    title: "Changer l'étiquette",
    build: (close) => {
      const select = h("select", { "aria-label": "Étiquette" },
        CATEGORIES.map(([key, , label]) => h("option", { value: key, text: label, selected: key === r.category })));
      const other = h("input", { type: "text", maxlength: 80, value: r.category_other || "", placeholder: "De quoi s'agit-il ?",
        "aria-label": "De quoi s'agit-il ?" });
      const otherRow = h("div", { class: "field" }, other);
      const sync = () => { otherRow.hidden = select.value !== "other"; };
      select.addEventListener("change", sync);
      sync();
      const error = h("p", { class: "form-error", role: "alert" });
      const save = action("Enregistrer", async () => {
        if (select.value === "other" && !other.value.trim()) { error.textContent = "Dites en quelques mots de quoi il s'agit."; return; }
        await adminApi.setReportCategory(r.id, select.value, select.value === "other" ? other.value.trim() : "");
        close();
        done();
      }, { tone: "primary", small: false });
      return h("div", { class: "stack" }, select, otherRow, error, h("div", { class: "row end" }, save));
    },
  });
}

export async function renderReports(main, ctx) {
  loading(main);
  let reports, counts;
  try {
    [reports, counts] = await Promise.all([adminApi.reports(filter, category), adminApi.reportCounts(filter)]);
  } catch (e) {
    failed(main, e, () => renderReports(main, ctx));
    return;
  }
  if (ctx.stale()) return;
  const reload = () => { renderReports(main, ctx); ctx.refreshBadges(); };

  const tabs = h("div", { class: "row wrap", role: "group", "aria-label": "Filtre" },
    [["unread", "Non lus"], ["all", "Tous"]].map(([value, label]) => h("button", {
      class: `chip-btn chip${filter === value ? " is-on" : ""}`, type: "button", "aria-pressed": String(filter === value),
      onClick: () => { filter = value; renderReports(main, ctx); }, text: label })));

  const total = counts.bug + counts.idea + counts.other;
  const sorts = h("div", { class: "row wrap", role: "group", "aria-label": "Étiquette" },
    [["all", "Toutes les étiquettes", total], ...CATEGORIES.map(([key, plural]) => [key, plural, counts[key]])]
      .map(([value, label, n]) => h("button", {
        class: `chip-btn chip${category === value ? " is-on" : ""}`, type: "button", "aria-pressed": String(category === value),
        onClick: () => { category = value; renderReports(main, ctx); } },
      label, h("span", { class: "chip-count", text: ` ${n}` }))));

  const unread = reports.filter((r) => !r.read_at).length;
  const cards = reports.map((r) => h("article", { class: `card pad report${r.read_at ? " is-read" : ""}` },
    h("header", { class: "row wrap between" },
      h("div", {},
        h("strong", { text: r.display_name }), h("span", { class: "muted", text: ` @${r.username}` }),
        h("div", { class: "hint", title: dateTime(r.created_at), text: `${ago(r.created_at)} · ${[r.client, r.app_version].filter(Boolean).join(" ")}${r.os ? ` · ${r.os}` : ""}` })),
      h("div", { class: "row wrap" }, categoryPill(r), r.read_at ? pill("lu", "muted") : pill("nouveau", "warn"))),
    h("p", { class: "report-message", text: r.message }),
    r.log_tail ? h("details", { class: "report-log" },
      h("summary", { text: `Journal joint (${plural(r.log_tail.split("\n").length, "ligne")})` }),
      h("pre", { text: r.log_tail }),
      copyButton(() => r.log_tail, "Copier le journal")) : null,
    h("div", { class: "row wrap" },
      action(r.read_at ? "Marquer non lu" : "Marquer lu", async () => { await adminApi.markReport(r.id, !r.read_at); reload(); }),
      action("Changer l'étiquette", () => relabel(r, reload)),
      copyButton(() => `${r.display_name} (@${r.username}), ${dateTime(r.created_at)}, ${r.client} ${r.app_version} ${r.os}\n\n${r.message}\n\n${r.log_tail || ""}`,
        "Copier tout"),
      action("Supprimer", async () => {
        if (!(await confirmDialog({ title: "Supprimer ce message ?", message: "Il disparaît du serveur.", confirmText: "Supprimer", danger: true }))) return;
        await adminApi.deleteReport(r.id);
        toast("Message supprimé.", "ok");
        reload();
      }, { tone: "danger" }))));

  main.replaceChildren(
    pageHead("Messages", "Ce que la famille envoie avec « Signaler un problème » (dans le carnet et l'application) : problèmes, idées et autres.",
      unread ? action("Tout marquer lu", async () => { await adminApi.markAllReports(); reload(); }) : null),
    tabs,
    sorts,
    cards.length ? h("div", { class: "stack" }, cards)
      : h("p", { class: "all-good", text: `${filter === "unread" ? "Aucun nouveau message" : "Aucun message"}${category === "all" ? "" : " avec cette étiquette"}.` }),
    h("p", { class: "hint", text: "Les journaux sont nettoyés à l'envoi (jetons, mots de passe et adresses e-mail masqués)." }));
}
