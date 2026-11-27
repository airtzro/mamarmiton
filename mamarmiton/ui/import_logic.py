"""Non-Tk logic of "Importer une recette": where each import is read, and turning the answer into a draft.

Routing, local first and offline safe:

* connected, and the server can read these files -> the server (OCR + its own local AI);
* otherwise, or when the server cannot be reached -> this computer: PDFs and documents always,
  photos only when Tesseract is installed here; else a plain-French refusal.

``run_import`` runs in a worker thread; nothing here imports tkinter.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mamarmiton.importers.pipeline import ImportResult, import_files, import_text, kind_of
from mamarmiton.models import Recipe
from mamarmiton.remote.client import ApiError, AuthError, OfflineError

log = logging.getLogger(__name__)

SERVER, LOCAL, REFUSE = "server", "local", "refuse"

PHOTO_NEEDS_ACCOUNT = "Pour lire une photo, connectez-vous à votre compte (le serveur sait lire les photos)."
PHOTO_SERVER_UNAVAILABLE = ("Le serveur ne peut pas lire les photos pour le moment. Réessayez plus tard, "
                            "ou importez plutôt un PDF ou un texte.")
NOTHING_CHOSEN = "Choisissez un fichier, ou collez le texte de la recette."
FALLBACK_WARNING = "Le serveur n'a pas répondu : la recette a été lue sur cet ordinateur."

DEFAULT_ACCEPTS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".txt", ".docx", ".odt"})


class ImportRefused(ValueError):
    """The import cannot be done; the message is a French sentence for the user."""


@dataclass(frozen=True)
class ServerImport:
    """What ``GET /import/status`` says the server can read."""

    ocr: bool = False
    ai: bool = False
    max_files: int = 8
    max_mb: int = 30
    accepts: frozenset[str] = DEFAULT_ACCEPTS


def normalize_status(raw: Any) -> ServerImport | None:
    """None when the server has no import (old server, 404, unexpected answer)."""
    if not isinstance(raw, dict) or raw.get("available") is not True:
        return None

    def pos_int(value: Any, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return number if number > 0 else default

    accepts = {str(e).strip().lower() for e in raw.get("accepts") or [] if isinstance(e, str)}
    accepts = {e if e.startswith(".") else "." + e for e in accepts if e}
    return ServerImport(ocr=raw.get("ocr") is True, ai=raw.get("ai") is True,
                        max_files=pos_int(raw.get("max_files"), 8), max_mb=pos_int(raw.get("max_mb"), 30),
                        accepts=frozenset(accepts) or DEFAULT_ACCEPTS)


@dataclass(frozen=True)
class Plan:
    route: str  # SERVER, LOCAL or REFUSE
    message: str = ""  # why, for REFUSE


def _size(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def server_can_read(server: ServerImport, paths: list[Path]) -> bool:
    """The server accepts every file; photos and PDFs need its OCR."""
    for path in paths:
        if Path(path).suffix.lower() not in server.accepts:
            return False
        if kind_of(path) in ("image", "pdf") and not server.ocr:
            return False
    return True


def plan_import(paths: list[Path], text: str | None, *, connected: bool, server: ServerImport | None,
                local_ocr: bool) -> Plan:
    """Where to read this import. ``server`` is None when it is unknown, unreachable or has no import."""
    paths = [Path(p) for p in paths]
    if not paths and not (text or "").strip():
        return Plan(REFUSE, NOTHING_CHOSEN)
    for path in paths:
        if kind_of(path) is None:
            return Plan(REFUSE, f"« {path.name} » n'est pas un format accepté : choisissez un PDF, une photo, "
                                "un texte ou un document Word ou LibreOffice.")
    if connected and server is not None and server_can_read(server, paths):
        if len(paths) > server.max_files:
            return Plan(REFUSE, f"{server.max_files} fichiers au maximum : choisissez-en moins.")
        if sum(_size(p) for p in paths) > server.max_mb * 1024 * 1024:
            return Plan(REFUSE, f"Ces fichiers sont trop lourds : {server.max_mb} Mo au maximum en tout.")
        return Plan(SERVER)
    if any(kind_of(p) == "image" for p in paths) and not local_ocr:
        return Plan(REFUSE, PHOTO_SERVER_UNAVAILABLE if connected else PHOTO_NEEDS_ACCOUNT)
    return Plan(LOCAL)


# ------------------------------------------------------------- server answer
def _dish_photo(paths: list[Path], index: Any, warnings: list[str]) -> list[Path]:
    """A copy of the photo the server picked as the dish photo, in a temporary folder.

    A copy, because the editor's clean-up deletes the folder of the imported photos.
    """
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(paths):
        return []
    source = Path(paths[index])
    if kind_of(source) != "image" or not source.is_file():
        return []
    folder = Path(tempfile.mkdtemp(prefix="mamarmiton_"))
    try:
        if source.suffix.lower() in (".heic", ".heif"):  # the app shows JPEG/PNG; convert when possible
            from mamarmiton.importers.pdf_text import open_photo

            target = folder / (source.stem + ".jpg")
            open_photo(source).convert("RGB").save(target, quality=90)
        else:
            target = folder / source.name
            shutil.copy2(source, target)
        return [target]
    except Exception:  # noqa: BLE001 - the recipe matters more than its photo
        log.warning("Could not keep the dish photo %s", source, exc_info=True)
        shutil.rmtree(folder, ignore_errors=True)
        warnings.append("La photo du plat n'a pas pu être ajoutée : ajoutez-la ensuite depuis la page de la recette.")
        return []


def result_from_server(data: Any, paths: list[Path]) -> ImportResult:
    """``POST /import/recipe`` answer -> the same ImportResult the local PDF import gives."""
    data = data if isinstance(data, dict) else {}
    raw = dict(data.get("recipe") or {}) if isinstance(data.get("recipe"), dict) else {}
    for key in ("id", "images", "favori", "cree_le", "modifie_le"):  # a new recipe of this book
        raw.pop(key, None)
    recipe = Recipe.from_dict(raw)
    if not recipe.source and paths:
        recipe.source = Path(paths[0]).name
    warnings = [str(w) for w in data.get("warnings") or [] if isinstance(w, str) and w.strip()]
    images = _dish_photo([Path(p) for p in paths], data.get("dish_photo"), warnings)
    method = "IA" if data.get("method") == "ia" else "règles"
    return ImportResult(recipe=recipe, images=images, method=method, warnings=warnings,
                        origin="vos photos" if images else "")


# -------------------------------------------------------------------- runner
_local_lock = threading.Lock()  # one local reading at a time (the AI model is big), even after a cancel


def read_locally(paths: list[Path], text: str | None, use_ai: bool, progress: Callable[[str], None]) -> ImportResult:
    if not _local_lock.acquire(blocking=False):
        progress("La lecture précédente se termine…")
        _local_lock.acquire()
    try:
        if paths:
            return import_files(paths, use_llm=use_ai, progress=progress, text=text or "")
        return import_text(text or "", use_llm=use_ai, progress=progress)
    finally:
        _local_lock.release()


@dataclass
class ImportJob:
    """Everything a worker thread needs. ``client`` is the ApiClient when connected, else None."""

    paths: list[Path]
    text: str | None = None
    client: Any = None
    use_ai: bool = True
    local_ocr: bool = False
    network_errors: list[BaseException] = field(default_factory=list)  # for the sidebar status


def run_import(job: ImportJob, progress: Callable[[str], None] = lambda _m: None,
               read_local: Callable[..., ImportResult] = read_locally) -> ImportResult:
    """Read the recipe where the plan says. Raises ``ImportRefused`` (shown as is) or another error."""
    paths = [Path(p) for p in job.paths]
    server = None
    if job.client is not None:
        progress("Connexion au serveur…")
        try:
            server = normalize_status(job.client.import_status())
        except (OfflineError, AuthError) as exc:
            job.network_errors.append(exc)
        except ApiError:
            log.warning("Import status refused", exc_info=True)
    plan = plan_import(paths, job.text, connected=job.client is not None, server=server, local_ocr=job.local_ocr)
    if plan.route == REFUSE:
        raise ImportRefused(plan.message)
    if plan.route == SERVER:
        progress("Lecture de la recette par le serveur…")
        try:
            return result_from_server(job.client.import_recipe(paths, job.text), paths)
        except (OfflineError, AuthError) as exc:  # unreachable, busy, session over: read it here if possible
            job.network_errors.append(exc)
            plan = plan_import(paths, job.text, connected=True, server=None, local_ocr=job.local_ocr)
            if plan.route == REFUSE:
                busy = isinstance(exc, OfflineError) and exc.status == 503 and exc.message
                raise ImportRefused(exc.message if busy else plan.message) from exc
            result = read_local(paths, job.text, job.use_ai, progress)
            result.warnings.insert(0, FALLBACK_WARNING)
            return result
        except ApiError as exc:  # the server read it and said no (too big, no recipe found...)
            raise ImportRefused(exc.message) from exc
    return read_local(paths, job.text, job.use_ai, progress)
