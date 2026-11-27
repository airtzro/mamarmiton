// The client-side copy of the recipe book, refreshed through GET /recipes?since=.
import { ApiError, api } from "./api.js";
import * as store from "./offline.js";
import { toast } from "./ui.js";
import { normalizeTags } from "./search.js";

export const state = {
  me: null,                 // {username, display_name, is_admin}
  users: [],                // other accounts [{username, display_name}]
  entries: new Map(),       // id -> server entry {id, recipe, version, owner, access, images}
  favorites: new Set(),
  seq: 0,
  filters: { text: "", category: "", tags: [], sort: "Favoris d'abord" },
};

export function resetState() {
  pendingCount = 0;
  state.me = null;
  state.users = [];
  state.entries = new Map();
  state.favorites = new Set();
  state.seq = 0;
  state.filters = { text: "", category: "", tags: [], sort: "Favoris d'abord" };
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
    tags: normalizeTags(r.tags),
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
  // our offline edits go first, so the pull below never overwrites them
  const { left, changed: pushed, error } = await flush();
  if (left > 0) throw error || new ApiError(0, "Impossible de joindre le serveur. Vérifiez la connexion Internet.", null, true);
  const data = await api.sync(state.seq);
  const changed = pushed;
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
  await persist();
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

// ------------------------------------------------ offline copy and outbox
export let pendingCount = 0;  // edits waiting for a connection
const announce = () => window.dispatchEvent(new Event("mamarmiton:pending"));

async function refreshPending() {
  pendingCount = state.me ? (await store.outboxList(state.me.username)).length : 0;
  announce();
}

/** Save the local copy of the book on the device. */
export async function persist() {
  if (!state.me) return;
  await store.saveSnapshot(state.me.username, {
    entries: [...state.entries.values()], favorites: [...state.favorites], seq: state.seq, users: state.users,
  });
}

/** Fill the state from the device copy. Returns true when there was one. */
export async function hydrate(username) {
  const snap = await store.loadSnapshot(username);
  if (!snap || !Array.isArray(snap.entries) || !Array.isArray(snap.favorites)) return false;
  state.entries = new Map(snap.entries.map((e) => [e.id, e]));
  state.favorites = new Set(snap.favorites);
  state.seq = Number(snap.seq) || 0;
  state.users = Array.isArray(snap.users) ? snap.users : [];
  await refreshPending();
  return true;
}

const userName = () => state.me.username;
const queuedFor = async (id) => (await store.outboxList(userName())).filter((o) => o.id === id);

async function queuePut(id, recipe, base) {
  const ops = await queuedFor(id);
  const prev = ops.find((o) => o.op === "put");
  if (prev) { prev.recipe = recipe; await store.outboxPut(prev); }
  else await store.outboxAdd({ user: userName(), op: "put", id, recipe, base });
  const old = state.entries.get(id);
  state.entries.set(id, { ...(old || { id, version: null, owner: userName(), access: "owner", images: [], seq: 0 }), recipe });
  await persist();
  await refreshPending();
}

async function queueDelete(id) {
  const ops = await queuedFor(id);
  const neverSent = ops.some((o) => o.op === "put" && o.base === null);
  for (const o of ops) await store.outboxDelete(o.uid);
  if (!neverSent) await store.outboxAdd({ user: userName(), op: "delete", id });
  dropRecipe(id);
  await persist();
  await refreshPending();
}

async function queueFav(id, on) {
  for (const o of await queuedFor(id)) if (o.op === "fav") await store.outboxDelete(o.uid);
  await store.outboxAdd({ user: userName(), op: "fav", id, value: on });
  if (on) state.favorites.add(id); else state.favorites.delete(id);
  await persist();
  await refreshPending();
}

/**
 * Save a recipe. Returns {entry} when the server took it, or {queued: true} when it was kept
 * on the device (no connection, or earlier edits of this recipe are still waiting).
 * Throws ApiError otherwise, including the 409 conflict (detail.current).
 */
export async function saveRecipe(id, recipe, base) {
  await flushDone();
  if ((await queuedFor(id)).length === 0) {
    try { return { entry: await api.putRecipe(id, recipe, base) }; }
    catch (e) { if (!e.offline) throw e; }
  }
  await queuePut(id, recipe, base);
  return { queued: true };
}

export async function deleteRecipe(id) {
  await flushDone();
  if ((await queuedFor(id)).length === 0) {
    try { await api.deleteRecipe(id); dropRecipe(id); await persist(); return { queued: false }; }
    catch (e) { if (!e.offline) throw e; }
  }
  await queueDelete(id);
  return { queued: true };
}

export async function setFavorite(id, on) {
  await flushDone();
  if ((await queuedFor(id)).length === 0) {
    try {
      await api.setFavorite(id, on);
      if (on) state.favorites.add(id); else state.favorites.delete(id);
      await persist();
      return { queued: false };
    } catch (e) { if (!e.offline) throw e; }
  }
  await queueFav(id, on);
  return { queued: true };
}

const isOfflineError = (e) => e && (e.offline || e.status === 0);

/** Keep the edit as "<nom> (ma version)" next to the server's recipe, so nothing is lost. */
async function keepMyVersion(recipe) {
  const copy = { ...recipe, nom: `${recipe.nom} (ma version)` };
  const entry = await api.putRecipe(newId(), copy, null);
  applyEntry(entry);
  return entry;
}

async function runOp(op, later, changed) {
  const { id } = op;
  if (op.op === "delete") {
    try { await api.deleteRecipe(id); } catch (e) { if (e.status !== 404) throw e; }
    changed.add(id);
  } else if (op.op === "fav") {
    try { await api.setFavorite(id, op.value); } catch (e) { if (e.status !== 404) throw e; }
    changed.add(id);
  } else {
    try {
      applyEntry(await api.putRecipe(id, op.recipe, op.base));
      changed.add(id);
    } catch (e) {
      if (e.status === 409 && op.base === null) {
        // that id is already taken on the server: store the recipe under a fresh one
        const fresh = newId();
        const entry = await api.putRecipe(fresh, op.recipe, null);
        dropRecipe(id);
        applyEntry(entry);
        for (const o of later) if (o.id === id) { o.id = fresh; await store.outboxPut(o); }
        changed.add(id).add(fresh);
      } else if (e.status === 409 && e.detail && e.detail.current) {
        // someone saved first: their version stays, mine becomes "<nom> (ma version)"
        applyEntry(e.detail.current);
        await keepMyVersion(op.recipe);
        changed.add(id);
        toast(`« ${op.recipe.nom} » a été modifiée ailleurs en attendant. Votre version est gardée sous « ${op.recipe.nom} (ma version) ».`, "info");
      } else if ((e.status === 403 || e.status === 404) && op.base !== null) {
        await keepMyVersion(op.recipe);
        dropRecipe(id);
        changed.add(id);
        toast(`Vous ne pouvez plus modifier « ${op.recipe.nom} ». Votre version est gardée sous « ${op.recipe.nom} (ma version) ».`, "info");
      } else throw e;
    }
  }
}

let flushing = null;

/** Wait for a running flush: a queued edit rewritten while it is being sent would be deleted unsent. */
const flushDone = async () => { if (flushing) await flushing.catch(() => {}); };

/** Send the offline edits, oldest first. Returns {left, changed, error}. */
export function flush() {
  if (!state.me) return Promise.resolve({ left: 0, changed: new Set(), error: null });
  if (!flushing) flushing = doFlush().finally(() => { flushing = null; });
  return flushing;
}

async function doFlush() {
  const changed = new Set();
  let error = null;
  const ops = await store.outboxList(userName());
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    try {
      await runOp(op, ops.slice(i + 1), changed);
    } catch (e) {
      // no connection, signed out, or a server that is struggling (5xx, 429): keep the edit and retry later
      if (isOfflineError(e) || e.status === 401 || e.status === 429 || e.status >= 500 || !(e instanceof ApiError)) { error = e; break; }
      toast(`Une modification faite hors ligne n'a pas pu être envoyée : ${e.message}`, "error");  // refused for good: drop it
    }
    await store.outboxDelete(op.uid);
  }
  await persist();
  await refreshPending();
  return { left: pendingCount, changed, error };
}
