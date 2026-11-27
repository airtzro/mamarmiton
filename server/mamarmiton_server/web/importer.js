// "Importer une recette": a photo of a cookbook page, a file (PDF, Word…), a pasted text or the address of a
// recipe website is read by the
// server (OCR + local model on the NAS, no cloud) and opens in the editor as a new recipe to check.
import { api } from "./api.js";
import { hit } from "./usage.js";
import { h, icon, clear } from "./dom.js";
import { openDialog } from "./ui.js";
import { NO_OCR_MESSAGE, acceptAttribute, addFiles, draftFromImport, formatSize, kindOf, normalizeStatus, urlProblem } from "./importlogic.js";

const GIVE_UP_MS = 200_000;  // the NAS can take two minutes: never cut it short before that
const GIVE_UP_URL_MS = 70_000;  // a website answers in seconds (the server stops at 45 s)

let status = null;   // normalized GET /import/status, null = this server cannot import
let known = false;   // false: not asked yet, or the last try had no connection
let asking = null;

export const importStatus = () => status;

/** Ask the server (once) whether it can import. Resolves the status, null when it cannot. */
export function loadImportStatus() {
  if (known) return Promise.resolve(status);
  if (!asking) {
    asking = api.importStatus().then((raw) => { status = normalizeStatus(raw); known = true; }, (e) => {
      status = null;
      known = e.status >= 400 && e.status < 500 && e.status !== 401 && e.status !== 429;  // an old server: 404, for good
    }).then(() => status).finally(() => { asking = null; });
  }
  return asking;
}

/** After a sign-out: the next account may be on another server version. */
export function forgetImportStatus() { status = null; known = false; }

function fileInput({ accept, capture = false, multiple = false, onFiles }) {
  const input = h("input", { type: "file", accept, class: "sr-only", tabindex: "-1", "aria-hidden": "true", multiple });
  if (capture) input.setAttribute("capture", "environment");
  input.addEventListener("change", () => { const list = [...input.files]; input.value = ""; if (list.length) onFiles(list); });
  return input;
}

/** The import dialog. `onDone(draft)` gets {recipe, warnings, dishPhoto} (see importlogic.draftFromImport). */
export function openImportDialog({ onDone }) {
  const st = status;
  if (!st) return;
  let files = [];
  const urls = new Map();   // File -> object URL of its thumbnail
  let controller = null;
  let waiting = false;
  let closed = false;
  let ticker = null;

  const revoke = (file) => { const url = urls.get(file); if (url) URL.revokeObjectURL(url); urls.delete(file); };
  const thumbUrl = (file) => { if (!urls.has(file)) urls.set(file, URL.createObjectURL(file)); return urls.get(file); };

  const dialog = openDialog({
    title: "Importer une recette",
    onClose: () => {
      closed = true;
      clearInterval(ticker);
      if (controller) controller.abort();
      for (const file of [...urls.keys()]) revoke(file);
    },
    build: (close) => {
      const errors = h("div", { class: "form-error import-errors", role: "alert" });
      const showErrors = (list) => clear(errors).append(...list.map((text) => h("p", { text })));

      const add = (list) => {
        const result = addFiles(files, list, st);
        files = result.files;
        showErrors(result.errors);
        draw();
      };
      const camera = fileInput({ accept: "image/*", capture: true, onFiles: add });
      const picker = fileInput({ accept: acceptAttribute(st), multiple: true, onFiles: add });

      const text = h("textarea", { id: "import-text", rows: 7, maxlength: 100000,
        placeholder: "Collez ici la recette : son nom, les ingrédients, les étapes…" });
      const textBox = h("div", { class: "field", hidden: true },
        h("label", { for: "import-text", text: "Le texte de la recette" }), text);
      const showText = () => { textBox.hidden = false; draw(); text.focus(); };

      const address = h("input", { id: "import-url", type: "url", inputmode: "url", autocapitalize: "none", autocomplete: "off",
        spellcheck: "false", placeholder: "https://www.marmiton.org/recettes/…" });
      address.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); read(); } });
      const urlBox = h("div", { class: "field", hidden: true },
        h("label", { for: "import-url", text: "Adresse d'une page de recette (Marmiton, 750g, Cuisine AZ…)" }), address,
        h("p", { class: "hint", text: "Ouvrez la recette sur le site, copiez l'adresse en haut de l'écran, puis collez-la ici." }));
      const showUrl = () => { urlBox.hidden = false; draw(); address.focus(); };

      const choice = (iconName, title, hint, onClick, disabled = false) =>
        h("button", { class: "import-choice", type: "button", onClick, disabled },
          h("span", { class: "import-choice-icon" }, icon(iconName, 28)),
          h("span", { class: "import-choice-text" }, h("strong", { text: title }), h("span", { text: hint })));
      const choices = h("div", { class: "import-choices" },
        choice("camera", "Prendre une photo", "D'un livre ou d'une fiche", () => camera.click(), !st.ocr),
        choice("file", "Choisir un fichier", st.ocr ? "Photo, PDF, Word, LibreOffice" : "Texte, Word ou LibreOffice", () => picker.click()),
        choice("paste", "Coller un texte", "Copié d'un e-mail ou d'un site", showText),
        st.url ? choice("link", "Adresse d'un site", "Marmiton, 750g, Cuisine AZ…", showUrl) : null);

      const list = h("ul", { class: "import-files", "aria-label": "Fichiers choisis" });
      const morePage = h("button", { class: "btn btn-secondary", type: "button", onClick: () => camera.click() }, icon("camera", 20), h("span"));
      const moreFile = h("button", { class: "btn btn-secondary", type: "button", onClick: () => picker.click() }, icon("file", 20), "Ajouter un fichier");
      const moreText = h("button", { class: "btn btn-secondary", type: "button", onClick: showText }, icon("paste", 20), "Coller un texte");
      const more = h("div", { class: "import-more" }, st.ocr ? morePage : null, moreFile, moreText);

      const readBtn = h("button", { class: "btn btn-primary btn-big", type: "button", onClick: read }, icon("scan", 22), "Lire la recette");
      const actions = h("div", { class: "import-actions" },
        h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: close, text: "Annuler" }), readBtn);

      const form = h("div", { class: "stack" },
        h("p", { text: `Une recette prise en photo, un fichier, un texte${st.url ? " ou l'adresse d'un site" : ""} : Mamarmiton la recopie pour vous, puis vous la vérifiez.` }),
        st.ocr ? null : h("p", { class: "import-note", text: NO_OCR_MESSAGE }),
        choices, list, more, textBox, urlBox, errors,
        h("p", { class: "hint import-drop-hint", text: "Vous pouvez aussi glisser des fichiers dans cette fenêtre." }),
        actions);

      // ---- waiting
      const elapsed = h("p", { class: "muted" });
      const waitNote = h("p", { text: "Cela peut prendre une à deux minutes." });
      const wait = h("div", { class: "import-wait", hidden: true },
        h("span", { class: "spinner", "aria-hidden": "true" }),
        h("div", { role: "status" },
          h("p", { class: "import-wait-title serif", text: "Lecture de la recette…" }),
          waitNote),
        elapsed,
        h("button", { class: "btn btn-secondary btn-big", type: "button", onClick: () => stop("cancel"), text: "Annuler" }));
      let stopReason = "";
      // abort() without a reason: fetch then rejects with an AbortError, which api.request passes through
      const stop = (why) => { stopReason = why; if (controller) controller.abort(); };

      function draw() {
        clear(list);
        let page = 0;
        files.forEach((file) => {
          const isImage = kindOf(file) === "image";
          const title = isImage ? `Page ${++page}` : file.name;
          const thumb = isImage
            ? h("img", { class: "import-thumb", alt: "", src: thumbUrl(file) })
            : h("span", { class: "import-thumb is-doc", "aria-hidden": "true" }, icon("file", 26));
          list.append(h("li", { class: "import-file" }, thumb,
            h("span", { class: "import-file-text" }, h("strong", { text: title }),
              h("span", { class: "muted", text: isImage ? formatSize(file.size) : `${kindOf(file) === "pdf" ? "PDF" : "Document"} · ${formatSize(file.size)}` })),
            h("button", { class: "btn btn-icon btn-ghost", type: "button", "aria-label": `Retirer ${title}`,
              onClick: () => { files = files.filter((f) => f !== file); revoke(file); showErrors([]); draw(); } }, icon("trash", 20))));
        });
        const urlMode = !urlBox.hidden;
        const hasContent = files.length > 0 || !textBox.hidden || urlMode;
        choices.hidden = hasContent;
        list.hidden = !files.length;
        more.hidden = !hasContent || urlMode;
        moreText.hidden = !textBox.hidden;
        morePage.lastChild.textContent = files.some((f) => kindOf(f) === "image") ? "Ajouter une page" : "Prendre une photo";
        readBtn.hidden = !hasContent;
      }

      async function read() {
        const fromSite = !urlBox.hidden;
        if (fromSite) {
          const problem = urlProblem(address.value);
          if (problem) { showErrors([problem]); address.focus(); return; }
        } else if (!files.length && !text.value.trim()) { showErrors(["Collez d'abord le texte de la recette, ou ajoutez une photo."]); text.focus(); return; }
        showErrors([]);
        const sent = [...files];
        controller = new AbortController();
        stopReason = "";
        const giveUp = setTimeout(() => stop("timeout"), fromSite ? GIVE_UP_URL_MS : GIVE_UP_MS);
        waitNote.textContent = fromSite ? "Cela ne devrait prendre que quelques secondes." : "Cela peut prendre une à deux minutes.";
        const started = Date.now();
        const tick = () => {
          const s = Math.round((Date.now() - started) / 1000);
          elapsed.textContent = s < 60 ? `Depuis ${s} seconde${s > 1 ? "s" : ""}` : `Depuis ${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}`;
        };
        tick();
        ticker = setInterval(tick, 1000);
        waiting = true;
        form.hidden = true;
        wait.hidden = false;
        wait.querySelector("button").focus();
        try {
          const result = fromSite ? await api.importUrl(address.value.trim(), controller.signal)
            : await api.importRecipe(sent, text.value, controller.signal);
          if (closed) return;
          if (fromSite) hit("url_import");
          const draft = draftFromImport(result, sent);
          waiting = false;
          close();
          onDone(draft);
        } catch (e) {
          if (closed) return;
          if (e.name !== "AbortError") showErrors([e.message]);
          else if (stopReason === "timeout") showErrors([fromSite ? "Le site met trop de temps à répondre. Réessayez dans un moment." : "La lecture prend trop de temps. Réessayez avec moins de pages, ou un peu plus tard."]);
          waiting = false;
          wait.hidden = true;
          form.hidden = false;
          readBtn.focus();
        } finally {
          clearTimeout(giveUp);
          clearInterval(ticker);
          controller = null;
        }
      }

      // ---- drag and drop (desktop browsers)
      const drop = h("div", { class: "import-drop", hidden: true }, icon("file", 36), h("strong", { text: "Déposez les fichiers ici" }));
      let depth = 0;
      const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
      queueMicrotask(() => {
        const el = dialog.el;
        el.addEventListener("dragenter", (e) => { if (!hasFiles(e) || waiting) return; e.preventDefault(); depth++; drop.hidden = false; });
        el.addEventListener("dragover", (e) => { if (!hasFiles(e) || waiting) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
        el.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) drop.hidden = true; });
        el.addEventListener("drop", (e) => {
          if (!hasFiles(e)) return;
          e.preventDefault();
          depth = 0;
          drop.hidden = true;
          if (!waiting) add([...e.dataTransfer.files]);
        });
        // a tap beside the window must not throw away two minutes of reading
        el.addEventListener("click", (e) => { if (waiting && e.target === el) e.stopImmediatePropagation(); }, true);
      });

      draw();
      return h("div", { class: "import" }, form, wait, drop, camera, picker);
    },
  });
}
