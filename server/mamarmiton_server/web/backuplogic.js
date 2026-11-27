// "Mes recettes" (sauvegarde): the wording, checked with node in tests/js/backuplogic.test.mjs.

export const BACKUP_ACCEPT = ".json,.zip,application/json,application/zip";
export const OFFLINE_MESSAGE = "Il faut être connecté à Internet pour sauvegarder ou importer vos recettes. Réessayez plus tard.";

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** The name of the downloaded file, e.g. mamarmiton-sauvegarde-2026-11-27.zip (local date). */
export function backupFileName(date = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `mamarmiton-sauvegarde-${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}.zip`;
}

/** "12 recettes ajoutées (5 photos), 3 déjà présentes." from the answer of POST /import/backup. */
export function importSummary(result) {
  const n = (key) => Math.max(0, Math.floor(Number(result && result[key]) || 0));
  const added = n("added"), skipped = n("skipped"), invalid = n("invalid"), photos = n("photos");
  const parts = [];
  if (added) parts.push(plural(added, "recette ajoutée", "recettes ajoutées") + (photos ? ` (${plural(photos, "photo", "photos")})` : ""));
  if (skipped) parts.push(`${skipped} déjà ${skipped > 1 ? "présentes" : "présente"}`);
  if (invalid) parts.push(`${invalid} ${invalid > 1 ? "illisibles" : "illisible"}`);
  if (!parts.length) return "Aucune recette trouvée dans ce fichier.";
  return `${parts.join(", ")}.`;
}
