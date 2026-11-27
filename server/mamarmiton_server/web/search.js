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

/** filters: {text, category}; category is "" (all), "favoris", "partagees" or a dish type. */
export function matches(recipe, filters) {
  const cat = filters.category || "";
  if (cat === "favoris" && !recipe.favori) return false;
  if (cat === "partagees" && recipe.access === "owner") return false;
  if (DISH_TYPES.includes(cat) && recipe.type_plat !== cat) return false;
  const words = fold(filters.text).split(/\s+/).filter(Boolean);
  if (words.length) {
    const haystack = [fold(recipe.nom), ...recipe.ingredients.map((i) => fold(i.nom))].join(" ");
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
