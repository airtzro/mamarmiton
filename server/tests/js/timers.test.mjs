import test from "node:test";
import assert from "node:assert/strict";
import { findDurations, formatClock, formatShort, secondsLeft, textIn } from "../../mamarmiton_server/web/timers.js";

const single = [
  ["Cuire 25 minutes.", 1500], ["Cuire 25 min à feu doux.", 1500], ["Laisser reposer 1 h 30.", 5400],
  ["Laisser reposer 1h30.", 5400], ["Cuire 1 heure 30 minutes.", 5400], ["Cuire 2 heures.", 7200],
  ["Cuire 1 heure.", 3600], ["Cuire 1,5 heure.", 5400], ["Saisir 30 secondes.", 30], ["Saisir 30 sec.", 30],
  ["Cuire 10 à 15 minutes.", 900], ["Cuire 10-15 min.", 900], ["Cuire 1 à 2 heures.", 7200],
  ["Cuire une heure.", 3600], ["Cuire une heure et demie.", 5400], ["Cuire une demi-heure.", 1800],
  ["Cuire un quart d'heure.", 900], ["Cuire un quart d’heure.", 900], ["Cuire trois quarts d'heure.", 2700],
  ["Cuire vingt minutes.", 1200], ["CUIRE 25 MINUTES", 1500],
];
for (const [text, seconds] of single) {
  test(`single: ${text}`, () => assert.deepEqual(findDurations(text).map((d) => d.seconds), [seconds]));
}

test("spans", () => {
  let text = "Cuire 10 à 15 minutes puis servir.";
  assert.equal(textIn(findDurations(text)[0], text), "10 à 15 minutes");
  text = "Attendre 1 h 30 avant de servir.";
  assert.equal(textIn(findDurations(text)[0], text), "1 h 30");
});

test("several in reading order", () => {
  const found = findDurations("Cuire 5 minutes, puis laisser reposer 1 heure et enfin 30 secondes au four.");
  assert.deepEqual(found.map((d) => d.seconds), [300, 3600, 30]);
  assert.deepEqual(found.map((d) => d.start), found.map((d) => d.start).sort((a, b) => a - b));
});

test("no overlap", () => {
  const found = findDurations("Cuire 1 h 30 puis 10 à 15 minutes.");
  assert.deepEqual(found.map((d) => d.seconds), [5400, 900]);
  assert.ok(found[0].end <= found[1].start);
});

for (const text of ["", "Mélanger les ingrédients.", "Préchauffer le four à 180°C.", "Ajouter 3 pommes et 200 g de sucre.",
  "Prendre 2 heureux convives.", "Servir avec 5 mini-tartes.", "Une minuscule pincée de sel.", "Utiliser 0 minute.",
  "Cuire 1/2 heure.", "5 hamburgers", "5 secs"]) {
  test(`no duration: ${text}`, () => assert.deepEqual(findDurations(text), []));
}

test("temperature vs time and boundaries", () => {
  assert.deepEqual(findDurations("Cuire à 180 °C pendant 25 minutes.").map((d) => d.seconds), [1500]);
  assert.deepEqual(findDurations("Cuire 5 min.").map((d) => d.seconds), [300]);
  assert.deepEqual(findDurations("Cuire 5 minutes!").map((d) => d.seconds), [300]);
});

test("formatClock", () => {
  for (const [s, c] of [[0, "00:00"], [5, "00:05"], [1499, "24:59"], [3600, "1:00:00"], [5400, "1:30:00"], [-3, "00:00"]]) {
    assert.equal(formatClock(s), c);
  }
});

test("formatShort", () => {
  for (const [s, c] of [[30, "30 s"], [1500, "25 min"], [5400, "1 h 30"], [7200, "2 h"], [90, "1 min 30"], [3660, "1 h 01"]]) {
    assert.equal(formatShort(s), c);
  }
});

test("secondsLeft is timestamp based", () => {
  assert.equal(secondsLeft(10_000, 0), 10);
  assert.equal(secondsLeft(10_001, 0), 11);  // rounds up: 00:00 only when really over
  assert.equal(secondsLeft(10_000, 9_999_999), 0);
  assert.equal(secondsLeft(5_000, 4_000 + 1_499_000), 0);
});
