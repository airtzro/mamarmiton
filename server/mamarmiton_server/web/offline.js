// Tiny IndexedDB wrapper: the offline copy of the recipe book and the outbox of
// edits made without a connection. No library. Reads never throw (an unavailable
// IndexedDB simply means "no cache"); queueing an edit does, so it is never lost silently.

const DB_NAME = "mamarmiton-offline";
let dbPromise = null;

function openDb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      if (typeof indexedDB === "undefined") { reject(new Error("IndexedDB indisponible")); return; }
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        db.createObjectStore("kv");  // "cache:<user>" snapshots and "me"
        const outbox = db.createObjectStore("outbox", { keyPath: "uid", autoIncrement: true });
        outbox.createIndex("user", "user");
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    dbPromise.catch(() => { dbPromise = null; });
  }
  return dbPromise;
}

async function run(storeName, mode, action) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const result = action(tx.objectStore(storeName));
    tx.oncomplete = () => resolve(result.result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

const quiet = async (task, fallback) => { try { return await task(); } catch { return fallback; } };

// ------------------------------------------------------------ snapshot per user
const cacheKey = (username) => `cache:${username}`;

export const loadSnapshot = (username) => quiet(() => run("kv", "readonly", (s) => s.get(cacheKey(username))), null);
export const saveSnapshot = (username, snapshot) => quiet(() => run("kv", "readwrite", (s) => s.put(snapshot, cacheKey(username))), null);
export const loadMe = () => quiet(() => run("kv", "readonly", (s) => s.get("me")), null);
export const saveMe = (me) => quiet(() => run("kv", "readwrite", (s) => s.put(me, "me")), null);

// ------------------------------------------------------------------- outbox
// op: {uid, user, op: "put"|"delete"|"fav", id, recipe?, base?, value?}
export const outboxList = (username) => quiet(() => run("outbox", "readonly", (s) => s.index("user").getAll(username)), []);
export const outboxAdd = (op) => run("outbox", "readwrite", (s) => s.add(op));
export const outboxPut = (op) => run("outbox", "readwrite", (s) => s.put(op));
export const outboxDelete = (uid) => quiet(() => run("outbox", "readwrite", (s) => s.delete(uid)), null);

/** Forget everything kept on this device for a user (explicit sign-out). */
export async function clearUser(username) {
  await quiet(() => run("kv", "readwrite", (s) => { s.delete(cacheKey(username)); return s.delete("me"); }), null);
  const ops = await outboxList(username);
  for (const op of ops) await outboxDelete(op.uid);
}
