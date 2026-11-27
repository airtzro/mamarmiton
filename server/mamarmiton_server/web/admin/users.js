import { h, icon } from "../dom.js";
import { busy, confirmDialog, openDialog, toast } from "../ui.js";
import { adminApi, action, failed, field, loading, pageHead, pill, showSecret, withPassword } from "./kit.js";
import { ago, bytes, dateTime, passwordMessage, plural, shortDate } from "./format.js";
import { newInvite } from "./overview.js";

export async function renderUsers(main, ctx) {
  loading(main);
  let users;
  try {
    users = await adminApi.users();
  } catch (e) {
    failed(main, e, () => renderUsers(main, ctx));
    return;
  }
  if (ctx.stale()) return;
  const reload = () => renderUsers(main, ctx);

  const rows = users.map((user) => h("tr", {},
    h("th", { scope: "row" },
      h("div", { class: "who-cell" },
        h("strong", { text: user.display_name }),
        h("span", { class: "muted", text: `@${user.username}` }),
        h("span", { class: "row wrap tight" },
          user.is_admin ? pill("admin", "accent") : null, user.is_me ? pill("vous", "muted") : null,
          user.invite_note ? h("span", { class: "hint", text: `invité·e : « ${user.invite_note} »` }) : null))),
    h("td", { "data-label": "Recettes", text: String(user.recipes) }),
    h("td", { "data-label": "Photos", text: user.photos ? `${user.photos} (${bytes(user.photo_bytes)})` : "0" }),
    h("td", { "data-label": "Appareils", text: String(user.devices) }),
    h("td", { "data-label": "Vu", title: user.last_seen ? dateTime(user.last_seen) : "" , text: user.last_seen ? ago(user.last_seen) : "jamais" }),
    h("td", { "data-label": "Partages", text: `${user.shares_given} donnés · ${user.shares_received} reçus` }),
    h("td", { "data-label": "Depuis", text: shortDate(user.created_at) }),
    h("td", { class: "cell-actions" },
      h("button", { class: "btn btn-secondary btn-small", type: "button", onClick: () => manage(user, reload),
        "aria-label": `Gérer le compte ${user.username}` }, "Gérer"))));

  main.replaceChildren(
    pageHead("Comptes", `${plural(users.length, "compte")} sur ce serveur. Le contenu des recettes reste privé : seuls les nombres apparaissent ici.`,
      action("Inviter quelqu'un", () => newInvite(), { tone: "primary", iconName: "plus" })),
    h("div", { class: "card table-wrap" },
      h("table", { class: "data-table responsive" },
        h("thead", {}, h("tr", {}, ["Personne", "Recettes", "Photos", "Appareils", "Vu", "Partages", "Depuis", ""].map((t) =>
          h("th", { scope: "col", text: t })))),
        h("tbody", {}, rows))),
    h("p", { class: "hint", text: "« Vu » est mis à jour au plus une fois par heure, quand un appareil se synchronise." }));
}

function manage(user, reload) {
  let changed = false;
  openDialog({
    title: user.display_name,
    wide: true,
    onClose: () => { if (changed) reload(); },
    build: (close) => {
      const devices = h("ul", { class: "plain-list" }, h("li", { class: "muted", text: "Chargement…" }));
      const loadDevices = async () => {
        try {
          const list = await adminApi.sessions(user.username);
          devices.replaceChildren(...(list.length ? list.map((s) => h("li", { class: "list-row" },
            h("div", { class: "grow" },
              h("strong", { text: s.device }), s.current ? h("span", {}, " ", pill("cet appareil", "accent")) : null,
              h("div", { class: "hint", text: `connecté le ${dateTime(s.created_at)} · utilisé ${ago(s.last_used)}` })),
            action("Déconnecter", async () => {
              await adminApi.revokeSession(user.username, s.id);
              changed = true;
              toast("Appareil déconnecté.", "ok");
              loadDevices();
            }, { tone: "danger" })))
            : [h("li", { class: "muted", text: "Aucun appareil connecté." })]));
        } catch (e) {
          devices.replaceChildren(h("li", { class: "form-error", text: e.message }));
        }
      };
      loadDevices();

      const name = h("input", { type: "text", value: user.display_name, maxlength: 60 });
      const saveName = h("button", { class: "btn btn-secondary", type: "submit", text: "Renommer" });
      const nameForm = h("form", { class: "row wrap align-end" }, h("div", { class: "grow" }, field("Prénom affiché", name)), saveName);
      nameForm.addEventListener("submit", (event) => {
        event.preventDefault();
        busy(saveName, async () => {
          try {
            await adminApi.changeUser(user.username, { display_name: name.value });
            changed = true;
            toast("Prénom changé.", "ok");
          } catch (e) { toast(e.message, "error"); }
        });
      });

      const resetBtn = action("Nouveau mot de passe provisoire", async () => {
        const r = await withPassword({
          title: "Mot de passe oublié",
          message: `Un mot de passe provisoire remplace celui de « ${user.username} ». Tous ses appareils sont déconnectés.`,
          confirmText: "Créer le mot de passe",
          task: (pw) => adminApi.resetPassword(user.username, pw),
        });
        if (!r) return;
        changed = true;
        loadDevices();
        showSecret({ title: "Mot de passe provisoire", intro: `Pour « ${r.username} » :`, secret: r.password,
          message: passwordMessage(r.username, r.password, location.origin) });
      }, { iconName: "key" });

      const signOutAll = action("Déconnecter tous ses appareils", async () => {
        const ok = await confirmDialog({ title: "Déconnecter partout ?", confirmText: "Déconnecter", danger: true,
          message: `Chaque appareil de « ${user.username} » devra se reconnecter avec le mot de passe. Ses recettes ne sont pas touchées.` });
        if (!ok) return;
        const r = await adminApi.signOutEverywhere(user.username);
        changed = true;
        toast(`${plural(r.signed_out, "appareil")} déconnecté${r.signed_out > 1 ? "s" : ""}.`, "ok");
        loadDevices();
      }, { iconName: "logout" });

      const adminToggle = action(user.is_admin ? "Retirer le rôle admin" : "Rendre administrateur", async () => {
        const r = await withPassword({
          title: user.is_admin ? "Retirer le rôle admin" : "Rendre administrateur",
          message: user.is_admin ? `« ${user.username} » n'aura plus accès à ce tableau de bord.`
            : `« ${user.username} » pourra tout gérer ici, comme vous : comptes, invitations, sauvegardes.`,
          confirmText: "Confirmer",
          task: (pw) => adminApi.changeUser(user.username, { is_admin: !user.is_admin, admin_password: pw }),
        });
        if (r) { changed = true; close(); toast("Rôle changé.", "ok"); }
      });

      const deleteBtn = action("Supprimer le compte", async () => {
        const r = await withPassword({
          title: `Supprimer « ${user.username} » ?`,
          danger: true,
          message: h("div", { class: "stack-tight" },
            h("p", { text: `Le compte, ses ${plural(user.recipes, "recette")} et ses ${plural(user.photos, "photo")} seront effacés du serveur. `
              + "Les personnes avec qui il partageait ne les verront plus." }),
            h("p", { class: "form-error", text: "Impossible à annuler, sauf en restaurant une sauvegarde." })),
          confirmText: "Supprimer définitivement",
          typeToConfirm: user.username,
          task: (pw, typed) => adminApi.deleteUser(user.username, pw, typed),
        });
        if (r) {
          changed = true;
          close();
          toast(`Compte supprimé (${plural(r.recipes, "recette")}, ${plural(r.photos, "photo")}).`, "ok");
        }
      }, { tone: "danger", iconName: "trash" });

      return h("div", { class: "stack" },
        h("p", { class: "muted", text: `@${user.username} · ${plural(user.recipes, "recette")} · inscrit·e le ${shortDate(user.created_at)}` }),
        nameForm,
        h("h3", { class: "serif", text: "Appareils connectés" }), devices,
        h("h3", { class: "serif", text: "Actions" }),
        user.is_me
          ? h("p", { class: "hint", text: "C'est votre compte : changez votre mot de passe depuis « Mon compte » dans le carnet." })
          : h("div", { class: "row wrap" }, resetBtn, signOutAll),
        user.is_me ? null : h("div", { class: "danger-zone stack-tight" },
          h("h3", { class: "serif" }, icon("trash", 20), " Zone sensible"),
          h("div", { class: "row wrap" }, adminToggle, deleteBtn)));
    },
  });
}
