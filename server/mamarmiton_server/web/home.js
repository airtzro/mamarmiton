import { h, icon, clear, append } from "./dom.js";
import { allRecipes, canEdit, displayName, photos, setFavorite, state } from "./state.js";
import { DISH_TYPES, SORTS, formatMinutes, search, tagCounts, totalTime } from "./search.js";
import { fold } from "./search.js";
import { toast } from "./ui.js";
import { hit } from "./usage.js";
import { openShareDialog } from "./share.js";
import { ALL, MINE, bookOptions, inBook, loadBook, saveBook, validBook } from "./books.js";

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
    hit("favorite_toggle");
    onDone();
  } catch (e) { toast(e.message, "error"); }
}

/** The first ingredient names, for the card: enough to tell two cakes apart. */
export function ingredientPreview(recipe, max = 6) {
  const names = recipe.ingredients.map((i) => String(i.nom || "").trim()).filter(Boolean);
  if (!names.length) return "";
  const shown = names.slice(0, max).join(", ");
  return names.length > max ? `${shown}…` : shown;
}

/** One recipe in the list: an index card with a dish-coloured edge, and the first photo beside it when there is one. */
function recipeCard(recipe, refresh) {
  const photo = recipe.images.length > 0;
  const star = h("button", {
    class: `fav-btn${recipe.favori ? " is-on" : ""}`, type: "button", "aria-pressed": String(recipe.favori),
    "aria-label": recipe.favori ? `Retirer ${recipe.nom} des favoris` : `Ajouter ${recipe.nom} aux favoris`,
    onClick: () => toggleFavorite(recipe, refresh),
  }, icon(recipe.favori ? "star-filled" : "star", 22));
  const time = totalTime(recipe);
  const preview = ingredientPreview(recipe);
  return h("article", { class: `card recipe-card ${dishClass(recipe.type_plat)}${photo ? " has-photo" : ""}` },
    h("div", { class: "rc-body" },
      h("h3", { class: "card-title serif" }, h("a", { href: `#/r/${recipe.id}`, text: recipe.nom })),
      preview ? h("p", { class: "rc-ings", text: preview }) : h("p", { class: "rc-ings is-empty", text: "Pas encore d'ingrédients" }),
      h("p", { class: "meta" },
        h("span", { class: "meta-item rc-dish" }, h("span", { class: "dish-dot", "aria-hidden": "true" }), recipe.type_plat),
        time ? h("span", { class: "meta-item" }, icon("clock", 16), formatMinutes(time)) : null,
        h("span", { class: "meta-item" }, icon("users", 16), `${recipe.pour} pers.`)),
      recipe.access !== "owner"
        ? h("p", { class: "owner" }, `De ${displayName(recipe.owner)}`, recipe.access === "read" ? ", en lecture seule" : ", modifiable")
        : null),
    photo ? h("div", { class: "rc-photo" }, coverImage(recipe, recipe.images[0].id)) : null,
    star);
}

/** The book switcher, pinned bottom-left: all books, mine, or one person's. `choose(id)` is called on a pick. */
function bookSwitcher(choose) {
  const button = h("button", { class: "book-switch-btn", type: "button", "aria-expanded": "false", "aria-controls": "book-menu" });
  const menu = h("div", { class: "book-menu", id: "book-menu", role: "group", "aria-label": "Choisir le carnet à afficher", hidden: true });
  const box = h("div", { class: "book-switch" }, menu, button);
  let options = [];
  let current = ALL;

  const open = (yes) => {
    menu.hidden = !yes;
    button.setAttribute("aria-expanded", String(yes));
    if (yes) (menu.querySelector('[aria-pressed="true"]') || menu.querySelector("button"))?.focus();
  };
  button.addEventListener("click", () => open(menu.hidden));
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { e.stopPropagation(); open(false); button.focus(); }
  });
  const outside = (e) => {
    if (!box.isConnected) { document.removeEventListener("click", outside, true); return; }  // the home view is gone
    if (!menu.hidden && !box.contains(e.target)) open(false);
  };
  document.addEventListener("click", outside, true);

  function paint(newOptions, book) {
    options = newOptions;
    current = book;
    const chosen = options.find((o) => o.id === current) || options[0];
    clear(button).append(icon("book", 22),
      h("span", { class: "book-switch-text" },
        h("span", { class: "book-switch-kicker", text: "Carnet affiché" }),
        h("span", { class: "book-switch-label", text: chosen.short })),
      h("span", { class: "book-switch-count", text: String(chosen.n), "aria-label": plural(chosen.n, "recette") }),
      icon("up", 18));
    button.setAttribute("aria-label", `Carnet affiché : ${chosen.label}, ${plural(chosen.n, "recette")}. Changer de carnet`);
    append(clear(menu), [
      h("p", { class: "book-menu-title", text: "Afficher les recettes de…" }),
      options.map((o, i) => [
        i === 2 ? h("hr", { class: "book-menu-sep" }) : null,
        h("button", { class: `book-option${o.id === current ? " is-current" : ""}`, type: "button", "aria-pressed": String(o.id === current),
          onClick: () => { open(false); button.focus(); choose(o.id); } },
          icon(o.id === ALL ? "users" : o.id === MINE ? "user" : "book", 20),
          h("span", { class: "grow", text: o.label }),
          h("span", { class: "book-option-count", text: String(o.n) }),
          o.id === current ? icon("check", 18) : null)])]);
  }
  return { el: box, paint };
}

/** The recipe list. Returns {update} to redraw after a sync without losing the search text. */
export function renderHome(main, { onNew }) {
  document.title = "Mamarmiton";
  const f = state.filters;
  const count = h("p", { class: "count", "aria-live": "polite" });
  const title = h("h1", { class: "serif" });
  const searchInput = h("input", { type: "search", id: "q", placeholder: "Rechercher une recette ou un ingrédient…",
    value: f.text, autocomplete: "off", enterkeyhint: "search" });
  const sortSelect = h("select", { id: "sort" }, SORTS.map((s) => h("option", { value: s, text: s, selected: s === f.sort })));
  const chips = h("div", { class: "filter-group", role: "group", "aria-label": "Catégories" });
  const tagRow = h("div", { class: "filter-group tag-chips", role: "group", "aria-label": "Étiquettes" });
  const list = h("div", { class: "grid", id: "recipe-list" });
  const empty = h("div", { class: "empty" });
  if (f.book === null || f.book === undefined) f.book = state.me ? loadBook(localStorage, state.me.username) : ALL;
  const switcher = bookSwitcher((id) => {
    f.book = id;
    if (state.me) saveBook(localStorage, state.me.username, id);
    draw();
    window.scrollTo(0, 0);
  });

  const resultsBar = h("div", { class: "results-bar" }, count,
    h("div", { class: "sort-box" }, h("label", { for: "sort", text: "Trier par" }), sortSelect));
  const shareBtn = h("button", { class: "btn btn-secondary share-book", type: "button", "aria-label": "Partager mon carnet",
    onClick: () => openShareDialog({ recipe: null }) }, icon("share", 20), h("span", { class: "btn-label", text: "Partager mon carnet" }));

  clear(main).append(
    h("div", { class: "container has-book-switch" },
      h("header", { class: "page-head" }, title, shareBtn),
      h("div", { class: "search-box" }, icon("search", 22),
        h("label", { class: "sr-only", for: "q", text: "Rechercher" }), searchInput),
      h("div", { class: "filters" }, chips, tagRow),
      resultsBar, list, empty),
    switcher.el);

  function draw() {
    const everything = allRecipes();
    const options = bookOptions(everything, displayName);
    f.book = validBook(f.book, options);  // a share may have been revoked since
    if (f.category === "partagees") f.category = "";  // replaced by the book switcher
    const book = options.find((o) => o.id === f.book);
    const all = everything.filter((r) => inBook(r, f.book));
    switcher.el.hidden = options.length <= 2;  // nobody shares with me: "all" and "mine" are the same list
    switcher.paint(options, f.book);
    shareBtn.hidden = f.book !== ALL && f.book !== MINE;
    clear(chips).append(...CATEGORIES.map(([value, label]) => h("button", {
      class: `chip-btn${f.category === value ? " is-on" : ""}`, type: "button", "aria-pressed": String(f.category === value),
      onClick: () => { f.category = value; draw(); }, text: label,
    })));

    const counts = tagCounts(all);
    f.tags = (f.tags || []).filter((t) => counts.some((c) => fold(c.tag) === fold(t)));  // a deleted tag cannot stay selected
    clear(tagRow);
    tagRow.hidden = !counts.length;
    tagRow.append(...counts.map(({ tag, n }) => {
      const on = f.tags.some((t) => fold(t) === fold(tag));
      return h("button", {
        class: `chip-btn chip-tag${on ? " is-on" : ""}`, type: "button", "aria-pressed": String(on),
        onClick: () => { f.tags = on ? f.tags.filter((t) => fold(t) !== fold(tag)) : [...f.tags, tag]; if (!on) hit("tag_filter"); draw(); },
      }, `#${tag}`, h("span", { class: "chip-count", text: String(n) }));
    }));

    const shown = search(all, f, f.sort);
    const filtered = !!(f.text.trim() || f.category || f.tags.length);
    const name = state.me ? (state.me.display_name || state.me.username) : "";
    if (!filtered) {
      if (f.book === ALL) {
        title.replaceChildren(h("span", { class: "greet", text: `${greeting()}${name ? ` ${name}` : ""},` }), "qu'est-ce qu'on cuisine ?");
      } else title.textContent = f.book === MINE ? "Mon carnet" : `Le carnet de ${book.short}`;
    } else {
      title.textContent = f.category === "favoris" ? "Mes favoris" : DISH_TYPES.includes(f.category) ? `${f.category}s` : "Résultats";
    }
    document.title = f.book === ALL ? "Mamarmiton" : `${book.label} · Mamarmiton`;
    count.textContent = !all.length ? "" : filtered ? `${plural(shown.length, "recette")} sur ${all.length}` : plural(shown.length, "recette");
    resultsBar.hidden = !all.length;

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
          h("p", { class: "muted", text: "Essayez un autre mot, ou changez de catégorie ou d'étiquette." }),
          h("button", { class: "btn btn-secondary", type: "button",
            onClick: () => { f.text = ""; f.category = ""; f.tags = []; searchInput.value = ""; draw(); }, text: "Effacer la recherche" }));
      }
    }
  }

  let timer;
  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { f.text = searchInput.value; if (f.text.trim()) hit("search"); draw(); }, 120);
  });
  sortSelect.addEventListener("change", () => { f.sort = sortSelect.value; draw(); });
  draw();
  return { update: draw };
}
