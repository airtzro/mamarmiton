// Talks to the Mamarmiton API. The token lives in localStorage (this origin only).

const TOKEN_KEY = "mamarmiton.token";

export class ApiError extends Error {
  constructor(status, message, detail = null, offline = false) {
    super(message);
    this.status = status;
    this.detail = detail;  // the raw "detail" of the server (a dict on 409 conflicts)
    this.offline = offline;
  }
}

export const token = {
  get() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } },
  set(value) { try { localStorage.setItem(TOKEN_KEY, value); } catch { /* private mode */ } memory = value; },
  clear() { try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } memory = ""; },
};
let memory = "";  // fallback when localStorage is unavailable
const currentToken = () => token.get() || memory;

let onUnauthorized = () => {};
let onNetwork = () => {};
export function setHandlers(handlers) {
  onUnauthorized = handlers.unauthorized || onUnauthorized;
  onNetwork = handlers.network || onNetwork;
}

function friendlyMessage(status, detail, retryAfter) {
  if (status === 429) {
    const minutes = Math.ceil(Number(retryAfter) / 60);
    const wait = minutes > 0 ? ` (environ ${minutes} minute${minutes > 1 ? "s" : ""})` : "";
    return `Trop d'essais. Patientez un peu${wait}, puis réessayez.`;
  }
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (status === 413) return "Fichier trop lourd.";
  if (status === 422) return "Certaines informations ne sont pas valides.";
  if (status >= 500) return "Le serveur a un souci, réessayez dans un instant.";
  return "Une erreur est survenue.";
}

async function request(method, path, { json, form, auth = true, signal } = {}) {
  const headers = {};
  const init = { method, headers, signal, cache: "no-store" };
  if (auth && currentToken()) headers.Authorization = `Bearer ${currentToken()}`;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form) {
    init.body = form;
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    onNetwork(false);
    throw new ApiError(0, "Impossible de joindre le serveur. Vérifiez la connexion Internet.", null, true);
  }
  onNetwork(true);
  if (response.ok) return response;

  let detail = null;
  try { detail = (await response.json()).detail ?? null; } catch { /* no JSON body */ }
  const message = friendlyMessage(response.status, detail, response.headers.get("Retry-After"));
  if (response.status === 401 && auth) onUnauthorized(message);
  throw new ApiError(response.status, message, detail);
}

const json = async (promise) => (await promise).json();
const user = (name) => encodeURIComponent(name);

export const api = {
  login: (username, password, device) => json(request("POST", "/auth/login", { json: { username, password, device }, auth: false })),
  register: (body) => json(request("POST", "/auth/register", { json: body, auth: false })),
  logout: () => request("POST", "/auth/logout"),
  me: () => json(request("GET", "/me")),
  changePassword: (old_password, new_password) => request("POST", "/me/password", { json: { old_password, new_password } }),
  users: () => json(request("GET", "/users")),
  createInvite: (note) => json(request("POST", "/invites", { json: { note } })),

  sync: (since) => json(request("GET", `/recipes?since=${Number(since) || 0}`)),
  putRecipe: (id, recipe, base_version) => json(request("PUT", `/recipes/${id}`, { json: { recipe, base_version } })),
  deleteRecipe: (id) => request("DELETE", `/recipes/${id}`),
  setFavorite: (id, on) => request(on ? "PUT" : "DELETE", `/recipes/${id}/favorite`),

  uploadImage(id, file) {
    const form = new FormData();
    form.append("file", file, file.name || "photo.jpg");
    return json(request("POST", `/recipes/${id}/images`, { form }));
  },
  deleteImage: (id, imageId) => request("DELETE", `/recipes/${id}/images/${imageId}`),
  async imageBlob(id, imageId) { return (await request("GET", `/recipes/${id}/images/${imageId}`)).blob(); },

  shares: () => json(request("GET", "/shares")),
  shareBook: (name, can_edit) => request("PUT", `/shares/book/${user(name)}`, { json: { can_edit } }),
  unshareBook: (name) => request("DELETE", `/shares/book/${user(name)}`),
  leaveBook: (name) => request("DELETE", `/shares/received/${user(name)}`),
  shareRecipe: (id, name, can_edit) => request("PUT", `/recipes/${id}/shares/${user(name)}`, { json: { can_edit } }),
  unshareRecipe: (id, name) => request("DELETE", `/recipes/${id}/shares/${user(name)}`),
};
