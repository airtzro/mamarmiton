"""Thin typed HTTP client for the Mamarmiton server.

Every method returns the decoded JSON (or ``None`` for empty answers) and
raises one of:

* ``AuthError`` (401): not signed in, wrong password, or the session expired.
* ``ConflictError`` (409): ``.current`` holds the server's version of the
  recipe (an API "recipe_out" dict) when the server sent it.
* ``OfflineError``: network failure, timeout, DNS error or a 5xx answer.
* ``UpgradeRequired`` (426, a kind of ``OfflineError`` so queued changes are kept): this app is older
  than the server's minimum; ``.min_version`` says which version is needed.
* ``ApiError`` (any other 4xx): ``.status`` and a French ``.message``.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from mamarmiton import __version__

TIMEOUT = 15.0


class ApiError(Exception):
    """The server refused a request. ``message`` is meant to be shown to the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AuthError(ApiError):
    """HTTP 401."""


class ConflictError(ApiError):
    """HTTP 409; ``current`` is the server's recipe when it was sent."""

    def __init__(self, status: int, message: str, current: dict | None = None):
        super().__init__(status, message)
        self.current = current


class OfflineError(ApiError):
    """The server cannot be reached (or is failing with a 5xx)."""

    def __init__(self, message: str = "Pas de connexion au serveur.", status: int = 0):
        super().__init__(status, message)


class UpgradeRequired(OfflineError):
    """HTTP 426: the server wants a newer desktop app. Nothing is lost: the local data stays as it is."""

    def __init__(self, message: str, min_version: str = ""):
        super().__init__(message, 426)
        self.min_version = min_version


class ApiClient:
    """Wrapper over the HTTP API.

    ``http`` may be any ``httpx.Client`` (tests pass Starlette's ``TestClient``).
    ``token`` is sent as a Bearer token; ``login``/``register`` set it.
    ``username`` / ``user`` are filled in by login, register and ``me``.
    """

    def __init__(self, base_url: str, token: str | None = None, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.user: dict | None = None
        self._http = http or httpx.Client(timeout=TIMEOUT)

    @property
    def username(self) -> str:
        return (self.user or {}).get("username", "")

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ plumbing
    def _request(self, method: str, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        headers = {"X-Mamarmiton-Client": f"desktop/{__version__}", **kwargs.pop("headers", {})}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._http.request(method, self.base_url + path, headers=headers, **kwargs)
        except httpx.TransportError as exc:  # connect, DNS, timeout, reset...
            raise OfflineError() from exc
        if response.status_code == 426:
            raise self._upgrade_required(response)
        if response.status_code >= 500:
            raise OfflineError("Le serveur ne répond pas correctement.", response.status_code)
        if response.status_code >= 400:
            raise self._error(response)
        if raw:
            return response.content
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _upgrade_required(response: httpx.Response) -> UpgradeRequired:
        message, minimum = "Cette version de Mamarmiton est trop ancienne. Mettez-la à jour.", ""
        try:
            body = response.json()
            message = str(body.get("detail") or message)
            minimum = str(body.get("min_version") or "")
        except (ValueError, AttributeError):
            pass
        return UpgradeRequired(message, minimum)

    @staticmethod
    def _error(response: httpx.Response) -> ApiError:
        status = response.status_code
        detail: Any = None
        current = None
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            pass
        if isinstance(detail, dict):
            current = detail.get("current")
            message = str(detail.get("message") or "")
        elif isinstance(detail, list):  # pydantic validation errors
            message = "Les informations envoyées sont incorrectes."
        else:
            message = str(detail or "")
        message = message or f"Erreur du serveur ({status})."
        if status == 401:
            return AuthError(status, message)
        if status == 409:
            return ConflictError(status, message, current)
        return ApiError(status, message)

    # ---------------------------------------------------------------- auth
    def login(self, username: str, password: str, device: str = "") -> dict:
        data = self._request("POST", "/auth/login", json={"username": username, "password": password,
                                                          "device": device})
        self.token, self.user = data["token"], data["user"]
        return data

    def register(self, username: str, password: str, invite: str, display_name: str = "",
                 device: str = "") -> dict:
        data = self._request("POST", "/auth/register", json={
            "username": username, "password": password, "invite": invite,
            "display_name": display_name, "device": device})
        self.token, self.user = data["token"], data["user"]
        return data

    def logout(self) -> None:
        try:
            self._request("POST", "/auth/logout")
        finally:
            self.token = None

    def me(self) -> dict:
        self.user = self._request("GET", "/me")
        return self.user

    def change_password(self, old_password: str, new_password: str) -> None:
        self._request("POST", "/me/password", json={"old_password": old_password,
                                                    "new_password": new_password})

    def users(self) -> list[dict]:
        return self._request("GET", "/users")

    def create_invite(self, note: str = "") -> dict:
        """Admin only. Returns ``{code, expires_at, note}``."""
        return self._request("POST", "/invites", json={"note": note})

    # ----------------------------------------------------- usage, reports
    def send_usage(self, client_id: str, days: dict) -> None:
        """Anonymous counters: ``{"2026-11-27": {"recipe_open": 3}}`` (see ``mamarmiton.usage``)."""
        self._request("POST", "/usage", json={"client": client_id, "days": days})

    def send_report(self, report: dict) -> None:
        """"Signaler un problème": ``{message, client, app_version, os, log_tail}``."""
        self._request("POST", "/reports", json=report)

    # ------------------------------------------------------------- recipes
    def list_recipes(self, since: int = 0) -> dict:
        """``{seq, recipes: [...], visible_ids, favorites}`` for changes after ``since``."""
        return self._request("GET", "/recipes", params={"since": since})

    def get_recipe(self, recipe_id: str) -> dict:
        return self._request("GET", f"/recipes/{recipe_id}")

    def put_recipe(self, recipe_id: str, recipe: dict, base_version: int | None) -> dict:
        """Create (``base_version=None``) or update. Raises ``ConflictError``."""
        return self._request("PUT", f"/recipes/{recipe_id}", json={"recipe": recipe,
                                                                   "base_version": base_version})

    def delete_recipe(self, recipe_id: str) -> None:
        self._request("DELETE", f"/recipes/{recipe_id}")

    def set_favorite(self, recipe_id: str, value: bool) -> None:
        self._request("PUT" if value else "DELETE", f"/recipes/{recipe_id}/favorite")

    # -------------------------------------------------------------- images
    def upload_image(self, recipe_id: str, path: Path | str) -> dict:
        """Upload a photo; returns ``{id, content_type, size}``."""
        path = Path(path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self._request("POST", f"/recipes/{recipe_id}/images",
                             files={"file": (path.name, path.read_bytes(), content_type)})

    def download_image(self, recipe_id: str, image_id: str) -> bytes:
        return self._request("GET", f"/recipes/{recipe_id}/images/{image_id}", raw=True)

    def delete_image(self, recipe_id: str, image_id: str) -> None:
        self._request("DELETE", f"/recipes/{recipe_id}/images/{image_id}")

    # -------------------------------------------------------------- shares
    def shares(self) -> dict:
        """``{book_shared_with, books_shared_with_me, recipes_shared}``."""
        return self._request("GET", "/shares")

    def share_book(self, username: str, can_edit: bool = False) -> None:
        self._request("PUT", f"/shares/book/{username}", json={"can_edit": can_edit})

    def unshare_book(self, username: str) -> None:
        self._request("DELETE", f"/shares/book/{username}")

    def leave_book(self, username: str) -> None:
        """Stop seeing the book of ``username`` (the receiving side)."""
        self._request("DELETE", f"/shares/received/{username}")

    def share_recipe(self, recipe_id: str, username: str, can_edit: bool = False) -> None:
        self._request("PUT", f"/recipes/{recipe_id}/shares/{username}", json={"can_edit": can_edit})

    def unshare_recipe(self, recipe_id: str, username: str) -> None:
        self._request("DELETE", f"/recipes/{recipe_id}/shares/{username}")
