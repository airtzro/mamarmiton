"""Import automatique: uploaded files or pasted text -> a prefilled recipe to check.

Everything runs on this server; nothing is sent anywhere:

* .txt, .docx, .odt and PDFs with a text layer are read directly;
* photos and scanned PDFs go through Tesseract OCR (French + English);
* the text is structured by the desktop app's rule-based parser and, when its
  model file is present, by the desktop app's small local model, whose numbers
  are cross-checked against the text the same way the desktop does.

Uploads stay in memory (Starlette spools very large ones to /tmp, a tmpfs in the
container). Nothing about an import is stored; the log holds sizes and timings only.

The OCR engine and the local model are attributes of :class:`Importer` (``app.state.importer``)
so tests can replace them with fakes.
"""

# no "from __future__ import annotations": FastAPI must see the real Session type of the routes
import base64
import concurrent.futures
import importlib.util
import io
import logging
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePath
from typing import Annotated, Callable, Iterator, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from mamarmiton.importers import documents, heuristic, llm, pdf_text, pipeline, web
from mamarmiton.models import Recipe
from mamarmiton_server import images, safeweb
from mamarmiton_server.config import Settings

log = logging.getLogger(__name__)

MAX_FILES = 8
MAX_TEXT_CHARS = 100_000
ACCEPTS = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".txt", ".docx", ".odt"]
EXTENSION_KIND = {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
                  ".heic": "image", ".gif": "image", ".txt": "text", ".docx": "docx", ".odt": "odt"}
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}

DISH_PHOTO_MAX_CHARS = 25  # an image whose OCR finds fewer letters/digits shows the dish, not a recipe
MIN_RECIPE_CHARS = 25  # less readable text than this: no recipe found
LOW_CONFIDENCE = 60  # mean Tesseract word confidence (0-100) under which we warn
OCR_PAGE_TIMEOUT_S = 90
OCR_MIN_SIDE = 1600  # small pictures are enlarged up to this (Tesseract likes ~300 dpi)
OCR_MAX_SIDE = 3500  # huge ones are reduced (time grows with pixels)
PDF_RENDER_DPI = 300
QUEUE_WAIT_S = 60  # how long an import waits for a free slot before "server busy"
MODEL_WAIT_S = 30  # how long it waits for the local model when another import uses it
MODEL_MIN_TIME_S = 10  # do not start the model with less time left than this

URL_TIMEOUT_S = 45  # the page and its photo together
MAX_URL_PHOTO_BYTES = 4 * 1024 * 1024  # of the re-encoded photo sent back with the draft

PASTED_NAME = "Texte collé"

NO_OCR = "La lecture des photos et des PDF scannés n'est pas activée sur le serveur."
NO_RECIPE = "Je n'ai pas trouvé de recette dans ce fichier."
ONLY_DISH = ("Je n'ai pas trouvé de recette : la photo semble montrer le plat. "
             "Ajoutez une photo de la recette ou collez son texte.")
W_RULES = "Lecture simple : vérifiez bien la recette."
W_OCR = "Lecture automatique : vérifiez les quantités avant d'enregistrer."
W_LOW_CONFIDENCE = ("Certains passages étaient difficiles à lire (photo floue, écriture manuscrite ?) : "
                    "vérifiez bien chaque ligne.")
W_SLOW = "La lecture a pris trop de temps : certaines pages n'ont pas été lues."
W_PAGES = "Seules les {n} premières pages du PDF ont été lues."
W_PAGE_FAILED = "Une page n'a pas pu être lue."
W_MODEL_FAILED = "L'analyse fine n'a pas abouti : lecture simple utilisée."
W_MODEL_BUSY = "L'analyse fine est occupée par une autre recette : lecture simple utilisée."
W_LONG = "Texte très long : seul le début a été analysé finement, vérifiez la fin de la recette."
W_UNSTRUCTURED = ("Je n'ai pas reconnu les ingrédients et les étapes : tout le texte lu est dans les notes, "
                  "à recopier au bon endroit.")
W_NO_OCR_PARTIAL = "Ce PDF semble scanné et la lecture des scans n'est pas activée : seul son texte a été repris."


class ImportProblem(Exception):
    """An answer for the client: HTTP status and a French sentence."""

    def __init__(self, status: int, message: str, refund: bool = False):
        super().__init__(message)
        self.status = status
        self.message = message
        self.refund = refund  # nothing heavy ran: do not count it against the daily limit


# ------------------------------------------------------------------ file types
def _zip_kind(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "mimetype" in names and zf.getinfo("mimetype").file_size < 200:
                if zf.read("mimetype").strip() == b"application/vnd.oasis.opendocument.text":
                    return "odt"
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    return None


def _looks_like_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    text = documents.plain_text(data)
    controls = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r\f")
    return controls <= max(2, len(text) // 100)


def sniff(data: bytes) -> str | None:
    """What the bytes really are: pdf, jpeg, png, gif, webp, heic, docx, odt, text, or None."""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS:
        return "heic"
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(data)
    if data and _looks_like_text(data):
        return "text"
    return None


def kind_of(fmt: str) -> str:
    return "image" if fmt in ("jpeg", "png", "gif", "webp", "heic") else fmt


@dataclass
class Upload:
    index: int  # position in the uploaded list (what ``dish_photo`` refers to)
    name: str
    data: bytes
    fmt: str  # sniffed format
    kind: str  # pdf / image / text / docx / odt


def clean_name(filename: str | None) -> str:
    name = PurePath((filename or "").replace("\\", "/")).name.strip()
    return "".join(c for c in name if c.isprintable())[:200]


def identify(index: int, filename: str | None, data: bytes) -> Upload:
    name = clean_name(filename) or f"fichier {index + 1}"
    ext = PurePath(name).suffix.lower()
    fmt = sniff(data)
    wrong = (f"Le fichier « {name} » n'est pas d'un type accepté "
             "(PDF, photo, document Word ou LibreOffice, texte).")
    if fmt is None or (ext and ext not in EXTENSION_KIND):
        raise ImportProblem(415, wrong)
    if ext and EXTENSION_KIND[ext] != kind_of(fmt):  # a renamed file: trust neither the name nor the bytes
        raise ImportProblem(415, wrong)
    if fmt == "heic" and not images.HEIF_SUPPORTED:
        raise ImportProblem(415, f"Les photos HEIC (iPhone) ne sont pas lisibles ici : envoyez « {name} » en JPEG.")
    return Upload(index, name, data, fmt, kind_of(fmt))


# ------------------------------------------------------------------ OCR
@dataclass
class OcrText:
    text: str
    confidence: float | None = None  # mean word confidence, 0-100, when the engine gives one


class Ocr(Protocol):
    def available(self) -> bool: ...
    def __call__(self, image: Image.Image, timeout: float) -> OcrText: ...


def words_to_text(data: dict) -> OcrText:
    """Lines of text and the mean word confidence out of ``pytesseract.image_to_data`` (dict output)."""
    lines: dict[tuple, list[str]] = {}
    confidences: list[float] = []
    for i, word in enumerate(data.get("text", [])):
        word = str(word or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (KeyError, IndexError, TypeError, ValueError):
            conf = -1.0
        if conf >= 0:
            confidences.append(conf)
        key = tuple(int(data[k][i]) for k in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(word)
    out: list[str] = []
    previous = None
    for key in sorted(lines):
        if previous is not None and key[:3] != previous:
            out.append("")  # new paragraph
        out.append(" ".join(lines[key]))
        previous = key[:3]
    confidence = sum(confidences) / len(confidences) if confidences else None
    return OcrText("\n".join(out).strip(), confidence)


class TesseractOcr:
    """Tesseract through pytesseract (one subprocess per page, killed after ``timeout``)."""

    def __init__(self) -> None:
        self._langs: str | None = None

    def available(self) -> bool:
        return shutil.which("tesseract") is not None and importlib.util.find_spec("pytesseract") is not None

    def languages(self) -> str:
        if self._langs is None:
            import pytesseract

            try:
                have = set(pytesseract.get_languages())
            except Exception:
                have = set()
            wanted = [lang for lang in ("fra", "eng") if lang in have]
            if "fra" not in have:
                log.warning("Tesseract has no French data (install tesseract-ocr-fra)")
            self._langs = "+".join(wanted) or "eng"
        return self._langs

    def __call__(self, image: Image.Image, timeout: float) -> OcrText:
        import pytesseract

        deadline = time.monotonic() + timeout
        result = self._read(image, timeout)
        if _good_enough(result):
            return result
        # a card photographed sideways or upside down (and no EXIF to say so): ask Tesseract which
        # way is up, then read again
        remaining = deadline - time.monotonic()
        if remaining < 3:
            return result
        try:
            osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT,
                                           timeout=max(1, int(min(remaining, 20))))
            rotate = int(osd.get("rotate", 0)) % 360
        except Exception:  # "too few characters": a dish photo, or nothing to read
            return result
        remaining = deadline - time.monotonic()
        if rotate == 0 or remaining < 3:
            return result
        turned = self._read(image.rotate(-rotate, expand=True, fillcolor=255), remaining)
        return turned if _score(turned) > _score(result) else result

    def _read(self, image: Image.Image, timeout: float) -> OcrText:
        import pytesseract

        data = pytesseract.image_to_data(image, lang=self.languages(), output_type=pytesseract.Output.DICT,
                                         timeout=max(1, int(timeout)))
        return words_to_text(data)


def _score(result: OcrText) -> float:
    return meaningful(result.text) * (result.confidence or 0)


def _good_enough(result: OcrText) -> bool:
    return result.confidence is not None and result.confidence >= LOW_CONFIDENCE and meaningful(result.text) >= 40


def open_image(upload: Upload) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(upload.data))
        if img.width * img.height > Image.MAX_IMAGE_PIXELS:
            raise ImportProblem(413, f"La photo « {upload.name} » est trop grande.")
        img.seek(0)  # first frame of an animated GIF
        img.load()
        return img
    except ImportProblem:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise ImportProblem(422, f"La photo « {upload.name} » est abîmée ou illisible.") from exc


def prepare_for_ocr(img: Image.Image) -> Image.Image:
    """Upright, grey, a sensible size and stretched contrast: what Tesseract reads best."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        img = Image.alpha_composite(white, rgba)  # transparent screenshots: black text on white
    img = img.convert("L")
    longest = max(img.size)
    if longest < OCR_MIN_SIDE:
        factor = min(3.0, OCR_MIN_SIDE / max(longest, 1))
        img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))),
                         Image.Resampling.LANCZOS)
    elif longest > OCR_MAX_SIDE:
        img.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)


# pdfium is not thread-safe, and two imports may run at once
_PDFIUM_LOCK = threading.Lock()


def pdf_page_count(upload: Upload) -> int:
    import pypdfium2 as pdfium

    try:
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(upload.data)
            try:
                return len(pdf)
            finally:
                pdf.close()
    except pdfium.PdfiumError as exc:
        raise ImportProblem(422, f"Le PDF « {upload.name} » est protégé par un mot de passe ou abîmé.") from exc


def render_pdf_pages(data: bytes, max_pages: int) -> Iterator[Image.Image]:
    """The first pages of a PDF as grey images, rendered one at a time."""
    import pypdfium2 as pdfium

    for index in range(max_pages):
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(data)
            try:
                if index >= len(pdf):
                    return
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    scale = min(PDF_RENDER_DPI / 72, OCR_MAX_SIDE / max(width, height, 1))
                    bitmap = page.render(scale=scale, grayscale=True)
                    image = bitmap.to_pil().convert("L")  # a copy: the bitmap is freed below
                    bitmap.close()
                finally:
                    page.close()
            finally:
                pdf.close()
        yield image


# ------------------------------------------------------------------ local model
class Model(Protocol):
    def available(self) -> bool: ...
    def parse(self, text: str, timeout: float) -> dict | None: ...


class ModelBusy(Exception):
    pass


_PROBE = ("import sys\n"
          "from llama_cpp import Llama\n"
          "m = Llama(model_path=sys.argv[1], n_ctx=256, n_gpu_layers=0, verbose=False)\n"
          "m.create_completion('Bonjour', max_tokens=1)\n")


class LocalModel:
    """The desktop app's small model (llama-cpp-python), loaded once, used by one import at a time.

    It runs in its own thread so an import can give up waiting for it (the answer is then dropped).
    """

    PROBE_TIMEOUT_S = 300

    def __init__(self, model_file: Path) -> None:
        self.model_file = model_file
        self._model = None
        self._broken = False
        self._lock = threading.Lock()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="import-model")

    def available(self) -> bool:
        return (not self._broken and self.model_file.is_file()
                and importlib.util.find_spec("llama_cpp") is not None)

    def parse(self, text: str, timeout: float) -> dict | None:
        """The model's recipe JSON; raises ModelBusy, concurrent.futures.TimeoutError or the model's error."""
        if not self._lock.acquire(timeout=max(0.0, min(timeout, MODEL_WAIT_S))):
            raise ModelBusy()
        try:
            future = self._pool.submit(self._run, text)
        except BaseException:
            self._lock.release()
            raise
        future.add_done_callback(lambda _f: self._lock.release())
        return future.result(timeout=timeout)

    def _run(self, text: str) -> dict | None:
        if self._model is None:
            self._check_it_runs()
            started = time.monotonic()
            self._model = llm.load_model(self.model_file)
            log.info("import model loaded in %.1fs", time.monotonic() - started)
        return llm.ask_model(self._model, text)

    def _check_it_runs(self) -> None:
        """Run the model once in a child process first: the prebuilt library dies with "illegal
        instruction" on a CPU it does not support, and that must not take the server down."""
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-c", _PROBE, str(self.model_file)],
                                    capture_output=True, timeout=self.PROBE_TIMEOUT_S)
            ok = result.returncode == 0
            detail = f"exit code {result.returncode}: {result.stderr[-500:].decode(errors='replace')}"
        except subprocess.TimeoutExpired:
            ok, detail = False, "timed out"
        if not ok:
            self._broken = True
            log.error("the import model cannot run on this server (%s); using the rule-based parser", detail)
            raise RuntimeError("model unusable")
        log.info("import model checked in %.1fs", time.monotonic() - started)


# ------------------------------------------------------------------ limits
class DailyLimits:
    """Imports per account and for the whole server, per calendar day (kept in memory)."""

    def __init__(self, per_user: int, overall: int) -> None:
        self.per_user = per_user
        self.overall = overall
        self._day = date.today()
        self._users: Counter[int] = Counter()
        self._lock = threading.Lock()

    def _roll(self) -> None:
        today = date.today()
        if today != self._day:
            self._day, self._users = today, Counter()

    def take(self, user_id: int) -> None:
        with self._lock:
            self._roll()
            if self._users[user_id] >= self.per_user:
                raise ImportProblem(429, f"Vous avez fait {self.per_user} imports aujourd'hui, "
                                         "c'est le maximum. Réessayez demain.")
            if sum(self._users.values()) >= self.overall:
                raise ImportProblem(429, "Le serveur a fait trop d'imports aujourd'hui. Réessayez demain.")
            self._users[user_id] += 1

    def give_back(self, user_id: int) -> None:
        with self._lock:
            self._roll()
            if self._users[user_id] > 0:
                self._users[user_id] -= 1


# ------------------------------------------------------------------ the import itself
def meaningful(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


@dataclass
class Reading:
    """What was read out of the uploads."""

    parts: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # files that gave text, in order
    dish_photo: int | None = None
    ocr_pages: int = 0
    ocr_chars: int = 0
    ocr_weighted_conf: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def add_ocr(self, result: OcrText) -> None:
        self.ocr_pages += 1
        if result.confidence is not None and result.text:
            n = meaningful(result.text)
            self.ocr_chars += n
            self.ocr_weighted_conf += result.confidence * n

    @property
    def confidence(self) -> float | None:
        return self.ocr_weighted_conf / self.ocr_chars if self.ocr_chars else None

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


class Importer:
    def __init__(self, settings: Settings, ocr: Ocr | None = None, model: Model | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.ocr: Ocr = ocr or TesseractOcr()
        self.model: Model = model or LocalModel(settings.import_model_file)
        self.limits = DailyLimits(settings.import_daily, settings.import_daily_all)
        self.slots = threading.BoundedSemaphore(max(1, settings.import_parallel))
        self.clock = clock

    @property
    def max_bytes(self) -> int:
        return self.settings.import_max_mb * 1024 * 1024

    def status(self) -> dict:
        return {"available": True, "ocr": bool(self.ocr.available()), "ai": bool(self.model.available()),
                "max_files": MAX_FILES, "max_mb": self.settings.import_max_mb, "accepts": list(ACCEPTS), "url": True}

    # -------------------------------------------------------------- entry point
    def handle(self, user_id: int, files: list[UploadFile], text: str) -> dict:
        started = self.clock()
        files = [f for f in files if f.filename or f.size]  # an empty file field from a browser form
        if len(files) > MAX_FILES:
            raise ImportProblem(400, f"Pas plus de {MAX_FILES} fichiers à la fois.")
        if len(text) > MAX_TEXT_CHARS:
            raise ImportProblem(400, "Le texte collé est trop long (100 000 caractères au maximum).")
        if not files and not text.strip():
            raise ImportProblem(400, "Envoyez un fichier ou collez le texte de la recette.")

        uploads: list[Upload] = []
        total = 0
        for index, upload_file in enumerate(files):
            data = upload_file.file.read(self.max_bytes - total + 1)
            total += len(data)
            if total > self.max_bytes:
                raise ImportProblem(413, f"Fichiers trop lourds : {self.settings.import_max_mb} Mo au maximum en tout.")
            if not data:
                raise ImportProblem(400, f"Le fichier « {clean_name(upload_file.filename)} » est vide.")
            uploads.append(identify(index, upload_file.filename, data))
        if any(u.kind == "image" for u in uploads) and not self.ocr.available():
            raise ImportProblem(503, NO_OCR)

        self.limits.take(user_id)
        if not self.slots.acquire(timeout=QUEUE_WAIT_S):
            self.limits.give_back(user_id)
            raise ImportProblem(503, "Le serveur lit déjà d'autres recettes. Réessayez dans une minute.")
        try:
            result, timings = self._run(uploads, text, deadline=started + self.settings.import_timeout_s)
        except ImportProblem as problem:
            if problem.refund:
                self.limits.give_back(user_id)
            log.info("import user=%s files=%d sizes=%s refused %d after %.1fs", user_id, len(uploads),
                     [len(u.data) for u in uploads], problem.status, self.clock() - started)
            raise
        except Exception as exc:
            log.exception("import user=%s failed after %.1fs", user_id, self.clock() - started)
            raise ImportProblem(500, "La lecture a échoué à cause d'un problème sur le serveur.") from exc
        finally:
            self.slots.release()
        log.info("import user=%s files=%d kinds=%s sizes=%s pasted_chars=%d method=%s ocr_pages=%d "
                 "read=%.1fs model=%.1fs total=%.1fs", user_id, len(uploads), [u.fmt for u in uploads],
                 [len(u.data) for u in uploads], len(text), result["method"], timings["ocr_pages"],
                 timings["read"], timings["model"], self.clock() - started)
        return result

    # -------------------------------------------------------------- a website
    def handle_url(self, user_id: int, address: str) -> dict:
        started = self.clock()
        try:
            url = web.normalize_url(address)
            safeweb.check_url(url)
        except web.WebImportError as exc:
            raise ImportProblem(400, str(exc)) from None
        self.limits.take(user_id)
        if not self.slots.acquire(timeout=QUEUE_WAIT_S):
            self.limits.give_back(user_id)
            raise ImportProblem(503, "Le serveur lit déjà d'autres recettes. Réessayez dans une minute.")
        try:
            result = self._run_url(url, time.monotonic() + URL_TIMEOUT_S)
        except web.WebImportError as exc:
            if isinstance(exc, safeweb.Refused):
                self.limits.give_back(user_id)  # nothing was contacted
            log.info("import user=%s url refused after %.1fs: %s", user_id, self.clock() - started, exc)
            raise ImportProblem(422, str(exc)) from None
        except Exception as exc:
            log.exception("import user=%s url failed after %.1fs", user_id, self.clock() - started)
            raise ImportProblem(500, "La lecture a échoué à cause d'un problème sur le serveur.") from exc
        finally:
            self.slots.release()
        log.info("import user=%s url host=%s photo=%s total=%.1fs", user_id, urlsplit(url).hostname,
                 bool(result["photo"]), self.clock() - started)
        return result

    @staticmethod
    def _run_url(url: str, deadline: float) -> dict:
        recipe, photo_urls = web._parse(safeweb.fetch_page(url, deadline), url)
        warnings: list[str] = []
        photo = None
        for photo_url in photo_urls[:3]:
            try:
                data, _, content_type = images.process(safeweb.fetch_image(photo_url, deadline))
            except (web.WebImportError, images.BadImage):
                continue
            if len(data) <= MAX_URL_PHOTO_BYTES:
                photo = {"content_type": content_type, "data": base64.b64encode(data).decode("ascii")}
                break
        if photo_urls and not photo:
            warnings.append("La photo de la recette n'a pas pu être récupérée.")
        return {"recipe": finish(recipe), "dish_photo": None, "method": "site", "warnings": warnings,
                "photo": photo}

    # -------------------------------------------------------------- reading
    def _run(self, uploads: list[Upload], pasted: str, deadline: float) -> tuple[dict, dict]:
        t0 = self.clock()
        reading = Reading()
        for upload in uploads:
            self._read(upload, reading, deadline)
        if pasted.strip():
            reading.parts.append(pasted.strip())
            reading.names.append(PASTED_NAME)
        source = "\n\n".join(p for p in reading.parts if p.strip())
        t1 = self.clock()
        if meaningful(source) < MIN_RECIPE_CHARS:
            raise ImportProblem(422, ONLY_DISH if reading.dish_photo is not None and not source.strip() else NO_RECIPE)

        recipe, method, warnings = self._structure(source, deadline)
        t2 = self.clock()
        warnings = reading.warnings + [w for w in warnings if w not in reading.warnings]
        if reading.ocr_pages:
            warnings.insert(0, W_OCR)
            confidence = reading.confidence
            if confidence is not None and confidence < LOW_CONFIDENCE:
                warnings.insert(1, W_LOW_CONFIDENCE)
        if method == "regles" and not reading.ocr_pages and not pipeline.rules_are_clear(recipe):
            warnings.append(W_RULES)  # one "vérifiez" is enough; a clearly laid-out text was copied as written

        if reading.names:
            recipe.source = reading.names[0]
        elif uploads:
            recipe.source = uploads[0].name
        data = finish(recipe)
        result = {"recipe": data, "dish_photo": reading.dish_photo, "method": method,
                  "warnings": list(dict.fromkeys(warnings))}
        return result, {"read": t1 - t0, "model": t2 - t1, "ocr_pages": reading.ocr_pages}

    def _read(self, upload: Upload, reading: Reading, deadline: float) -> None:
        if upload.kind in ("text", "docx", "odt"):
            reader = {"text": documents.plain_text, "docx": documents.docx_text, "odt": documents.odt_text}
            try:
                text = reader[upload.kind](upload.data)
            except documents.DocumentError as exc:
                raise ImportProblem(422, f"Le document « {upload.name} » est abîmé ou illisible.") from exc
            self._add(reading, upload, text)
        elif upload.kind == "image":
            image = prepare_for_ocr(open_image(upload))
            result = self._ocr(image, reading, deadline)
            if result is None:
                return
            if meaningful(result.text) < DISH_PHOTO_MAX_CHARS:
                if reading.dish_photo is None:
                    reading.dish_photo = upload.index
                return
            reading.add_ocr(result)
            self._add(reading, upload, result.text)
        elif upload.kind == "pdf":
            self._read_pdf(upload, reading, deadline)

    @staticmethod
    def _add(reading: Reading, upload: Upload, text: str) -> None:
        if text.strip():
            reading.parts.append(text.strip())
            reading.names.append(upload.name)

    def _ocr(self, image: Image.Image, reading: Reading, deadline: float) -> OcrText | None:
        remaining = deadline - self.clock()
        if remaining < 3:
            reading.warn(W_SLOW)
            return None
        try:
            return self.ocr(image, timeout=min(OCR_PAGE_TIMEOUT_S, remaining))
        except RuntimeError as exc:  # pytesseract: "Tesseract process timeout"
            if "timeout" in str(exc).lower():
                reading.warn(W_SLOW)
                return None
            log.exception("OCR failed")
        except Exception:
            log.exception("OCR failed")
        reading.warn(W_PAGE_FAILED)
        return None

    def _read_pdf(self, upload: Upload, reading: Reading, deadline: float) -> None:
        max_pages = max(1, self.settings.import_max_pages)
        pages = pdf_page_count(upload)
        if pages > max_pages:
            reading.warn(W_PAGES.format(n=max_pages))
        try:
            text = pdf_text.extract_text_layer(io.BytesIO(upload.data), max_pages=max_pages)
        except Exception as exc:  # pdfminer raises many kinds of errors on broken files
            log.warning("PDF text layer unreadable: %s", type(exc).__name__)
            text = ""
        if len(text) >= pdf_text.MIN_TEXT_CHARS:
            self._add(reading, upload, text)
            return
        # little or no text layer: probably a scan
        if not self.ocr.available():
            if meaningful(text) >= MIN_RECIPE_CHARS:
                reading.warn(W_NO_OCR_PARTIAL)
                self._add(reading, upload, text)
                return
            raise ImportProblem(503, NO_OCR, refund=True)
        page_texts = []
        page_results = []
        for image in render_pdf_pages(upload.data, min(pages, max_pages)):
            result = self._ocr(image, reading, deadline)
            if result is None:
                if W_SLOW in reading.warnings:
                    break
                continue
            page_results.append(result)
            page_texts.append(result.text)
        ocr_text = "\n\n".join(t for t in page_texts if t.strip())
        if len(ocr_text) > len(text):
            for result in page_results:
                reading.add_ocr(result)
            text = ocr_text
        self._add(reading, upload, text)

    # -------------------------------------------------------------- structuring
    def _structure(self, source: str, deadline: float) -> tuple[Recipe, str, list[str]]:
        warnings: list[str] = []
        rules = heuristic.parse_recipe_text(source)
        recipe, method = rules, "regles"
        if self.model.available() and not pipeline.rules_are_clear(rules):
            remaining = deadline - self.clock()
            if remaining < MODEL_MIN_TIME_S:
                warnings.append(W_MODEL_FAILED)
            else:
                try:
                    data = self.model.parse(source, timeout=remaining)
                    if data:
                        recipe = pipeline.merge(Recipe.from_dict(data), rules, source)
                        method = "ia"
                        if len(source) > llm.MAX_INPUT_CHARS:
                            warnings.append(W_LONG)
                    else:
                        warnings.append(W_MODEL_FAILED)
                except ModelBusy:
                    warnings.append(W_MODEL_BUSY)
                except concurrent.futures.TimeoutError:
                    log.warning("import model did not answer in time")
                    warnings.append(W_MODEL_FAILED)
                except Exception:
                    log.exception("import model failed")
                    warnings.append(W_MODEL_FAILED)
        if not recipe.ingredients and not recipe.etapes:
            recipe.notes = f"{recipe.notes}\n\n{source}".strip()
            warnings.append(W_UNSTRUCTURED)
        return recipe, method, warnings


def finish(recipe: Recipe) -> dict:
    """The recipe as the clients' JSON, normalised and within the limits a save accepts."""
    from mamarmiton_server.api import MAX_ITEMS, MAX_NOTES, MAX_NUMBER, MAX_TEXT

    data = Recipe.from_dict(recipe.to_dict()).to_dict()
    for key in ("id", "favori", "images", "cree_le", "modifie_le"):
        data.pop(key, None)
    data["nom"] = data["nom"][:200]
    data["source"] = data["source"][:200]
    data["notes"] = data["notes"][:MAX_NOTES]
    for key in ("temps", "temps_cuisson", "pour"):
        data[key] = min(data[key], MAX_NUMBER)
    data["ingredients"] = [{"nom": i["nom"][:MAX_TEXT], "quantité": i["quantité"][:MAX_TEXT]}
                           for i in data["ingredients"][:MAX_ITEMS]]
    data["etapes"] = [s[:MAX_TEXT] for s in data["etapes"][:MAX_ITEMS]]
    return data


class UrlBody(BaseModel):
    url: str = Field(max_length=2000)


# ------------------------------------------------------------------ routes
def register(app: FastAPI, settings: Settings, Session) -> None:
    app.state.importer = Importer(settings)

    @app.get("/import/status")
    def import_status(user: Session) -> dict:
        return app.state.importer.status()

    @app.post("/import/recipe")
    def import_recipe(user: Session,
                      files: Annotated[list[UploadFile] | None, File()] = None,
                      text: Annotated[str, Form()] = "") -> dict:
        try:
            return app.state.importer.handle(user["id"], files or [], text or "")
        except ImportProblem as problem:
            raise HTTPException(problem.status, problem.message) from None

    @app.post("/import/url")
    def import_url(user: Session, body: UrlBody) -> dict:
        try:
            return app.state.importer.handle_url(user["id"], body.url)
        except ImportProblem as problem:
            raise HTTPException(problem.status, problem.message) from None
