import { h } from "../dom.js";
import { openDialog, toast } from "../ui.js";
import { adminApi, action, facts, failed, loading, pageHead, panel, pill } from "./kit.js";
import { ago, bytes, dateTime, duration } from "./format.js";

export async function renderSystem(main, ctx) {
  loading(main);
  let backups;
  let security;
  try {
    [backups, security] = await Promise.all([adminApi.backups(), adminApi.security()]);
  } catch (e) {
    failed(main, e, () => renderSystem(main, ctx));
    return;
  }
  if (ctx.stale()) return;
  const reload = () => renderSystem(main, ctx);

  const nightly = backups.nightly;
  const nightlyFacts = nightly
    ? facts([
      ["Dernier passage", h("span", { title: dateTime(nightly.finished_at) }, `${ago(nightly.finished_at)} `,
        nightly.exit === 0 ? pill("ok", "ok") : pill(nightly.exit === 2 ? "incomplet" : "échec", "bad"))],
      nightly.exit !== 0 ? ["Problème", nightly.message || `code ${nightly.exit}`] : null,
      ["Copie sur le disque USB", nightly.usb ? pill("présente", "ok") : pill("absente", "bad")],
      ["Copie hors site", { ok: pill("ok", "ok"), failed: pill("échec", "bad"), off: pill("pas configurée", "muted") }[nightly.offsite] || nightly.offsite],
    ])
    : h("p", { class: "muted", text: "La tâche de nuit n'a encore rien signalé (ou elle tourne avec une ancienne version de backup-cron.sh)." });

  const drill = backups.drill;
  const drillBlock = drill
    ? h("div", { class: "stack-tight" },
      facts([
        ["Dernier test", h("span", { title: dateTime(drill.finished_at) }, `${ago(drill.finished_at)} `, drill.ok ? pill("réussi", "ok") : pill("échoué", "bad"))],
        ["Sauvegarde testée", drill.snapshot],
      ]),
      drill.problems.length ? h("ul", { class: "form-error" }, drill.problems.map((p) => h("li", { text: p }))) : null)
    : h("p", { class: "muted", text: "Jamais fait depuis ce tableau de bord." });

  const snapshots = backups.snapshots.length
    ? h("ul", { class: "plain-list" }, backups.snapshots.map((s) => h("li", { class: "list-row" },
      h("span", { class: "grow mono", text: s.name }), h("span", { class: "hint", text: `${ago(s.created_at)} · ${bytes(s.size)}` }))))
    : h("p", { class: "muted", text: "Aucune sauvegarde dans le dossier des données." });

  const blocked = security.blocked.length
    ? h("ul", { class: "plain-list" }, security.blocked.map((b) => h("li", { class: "list-row" },
      h("div", { class: "grow" },
        h("strong", { text: b.username === "" ? "Toute l'adresse" : b.username.startsWith("pw:") ? `Changement de mot de passe de ${b.username.slice(3)}` : b.username }),
        h("div", { class: "hint", text: `${b.ip} · ${b.failures} essais ratés · débloqué dans ${duration(b.retry_after)}` })),
      action("Débloquer", async () => { await adminApi.unblock(b.ip, b.username); toast("Débloqué.", "ok"); reload(); }))))
    : h("p", { class: "all-good", text: "Personne n'est bloqué." });

  main.replaceChildren(
    pageHead("Système", "Sauvegardes, tests de restauration et sécurité des connexions."),
    h("div", { class: "grid-2" },
      panel("Sauvegarde de nuit", nightlyFacts,
        h("p", { class: "hint", text: "Faite chaque nuit par ops/backup-cron.sh sur le NAS : copie vérifiée sur le disque USB." })),
      panel("Test de restauration", drillBlock,
        h("p", { class: "hint", text: "Restaure la dernière sauvegarde dans un dossier temporaire et la vérifie avec le vrai code. À faire chaque trimestre." }),
        h("div", {}, action("Tester la restauration", async () => {
          const r = await adminApi.drill();
          openDialog({ title: r.ok ? "Restauration réussie" : "Restauration en échec", build: (close) => h("div", { class: "stack" },
            r.problems.length ? h("ul", { class: "form-error" }, r.problems.map((p) => h("li", { text: p }))) : null,
            r.warnings.length ? h("ul", {}, r.warnings.map((p) => h("li", { text: p }))) : null,
            h("ul", { class: "hint" }, r.info.map((p) => h("li", { text: p }))),
            h("div", { class: "row end" }, h("button", { class: "btn btn-primary", type: "button", onClick: close, text: "Fermer" }))),
          onClose: reload });
        }, { tone: "primary" })))),
    panel("Sauvegardes sur le serveur",
      h("p", { class: "hint", text: "Les 3 derniers jours sont gardés ici ; le disque USB garde 7 jours et 8 semaines." }),
      snapshots,
      h("div", {}, action("Sauvegarder maintenant", async () => {
        const r = await adminApi.backupNow();
        toast(`Sauvegarde faite : ${r.name} (${bytes(r.size)}).`, "ok");
        reload();
      }))),
    panel("Connexions bloquées",
      h("p", { class: "hint", text: `Après ${security.attempts} mots de passe faux pour un compte (ou ${security.attempts * 4} depuis une même adresse), `
        + `il faut attendre ${duration(security.window_s)}. Un redémarrage du serveur efface tout.` }),
      blocked));
}
