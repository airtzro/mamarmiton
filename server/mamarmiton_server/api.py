"""HTTP API.

Every recipe belongs to one account (its "book"). An owner can share the
whole book, or single recipes, with other accounts: read only, read and make
variants (copies of their own), or edit. Every recipe carries a permanent
credit (``author``), set by the server: a variant keeps the author of the
recipe it came from, so the name of whoever wrote it follows every copy.
Accounts are created with an invite code only.

Sync: ``GET /recipes?since=N`` returns what changed after change number N,
plus the ids of every recipe the caller can still see (so a device also
learns about deletions and revoked shares) and the caller's favourites.
"""


import json
import logging
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Iterator, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from mamarmiton.models import Recipe
from mamarmiton.redact import redact
from mamarmiton_server import __version__, admin, db, images, importer, portable, security, usage
from mamarmiton_server.config import Settings

log = logging.getLogger(__name__)

Access = Literal["owner", "edit", "read"]

# limits on what one recipe may hold
MAX_TEXT = 4000
MAX_NOTES = 20000
MAX_ITEMS = 300
MAX_NUMBER = 1_000_000  # minutes / servings
MAX_JSON_BODY = 2 * 1024 * 1024  # a maximal recipe is well under 2 MB
MAX_RECIPES_PER_USER = 5000

NO_VARIANT_MSG = "La personne qui vous a partagé cette recette ne permet pas d'en faire des variantes."


class BodyLimitMiddleware:
    """Refuse oversized request bodies while they stream in (Content-Length can be absent or a lie)."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.image_limit = (settings.max_image_mb + 1) * 1024 * 1024
        # the files themselves are checked against import_max_mb; +1 MB for the form around them
        self.import_limit = (settings.import_max_mb + 1) * 1024 * 1024
        self.backup_limit = (settings.backup_max_mb + 1) * 1024 * 1024
        self.backup_message = json.dumps(
            {"detail": f"Fichier trop lourd : {settings.backup_max_mb} Mo au maximum."}).encode()
        self.import_message = json.dumps(
            {"detail": f"Fichiers trop lourds : {settings.import_max_mb} Mo au maximum en tout."}).encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        is_upload = scope["method"] == "POST" and scope["path"].endswith("/images")
        is_import = scope["method"] == "POST" and scope["path"] == "/import/recipe"
        is_backup = scope["method"] == "POST" and scope["path"] == "/import/backup"
        limit = (self.backup_limit if is_backup else self.import_limit if is_import
                 else self.image_limit if is_upload else MAX_JSON_BODY)
        started = False

        async def too_large():
            body = self.backup_message if is_backup else self.import_message if is_import else b'{"detail":"Requ\xc3\xaate trop volumineuse."}'
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"connection", b"close"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            return await too_large()
        received = 0
        rejected = False

        async def limited_receive():
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # answer now and pretend the client left: FastAPI would turn an exception
                    # raised from here into a 400 "bad body"
                    rejected = True
                    if not started:
                        await too_large()
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, tracking_send)


class SecurityHeadersMiddleware:
    """nosniff everywhere; API answers (tokens, recipes) are never cached unless a route says so."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message["headers"]}
                extra = []
                if b"x-content-type-options" not in names:
                    extra.append((b"x-content-type-options", b"nosniff"))
                if b"cache-control" not in names:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message["headers"], *extra]}
            await send(message)

        await self.app(scope, receive, wrapped)


def parse_version(text: str) -> tuple[int, ...] | None:
    """"4.1.0" or "v4.1" -> (4, 1, 0); None when it is not a plain dotted version."""
    m = re.fullmatch(r"v?(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?", (text or "").strip())
    return tuple(int(g or 0) for g in m.groups()) if m else None


class MinVersionMiddleware:
    """Answer 426 to a desktop client older than ``min_desktop_version``.

    Clients say who they are with ``X-Mamarmiton-Client: desktop/4.0.0`` (or ``web/...``).
    A missing, malformed or non-desktop header is always let through (curl, health checks, the web app,
    which the server itself serves at its current version).
    """

    def __init__(self, app, settings: Settings):
        self.app = app
        self.minimum = settings.min_desktop_version
        self.minimum_tuple = parse_version(self.minimum)
        if self.minimum and self.minimum_tuple is None:
            log.warning("MIN_DESKTOP_VERSION %r is not a version; ignored", self.minimum)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.minimum_tuple is not None:
            raw = dict(scope["headers"]).get(b"x-mamarmiton-client", b"").decode("latin-1")
            kind, _, version = raw.partition("/")
            have = parse_version(version)
            if kind.strip().lower() == "desktop" and have is not None and have < self.minimum_tuple:
                body = json.dumps({"detail": "Cette version de Mamarmiton est trop ancienne. Mettez-la à jour.",
                                   "min_version": self.minimum}).encode()
                await send({"type": "http.response.start", "status": 426,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


# ------------------------------------------------------------------ schemas
class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=security.MAX_PASSWORD)
    device: str = Field(default="", max_length=100)


class Registration(Credentials):
    invite: str = Field(max_length=64)
    display_name: str = Field(default="", max_length=60)


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=security.MAX_PASSWORD)
    new_password: str = Field(max_length=security.MAX_PASSWORD)


class InviteRequest(BaseModel):
    note: str = Field(default="", max_length=100)


class RecipeWrite(BaseModel):
    recipe: dict
    # the version this edit is based on; None = create a new recipe
    base_version: int | None = None
    # on create only: the recipe this one is a variant of (needs the right to make variants of it)
    variant_of: str | None = None


class UsageBatch(BaseModel):
    client: str = Field(default="", max_length=64)  # "desktop/4.1.0": only "desktop" or "web" is kept
    days: dict = Field(default_factory=dict)        # {"2026-11-27": {"recipe_open": 3}}, validated by usage.clean_batch


MAX_REPORT_MESSAGE = 4000
MAX_REPORT_LOG = 64 * 1024  # bytes
MAX_REPORTS_PER_DAY = 20    # per account
MAX_REPORT_CATEGORY_OTHER = 80


class ReportBody(BaseModel):
    message: str = Field(max_length=MAX_REPORT_MESSAGE)
    # Older apps send no category: they only had "Signaler un problème", so "bug".
    category: Literal["bug", "idea", "other"] = "bug"
    category_other: str = Field(default="", max_length=MAX_REPORT_CATEGORY_OTHER)  # required for "other"
    client: str = Field(default="", max_length=64)
    app_version: str = Field(default="", max_length=64)
    os: str = Field(default="", max_length=300)
    log_tail: str = Field(default="", max_length=MAX_REPORT_LOG)


class ShareRequest(BaseModel):
    can_edit: bool = False
    # may the person make variants of it? None (older apps) = leave as it was; a new share allows it.
    # Editing always allows it.
    can_copy: bool | None = None


# ------------------------------------------------------------------ helpers
def _error(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def clean_recipe(raw: dict, recipe_id: str) -> dict:
    """Normalise a recipe the way the desktop app does, and enforce size limits.

    ``favori`` (per user) and ``images`` (managed by the image endpoints) are
    not stored in the recipe itself.
    """
    tags = raw.get("tags") if isinstance(raw, dict) else None
    if tags is not None and (not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)):
        raise _error(422, "Les étiquettes doivent être une liste de textes.")
    if tags and len(tags) > MAX_ITEMS:
        raise _error(422, "Trop d'étiquettes.")
    try:
        recipe = Recipe.from_dict(raw)
    except (ValueError, TypeError, OverflowError) as exc:  # OverflowError: {"temps": Infinity}
        raise _error(422, f"Recette invalide : {exc}") from exc
    recipe.id = recipe_id
    data = recipe.to_dict()
    data.pop("favori", None)
    data.pop("images", None)
    if any(abs(data[k]) > MAX_NUMBER for k in ("temps", "temps_cuisson", "pour")):
        raise _error(422, "Valeur numérique trop grande.")
    if len(data["nom"]) > 200:
        raise _error(422, "Le nom de la recette est trop long.")
    if len(data["notes"]) > MAX_NOTES:
        raise _error(422, "Les notes sont trop longues.")
    if len(data["ingredients"]) > MAX_ITEMS or len(data["etapes"]) > MAX_ITEMS:
        raise _error(422, "Trop d'ingrédients ou d'étapes.")
    texts = [data["source"], data["pour_unite"], data["cree_le"], data["modifie_le"], *data["etapes"]]
    texts += [part for i in data["ingredients"] for part in (i["nom"], i["quantité"])]
    if any(len(t) > MAX_TEXT for t in texts):
        raise _error(422, "Un des textes de la recette est trop long.")
    return data


def valid_recipe_id(recipe_id: str) -> bool:
    # the desktop app uses uuid4().hex; also used as a folder name, so stay strict
    return 8 <= len(recipe_id) <= 64 and all(c in "0123456789abcdefABCDEF-" for c in recipe_id)


WEB_DIR = Path(__file__).parent / "web"
WEB_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ttf": "font/ttf",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
}
WEB_COMMON_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
# no inline script or style: anything injected into the page cannot run or phone home
WEB_CSP = ("default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; "
           "font-src 'self'; connect-src 'self'; manifest-src 'self'; object-src 'none'; "
           "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
WEB_PAGE_HEADERS = {**WEB_COMMON_HEADERS, "Content-Security-Policy": WEB_CSP, "Cache-Control": "no-cache",
                    "X-Frame-Options": "DENY"}


def _web_file(asset_path: str) -> Path | None:
    """The file under web/ for a URL path, or None (unknown, outside web/, or a type we do not serve)."""
    if not asset_path or "\\" in asset_path or "\x00" in asset_path:
        return None
    root = WEB_DIR.resolve()
    path = (root / asset_path).resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() not in WEB_TYPES:
        return None
    return path


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    db.init(settings.db_path)
    limiter = security.LoginLimiter(settings.login_attempts, settings.login_window_s)

    app = FastAPI(title="Mamarmiton", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.limiter = limiter
    app.add_middleware(BodyLimitMiddleware, settings=settings)
    app.add_middleware(MinVersionMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware)

    # -------------------------------------------------------- dependencies
    def get_db() -> Iterator[sqlite3.Connection]:
        conn = db.connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(get_db)]

    def current_session(request: Request, conn: Conn) -> sqlite3.Row:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Connexion requise.", headers={"WWW-Authenticate": "Bearer"})
        row = conn.execute(
            "SELECT s.token_hash, s.last_used, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?", (security.sha256(token),)).fetchone()
        if row is None:
            raise HTTPException(401, "Session expirée, reconnectez-vous.", headers={"WWW-Authenticate": "Bearer"})
        last_used = datetime.fromisoformat(row["last_used"])
        now = datetime.now(timezone.utc)
        if now - last_used > timedelta(days=settings.token_idle_days):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            raise HTTPException(401, "Session expirée, reconnectez-vous.", headers={"WWW-Authenticate": "Bearer"})
        if now - last_used > timedelta(hours=1):  # do not write on every request
            conn.execute("UPDATE sessions SET last_used = ? WHERE token_hash = ?", (db.now(), row["token_hash"]))
        return row

    Session = Annotated[sqlite3.Row, Depends(current_session)]

    def require_admin(user: Session) -> sqlite3.Row:
        if not user["is_admin"]:
            raise _error(403, "Réservé à l'administrateur.")
        return user

    Admin = Annotated[sqlite3.Row, Depends(require_admin)]

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def access_to(conn: sqlite3.Connection, user_id: int, recipe: sqlite3.Row,
                  include_hidden: bool = False) -> Access | None:
        if recipe["owner_id"] == user_id:
            return "owner"
        if recipe["deleted"]:
            return None
        if not include_hidden and conn.execute("SELECT 1 FROM hidden_recipes WHERE user_id = ? AND recipe_id = ?",
                                               (user_id, recipe["id"])).fetchone():
            return None  # taken off my list: as if it were not shared
        rows = conn.execute(
            "SELECT can_edit FROM recipe_shares WHERE recipe_id = ? AND grantee_id = ? "
            "UNION ALL SELECT can_edit FROM book_shares WHERE owner_id = ? AND grantee_id = ?",
            (recipe["id"], user_id, recipe["owner_id"], user_id)).fetchall()
        if not rows:
            return None
        return "edit" if any(r["can_edit"] for r in rows) else "read"

    def may_copy(conn: sqlite3.Connection, user_id: int, recipe: sqlite3.Row) -> bool:
        """May this person make a variant of the recipe? Owner and editors always; readers if the
        share says so."""
        if recipe["owner_id"] == user_id:
            return True
        return conn.execute(
            "SELECT 1 FROM recipe_shares WHERE recipe_id = ? AND grantee_id = ? AND (can_edit OR can_copy) "
            "UNION ALL SELECT 1 FROM book_shares WHERE owner_id = ? AND grantee_id = ? AND (can_edit OR can_copy)",
            (recipe["id"], user_id, recipe["owner_id"], user_id)).fetchone() is not None

    def load_recipe(conn: sqlite3.Connection, user_id: int, recipe_id: str,
                    need: tuple[Access, ...] = ("owner", "edit", "read"),
                    allow_deleted: bool = False) -> tuple[sqlite3.Row, Access]:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        access = access_to(conn, user_id, row) if row else None
        # a tombstone is only visible to its owner, and only where asked (never a target for photos etc.)
        if row is None or access is None or (row["deleted"] and (access != "owner" or not allow_deleted)):
            raise _error(404, "Recette introuvable.")  # never reveal recipes you cannot see
        if access not in need:
            raise _error(403, "Vous ne pouvez pas modifier cette recette.")
        return row, access

    # recipes the user can see: own, from shared books, shared one by one
    VISIBLE = """
        SELECT r.*, u.username AS owner_name, u.display_name AS owner_display,
            CASE WHEN r.owner_id = :uid THEN 'owner'
                 WHEN EXISTS (SELECT 1 FROM book_shares b WHERE b.owner_id = r.owner_id
                                  AND b.grantee_id = :uid AND b.can_edit)
                   OR EXISTS (SELECT 1 FROM recipe_shares s WHERE s.recipe_id = r.id
                                  AND s.grantee_id = :uid AND s.can_edit) THEN 'edit'
                 ELSE 'read' END AS access,
            CASE WHEN r.owner_id = :uid
                   OR EXISTS (SELECT 1 FROM book_shares b WHERE b.owner_id = r.owner_id
                                  AND b.grantee_id = :uid AND (b.can_edit OR b.can_copy))
                   OR EXISTS (SELECT 1 FROM recipe_shares s WHERE s.recipe_id = r.id
                                  AND s.grantee_id = :uid AND (s.can_edit OR s.can_copy)) THEN 1
                 ELSE 0 END AS can_copy
        FROM recipes r JOIN users u ON u.id = r.owner_id
        WHERE (r.owner_id = :uid AND (r.deleted = 0 OR :tombstones))
           OR (r.deleted = 0 AND (
                EXISTS (SELECT 1 FROM book_shares b WHERE b.owner_id = r.owner_id AND b.grantee_id = :uid)
             OR EXISTS (SELECT 1 FROM recipe_shares s WHERE s.recipe_id = r.id AND s.grantee_id = :uid))
             AND NOT EXISTS (SELECT 1 FROM hidden_recipes x WHERE x.user_id = :uid AND x.recipe_id = r.id))
    """

    def recipe_out(conn: sqlite3.Connection, row: sqlite3.Row, user_id: int, access: Access) -> dict:
        data = json.loads(row["data"])
        pics = conn.execute("SELECT id, content_type, size FROM images WHERE recipe_id = ? ORDER BY position, "
                            "created_at", (row["id"],)).fetchall()
        owner = row["owner_name"] if "owner_name" in row.keys() else conn.execute(
            "SELECT username FROM users WHERE id = ?", (row["owner_id"],)).fetchone()[0]
        can_copy = bool(row["can_copy"]) if "can_copy" in row.keys() else (
            access in ("owner", "edit") or may_copy(conn, user_id, row))
        return {
            "id": row["id"],
            "recipe": data,
            "version": row["version"],
            "seq": row["seq"],
            "updated_at": row["updated_at"],
            "deleted": bool(row["deleted"]),
            "owner": owner,
            "access": access,
            "can_copy": can_copy,
            "credit": credit_of(conn, row),
            "images": [dict(p) for p in pics],
        }

    def credit_of(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        """Who wrote the recipe (and which recipe it is a variant of). Never editable by clients."""
        author = conn.execute("SELECT username, display_name FROM users WHERE id = ?",
                              (row["author_id"],)).fetchone() if row["author_id"] is not None else None
        return {
            "author": author["username"] if author else None,  # None: the author's account is gone
            "author_name": (author["display_name"] if author else "") or row["author_name"],
            "variant_of": row["variant_of"],
            "variant_of_nom": row["variant_of_nom"],
        }

    def credit_for_new(conn: sqlite3.Connection, user: sqlite3.Row, variant_of: str | None) -> tuple:
        """(author_id, author_name, variant_of, variant_of_nom) for a recipe being created by ``user``.

        A variant keeps the author of its source; only someone allowed to make variants may create one.
        A source deleted meanwhile still passes its credit on (its tombstone keeps it).
        """
        if variant_of is None:
            return user["id"], user["display_name"], None, ""
        source = conn.execute("SELECT * FROM recipes WHERE id = ?", (variant_of,)).fetchone() \
            if valid_recipe_id(variant_of) else None
        if source is None:
            raise _error(404, "La recette d'origine de cette variante est introuvable.")
        if not source["deleted"]:
            if access_to(conn, user["id"], source, include_hidden=True) is None:
                raise _error(404, "La recette d'origine de cette variante est introuvable.")
            if not may_copy(conn, user["id"], source):
                raise _error(403, NO_VARIANT_MSG)
        nom = json.loads(source["data"]).get("nom", "") if not source["deleted"] else ""
        return source["author_id"], source["author_name"], source["id"], str(nom)[:200]

    def insert_recipe(conn: sqlite3.Connection, recipe_id: str, owner_id: int, data: dict, version: int,
                      credit: tuple) -> None:
        conn.execute("INSERT OR REPLACE INTO recipes (id, owner_id, data, version, seq, updated_at, deleted, "
                     "author_id, author_name, variant_of, variant_of_nom) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                     (recipe_id, owner_id, json.dumps(data, ensure_ascii=False), version, db.next_seq(conn),
                      db.now(), *credit))

    def bump(conn: sqlite3.Connection, where: str, params: tuple) -> None:
        """Mark recipes as changed so devices pick them up on their next sync."""
        conn.execute(f"UPDATE recipes SET seq = ?, updated_at = ? WHERE {where}", (db.next_seq(conn), db.now(), *params))

    def user_by_name(conn: sqlite3.Connection, username: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (security.normalize_username(username),)).fetchone()
        if row is None:
            raise _error(404, "Utilisateur introuvable.")
        return row

    def public_user(row: sqlite3.Row) -> dict:
        return {"username": row["username"], "display_name": row["display_name"]}

    # ------------------------------------------------------------ web page
    # The recipe book as a web page: static files, no build step. They live
    # under /app so they can never collide with an API route.
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def web_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8", headers=WEB_PAGE_HEADERS)

    @app.api_route("/app/{asset_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_asset(asset_path: str) -> Response:
        if asset_path == "sw.js":
            # the service worker lives under /app but controls the whole site (photos are under /recipes);
            # the release number in it makes a new version replace the old shell cache
            code = (WEB_DIR / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", __version__)
            return Response(code, media_type=WEB_TYPES[".js"], headers={
                **WEB_COMMON_HEADERS, "Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
        path = _web_file(asset_path)
        if path is None:
            raise _error(404, "Page introuvable.")
        media_type = WEB_TYPES[path.suffix.lower()]
        # code and pages are re-checked on every visit so updates reach phones;
        # fonts and icons never change under the same name
        static = path.suffix.lower() in (".woff2", ".ttf", ".png")
        headers = {**WEB_COMMON_HEADERS, "Cache-Control": "public, max-age=604800" if static else "no-cache"}
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.api_route("/admin", methods=["GET", "HEAD"], include_in_schema=False)
    def admin_page() -> FileResponse:
        # the page itself is public (it is only a login form + code); every number comes from /admin/api
        return FileResponse(WEB_DIR / "admin" / "index.html", media_type="text/html; charset=utf-8",
                            headers={**WEB_PAGE_HEADERS, "X-Robots-Tag": "noindex, nofollow"})

    admin.register(app, settings, limiter, Conn, Admin, client_ip)

    # --------------------------------------------------------------- misc
    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/version")
    def version() -> dict:
        return {"server": __version__, "min_desktop": settings.min_desktop_version or None}

    # --------------------------------------------------------------- auth
    def _open_session(conn: sqlite3.Connection, user_id: int, device: str) -> str:
        token = security.new_token()
        now = db.now()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.token_idle_days)).isoformat(timespec="microseconds")
        conn.execute("DELETE FROM sessions WHERE last_used < ?", (cutoff,))  # tokens nobody will use again
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                     (security.sha256(token), user_id, device.strip()[:100], now, now))
        return token

    @app.post("/auth/register", status_code=201)
    def register(body: Registration, request: Request, conn: Conn) -> dict:
        ip = client_ip(request)
        # invite guessing counts as failed logins too
        wait = limiter.retry_after(ip, "")
        if wait:
            raise HTTPException(429, "Trop d'essais. Réessayez plus tard.", headers={"Retry-After": str(wait)})
        username = security.normalize_username(body.username)
        if not security.valid_username(username):
            raise _error(422, "Nom d'utilisateur : 2 à 32 lettres minuscules, chiffres, point, tiret ou souligné.")
        problem = security.password_problem(body.password, username)
        if problem:
            raise _error(422, problem)
        code_hash = security.sha256(security.normalize_invite(body.invite))
        password_hash = security.hash_password(body.password)
        with db.transaction(conn):
            invite = conn.execute("SELECT * FROM invites WHERE code_hash = ? AND used_by IS NULL AND expires_at > ?",
                                  (code_hash, db.now())).fetchone()
            if invite is None:
                limiter.failed(ip, "")
                raise _error(403, "Code d'invitation invalide ou expiré.")
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise _error(409, "Ce nom d'utilisateur est déjà pris.")
            user_id = conn.execute(
                "INSERT INTO users (username, display_name, password_hash, is_admin, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (username, body.display_name.strip() or username, password_hash, db.now())).lastrowid
            conn.execute("UPDATE invites SET used_by = ? WHERE code_hash = ?", (user_id, code_hash))
            token = _open_session(conn, user_id, body.device)
        return {"token": token, "user": {"username": username, "display_name": body.display_name.strip() or username,
                                         "is_admin": False}}

    @app.post("/auth/login")
    def login(body: Credentials, request: Request, conn: Conn) -> dict:
        ip = client_ip(request)
        username = security.normalize_username(body.username)
        wait = limiter.retry_after(ip, username)
        if wait:
            raise HTTPException(429, "Trop d'essais. Réessayez dans quelques minutes.",
                                headers={"Retry-After": str(wait)})
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not security.verify_password(user["password_hash"] if user else None, body.password):
            limiter.failed(ip, username)
            raise _error(401, "Nom d'utilisateur ou mot de passe incorrect.")
        limiter.succeeded(ip, username)
        if security.needs_rehash(user["password_hash"]):
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (security.hash_password(body.password), user["id"]))
        token = _open_session(conn, user["id"], body.device)
        return {"token": token, "user": {"username": user["username"], "display_name": user["display_name"],
                                         "is_admin": bool(user["is_admin"])}}

    @app.post("/auth/logout", status_code=204)
    def logout(session: Session, conn: Conn) -> Response:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
        return Response(status_code=204)

    @app.get("/me")
    def me(user: Session) -> dict:
        return {"username": user["username"], "display_name": user["display_name"], "is_admin": bool(user["is_admin"])}

    @app.post("/me/password", status_code=204)
    def change_password(body: PasswordChange, request: Request, user: Session, conn: Conn) -> Response:
        # a stolen token must not allow guessing the current password offline-fast
        key = (client_ip(request), "pw:" + user["username"])
        wait = limiter.retry_after(*key)
        if wait:
            raise HTTPException(429, "Trop d'essais. Réessayez plus tard.", headers={"Retry-After": str(wait)})
        if not security.verify_password(user["password_hash"], body.old_password):
            limiter.failed(*key)
            raise _error(403, "L'ancien mot de passe est incorrect.")
        problem = security.password_problem(body.new_password, user["username"])
        if problem:
            raise _error(422, problem)
        with db.transaction(conn):
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (security.hash_password(body.new_password), user["id"]))
            # sign out every other device
            conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                         (user["id"], user["token_hash"]))
        return Response(status_code=204)

    @app.get("/users")
    def list_users(user: Session, conn: Conn) -> list[dict]:
        """Everyone on this (invite-only) server, to pick whom to share with."""
        rows = conn.execute("SELECT username, display_name FROM users WHERE id != ? ORDER BY display_name",
                            (user["id"],)).fetchall()
        return [public_user(r) for r in rows]

    # ------------------------------------------------------------- invites
    @app.post("/invites", status_code=201)
    def create_invite(body: InviteRequest, admin: Admin, conn: Conn) -> dict:
        code = security.new_invite_code()
        expires = datetime.now(timezone.utc) + timedelta(days=settings.invite_days)
        conn.execute("INSERT INTO invites (code_hash, created_by, note, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                     (security.sha256(code), admin["id"], body.note.strip(), db.now(),
                      expires.isoformat(timespec="microseconds")))
        return {"code": code, "expires_at": expires.isoformat(timespec="seconds"), "note": body.note.strip()}

    # ------------------------------------------------------------- recipes
    @app.get("/recipes")
    def list_recipes(user: Session, conn: Conn, since: Annotated[int, Query(ge=0, le=2**62)] = 0) -> dict:
        uid = user["id"]
        conn.execute("BEGIN")  # one consistent snapshot for everything below
        try:
            seq = db.current_seq(conn)
            changed = conn.execute(f"SELECT * FROM ({VISIBLE}) WHERE seq > :since ORDER BY seq",
                                   {"uid": uid, "tombstones": 1 if since else 0, "since": since}).fetchall()
            visible = conn.execute(f"SELECT id FROM ({VISIBLE})", {"uid": uid, "tombstones": 0}).fetchall()
            favorites = conn.execute("SELECT recipe_id FROM favorites WHERE user_id = ?", (uid,)).fetchall()
            recipes = [recipe_out(conn, r, uid, r["access"]) for r in changed]
        finally:
            conn.execute("COMMIT")
        return {
            "seq": seq,  # pass this as ?since= next time
            "recipes": recipes,
            "visible_ids": [r["id"] for r in visible],
            "favorites": [r["recipe_id"] for r in favorites],
        }

    @app.get("/recipes/{recipe_id}")
    def get_recipe(recipe_id: str, user: Session, conn: Conn) -> dict:
        row, access = load_recipe(conn, user["id"], recipe_id, allow_deleted=True)
        return recipe_out(conn, row, user["id"], access)

    @app.put("/recipes/{recipe_id}")
    def put_recipe(recipe_id: str, body: RecipeWrite, user: Session, conn: Conn) -> dict:
        """Create (base_version omitted) or update (base_version = the version you edited)."""
        if not valid_recipe_id(recipe_id):
            raise _error(422, "Identifiant de recette invalide.")
        data = clean_recipe(body.recipe, recipe_id)
        uid = user["id"]
        with db.transaction(conn):
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            if row is None or (row["deleted"] and row["owner_id"] == uid):
                if body.base_version is not None and row is None:
                    raise _error(404, "Recette introuvable (supprimée ?).")
                if row is None and conn.execute("SELECT COUNT(*) FROM recipes WHERE owner_id = ? AND deleted = 0",
                                                (uid,)).fetchone()[0] >= MAX_RECIPES_PER_USER:
                    raise _error(409, "Trop de recettes dans ce livre.")
                version = (row["version"] + 1) if row else 1
                if row is not None and body.variant_of is None:
                    # a deleted recipe of mine coming back (undo): it keeps its credit
                    credit = (row["author_id"], row["author_name"], row["variant_of"], row["variant_of_nom"])
                else:
                    credit = credit_for_new(conn, user, body.variant_of)
                insert_recipe(conn, recipe_id, uid, data, version, credit)
                access: Access = "owner"
            else:
                access = access_to(conn, uid, row)
                if access is None or row["deleted"]:
                    raise _error(404, "Recette introuvable.")
                if access == "read":
                    raise _error(403, "Vous ne pouvez pas modifier cette recette.")
                if body.base_version is None:
                    raise _error(409, "Cette recette existe déjà.")
                if body.base_version != row["version"]:
                    # someone else saved in between: the client shows both and lets the user choose
                    raise HTTPException(409, {"message": "La recette a été modifiée entre-temps.",
                                              "current": recipe_out(conn, row, uid, access)})
                conn.execute("UPDATE recipes SET data = ?, version = version + 1, seq = ?, updated_at = ? "
                             "WHERE id = ?", (json.dumps(data, ensure_ascii=False), db.next_seq(conn), db.now(),
                                              recipe_id))
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            return recipe_out(conn, row, uid, access)

    @app.post("/recipes/{recipe_id}/variant", status_code=201)
    def make_variant(recipe_id: str, user: Session, conn: Conn) -> dict:
        """Copy a recipe into my book as "<nom> (variante)", photos included. The copy keeps the
        original author's credit. Refused when the share does not allow variants."""
        uid = user["id"]
        new_id = secrets.token_hex(16)
        with db.transaction(conn):
            source, _ = load_recipe(conn, uid, recipe_id)
            if conn.execute("SELECT COUNT(*) FROM recipes WHERE owner_id = ? AND deleted = 0",
                            (uid,)).fetchone()[0] >= MAX_RECIPES_PER_USER:
                raise _error(409, "Trop de recettes dans ce livre.")
            data = json.loads(source["data"])
            data.update(id=new_id, nom=f"{data.get('nom', '')} (variante)"[:200],
                        cree_le=datetime.now().isoformat(timespec="seconds"), modifie_le="")
            insert_recipe(conn, new_id, uid, clean_recipe(data, new_id), 1, credit_for_new(conn, user, recipe_id))
            pics = conn.execute("SELECT * FROM images WHERE recipe_id = ? ORDER BY position, created_at",
                                (recipe_id,)).fetchall()
            folder = settings.images_dir / new_id
            for n, pic in enumerate(pics):
                src = settings.images_dir / recipe_id / pic["filename"]
                if not src.is_file():
                    continue
                image_id = secrets.token_hex(16)
                filename = f"{image_id}{Path(pic['filename']).suffix}"
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, folder / filename)
                conn.execute("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (image_id, new_id, filename, pic["content_type"], pic["size"], n, db.now()))
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (new_id,)).fetchone()
            return recipe_out(conn, row, uid, "owner")

    @app.delete("/recipes/{recipe_id}", status_code=204)
    def delete_recipe(recipe_id: str, user: Session, conn: Conn) -> Response:
        with db.transaction(conn):
            row, _ = load_recipe(conn, user["id"], recipe_id, need=("owner",), allow_deleted=True)
            if row["deleted"]:
                return Response(status_code=204)
            files = conn.execute("SELECT filename FROM images WHERE recipe_id = ?", (recipe_id,)).fetchall()
            for table in ("images", "favorites", "recipe_shares", "hidden_recipes"):
                conn.execute(f"DELETE FROM {table} WHERE recipe_id = ?", (recipe_id,))
            conn.execute("UPDATE recipes SET data = '{}', deleted = 1, version = version + 1, seq = ?, updated_at = ? "
                         "WHERE id = ?", (db.next_seq(conn), db.now(), recipe_id))
        folder = settings.images_dir / recipe_id
        for f in files:
            (folder / f["filename"]).unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        return Response(status_code=204)

    @app.put("/recipes/{recipe_id}/favorite", status_code=204)
    def add_favorite(recipe_id: str, user: Session, conn: Conn) -> Response:
        load_recipe(conn, user["id"], recipe_id)
        conn.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (user["id"], recipe_id))
        return Response(status_code=204)

    @app.delete("/recipes/{recipe_id}/favorite", status_code=204)
    def remove_favorite(recipe_id: str, user: Session, conn: Conn) -> Response:
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?", (user["id"], recipe_id))
        return Response(status_code=204)

    # -------------------------------------------------------------- images
    @app.post("/recipes/{recipe_id}/images", status_code=201)
    def upload_image(recipe_id: str, user: Session, conn: Conn, file: UploadFile = File(...)) -> dict:
        load_recipe(conn, user["id"], recipe_id, need=("owner", "edit"))
        limit = settings.max_image_mb * 1024 * 1024
        raw = file.file.read(limit + 1)
        if len(raw) > limit:
            raise _error(413, f"Photo trop lourde (maximum {settings.max_image_mb} Mo).")
        try:
            data, ext, content_type = images.process(raw)
        except images.BadImage as exc:
            raise _error(415, str(exc)) from exc
        image_id = secrets.token_hex(16)
        folder = settings.images_dir / recipe_id
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{image_id}.{ext}"
        with db.transaction(conn):
            load_recipe(conn, user["id"], recipe_id, need=("owner", "edit"))  # deleted / unshared meanwhile?
            count = conn.execute("SELECT COUNT(*) FROM images WHERE recipe_id = ?", (recipe_id,)).fetchone()[0]
            if count >= settings.max_images_per_recipe:
                raise _error(409, f"Maximum {settings.max_images_per_recipe} photos par recette.")
            tmp = folder / f".{filename}.tmp"
            tmp.write_bytes(data)
            tmp.replace(folder / filename)
            conn.execute("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (image_id, recipe_id, filename, content_type, len(data), count, db.now()))
            bump(conn, "id = ?", (recipe_id,))
        return {"id": image_id, "content_type": content_type, "size": len(data)}

    def _image_row(conn: sqlite3.Connection, recipe_id: str, image_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM images WHERE id = ? AND recipe_id = ?", (image_id, recipe_id)).fetchone()
        if row is None:
            raise _error(404, "Photo introuvable.")
        return row

    @app.get("/recipes/{recipe_id}/images/{image_id}")
    def get_image(recipe_id: str, image_id: str, user: Session, conn: Conn) -> FileResponse:
        load_recipe(conn, user["id"], recipe_id)
        row = _image_row(conn, recipe_id, image_id)
        path: Path = settings.images_dir / recipe_id / row["filename"]
        if not path.is_file():
            raise _error(404, "Photo introuvable.")
        return FileResponse(path, media_type=row["content_type"],
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.delete("/recipes/{recipe_id}/images/{image_id}", status_code=204)
    def delete_image(recipe_id: str, image_id: str, user: Session, conn: Conn) -> Response:
        with db.transaction(conn):
            load_recipe(conn, user["id"], recipe_id, need=("owner", "edit"))
            row = _image_row(conn, recipe_id, image_id)
            conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
            bump(conn, "id = ?", (recipe_id,))
        (settings.images_dir / recipe_id / row["filename"]).unlink(missing_ok=True)
        return Response(status_code=204)

    # ------------------------------------------------------- backup export / import
    @app.get("/export")
    def export_backup(user: Session, conn: Conn) -> FileResponse:
        """My own recipes and their photos as one zip (layout in portable.py), built in a temp file."""
        uid = user["id"]
        favorites = {r[0] for r in conn.execute("SELECT recipe_id FROM favorites WHERE user_id = ?", (uid,))}
        rows = conn.execute("SELECT id, data FROM recipes WHERE owner_id = ? AND deleted = 0 ORDER BY seq",
                            (uid,)).fetchall()
        tmp = tempfile.NamedTemporaryFile(prefix=".export-", suffix=".zip", dir=settings.data_dir, delete=False)
        path = Path(tmp.name)
        try:
            recipes = []
            with tmp, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
                for row in rows:
                    data = json.loads(row["data"])
                    names = []
                    pics = conn.execute("SELECT filename FROM images WHERE recipe_id = ? ORDER BY position, created_at",
                                        (row["id"],)).fetchall()
                    for pic in pics:
                        source = settings.images_dir / row["id"] / pic["filename"]
                        if source.is_file():
                            archive.write(source, f"images/{row['id']}/{pic['filename']}", zipfile.ZIP_STORED)
                            names.append(pic["filename"])
                    recipes.append({**data, "id": row["id"], "favori": row["id"] in favorites, "images": names})
                archive.writestr("recettes.json", json.dumps(recipes, ensure_ascii=False, indent=1))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return FileResponse(path, media_type="application/zip",
                            filename=f"mamarmiton-sauvegarde-{datetime.now():%Y-%m-%d}.zip",
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.post("/import/backup")
    def import_backup(user: Session, conn: Conn, file: UploadFile = File(...)) -> dict:
        """Add the recipes of a backup (our zip, a desktop recette.json, or a zipped desktop folder) to my book.

        A recipe I already own is skipped; one whose id belongs to someone else gets a new id.
        All recipes go in one transaction; photos go through the same checks as uploads."""
        source = file.file
        archive = None
        try:
            if zipfile.is_zipfile(source):
                archive = portable.BackupZip(source, max_file=settings.max_image_mb * 1024 * 1024)
                raw, prefix = archive.locate()
            else:
                source.seek(0)
                raw, prefix = source.read(portable.MAX_JSON_BYTES + 1), ""
                if len(raw) > portable.MAX_JSON_BYTES:
                    raise portable.BadBackup("Ce fichier est trop gros pour être une liste de recettes.")
            items = portable.decode_recipes(raw)
            del raw
            return _import_items(conn, user, items, archive, prefix)
        except portable.BadBackup as exc:
            raise _error(422, str(exc)) from exc
        finally:
            if archive:
                archive.close()

    def _import_items(conn: sqlite3.Connection, user: sqlite3.Row, items: list, archive, prefix: str) -> dict:
        uid = user["id"]
        added = skipped = invalid = photos = 0
        written: list[Path] = []
        try:
            with db.transaction(conn):
                count = conn.execute("SELECT COUNT(*) FROM recipes WHERE owner_id = ? AND deleted = 0",
                                     (uid,)).fetchone()[0]
                seen: set[str] = set()
                for item in items:
                    try:
                        parsed = Recipe.from_dict(item)
                        old_id = parsed.id
                        new_id = old_id if valid_recipe_id(old_id) else secrets.token_hex(16)
                        data = clean_recipe(item, new_id)
                    except (HTTPException, ValueError, TypeError, OverflowError):
                        invalid += 1
                        continue
                    row = conn.execute("SELECT owner_id, deleted, version, author_id, author_name, variant_of, "
                                       "variant_of_nom FROM recipes WHERE id = ?", (new_id,)).fetchone()
                    if new_id in seen or (row and row["owner_id"] == uid and not row["deleted"]):
                        skipped += 1  # already there (or twice in the file): never duplicate
                        continue
                    if row and row["owner_id"] == uid:  # one I deleted: it comes back, keeping its credit
                        version = row["version"] + 1
                        credit = (row["author_id"], row["author_name"], row["variant_of"], row["variant_of_nom"])
                    else:
                        if row:  # someone else's id: this copy is a new recipe
                            new_id = secrets.token_hex(16)
                            data["id"] = new_id
                        version, credit = 1, credit_for_new(conn, user, None)
                    seen.add(new_id)
                    count += 1
                    if count > MAX_RECIPES_PER_USER:
                        raise _error(409, "Trop de recettes dans ce livre.")
                    insert_recipe(conn, new_id, uid, data, version, credit)
                    added += 1
                    if parsed.favori:
                        conn.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (uid, new_id))
                    if archive is None or not (isinstance(item, dict) and isinstance(item.get("id"), str)):
                        continue  # photos are found under the id the file gave the recipe
                    attached = 0
                    for name in parsed.images[:settings.max_images_per_recipe]:
                        raw = archive.photo(prefix, old_id, name)
                        if raw is None:
                            continue
                        try:
                            pic, ext, content_type = images.process(raw)
                        except images.BadImage:
                            continue
                        image_id = secrets.token_hex(16)
                        filename = f"{image_id}.{ext}"
                        folder = settings.images_dir / new_id
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / filename).write_bytes(pic)
                        written.append(folder / filename)
                        conn.execute("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?)",
                                     (image_id, new_id, filename, content_type, len(pic), attached, db.now()))
                        attached += 1
                    photos += attached
        except BaseException:
            for path in written:  # the transaction rolled back: its files must not stay behind
                path.unlink(missing_ok=True)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            raise
        return {"added": added, "skipped": skipped, "invalid": invalid, "photos": photos}

    @app.put("/recipes/{recipe_id}/hidden", status_code=204)
    def hide_recipe(recipe_id: str, user: Session, conn: Conn) -> Response:
        """Take a recipe someone shared with me off my list (theirs stays as it is)."""
        with db.transaction(conn):
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            access = access_to(conn, user["id"], row, include_hidden=True) if row else None
            if access is None:
                raise _error(404, "Recette introuvable.")
            if access == "owner":
                raise _error(422, "C'est votre recette : supprimez-la plutôt.")
            conn.execute("INSERT OR IGNORE INTO hidden_recipes VALUES (?, ?, ?)", (user["id"], recipe_id, db.now()))
        return Response(status_code=204)

    @app.delete("/recipes/{recipe_id}/hidden", status_code=204)
    def unhide_recipe(recipe_id: str, user: Session, conn: Conn) -> Response:
        """Put a recipe I took off my list back (if it is still shared with me)."""
        with db.transaction(conn):
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            if row is None or access_to(conn, user["id"], row, include_hidden=True) is None:
                raise _error(404, "Recette introuvable (plus partagée ?).")
            gone = conn.execute("DELETE FROM hidden_recipes WHERE user_id = ? AND recipe_id = ?",
                                (user["id"], recipe_id)).rowcount
            if gone:
                bump(conn, "id = ?", (recipe_id,))  # so the next sync sends it again
        return Response(status_code=204)

    # ------------------------------------------------------------- sharing
    @app.get("/shares")
    def list_shares(user: Session, conn: Conn) -> dict:
        uid = user["id"]
        given = conn.execute("SELECT u.username, u.display_name, b.can_edit, b.can_copy FROM book_shares b "
                             "JOIN users u ON u.id = b.grantee_id WHERE b.owner_id = ?", (uid,)).fetchall()
        received = conn.execute("SELECT u.username, u.display_name, b.can_edit, b.can_copy FROM book_shares b "
                                "JOIN users u ON u.id = b.owner_id WHERE b.grantee_id = ?", (uid,)).fetchall()
        single = conn.execute("SELECT s.recipe_id, u.username, u.display_name, s.can_edit, s.can_copy "
                              "FROM recipe_shares s "
                              "JOIN recipes r ON r.id = s.recipe_id JOIN users u ON u.id = s.grantee_id "
                              "WHERE r.owner_id = ?", (uid,)).fetchall()
        hidden = conn.execute(
            "SELECT r.id, r.data, u.username, u.display_name FROM hidden_recipes x "
            "JOIN recipes r ON r.id = x.recipe_id JOIN users u ON u.id = r.owner_id "
            "WHERE x.user_id = :uid AND r.deleted = 0 AND ("
            "  EXISTS (SELECT 1 FROM book_shares b WHERE b.owner_id = r.owner_id AND b.grantee_id = :uid)"
            "  OR EXISTS (SELECT 1 FROM recipe_shares s WHERE s.recipe_id = r.id AND s.grantee_id = :uid)) "
            "ORDER BY x.created_at DESC", {"uid": uid}).fetchall()
        as_dict = lambda rows: [{**dict(r), "can_edit": bool(r["can_edit"]),  # noqa: E731
                                 "can_copy": bool(r["can_edit"] or r["can_copy"])} for r in rows]
        return {"book_shared_with": as_dict(given), "books_shared_with_me": as_dict(received),
                "recipes_shared": as_dict(single),
                # shared with me but taken off my list (still shared, so they can come back)
                "hidden": [{"recipe_id": r["id"], "nom": json.loads(r["data"]).get("nom", ""),
                            "username": r["username"], "display_name": r["display_name"]} for r in hidden]}

    def _grantee(conn: sqlite3.Connection, user: sqlite3.Row, username: str) -> sqlite3.Row:
        other = user_by_name(conn, username)
        if other["id"] == user["id"]:
            raise _error(422, "Vous ne pouvez pas partager avec vous-même.")
        return other

    def save_share(conn: sqlite3.Connection, table: str, key: str, key_value, grantee_id: int,
                   body: ShareRequest) -> None:
        """Create or change a share. ``can_copy=None`` (older apps) keeps what it was (a new one allows it)."""
        can_copy = 1 if body.can_edit or body.can_copy is None else int(body.can_copy)
        keep = body.can_copy is None and not body.can_edit
        conn.execute(f"INSERT INTO {table} ({key}, grantee_id, can_edit, can_copy, created_at) VALUES (?, ?, ?, ?, ?) "
                     f"ON CONFLICT ({key}, grantee_id) DO UPDATE SET can_edit = excluded.can_edit, "
                     f"can_copy = {'can_copy' if keep else 'excluded.can_copy'}",
                     (key_value, grantee_id, int(body.can_edit), can_copy, db.now()))

    @app.put("/shares/book/{username}", status_code=204)
    def share_book(username: str, body: ShareRequest, user: Session, conn: Conn) -> Response:
        with db.transaction(conn):
            other = _grantee(conn, user, username)
            save_share(conn, "book_shares", "owner_id", user["id"], other["id"], body)
            bump(conn, "owner_id = ? AND deleted = 0", (user["id"],))
        return Response(status_code=204)

    @app.delete("/shares/book/{username}", status_code=204)
    def unshare_book(username: str, user: Session, conn: Conn) -> Response:
        other = user_by_name(conn, username)
        conn.execute("DELETE FROM book_shares WHERE owner_id = ? AND grantee_id = ?", (user["id"], other["id"]))
        return Response(status_code=204)

    @app.delete("/shares/received/{username}", status_code=204)
    def leave_book(username: str, user: Session, conn: Conn) -> Response:
        """Stop seeing someone's book (the grantee side of unshare)."""
        other = user_by_name(conn, username)
        conn.execute("DELETE FROM book_shares WHERE owner_id = ? AND grantee_id = ?", (other["id"], user["id"]))
        return Response(status_code=204)

    @app.put("/recipes/{recipe_id}/shares/{username}", status_code=204)
    def share_recipe(recipe_id: str, username: str, body: ShareRequest, user: Session, conn: Conn) -> Response:
        with db.transaction(conn):
            load_recipe(conn, user["id"], recipe_id, need=("owner",))
            other = _grantee(conn, user, username)
            save_share(conn, "recipe_shares", "recipe_id", recipe_id, other["id"], body)
            # sharing this very recipe again is a fresh offer: it comes back if they had taken it off
            conn.execute("DELETE FROM hidden_recipes WHERE user_id = ? AND recipe_id = ?", (other["id"], recipe_id))
            bump(conn, "id = ?", (recipe_id,))
        return Response(status_code=204)

    @app.delete("/recipes/{recipe_id}/shares/{username}", status_code=204)
    def unshare_recipe(recipe_id: str, username: str, user: Session, conn: Conn) -> Response:
        load_recipe(conn, user["id"], recipe_id, need=("owner",))
        other = user_by_name(conn, username)
        conn.execute("DELETE FROM recipe_shares WHERE recipe_id = ? AND grantee_id = ?", (recipe_id, other["id"]))
        return Response(status_code=204)

    @app.post("/usage")
    def post_usage(body: UsageBatch, user: Session, conn: Conn) -> dict:
        """Anonymous daily counters. The account only proves the sender is a family member; it is
        not stored. Unknown events and malformed days are skipped, never an error."""
        rows = usage.clean_batch(body.days)
        if rows:
            usage.record(conn, usage.client_kind(body.client), rows)
        return {"accepted": len(rows)}

    # ------------------------------------------------------ import automatique
    importer.register(app, settings, Session)

    @app.post("/reports", status_code=201)
    def post_report(body: ReportBody, user: Session, conn: Conn) -> dict:
        message = body.message.strip()
        if not message:
            raise _error(422, "Écrivez quelques mots pour décrire le problème.")
        category_other = " ".join(body.category_other.split()) if body.category == "other" else ""
        if body.category == "other" and not category_other:
            raise _error(422, "Dites en quelques mots de quoi il s'agit.")
        log_tail = redact(body.log_tail)
        if len(log_tail.encode("utf-8")) > MAX_REPORT_LOG:
            raise _error(413, "Le journal joint est trop gros.")
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="microseconds")
        if conn.execute("SELECT COUNT(*) FROM reports WHERE user_id = ? AND created_at > ?",
                        (user["id"], since)).fetchone()[0] >= MAX_REPORTS_PER_DAY:
            raise _error(429, "Vous avez déjà envoyé beaucoup de messages aujourd'hui. Réessayez demain.")
        cur = conn.execute(
            "INSERT INTO reports (user_id, created_at, message, category, category_other, client, app_version, os, "
            "log_tail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], db.now(), redact(message), body.category, redact(category_other), body.client,
             body.app_version, body.os, log_tail))
        return {"id": cur.lastrowid}

    return app
