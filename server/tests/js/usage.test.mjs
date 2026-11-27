import test from "node:test";
import assert from "node:assert/strict";
import { ACTIVE, EVENTS, createCounter, localDay } from "../../mamarmiton_server/web/usage.js";
import { buildReport, install, logTail, record, recorded, redact } from "../../mamarmiton_server/web/diagnostics.js";

function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { data, getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k) };
}
const at = (iso) => { let t = new Date(iso); return { now: () => t, set: (v) => { t = new Date(v); } }; };

test("counts per day and event, only known events", () => {
  const storage = fakeStorage(); const clock = at("2026-11-27T10:00:00");
  const c = createCounter({ storage, now: clock.now });
  c.hit("recipe_open"); c.hit("recipe_open"); c.hit("search"); c.hit("not_an_event"); c.hit("active");
  assert.deepEqual(c.snapshot(), { "2026-11-27": { recipe_open: 2, search: 1, active: 1 } });
  assert.deepEqual(JSON.parse(storage.data.get("mamarmiton.usage")), { "2026-11-27": { recipe_open: 2, search: 1, active: 1 } });
});

test("counts survive a reload and prune after 90 days", () => {
  const storage = fakeStorage(); const clock = at("2026-01-01T10:00:00");
  createCounter({ storage, now: clock.now }).hit("search");
  clock.set("2026-11-27T10:00:00");
  const again = createCounter({ storage, now: clock.now });
  again.hit("recipe_open");
  assert.deepEqual(Object.keys(again.snapshot()), ["2026-11-27"]);
});

test("flush sends, clears on success, keeps hits made meanwhile, reports the day active once", async () => {
  const storage = fakeStorage(); const clock = at("2026-11-27T10:00:00");
  const c = createCounter({ storage, now: clock.now });
  c.hit("search");
  let sent;
  assert.equal(await c.flush(async (days) => { sent = days; c.hit("search"); }, { force: true }), true);
  assert.deepEqual(sent, { "2026-11-27": { search: 1, active: 1 } });
  assert.deepEqual(c.snapshot(), { "2026-11-27": { search: 1 } });  // active not sent twice
});

test("flush failure keeps everything and waits 5 minutes", async () => {
  const storage = fakeStorage(); const clock = at("2026-11-27T10:00:00");
  const c = createCounter({ storage, now: clock.now });
  c.hit("search");
  assert.equal(await c.flush(async () => { throw new Error("offline"); }), false);
  assert.equal(await c.flush(async () => {}), false);  // too soon
  assert.deepEqual(c.snapshot(), { "2026-11-27": { search: 1, active: 1 } });
  clock.set("2026-11-27T10:06:00");
  assert.equal(await c.flush(async () => {}), true);
  assert.deepEqual(c.snapshot(), {});
});

test("opt-out counts nothing, sends nothing and forgets", async () => {
  const storage = fakeStorage(); const c = createCounter({ storage, now: at("2026-11-27T10:00:00").now });
  c.hit("search");
  c.setEnabled(false);
  c.hit("search");
  assert.deepEqual(c.snapshot(), {});
  assert.equal(await c.flush(async () => assert.fail("must not send"), { force: true }), false);
  assert.equal(storage.data.has("mamarmiton.usage"), false);
  c.setEnabled(true); c.hit("search");
  assert.equal(c.snapshot()["2026-11-27"].search, 1);
});

test("a broken storage never throws", () => {
  const broken = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } };
  const c = createCounter({ storage: broken });
  c.hit("search"); c.setEnabled(false);
  assert.ok(EVENTS.includes("search") && ACTIVE === "active" && /^\d{4}-\d\d-\d\d$/.test(localDay()));
});

test("redact hides tokens, passwords and invite codes", () => {
  assert.equal(redact("Authorization: Bearer abc.DEF-123"), "Authorization: [masqué]");
  assert.match(redact("login password=hunter2 ok"), /password=\[masqué\] ok/);
  assert.match(redact('{"password": "mon secret", "x": 1}'), /"password": \[masqué\]/);
  assert.equal(redact("code ABCD-EFGH-JKLM-NPQR"), "code [masqué]");
  assert.equal(redact("t=" + "a".repeat(43)), "t=[masqué]");
  assert.equal(redact("simple message"), "simple message");
});

test("error ring buffer keeps the last 50, redacted, and builds the report", () => {
  for (let i = 0; i < 60; i++) record(`boom ${i} token=secretvalue`);
  assert.equal(recorded().length, 50);
  assert.ok(recorded()[0].includes("boom 10"));
  assert.ok(!logTail().includes("secretvalue"));
  const r = buildReport("  bug ", true, { navigator: { userAgent: "UA/1" } });
  assert.deepEqual([r.message, r.client, r.os], ["bug", "web", "UA/1"]);
  assert.equal(buildReport("x", false, { navigator: { userAgent: "" } }).log_tail, "");
});

test("install captures console.error once", () => {
  const listeners = {}; const calls = [];
  const win = { addEventListener: (n, f) => { listeners[n] = f; }, console: { error: (...a) => calls.push(a) } };
  install(win); install(win);
  win.console.error("kaput", new Error("x"));
  assert.equal(calls.length, 1);
  assert.ok(recorded().some((l) => l.includes("console.error: kaput x")));
  listeners.error({ message: "oops", filename: "a.js", lineno: 3 });
  assert.ok(recorded().some((l) => l.includes("error: oops (a.js:3)")));
});
