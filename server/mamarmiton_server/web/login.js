import { api, token } from "./api.js";
import { h, clear } from "./dom.js";
import { busy, showPasswordToggle } from "./ui.js";

const deviceName = () => "Navigateur web";

function field(label, input, hint) {
  const id = `f-${Math.random().toString(36).slice(2, 8)}`;
  input.id = id;
  return h("div", { class: "field" },
    h("label", { for: id, text: label }),
    input,
    hint ? h("p", { class: "hint", text: hint }) : null);
}

/** Login / register screen. `onDone(user)` is called once the token is stored. */
export function renderLogin(root, { message = "", onDone }) {
  let mode = "login";
  const card = h("div", { class: "login-card" });
  const wrap = h("main", { class: "login", id: "main" },
    h("div", { class: "login-brand" },
      h("img", { src: "/app/icon.svg", alt: "", width: 72, height: 72, class: "login-logo" }),
      h("h1", { class: "serif", text: "Mamarmiton" }),
      h("p", { class: "muted", text: "Le carnet de recettes de la famille" })),
    card);
  clear(root).append(wrap);
  document.title = "Connexion · Mamarmiton";

  function draw(note = message) {
    const error = h("p", { class: "form-error", role: "alert" });
    if (note) error.textContent = note;
    const username = h("input", { type: "text", name: "username", autocomplete: "username", autocapitalize: "none",
      autocorrect: "off", spellcheck: "false", required: true });
    const password = h("input", { type: "password", name: "password", required: true,
      autocomplete: mode === "login" ? "current-password" : "new-password" });
    const display = h("input", { type: "text", name: "display_name", autocomplete: "name", maxlength: 60 });
    const invite = h("input", { type: "text", name: "invite", autocomplete: "off", autocapitalize: "characters",
      spellcheck: "false", required: true });
    const confirm = h("input", { type: "password", name: "confirm", autocomplete: "new-password", required: true });
    const submit = h("button", { class: "btn btn-primary btn-big", type: "submit",
      text: mode === "login" ? "Se connecter" : "Créer mon compte" });

    const form = h("form", { class: "stack", novalidate: true },
      h("h2", { class: "serif", text: mode === "login" ? "Bienvenue" : "Créer un compte" }),
      mode === "register" ? h("p", { class: "muted", text: "Il faut un code d'invitation, donné par la personne qui gère le carnet." }) : null,
      mode === "register" ? field("Code d'invitation", invite) : null,
      mode === "register" ? field("Votre prénom", display, "Il sera visible des personnes qui partagent leurs recettes avec vous.") : null,
      field("Nom d'utilisateur", username,
        mode === "register" ? "2 à 32 lettres minuscules, chiffres, point, tiret ou souligné." : null),
      field("Mot de passe", password),
      mode === "register" ? field("Mot de passe (encore une fois)", confirm) : null,
      mode === "register" ? showPasswordToggle(password, confirm) : showPasswordToggle(password),
      error,
      submit,
      h("button", { class: "btn btn-link", type: "button",
        onClick: () => { mode = mode === "login" ? "register" : "login"; draw(""); },
        text: mode === "login" ? "J'ai un code d'invitation : créer un compte" : "J'ai déjà un compte : me connecter" }));

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.textContent = "";
      if (!username.value.trim() || !password.value) { error.textContent = "Entrez votre nom d'utilisateur et votre mot de passe."; return; }
      if (mode === "register") {
        if (!invite.value.trim()) { error.textContent = "Entrez le code d'invitation."; return; }
        if (password.value !== confirm.value) { error.textContent = "Les deux mots de passe ne sont pas identiques."; return; }
      }
      await busy(submit, async () => {
        try {
          const result = mode === "login"
            ? await api.login(username.value.trim(), password.value, deviceName())
            : await api.register({ username: username.value.trim(), password: password.value, device: deviceName(),
              invite: invite.value.trim(), display_name: display.value.trim() });
          token.set(result.token);
          onDone(result.user);
        } catch (e) {
          error.textContent = e.message;
          password.value = "";
          password.focus();
        }
      });
    });
    clear(card).append(form);
    (mode === "register" ? invite : username).focus();
  }
  draw();
}
