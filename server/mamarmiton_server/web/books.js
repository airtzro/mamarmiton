// Which recipe book the home page shows: everything, my own book, or one person's book.
// Pure functions (no DOM, no state import) so they are tested with node --test.

export const ALL = "";
export const MINE = ":mine";  // ":" never appears in a username, so no one can be called this

/** Does `recipe` belong to `book` ("" = every book, MINE = mine, otherwise an owner's username)? */
export function inBook(recipe, book) {
  if (!book) return true;
  if (book === MINE) return recipe.access === "owner";
  return recipe.access !== "owner" && recipe.owner === book;
}

/**
 * The choices for the switcher, with recipe counts: all, mine, then one per person who shares
 * recipes with me (sorted by display name). `nameOf(username)` gives the display name.
 */
export function bookOptions(recipes, nameOf = (u) => u) {
  const owners = new Map();
  let mine = 0;
  for (const r of recipes) {
    if (r.access === "owner") mine++;
    else owners.set(r.owner, (owners.get(r.owner) || 0) + 1);
  }
  const people = [...owners.entries()]
    .map(([owner, n]) => ({ id: owner, label: `Carnet de ${nameOf(owner) || owner}`, short: nameOf(owner) || owner, n }))
    .sort((a, b) => a.short.localeCompare(b.short, "fr", { sensitivity: "base" }));
  return [
    { id: ALL, label: "Tous les carnets", short: "Tous les carnets", n: recipes.length },
    { id: MINE, label: "Mon carnet", short: "Mon carnet", n: mine },
    ...people,
  ];
}

/** `book` if it is still one of the options (a share can be revoked), else "all". */
export function validBook(book, options) {
  return options.some((o) => o.id === book) ? book : ALL;
}

const KEY = "mamarmiton.book.";

export function loadBook(storage, username) {
  try { return storage.getItem(KEY + username) || ALL; } catch { return ALL; }
}

export function saveBook(storage, username, book) {
  try {
    if (book) storage.setItem(KEY + username, book);
    else storage.removeItem(KEY + username);
  } catch { /* private mode */ }
}
