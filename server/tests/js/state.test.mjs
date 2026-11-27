// The offline outbox and the sync loop (state.js + offline.js), run under node with a tiny
// in-memory IndexedDB, a stubbed DOM and a scripted fetch.
import test from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------- stubs
const noop = () => {};
const element = () => ({ append: noop, remove: noop, setAttribute: noop, addEventListener: noop, classList: { add: noop, remove: noop, toggle: noop } });
const fired = [];
globalThis.window = { addEventListener: noop, dispatchEvent: (e) => fired.push(e.type), matchMedia: () => ({ matches: false, addEventListener: noop }) };
globalThis.document = { createElement: element, createTextNode: element, getElementById: element, documentElement: { dataset: {} }, querySelector: () => null };
globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop };

function fakeIndexedDB() {
  const stores = {};
  const later = (fn) => setTimeout(fn, 0);
  const makeStore = (name, { keyPath, autoIncrement } = {}) => {
    const rows = new Map();
    let counter = 0;
    const indexes = {};
    stores[name] = { rows, indexes };
    return {
      createIndex(index, field) { indexes[index] = field; },
      api() {
        return {
          get: (key) => ({ result: structuredClone(rows.get(key)) }),
          put(value, key) {
            if (keyPath) key = value[keyPath];
            rows.set(key, structuredClone(value));
            return { result: key };
          },
          add(value) {
            value = structuredClone(value);
            if (autoIncrement && value[keyPath] === undefined) value[keyPath] = ++counter;
            if (rows.has(value[keyPath])) throw new Error("duplicate key");
            rows.set(value[keyPath], value);
            return { result: value[keyPath] };
          },
          delete: (key) => { rows.delete(key); return { result: undefined }; },
          index: (index) => ({ getAll: (q) => ({ result: [...rows.values()].filter((r) => r[indexes[index]] === q).sort((a, b) => a[keyPath] - b[keyPath]).map((r) => structuredClone(r)) }) }),
        };
      },
    };
  };
  const made = {};
  return {
    open() {
      const request = {};
      later(() => {
        const db = {
          createObjectStore: (name, opts) => (made[name] = makeStore(name, opts)),
          transaction: (name) => {
            const tx = {};
            later(() => tx.oncomplete && tx.oncomplete());
            tx.objectStore = () => made[name].api();
            return tx;
          },
        };
        request.result = db;
        request.onupgradeneeded();
        request.onsuccess();
      });
      return request;
    },
    stores,
  };
}
const idb = fakeIndexedDB();
globalThis.indexedDB = idb;

// a scripted server: routes by "METHOD path"
let routes = {};
const calls = [];
globalThis.fetch = async (path, init) => {
  const key = `${init.method} ${path.split("?")[0]}`;
  calls.push(key);
  const handler = routes[key] || routes[`${init.method} *`];
  if (!handler) throw new Error(`unexpected request ${key}`);
  const out = await handler(init.body ? JSON.parse(init.body) : null, path);
  if (out instanceof Error) throw out;
  const { status = 200, body = null } = out;
  return { ok: status < 400, status, headers: { get: () => null }, json: async () => body, blob: async () => ({}) };
};
const offline = () => Object.assign(new TypeError("Failed to fetch"), {});
const entryOf = (id, recipe, version, seq = version) => ({ id, recipe, version, seq, deleted: false, owner: "ana", access: "owner", images: [] });

const { state, resetState, saveRecipe, deleteRecipe, setFavorite, flush, sync } = await import("../../mamarmiton_server/web/state.js");
const store = await import("../../mamarmiton_server/web/offline.js");

function login() {
  resetState();
  state.me = { username: "ana", display_name: "Ana" };
  idb.stores.outbox?.rows.clear();
  calls.length = 0;
  routes = {};
}
const outbox = () => store.outboxList("ana");

test("offline edits are queued, merged per recipe, and sent oldest first", async () => {
  login();
  routes = { "PUT *": () => offline(), "DELETE *": () => offline() };
  assert.deepEqual(await saveRecipe("aaaaaaaa", { nom: "Tarte" }, null), { queued: true });
  assert.deepEqual(await saveRecipe("aaaaaaaa", { nom: "Tarte aux pommes" }, null), { queued: true });  // same op, updated
  await setFavorite("aaaaaaaa", true);
  await saveRecipe("bbbbbbbb", { nom: "Cake" }, null);
  const ops = await outbox();
  assert.deepEqual(ops.map((o) => [o.op, o.id]), [["put", "aaaaaaaa"], ["fav", "aaaaaaaa"], ["put", "bbbbbbbb"]]);
  assert.equal(ops[0].recipe.nom, "Tarte aux pommes");
  assert.equal(state.entries.get("aaaaaaaa").recipe.nom, "Tarte aux pommes");

  const sent = [];
  routes = {
    "PUT /recipes/aaaaaaaa": (b) => { sent.push(["put", "a"]); return { body: entryOf("aaaaaaaa", b.recipe, 1) }; },
    "PUT /recipes/aaaaaaaa/favorite": () => { sent.push(["fav", "a"]); return { status: 204 }; },
    "PUT /recipes/bbbbbbbb": (b) => { sent.push(["put", "b"]); return { body: entryOf("bbbbbbbb", b.recipe, 1) }; },
  };
  const result = await flush();
  assert.equal(result.left, 0);
  assert.deepEqual(sent, [["put", "a"], ["fav", "a"], ["put", "b"]]);
  assert.equal(state.entries.get("aaaaaaaa").version, 1);
  assert.deepEqual(await outbox(), []);
});

test("deleting a recipe that never reached the server cancels its edits", async () => {
  login();
  routes = { "PUT *": () => offline(), "DELETE *": () => offline() };
  await saveRecipe("cccccccc", { nom: "Brouillon" }, null);
  assert.deepEqual(await deleteRecipe("cccccccc"), { queued: true });
  assert.deepEqual(await outbox(), []);
  assert.equal(state.entries.has("cccccccc"), false);
});

test("a server error (5xx / 429) keeps the edit queued instead of dropping it", async () => {
  for (const status of [500, 502, 503, 429]) {
    login();
    routes = { "PUT *": () => offline() };
    await saveRecipe("dddddddd", { nom: "Soupe" }, null);
    routes = { "PUT *": () => ({ status, body: { detail: "oups" } }) };
    const result = await flush();
    assert.equal(result.left, 1, `status ${status}`);
    assert.equal((await outbox()).length, 1);
  }
});

test("a refused edit (422) is dropped for good", async () => {
  login();
  routes = { "PUT *": () => offline() };
  await saveRecipe("eeeeeeee", { nom: "Bad" }, null);
  routes = { "PUT *": () => ({ status: 422, body: { detail: "non" } }) };
  assert.equal((await flush()).left, 0);
});

test("409 on an edit keeps the server version and saves mine as '(ma version)'", async () => {
  login();
  const theirs = entryOf("ffffffff", { nom: "Theirs" }, 5);
  state.entries.set("ffffffff", entryOf("ffffffff", { nom: "Old" }, 3));
  routes = { "PUT *": () => offline() };
  await saveRecipe("ffffffff", { nom: "Mine" }, 3);
  const created = [];
  routes = {
    "PUT /recipes/ffffffff": () => ({ status: 409, body: { detail: { message: "x", current: theirs } } }),
    "PUT *": (b, path) => { created.push([path, b.recipe.nom, b.base_version]); return { body: entryOf(path.split("/")[2], b.recipe, 1) }; },
  };
  assert.equal((await flush()).left, 0);
  assert.equal(state.entries.get("ffffffff").recipe.nom, "Theirs");
  assert.equal(created.length, 1);
  assert.equal(created[0][1], "Mine (ma version)");
  assert.equal(created[0][2], null);
});

test("sync pushes the outbox first, then pulls with the last seq and applies deletions", async () => {
  login();
  state.seq = 7;
  state.entries.set("11111111", entryOf("11111111", { nom: "Keep" }, 1, 3));
  state.entries.set("22222222", entryOf("22222222", { nom: "Gone" }, 1, 4));
  state.favorites = new Set(["22222222"]);
  let asked = null;
  routes = {
    "GET /recipes": (_b, path) => {
      asked = path;
      return { body: { seq: 12, recipes: [{ ...entryOf("22222222", {}, 2, 11), deleted: true }, entryOf("33333333", { nom: "New" }, 1, 12)],
        visible_ids: ["11111111", "33333333"], favorites: ["33333333"] } };
    },
  };
  const changed = await sync();
  assert.equal(asked, "/recipes?since=7");
  assert.equal(state.seq, 12);
  assert.deepEqual([...state.entries.keys()].sort(), ["11111111", "33333333"]);
  assert.deepEqual([...state.favorites], ["33333333"]);
  assert.deepEqual([...changed].sort(), ["22222222", "33333333"]);
  const snap = await store.loadSnapshot("ana");
  assert.equal(snap.seq, 12);
});

test("sync refuses to pull while edits are still waiting (offline)", async () => {
  login();
  routes = { "PUT *": () => offline() };
  await saveRecipe("44444444", { nom: "Wait" }, null);
  await assert.rejects(sync(), (e) => e.offline === true);
  assert.equal(calls.includes("GET /recipes"), false);
  assert.equal(state.entries.has("44444444"), true);  // not wiped by a pull that would not list it
});

test("an edit made while a flush is running is not lost", async () => {
  login();
  routes = { "PUT *": () => offline() };
  await saveRecipe("55555555", { nom: "v1" }, null);
  let release;
  const gate = new Promise((r) => { release = r; });
  const received = [];
  routes = { "PUT /recipes/55555555": async (b) => { received.push(b.recipe.nom); await gate; return { body: entryOf("55555555", b.recipe, 1) }; } };
  const running = flush();
  await new Promise((r) => setTimeout(r, 20));
  const second = saveRecipe("55555555", { nom: "v2" }, 1);  // user saves again mid-flush
  setTimeout(release, 40);  // long enough for an unprotected queue write to land mid-flush
  await running;
  routes = { "PUT /recipes/55555555": async (b) => { received.push(b.recipe.nom); return { body: entryOf("55555555", b.recipe, 2) }; } };
  const result = await second;  // sent right away (nothing left in the outbox), never swallowed by the flush
  assert.equal(result.entry ? result.entry.recipe.nom : (await flush(), received.at(-1)), "v2");
  assert.deepEqual(await outbox(), []);
  assert.equal(received.at(-1), "v2");
});
