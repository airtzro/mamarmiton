"""Server-backed recipe book (accounts, sync, sharing) with an offline cache."""

from __future__ import annotations

from mamarmiton.remote.account import DEFAULT_SERVER_URL, Account, clear_account, load_account, save_account
from mamarmiton.remote.client import ApiClient, ApiError, AuthError, ConflictError, OfflineError
from mamarmiton.remote.store import Conflict, RemoteStore, SyncResult
from mamarmiton.storage import RecipeStore


def open_store(local_fallback: bool = True) -> tuple["RemoteStore | RecipeStore | None", Account | None]:
    """The store to use at start-up, with the account behind it.

    A saved account gives ``(RemoteStore, account)``. Without one the plain
    local ``RecipeStore`` is returned (``(None, None)`` if ``local_fallback`` is False).
    """
    account = load_account()
    if account is not None:
        client = ApiClient(account.server_url, token=account.token)
        client.user = {"username": account.username, "display_name": account.display_name,
                       "is_admin": account.is_admin}
        return RemoteStore(client, username=account.username), account
    return (RecipeStore() if local_fallback else None), None


__all__ = [
    "Account", "ApiClient", "ApiError", "AuthError", "Conflict", "ConflictError", "DEFAULT_SERVER_URL",
    "OfflineError", "RemoteStore", "SyncResult", "clear_account", "load_account", "open_store", "save_account",
]
