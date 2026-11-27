import { h, icon, clear } from "./dom.js";
import { allRecipes, canEdit, displayName, photos, setFavorite, state } from "./state.js";
import { DISH_TYPES, SORTS, formatMinutes, search, totalTime } from "./search.js";
import { toast } from "./ui.js";
import { openShareDialog } from "./share.js";

const CATEGORIES = [
  ["", "Toutes"], ["favoris", "Favoris"], ["Entrée", "Entrée"], ["Plat", "Plat"], ["Dessert", "Dessert"], ["Pain", "Pain"],
];
const DISH_CLASS = { "Entrée": "dish-entree", Plat: "dish-plat", Dessert: "dish-dessert", Pain: "dish-pain" };
export const dishClass = (type) => DISH_CLASS[type] || "dish-other";

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 || hour >= 18 ? "Bonsoir" : "Bonjour";
}

const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

// covers are fetched only when they scroll into view
const lazy = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { lazy.unobserve(e.target); e.target.load(); }
  }, { rootMargin: "300px" })
  : null;

export function coverImage(recipe, imageId, alt = "") {
  const img = h("img", { alt, decoding: "async", class: "cover-img" });
  img.load = () => photos.get(recipe.id, imageId).then((url) => { img.src = url; }, () => {});
  if (lazy) lazy.observe(img); else img.load();
  return img;
}

export function cover(recipe, size = "card") {
  const box = h("div", { class: `cover ${dishClass(recipe.type_plat)} cover-${size}` },
    h("span", { class: "cover-letter serif", "aria-hidden": "true", text: recipe.nom.trim().charAt(0).toUpperCase() }));
  if (recipe.images.length) box.append(coverImage(recipe, recipe.images[0].id));
  return box;
}

export async function toggleFavorite(recipe, onDone) {
  const on = !recipe.favori;
  try {
    await setFavorite(recipe.id, on);
    onDone();
  } catch (e) { toast(e.message, "error"); }
}

function recipeCard(recipe, refresh) {
  const star = h("button", {
    class: `fav-btn${recipe.favori ? " is-on" : ""}`, type: "button", "aria-pressed": String(recipe.favori),
    "aria-label": recipe.favori ? `Retirer ${recipe.nom} des favoris` : `Ajouter ${recipe.nom} aux favoris`,
    onClick: () => toggleFavorite(recipe, refresh),
  }, icon(recipe.favori ? "star-filled" : "star", 22));
  const time = totalTime(recipe);
  return h("article", { class: "card recipe-card" },
    cover(recipe),
    star,
    h("div", { class: "card-body" },
      h("h3", { class: "card-title serif" }, h("a", { href: `#/r/${recipe.id}`, text: recipe.nom })),
      h("p", { class: "meta" },
        h("span", { class: "meta-item" }, icon("clock", 16), time ? formatMinutes(time) : "—"),
        h("span", { class: "meta-item" }, icon("users", 16), `${recipe.pour} pers.`),
        h("span", { class: `chip chip-dish ${dishClass(recipe.type_plat)}`, text: recipe.type_plat })),
      recipe.access !== "owner"
        ? h("p", { class: "owner" }, `De ${displayName(recipe.owner)}`, recipe.access === "read" ? " · lecture seule" : " · modifiable")
        : null));
}

/** The recipe list. Returns {update} to redraw after a sync without losing the search text. */
export function renderHome(main, { onNew }) {
  document.title = "Mamarmiton";
  const f = state.filters;
  const count = h("p", { class: "count muted" });
  const eyebrow = h("p", { class: "eyebrow" });
  const title = h("h1", { class: "serif" });
  const searchInput = h("input", { type: "search", id: "q", placeholder: "Rechercher une recette ou un ingrédient…",
    value: f.text, autocomplete: "off", enterkeyhint: "search" });
  const sortSelect = h("select", { id: "sort" }, SORTS.map((s) => h("option", { value: s, text: `Tri : ${s[0].toLowerCase()}${s.slice(1)}`, selected: s === f.sort })));
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Catégories" });
  const list = h("div", { class: "grid", id: "recipe-list" });
  const empty = h("div", { class: "empty" });

  const shareBtn = h("button", { class: "btn btn-secondary", type: "button", onClick: () => openShareDialog({ recipe: null }) },
    icon("share", 18), h("span", { text: "Partager mon carnet" }));

  clear(main).append(
    h("div", { class: "container" },
      h("header", { class: "page-head" },
        h("div", {}, eyebrow, title, count),
        h("div", { class: "page-actions" }, shareBtn)),
      h("div", { class: "toolbar" },
        h("div", { class: "search-box" }, icon("search", 20),
          h("label", { class: "sr-only", for: "q", text: "Rechercher" }), searchInput),
        h("div", { class: "sort-box" }, h("label", { class: "sr-only", for: "sort", text: "Trier" }), sortSelect)),
      chips, list, empty));

  function draw() {
    const all = allRecipes();
    const hasShared = all.some((r) => r.access !== "owner");
    if (f.category === "partagees" && !hasShared) f.category = "";
    const cats = hasShared ? [...CATEGORIES, ["partagees", "Partagées avec moi"]] : CATEGORIES;
    clear(chips).append(...cats.map(([value, label]) => h("button", {
      class: `chip chip-btn${f.category === value ? " is-on" : ""}`, type: "button", "aria-pressed": String(f.category === value),
      onClick: () => { f.category = value; draw(); }, text: label,
    })));

    const shown = search(all, f, f.sort);
    const filtered = !!(f.text.trim() || f.category);
    if (!filtered) {
      eyebrow.textContent = `${greeting()} !`.toUpperCase();
      title.textContent = "Qu'est-ce qu'on cuisine ?";
    } else {
      eyebrow.textContent = "VOTRE CARNET";
      title.textContent = f.category === "favoris" ? "Mes favoris" : DISH_TYPES.includes(f.category) ? `${f.category}s`
        : f.category === "partagees" ? "Partagées avec moi" : "Résultats";
    }
    count.textContent = all.length ? plural(shown.length, "recette") : "";

    clear(list).append(...shown.map((r) => recipeCard(r, draw)));
    clear(empty);
    if (!shown.length) {
      if (!all.length) {
        empty.append(h("h2", { class: "serif", text: "Votre carnet est encore vide" }),
          h("p", { class: "muted", text: "Ajoutez votre première recette : tapez-la, ou prenez-la en photo." }),
          h("button", { class: "btn btn-primary btn-big", type: "button", onClick: onNew }, icon("plus", 20), "Nouvelle recette"));
      } else if (f.category === "favoris" && !f.text.trim()) {
        empty.append(h("h2", { class: "serif", text: "Pas encore de favori" }),
          h("p", { class: "muted", text: "Touchez l'étoile d'une recette pour la retrouver ici." }));
      } else {
        empty.append(h("h2", { class: "serif", text: "Aucune recette ne correspond" }),
          h("p", { class: "muted", text: "Essayez un autre mot, ou changez de catégorie." }),
          h("button", { class: "btn btn-secondary", type: "button",
            onClick: () => { f.text = ""; f.category = ""; searchInput.value = ""; draw(); }, text: "Effacer la recherche" }));
      }
    }
  }

  let timer;
  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { f.text = searchInput.value; draw(); }, 120);
  });
  sortSelect.addEventListener("change", () => { f.sort = sortSelect.value; draw(); });
  draw();
  return { update: draw };
}
