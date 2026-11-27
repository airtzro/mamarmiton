// The shopping list ("Courses"): the logic is in shopping.js (port of shopping.py); this file
// keeps the list in localStorage (one list per user, removed at sign-out like the offline
// copy of the recipes) and draws the view.
import { servingsText } from "./quantities.js";
import { h, icon, clear } from "./dom.js";
import { getRecipe, state } from "./state.js";
import { confirmDialog, toast } from "./ui.js";
import { hit } from "./usage.js";
import {
  addExtra, addRecipe, buildList, groupByAisle, newState, prune, removeRecipe, servingsOf, setChecked, stateFromDict,
  stateToDict, toText, clearState, itemLabel,
} from "./shopping.js";

const keyOf = (username) => `mamarmiton:courses:${username}`;
let cache = null;   // {user, list}

function username() { return state.me && state.me.username; }

export function loadShopping(user = username()) {
  if (cache && cache.user === user) return cache.list;
  let list = newState();
  try {
    const raw = localStorage.getItem(keyOf(user));
    if (raw) list = stateFromDict(JSON.parse(raw));
  } catch { /* storage unavailable or damaged: start with an empty list */ }
  cache = { user, list };
  return list;
}

export function saveShopping() {
  if (!cache) return;
  try { localStorage.setItem(keyOf(cache.user), JSON.stringify(stateToDict(cache.list))); } catch { /* private mode: the list lives until the page closes */ }
}

/** Forget the list of this user on this device (sign-out). */
export function clearShopping(user) {
  if (cache && cache.user === user) cache = null;
  try { localStorage.removeItem(keyOf(user)); } catch { /* ignore */ }
}

export const servingsInList = (id) => servingsOf(loadShopping(), id);

export function addRecipeToShopping(recipe, servings) {
  addRecipe(loadShopping(), recipe.id, servings);
  saveShopping();
}

function selection(list) {
  return list.recipes.map((e) => ({ recipe: getRecipe(e.id), servings: e.servings })).filter((s) => s.recipe);
}

async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through to the old way */ }
  try {
    const area = h("textarea", { class: "sr-only", "aria-hidden": "true", tabindex: "-1" });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch { return false; }
}

export function renderCourses(main) {
  hit("shopping_open");
  const list = loadShopping();
  const listBox = h("div", { class: "stack courses-list" });
  const input = h("input", { type: "text", id: "extra-item", class: "grow", autocomplete: "off", placeholder: "Ajouter un article (papier absorbant…)", "aria-label": "Nouvel article" });
  const form = h("form", { class: "row courses-add", onSubmit: (e) => {
    e.preventDefault();
    if (addExtra(list, input.value)) { input.value = ""; saveShopping(); draw(); }
    input.focus();
  } }, input, h("button", { class: "btn btn-primary", type: "submit" }, icon("plus", 20), h("span", { text: "Ajouter" })));

  const currentText = () => {
    const items = buildList(selection(list));
    return toText(items, list.checked, list.extras);
  };
  const copyBtn = h("button", { class: "btn btn-secondary", type: "button", onClick: async () => {
    hit("shopping_copy_or_print");
    const ok = await copyText(currentText());
    toast(ok ? "Liste copiée." : "Impossible de copier la liste.", ok ? "ok" : "error");
  } }, icon("copy", 20), h("span", { class: "btn-label", text: "Copier" }));
  const shareBtn = h("button", { class: "btn btn-secondary", type: "button", onClick: async () => {
    hit("shopping_copy_or_print");
    const text = currentText();
    if (typeof navigator.share === "function") {
      try { await navigator.share({ title: "Liste de courses", text }); return; } catch (e) { if (e && e.name === "AbortError") return; }
    }
    toast((await copyText(text)) ? "Liste copiée." : "Impossible de partager la liste.", "info");
  } }, icon("share", 20), h("span", { class: "btn-label", text: "Partager" }));
  const clearBtn = h("button", { class: "btn btn-danger", type: "button", onClick: async () => {
    const ok = await confirmDialog({ title: "Vider la liste de courses ?", danger: true, confirmText: "Vider",
      message: "Les recettes, les articles ajoutés et les cases cochées seront effacés." });
    if (!ok) return;
    clearState(list); saveShopping(); draw();
  } }, icon("trash", 20), h("span", { class: "btn-label", text: "Vider" }));

  function recipeRow(entry) {
    const recipe = getRecipe(entry.id);
    const set = (n) => { addRecipe(list, entry.id, n); saveShopping(); draw(); };
    return h("li", { class: "list-row courses-recipe" },
      h("a", { class: "grow", href: `#/r/${entry.id}`, text: recipe ? recipe.nom : "Recette indisponible" }),
      h("div", { class: "stepper small", role: "group", "aria-label": `Quantité pour ${recipe ? recipe.nom : "cette recette"}` },
        h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Moins", disabled: entry.servings <= 1, onClick: () => set(entry.servings - 1) }, icon("minus")),
        h("output", { class: "stepper-value", text: servingsText(entry.servings, recipe?.pour_unite, true) }),
        h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Plus", disabled: entry.servings >= 99, onClick: () => set(entry.servings + 1) }, icon("plus"))),
      h("button", { class: "btn btn-icon btn-ghost", type: "button", "aria-label": `Retirer ${recipe ? recipe.nom : "la recette"} de la liste`,
        onClick: () => { removeRecipe(list, entry.id); saveShopping(); draw(); } }, icon("close")));
  }

  function checkRow(label, done, onToggle, extra = null, note = "") {
    const box = h("input", { type: "checkbox", checked: done, onChange: (e) => { row.classList.toggle("is-done", e.target.checked); onToggle(e.target.checked); } });
    const row = h("li", { class: `shop-row${done ? " is-done" : ""}` },
      h("label", {}, box, h("span", { class: "shop-text" }, h("span", { class: "shop-label", text: label }), note ? h("span", { class: "shop-note muted", text: note }) : null)),
      extra);
    return row;
  }

  function draw() {
    if (state.entries.size) {   // recipes deleted since: forget them (nothing to compare with before the first sync)
      const items0 = buildList(selection(list));
      prune(list, new Set(state.entries.keys()), new Set(items0.map((i) => i.key)));
    }
    const items = buildList(selection(list));
    const open = items.filter((i) => !list.checked.has(i.key)).length + list.extras.filter((e) => !e.done).length;
    const empty = !list.recipes.length && !list.extras.length;
    clear(listBox);
    if (empty) {
      listBox.append(h("div", { class: "empty card pad" },
        h("p", { class: "serif", text: "Votre liste de courses est vide." }),
        h("p", { class: "muted", text: "Ouvrez une recette et touchez « Ajouter aux courses » : les quantités sont additionnées et rangées par rayon." }),
        h("a", { class: "btn btn-primary", href: "#/" }, "Voir les recettes")));
    } else {
      if (list.recipes.length) {
        listBox.append(h("section", { class: "card pad" }, h("h2", { class: "serif", text: "Recettes" }),
          h("ul", { class: "plain-list" }, list.recipes.map(recipeRow))));
      }
      for (const [aisle, group] of groupByAisle(items)) {
        listBox.append(h("section", { class: "card pad" }, h("h2", { class: "serif", text: aisle }),
          h("ul", { class: "shopping" }, group.map((item) =>
            checkRow(itemLabel(item), list.checked.has(item.key), (on) => { setChecked(list, item.key, on); saveShopping(); }, null,
              item.recipes.join(", "))))));
      }
      if (list.extras.length) {
        listBox.append(h("section", { class: "card pad" }, h("h2", { class: "serif", text: "Autres articles" }),
          h("ul", { class: "shopping" }, list.extras.map((extra, i) =>
            checkRow(extra.text, extra.done, (on) => { extra.done = on; saveShopping(); },
              h("button", { class: "btn btn-icon btn-ghost", type: "button", "aria-label": `Retirer ${extra.text}`,
                onClick: () => { list.extras.splice(i, 1); saveShopping(); draw(); } }, icon("close", 20)))))));
      }
      if (!items.length && !list.extras.length) listBox.append(h("p", { class: "muted", text: "Les recettes choisies n'ont aucun ingrédient." }));
    }
    counter.textContent = empty ? "" : `${open} article${open > 1 ? "s" : ""} à acheter`;
    for (const b of [copyBtn, shareBtn, clearBtn]) b.disabled = empty;
  }

  const counter = h("p", { class: "muted", "aria-live": "polite" });
  document.title = "Courses · Mamarmiton";
  clear(main).append(h("div", { class: "container courses" },
    h("div", { class: "detail-bar" },
      h("a", { class: "btn btn-ghost", href: "#/" }, icon("back", 20), h("span", { text: "Toutes les recettes" })),
      h("div", { class: "actions" }, copyBtn, shareBtn, clearBtn)),
    h("h1", { class: "serif", text: "Courses" }), counter, form, listBox));
  draw();
  return { update: draw };
}
