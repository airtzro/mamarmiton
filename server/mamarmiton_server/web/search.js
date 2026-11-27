// Port of mamarmiton/search.py: accent-insensitive search on name + ingredients.

export const DISH_TYPES = ["Entrée", "Plat", "Dessert", "Pain"];
export const COOKING_TYPES = ["Four", "Poêle", "Casserole", "Vapeur", "Micro-ondes", "Sans cuisson"];
export const SORTS = ["Favoris d'abord", "Nom (A → Z)", "Plus rapides", "Plus récentes"];

export function fold(text) {
  return String(text ?? "").normalize("NFD").replace(/\p{Mn}/gu, "").toLowerCase().trim();
}

export function totalTime(recipe) {
  return (recipe.temps || 0) + (recipe.temps_cuisson || 0);
}

/** "1 h 05" / "45 min" (same as models.format_minutes). */
export function formatMinutes(minutes) {
  minutes = Math.max(0, Math.trunc(minutes || 0));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min`;
  if (rest === 0) return `${hours} h`;
  return `${hours} h ${String(rest).padStart(2, "0")}`;
}

export const MAX_TAGS = 20, MAX_TAG_LEN = 30;

/** Same rules as models.normalize_tags: trimmed, single spaces, no duplicates (case/accents), bounded. */
export function normalizeTags(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    if (typeof item !== "string") continue;
    const tag = item.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_TAG_LEN).trim();
    const key = fold(tag);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
    if (out.length >= MAX_TAGS) break;
  }
  return out;
}

/** Every tag of the book with its recipe count: most used first, then A to Z. */
export function tagCounts(recipes) {
  const counts = new Map();
  for (const r of recipes) {
    for (const tag of r.tags || []) {
      const key = fold(tag);
      const known = counts.get(key);
      if (known) known.n += 1; else counts.set(key, { tag, n: 1 });
    }
  }
  return [...counts.values()].sort((a, b) => b.n - a.n || (fold(a.tag) < fold(b.tag) ? -1 : 1));
}

/** filters: {text, category, tags}; category is "" (all), "favoris", "partagees" or a dish type. */
export function matches(recipe, filters) {
  const cat = filters.category || "";
  if (cat === "favoris" && !recipe.favori) return false;
  if (cat === "partagees" && recipe.access === "owner") return false;
  if (DISH_TYPES.includes(cat) && recipe.type_plat !== cat) return false;
  if (filters.tags?.length) {
    const have = new Set((recipe.tags || []).map(fold));
    if (!filters.tags.every((t) => have.has(fold(t)))) return false;
  }
  const words = fold(filters.text).split(/\s+/).filter(Boolean);
  if (words.length) {
    const haystack = [fold(recipe.nom), ...recipe.ingredients.map((i) => fold(i.nom)), ...(recipe.tags || []).map(fold)].join(" ");
    if (!words.every((w) => haystack.includes(w))) return false;
  }
  return true;
}

const byName = (a, b) => {
  const x = fold(a.nom), y = fold(b.nom);
  return x < y ? -1 : x > y ? 1 : 0;
};

export function sortRecipes(recipes, how = SORTS[0]) {
  const list = [...recipes].sort(byName);  // stable: ties keep the name order
  if (how === "Nom (A → Z)") return list;
  if (how === "Plus rapides") return list.sort((a, b) => totalTime(a) - totalTime(b));
  if (how === "Plus récentes") return list.sort((a, b) => (a.cree_le < b.cree_le ? 1 : a.cree_le > b.cree_le ? -1 : 0));
  return list.sort((a, b) => Number(!!b.favori) - Number(!!a.favori));
}

export function search(recipes, filters, how = SORTS[0]) {
  return sortRecipes(recipes.filter((r) => matches(r, filters)), how);
}
