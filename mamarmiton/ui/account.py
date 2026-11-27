"""Account dialog: sign in / join with an invitation code, then manage the account."""

from __future__ import annotations

import platform
from tkinter import messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton.remote import DEFAULT_SERVER_URL, Account, ApiClient, save_account
from mamarmiton.ui import art, theme
from mamarmiton.ui.remote_logic import (
    error_text, format_date, last_sync_text, normalize_server_url, run_in_thread, status_line, upload_candidates,
)
from mamarmiton.ui.sharing import ShareDialog, level_of
from mamarmiton.ui.widgets import Choice, IconLine, Modal, ScrollFrame

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

HAVE_ACCOUNT, HAVE_CODE = "J'ai un compte", "J'ai un code d'invitation"
STATUS_COLORS = {"ok": theme.ACCENT_TEXT, "busy": theme.MUTED, "warn": theme.STAR, "bad": theme.DANGER}


class AccountDialog(Modal):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Mon compte", 640, 800)
        self.app = app
        self._wrapped: list[tuple[ctk.CTkLabel, int]] = []
        self._pills: Choice | None = None
        self._status_label: ctk.CTkLabel | None = None
        self._closed = False
        self._last_key: tuple | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.head = self.header("Mon compte", "")
        self.body = ScrollFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(18, 8))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.bind("<Configure>", self._rewrap, add="+")
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=28, pady=(6, 18))
        theme.secondary_button(footer, text="Fermer", width=130, command=self.close).pack(side="right")
        self.actions = ctk.CTkFrame(footer, fg_color="transparent")  # the main button of the current view
        self.actions.pack(side="left")
        self.render()

    # -------------------------------------------------------------- helpers
    def close(self) -> None:
        self._closed = True
        self.app.account_dialog = None
        super().close()
        self.app.after(300, self.app._check_conflicts)

    def _rewrap(self, event=None) -> None:
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = self.body.winfo_width() / scaling
        if width <= 1:
            return
        width = int(width) // 10 * 10  # coarse steps: re-wrapping must not feed back into the scrollbar
        key = (width, int(self.winfo_width() / scaling) // 10, len(self._wrapped))
        if key == self._last_key:
            return
        self._last_key = key
        if getattr(self, "_sub", None) is not None:
            self._sub.configure(wraplength=max(240, int(self.winfo_width() / scaling) - 70))
        for label, used in self._wrapped:
            try:
                label.configure(wraplength=max(160, int(width - used)))
            except Exception:  # destroyed
                pass
        if self._status_label is not None:
            self._status_label.set_wrap(max(160, int(width - 100)))
        if self._pills is not None:
            self._pills.reflow(max(200, int(width - 70)))

    def _text(self, parent, text: str, used: int = 80, size: int = 14, color=None, bold: bool = False):
        label = ctk.CTkLabel(parent, text=text, font=theme.font(size, "bold" if bold else "normal"),
                             text_color=color or theme.MUTED, anchor="w", justify="left", wraplength=480)
        self._wrapped.append((label, used))
        return label

    def _card(self, title: str | None = None) -> ctk.CTkFrame:
        card = theme.card(self.body)
        card.pack(fill="x", padx=10, pady=(0, 14))
        if title:
            theme.heading(card, title, size=19).pack(anchor="w", padx=20, pady=(18, 6))
        return card

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        for child in self.actions.winfo_children():
            child.destroy()
        self._wrapped = []
        self._last_key = None
        self._pills = None
        self._status_label = None

    def render(self) -> None:
        self._clear()
        app = self.app
        if app.is_remote and app.store.status != "auth":
            self._connected()
        else:
            self._login()
        self.after(60, self._rewrap)
        self.body._parent_canvas.yview_moveto(0)

    def _subtitle(self, text: str) -> None:
        sub = self.head.winfo_children()[1] if len(self.head.winfo_children()) > 1 else None
        if sub is None:
            sub = ctk.CTkLabel(self.head, text="", font=theme.font(13), text_color=theme.MUTED, anchor="w",
                               justify="left", wraplength=540)
            sub.pack(anchor="w", pady=(2, 0))
        sub.configure(text=text)
        self._sub = sub

    def _busy_bar(self, parent) -> ctk.CTkProgressBar:
        return ctk.CTkProgressBar(parent, mode="indeterminate", height=8, corner_radius=4,
                                  progress_color=theme.ACCENT, fg_color=theme.SURFACE_ALT)

    # ======================================================================
    # Not signed in
    # ======================================================================
    def _login(self) -> None:
        app = self.app
        expired = app.is_remote  # signed in before, but the server refuses the saved session
        self._subtitle("Sans compte, vos recettes restent sur cet ordinateur. Avec un compte, vous les retrouvez "
                       "sur tous vos appareils et vous pouvez les partager en famille."
                       if not expired else "Votre connexion a expiré : entrez de nouveau votre mot de passe.")

        self._pills = Choice(self.body, [HAVE_ACCOUNT, HAVE_CODE], command=lambda _v: self._mode_changed())
        self._pills.pack(anchor="w", padx=14, pady=(0, 12))
        self._pills.set(HAVE_ACCOUNT)

        card = self._card()
        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=18)
        form.grid_columnconfigure(0, weight=1)
        self.f_invite = self._field(form, 0, "Code d'invitation", hint="La personne qui vous invite vous l'a donné.")
        self.f_display = self._field(form, 1, "Votre prénom", hint="C'est ce que verront les autres.")
        self.f_user = self._field(form, 2, "Identifiant", hint="Sans espace, par exemple « maman ».")
        self.f_pass = self._field(form, 3, "Mot de passe", secret=True)
        self._form = form
        # fields are (frame, entry) tuples; keep the entries handy
        self.e_invite, self.e_display, self.e_user, self.e_pass = (
            self.f_invite[1], self.f_display[1], self.f_user[1], self.f_pass[1])

        adv = ctk.CTkFrame(form, fg_color="transparent")
        adv.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.btn_adv = theme.link_button(adv, text="Options avancées  ▾", command=self._toggle_advanced)
        self.btn_adv.pack(anchor="w")
        self.adv_box = ctk.CTkFrame(adv, fg_color="transparent")
        theme.eyebrow(self.adv_box, "Adresse du serveur").pack(anchor="w", pady=(6, 4))
        self.e_server = theme.entry(self.adv_box)
        self.e_server.pack(fill="x")
        self._text(self.adv_box, "Ne la changez que si on vous l'a demandé.", used=70, size=12).pack(
            anchor="w", pady=(4, 0))
        saved = app.account.server_url if app.account else DEFAULT_SERVER_URL
        self.e_server.insert(0, saved)
        if app.account:
            self.e_user.insert(0, app.account.username)

        self.error = self._text(form, "", used=110, size=14, color=theme.DANGER, bold=True)
        self.error.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.bar = self._busy_bar(form)
        self.btn_go = theme.primary_button(self.actions, text="Se connecter", width=0, command=self._submit)
        self.btn_go.pack(side="left")
        for entry in (self.e_invite, self.e_display, self.e_user, self.e_pass, self.e_server):
            entry.bind("<Return>", lambda _e: self._submit())
        self._mode_changed()
        self.after(150, lambda: (self.e_user if not self.e_user.get() else self.e_pass).focus_set())

    def _field(self, form, row: int, label: str, hint: str = "", secret: bool = False):
        box = ctk.CTkFrame(form, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        box.grid_columnconfigure(0, weight=1)
        theme.eyebrow(box, label).grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry = theme.entry(box, show="•" if secret else "")
        entry.grid(row=1, column=0, sticky="ew")
        if secret:
            eye = theme.link_button(box, text="Afficher", width=0, height=42, command=self._toggle_password)
            eye.grid(row=1, column=1, padx=(6, 0))
            self.btn_eye = eye
        if hint:
            self._text(box, hint, used=90, size=12, color=theme.FAINT).grid(row=2, column=0, sticky="w", pady=(3, 0))
        return box, entry

    def _toggle_password(self) -> None:
        hidden = self.e_pass.cget("show") != ""
        self.e_pass.configure(show="" if hidden else "•")
        self.btn_eye.configure(text="Masquer" if hidden else "Afficher")

    def _toggle_advanced(self) -> None:
        if self.adv_box.winfo_ismapped():
            self.adv_box.pack_forget()
            self.btn_adv.configure(text="Options avancées  ▾")
        else:
            self.adv_box.pack(fill="x")
            self.btn_adv.configure(text="Options avancées  ▴")

    def _register_mode(self) -> bool:
        return self._pills is not None and self._pills.get() == HAVE_CODE

    def _mode_changed(self) -> None:
        register = self._register_mode()
        for frame, _entry in (self.f_invite, self.f_display):
            if register:
                frame.grid()
            else:
                frame.grid_remove()
        self.btn_go.configure(text="Créer mon compte" if register else "Se connecter")
        self.error.configure(text="")

    def _set_busy(self, busy: bool) -> None:
        self.btn_go.configure(state="disabled" if busy else "normal")
        if busy:
            self.bar.grid(row=6, column=0, sticky="ew", pady=(10, 0))
            self.bar.start()
        else:
            self.bar.stop()
            self.bar.grid_remove()

    def _submit(self) -> None:
        if str(self.btn_go.cget("state")) == "disabled":
            return
        register = self._register_mode()
        username, password = self.e_user.get().strip(), self.e_pass.get()
        server = normalize_server_url(self.e_server.get()) or DEFAULT_SERVER_URL
        invite, display = self.e_invite.get().strip(), self.e_display.get().strip()
        if register and not invite:
            return self._fail("Entrez le code d'invitation.")
        if register and not display:
            return self._fail("Entrez votre prénom.")
        if not username:
            return self._fail("Entrez votre identifiant.")
        if not password:
            return self._fail("Entrez votre mot de passe.")
        self.error.configure(text="")
        self._set_busy(True)
        device = platform.node() or "ordinateur"

        def work():
            client = ApiClient(server)
            if register:
                client.register(username, password, invite, display, device=device)
            else:
                client.login(username, password, device=device)
            return client

        run_in_thread(self, work, lambda client: self._signed_in(client, server), self._login_failed)

    def _fail(self, message: str) -> None:
        self.error.configure(text=message)

    def _login_failed(self, exc: BaseException) -> None:
        self._set_busy(False)
        self._fail(error_text(exc))

    def _signed_in(self, client: ApiClient, server: str) -> None:
        user = client.user or {}
        account = Account(server_url=server, token=client.token or "", username=user.get("username", ""),
                          display_name=user.get("display_name") or user.get("username", ""),
                          is_admin=bool(user.get("is_admin")))
        save_account(account)
        local = self.app.local_book()
        self.app.sign_in(client, account)
        count = upload_candidates(self.app.store, local)
        if count:
            self._offer_upload(local, count)
        else:
            self.render()

    # ======================================================================
    # Upload of the local book
    # ======================================================================
    def _offer_upload(self, local, count: int) -> None:
        self._clear()
        self._subtitle("Vous êtes connecté(e).")
        card = self._card(f"Envoyer mes {count} recettes vers mon compte ?")
        self._text(card, "Vos recettes actuelles, avec leurs photos, seront copiées sur votre compte pour que "
                   "vous les retrouviez sur vos autres appareils.\n\nLe fichier qui est sur cet ordinateur "
                   "n'est pas touché : il reste là comme sauvegarde.", used=70, color=theme.TEXT).pack(
            anchor="w", padx=20, pady=(0, 14))
        self.up_error = self._text(card, "", used=70, color=theme.DANGER, bold=True)
        self.up_label = self._text(card, "", used=70, size=13)
        self.up_bar = ctk.CTkProgressBar(card, height=10, corner_radius=5, progress_color=theme.ACCENT,
                                         fg_color=theme.SURFACE_ALT)
        self.up_bar.set(0)
        self.up_slot = ctk.CTkFrame(card, fg_color="transparent", height=14)  # progress widgets go above it
        self.up_slot.pack()
        self.up_go = theme.primary_button(self.actions, text=f"Envoyer mes {count} recettes", width=0,
                                          command=lambda: self._upload(local, count))
        self.up_go.pack(side="left")
        self.up_skip = theme.secondary_button(card, text="Pas maintenant", width=0, command=self.render)
        self.up_skip.pack(anchor="w", padx=20, pady=(0, 20))
        self.after(60, self._rewrap)

    def _upload(self, local, count: int) -> None:
        self.up_go.configure(state="disabled")
        self.up_skip.configure(state="disabled")
        self.up_error.pack_forget()
        self.up_label.pack(anchor="w", padx=20, pady=(0, 6), before=self.up_slot)
        self.up_bar.pack(fill="x", padx=20, pady=(0, 6), before=self.up_slot)
        self.up_label.configure(text="Envoi en cours…")
        store = self.app.store

        def progress(done: int, total: int) -> None:
            def show():
                if self._closed:
                    return
                self.up_bar.set(done / max(1, total))
                self.up_label.configure(text=f"Envoi en cours : {min(done + 1, total)} sur {total}…"
                                        if done < total else "Dernières vérifications…")
            try:
                self.after(0, show)
            except Exception:  # window closed
                pass

        def work():
            store.upload_local(local, progress=progress)
            return store.sync()

        def done(result):
            self.app.on_sync_done(result)
            if self._closed:
                return
            self.render()
            self._subtitle("Vos recettes sont maintenant sur votre compte. Le fichier de cet ordinateur reste "
                           "comme sauvegarde.")

        def failed(exc):
            self.app.note_network_error(exc)
            self.up_bar.pack_forget()
            self.up_label.pack_forget()
            self.up_error.configure(text=error_text(exc) + "\nL'envoi reprendra là où il s'est arrêté.")
            self.up_error.pack(anchor="w", padx=20, pady=(0, 6), before=self.up_slot)
            self.up_go.configure(state="normal", text="Réessayer")
            self.up_skip.configure(state="normal")

        run_in_thread(self, work, done, failed)

    # ======================================================================
    # Signed in
    # ======================================================================
    def _connected(self) -> None:
        app = self.app
        account = app.account
        self._subtitle("Vos recettes sont gardées sur votre compte.")
        card = self._card()
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(18, 4))
        avatar = ctk.CTkFrame(top, width=52, height=52, corner_radius=26, fg_color=theme.ACCENT_SOFT)
        avatar.pack(side="left")
        avatar.pack_propagate(False)
        ctk.CTkLabel(avatar, text="", image=art.icon("users", theme.ACCENT_TEXT, 24)).place(relx=0.5, rely=0.5,
                                                                                            anchor="center")
        words = ctk.CTkFrame(top, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True, padx=(14, 0))
        ctk.CTkLabel(words, text=account.display_name or account.username, font=theme.serif(22),
                     text_color=theme.TEXT, anchor="w").pack(anchor="w")
        who = f"Identifiant : {account.username}" + ("  ·  Administrateur" if account.is_admin else "")
        self._text(words, who, used=150, size=13).pack(anchor="w")
        self._status_label = IconLine(card, theme.font(14, "bold"), size=18, wraplength=440)
        self._status_label.pack(fill="x", padx=20, pady=(10, 0))
        self._sync_label = self._text(card, "", used=70, size=13)
        self._sync_label.pack(anchor="w", padx=20, pady=(0, 4))
        self.btn_sync = theme.secondary_button(card, text="Synchroniser maintenant", width=0,
                                               image=art.icon("reset", theme.TEXT, 17), compound="left",
                                               command=app.sync_now)
        self.btn_sync.pack(anchor="w", padx=20, pady=(8, 20))
        self.refresh_status()

        # --- sharing
        card = self._card("Partage")
        self._text(card, "Montrez vos recettes à votre famille. Pour partager une seule recette, ouvrez-la et "
                   "cliquez sur « Partager ».", used=70).pack(anchor="w", padx=20)
        theme.secondary_button(card, text="Partager mon carnet", width=0, image=art.icon("share", theme.TEXT, 17),
                               compound="left", command=lambda: ShareDialog(self, app)).pack(
            anchor="w", padx=20, pady=(12, 14))
        theme.divider(card).pack(fill="x", padx=20)
        theme.eyebrow(card, "Carnets partagés avec moi").pack(anchor="w", padx=20, pady=(14, 4))
        self.received = ctk.CTkFrame(card, fg_color="transparent")
        self.received.pack(fill="x", padx=14, pady=(0, 16))
        self._load_received()

        # --- password
        card = self._card("Mot de passe")
        self.pw_box = ctk.CTkFrame(card, fg_color="transparent")
        self.btn_pw = theme.secondary_button(card, text="Changer mon mot de passe", width=0,
                                             command=self._toggle_password_form)
        self.btn_pw.pack(anchor="w", padx=20, pady=(0, 18))
        self._build_password_form()

        # --- admin
        if account.is_admin:
            card = self._card("Inviter quelqu'un")
            self._text(card, "Créez un code, puis donnez-le à la personne : elle pourra s'inscrire avec.",
                       used=70).pack(anchor="w", padx=20)
            self.btn_invite = theme.secondary_button(card, text="Créer une invitation", width=0,
                                                     command=self._create_invite)
            self.btn_invite.pack(anchor="w", padx=20, pady=(12, 8))
            self.invite_box = ctk.CTkFrame(card, fg_color="transparent")
            self.invite_msg = self._text(card, "", used=70, size=13)
            self.invite_slot = ctk.CTkFrame(card, fg_color="transparent", height=10)
            self.invite_slot.pack()

        # --- sign out
        card = self._card("Se déconnecter")
        self._text(card, "Vos recettes restent sur votre compte et sur cet ordinateur ; vous pourrez vous "
                   "reconnecter quand vous voulez.", used=70).pack(anchor="w", padx=20)
        theme.danger_button(card, text="Se déconnecter", width=0, command=self._logout).pack(
            anchor="w", padx=20, pady=(12, 18))

    def refresh_status(self) -> None:
        """Called by the app whenever the sync state changes."""
        label = self._status_label
        if label is None or self._closed or not self.app.is_remote:
            return
        store = self.app.store
        icon, text, kind = status_line(store.status, store.pending_count, store.last_error)
        label.set(icon, text, STATUS_COLORS[kind])
        self._sync_label.configure(text=last_sync_text(store.last_sync))
        self.btn_sync.configure(state="disabled" if store.status == "syncing" else "normal")

    # ---- shared with me
    def _load_received(self) -> None:
        ctk.CTkLabel(self.received, text="Chargement…", font=theme.font(14), text_color=theme.MUTED).pack(
            anchor="w", padx=6)
        run_in_thread(self, self.app.store.client.shares, self._show_received, self._received_failed)

    def _received_failed(self, exc: BaseException) -> None:
        for child in self.received.winfo_children():
            child.destroy()
        self._text(self.received, error_text(exc), used=90).pack(anchor="w", padx=6)
        self.app.note_network_error(exc)

    def _show_received(self, shares: dict) -> None:
        for child in self.received.winfo_children():
            child.destroy()
        books = shares.get("books_shared_with_me", [])
        if not books:
            self._text(self.received, "Personne ne vous a encore partagé son carnet.", used=90).pack(
                anchor="w", padx=6)
        for book in books:
            row = ctk.CTkFrame(self.received, fg_color="transparent")
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(0, weight=1)
            words = ctk.CTkFrame(row, fg_color="transparent")
            words.grid(row=0, column=0, sticky="w", padx=6)
            ctk.CTkLabel(words, text=f"Carnet de {book.get('display_name') or book['username']}",
                         font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(words, text=level_of(book), font=theme.font(12),
                         text_color=theme.MUTED, anchor="w").pack(anchor="w")
            theme.secondary_button(row, text="Ne plus voir", width=0, height=36,
                                   command=lambda b=book: self._leave(b)).grid(row=0, column=1, padx=(8, 4))
        hidden = shares.get("hidden", [])
        if hidden:
            ctk.CTkLabel(self.received, text="Recettes retirées de ma liste", font=theme.font(14, "bold"),
                         text_color=theme.TEXT, anchor="w").pack(anchor="w", padx=6, pady=(12, 2))
        for item in hidden:
            row = ctk.CTkFrame(self.received, fg_color="transparent")
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(0, weight=1)
            words = ctk.CTkFrame(row, fg_color="transparent")
            words.grid(row=0, column=0, sticky="w", padx=6)
            ctk.CTkLabel(words, text=item.get("nom") or "Recette sans nom", font=theme.font(14),
                         text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(words, text=f"Carnet de {item.get('display_name') or item['username']}", font=theme.font(12),
                         text_color=theme.MUTED, anchor="w").pack(anchor="w")
            theme.secondary_button(row, text="Remettre", width=0, height=36,
                                   command=lambda i=item: self._unhide(i)).grid(row=0, column=1, padx=(8, 4))

    def _unhide(self, item: dict) -> None:
        client = self.app.store.client

        def work():
            client.set_hidden(item["recipe_id"], False)
            return client.shares()

        def failed(exc):
            messagebox.showerror("Recette partagée", error_text(exc), parent=self)
            self.app.note_network_error(exc)

        def done(shares):
            self._show_received(shares)
            self.app.sync_now()

        run_in_thread(self, work, done, failed)

    def _leave(self, book: dict) -> None:
        name = book.get("display_name") or book["username"]
        if not messagebox.askyesno("Ne plus voir ce carnet", f"Vous ne verrez plus les recettes de {name}. "
                                   "Elles ne sont pas supprimées chez elle. Continuer ?", parent=self):
            return
        client = self.app.store.client

        def work():
            client.leave_book(book["username"])
            return client.shares()

        def failed(exc):
            messagebox.showerror("Carnet partagé", error_text(exc), parent=self)
            self.app.note_network_error(exc)

        def done(shares):
            self._show_received(shares)
            self.app.sync_now()

        run_in_thread(self, work, done, failed)

    # ---- password
    def _build_password_form(self) -> None:
        box = self.pw_box
        box.grid_columnconfigure(0, weight=1)
        self.pw_entries = []
        for i, label in enumerate(("Mot de passe actuel", "Nouveau mot de passe", "Nouveau mot de passe, encore une fois")):
            theme.eyebrow(box, label).grid(row=2 * i, column=0, sticky="w", pady=(0 if i == 0 else 8, 4))
            entry = theme.entry(box, show="•")
            entry.grid(row=2 * i + 1, column=0, sticky="ew")
            self.pw_entries.append(entry)
        self.pw_msg = self._text(box, "", used=110, size=14, color=theme.DANGER, bold=True)
        self.pw_msg.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        self.pw_save = theme.primary_button(box, text="Enregistrer le nouveau mot de passe", width=0,
                                            command=self._change_password)
        self.pw_save.grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.pw_entries[2].bind("<Return>", lambda _e: self._change_password())

    def _toggle_password_form(self) -> None:
        if self.pw_box.winfo_ismapped():
            self.pw_box.pack_forget()
        else:
            self.pw_box.pack(fill="x", padx=20, pady=(0, 18))
            self.pw_entries[0].focus_set()
            self.after(60, self._rewrap)

    def _change_password(self) -> None:
        old, new, again = (e.get() for e in self.pw_entries)
        self.pw_msg.configure(text_color=theme.DANGER)
        if not old or not new:
            return self.pw_msg.configure(text="Remplissez les deux premières cases.")
        if new != again:
            return self.pw_msg.configure(text="Les deux nouveaux mots de passe sont différents.")
        self.pw_save.configure(state="disabled")
        self.pw_msg.configure(text="")
        client = self.app.store.client

        def done(_r):
            self.pw_save.configure(state="normal")
            for e in self.pw_entries:
                e.delete(0, "end")
            self.pw_box.pack_forget()
            messagebox.showinfo("Mot de passe", "Votre mot de passe a bien été changé.", parent=self)

        def failed(exc):
            self.pw_save.configure(state="normal")
            self.pw_msg.configure(text=error_text(exc))

        run_in_thread(self, lambda: client.change_password(old, new), done, failed)

    # ---- invitation
    def _create_invite(self) -> None:
        self.btn_invite.configure(state="disabled")
        client = self.app.store.client

        def done(data):
            self.btn_invite.configure(state="normal", text="Créer une autre invitation")
            for child in self.invite_box.winfo_children():
                child.destroy()
            self.invite_box.pack(fill="x", padx=20, pady=(0, 6), before=self.invite_slot)
            self.invite_box.grid_columnconfigure(0, weight=1)
            entry = theme.entry(self.invite_box, font=theme.font(18, "bold"), justify="center")
            entry.insert(0, data["code"])
            entry.configure(state="readonly")
            entry.grid(row=0, column=0, sticky="ew")
            copy = theme.primary_button(self.invite_box, text="Copier", width=0, image=art.icon("copy", theme.ON_ACCENT, 17),
                                        compound="left", command=lambda: self._copy(data["code"]))
            copy.grid(row=0, column=1, padx=(8, 0))
            until = format_date(data.get("expires_at", "")).split(" à ")[0]
            self.invite_msg.configure(text=f"Valable jusqu'au {until}." if until != "date inconnue" else "",
                                      text_color=theme.MUTED)
            self.invite_msg.pack(anchor="w", padx=20, before=self.invite_slot)

        def failed(exc):
            self.btn_invite.configure(state="normal")
            self.invite_msg.configure(text=error_text(exc), text_color=theme.DANGER)
            self.invite_msg.pack(anchor="w", padx=20, before=self.invite_slot)

        run_in_thread(self, client.create_invite, done, failed)

    def _copy(self, code: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(code)
        self.invite_msg.configure(text="Code copié. Collez-le dans un message pour l'envoyer.",
                                  text_color=theme.ACCENT_TEXT)

    # ---- sign out
    def _logout(self) -> None:
        pending = self.app.store.pending_count
        extra = (f"\n\n{pending} changement(s) pas encore envoyé(s) seront gardés et envoyés à votre prochaine "
                 "connexion." if pending else "")
        if not messagebox.askyesno("Se déconnecter", "Voulez-vous vraiment vous déconnecter ?\n\nVos recettes "
                                   "restent sur votre compte, mais elles ne s'afficheront plus ici tant que vous "
                                   "ne vous reconnectez pas." + extra, parent=self):
            return
        self.app.sign_out()
        self.render()
