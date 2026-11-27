"""Import automatique: files or pasted text -> a recipe to check (POST /import/recipe).

Tesseract and the local model are replaced by fakes, except in the one real OCR test
(skipped when the tesseract program is not installed).
"""

import concurrent.futures
import io
import shutil
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from mamarmiton_server import images, importer
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings
from mamarmiton_server.importer import OcrText

from test_api import register  # noqa: F401

RECIPE_TEXT = """TARTE AUX POMMES
Pour 6 personnes
Préparation : 20 min
Cuisson : 35 min
Ingrédients
250 g de farine
125 g de beurre
4 pommes
Préparation
1. Étaler la pâte dans le moule.
2. Couper les pommes et les disposer sur la pâte.
3. Enfourner 35 min à 180 °C.
Astuce
Ma grand-mère ajoutait un peu de cannelle."""


class FakeOcr:
    def __init__(self, texts=(), confidence=92.0, ok=True):
        self.texts = list(texts)
        self.confidence = confidence
        self.ok = ok
        self.calls = []

    def available(self):
        return self.ok

    def __call__(self, image, timeout):
        self.calls.append({"size": image.size, "mode": image.mode, "timeout": timeout})
        return OcrText(self.texts.pop(0) if self.texts else "", self.confidence)


class FakeModel:
    def __init__(self, answer=None, ok=True, error=None):
        self.answer = answer
        self.ok = ok
        self.error = error
        self.texts = []

    def available(self):
        return self.ok

    def parse(self, text, timeout):
        self.texts.append(text)
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, login_attempts=3, login_window_s=60)


@pytest.fixture
def app(settings):
    app = create_app(settings)
    app.state.importer.ocr = FakeOcr(ok=False)
    app.state.importer.model = FakeModel(ok=False)
    app.state.importer.credits = None  # tiers have their own tests (test_tiers.py)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth(client, settings):
    return register(client, settings, "mamounette")


def post(client, auth, files=(), text=None):
    data = {"text": text} if text is not None else {}
    return client.post("/import/recipe", headers=auth, data=data,
                       files=[("files", (name, content, "application/octet-stream")) for name, content in files])


def png(size=(300, 200), color="white", **save) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG", **save)
    return out.getvalue()


def jpeg(size=(300, 200), exif=None) -> bytes:
    out = io.BytesIO()
    img = Image.new("RGB", size, "white")
    kwargs = {}
    if exif is not None:
        info = Image.Exif()
        info.update(exif)
        kwargs["exif"] = info
    img.save(out, "JPEG", **kwargs)
    return out.getvalue()


def docx(paragraphs) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def odt(paragraphs) -> bytes:
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"')
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", f"<office:document-content {ns}><office:body><office:text>{body}"
                                   "</office:text></office:body></office:document-content>")
    return out.getvalue()


def pdf(pages) -> bytes:
    """A small valid PDF; each page is a list of text lines (an empty list = a page with no text, like a scan)."""
    n = len(pages)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n)), n)]
    for i, lines in enumerate(pages):
        ops = []
        if lines:
            ops = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
            for line in lines:
                ops.append("(%s) Tj T*" % line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))
            ops.append("ET")
        content = "\n".join(ops).encode("cp1252")
        font = 3 + 2 * n
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                       b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (4 + 2 * i, font))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


# ------------------------------------------------------------------ status and auth
def test_status_needs_login(client):
    assert client.get("/import/status").status_code == 401
    assert client.post("/import/recipe", data={"text": RECIPE_TEXT}).status_code == 401


def test_status_reports_ocr_and_model(client, app, auth):
    body = client.get("/import/status", headers=auth).json()
    assert body == {"available": True, "ocr": False, "ai": False, "max_files": 8, "max_mb": 30,
                    "accepts": [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".txt", ".docx", ".odt"],
                    "url": True}
    app.state.importer.ocr = FakeOcr()
    app.state.importer.model = FakeModel()
    body = client.get("/import/status", headers=auth).json()
    assert body["ocr"] is True and body["ai"] is True


def test_real_status_without_model_file_has_no_ai(settings):
    client = TestClient(create_app(settings))
    auth = register(client, settings, "mamounette")
    body = client.get("/import/status", headers=auth).json()
    assert body["ai"] is False  # no GGUF file in the data folder
    assert body["ocr"] is (shutil.which("tesseract") is not None)
    assert settings.import_model_file == settings.data_dir / "models" / "qwen2.5-1.5b-instruct-q4_0.gguf"


def test_model_path_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MAMARMITON_IMPORT_MODEL_PATH", "/models/qwen.gguf")
    assert str(Settings(data_dir=tmp_path).import_model_file) == "/models/qwen.gguf"


# ------------------------------------------------------------------ text sources
def test_pasted_text_uses_the_rules(client, auth):
    r = post(client, auth, text=RECIPE_TEXT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "regles"
    assert body["dish_photo"] is None
    assert body["warnings"] == []  # clear sections: copied as written, the editor's own notice is enough
    recipe = body["recipe"]
    assert set(recipe) == {"nom", "temps", "temps_cuisson", "pour", "pour_unite", "type_plat", "type_cuisson", "ingredients",
                           "etapes", "notes", "tags", "source"}
    assert recipe["nom"] == "Tarte aux pommes"
    assert recipe["pour"] == 6 and recipe["temps"] == 20 and recipe["temps_cuisson"] == 35
    assert {"nom": "Farine", "quantité": "250 g"} in recipe["ingredients"]
    assert recipe["etapes"][0] == "Étaler la pâte dans le moule."
    assert "cannelle" in recipe["notes"]
    assert recipe["type_cuisson"] == "Four" and recipe["source"] == "Texte collé" and recipe["tags"] == []


def test_txt_docx_and_odt_files(client, auth):
    lines = RECIPE_TEXT.splitlines()
    for name, content in (("tarte.txt", RECIPE_TEXT.encode("cp1252")), ("tarte.docx", docx(lines)),
                          ("tarte.odt", odt(lines))):
        r = post(client, auth, files=[(name, content)])
        assert r.status_code == 200, (name, r.text)
        recipe = r.json()["recipe"]
        assert recipe["source"] == name
        assert {"nom": "Beurre", "quantité": "125 g"} in recipe["ingredients"], name


def test_text_without_sections_lands_in_the_notes(client, auth):
    text = "Le gâteau de mamie, celui du dimanche, toujours réussi"
    body = post(client, auth, text=text).json()
    assert text in body["recipe"]["notes"]
    assert any("notes" in w for w in body["warnings"])


def test_nothing_readable_is_422(client, auth):
    r = post(client, auth, text="ok !")
    assert r.status_code == 422
    assert r.json()["detail"] == "Je n'ai pas trouvé de recette dans ce fichier."


def test_bad_requests(client, auth):
    assert post(client, auth).status_code == 400
    assert post(client, auth, text="   ").status_code == 400
    r = post(client, auth, files=[(f"r{i}.txt", RECIPE_TEXT.encode()) for i in range(9)])
    assert r.status_code == 400 and "8 fichiers" in r.json()["detail"]
    r = post(client, auth, text="a" * 100_001)
    assert r.status_code == 400 and "trop long" in r.json()["detail"]
    assert post(client, auth, files=[("vide.txt", b"")]).status_code == 400


# ------------------------------------------------------------------ file types
@pytest.mark.parametrize("name, content", [
    ("recette.pdf", png()),               # a photo renamed .pdf
    ("photo.jpg", b"MZ\x90\x00" + bytes(200)),  # a program renamed .jpg
    ("photo.png", bytes(range(256)) * 4),  # binary junk
    ("recette.exe", b"hello"),            # not an accepted extension
    ("recette.docx", b"PK\x03\x04garbage"),  # a broken zip
    ("recette.txt", jpeg()),              # a photo renamed .txt
])
def test_fake_or_unknown_files_are_415(client, auth, name, content):
    r = post(client, auth, files=[(name, content)])
    assert r.status_code == 415, r.text
    assert "n'est pas d'un type accepté" in r.json()["detail"]


def test_sniff():
    assert importer.sniff(pdf([["x"]])) == "pdf"
    assert importer.sniff(jpeg()) == "jpeg"
    assert importer.sniff(png()) == "png"
    assert importer.sniff(docx(["a"])) == "docx"
    assert importer.sniff(odt(["a"])) == "odt"
    assert importer.sniff("Crêpes\n250 g de farine".encode("utf-8")) == "text"
    assert importer.sniff(b"\x00\x01\x02binary") is None
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, "WEBP")
    assert importer.sniff(out.getvalue()) == "webp"


def test_image_extensions_are_interchangeable(client, app, auth):
    app.state.importer.ocr = FakeOcr([RECIPE_TEXT])
    assert post(client, auth, files=[("photo.jpg", png())]).status_code == 200


@pytest.mark.skipif(not images.HEIF_SUPPORTED, reason="pillow-heif not installed")
def test_heic_photo(client, app, auth):
    app.state.importer.ocr = FakeOcr([RECIPE_TEXT])
    out = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(out, "HEIF")
    assert importer.sniff(out.getvalue()) == "heic"
    r = post(client, auth, files=[("IMG_0001.HEIC", out.getvalue())])
    assert r.status_code == 200, r.text


def test_heic_without_support_asks_for_jpeg(client, app, auth, monkeypatch):
    monkeypatch.setattr(images, "HEIF_SUPPORTED", False)
    data = b"\x00\x00\x00\x1cftypheic" + bytes(100)
    r = post(client, auth, files=[("IMG_0001.heic", data)])
    assert r.status_code == 415 and "JPEG" in r.json()["detail"]


# ------------------------------------------------------------------ photos (OCR)
def test_photo_goes_through_ocr(client, app, auth):
    ocr = FakeOcr([RECIPE_TEXT])
    app.state.importer.ocr = ocr
    r = post(client, auth, files=[("fiche.png", png((600, 400)))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"]["nom"] == "Tarte aux pommes"
    assert body["recipe"]["source"] == "fiche.png"
    assert body["warnings"][0] == "Lecture automatique : vérifiez les quantités avant d'enregistrer."
    call = ocr.calls[0]
    assert call["mode"] == "L"  # greyscale
    assert max(call["size"]) == importer.OCR_MIN_SIDE  # small pictures are enlarged
    assert 0 < call["timeout"] <= importer.OCR_PAGE_TIMEOUT_S


def test_huge_photo_is_reduced_and_turned_upright(client, app, auth):
    ocr = FakeOcr([RECIPE_TEXT, RECIPE_TEXT])
    app.state.importer.ocr = ocr
    assert post(client, auth, files=[("grand.png", png((5000, 3000)))]).status_code == 200
    assert max(ocr.calls[0]["size"]) == importer.OCR_MAX_SIDE
    # EXIF orientation 6: the phone was held upright, the pixels are stored sideways
    assert post(client, auth, files=[("tourne.jpg", jpeg((1800, 900), exif={0x0112: 6}))]).status_code == 200
    width, height = ocr.calls[1]["size"]
    assert height > width


def test_transparent_screenshot_is_put_on_white():
    img = Image.new("RGBA", (2000, 1000), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((10, 10, 100, 100), fill=(0, 0, 0, 255))
    ready = importer.prepare_for_ocr(img)
    assert ready.mode == "L" and ready.getpixel((500, 500)) == 255 and ready.getpixel((50, 50)) == 0


def test_low_confidence_adds_a_warning(client, app, auth):
    app.state.importer.ocr = FakeOcr([RECIPE_TEXT], confidence=41.0)
    warnings = post(client, auth, files=[("carte.jpg", jpeg())]).json()["warnings"]
    assert any("difficiles à lire" in w for w in warnings)


def test_photo_without_ocr_is_503(client, auth):
    r = post(client, auth, files=[("fiche.jpg", jpeg())], text=RECIPE_TEXT)
    assert r.status_code == 503
    assert r.json()["detail"] == "La lecture des photos et des PDF scannés n'est pas activée sur le serveur."


def test_broken_photo_is_422(client, app, auth):
    app.state.importer.ocr = FakeOcr()
    r = post(client, auth, files=[("photo.jpg", jpeg()[:200])])
    assert r.status_code == 422 and "illisible" in r.json()["detail"]


# ------------------------------------------------------------------ dish photo
def test_photo_with_almost_no_text_is_the_dish(client, app, auth):
    app.state.importer.ocr = FakeOcr(["", RECIPE_TEXT, "Bon appétit"])
    files = [("plat.jpg", jpeg()), ("recette.jpg", jpeg()), ("autre-plat.jpg", jpeg())]
    body = post(client, auth, files=files).json()
    assert body["dish_photo"] == 0  # the first one only
    assert body["recipe"]["source"] == "recette.jpg"
    assert "Bon appétit" not in body["recipe"]["notes"]


def test_dish_photo_index_counts_every_uploaded_file(client, app, auth):
    app.state.importer.ocr = FakeOcr(["  .  "])
    body = post(client, auth, files=[("recette.txt", RECIPE_TEXT.encode()), ("plat.png", png())]).json()
    assert body["dish_photo"] == 1


def test_no_dish_photo_for_documents_or_text_photos(client, app, auth):
    app.state.importer.ocr = FakeOcr([RECIPE_TEXT])
    assert post(client, auth, files=[("fiche.jpg", jpeg())]).json()["dish_photo"] is None
    assert post(client, auth, files=[("r.pdf", pdf([RECIPE_TEXT.splitlines()]))]).json()["dish_photo"] is None


def test_only_a_dish_photo_is_422(client, app, auth):
    app.state.importer.ocr = FakeOcr([""])
    r = post(client, auth, files=[("plat.jpg", jpeg())])
    assert r.status_code == 422 and "photo semble montrer le plat" in r.json()["detail"]


def test_dish_photo_with_pasted_text(client, app, auth):
    app.state.importer.ocr = FakeOcr([""])
    body = post(client, auth, files=[("plat.jpg", jpeg())], text=RECIPE_TEXT).json()
    assert body["dish_photo"] == 0 and body["recipe"]["source"] == "Texte collé"


# ------------------------------------------------------------------ PDFs
def test_pdf_with_a_text_layer_needs_no_ocr(client, app, auth):
    ocr = FakeOcr()
    app.state.importer.ocr = ocr
    lines = [line.replace("É", "E") for line in RECIPE_TEXT.splitlines()]
    r = post(client, auth, files=[("tarte.pdf", pdf([lines]))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"nom": "Farine", "quantité": "250 g"} in body["recipe"]["ingredients"]
    assert body["recipe"]["source"] == "tarte.pdf"
    assert ocr.calls == []
    assert not any("Lecture automatique" in w for w in body["warnings"])


def test_scanned_pdf_pages_go_through_ocr(client, app, auth):
    ocr = FakeOcr([RECIPE_TEXT, "Bon appétit !"])
    app.state.importer.ocr = ocr
    r = post(client, auth, files=[("scan.pdf", pdf([[], []]))])
    assert r.status_code == 200, r.text
    assert len(ocr.calls) == 2
    assert ocr.calls[0]["mode"] == "L" and max(ocr.calls[0]["size"]) > 2000  # rendered at about 300 dpi
    assert r.json()["warnings"][0].startswith("Lecture automatique")


def test_pdf_page_limit(client, app, auth, settings):
    settings.import_max_pages = 1
    ocr = FakeOcr([RECIPE_TEXT, RECIPE_TEXT])
    app.state.importer.ocr = ocr
    body = post(client, auth, files=[("scan.pdf", pdf([[], [], []]))]).json()
    assert len(ocr.calls) == 1
    assert "Seules les 1 premières pages du PDF ont été lues." in body["warnings"]


def test_scanned_pdf_without_ocr_is_503_and_not_counted(client, app, auth, settings):
    app.state.importer.limits.per_user = 1
    r = post(client, auth, files=[("scan.pdf", pdf([[]]))])
    assert r.status_code == 503
    assert post(client, auth, text=RECIPE_TEXT).status_code == 200  # the refused scan did not use the quota


def test_broken_pdf_is_422(client, auth):
    r = post(client, auth, files=[("r.pdf", b"%PDF-1.4\n garbage")])
    assert r.status_code == 422


# ------------------------------------------------------------------ local model
def test_unclear_text_gets_the_simple_reading_warning(client, auth):
    body = post(client, auth, text="Tarte aux pommes\nmélanger la farine et le beurre puis cuire longtemps au four").json()
    assert body["method"] == "regles"
    assert "Lecture simple : vérifiez bien la recette." in body["warnings"]


def test_clear_recipe_text_skips_the_local_model(client, app, auth):
    model = FakeModel({"nom": "Autre chose", "ingredients": [], "etapes": []})
    app.state.importer.model = model
    body = post(client, auth, text=RECIPE_TEXT).json()
    assert body["method"] == "regles" and model.texts == []  # the rules copied it as written
    assert body["recipe"]["nom"] == "Tarte aux pommes"


def test_local_model_answer_is_merged_and_checked(client, app, auth, monkeypatch):
    monkeypatch.setattr(importer.pipeline, "rules_are_clear", lambda rules: False)
    answer = {"nom": "Tarte aux pommes de mamie", "temps": 15, "temps_cuisson": 35, "pour": 8,
              "type_plat": "Dessert", "type_cuisson": "Four",
              "ingredients": [{"nom": "farine", "quantité": "300 g"}, {"nom": "beurre", "quantité": "125 g"}],
              "etapes": ["Étaler la pâte.", "Disposer les pommes.", "Cuire 35 min."]}
    model = FakeModel(answer)
    app.state.importer.model = model
    body = post(client, auth, text=RECIPE_TEXT).json()
    assert body["method"] == "ia"
    assert model.texts == [RECIPE_TEXT]
    recipe = body["recipe"]
    assert recipe["nom"] == "Tarte aux pommes de mamie" and recipe["type_plat"] == "Dessert"
    # numbers the text does not contain are replaced by what it says
    assert {"nom": "farine", "quantité": "250 g"} in recipe["ingredients"]
    assert recipe["pour"] == 6 and recipe["temps"] == 20
    assert "Lecture simple : vérifiez bien la recette." not in body["warnings"]


@pytest.mark.parametrize("model, warning", [
    (FakeModel(None), "L'analyse fine n'a pas abouti"),
    (FakeModel(error=RuntimeError("boom")), "L'analyse fine n'a pas abouti"),
    (FakeModel(error=concurrent.futures.TimeoutError()), "L'analyse fine n'a pas abouti"),
    (FakeModel(error=importer.ModelBusy()), "occupée"),
])
def test_local_model_problems_fall_back_to_the_rules(client, app, auth, model, warning, monkeypatch):
    monkeypatch.setattr(importer.pipeline, "rules_are_clear", lambda rules: False)
    app.state.importer.model = model
    body = post(client, auth, text=RECIPE_TEXT).json()
    assert body["method"] == "regles"
    assert body["recipe"]["nom"] == "Tarte aux pommes"
    assert any(warning in w for w in body["warnings"])


def test_local_model_missing_file_is_unavailable(tmp_path):
    assert not importer.LocalModel(tmp_path / "absent.gguf").available()


def test_local_model_that_cannot_run_is_disabled(tmp_path, monkeypatch):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"not a model")
    model = importer.LocalModel(model_file)
    monkeypatch.setattr(importer.importlib.util, "find_spec", lambda name: object())  # pretend it is installed
    assert model.available()
    with pytest.raises(RuntimeError):
        model.parse("texte", timeout=60)  # the check in a child process fails (no library / bad file)
    assert not model.available()


def test_local_model_timeout_and_busy(tmp_path, monkeypatch):
    model = importer.LocalModel(tmp_path / "m.gguf")
    release = threading.Event()
    model._run = lambda text: release.wait(5) and {"nom": text}
    monkeypatch.setattr(importer, "MODEL_WAIT_S", 0.1)
    with pytest.raises(concurrent.futures.TimeoutError):
        model.parse("a", timeout=0.2)
    with pytest.raises(importer.ModelBusy):  # the first answer is still being written
        model.parse("b", timeout=0.2)
    release.set()
    time.sleep(0.2)
    assert model.parse("c", timeout=2) == {"nom": "c"}


# ------------------------------------------------------------------ limits and time
def test_total_size_limit(client, app, auth, settings):
    settings.import_max_mb = 1
    app.state.importer.ocr = FakeOcr()
    big = b"a" * (1024 * 1024 + 10)
    r = post(client, auth, files=[("a.txt", big)])
    assert r.status_code == 413 and "1 Mo" in r.json()["detail"]


def test_body_limit_middleware_for_imports(tmp_path):
    settings = Settings(data_dir=tmp_path, import_max_mb=1)
    client = TestClient(create_app(settings))
    auth = register(client, settings, "mamounette")
    r = post(client, auth, files=[("a.txt", b"a" * (3 * 1024 * 1024))])
    assert r.status_code == 413
    assert r.json()["detail"] == "Fichiers trop lourds : 1 Mo au maximum en tout."


def test_daily_limit_per_account_and_overall(client, app, auth, settings):
    app.state.importer.limits.per_user = 2
    assert post(client, auth, text=RECIPE_TEXT).status_code == 200
    assert post(client, auth, text=RECIPE_TEXT).status_code == 200
    r = post(client, auth, text=RECIPE_TEXT)
    assert r.status_code == 429 and "demain" in r.json()["detail"]
    app.state.importer.limits.per_user = 100
    app.state.importer.limits.overall = 3
    other = register(client, settings, "tristan")
    assert post(client, other, text=RECIPE_TEXT).status_code == 200
    assert post(client, other, text=RECIPE_TEXT).status_code == 429


def test_daily_limit_resets_the_next_day(monkeypatch):
    limits = importer.DailyLimits(per_user=1, overall=10)
    limits.take(1)
    with pytest.raises(importer.ImportProblem):
        limits.take(1)
    limits._day = limits._day.replace(year=2000)
    limits.take(1)


def test_slow_reading_stops_at_the_deadline(client, app, auth, settings):
    now = [0.0]
    settings.import_timeout_s = 100

    class SlowOcr(FakeOcr):
        def __call__(self, image, timeout):
            now[0] += 99  # each page takes 99 s
            return super().__call__(image, timeout)

    ocr = SlowOcr([RECIPE_TEXT, "page 2"])
    app.state.importer.ocr = ocr
    app.state.importer.clock = lambda: now[0]
    body = post(client, auth, files=[("a.jpg", jpeg()), ("b.jpg", jpeg())]).json()
    assert len(ocr.calls) == 1
    assert "La lecture a pris trop de temps : certaines pages n'ont pas été lues." in body["warnings"]


def test_server_busy(client, app, auth, monkeypatch):
    monkeypatch.setattr(importer, "QUEUE_WAIT_S", 0.05)
    for _ in range(app.state.settings.import_parallel):
        app.state.importer.slots.acquire()
    r = post(client, auth, text=RECIPE_TEXT)
    assert r.status_code == 503 and "Réessayez" in r.json()["detail"]


# ------------------------------------------------------------------ helpers
def test_words_to_text_builds_lines_and_confidence():
    data = {"text": ["", "Tarte", "aux", "pommes", "250", "g", ""],
            "conf": ["-1", "90", "80", "70", "60", "50", "-1"],
            "page_num": [1] * 7, "block_num": [0, 1, 1, 1, 2, 2, 2], "par_num": [0, 1, 1, 1, 1, 1, 1],
            "line_num": [0, 1, 1, 1, 1, 1, 1]}
    result = importer.words_to_text(data)
    assert result.text == "Tarte aux pommes\n\n250 g"
    assert result.confidence == pytest.approx(70)


def test_finish_keeps_a_saveable_recipe():
    from mamarmiton.models import Ingredient, Recipe

    recipe = Recipe(nom="x" * 500, pour=10**9, ingredients=[Ingredient("sel", "")] * 400, etapes=["é" * 9000])
    data = importer.finish(recipe)
    assert len(data["nom"]) == 200 and data["pour"] == 1_000_000
    assert len(data["ingredients"]) == 300 and len(data["etapes"][0]) == 4000
    assert "id" not in data and "images" not in data and "favori" not in data


def printed_card(lines) -> Image.Image:
    img = Image.new("RGB", (1000, 80 + 70 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 70 * i), line, fill="black", font=font)
    return img


needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")


@needs_tesseract
def test_real_tesseract_reads_a_printed_card():
    img = printed_card(["Ingredients", "250 g de farine", "4 pommes"])
    result = importer.TesseractOcr()(importer.prepare_for_ocr(img), timeout=60)
    assert "farine" in result.text.lower()
    assert "250" in result.text
    assert result.confidence is not None and result.confidence > 50


@needs_tesseract
def test_real_tesseract_turns_an_upside_down_card():
    lines = ["Gateau au yaourt", "Pour 6 personnes", "Ingredients", "1 pot de yaourt", "3 pots de farine",
             "2 pots de sucre", "3 oeufs", "Preparation", "Melanger le tout et cuire 35 minutes."]
    img = printed_card(lines).rotate(180)
    result = importer.TesseractOcr()(importer.prepare_for_ocr(img), timeout=60)
    assert "farine" in result.text.lower()
