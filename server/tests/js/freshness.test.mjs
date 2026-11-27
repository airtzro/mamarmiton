import test from "node:test";
import assert from "node:assert/strict";
import { decide, isOutdated, pageBuild } from "../../mamarmiton_server/web/freshness.js";

const doc = (content) => ({ querySelector: () => (content === null ? null : { getAttribute: () => content }) });

test("reads the build the page was served with", () => {
  assert.equal(pageBuild(doc("abc123")), "abc123");
  assert.equal(pageBuild(doc("__BUILD__")), "");  // file opened without the server
  assert.equal(pageBuild(doc(null)), "");
});

test("only a different, known build is newer", () => {
  assert.equal(isOutdated("aaa", "bbb"), true);
  assert.equal(isOutdated("aaa", "aaa"), false);
  assert.equal(isOutdated("aaa", ""), false);         // older server without the field
  assert.equal(isOutdated("aaa", undefined), false);
  assert.equal(isOutdated("", "bbb"), false);         // page of unknown build never reloads
});

test("reloads at once, or waits while the person is busy", () => {
  assert.equal(decide("aaa", "aaa", false), "stay");
  assert.equal(decide("aaa", "bbb", false), "reload");
  assert.equal(decide("aaa", "bbb", true), "wait");
  assert.equal(decide("aaa", "aaa", true), "stay");
});
