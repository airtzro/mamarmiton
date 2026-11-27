// "Importer une recette": the rules that need no page (checked with node in tests/js/importlogic.test.mjs).
//  - which files may be sent (GET /import/status gives the limits),
//  - turning the answer of POST /import/recipe into a draft for the editor,
//  - the one draft waiting to be opened by the editor (#/nouvelle).
import { COOKING_TYPES, DISH_TYPES, normalizeTags } from "./search.js";

export const DEFAULT_ACCEPTS = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".txt", ".docx", ".odt"];
const IMAGE_EXT = new Set([".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"]);
const NEEDS_OCR = new Set([...IMAGE_EXT, ".pdf"]);
export const NO_OCR_MESSAGE = "La lecture des photos n'est pas disponible sur ce serveur.";

/** The status the page works with; null when the server has no import (old server, 404, bad answer). */
export function normalizeStatus(raw) {
  if (!raw || typeof raw !== "object" || raw.available !== true) return null;
  const accepts = Array.isArray(raw.accepts)
    ? raw.accepts.map((e) => String(e).trim().toLowerCase()).filter((e) => /^\.[a-z0-9]{1,8}$/.test(e))
    : [];
  const posInt = (v, fallback) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Math.floor(Number(v)) : fallback);
  return {
    ocr: raw.ocr === true,
    ai: raw.ai === true,
    url: raw.url === true,  // can read a recipe from a website address
    maxFiles: posInt(raw.max_files, 8),
    maxMb: posInt(raw.max_mb, 30),
    accepts: accepts.length ? accepts : DEFAULT_ACCEPTS,
  };
}

export function extensionOf(name) {
  const m = /\.[A-Za-z0-9]{1,8}$/.exec(String(name || ""));
  return m ? m[0].toLowerCase() : "";
}

/** "image" (shown as a thumbnail), "pdf" or "doc". */
export function kindOf(file) {
  const ext = extensionOf(file.name);
  if (IMAGE_EXT.has(ext) || (!ext && String(file.type || "").startsWith("image/"))) return "image";
  if (ext === ".pdf" || file.type === "application/pdf") return "pdf";
  return "doc";
}

const needsOcr = (file) => kindOf(file) !== "doc";  // photos and PDFs are read by OCR on the server

/** The `accept` attribute of the "Choisir un fichier" input. */
export function acceptAttribute(status) {
  const list = status.accepts.filter((ext) => status.ocr || !NEEDS_OCR.has(ext));
  return [status.ocr ? "image/*" : null, ...list].filter(Boolean).join(",");
}

export const sameFile = (a, b) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;

export function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} Mo`;
}

/**
 * Add `added` to the files already chosen. Returns {files, errors}: the files that can be sent,
 * and one plain-French sentence per file left out. Nothing is thrown, the dialog shows the sentences.
 */
export function addFiles(current, added, status) {
  const files = [...current];
  const errors = [];
  const limit = status.maxMb * 1024 * 1024;
  let total = files.reduce((n, f) => n + f.size, 0);
  let tooMany = 0;
  for (const file of added) {
    const ext = extensionOf(file.name);
    const isImage = String(file.type || "").startsWith("image/");
    if (files.some((f) => sameFile(f, file))) continue;  // the same page picked twice
    if (!file.size) { errors.push(`« ${file.name || "Ce fichier"} » est vide.`); continue; }
    if (ext ? !status.accepts.includes(ext) : !isImage) {
      errors.push(`« ${file.name} » n'est pas accepté : choisissez ${status.ocr ? "une photo, un PDF, " : ""}un texte ou un document Word ou LibreOffice.`);
      continue;
    }
    if (!status.ocr && needsOcr(file)) { errors.push(`« ${file.name} » : ${NO_OCR_MESSAGE}`); continue; }
    if (files.length >= status.maxFiles) { tooMany++; continue; }
    if (total + file.size > limit) {
      errors.push(`« ${file.name} » est trop lourd : ${status.maxMb} Mo au maximum pour l'ensemble des fichiers.`);
      continue;
    }
    files.push(file);
    total += file.size;
  }
  if (tooMany) errors.push(`${status.maxFiles} fichiers au maximum : ${tooMany > 1 ? `${tooMany} fichiers n'ont pas été ajoutés` : "un fichier n'a pas été ajouté"}.`);
  return { files, errors };
}

/** Why "Lire la recette" cannot start yet, or "" when it can. */
export function readyProblem(files, text) {
  if (!files.length && !String(text || "").trim()) return "Ajoutez une photo, un fichier ou un texte.";
  return "";
}

/** Why "Lire la recette" cannot start with this website address, or "" when it can (the server checks the rest). */
export function urlProblem(text) {
  const url = String(text || "").trim();
  if (!url) return "Collez d'abord l'adresse de la page de la recette.";
  if (/\s/.test(url) || !url.includes(".")) return "Ce n'est pas une adresse de site. Copiez l'adresse complète de la page (elle commence par « https:// »).";
  return "";
}

// ------------------------------------------------------------------ draft
const str = (v, max = 20000) => (typeof v === "string" ? v.trim().slice(0, max) : typeof v === "number" ? String(v) : "");
const minutes = (v) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) && n > 0 ? Math.min(n, 99 * 60) : 0;
};

const PHOTO_TYPES = { "image/jpeg": "photo.jpg", "image/png": "photo.png" };

/** The photo of a website recipe ({content_type, data: base64} in the answer of POST /import/url) as a File, or null. */
export function photoFromResult(result) {
  const p = result && result.photo;
  if (!p || !PHOTO_TYPES[p.content_type] || typeof p.data !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(p.data)) return null;
  try {
    const bytes = Uint8Array.from(atob(p.data), (c) => c.charCodeAt(0));
    return bytes.length ? new File([bytes], PHOTO_TYPES[p.content_type], { type: p.content_type }) : null;
  } catch { return null; }
}

/**
 * The answer of POST /import/recipe (or /import/url) -> {recipe, warnings, dishPhoto}. `recipe` has the shape the editor
 * fills its fields from (like state.recipeOf: ingredients as {nom, quantite}); `dishPhoto` is the File
 * the server picked as the photo of the dish (or the photo of the website), or null.
 */
export function draftFromImport(result, sentFiles = []) {
  const r = (result && typeof result.recipe === "object" && result.recipe) || {};
  const pour = Math.round(Number(r.pour));
  const ingredients = (Array.isArray(r.ingredients) ? r.ingredients : [])
    .map((i) => (typeof i === "string" ? { nom: str(i, 200), quantite: "" }
      : { nom: str(i && i.nom, 200), quantite: str(i && (i["quantité"] ?? i.quantite), 100) }))
    .filter((i) => i.nom || i.quantite);
  const etapes = (Array.isArray(r.etapes) ? r.etapes : []).map((s) => str(s, 5000)).filter(Boolean);
  const index = result && Number.isInteger(result.dish_photo) ? result.dish_photo : -1;
  const photo = sentFiles[index];
  return {
    recipe: {
      nom: str(r.nom, 200),
      temps: minutes(r.temps),
      temps_cuisson: minutes(r.temps_cuisson),
      pour: pour >= 1 && pour <= 99 ? pour : 4,
      type_plat: DISH_TYPES.includes(r.type_plat) ? r.type_plat : "Plat",
      type_cuisson: COOKING_TYPES.includes(r.type_cuisson) ? r.type_cuisson : "Sans cuisson",
      ingredients,
      etapes,
      notes: str(r.notes),
      tags: normalizeTags(Array.isArray(r.tags) ? r.tags.filter((t) => typeof t === "string") : []),
      source: str(r.source, 500),
      favori: false,
      images: [],
    },
    warnings: (Array.isArray(result && result.warnings) ? result.warnings : []).map((w) => str(w, 300)).filter(Boolean).slice(0, 10),
    dishPhoto: photo && index >= 0 && kindOf(photo) === "image" ? photo : photoFromResult(result),
  };
}

// The draft waiting for the editor: set by the import dialog, taken (once) by the #/nouvelle route.
let pendingDraft = null;
export function setPendingDraft(draft) { pendingDraft = draft || null; }
export function takePendingDraft() {
  const draft = pendingDraft;
  pendingDraft = null;
  return draft;
}
