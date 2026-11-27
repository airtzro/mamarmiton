import { h } from "../dom.js";
import { adminApi, failed, loading, pageHead, pill } from "./kit.js";
import { eventLabel, heat, shortDate } from "./format.js";

let days = 56;
const PERIODS = [[28, "4 semaines"], [56, "8 semaines"], [91, "13 semaines"], [182, "6 mois"]];
const KIND_LABELS = { desktop: "Ordinateur", web: "Navigateur / téléphone", other: "Autre" };

export async function renderUsage(main, ctx) {
  loading(main);
  let data;
  try {
    data = await adminApi.usage(days);
  } catch (e) {
    failed(main, e, () => renderUsage(main, ctx));
    return;
  }
  if (ctx.stale()) return;

  const select = h("select", { "aria-label": "Période", onChange: () => { days = Number(select.value); renderUsage(main, ctx); } },
    PERIODS.map(([value, label]) => h("option", { value, selected: value === days, text: label })));

  const max = Math.max(0, ...data.events.flatMap((e) => e.counts));
  const head = h("tr", {}, h("th", { scope: "col", text: "Action" }),
    data.weeks.map((w) => h("th", { scope: "col", class: "num", title: `semaine du ${shortDate(w)}`, text: shortDate(w).slice(0, 5) })),
    h("th", { scope: "col", class: "num", text: "Total" }), h("th", { scope: "col", class: "num", text: "Jours" }));
  const rows = data.events.map((e) => h("tr", { class: e.total ? "" : "is-unused" },
    h("th", { scope: "row" }, eventLabel(e.event), " ", e.total ? null : pill("jamais", "muted")),
    e.counts.map((n) => h("td", { class: `num heat heat-${heat(n, max)}`, text: n ? String(n) : "·" })),
    h("td", { class: "num strong", text: String(e.total) }), h("td", { class: "num", text: String(e.days) })));
  const activeRows = Object.entries(data.active).map(([kind, counts]) => h("tr", {},
    h("th", { scope: "row", text: KIND_LABELS[kind] || kind }),
    counts.map((n) => h("td", { class: "num", text: n ? String(n) : "·" })),
    h("td", { class: "num strong", text: String(counts.reduce((a, b) => a + b, 0)) }), h("td", {})));
  const unused = data.events.filter((e) => !e.total).length;

  main.replaceChildren(
    pageHead("Utilisation", `Du ${shortDate(data.start)} au ${shortDate(data.end)}. Compteurs anonymes : ni compte, ni recette, ni texte.`, select),
    h("p", { class: "muted", text: unused
      ? `${unused} fonction${unused > 1 ? "s" : ""} sur ${data.events.length} jamais utilisée${unused > 1 ? "s" : ""} sur la période (en haut du tableau).`
      : "Toutes les fonctions ont servi sur la période." }),
    h("div", { class: "card table-wrap" }, h("table", { class: "data-table usage-table" },
      h("caption", { class: "sr-only", text: "Nombre d'utilisations par semaine, les moins utilisées en premier" }),
      h("thead", {}, head), h("tbody", {}, rows),
      h("tbody", { class: "usage-active" },
        h("tr", {}, h("th", { scope: "rowgroup", colspan: data.weeks.length + 3, text: "Appareils actifs (appareils × jours)" })),
        activeRows))),
    h("p", { class: "hint", text: "Chaque appareil envoie ses compteurs au plus toutes les 5 minutes ; un appareil hors ligne les envoie plus tard." }));
}
