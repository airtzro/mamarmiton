import test from "node:test";
import assert from "node:assert/strict";
import { api, reloadOnceForUpgrade } from "../../mamarmiton_server/web/api.js";

const memory = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) }; };

test("reloads once, then not again within the guard window", () => {
  const storage = memory();
  let reloads = 0;
  assert.equal(reloadOnceForUpgrade(storage, () => reloads++, 1000), true);
  assert.equal(reloadOnceForUpgrade(storage, () => reloads++, 5000), false);
  assert.equal(reloads, 1);
  assert.equal(reloadOnceForUpgrade(storage, () => reloads++, 1000 + 61_000), true);
  assert.equal(reloads, 2);
});

test("broken storage never reloads (no loop)", () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(reloadOnceForUpgrade(broken, () => assert.fail("reloaded"), 1), false);
});

test("every request carries the client header", async () => {
  let seen;
  globalThis.fetch = async (path, init) => { seen = init.headers; return new Response("{}", { status: 200 }); };
  await api.users();
  assert.match(seen["X-Mamarmiton-Client"], /^web\/\S+$/);
});
