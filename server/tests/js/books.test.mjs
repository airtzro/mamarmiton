import test from "node:test";
import assert from "node:assert/strict";
import { ALL, MINE, bookOptions, inBook, loadBook, saveBook, validBook } from "../../mamarmiton_server/web/books.js";

const r = (id, access, owner) => ({ id, access, owner });
const recipes = [r(1, "owner", "tristan"), r(2, "owner", "tristan"), r(3, "read", "maman"), r(4, "edit", "maman"), r(5, "read", "zoe"), r(6, "read", "alice")];
const names = { maman: "Mamounette", zoe: "Zoé", alice: "Alice" };

test("options: all, mine, then each person by display name, with counts", () => {
  const opts = bookOptions(recipes, (u) => names[u]);
  assert.deepEqual(opts.map((o) => [o.id, o.label, o.n]), [
    [ALL, "Tous les carnets", 6], [MINE, "Mon carnet", 2],
    ["alice", "Carnet de Alice", 1], ["maman", "Carnet de Mamounette", 2], ["zoe", "Carnet de Zoé", 1]]);
});

test("inBook filters by owner; my own recipes never show in someone else's book", () => {
  assert.deepEqual(recipes.filter((x) => inBook(x, ALL)).map((x) => x.id), [1, 2, 3, 4, 5, 6]);
  assert.deepEqual(recipes.filter((x) => inBook(x, MINE)).map((x) => x.id), [1, 2]);
  assert.deepEqual(recipes.filter((x) => inBook(x, "maman")).map((x) => x.id), [3, 4]);
  assert.deepEqual(recipes.filter((x) => inBook(x, "tristan")).map((x) => x.id), []);
});

test("a book that disappeared (share revoked) falls back to all; MINE cannot clash with a username", () => {
  const opts = bookOptions(recipes, (u) => u);
  assert.equal(validBook("maman", opts), "maman");
  assert.equal(validBook("bob", opts), ALL);
  assert.equal(validBook(MINE, opts), MINE);
  assert.ok(!/^[a-z0-9][a-z0-9._-]{1,31}$/.test(MINE));
});

test("only my book when nobody shares", () => {
  assert.equal(bookOptions([r(1, "owner", "me")]).length, 2);
});

test("choice is remembered per account", () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: (k) => data.delete(k) };
  assert.equal(loadBook(storage, "tristan"), ALL);
  saveBook(storage, "tristan", "maman");
  assert.equal(loadBook(storage, "tristan"), "maman");
  assert.equal(loadBook(storage, "maman"), ALL);
  saveBook(storage, "tristan", ALL);
  assert.equal(loadBook(storage, "tristan"), ALL);
  assert.equal(loadBook({ getItem() { throw new Error("blocked"); } }, "x"), ALL);
});
