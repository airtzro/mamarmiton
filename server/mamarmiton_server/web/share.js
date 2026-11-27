import { api } from "./api.js";
import { h, clear } from "./dom.js";
import { hit } from "./usage.js";
import { displayName, state, sync } from "./state.js";
import { busy, confirmDialog, openDialog, toast } from "./ui.js";

// what a share lets the other person do: read only, read and make their own variant, or edit
const MODES = [["read", "Lecture seule"], ["copy", "Lecture + variantes"], ["edit", "Modification"]];
const HINTS = {
  read: "Elle pourra lire, sans modifier ni faire sa propre version.",
  copy: "Elle pourra lire et créer sa propre variante. Votre nom restera sur la variante.",
  edit: "Elle pourra modifier directement, et créer des variantes.",
};
// older servers send no can_copy: variants were always allowed then
const levelOf = (s) => (s.can_edit ? "edit" : s.can_copy === false ? "read" : "copy");
const modeLabel = (s) => MODES.find(([v]) => v === levelOf(s))[1];
const rights = (level) => [level === "edit", level !== "read"];  // [can_edit, can_copy]

/**
 * Share dialog. `recipe` = a recipe I own (share just that one) or null (share my whole book,
 * and manage the books other people shared with me).
 */
export function openShareDialog({ recipe }) {
  const isBook = !recipe;
  openDialog({
    title: isBook ? "Partager mon carnet" : `Partager « ${recipe.nom} »`, wide: true,
    build: () => {
      const body = h("div", { class: "stack" });
      let current = null;  // result of GET /shares
      const status = h("p", { class: "form-error", role: "alert" });

      async function act(button, task, okMessage) {
        status.textContent = "";
        await busy(button, async () => {
          try { await task(); if (okMessage) toast(okMessage, "ok"); await load(); } catch (e) { status.textContent = e.message; }
        });
      }

      function modeSelect(value, onChange, label) {
        return h("select", { "aria-label": label, onChange: (e) => onChange(e.target.value) },
          MODES.map(([v, text]) => h("option", { value: v, text, selected: v === value })));
      }

      function draw() {
        const given = isBook
          ? current.book_shared_with
          : current.recipes_shared.filter((s) => s.recipe_id === recipe.id);
        const grantees = new Set(given.map((s) => s.username));
        const bookTarget = new Set(current.book_shared_with.map((s) => s.username));

        const intro = isBook
          ? "Toutes vos recettes seront visibles par les personnes choisies, y compris celles que vous ajouterez plus tard."
          : "Seule cette recette sera visible par les personnes choisies.";
        const setMode = (username, level, btn) => act(btn, () => (isBook
          ? api.shareBook(username, ...rights(level)) : api.shareRecipe(recipe.id, username, ...rights(level))));
        const stop = (username, btn) => act(btn, () => (isBook
          ? api.unshareBook(username) : api.unshareRecipe(recipe.id, username)), "Partage retiré.");

        const list = given.length
          ? h("ul", { class: "plain-list" }, given.map((s) => {
            const del = h("button", { class: "btn btn-danger btn-small", type: "button", text: "Retirer",
              "aria-label": `Ne plus partager avec ${s.display_name || s.username}` });
            del.addEventListener("click", () => stop(s.username, del));
            return h("li", { class: "list-row" },
              h("span", { class: "grow" }, h("strong", { text: s.display_name || s.username }), h("br"),
                h("span", { class: "muted", text: `@${s.username}` })),
              modeSelect(levelOf(s), (level) => setMode(s.username, level, del), `Droits de ${s.display_name || s.username}`),
              del);
          }))
          : h("p", { class: "muted", text: isBook ? "Votre carnet n'est partagé avec personne." : "Cette recette n'est partagée avec personne." });

        const candidates = state.users.filter((u) => !grantees.has(u.username));
        const who = h("select", { id: "share-who" }, candidates.map((u) => h("option", { value: u.username, text: u.display_name || u.username })));
        const hint = h("p", { class: "hint", id: "share-how-hint", text: HINTS.read });
        const how = h("select", { id: "share-how", "aria-label": "Droits", "aria-describedby": "share-how-hint",
          onChange: () => { hint.textContent = HINTS[how.value]; } }, MODES.map(([v, t]) => h("option", { value: v, text: t })));
        const add = h("button", { class: "btn btn-primary", type: "button", text: "Partager" });
        add.addEventListener("click", () => {
          const username = who.value;
          if (username) act(add, async () => {
            await (isBook ? api.shareBook(username, ...rights(how.value)) : api.shareRecipe(recipe.id, username, ...rights(how.value)));
            hit("share_create");
          }, "Partagé.");
        });
        const form = candidates.length
          ? h("div", { class: "share-form" },
            h("div", { class: "field" }, h("label", { for: "share-who", text: "Avec qui ?" }), who),
            h("div", { class: "field" }, h("label", { for: "share-how", text: "Droits" }), how), add, hint)
          : h("p", { class: "muted", text: state.users.length ? "Tout le monde a déjà accès." :
            "Personne d'autre n'a encore de compte. Demandez un code d'invitation à la personne qui gère le carnet." });

        clear(body).append(
          h("p", { text: intro }),
          h("h3", { class: "serif", text: "Déjà partagé avec" }), list,
          h("h3", { class: "serif", text: "Ajouter quelqu'un" }), form,
          status);

        if (isBook) {
          const received = current.books_shared_with_me;
          body.append(h("h3", { class: "serif", text: "Carnets partagés avec moi" }),
            received.length
              ? h("ul", { class: "plain-list" }, received.map((s) => {
                const leave = h("button", { class: "btn btn-danger btn-small", type: "button", text: "Ne plus voir ce carnet" });
                leave.addEventListener("click", async () => {
                  const ok = await confirmDialog({ title: "Quitter ce carnet ?", confirmText: "Ne plus le voir", danger: true,
                    message: `Les recettes de ${s.display_name || s.username} disparaîtront de votre liste. Elles pourront vous les partager de nouveau.` });
                  if (ok) act(leave, async () => { await api.leaveBook(s.username); await sync(); window.dispatchEvent(new Event("mamarmiton:data")); });
                });
                return h("li", { class: "list-row" },
                  h("span", { class: "grow" }, h("strong", { text: s.display_name || s.username }), h("br"),
                    h("span", { class: "muted", text: modeLabel(s) })), leave);
              }))
              : h("p", { class: "muted", text: "Personne ne vous a partagé son carnet." }));

          const hidden = current.hidden || [];
          if (hidden.length) {
            body.append(h("h3", { class: "serif", text: "Recettes retirées de ma liste" }),
              h("ul", { class: "plain-list" }, hidden.map((x) => {
                const back = h("button", { class: "btn btn-secondary btn-small", type: "button", text: "Remettre" });
                back.addEventListener("click", () => act(back, async () => {
                  await api.setHidden(x.recipe_id, false);
                  await sync();
                  window.dispatchEvent(new Event("mamarmiton:data"));
                }, "Recette remise dans votre liste."));
                return h("li", { class: "list-row" },
                  h("span", { class: "grow" }, h("strong", { text: x.nom || "Recette sans nom" }), h("br"),
                    h("span", { class: "muted", text: `Carnet de ${x.display_name || x.username}` })), back);
              })));
          }
        }
        void bookTarget;
      }

      async function load() {
        try {
          const [shares, users] = await Promise.all([api.shares(), api.users()]);
          state.users = users;
          current = shares;
          draw();
        } catch (e) {
          clear(body).append(h("p", { class: "form-error", text: e.message }));
        }
      }
      body.append(h("p", { class: "muted", text: "Chargement…" }));
      load();
      return body;
    },
  });
}
