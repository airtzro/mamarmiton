import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_ACCEPTS, NO_OCR_MESSAGE, acceptAttribute, addFiles, draftFromImport, extensionOf, formatSize, kindOf,
  normalizeStatus, photoFromResult, readyProblem, setPendingDraft, takePendingDraft, urlProblem,
} from "../../mamarmiton_server/web/importlogic.js";

const MB = 1024 * 1024;
const file = (name, size = 100_000, type = "", lastModified = 1) => ({ name, size, type, lastModified });
const status = (over = {}) => normalizeStatus({ available: true, ocr: true, ai: false, max_files: 8, max_mb: 30, accepts: DEFAULT_ACCEPTS, ...over });

test("status: an old server (no answer, not available, junk) means no import", () => {
  assert.equal(normalizeStatus(null), null);
  assert.equal(normalizeStatus({ available: false, ocr: true }), null);
  assert.equal(normalizeStatus("yes"), null);
  const s = normalizeStatus({ available: true });
  assert.deepEqual(s, { ocr: false, ai: false, url: false, maxFiles: 8, maxMb: 30, accepts: DEFAULT_ACCEPTS, aiCredits: null });
  assert.deepEqual(normalizeStatus({ available: true, max_files: -1, max_mb: "x", accepts: [".PDF", "nope", ".txt"] }).accepts, [".pdf", ".txt"]);
});

test("kind and extension", () => {
  assert.equal(extensionOf("Tarte.Tatin.JPG"), ".jpg");
  assert.equal(extensionOf("sans extension"), "");
  assert.equal(kindOf(file("p.heic")), "image");
  assert.equal(kindOf(file("image", 1, "image/jpeg")), "image");  // a camera photo with no extension
  assert.equal(kindOf(file("r.pdf")), "pdf");
  assert.equal(kindOf(file("r.docx")), "doc");
});

test("accept attribute leaves photos and PDFs out when the server has no OCR", () => {
  assert.equal(acceptAttribute(status()), `image/*,${DEFAULT_ACCEPTS.join(",")}`);
  assert.equal(acceptAttribute(status({ ocr: false })), ".txt,.docx,.odt");
});

test("files: accepted, duplicates skipped, empty and unknown types refused", () => {
  const a = file("page1.jpg");
  const { files, errors } = addFiles([a], [a, file("page2.png"), file("vide.pdf", 0), file("tableau.xlsx"), file("photo", 10, "image/jpeg")], status());
  assert.deepEqual(files.map((f) => f.name), ["page1.jpg", "page2.png", "photo"]);
  assert.equal(errors.length, 2);
  assert.match(errors[0], /vide/);
  assert.match(errors[1], /tableau\.xlsx/);
});

test("files: at most max_files, one message for all the extra ones", () => {
  const many = Array.from({ length: 11 }, (_, i) => file(`p${i}.jpg`, 1000, "", i));
  const { files, errors } = addFiles([], many, status({ max_files: 8 }));
  assert.equal(files.length, 8);
  assert.deepEqual(errors, ["8 fichiers au maximum : 3 fichiers n'ont pas été ajoutés."]);
  assert.deepEqual(addFiles(files, [file("x.jpg", 5, "", 99)], status()).errors, ["8 fichiers au maximum : un fichier n'a pas été ajouté."]);
});

test("files: max_mb is for all the files together", () => {
  const s = status({ max_mb: 30 });
  const first = addFiles([], [file("a.jpg", 12 * MB), file("b.jpg", 12 * MB)], s);
  assert.equal(first.files.length, 2);
  const next = addFiles(first.files, [file("c.jpg", 8 * MB), file("d.txt", 2 * MB)], s);
  assert.deepEqual(next.files.map((f) => f.name), ["a.jpg", "b.jpg", "d.txt"]);
  assert.match(next.errors[0], /c\.jpg.*trop lourd.*30 Mo/);
});

test("files: no OCR on the server refuses photos and PDFs with a plain explanation", () => {
  const { files, errors } = addFiles([], [file("p.jpg"), file("r.pdf"), file("r.odt")], status({ ocr: false }));
  assert.deepEqual(files.map((f) => f.name), ["r.odt"]);
  assert.equal(errors.length, 2);
  assert.ok(errors.every((e) => e.endsWith(NO_OCR_MESSAGE)));
});

test("ready: needs a file or some text", () => {
  assert.notEqual(readyProblem([], "  "), "");
  assert.equal(readyProblem([], "Crêpes"), "");
  assert.equal(readyProblem([file("a.jpg")], ""), "");
});

test("sizes in French", () => {
  assert.equal(formatSize(2048), "2 Ko");
  assert.equal(formatSize(10), "1 Ko");
  assert.equal(formatSize(2.34 * MB), "2,3 Mo");
});

test("draft: the server recipe becomes the editor's starting fields", () => {
  const sent = [file("page1.jpg"), file("plat.jpg"), file("notes.pdf")];
  const draft = draftFromImport({
    recipe: {
      nom: "  Tarte aux pommes ", temps: 20, temps_cuisson: "45", pour: 6, type_plat: "Dessert", type_cuisson: "Four",
      ingredients: [{ nom: "Pommes", "quantité": "4" }, { nom: "", "quantité": "" }, "Sucre"],
      etapes: ["Éplucher les pommes.", "", "Cuire 45 min."], notes: "De mamie", tags: ["Goûter", "goûter", 3], source: "Livre rouge",
    },
    dish_photo: 1, method: "ia", warnings: ["Les quantités de la page 2 sont peu lisibles.", ""],
  }, sent);
  assert.deepEqual(draft.recipe, {
    nom: "Tarte aux pommes", temps: 20, temps_cuisson: 45, pour: 6, type_plat: "Dessert", type_cuisson: "Four",
    ingredients: [{ nom: "Pommes", quantite: "4" }, { nom: "Sucre", quantite: "" }],
    etapes: ["Éplucher les pommes.", "Cuire 45 min."], notes: "De mamie", tags: ["Goûter"], source: "Livre rouge",
    favori: false, images: [],
  });
  assert.deepEqual(draft.warnings, ["Les quantités de la page 2 sont peu lisibles."]);
  assert.equal(draft.dishPhoto, sent[1]);
});

test("draft: unknown values fall back to the editor's defaults; a bad dish_photo is ignored", () => {
  const sent = [file("r.pdf"), file("a.jpg")];
  const d = draftFromImport({ recipe: { type_plat: "Goûter", type_cuisson: "Barbecue", pour: 0, temps: -3, ingredients: "x" }, dish_photo: 0 }, sent);
  assert.equal(d.recipe.type_plat, "Plat");
  assert.equal(d.recipe.type_cuisson, "Sans cuisson");
  assert.equal(d.recipe.pour, 4);
  assert.equal(d.recipe.temps, 0);
  assert.deepEqual(d.recipe.ingredients, []);
  assert.equal(d.dishPhoto, null);  // a PDF is not a photo of the dish
  assert.equal(draftFromImport({ recipe: {}, dish_photo: 7 }, sent).dishPhoto, null);
  assert.equal(draftFromImport({ recipe: {}, dish_photo: null }, sent).dishPhoto, null);
  assert.equal(draftFromImport(null).recipe.nom, "");
});

test("the pending draft is taken once", () => {
  setPendingDraft({ recipe: { nom: "A" } });
  assert.equal(takePendingDraft().recipe.nom, "A");
  assert.equal(takePendingDraft(), null);
});

test("status: website import is announced by the server", () => {
  assert.equal(normalizeStatus({ available: true, url: true }).url, true);
  assert.equal(normalizeStatus({ available: true, url: "yes" }).url, false);
  assert.equal(status({ url: true }).url, true);
});

test("urlProblem: only obvious mistakes are caught on the phone, the server checks the rest", () => {
  assert.match(urlProblem(""), /Collez/);
  assert.match(urlProblem("   "), /Collez/);
  assert.match(urlProblem("tarte aux pommes"), /pas une adresse/);
  assert.match(urlProblem("marmiton"), /pas une adresse/);
  assert.equal(urlProblem("https://www.marmiton.org/recettes/recette_tarte_1.aspx"), "");
  assert.equal(urlProblem("  marmiton.org/recettes/x "), "");
});

test("photoFromResult: the photo of a website recipe becomes a File", async () => {
  const bytes = Uint8Array.from([0xff, 0xd8, 0xff, 1, 2, 3]);
  const data = Buffer.from(bytes).toString("base64");
  const photo = photoFromResult({ photo: { content_type: "image/jpeg", data } });
  assert.equal(photo.type, "image/jpeg");
  assert.equal(photo.name, "photo.jpg");
  assert.deepEqual(new Uint8Array(await photo.arrayBuffer()), bytes);
  assert.equal(photoFromResult({ photo: { content_type: "image/png", data } }).name, "photo.png");
  for (const bad of [null, {}, { photo: null }, { photo: { content_type: "text/html", data } },
    { photo: { content_type: "image/jpeg", data: "***" } }, { photo: { content_type: "image/jpeg", data: "" } },
    { photo: { content_type: "image/jpeg" } }]) assert.equal(photoFromResult(bad), null);
});

test("draftFromImport takes the website photo as the dish photo", () => {
  const data = Buffer.from([1, 2, 3]).toString("base64");
  const draft = draftFromImport({ recipe: { nom: "Gratin" }, dish_photo: null, photo: { content_type: "image/jpeg", data } });
  assert.equal(draft.recipe.nom, "Gratin");
  assert.equal(draft.dishPhoto.type, "image/jpeg");
  assert.equal(draftFromImport({ recipe: { nom: "Gratin" }, photo: null }).dishPhoto, null);
});

test("ai credits: shown only when the plan has AI and the server has the model", async () => {
  const { aiCredits, aiCreditsText } = await import("../../mamarmiton_server/web/importlogic.js");
  assert.equal(aiCredits({ ai: true, ai_allowance: 0, ai_left: 0 }), null);  // base
  assert.equal(aiCredits({ ai: false, ai_allowance: 30, ai_left: 12 }), null);  // no model on the server
  assert.equal(aiCredits({ ai: true }), null);  // older server
  assert.deepEqual(aiCredits({ ai: true, ai_allowance: 30, ai_left: 12 }), { left: 12, allowance: 30 });
  assert.match(aiCreditsText({ left: 12, allowance: 30 }), /12 sur 30/);
  assert.match(aiCreditsText({ left: 0, allowance: 30 }), /lecture simple/);
});
