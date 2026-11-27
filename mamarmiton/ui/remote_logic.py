"""Non-Tk helpers for the account / sync UI (easy to unit-test).

Nothing here imports tkinter: widgets are passed in only as objects with
``after`` / ``after_cancel`` (a Tk widget, or a fake in the tests).
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mamarmiton.remote import AuthError, OfflineError
from mamarmiton.remote.client import ApiError

SYNC_INTERVAL_MS = 60_000
DEBOUNCE_MS = 2_000


# ------------------------------------------------------------------ status
def status_line(status: str, pending: int = 0, error: str = "") -> tuple[str, str, str]:
    """``(icon, text, kind)`` for the sidebar. ``kind`` is "ok", "busy", "warn" or "bad"."""
    if status == "syncing":
        return "reset", "Synchronisation…", "busy"
    if status == "offline":
        text = "Hors ligne"
        if pending:
            text += f" — vos changements seront envoyés plus tard ({pending} en attente)"
        return "cloud_off", text, "warn"
    if status == "auth":
        return "alert", "Reconnectez-vous", "bad"
    if status == "error":
        return "alert", error or "La synchronisation a rencontré un problème", "bad"
    if pending:
        return "reset", f"{pending} changement{'s' if pending > 1 else ''} à envoyer", "busy"
    return "check", "À jour", "ok"


def last_sync_text(when: datetime | None, now: datetime | None = None) -> str:
    if when is None:
        return "Pas encore synchronisé"
    now = now or datetime.now()
    seconds = (now - when).total_seconds()
    if seconds < 90:
        return "Synchronisé à l'instant"
    if when.date() == now.date():
        return f"Synchronisé aujourd'hui à {when:%H:%M}"
    return f"Synchronisé le {when:%d/%m/%Y} à {when:%H:%M}"


# ------------------------------------------------------------------ errors
def error_text(exc: BaseException) -> str:
    """A short French sentence for any failure of a network call."""
    if isinstance(exc, OfflineError):
        return "Impossible de joindre le serveur. Vérifiez votre connexion internet, puis réessayez."
    if isinstance(exc, AuthError):
        return exc.message or "Identifiant ou mot de passe incorrect."
    if isinstance(exc, ApiError):
        return exc.message or "Le serveur a refusé la demande."
    if isinstance(exc, PermissionError):
        return str(exc) or "Vous n'avez pas le droit de faire cela."
    if exc.__class__.__name__ in ("InvalidURL", "UnsupportedProtocol", "LocalProtocolError"):
        return "L'adresse du serveur n'est pas valide."
    return "Une erreur inattendue est survenue. Réessayez dans un instant."


def normalize_server_url(raw: str) -> str:
    """Add https:// when missing, drop spaces and trailing slashes."""
    url = (raw or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


# ----------------------------------------------------------- permissions
def can_edit(store: Any, recipe_id: str) -> bool:
    """Local stores have no permissions: everything is allowed."""
    return bool(getattr(store, "can_edit", lambda _id: True)(recipe_id))


def can_delete(store: Any, recipe_id: str) -> bool:
    return bool(getattr(store, "can_delete", lambda _id: True)(recipe_id))


def is_owner(store: Any, recipe_id: str) -> bool:
    return getattr(store, "access", lambda _id: "owner")(recipe_id) == "owner"


def owner_of(store: Any, recipe_id: str) -> str:
    """Owner's username, or "" for a local store."""
    owner = getattr(store, "owner", None)
    return owner(recipe_id) if owner else ""


def person_name(people: dict[str, str], username: str) -> str:
    """Display name for ``username`` (falls back to the username itself)."""
    return people.get(username) or username


def sharer_label(store: Any, recipe_id: str, people: dict[str, str]) -> str:
    """Name of whoever shared this recipe with me, or "" when it is mine."""
    if is_owner(store, recipe_id):
        return ""
    return person_name(people, owner_of(store, recipe_id))


def unknown_owners(store: Any, people: dict[str, str]) -> bool:
    """True when some recipe belongs to someone whose display name we do not know yet."""
    if not hasattr(store, "owner"):
        return False
    return any(not is_owner(store, r.id) and store.owner(r.id) not in people for r in store.all())


# ----------------------------------------------------------------- upload
def upload_candidates(store: Any, local_store: Any) -> int:
    """How many local recipes are not yet uploaded to this account."""
    import json

    state: dict = {}
    try:
        with open(Path(store.cache_dir) / "upload_state.json", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, AttributeError):
        pass
    return sum(1 for r in local_store.all() if not (state.get(r.id) or {}).get("done"))


# --------------------------------------------------------------- debounce
class Debouncer:
    """Run ``func`` once, ``delay_ms`` after the last ``trigger()`` (Tk ``after`` based)."""

    def __init__(self, widget, delay_ms: int, func: Callable[[], None]):
        self.widget, self.delay_ms, self.func = widget, delay_ms, func
        self._job: Any = None

    def trigger(self) -> None:
        self.cancel()
        self._job = self.widget.after(self.delay_ms, self._fire)

    def cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:  # noqa: BLE001 - widget already destroyed
                pass
            self._job = None

    def _fire(self) -> None:
        self._job = None
        self.func()


class SyncRunner:
    """Runs ``store.sync()`` in a daemon thread: at start, every minute, after changes, on demand.

    ``widget`` supplies ``after`` / ``after_cancel`` on the Tk thread; ``on_done(result)`` is
    always called on the Tk thread. Never blocks: a second request while a sync runs is
    remembered and replayed once the first finishes.
    """

    def __init__(self, widget, on_done: Callable[[Any], None], on_state: Callable[[], None] | None = None,
                 interval_ms: int = SYNC_INTERVAL_MS, debounce_ms: int = DEBOUNCE_MS):
        self.widget, self.on_done, self.on_state = widget, on_done, on_state
        self.interval_ms = interval_ms
        self.store: Any = None
        self.running = False
        self._again = False
        self._timer: Any = None
        self._debounce = Debouncer(widget, debounce_ms, self.sync_now)

    @property
    def active(self) -> bool:
        return self.store is not None and hasattr(self.store, "sync")

    def attach(self, store: Any) -> None:
        """Use ``store`` from now on (a local store disables syncing)."""
        self._debounce.cancel()
        self._cancel_timer()
        self.store = store
        if self.active:
            self.sync_now()
            self._schedule()

    def stop(self) -> None:
        self._debounce.cancel()
        self._cancel_timer()
        self.store = None

    def changed(self) -> None:
        """A local mutation happened: sync soon (debounced)."""
        if self.active:
            self._debounce.trigger()

    def sync_now(self) -> None:
        if not self.active:
            return
        if self.running:
            self._again = True
            return
        self.running = True
        store = self.store
        if self.on_state:
            self.on_state()

        def work() -> None:
            try:
                result = store.sync()
            except Exception as exc:  # noqa: BLE001 - never kill the thread silently
                result = exc
            try:
                self.widget.after(0, lambda: self._finished(store, result))
            except Exception:  # noqa: BLE001 - window closed meanwhile
                pass

        threading.Thread(target=work, daemon=True).start()

    def _finished(self, store: Any, result: Any) -> None:
        self.running = False
        if store is self.store:
            self.on_done(result)
        if self._again and self.active:
            self._again = False
            self.sync_now()

    def _schedule(self) -> None:
        self._timer = self.widget.after(self.interval_ms, self._tick)

    def _tick(self) -> None:
        self._timer = None
        if self.active:
            self.sync_now()
            self._schedule()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self.widget.after_cancel(self._timer)
            except Exception:  # noqa: BLE001
                pass
            self._timer = None


def run_in_thread(widget, work: Callable[[], Any], on_ok: Callable[[Any], None] | None = None,
                  on_error: Callable[[BaseException], None] | None = None) -> None:
    """Run ``work`` off the Tk thread; call ``on_ok(result)`` / ``on_error(exc)`` back on it.

    The callbacks are dropped when ``widget`` has been destroyed meanwhile.
    """
    def alive() -> bool:
        try:
            return bool(widget.winfo_exists())
        except Exception:  # noqa: BLE001
            return False

    def target() -> None:
        try:
            result, error = work(), None
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            result, error = None, exc

        def deliver() -> None:
            if not alive():
                return
            if error is not None:
                if on_error:
                    on_error(error)
            elif on_ok:
                on_ok(result)

        try:
            widget.after(0, deliver)
        except Exception:  # noqa: BLE001 - window closed
            pass

    threading.Thread(target=target, daemon=True).start()


# --------------------------------------------------------------- conflicts
def format_date(iso: str) -> str:
    """"12/03/2026 à 14:30" from an ISO timestamp ("date inconnue" when unreadable)."""
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "date inconnue"
    return f"{when:%d/%m/%Y} à {when:%H:%M}"


def recipe_sections(recipe: Any) -> list[tuple[str, str]]:
    """The parts of a recipe a person compares: ``(title, text)``."""
    from mamarmiton.models import format_minutes

    facts = [f"Pour {recipe.pour} personne{'s' if recipe.pour > 1 else ''}"]
    if recipe.temps:
        facts.append(f"préparation {format_minutes(recipe.temps)}")
    if recipe.temps_cuisson:
        facts.append(f"cuisson {format_minutes(recipe.temps_cuisson)}")
    ingredients = "\n".join(f"• {(i.quantite + ' ') if i.quantite else ''}{i.nom}" for i in recipe.ingredients)
    steps = "\n".join(f"{n}. {s}" for n, s in enumerate(recipe.etapes, 1))
    return [("Nom", recipe.nom), ("Détails", ", ".join(facts)), ("Ingrédients", ingredients or "—"),
            ("Étapes", steps or "—"), ("Notes", recipe.notes or "—")]


def conflict_rows(mine: Any, theirs: Any) -> list[tuple[str, str, str, bool]]:
    """``(title, my text, their text, differs)`` for each section."""
    return [(title, a, b, a != b) for (title, a), (_t, b) in zip(recipe_sections(mine), recipe_sections(theirs))]
