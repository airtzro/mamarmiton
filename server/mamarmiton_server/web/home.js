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
  const count = h("p", { class: "count muted" });
  const eyebrow = h("p", { class: "eyebrow" });
  const title = h("h1", { class: "serif" });
  const searchInput = h("input", { type: "search", id: "q", placeholder: "Rechercher une recette ou un ingrédient…",
    value: f.text, autocomplete: "off", enterkeyhint: "search" });
  const sortSelect = h("select", { id: "sort" }, SORTS.map((s) => h("option", { value: s, text: `Tri : ${s[0].toLowerCase()}${s.slice(1)}`, selected: s === f.sort })));
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Catégories" });
  const tagRow = h("div", { class: "chips tag-chips", role: "group", "aria-label": "Étiquettes" });
  const list = h("div", { class: "grid", id: "recipe-list" });
  const empty = h("div", { class: "empty" });
  if (f.book === null || f.book === undefined) f.book = state.me ? loadBook(localStorage, state.me.username) : ALL;
  const switcher = bookSwitcher((id) => {
    f.book = id;
    if (state.me) saveBook(localStorage, state.me.username, id);
    draw();
    window.scrollTo(0, 0);
  });

  const shareBtn = h("button", { class: "btn btn-secondary", type: "button", onClick: () => openShareDialog({ recipe: null }) },
    icon("share", 18), h("span", { text: "Partager mon carnet" }));

  clear(main).append(
    h("div", { class: "container has-book-switch" },
      h("header", { class: "page-head" },
        h("div", {}, eyebrow, title, count),
        h("div", { class: "page-actions" }, shareBtn)),
      h("div", { class: "toolbar" },
        h("div", { class: "search-box" }, icon("search", 20),
          h("label", { class: "sr-only", for: "q", text: "Rechercher" }), searchInput),
        h("div", { class: "sort-box" }, h("label", { class: "sr-only", for: "sort", text: "Trier" }), sortSelect)),
      chips, tagRow, list, empty),
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
      class: `chip chip-btn${f.category === value ? " is-on" : ""}`, type: "button", "aria-pressed": String(f.category === value),
      onClick: () => { f.category = value; draw(); }, text: label,
    })));

    const counts = tagCounts(all);
    f.tags = (f.tags || []).filter((t) => counts.some((c) => fold(c.tag) === fold(t)));  // a deleted tag cannot stay selected
    clear(tagRow);
    tagRow.hidden = !counts.length;
    tagRow.append(...counts.map(({ tag, n }) => {
      const on = f.tags.some((t) => fold(t) === fold(tag));
      return h("button", {
        class: `chip chip-btn chip-tag${on ? " is-on" : ""}`, type: "button", "aria-pressed": String(on),
        onClick: () => { f.tags = on ? f.tags.filter((t) => fold(t) !== fold(tag)) : [...f.tags, tag]; if (!on) hit("tag_filter"); draw(); },
      }, `#${tag}`, h("span", { class: "chip-count", text: ` ${n}` }));
    }));

    const shown = search(all, f, f.sort);
    const filtered = !!(f.text.trim() || f.category || f.tags.length);
    if (!filtered) {
      eyebrow.textContent = `${greeting()} !`.toUpperCase();
      title.textContent = f.book === ALL ? "Qu'est-ce qu'on cuisine ?" : f.book === MINE ? "Mon carnet" : `Le carnet de ${book.short}`;
    } else {
      eyebrow.textContent = (f.book === ALL ? "Votre carnet" : book.label).toUpperCase();
      title.textContent = f.category === "favoris" ? "Mes favoris" : DISH_TYPES.includes(f.category) ? `${f.category}s` : "Résultats";
    }
    document.title = f.book === ALL ? "Mamarmiton" : `${book.label} · Mamarmiton`;
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
