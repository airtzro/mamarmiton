import test from "node:test";
import assert from "node:assert/strict";
import { EVENT_LABELS, ago, bytes, duration, eventLabel, heat, inviteMessage, passwordMessage, plural }
  from "../../mamarmiton_server/web/admin/format.js";
import { EVENTS } from "../../mamarmiton_server/web/usage.js";

test("every usage event has a French label", () => {
  for (const e of EVENTS) assert.ok(EVENT_LABELS[e], `missing label for ${e}`);
  assert.equal(eventLabel("unknown_event"), "unknown_event");
});

test("bytes", () => {
  assert.equal(bytes(0), "0 o");
  assert.equal(bytes(1536), "1,5 Ko");
  assert.equal(bytes(250 * 1024 * 1024), "250 Mo");
  assert.equal(bytes(3.2 * 1024 ** 3), "3,2 Go");
});

test("ago", () => {
  const now = new Date("2026-11-27T12:00:00Z");
  assert.equal(ago(null, now), "jamais");
  assert.equal(ago("2026-11-27T11:59:40Z", now), "à l'instant");
  assert.equal(ago("2026-11-27T11:15:00Z", now), "il y a 45 min");
  assert.equal(ago("2026-11-27T07:00:00Z", now), "il y a 5 h");
  assert.equal(ago("2026-11-26T10:00:00Z", now), "hier");
  assert.equal(ago("2026-11-20T10:00:00Z", now), "il y a 7 jours");
  assert.match(ago("2026-01-01T10:00:00Z", now), /^le 01\/01\/2026$/);
  assert.equal(ago("2026-11-27T12:30:00Z", now), "dans 30 min");
  assert.equal(ago("2026-12-04T12:00:00Z", now), "dans 7 jours");
  assert.equal(ago("garbage", now), "?");
});

test("plural, duration, heat", () => {
  assert.equal(plural(1, "recette"), "1 recette");
  assert.equal(plural(0, "recette"), "0 recette");
  assert.equal(plural(3, "recette"), "3 recettes");
  assert.equal(duration(59), "0 min");
  assert.equal(duration(3 * 3600 + 120), "3 h 2 min");
  assert.equal(duration(2 * 86400 + 3600), "2 j 1 h");
  assert.equal(heat(0, 10), 0);
  assert.equal(heat(1, 100), 1);
  assert.equal(heat(100, 100), 4);
  assert.equal(heat(5, 0), 0);
});

test("messages carry the code and the address", () => {
  const msg = inviteMessage("ABCD-EFGH-JKLM-NPQR", "https://example.org", "2026-12-04T08:50:00Z");
  assert.match(msg, /ABCD-EFGH-JKLM-NPQR/);
  assert.match(msg, /https:\/\/example\.org\//);
  assert.match(msg, /J'ai un code d'invitation/);
  const pw = passwordMessage("maman", "abcd-efgh-jkmn", "https://example.org");
  assert.match(pw, /abcd-efgh-jkmn/);
  assert.match(pw, /« maman »/);
});
