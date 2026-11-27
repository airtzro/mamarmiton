import { h } from "../dom.js";
import { confirmDialog, toast } from "../ui.js";
import { adminApi, action, copyButton, failed, loading, pageHead, pill } from "./kit.js";
import { ago, dateTime, plural } from "./format.js";

let filter = "unread";

export async function renderReports(main, ctx) {
  loading(main);
  let reports;
  try {
    reports = await adminApi.reports(filter);
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

  const unread = reports.filter((r) => !r.read_at).length;
  const cards = reports.map((r) => h("article", { class: `card pad report${r.read_at ? " is-read" : ""}` },
    h("header", { class: "row wrap between" },
      h("div", {},
        h("strong", { text: r.display_name }), h("span", { class: "muted", text: ` @${r.username}` }),
        h("div", { class: "hint", title: dateTime(r.created_at), text: `${ago(r.created_at)} · ${[r.client, r.app_version].filter(Boolean).join(" ")}${r.os ? ` · ${r.os}` : ""}` })),
      r.read_at ? pill("lu", "muted") : pill("nouveau", "warn")),
    h("p", { class: "report-message", text: r.message }),
    r.log_tail ? h("details", { class: "report-log" },
      h("summary", { text: `Journal joint (${plural(r.log_tail.split("\n").length, "ligne")})` }),
      h("pre", { text: r.log_tail }),
      copyButton(() => r.log_tail, "Copier le journal")) : null,
    h("div", { class: "row wrap" },
      action(r.read_at ? "Marquer non lu" : "Marquer lu", async () => { await adminApi.markReport(r.id, !r.read_at); reload(); }),
      copyButton(() => `${r.display_name} (@${r.username}), ${dateTime(r.created_at)}, ${r.client} ${r.app_version} ${r.os}\n\n${r.message}\n\n${r.log_tail || ""}`,
        "Copier tout"),
      action("Supprimer", async () => {
        if (!(await confirmDialog({ title: "Supprimer ce message ?", message: "Il disparaît du serveur.", confirmText: "Supprimer", danger: true }))) return;
        await adminApi.deleteReport(r.id);
        toast("Message supprimé.", "ok");
        reload();
      }, { tone: "danger" }))));

  main.replaceChildren(
    pageHead("Messages", "Ce que la famille envoie avec « Signaler un problème » (dans le carnet et l'application).",
      unread ? action("Tout marquer lu", async () => { await adminApi.markAllReports(); reload(); }) : null),
    tabs,
    cards.length ? h("div", { class: "stack" }, cards)
      : h("p", { class: "all-good", text: filter === "unread" ? "Aucun nouveau message." : "Aucun message." }),
    h("p", { class: "hint", text: "Les journaux sont nettoyés à l'envoi (jetons, mots de passe et adresses e-mail masqués)." }));
}
