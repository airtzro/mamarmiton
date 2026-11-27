import test from "node:test";
import assert from "node:assert/strict";
import { TEXT_SIZES, parseTextSize } from "../../mamarmiton_server/web/textsize.js";

test("known sizes are kept", () => {
  for (const [id] of TEXT_SIZES) assert.equal(parseTextSize(id), id);
});

test("anything else falls back to normal", () => {
  for (const bad of [null, undefined, "", "huge", "XLARGE", 1.3, {}, "__proto__"]) assert.equal(parseTextSize(bad), "normal");
});

test("three sizes, normal first", () => {
  assert.deepEqual(TEXT_SIZES.map(([id]) => id), ["normal", "large", "xlarge"]);
});
