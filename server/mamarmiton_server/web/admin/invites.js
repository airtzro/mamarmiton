import { h } from "../dom.js";
import { busy, toast } from "../ui.js";
import { adminApi, action, failed, field, loading, pageHead, pill } from "./kit.js";
import { INVITE_STATUS, ago, dateTime } from "./format.js";
import { newInvite } from "./overview.js";

export async function renderInvites(main, ctx) {
  loading(main);
  let invites;
  try {
    invites = await adminApi.invites();
  } catch (e) {
    failed(main, e, () => renderInvites(main, ctx));
    return;
  }
  if (ctx.stale()) return;
  const reload = () => renderInvites(main, ctx);

  const note = h("input", { type: "text", maxlength: 100, placeholder: "ex. pour tante Marie" });
  const create = h("button", { class: "btn btn-primary", type: "submit", text: "Créer le code" });
  const form = h("form", { class: "card pad invite-form" },
    h("div", { class: "grow" }, field("Pour qui ? (note pour vous, facultatif)", note,
      "La personne crée elle-même son nom d'utilisateur et son mot de passe avec ce code.")),
    create);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(create, async () => {
      try {
        await newInvite(note.value.trim());
        note.value = "";
        reload();
      } catch (e) { toast(e.message, "error"); }
    });
  });

  const expired = invites.filter((i) => i.status === "expired").length;
  const rows = invites.map((i) => {
    const status = INVITE_STATUS[i.status] || { label: i.status, tone: "muted" };
    let detail = "";
    if (i.status === "used") detail = `par ${i.used_by_name || i.used_by} (@${i.used_by})`;
    else if (i.status === "open") detail = `expire ${ago(i.expires_at)}`;
    else detail = `le ${dateTime(i.expires_at)}`;
    return h("tr", {},
      h("th", { scope: "row", text: i.note || "—" }),
      h("td", { "data-label": "État" }, pill(status.label, status.tone), " ", h("span", { class: "hint", text: detail })),
      h("td", { "data-label": "Créée", title: dateTime(i.created_at), text: ago(i.created_at) }),
      h("td", { class: "cell-actions" }, i.status === "open"
        ? action("Annuler", async () => {
          await adminApi.revokeInvite(i.id);
          toast("Invitation annulée : le code ne marche plus.", "ok");
          reload();
        }, { tone: "danger" }) : null));
  });

  main.replaceChildren(
    pageHead("Invitations", "Sans code, personne ne peut créer de compte. Chaque code sert une seule fois.",
      expired ? action(`Effacer les ${expired} expirées`, async () => {
        const r = await adminApi.cleanupInvites();
        toast(`${r.deleted} invitation(s) effacée(s).`, "ok");
        reload();
      }) : null),
    form,
    invites.length
      ? h("div", { class: "card table-wrap" }, h("table", { class: "data-table responsive" },
        h("thead", {}, h("tr", {}, ["Note", "État", "Créée", ""].map((t) => h("th", { scope: "col", text: t })))),
        h("tbody", {}, rows)))
      : h("p", { class: "muted", text: "Aucune invitation pour l'instant." }),
    h("p", { class: "hint", text: "Les codes eux-mêmes ne sont jamais réaffichés : le serveur n'en garde qu'une empreinte. "
      + "Un code perdu ? Annulez-le et créez-en un autre." }));
}
