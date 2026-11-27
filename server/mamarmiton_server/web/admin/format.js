// Pure formatting helpers for the admin dashboard (no DOM: tested with node --test).

export const EVENT_LABELS = {
  recipe_open: "Ouvrir une recette",
  recipe_create: "Créer une recette",
  recipe_edit: "Modifier une recette",
  recipe_delete: "Supprimer une recette",
  recipe_hide: "Retirer une recette partagée",
  delete_undo: "Annuler une suppression",
  variant_create: "Créer une variante",
  search: "Rechercher",
  favorite_toggle: "Favori (ajout/retrait)",
  photo_add: "Ajouter une photo",
  pdf_import: "Importer un PDF",
  url_import: "Importer depuis un site",
  pdf_export: "Exporter en PDF",
  kitchen_open: "Mode cuisine",
  timer_start: "Lancer un minuteur",
  shopping_add: "Ajouter aux courses",
  shopping_open: "Ouvrir les courses",
  shopping_copy_or_print: "Copier/imprimer les courses",
  tag_filter: "Filtrer par étiquette",
  tag_add: "Ajouter une étiquette",
  share_create: "Partager",
  sync_conflict_shown: "Conflit de synchro affiché",
  update_banner_click: "Clic « mise à jour »",
  backup_export: "Exporter une sauvegarde",
  backup_import: "Importer une sauvegarde",
};

export const eventLabel = (event) => EVENT_LABELS[event] || event;

export function plural(n, one, many = `${one}s`) {
  return `${n} ${Math.abs(n) >= 2 ? many : one}`;
}

export function bytes(n) {
  const value = Number(n) || 0;
  if (value < 1024) return `${value} o`;
  const units = ["Ko", "Mo", "Go", "To"];
  let v = value / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1).replace(".", ",")} ${units[i]}`;
}

const toDate = (value) => (value instanceof Date ? value : new Date(value));

/** "à l'instant", "il y a 5 min", "il y a 3 h", "hier", "il y a 4 jours", then a date. Future: "dans …". */
export function ago(value, now = new Date()) {
  if (!value) return "jamais";
  const when = toDate(value);
  if (Number.isNaN(when.getTime())) return "?";
  const seconds = Math.round((now - when) / 1000);
  if (seconds < 0) return future(-seconds);
  if (seconds < 60) return "à l'instant";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "hier";
  if (days < 30) return `il y a ${days} jours`;
  return `le ${shortDate(when)}`;
}

function future(seconds) {
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `dans ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `dans ${hours} h`;
  return `dans ${Math.round(hours / 24)} jours`;
}

export function shortDate(value) {
  const d = toDate(value);
  if (Number.isNaN(d.getTime())) return "?";
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
}

export function dateTime(value) {
  const d = toDate(value);
  if (Number.isNaN(d.getTime())) return "?";
  return `${shortDate(d)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function duration(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (days) return `${days} j ${hours} h`;
  if (hours) return `${hours} h ${minutes} min`;
  return `${minutes} min`;
}

/** Heat level 0..4 of a count relative to the largest count of the table (0 stays 0). */
export function heat(count, max) {
  if (!count || !max) return 0;
  return Math.min(4, Math.max(1, Math.ceil((count / max) * 4)));
}

/** The message to send with an invite code. */
export function inviteMessage(code, origin, expiresAt) {
  return [
    "Bonjour ! Voici ton invitation pour Mamarmiton, le carnet de recettes de la famille.",
    "",
    `1. Ouvre ${origin}/ (ou l'application Mamarmiton sur l'ordinateur).`,
    "2. Choisis « J'ai un code d'invitation : créer un compte ».",
    `3. Entre ce code : ${code}`,
    "",
    `Le code ne sert qu'une fois et marche jusqu'au ${dateTime(expiresAt)}.`,
  ].join("\n");
}

/** The message to send with a temporary password. */
export function passwordMessage(username, password, origin) {
  return [
    `Ton nouveau mot de passe provisoire pour Mamarmiton (compte « ${username} ») : ${password}`,
    "",
    `Connecte-toi sur ${origin}/ ou dans l'application, puis change-le dans « Mon compte ».`,
  ].join("\n");
}

export const INVITE_STATUS = {
  open: { label: "En attente", tone: "warn" },
  used: { label: "Utilisée", tone: "ok" },
  expired: { label: "Expirée", tone: "muted" },
};
