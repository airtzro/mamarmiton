// Anonymous usage counters: how many times per day each feature was used. Nothing else.
// Counted: an event name from EVENTS and the day. Never counted: recipe names or content, search words,
// who you are. Kept in localStorage and sent with the sync (POST /usage), then cleared.
// The same list exists in mamarmiton_server/usage.py and mamarmiton/usage.py (a test keeps them equal).

export const EVENTS = [
  "recipe_open", "recipe_create", "recipe_edit", "recipe_delete", "delete_undo", "variant_create",
  "search", "favorite_toggle", "photo_add", "pdf_import", "url_import", "pdf_export",
  "kitchen_open", "timer_start", "shopping_add", "shopping_open", "shopping_copy_or_print",
  "tag_filter", "tag_add", "share_create", "sync_conflict_shown", "update_banner_click",
  "backup_export", "backup_import",
];
export const ACTIVE = "active";  // 1 per day: "the app was used today"
export const EXPLANATION =
  "Pour savoir quelles fonctions servent vraiment, l'application compte combien de fois par jour chaque fonction est "
  + "utilisée (par exemple « une recette ouverte », « une liste de courses copiée »). Ces nombres sont anonymes : "
  + "ils ne contiennent ni le nom de vos recettes, ni vos recherches, ni votre nom, et ne sont pas associés à votre "
  + "compte. Rien n'est envoyé à une société extérieure, seulement à votre serveur Mamarmiton.";

const KEY = "mamarmiton.usage";
const OFF_KEY = "mamarmiton.usage.off";
const KEEP_DAYS = 90;
const SEND_EVERY_MS = 5 * 60 * 1000;

export const localDay = (date = new Date()) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

/** A counter over any Storage-like object (tests pass a fake). Every method swallows its errors. */
export function createCounter({ storage, now = () => new Date() } = {}) {
  let days = null;
  let lastTry = 0;
  const read = () => {
    if (days) return days;
    days = {};
    try {
      const data = JSON.parse(storage.getItem(KEY) || "{}");
      if (data && typeof data === "object") {
        for (const [day, events] of Object.entries(data)) {
          if (events && typeof events === "object") days[day] = { ...events };
        }
      }
    } catch { /* unreadable: start again */ }
    return days;
  };
  const save = () => { try { storage.setItem(KEY, JSON.stringify(days)); } catch { /* private mode, full */ } };
  const prune = () => {
    const limit = localDay(new Date(now().getTime() - KEEP_DAYS * 86400000));
    for (const day of Object.keys(days)) if (day < limit) delete days[day];
  };
  const isEnabled = () => { try { return storage.getItem(OFF_KEY) !== "1"; } catch { return true; } };

  return {
    enabled: isEnabled,
    setEnabled(value) {
      try {
        if (value) storage.removeItem(OFF_KEY); else { storage.setItem(OFF_KEY, "1"); storage.removeItem(KEY); }
      } catch { /* ignore */ }
      if (!value) days = {};
    },
    hit(event) {
      try {
        if (!EVENTS.includes(event) || !isEnabled()) return;
        read();
        const day = localDay(now());
        const today = (days[day] ||= {});
        today[event] = (today[event] || 0) + 1;
        if (today[ACTIVE] === undefined) today[ACTIVE] = 1;
        prune();
        save();
      } catch { /* never in the way */ }
    },
    /** What is waiting to be sent (a 0 means "already sent"). */
    snapshot() {
      read();
      const out = {};
      for (const [day, events] of Object.entries(days)) {
        const waiting = Object.fromEntries(Object.entries(events).filter(([, n]) => n > 0));
        if (Object.keys(waiting).length) out[day] = waiting;
      }
      return out;
    },
    acknowledge(sent) {
      read();
      for (const [day, events] of Object.entries(sent)) {
        const mine = days[day];
        if (!mine) continue;
        for (const [event, n] of Object.entries(events)) {
          if (event in mine) mine[event] = event === ACTIVE ? 0 : Math.max(0, mine[event] - n);
        }
        days[day] = Object.fromEntries(Object.entries(mine).filter(([e, n]) => n > 0 || e === ACTIVE));
      }
      save();
    },
    /** send(days) must resolve on a 2xx. Silent on any failure; at most one try every 5 minutes. */
    async flush(send, { force = false } = {}) {
      try {
        if (!isEnabled()) return false;
        const t = now().getTime();
        if (!force && lastTry && t - lastTry < SEND_EVERY_MS) return false;
        const batch = this.snapshot();
        if (!Object.keys(batch).length) return false;
        lastTry = t;
        await send(batch);
        this.acknowledge(batch);
        return true;
      } catch { return false; }
    },
  };
}

let shared = null;
function counter() {
  if (shared) return shared;
  let storage;
  try { storage = window.localStorage; } catch { storage = null; }
  const memory = new Map();
  shared = createCounter({ storage: storage || {
    getItem: (k) => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: (k) => memory.delete(k) } });
  return shared;
}

export const hit = (event) => counter().hit(event);
export const usageEnabled = () => counter().enabled();
export const setUsageEnabled = (value) => counter().setEnabled(value);
export const flushUsage = (send) => counter().flush(send);
