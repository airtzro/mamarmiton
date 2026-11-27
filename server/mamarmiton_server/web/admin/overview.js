import { h, icon } from "../dom.js";
import { toast } from "../ui.js";
import { adminApi, action, facts, failed, loading, pageHead, panel, pill, showSecret } from "./kit.js";
import { ago, bytes, duration, inviteMessage, plural } from "./format.js";

function tile(label, value, sub, onClick) {
  const body = [h("span", { class: "tile-label", text: label }), h("span", { class: "tile-value", text: String(value) }),
    sub ? h("span", { class: "tile-sub", text: sub }) : null];
  return onClick
    ? h("button", { class: "tile card", type: "button", onClick }, ...body)
    : h("div", { class: "tile card" }, ...body);
}

export async function newInvite(note = "") {
  const invite = await adminApi.createInvite(note);
  showSecret({
    title: "Nouvelle invitation",
    intro: invite.note ? `Invitation « ${invite.note} » :` : "Code d'invitation :",
    secret: invite.code,
    message: inviteMessage(invite.code, location.origin, invite.expires_at),
  });
  return invite;
}

export async function renderOverview(main, ctx) {
  loading(main);
  let data;
  try {
    data = await adminApi.overview();
  } catch (e) {
    failed(main, e, () => renderOverview(main, ctx));
    return;
  }
  if (ctx.stale()) return;
  const { server, counts, storage, backups, alerts } = data;

  const alertList = alerts.length
    ? h("ul", { class: "alerts" }, alerts.map((a) => h("li", { class: `alert alert-${a.level}` },
      h("span", { class: "alert-dot", "aria-hidden": "true" }), h("span", { class: "grow", text: a.text }),
      a.view ? h("button", { class: "btn btn-link", type: "button", onClick: () => ctx.go(a.view), text: "Voir" }) : null)))
    : h("p", { class: "all-good" }, icon("check", 22), "Tout va bien : rien ne demande votre attention.");

  const disk = storage.disk;
  const diskMeter = disk
    ? h("div", { class: "stack-tight" },
      h("meter", { min: 0, max: disk.total, value: disk.total - disk.free, low: disk.total * 0.7, high: disk.total * 0.9,
        optimum: 0, "aria-label": "Espace disque utilisé" }),
      h("span", { class: "hint", text: `${bytes(disk.free)} libres sur ${bytes(disk.total)}` }))
    : h("span", { class: "muted", text: "inconnu" });

  const latest = backups.latest;
  main.replaceChildren(
    pageHead("Vue d'ensemble", `Serveur Mamarmiton ${server.version}, en marche depuis ${duration(server.uptime_s)}.`,
      action("Créer une invitation", () => newInvite(), { tone: "primary", iconName: "plus" })),
    h("section", { class: "panel", "aria-label": "À surveiller" }, alertList),
    h("div", { class: "tiles" },
      tile("Comptes", counts.users, `${plural(counts.active_users_7d, "actif")} cette semaine`, () => ctx.go("users")),
      tile("Recettes", counts.recipes, `${plural(counts.recipes_changed_7d, "modifiée")} cette semaine`),
      tile("Photos", counts.photos, bytes(counts.photo_bytes)),
      tile("Messages non lus", counts.unread_reports, "« Signaler un problème »", () => ctx.go("reports")),
      tile("Invitations en attente", counts.open_invites, null, () => ctx.go("invites"))),
    h("div", { class: "grid-2" },
      panel("Sauvegardes",
        facts([
          ["Dernière", latest ? h("span", {}, `${ago(latest.created_at)} `, backups.stale ? pill("trop ancienne", "bad") : pill("ok", "ok"))
            : pill("aucune", "bad")],
          ["Taille", latest ? bytes(latest.size) : "—"],
          ["Test de restauration", backups.drill_due ? pill("à faire", "warn") : pill("fait ce trimestre", "ok")],
        ]),
        h("div", { class: "row wrap" },
          action("Sauvegarder maintenant", async () => {
            const r = await adminApi.backupNow();
            toast(`Sauvegarde faite : ${r.name} (${bytes(r.size)}).`, "ok");
            renderOverview(main, ctx);
          }),
          h("button", { class: "btn btn-link", type: "button", onClick: () => ctx.go("backups"), text: "Détails" }))),
      panel("Stockage",
        facts([
          ["Base de données", bytes(storage.db_bytes)],
          ["Photos", bytes(storage.photo_bytes)],
          ["Disque", diskMeter],
        ])),
      panel("Serveur",
        facts([
          ["Version", server.version],
          ["Schéma de la base", server.schema === server.schema_latest ? `v${server.schema}` : h("span", {}, `v${server.schema} `, pill(`attendu v${server.schema_latest}`, "bad"))],
          ["Démarré", ago(server.started_at)],
          ["Version minimale (ordinateur)", server.min_desktop_version || "aucune"],
          ["Python", server.python],
        ])),
      panel("Réglages",
        facts([
          ["Validité d'une invitation", plural(server.invite_days, "jour")],
          ["Déconnexion après", `${plural(server.token_idle_days, "jour")} sans utilisation`],
          ["Taille max d'une photo", `${server.max_image_mb} Mo`],
          ["Appareils connectés", String(counts.sessions)],
          ["Partages", `${plural(counts.book_shares, "carnet")}, ${plural(counts.recipe_shares, "recette")}`],
        ]),
        h("p", { class: "hint", text: "Ces réglages se changent dans le fichier .env du serveur (voir server/README.md)." }))));
}
