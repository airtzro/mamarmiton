import { api } from "./api.js";
import { hit } from "./usage.js";
import { h, icon, clear } from "./dom.js";
import { canEdit, creditLine, deleteRecipe, displayName, getRecipe, hideRecipe, makeVariant, photos, state, sync } from "./state.js";
import { frac, scaleQuantity, servingsText } from "./quantities.js";
import { formatMinutes } from "./search.js";
import { actionMenu, busy, confirmDialog, openDialog, toast } from "./ui.js";
import { cover, dishClass, toggleFavorite } from "./home.js";
import { openShareDialog } from "./share.js";
import { openKitchen } from "./kitchen.js";
import { timerButtons } from "./timer-tray.js";
import { addRecipeToShopping, servingsInList } from "./courses.js";

const servingsMemo = new Map();  // recipe id -> chosen number of people (kept across refreshes)

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
    if (done) { hit("photo_add"); await sync().catch(() => {}); toast(done > 1 ? "Photos ajoutées." : "Photo ajoutée.", "ok"); }
  });
}

/** Renders one recipe. Returns {update()} to redraw after a sync (servings are kept). */
export function renderDetail(main, id, { onEdit, onGone, onChange }) {
  let recipe = getRecipe(id);
  if (recipe) hit("recipe_open");  // once per opening, not per redraw
  const checked = new Set();   // ticked ingredients and finished steps (not saved)
  const done = new Set();

  function draw() {
    recipe = getRecipe(id);
    if (!recipe) { onGone(); return; }
    document.title = `${recipe.nom} · Mamarmiton`;
    const servings = servingsMemo.get(id) ?? recipe.pour;
    const editable = canEdit(recipe);

    const back = h("a", { class: "btn btn-ghost", href: "#/" }, icon("back", 20), h("span", { text: "Toutes les recettes" }));
    const favBtn = h("button", { class: `btn btn-secondary btn-icon fav-toggle${recipe.favori ? " is-on" : ""}`, type: "button",
      "aria-pressed": String(recipe.favori), "aria-label": "Favori", title: recipe.favori ? "Retirer des favoris" : "Ajouter aux favoris",
      onClick: () => toggleFavorite(recipe, onChange) }, icon(recipe.favori ? "star-filled" : "star", 22));
    const cookBtn = recipe.etapes.length
      ? h("button", { class: "btn btn-primary btn-big", type: "button",
        onClick: () => hit("kitchen_open") || openKitchen(recipe, { servings: servingsMemo.get(id) ?? recipe.pour }) },
      icon("chef", 22), h("span", { text: "Commencer à cuisiner" }))
      : null;
    const inList = servingsInList(id);
    const shopBtn = inList !== null && inList === servings
      ? h("a", { class: "btn btn-secondary", href: "#/courses" }, icon("check", 20), h("span", { text: "Dans les courses" }))
      : h("button", { class: "btn btn-secondary", type: "button", onClick: () => {
        hit("shopping_add");
        addRecipeToShopping(recipe, servings);
        toast(`Ajouté aux courses pour ${servingsText(servings, recipe.pour_unite)}.`, "ok");
        draw();
      } }, icon("cart", 20), h("span", { text: inList === null ? "Ajouter aux courses" : `Courses : passer à ${servingsText(servings, recipe.pour_unite, true)}` }));

    // rarely used actions wait behind "Plus"
    const photoInput = h("input", { type: "file", accept: "image/*", multiple: true, class: "sr-only", tabindex: "-1", "aria-hidden": "true" });
    const owner = recipe.access === "owner";
    const more = actionMenu({ items: [
      owner ? { icon: "share", text: "Partager", onClick: () => openShareDialog({ recipe }) } : null,
      recipe.can_copy ? { icon: "copy", text: "Créer une variante", onClick: (e, button) => variant(button) } : null,
      editable ? { icon: "camera", text: "Ajouter une photo", onClick: () => photoInput.click() } : null,
      owner ? { icon: "trash", text: "Supprimer", danger: true, onClick: (e, button) => removeRecipe(button) }
        : { icon: "close", text: "Retirer de mes recettes", danger: true, onClick: (e, button) => hideShared(button) },
    ] });
    photoInput.addEventListener("change", () => {
      const files = [...photoInput.files];
      photoInput.value = "";
      if (files.length) addPhotos(recipe, files, more.querySelector("button"));
    });
    async function removeRecipe(button) {
      const ok = await confirmDialog({ title: "Supprimer cette recette ?", danger: true, confirmText: "Supprimer",
        message: `« ${recipe.nom} » sera supprimée définitivement, avec ses photos.` });
      if (!ok) return;
      await busy(button, async () => {
        try {
          const { queued } = await deleteRecipe(id);
          hit("recipe_delete");
          servingsMemo.delete(id);
          toast(queued ? "Recette supprimée sur cet appareil. Elle le sera partout au retour de la connexion." : "Recette supprimée.", "ok");
          location.hash = "#/";
        } catch (e) { toast(e.message, "error"); }
      });
    }
    async function variant(button) {
      await busy(button, async () => {
        try {
          const copy = await makeVariant(id);
          hit("variant_create");
          toast("Variante créée : modifiez-la à votre goût.", "ok");
          onEdit(copy.id);
        } catch (e) { toast(e.offline ? "Pas de connexion : réessayez une fois en ligne." : e.message, "error"); }
      });
    }
    async function hideShared(button) {
      const who = displayName(recipe.owner);
      const ok = await confirmDialog({ title: "Retirer cette recette ?", danger: true, confirmText: "Retirer",
        message: `« ${recipe.nom} » n'apparaîtra plus dans vos recettes. Elle reste dans le carnet de ${who}. `
          + "Vous pourrez la remettre depuis « Partager mon carnet »." });
      if (!ok) return;
      await busy(button, async () => {
        try {
          await hideRecipe(id);
          hit("recipe_hide");
          servingsMemo.delete(id);
          toast("Recette retirée de votre liste.", "ok");
          location.hash = "#/";
        } catch (e) { toast(e.offline ? "Pas de connexion : réessayez une fois en ligne." : e.message, "error"); }
      });
    }
    const topActions = h("div", { class: "actions" },
      editable ? h("button", { class: "btn btn-secondary", type: "button", "aria-label": "Modifier la recette", onClick: () => onEdit(id) },
        icon("pencil", 20), h("span", { class: "btn-label", text: "Modifier" })) : null,
      more, photoInput);

    // ---- hero: what it is, how long it takes, and the way into cooking it
    const photo = recipe.images.length > 0;
    const hero = h("section", { class: `hero card${photo ? " has-photo" : ""}` });
    if (photo) {
      const gallery = h("div", { class: "gallery" });
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
      hero.append(gallery);
    }

    const facts = [];
    if (recipe.temps) facts.push(["Préparation", formatMinutes(recipe.temps)]);
    if (recipe.temps_cuisson) facts.push(["Cuisson", formatMinutes(recipe.temps_cuisson)]);
    if (recipe.temps && recipe.temps_cuisson) facts.push(["Total", formatMinutes(recipe.temps + recipe.temps_cuisson)]);
    facts.push(["Pour", servingsText(recipe.pour, recipe.pour_unite, true)]);
    const info = h("div", { class: "hero-info" },
      h("p", { class: "hero-kind" },
        h("span", { class: `rc-dish ${dishClass(recipe.type_plat)}` }, h("span", { class: "dish-dot", "aria-hidden": "true" }), recipe.type_plat),
        recipe.type_cuisson ? h("span", { class: "muted", text: recipe.type_cuisson }) : null),
      h("h1", { class: "serif", text: recipe.nom }),
      recipe.access !== "owner"
        ? h("p", { class: "owner-note" }, icon("eye", 18), `Recette de ${displayName(recipe.owner)}`,
          recipe.access === "read"
            ? (recipe.can_copy ? " : vous pouvez la lire et en faire votre variante." : " : vous pouvez la lire, pas la modifier.")
            : " : vous pouvez la modifier.")
        : null,
      h("dl", { class: "facts" }, facts.map(([label, value]) =>
        h("div", { class: "fact" }, h("dt", { text: label }), h("dd", { class: "serif", text: value })))),
      h("div", { class: "hero-actions" }, cookBtn, shopBtn, favBtn),
      recipe.tags.length
        ? h("p", { class: "tag-list" }, recipe.tags.map((tag) => h("button", {
          class: "chip-btn chip-tag", type: "button", title: `Voir les recettes « ${tag} »`,
          onClick: () => { state.filters.text = ""; state.filters.category = ""; state.filters.book = ""; state.filters.tags = [tag]; hit("tag_filter"); location.hash = "#/"; },
          text: `#${tag}` })))
        : null,
      recipe.source ? h("p", { class: "hint source-line" }, "Source : ", /^https?:\/\//i.test(recipe.source)
        ? h("a", { href: recipe.source, target: "_blank", rel: "noopener noreferrer", text: recipe.source })
        : recipe.source) : null);
    hero.append(info);

    // ---- ingredients
    const factor = frac(servings, Math.max(1, recipe.pour));
    const stepper = h("div", { class: "stepper", role: "group", "aria-label": "Quantité" },
      h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Moins", disabled: servings <= 1,
        onClick: () => { servingsMemo.set(id, Math.max(1, servings - 1)); draw(); } }, icon("minus")),
      h("output", { class: "stepper-value", "aria-live": "polite", text: servingsText(servings, recipe.pour_unite) }),
      h("button", { class: "btn btn-icon btn-secondary", type: "button", "aria-label": "Plus", disabled: servings >= 99,
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
    const ingredients = h("section", { class: "card pad ingredients-card" },
      h("h2", { class: "serif", text: "Ingrédients" }), stepper,
      servings !== recipe.pour
        ? h("button", { class: "btn btn-link", type: "button", onClick: () => { servingsMemo.delete(id); draw(); },
          text: `Revenir à ${servingsText(recipe.pour, recipe.pour_unite)}` })
        : null,
      ingList);

    // ---- steps
    const steps = h("section", { class: "card pad" },
      h("div", { class: "row between" }, h("h2", { class: "serif", text: "Préparation" }),
        recipe.etapes.length ? h("span", { class: "muted", text: `${recipe.etapes.length} étape${recipe.etapes.length > 1 ? "s" : ""}` }) : null),
      recipe.etapes.length
        ? [h("p", { class: "hint", text: "Touchez une étape pour la cocher quand elle est faite." }),
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
            return h("li", {}, button, timerButtons(i, text));
          }))]
        : h("p", { class: "muted", text: "Aucune étape pour l'instant." }));
    const notes = recipe.notes.trim()
      ? h("section", { class: "note" }, h("h2", { class: "serif", text: "Le petit mot du chef" }), h("p", { class: "note-text", text: recipe.notes }))
      : null;
    const when = (iso) => {
      const d = new Date(iso);
      return isNaN(d) ? "date inconnue" : `${d.toLocaleDateString("fr-FR")} à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
    };
    // the author's credit signs the page, quietly; nobody can edit it away. Not repeated when the
    // note under the title already says the same.
    let credit = creditLine(recipe);
    if (recipe.access !== "owner" && credit === `Recette de ${displayName(recipe.owner)}`) credit = "";
    const signature = credit ? h("p", { class: "credit serif", text: `— ${credit}` }) : null;
    const dates = h("p", { class: "hint", text: [recipe.cree_le && `Créée le ${when(recipe.cree_le)}`,
      recipe.modifie_le && `modifiée le ${when(recipe.modifie_le)}`].filter(Boolean).join(" · ") });

    clear(main).append(h("div", { class: "container detail" },
      h("div", { class: "detail-bar" }, back, topActions),
      hero,
      h("div", { class: "detail-body" }, ingredients, h("div", { class: "stack" }, steps, notes, h("div", {}, signature, dates)))));
  }
  draw();
  return { update: draw };
}
