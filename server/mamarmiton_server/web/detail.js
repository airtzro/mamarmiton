import { api } from "./api.js";
import { h, icon, clear } from "./dom.js";
import { canEdit, deleteRecipe, displayName, getRecipe, photos, state, sync } from "./state.js";
import { frac, scaleQuantity } from "./quantities.js";
import { formatMinutes } from "./search.js";
import { busy, confirmDialog, openDialog, toast } from "./ui.js";
import { cover, dishClass, toggleFavorite } from "./home.js";
import { openShareDialog } from "./share.js";

const servingsMemo = new Map();  // recipe id -> chosen number of people (kept across refreshes)
let wakeLock = null;

async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
    else if (wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch { /* not allowed or not supported: the screen may simply dim */ }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && document.body.classList.contains("cooking")) keepAwake(true);
});

export function leaveCookMode() {
  document.body.classList.remove("cooking");
  keepAwake(false);
}

function openLightbox(recipe, start) {
  let index = start;
  const img = h("img", { class: "lightbox-img", alt: "" });
  const counter = h("p", { class: "lightbox-count", "aria-live": "polite" });
  const show = async (i) => {
    index = (i + recipe.images.length) % recipe.images.length;
    counter.textContent = `${recipe.nom} · photo ${index + 1} sur ${recipe.images.length}`;
    try { img.src = await photos.get(recipe.id, recipe.images[index].id); } catch { toast("Photo introuvable.", "error"); }
  };
  const dlg = openDialog({
    title: "Photos", label: `Photos de ${recipe.nom}`, wide: true,
    build: () => {
      const nav = recipe.images.length > 1;
      const prev = h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Photo précédente", onClick: () => show(index - 1) }, icon("back"));
      const next = h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Photo suivante", onClick: () => show(index + 1) }, icon("next"));
      return h("div", { class: "lightbox" }, img, counter, nav ? h("div", { class: "row center" }, prev, next) : null);
    },
  });
  dlg.el.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") show(index - 1);
    if (e.key === "ArrowRight") show(index + 1);
  });
  show(index);
}

async function addPhotos(recipe, files, button) {
  await busy(button, async () => {
    let done = 0;
    for (const file of files) {
      try { await api.uploadImage(recipe.id, file); done++; } catch (e) { toast(e.message, "error"); }
    }
    if (done) { await sync().catch(() => {}); toast(done > 1 ? "Photos ajoutées." : "Photo ajoutée.", "ok"); }
  });
}

/** Renders one recipe. Returns {update()} to redraw after a sync (servings are kept). */
export function renderDetail(main, id, { onEdit, onGone, onChange }) {
  let recipe = getRecipe(id);
  const checked = new Set();   // ticked ingredients and finished steps (not saved)
  const done = new Set();

  function draw() {
    recipe = getRecipe(id);
    if (!recipe) { onGone(); return; }
    document.title = `${recipe.nom} · Mamarmiton`;
    const servings = servingsMemo.get(id) ?? recipe.pour;
    const editable = canEdit(recipe);
    const cooking = document.body.classList.contains("cooking");

    const back = h("a", { class: "btn btn-ghost", href: "#/" }, icon("back", 20), h("span", { text: "Toutes les recettes" }));
    const favBtn = h("button", { class: `btn btn-secondary${recipe.favori ? " is-on" : ""}`, type: "button", "aria-pressed": String(recipe.favori),
      onClick: () => toggleFavorite(recipe, onChange) }, icon(recipe.favori ? "star-filled" : "star", 20),
    h("span", { class: "btn-label", text: recipe.favori ? "Dans mes favoris" : "Ajouter aux favoris" }));
    const cookBtn = h("button", { class: "btn btn-secondary", type: "button", "aria-pressed": String(cooking),
      onClick: () => { const on = !document.body.classList.contains("cooking"); document.body.classList.toggle("cooking", on); keepAwake(on); draw(); window.scrollTo(0, 0); } },
    icon("chef", 20), h("span", { class: "btn-label", text: cooking ? "Quitter le mode cuisine" : "Mode cuisine" }));
    const actions = h("div", { class: "actions" }, cooking ? null : favBtn, cookBtn);
    if (recipe.access === "owner" && !cooking) {
      actions.append(h("button", { class: "btn btn-secondary", type: "button", onClick: () => openShareDialog({ recipe }) },
        icon("share", 20), h("span", { class: "btn-label", text: "Partager" })));
    }
    if (editable && !cooking) {
      actions.append(h("button", { class: "btn btn-primary", type: "button", onClick: () => onEdit(id) },
        icon("pencil", 20), h("span", { class: "btn-label", text: "Modifier" })));
    }
    if (recipe.access === "owner" && !cooking) {
      const del = h("button", { class: "btn btn-danger", type: "button" }, icon("trash", 20), h("span", { class: "btn-label", text: "Supprimer" }));
      del.addEventListener("click", async () => {
        const ok = await confirmDialog({ title: "Supprimer cette recette ?", danger: true, confirmText: "Supprimer",
          message: `« ${recipe.nom} » sera supprimée définitivement, avec ses photos.` });
        if (!ok) return;
        await busy(del, async () => {
          try {
            const { queued } = await deleteRecipe(id);
            servingsMemo.delete(id);
            toast(queued ? "Recette supprimée sur cet appareil. Elle le sera partout au retour de la connexion." : "Recette supprimée.", "ok");
            location.hash = "#/";
          } catch (e) { toast(e.message, "error"); }
        });
      });
      actions.append(del);
    }

    // ---- hero
    const hero = h("section", { class: "hero card" });
    const gallery = h("div", { class: "gallery" });
    if (recipe.images.length) {
      const main_ = cover(recipe, "hero");
      main_.setAttribute("role", "button");
      main_.setAttribute("tabindex", "0");
      main_.setAttribute("aria-label", "Agrandir la photo");
      main_.addEventListener("click", () => openLightbox(recipe, 0));
      main_.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openLightbox(recipe, 0); } });
      gallery.append(main_);
      if (recipe.images.length > 1) {
        gallery.append(h("div", { class: "thumbs" }, recipe.images.map((img, i) => {
          const t = h("button", { class: "thumb", type: "button", "aria-label": `Photo ${i + 1} sur ${recipe.images.length}`, onClick: () => openLightbox(recipe, i) });
          const picture = h("img", { alt: "", class: "cover-img" });
          photos.get(recipe.id, img.id).then((url) => { picture.src = url; }, () => {});
          t.append(picture);
          return t;
        })));
      }
    } else {
      gallery.append(cover(recipe, "hero"));
    }
    if (editable) {
      const input = h("input", { type: "file", accept: "image/*", multiple: true, class: "sr-only", "aria-label": "Ajouter une photo" });
      const addBtn = h("button", { class: "btn btn-secondary btn-small", type: "button", onClick: () => input.click() }, icon("camera", 18), "Ajouter une photo");
      input.addEventListener("change", () => { const files = [...input.files]; input.value = ""; if (files.length) addPhotos(recipe, files, addBtn); });
      gallery.append(h("div", { class: "row" }, addBtn, input));
    }

    const facts = [["clock", "Préparation", recipe.temps ? formatMinutes(recipe.temps) : "—"],
      ["pot", "Cuisson", recipe.temps_cuisson ? formatMinutes(recipe.temps_cuisson) : "—"],
      ["clock", "Total", recipe.temps + recipe.temps_cuisson ? formatMinutes(recipe.temps + recipe.temps_cuisson) : "—"],
      ["users", "Pour", `${recipe.pour} pers.`]];
    const info = h("div", { class: "hero-info" },
      h("p", {}, h("span", { class: `chip chip-dish ${dishClass(recipe.type_plat)}`, text: recipe.type_plat }), " ",
        h("span", { class: "chip", text: recipe.type_cuisson })),
      recipe.tags.length
        ? h("p", { class: "tag-list" }, recipe.tags.map((tag) => h("button", {
          class: "chip chip-btn chip-tag", type: "button", title: `Voir les recettes « ${tag} »`,
          onClick: () => { state.filters.text = ""; state.filters.category = ""; state.filters.tags = [tag]; location.hash = "#/"; },
          text: `#${tag}` })))
        : null,
      h("h1", { class: "serif", text: recipe.nom }),
      recipe.access !== "owner"
        ? h("p", { class: "owner-note" }, icon("eye", 18), `Recette de ${displayName(recipe.owner)}`,
          recipe.access === "read" ? " · vous pouvez la lire, pas la modifier." : " · vous pouvez la modifier.")
        : null,
      recipe.source ? h("p", { class: "muted italic", text: `Importée depuis « ${recipe.source} »` }) : null,
      h("dl", { class: "facts" }, facts.map(([ic, label, value]) =>
        h("div", { class: "fact" }, h("dt", {}, icon(ic, 16), label), h("dd", { class: "serif", text: value })))));
    hero.append(gallery, info);

    // ---- ingredients
    const factor = frac(servings, Math.max(1, recipe.pour));
    const stepper = h("div", { class: "stepper", role: "group", "aria-label": "Nombre de personnes" },
      h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Une personne de moins", disabled: servings <= 1,
        onClick: () => { servingsMemo.set(id, Math.max(1, servings - 1)); draw(); } }, icon("minus")),
      h("output", { class: "stepper-value", "aria-live": "polite", text: `${servings} personne${servings > 1 ? "s" : ""}` }),
      h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Une personne de plus", disabled: servings >= 99,
        onClick: () => { servingsMemo.set(id, Math.min(99, servings + 1)); draw(); } }, icon("plus")));
    const ingList = recipe.ingredients.length
      ? h("ul", { class: "ingredients" }, recipe.ingredients.map((ing, i) => {
        const box = h("input", { type: "checkbox", checked: checked.has(i), onChange: (e) => { if (e.target.checked) checked.add(i); else checked.delete(i); row.classList.toggle("is-done", e.target.checked); } });
        const qty = scaleQuantity(ing.quantite, factor);
        const row = h("li", { class: checked.has(i) ? "is-done" : "" },
          h("label", {}, box, qty ? h("span", { class: "qty", text: qty }) : null, h("span", { class: "ing-name", text: ing.nom })));
        return row;
      }))
      : h("p", { class: "muted", text: "Aucun ingrédient pour l'instant." });
    const ingredients = h("section", { class: "card pad" },
      h("h2", { class: "serif", text: "Ingrédients" }), stepper,
      servings !== recipe.pour
        ? h("button", { class: "btn btn-link", type: "button", onClick: () => { servingsMemo.delete(id); draw(); },
          text: `Revenir à ${recipe.pour} personne${recipe.pour > 1 ? "s" : ""}` })
        : null,
      ingList);

    // ---- steps
    const steps = h("section", { class: "card pad" },
      h("div", { class: "row between" }, h("h2", { class: "serif", text: "Préparation" }),
        recipe.etapes.length ? h("span", { class: "muted", text: `${recipe.etapes.length} étape${recipe.etapes.length > 1 ? "s" : ""}` }) : null),
      recipe.etapes.length
        ? [h("p", { class: "hint", text: "Astuce : touchez une étape quand elle est faite." }),
          h("ol", { class: "steps" }, recipe.etapes.map((text, i) => {
            const badge = h("span", { class: "badge", "aria-hidden": "true" }, done.has(i) ? icon("check", 18) : String(i + 1));
            const button = h("button", { class: `step${done.has(i) ? " is-done" : ""}`, type: "button", "aria-pressed": String(done.has(i)),
              onClick: () => {
                const on = !done.has(i);
                if (on) done.add(i); else done.delete(i);
                button.classList.toggle("is-done", on);
                button.setAttribute("aria-pressed", String(on));
                badge.replaceChildren(...(on ? [icon("check", 18)] : [String(i + 1)]));
              } },
            badge, h("span", { class: "step-text", text }));
            return h("li", {}, button);
          }))]
        : h("p", { class: "muted", text: "Aucune étape pour l'instant." }));
    const notes = recipe.notes.trim()
      ? h("section", { class: "note" }, h("h2", { class: "serif", text: "Le petit mot du chef" }), h("p", { class: "note-text", text: recipe.notes }))
      : null;

    clear(main).append(h("div", { class: `container detail${cooking ? " cook" : ""}` },
      h("div", { class: "detail-bar" }, back, actions),
      cooking ? h("h1", { class: "serif cook-title", text: recipe.nom }) : hero,
      h("div", { class: "detail-body" }, ingredients, h("div", { class: "stack" }, steps, notes))));
  }
  draw();
  return { update: draw };
}
