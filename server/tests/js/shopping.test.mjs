import test from "node:test";
import assert from "node:assert/strict";
import { frac } from "../../mamarmiton_server/web/quantities.js";
import {
  AISLES, addExtra, addRecipe, aisleOf, buildList, clearState, formatAmount, groupByAisle, itemLabel, newState,
  parseAmount, prune, removeRecipe, setChecked, stateFromDict, stateToDict, toText,
} from "../../mamarmiton_server/web/shopping.js";

const recipe = (nom, pour, ...ings) => ({ nom, pour, ingredients: ings.map(([n, q]) => ({ nom: n, quantite: q })) });
const lines = (items) => items.map(itemLabel);
const one = (r, servings) => lines(buildList([{ recipe: r, servings }]));

test("merge and scale mass", () => {
  const a = recipe("Gâteau", 4, ["farine", "500 g"], ["sucre", "200 g"]);
  const b = recipe("Crêpes", 2, ["Farine", "1 kg"]);
  const out = lines(buildList([{ recipe: a, servings: 4 }, { recipe: b, servings: 4 }]));
  assert.ok(out.includes("2,5 kg de farine"));
  assert.ok(out.includes("200 g de sucre"));
});

test("kg boundary and decimal comma", () => {
  assert.deepEqual(one(recipe("A", 1, ["farine", "1500 g"]), 1), ["1,5 kg de farine"]);
  assert.deepEqual(one(recipe("B", 1, ["sel", "500 g"], ["sel", "400 g"]), 1), ["900 g de sel"]);
  assert.deepEqual(one(recipe("C", 1, ["sucre", "500 mg"]), 1), ["500 mg de sucre"]);
});

test("volumes merge across units", () => {
  assert.deepEqual(one(recipe("A", 1, ["lait", "25 cl"], ["lait", "1 dl"], ["lait", "1 l"], ["lait", "150 ml"]), 1), ["1,5 l de lait"]);
  assert.deepEqual(one(recipe("B", 1, ["crème", "20 cl"]), 1), ["200 ml de crème"]);
});

test("spoon variants merge", () => {
  const out = one(recipe("A", 1, ["huile d'olive", "2 c. à soupe"], ["huile d'olive", "1 cuillère à soupe"],
    ["huile d'olive", "1 cs"], ["sucre", "1 c. à café"], ["sucre", "2 cuillères à café"]), 1);
  assert.ok(out.includes("4 c. à soupe d'huile d'olive"));
  assert.ok(out.includes("3 c. à café de sucre"));
});

test("spoons and grams stay separate lines", () => {
  assert.deepEqual(one(recipe("A", 1, ["sucre", "100 g"], ["sucre", "1 c. à soupe"]), 1).sort(), ["1 c. à soupe de sucre", "100 g de sucre"]);
});

test("counts, plural, accents, oeufs", () => {
  assert.deepEqual(one(recipe("A", 4, ["oeuf", "1"], ["Oeufs", "2"], ["Œufs", "une"]), 4), ["4 oeufs"]);
  assert.deepEqual(one(recipe("B", 2, ["poireau", "1"]), 2), ["1 poireau"]);
  assert.deepEqual(one(recipe("C", 2, ["tomates", "3"], ["Tomate", "2"]), 2), ["5 tomates"]);
});

test("accent folding in names", () => {
  assert.deepEqual(one(recipe("A", 1, ["crème fraîche", "10 cl"], ["Creme fraiche", "10 cl"]), 1), ["200 ml de crème fraîche"]);
});

test("unitless scaling and fractions", () => {
  const a = recipe("A", 4, ["oignon", "1/2"]);
  assert.deepEqual(one(a, 8), ["1 oignon"]);
  assert.deepEqual(one(a, 4), ["1/2 oignon"]);
});

test("named units pluralise", () => {
  const a = recipe("A", 2, ["ail", "1 gousse"], ["sachet de levure", ""], ["levure", "1 sachet"]);
  const b = recipe("B", 2, ["ail", "2 gousses"]);
  const out = lines(buildList([{ recipe: a, servings: 2 }, { recipe: b, servings: 2 }]));
  assert.ok(out.includes("3 gousses d'ail"));
  assert.ok(out.includes("1 sachet de levure"));
});

test("no quantity listed once and dropped when quantified", () => {
  const a = recipe("A", 1, ["sel", ""], ["poivre", ""], ["persil", ""]);
  const b = recipe("B", 1, ["Sel", ""], ["poivre", "1 pincée"]);
  const items = buildList([{ recipe: a, servings: 1 }, { recipe: b, servings: 1 }]);
  assert.equal(lines(items).filter((l) => l === "sel").length, 1);
  assert.ok(lines(items).includes("1 pincée de poivre"));
  assert.ok(!lines(items).includes("poivre"));
  assert.deepEqual(items.find((i) => i.name.toLowerCase() === "sel").recipes.sort(), ["A", "B"]);
});

test("text quantity without number is no quantity", () => {
  assert.deepEqual(one(recipe("A", 1, ["sel", "au goût"]), 1), ["sel"]);
});

test("number word and range", () => {
  assert.equal(parseAmount("une pincée").dim, "unit");
  const r = parseAmount("2-3 c. à soupe");
  assert.equal(r.value.n / r.value.d, 3);
  assert.equal(parseAmount(""), null);
  assert.equal(parseAmount("sel"), null);
});

test("empty names skipped", () => assert.deepEqual(buildList([{ recipe: recipe("A", 1, ["", "2 g"]), servings: 1 }]), []));

test("servings scaling uses recipe pour", () => assert.deepEqual(one(recipe("A", 4, ["beurre", "125 g"]), 6), ["188 g de beurre"]));

test("aisles", () => {
  const cases = [["tomates", "Fruits et légumes"], ["pommes de terre", "Fruits et légumes"], ["Lait demi-écrémé", "Crèmerie"],
    ["œufs", "Crèmerie"], ["blanc de poulet", "Boucherie / poisson"], ["farine", "Épicerie"], ["pâtes", "Épicerie"],
    ["pâte feuilletée", "Crèmerie"], ["baguette", "Boulangerie"], ["petits pois surgelés", "Surgelés"], ["vin blanc", "Boissons"],
    ["vinaigre", "Épicerie"], ["lait de coco", "Épicerie"], ["machin inconnu", "Autres"], ["huile d'olive", "Épicerie"]];
  for (const [name, aisle] of cases) assert.equal(aisleOf(name), aisle, name);
});

test("grouping order and text", () => {
  const a = recipe("A", 1, ["farine", "500 g"], ["tomates", "3"], ["lait", "1 l"], ["truc", "1"]);
  const items = buildList([{ recipe: a, servings: 1 }]);
  const groups = groupByAisle(items);
  assert.deepEqual(groups.map(([g]) => g), ["Fruits et légumes", "Crèmerie", "Épicerie", "Autres"]);
  assert.deepEqual(groups.map(([g]) => g), AISLES.filter((x) => groups.some(([g]) => g === x)));
  const text = toText(items, new Set(), [{ text: "papier absorbant", done: false }, { text: "pile", done: true }]);
  assert.ok(text.startsWith("Liste de courses\n"));
  assert.ok(text.includes("- 3 tomates") && text.includes("- 500 g de farine") && text.includes("- papier absorbant"));
  assert.ok(!text.includes("pile"));
  const tomato = items.find((i) => i.name.includes("tomate"));
  assert.ok(!toText(items, new Set([tomato.key])).includes("tomates"));
  assert.ok(toText([]).includes("rien à acheter"));
});

test("format amount", () => {
  assert.equal(formatAmount({ dim: "mass", value: frac(1000) }), "1 kg");
  assert.equal(formatAmount({ dim: "mass", value: frac(2250) }), "2,25 kg");
  assert.equal(formatAmount({ dim: "vol", value: frac(2500) }), "2,5 l");
  assert.equal(formatAmount({ dim: "vol", value: frac(5, 2) }), "2,5 ml");
  assert.equal(formatAmount({ dim: "cas", value: frac(3, 2) }), "1 1/2 c. à soupe");
});

test("state edit and roundtrip", () => {
  const s = newState();
  addRecipe(s, "r1", 4); addRecipe(s, "r2", 2); addRecipe(s, "r1", 6);
  setChecked(s, "farine|mass", true);
  assert.ok(addExtra(s, "  papier   absorbant "));
  assert.ok(!addExtra(s, "   "));
  const loaded = stateFromDict(JSON.parse(JSON.stringify(stateToDict(s))));
  assert.deepEqual(loaded.recipes, [{ id: "r1", servings: 6 }, { id: "r2", servings: 2 }]);
  assert.deepEqual([...loaded.checked], ["farine|mass"]);
  assert.deepEqual(loaded.extras, [{ text: "papier absorbant", done: false }]);
  removeRecipe(loaded, "r1");
  clearState(loaded);
  assert.deepEqual(stateToDict(loaded).recipes, []);
  assert.equal(loaded.checked.size, 0);
});

test("state tolerates garbage", () => {
  const s = stateFromDict({ recipes: [{ id: "a", servings: "x" }, { id: "a" }, 3, { servings: 2 }], checked: [1, "k"],
    extras: [{ text: "" }, { text: "ok", done: 1 }] });
  assert.deepEqual(s.recipes, [{ id: "a", servings: 4 }]);
  assert.deepEqual([...s.checked], ["k"]);
  assert.deepEqual(s.extras, [{ text: "ok", done: true }]);
  assert.deepEqual(stateFromDict([]).recipes, []);
  assert.deepEqual(stateFromDict(null).recipes, []);
});

test("prune", () => {
  const s = newState();
  addRecipe(s, "a", 1); addRecipe(s, "b", 1);
  s.checked = new Set(["x|mass", "y|none"]);
  prune(s, new Set(["a"]), new Set(["x|mass"]));
  assert.deepEqual(s.recipes.map((e) => e.id), ["a"]);
  assert.deepEqual([...s.checked], ["x|mass"]);
});
