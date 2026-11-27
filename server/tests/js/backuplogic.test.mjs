import test from "node:test";
import assert from "node:assert/strict";
import { backupFileName, importSummary } from "../../mamarmiton_server/web/backuplogic.js";

test("summary wording", () => {
  assert.equal(importSummary({ added: 12, skipped: 3, invalid: 0, photos: 0 }), "12 recettes ajoutées, 3 déjà présentes.");
  assert.equal(importSummary({ added: 1, skipped: 1, invalid: 1, photos: 1 }), "1 recette ajoutée (1 photo), 1 déjà présente, 1 illisible.");
  assert.equal(importSummary({ added: 0, skipped: 5, invalid: 2, photos: 0 }), "5 déjà présentes, 2 illisibles.");
  assert.equal(importSummary({ added: 2, skipped: 0, invalid: 0, photos: 4 }), "2 recettes ajoutées (4 photos).");
});

test("summary: nothing, or junk", () => {
  assert.equal(importSummary({ added: 0, skipped: 0, invalid: 0, photos: 0 }), "Aucune recette trouvée dans ce fichier.");
  assert.equal(importSummary(null), "Aucune recette trouvée dans ce fichier.");
  assert.equal(importSummary({ added: "x", skipped: -2 }), "Aucune recette trouvée dans ce fichier.");
});

test("file name uses the local date", () => {
  assert.equal(backupFileName(new Date(2026, 0, 5)), "mamarmiton-sauvegarde-2026-01-05.zip");
  assert.equal(backupFileName(new Date(2026, 10, 27, 23, 59)), "mamarmiton-sauvegarde-2026-11-27.zip");
});
