// The client-side copy of the recipe book, refreshed through GET /recipes?since=.
import { api } from "./api.js";

export const state = {
  me: null,                 // {username, display_name, is_admin}
  users: [],                // other accounts [{username, display_name}]
  entries: new Map(),       // id -> server entry {id, recipe, version, owner, access, images}
  favorites: new Set(),
  seq: 0,
  filters: { text: "", category: "", sort: "Favoris d'abord" },
};

export function resetState() {
  state.me = null;
  state.users = [];
  state.entries = new Map();
  state.favorites = new Set();
  state.seq = 0;
  state.filters = { text: "", category: "", sort: "Favoris d'abord" };
  photos.clear();
}

/** The flat recipe the views work with (desktop-like fields + access, owner, version). */
export function recipeOf(entry) {
  const r = entry.recipe || {};
  return {
    id: entry.id,
    nom: r.nom || "Recette sans nom",
    temps: r.temps || 0,
    temps_cuisson: r.temps_cuisson || 0,
    pour: r.pour || 4,
    type_plat: r.type_plat || "Plat",
    type_cuisson: r.type_cuisson || "Sans cuisson",
    ingredients: Array.isArray(r.ingredients) ? r.ingredients.map((i) => ({ nom: i.nom || "", quantite: i["quantité"] || "" })) : [],
    etapes: Array.isArray(r.etapes) ? r.etapes : [],
    notes: r.notes || "",
    source: r.source || "",
    cree_le: r.cree_le || "",
    modifie_le: r.modifie_le || "",
    favori: state.favorites.has(entry.id),
    access: entry.access,
    owner: entry.owner,
    version: entry.version,
    images: entry.images || [],
  };
}

export const allRecipes = () => [...state.entries.values()].map(recipeOf);
export const getRecipe = (id) => (state.entries.has(id) ? recipeOf(state.entries.get(id)) : null);
export const canEdit = (recipe) => recipe.access === "owner" || recipe.access === "edit";

export function displayName(username) {
  if (!username) return "";
  if (state.me && username === state.me.username) return state.me.display_name || username;
  const found = state.users.find((u) => u.username === username);
  return found ? found.display_name || username : username;
}

export function newId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;  // uuid4, like the desktop app
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Put one server entry in the local copy (after our own save). */
export function applyEntry(entry) {
  if (entry.deleted) dropRecipe(entry.id);
  else state.entries.set(entry.id, entry);
}

export function dropRecipe(id) {
  state.entries.delete(id);
  state.favorites.delete(id);
  photos.forgetRecipe(id);
}

/**
 * Fetch what changed since the last call. Returns the set of changed recipe ids
 * (added, updated, removed), plus whether favourites changed.
 */
export async function sync() {
  const data = await api.sync(state.seq);
  const changed = new Set();
  for (const entry of data.recipes) {
    if (entry.deleted) {
      if (state.entries.has(entry.id)) changed.add(entry.id);
      dropRecipe(entry.id);
    } else {
      const old = state.entries.get(entry.id);
      if (!old || old.version !== entry.version || old.seq !== entry.seq) changed.add(entry.id);
      // photos that disappeared must not stay in memory
      if (old) for (const img of old.images) if (!entry.images.some((i) => i.id === img.id)) photos.forget(entry.id, img.id);
      state.entries.set(entry.id, entry);
    }
  }
  const visible = new Set(data.visible_ids);
  for (const id of [...state.entries.keys()]) {
    if (!visible.has(id)) { changed.add(id); dropRecipe(id); }  // deleted or share revoked
  }
  const favorites = new Set(data.favorites);
  const favChanged = favorites.size !== state.favorites.size || [...favorites].some((id) => !state.favorites.has(id));
  if (favChanged) for (const id of new Set([...favorites, ...state.favorites])) if (favorites.has(id) !== state.favorites.has(id)) changed.add(id);
  state.favorites = favorites;
  state.seq = data.seq;
  return changed;
}

// ------------------------------------------------- photos (need the bearer token)
const cache = new Map();  // "recipe/image" -> Promise<blob url>

export const photos = {
  /** A blob: URL for the photo, fetched with the token and cached. */
  get(recipeId, imageId) {
    const key = `${recipeId}/${imageId}`;
    if (!cache.has(key)) {
      cache.set(key, api.imageBlob(recipeId, imageId).then((blob) => URL.createObjectURL(blob)).catch((error) => {
        cache.delete(key);
        throw error;
      }));
    }
    return cache.get(key);
  },
  forget(recipeId, imageId) {
    const key = `${recipeId}/${imageId}`;
    const pending = cache.get(key);
    cache.delete(key);
    if (pending) pending.then((url) => URL.revokeObjectURL(url), () => {});
  },
  forgetRecipe(recipeId) {
    for (const key of [...cache.keys()]) if (key.startsWith(`${recipeId}/`)) this.forget(...key.split("/"));
  },
  clear() {
    for (const key of [...cache.keys()]) this.forget(...key.split("/"));
  },
};
