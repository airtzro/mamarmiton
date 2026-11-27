import { api } from "./api.js";
import { hit } from "./usage.js";
import { h, icon, clear } from "./dom.js";
import { allRecipes, applyEntry, canEdit, getRecipe, newId, photos, saveRecipe, setFavorite, state, sync } from "./state.js";
import { COMMON_UNITS, joinAmountUnit, servingsText, splitAmountUnit } from "./quantities.js";
import { COOKING_TYPES, DISH_TYPES, MAX_TAGS, MAX_TAG_LEN, fold, normalizeTags, tagCounts } from "./search.js";
import { busy, confirmDialog, openDialog, toast } from "./ui.js";

const stripStepNumber = (line) =>
  line.replace(/^\s*(?:[ée]tape\s*)?\d{1,2}\s*[).:\-–]\s*|^\s*[-•*]\s*/i, "").trim();

const toInt = (value, fallback, min) => {
  const m = /\d+/.exec(String(value ?? ""));
  return m ? Math.max(min, Number(m[0])) : fallback;
};

/** "1 h 30", "1h30", "45" -> minutes (same idea as models.parse_minutes). */
export function parseMinutes(value) {
  const text = String(value ?? "").normalize("NFD").replace(/\p{Mn}/gu, "").toLowerCase().replace(",", ".").trim();
  if (!text) return 0;
  const m = /^(\d+(?:\.\d+)?)\s*h(?:eures?|rs?)?\s*(?:et\s*)?(\d+)?\s*(?:min(?:utes?)?|mn)?$/.exec(text);
  if (m) return Math.round(Number(m[1]) * 60) + Number(m[2] || 0);
  const n = /\d+(?:\.\d+)?/.exec(text);
  return n ? Math.round(Number(n[0])) : 0;
}

function field(label, input, { hint, id } = {}) {
  id = id || `e-${Math.random().toString(36).slice(2, 8)}`;
  input.id = id;
  return h("div", { class: "field" }, h("label", { for: id, text: label }), input, hint ? h("p", { class: "hint", text: hint }) : null);
}

function select(options, value) {
  return h("select", {}, options.map((o) => h("option", { value: o, text: o, selected: o === value })));
}

function summaryOf(recipe) {
  const ings = (recipe.ingredients || []).map((i) => (i["quantité"] ? `${i["quantité"]} ${i.nom}` : i.nom));
  return h("div", { class: "stack small" },
    h("h3", { class: "serif", text: recipe.nom || "Recette sans nom" }),
    h("p", { class: "muted", text: `${recipe.type_plat || ""} · ${recipe.type_cuisson || ""} · préparation ${recipe.temps || 0} min · cuisson ${recipe.temps_cuisson || 0} min · ${servingsText(recipe.pour || 0, recipe.pour_unite, true)}` }),
    h("h4", { text: "Ingrédients" }), ings.length ? h("ul", {}, ings.map((t) => h("li", { text: t }))) : h("p", { class: "muted", text: "Aucun." }),
    h("h4", { text: "Étapes" }), (recipe.etapes || []).length ? h("ol", {}, recipe.etapes.map((t) => h("li", { text: t }))) : h("p", { class: "muted", text: "Aucune." }),
    recipe.notes ? [h("h4", { text: "Notes" }), h("p", { text: recipe.notes })] : null);
}

/** Show both versions of a recipe; resolves "mine" | "theirs" | "cancel". */
function askConflict(mine, current) {
  return new Promise((resolve) => {
    let answer = "cancel";
    openDialog({
      title: "Cette recette a changé", wide: true, onClose: () => resolve(answer),
      build: (close) => {
        const pick = (value) => () => { answer = value; close(); };
        return h("div", { class: "stack" },
          h("p", { text: "Quelqu'un d'autre a enregistré cette recette pendant que vous la modifiiez. Quelle version voulez-vous garder ?" }),
          h("div", { class: "compare" },
            h("section", { class: "card pad" }, h("h2", { class: "serif", text: "Ma version" }), summaryOf(mine)),
            h("section", { class: "card pad" }, h("h2", { class: "serif", text: "Version enregistrée" }), summaryOf(current.recipe))),
          h("div", { class: "row end wrap" },
            h("button", { class: "btn btn-secondary", type: "button", onClick: pick("cancel"), text: "Je décide plus tard" }),
            h("button", { class: "btn btn-secondary", type: "button", onClick: pick("theirs"), text: "Garder la version enregistrée" }),
            h("button", { class: "btn btn-primary", type: "button", onClick: pick("mine"), text: "Garder ma version" })));
      },
    });
  });
}

/**
 * Create (id = null) or edit a recipe. `done(id)` navigates away.
 * `draft` (new recipes only) prefills the form: {recipe, warnings, dishPhoto} from importlogic.draftFromImport.
 */
export function renderEditor(main, id, { done, cancel, draft = null }) {
  const existing = id ? getRecipe(id) : null;
  if (id && (!existing || !canEdit(existing))) { cancel(); return { update() {} }; }
  if (id) draft = null;
  // once saved or cancelled, navigating away must not ask "abandon changes?"
  let left = false;
  const doneRaw = done, cancelRaw = cancel;
  done = (rid) => { left = true; doneRaw(rid); };
  cancel = () => { left = true; cancelRaw(); };
  const entry = id ? state.entries.get(id) : null;
  const base = entry ? structuredClone(entry.recipe) : draft && draft.recipe.source ? { source: draft.recipe.source } : {};
  const init = existing || (draft ? draft.recipe : null);  // what the fields start with
  const recipeId = id || newId();
  document.title = `${id ? "Modifier" : "Nouvelle recette"} · Mamarmiton`;

  const error = h("p", { class: "form-error", role: "alert" });
  const nom = h("input", { type: "text", maxlength: 200, value: init?.nom ?? "", placeholder: "ex. Gâteau au chocolat de mamie", autocomplete: "off", enterkeyhint: "next" });
  const dish = select(DISH_TYPES, init?.type_plat ?? "Plat");
  const cooking = select(COOKING_TYPES, init?.type_cuisson ?? "Sans cuisson");
  const prep = h("input", { type: "text", inputmode: "numeric", value: init?.temps ? String(init.temps) : "", placeholder: "ex. 20" });
  const cook = h("input", { type: "text", inputmode: "numeric", value: init?.temps_cuisson ? String(init.temps_cuisson) : "", placeholder: "ex. 45" });
  const pour = h("input", { type: "number", min: 1, max: 99, inputmode: "numeric", value: String(init?.pour ?? 4), "aria-label": "Nombre" });
  const unite = h("input", { type: "text", maxlength: 40, value: init?.pour_unite ?? "", placeholder: "personnes", "aria-label": "Unité (personnes, crêpes, parts…)" });
  const fav = h("input", { type: "checkbox", checked: existing?.favori ?? false });
  const steps = h("textarea", { rows: 8, placeholder: "Une étape par ligne" });
  steps.value = (init?.etapes ?? []).join("\n");
  const notes = h("textarea", { rows: 4, maxlength: 20000, placeholder: "Un conseil, une variante, un souvenir…" });
  notes.value = init?.notes ?? "";
  const source = h("input", { type: "text", maxlength: 500, value: init?.source ?? base.source ?? "", placeholder: "ex. Mamie Jeanne, ou https://…", autocomplete: "off" });

  // ---- tags: type + Enter or comma adds a chip
  let tags = normalizeTags(init?.tags);
  const tagInput = h("input", { type: "text", maxlength: MAX_TAG_LEN * 3, placeholder: "ex. Noël", autocomplete: "off",
    enterkeyhint: "done", "aria-label": "Ajouter une étiquette" });
  const tagChips = h("div", { class: "chip-wrap" });
  const tagSuggest = h("div", { class: "chip-wrap", role: "group", "aria-label": "Étiquettes déjà utilisées" });
  const knownTags = tagCounts(allRecipes());
  function addTags(...names) {
    const before = tags.length;
    tags = normalizeTags([...tags, ...names]);
    if (tags.length > before) hit("tag_add");
    drawTags();
  }
  function drawTags() {
    clear(tagChips).append(...tags.map((t) => h("button", { class: "chip-btn chip-tag is-on", type: "button",
      "aria-label": `Retirer l'étiquette ${t}`, onClick: () => { tags = tags.filter((x) => fold(x) !== fold(t)); drawTags(); } },
    `#${t}`, icon("close", 16))));
    const typed = fold(tagInput.value);
    const have = new Set(tags.map(fold));
    const options = knownTags.filter(({ tag }) => !have.has(fold(tag)) && fold(tag).includes(typed)).slice(0, 12);
    clear(tagSuggest).append(...options.map(({ tag }) => h("button", { class: "chip-btn chip-tag", type: "button",
      onClick: () => { tagInput.value = ""; addTags(tag); } }, icon("plus", 16), tag)));
    tagInput.disabled = tags.length >= MAX_TAGS;
  }
  tagInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();  // Enter adds the tag, it must not submit the form
    const text = tagInput.value;
    tagInput.value = "";
    addTags(...text.split(","));
  });
  tagInput.addEventListener("input", () => {
    if (tagInput.value.includes(",")) {
      const done = tagInput.value.slice(0, tagInput.value.lastIndexOf(","));
      tagInput.value = tagInput.value.slice(tagInput.value.lastIndexOf(",") + 1).trimStart();
      addTags(...done.split(","));
    } else drawTags();
  });
  drawTags();

  // ---- ingredient rows
  const datalist = h("datalist", { id: "units" }, COMMON_UNITS.map((u) => h("option", { value: u })));
  const rowsBox = h("div", { class: "ing-rows" });
  const rows = [];
  function addRow(ingredient, focus = false) {
    const [amountText, unitText] = ingredient ? splitAmountUnit(ingredient.quantite) : ["", ""];
    const name = h("input", { type: "text", "aria-label": "Ingrédient", placeholder: "ex. Farine", value: ingredient?.nom ?? "", enterkeyhint: "next" });
    const amount = h("input", { type: "text", inputmode: "decimal", "aria-label": "Quantité", placeholder: "ex. 500", value: amountText, enterkeyhint: "next" });
    const unit = h("input", { type: "text", "aria-label": "Unité", placeholder: "unité", list: "units", value: unitText, autocomplete: "off", enterkeyhint: "next" });
    const remove = h("button", { class: "btn btn-icon btn-ghost", type: "button", "aria-label": "Retirer cet ingrédient" }, icon("close", 18));
    const row = h("div", { class: "ing-row" }, name, h("div", { class: "ing-qty" }, amount, unit), remove);
    const entry_ = { row, name, amount, unit };
    remove.addEventListener("click", () => { rows.splice(rows.indexOf(entry_), 1); row.remove(); (rows.at(-1)?.name || addBtn).focus(); });
    const go = (next) => (e) => { if (e.key === "Enter") { e.preventDefault(); next(); } };
    name.addEventListener("keydown", go(() => amount.focus()));
    amount.addEventListener("keydown", go(() => unit.focus()));
    unit.addEventListener("keydown", go(() => addRow(null, true)));
    rows.push(entry_);
    rowsBox.append(row);
    if (focus) name.focus();
  }
  const addBtn = h("button", { class: "btn btn-secondary", type: "button", onClick: () => addRow(null, true) }, icon("plus", 18), "Ajouter un ingrédient");
  for (const ing of init?.ingredients ?? []) addRow(ing);
  if (!rows.length) addRow(null);

  // ---- photos
  const removedImages = new Set();
  const pending = [];  // {file, url}
  const photoGrid = h("div", { class: "photo-grid" });
  function drawPhotos() {
    clear(photoGrid);
    for (const img of existing?.images ?? []) {
      const gone = removedImages.has(img.id);
      const picture = h("img", { alt: "", class: "cover-img" });
      photos.get(recipeId, img.id).then((url) => { picture.src = url; }, () => {});
      photoGrid.append(h("div", { class: `photo${gone ? " is-removed" : ""}` }, picture,
        h("button", { class: "btn btn-small btn-secondary", type: "button",
          onClick: () => { if (gone) removedImages.delete(img.id); else removedImages.add(img.id); drawPhotos(); },
          text: gone ? "Garder" : "Retirer" })));
    }
    pending.forEach((p, i) => {
      photoGrid.append(h("div", { class: "photo" }, h("img", { alt: "", class: "cover-img", src: p.url }),
        h("button", { class: "btn btn-small btn-secondary", type: "button",
          onClick: () => { URL.revokeObjectURL(p.url); pending.splice(i, 1); drawPhotos(); }, text: "Retirer" })));
    });
  }
  const pick = (capture) => {
    const input = h("input", { type: "file", accept: "image/*", class: "sr-only", tabindex: "-1", "aria-hidden": "true", multiple: !capture });
    if (capture) input.setAttribute("capture", "environment");
    input.addEventListener("change", () => {
      for (const file of input.files) pending.push({ file, url: URL.createObjectURL(file) });
      input.value = "";
      drawPhotos();
    });
    return input;
  };
  const cameraInput = pick(true), libraryInput = pick(false);
  // the photo of the dish found in an imported page: sent with the recipe when it is saved
  if (draft && draft.dishPhoto) pending.push({ file: draft.dishPhoto, url: URL.createObjectURL(draft.dishPhoto) });
  drawPhotos();

  // ---- collect / save
  function collect() {
    const ingredients = [];
    for (const r of rows) {
      const name = r.name.value.trim();
      const qty = joinAmountUnit(r.amount.value, r.unit.value);
      if (!name && !qty) continue;
      ingredients.push({ nom: name || qty, "quantité": name ? qty : "" });  // as the desktop editor does
    }
    return {
      ...base,
      id: recipeId,
      nom: nom.value.trim(),
      temps: parseMinutes(prep.value),
      temps_cuisson: parseMinutes(cook.value),
      pour: toInt(pour.value, 4, 1),
      pour_unite: unite.value.trim().replace(/\s+/g, " "),
      type_plat: dish.value,
      type_cuisson: cooking.value,
      ingredients,
      etapes: steps.value.split("\n").map(stripStepNumber).filter(Boolean),
      notes: notes.value,
      source: source.value.trim().replace(/\s+/g, " "),
      tags: normalizeTags([...tags, ...tagInput.value.split(",")]),
      cree_le: base.cree_le || new Date().toISOString().slice(0, 19),
      modifie_le: new Date().toISOString().slice(0, 19),
    };
  }
  const snapshot = () => JSON.stringify([{ ...collect(), modifie_le: "", cree_le: "" }, fav.checked, pending.length, [...removedImages]]);
  const initial = draft ? "" : snapshot();  // an imported recipe is never thrown away without asking

  const saveLabel = h("span", { text: "Enregistrer" });
  const saveBtn = h("button", { class: "btn btn-primary btn-big", type: "submit" }, icon("check", 20), saveLabel);
  async function save(event) {
    event.preventDefault();
    error.textContent = "";
    const recipe = collect();
    if (!recipe.nom) { error.textContent = "Donnez un nom à la recette."; nom.focus(); return; }
    await busy(saveBtn, async () => {
      try {
        let result;
        let baseVersion = entry ? entry.version : null;
        for (;;) {
          try {
            const saved = await saveRecipe(recipeId, recipe, baseVersion);
            hit(existing ? "recipe_edit" : "recipe_create");
            if (saved.queued) {
              if (pending.length || removedImages.size) toast("Les photos n'ont pas pu être modifiées hors ligne : ajoutez-les une fois la connexion revenue.", "error");
              if (fav.checked !== (existing?.favori ?? false)) await setFavorite(recipeId, fav.checked);
              toast("Recette enregistrée sur cet appareil. Elle sera envoyée au retour de la connexion.", "ok");
              pending.forEach((p) => URL.revokeObjectURL(p.url));
              pending.length = 0;
              done(recipeId);
              return;
            }
            result = saved.entry;
            break;
          } catch (e) {
            if (e.status !== 409 || !e.detail || typeof e.detail !== "object" || !e.detail.current) throw e;
            hit("sync_conflict_shown");
            const choice = await askConflict(recipe, e.detail.current);
            if (choice === "cancel") return;
            if (choice === "theirs") {
              applyEntry(e.detail.current);
              toast("Version enregistrée conservée. Vos changements ont été abandonnés.", "ok");
              done(recipeId);
              return;
            }
            baseVersion = e.detail.current.version;  // keep mine: save on top of the newer version
          }
        }
        applyEntry(result);
        if (fav.checked !== (existing?.favori ?? false)) {
          try {
            await setFavorite(recipeId, fav.checked);
          } catch (e) { toast(e.message, "error"); }
        }
        let failed = 0, n = 0;
        for (const imageId of removedImages) {
          try { await api.deleteImage(recipeId, imageId); photos.forget(recipeId, imageId); } catch { failed++; }
        }
        for (const p of pending) {
          saveLabel.textContent = `Envoi de la photo ${++n} sur ${pending.length}…`;
          try { await api.uploadImage(recipeId, p.file); } catch (e) { failed++; toast(e.message, "error"); }
        }
        pending.forEach((p) => URL.revokeObjectURL(p.url));
        pending.length = 0;
        await sync().catch(() => {});
        toast(failed ? "Recette enregistrée, mais une photo n'a pas pu être modifiée." : "Recette enregistrée.", failed ? "error" : "ok");
        done(recipeId);
      } catch (e) {
        error.textContent = e.message;
      } finally {
        saveLabel.textContent = "Enregistrer";
      }
    });
  }

  const dirty = () => !left && snapshot() !== initial;
  async function confirmLeave() {
    if (!dirty()) return true;
    return confirmDialog({ title: draft ? "Abandonner la recette importée ?" : "Abandonner les changements ?",
      message: draft ? "La recette lue ne sera pas enregistrée." : "Ce que vous avez écrit ne sera pas enregistré.",
      confirmText: "Abandonner", cancelText: "Continuer à modifier", danger: true });
  }
  async function leave() {
    if (!(await confirmLeave())) return;
    pending.forEach((p) => URL.revokeObjectURL(p.url));
    cancel();
  }

  const notice = draft ? h("div", { class: "import-notice", role: "status", tabindex: "-1" },
    icon("scan", 24),
    h("div", { class: "import-notice-text" },
      h("p", { class: "import-notice-title", text: "Recette lue automatiquement : vérifiez les quantités et les étapes avant d'enregistrer." }),
      draft.warnings.length ? h("ul", {}, draft.warnings.map((w) => h("li", { text: w }))) : null,
      draft.dishPhoto ? h("p", { text: "La photo du plat a été ajoutée en bas, dans « Photos »." }) : null)) : null;

  const form = h("form", { class: "editor stack", novalidate: true, onSubmit: save },
    h("header", { class: "editor-head" },
      h("h1", { class: "serif", text: id ? "Modifier la recette" : draft ? "Recette importée" : "Nouvelle recette" }),
      h("p", { class: "muted", text: "Seul le nom est obligatoire. Le reste peut être complété plus tard." })),
    notice,
    h("section", { class: "card pad stack" },
      field("Nom de la recette *", nom, { id: "e-nom" }),
      h("div", { class: "grid-2" }, field("Type de plat", dish), field("Mode de cuisson", cooking)),
      h("div", { class: "grid-3" },
        field("Préparation (minutes)", prep, { hint: "Ex. 20, ou 1h30" }),
        field("Cuisson (minutes)", cook),
        h("div", { class: "field" }, h("label", { for: "e-pour", text: "Pour" }),
          h("div", { class: "pour-row" }, Object.assign(pour, { id: "e-pour" }), unite),
          h("p", { class: "hint", text: "Laissez « personnes », ou écrivez crêpes, parts, verres…" }))),
      h("label", { class: "check" }, fav, h("span", { text: "Recette favorite" }))),
    h("section", { class: "card pad stack" },
      h("h2", { class: "serif", text: "Étiquettes" }),
      h("p", { class: "hint", text: "Pour retrouver la recette : Noël, rapide, sans gluten… Tapez un mot puis Entrée (ou une virgule)." }),
      tagInput, tagChips, tagSuggest),
    h("section", { class: "card pad stack" },
      h("h2", { class: "serif", text: "Ingrédients" }),
      h("p", { class: "hint", text: "Un ingrédient par ligne : son nom, la quantité (ex. 500 ou 1/2) et l'unité." }),
      datalist, rowsBox, h("div", {}, addBtn)),
    h("section", { class: "card pad stack" },
      h("h2", { class: "serif", text: "Préparation" }),
      field("Les étapes", steps, { hint: "Une étape par ligne, sans numéro." })),
    h("section", { class: "card pad stack" },
      h("h2", { class: "serif", text: "Le petit mot du chef" }),
      field("Notes", notes),
      field("Source (facultatif)", source, { hint: "D'où vient la recette ? Un livre, un site, une personne…" })),
    h("section", { class: "card pad stack" },
      h("h2", { class: "serif", text: "Photos" }),
      photoGrid,
      h("div", { class: "row wrap" },
        h("button", { class: "btn btn-secondary", type: "button", onClick: () => cameraInput.click() }, icon("camera", 20), "Prendre une photo"),
        h("button", { class: "btn btn-secondary", type: "button", onClick: () => libraryInput.click() }, icon("image", 20), "Choisir une photo"),
        cameraInput, libraryInput)),
    h("div", { class: "save-bar" }, error,
      h("div", { class: "save-bar-buttons" },
        h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: leave, text: "Annuler" }), saveBtn)));
  clear(main).append(h("div", { class: "container narrow" }, form));
  if (notice) notice.focus(); else nom.focus();  // on a phone the keyboard would hide the notice
  return { update() {}, dirty, confirmLeave, discard() { left = true; } };
}
