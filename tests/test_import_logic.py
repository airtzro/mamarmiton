"""Where "Importer une recette" reads each import, the server answer, and the local text/file path."""

import io
import zipfile
from pathlib import Path

import pytest

from mamarmiton.importers import llm, pipeline
from mamarmiton.importers.pipeline import ImportResult, kind_of
from mamarmiton.models import Recipe
from mamarmiton.remote.client import ApiError, AuthError, OfflineError, UpgradeRequired
from mamarmiton.ui import import_logic as il
from mamarmiton.ui.import_logic import LOCAL, REFUSE, SERVER, ImportJob, ImportRefused, ServerImport

FULL = ServerImport(ocr=True, ai=True)
NO_OCR = ServerImport(ocr=False, ai=True)


def files(tmp_path, *names, size=10):
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        paths.append(path)
    return paths


# ------------------------------------------------------------------ routing
def test_kinds():
    assert [kind_of(n) for n in ("a.PDF", "b.jpg", "c.HEIC", "d.webp", "e.txt", "f.docx", "g.odt", "h.zip")] == [
        "pdf", "image", "image", "image", "document", "document", "document", None]


@pytest.mark.parametrize("names, text, connected, server, local_ocr, route", [
    # connected and the server reads it: everything goes there
    (["p.pdf"], None, True, FULL, False, SERVER),
    (["1.jpg", "2.jpg"], None, True, FULL, False, SERVER),
    (["r.docx"], None, True, FULL, False, SERVER),
    ([], "Pour 4 personnes", True, FULL, False, SERVER),
    (["1.jpg"], "notes", True, FULL, False, SERVER),
    # not connected: this computer; photos only with a local Tesseract
    (["p.pdf"], None, False, None, False, LOCAL),
    (["r.odt", "s.txt"], None, False, None, False, LOCAL),
    ([], "Pour 4 personnes", False, None, False, LOCAL),
    (["1.jpg"], None, False, None, True, LOCAL),
    (["1.jpg"], None, False, None, False, REFUSE),
    (["p.pdf", "1.jpg"], None, False, None, False, REFUSE),
    # connected, but the server cannot help (old server, unreachable, or no OCR for photos/PDFs)
    (["p.pdf"], None, True, None, False, LOCAL),
    (["1.jpg"], None, True, None, False, REFUSE),
    (["1.jpg"], None, True, None, True, LOCAL),
    (["p.pdf"], None, True, NO_OCR, False, LOCAL),
    (["r.docx"], None, True, NO_OCR, False, SERVER),
    (["1.jpg"], None, True, NO_OCR, False, REFUSE),
    # nothing, or a file that is not a recipe
    ([], "  ", False, None, False, REFUSE),
    (["archive.zip"], None, True, FULL, True, REFUSE),
])
def test_routing_table(tmp_path, names, text, connected, server, local_ocr, route):
    plan = il.plan_import(files(tmp_path, *names), text, connected=connected, server=server, local_ocr=local_ocr)
    assert plan.route == route
    assert bool(plan.message) == (route == REFUSE)


def test_refusal_messages(tmp_path):
    photo = files(tmp_path, "1.jpg")
    assert il.plan_import(photo, None, connected=False, server=None, local_ocr=False).message == (
        "Pour lire une photo, connectez-vous à votre compte (le serveur sait lire les photos).")
    assert "Réessayez plus tard" in il.plan_import(photo, None, connected=True, server=None, local_ocr=False).message
    assert "archive.zip" in il.plan_import(files(tmp_path, "archive.zip"), None, connected=False, server=None,
                                           local_ocr=False).message


def test_server_limits(tmp_path):
    many = files(tmp_path, *[f"{i}.jpg" for i in range(9)])
    assert "8 fichiers au maximum" in il.plan_import(many, None, connected=True, server=FULL, local_ocr=False).message
    small = ServerImport(ocr=True, max_mb=1)
    heavy = files(tmp_path, "big.jpg", size=1024 * 1024 + 1)
    assert "1 Mo au maximum" in il.plan_import(heavy, None, connected=True, server=small, local_ocr=False).message
    only_pdf = ServerImport(ocr=True, accepts=frozenset({".pdf"}))
    assert il.plan_import(files(tmp_path, "r.docx"), None, connected=True, server=only_pdf,
                          local_ocr=False).route == LOCAL


def test_normalize_status():
    assert il.normalize_status({"available": False}) is None
    assert il.normalize_status(None) is None and il.normalize_status([]) is None
    status = il.normalize_status({"available": True, "ocr": True, "ai": "yes", "max_files": "4", "max_mb": -1,
                                  "accepts": ["PDF", ".jpg", 3]})
    assert status == ServerImport(ocr=True, ai=False, max_files=4, max_mb=30, accepts=frozenset({".pdf", ".jpg"}))
    assert il.normalize_status({"available": True}).accepts == il.DEFAULT_ACCEPTS


# ------------------------------------------------------------ server answer
ANSWER = {"recipe": {"nom": "Tarte aux pommes", "temps": 20, "temps_cuisson": 35, "pour": 6, "type_plat": "Dessert",
                     "type_cuisson": "Four", "ingredients": [{"nom": "pommes", "quantité": "4"}],
                     "etapes": ["Éplucher.", "Cuire."], "notes": "", "tags": ["goûter"], "source": "",
                     "id": "server-id"},
          "dish_photo": 1, "method": "ia", "warnings": ["Vérifiez le temps de cuisson.", ""]}


def test_server_answer_becomes_the_same_draft_as_a_pdf_import(tmp_path):
    from PIL import Image

    page = tmp_path / "page.jpg"
    Image.new("RGB", (20, 20), "white").save(page)
    dish = tmp_path / "plat.jpg"
    Image.new("RGB", (20, 20), "red").save(dish)
    result = il.result_from_server(ANSWER, [page, dish])
    r = result.recipe
    assert (r.nom, r.temps, r.temps_cuisson, r.pour, r.type_plat, r.tags) == (
        "Tarte aux pommes", 20, 35, 6, "Dessert", ["goûter"])
    assert [(i.nom, i.quantite) for i in r.ingredients] == [("pommes", "4")]
    assert r.id != "server-id" and r.source == "page.jpg"
    assert result.method == "IA" and result.warnings == ["Vérifiez le temps de cuisson."]
    # the dish photo is a copy in a temporary folder: the editor's clean-up must never touch the user's folder
    assert len(result.images) == 1 and result.images[0].read_bytes() == dish.read_bytes()
    assert result.images[0].parent != tmp_path and result.origin == "vos photos"


def test_server_answer_without_photo_or_with_a_bad_index(tmp_path):
    page = files(tmp_path, "page.pdf")
    for index in (None, 5, -1, True, "0", 0):  # 0 points at a PDF, not a photo
        result = il.result_from_server({**ANSWER, "dish_photo": index}, page)
        assert result.images == [] and result.origin == ""
    assert il.result_from_server({"recipe": None, "method": "regles"}, []).method == "règles"
    assert il.result_from_server("nonsense", []).recipe.nom == "Recette sans nom"


def test_unreadable_heic_dish_photo_is_a_warning(tmp_path):
    photo = files(tmp_path, "plat.heic")
    result = il.result_from_server({**ANSWER, "dish_photo": 0, "warnings": []}, photo)
    assert result.images == []
    assert any("photo du plat" in w for w in result.warnings)


# --------------------------------------------------------------- the runner
class FakeClient:
    def __init__(self, status=None, answer=None, status_error=None, import_error=None):
        self.status = {"available": True, "ocr": True, "ai": True} if status is None else status
        self.answer, self.status_error, self.import_error = answer or ANSWER, status_error, import_error
        self.sent = None

    def import_status(self):
        if self.status_error:
            raise self.status_error
        return self.status

    def import_recipe(self, paths, text=None):
        self.sent = (list(paths), text)
        if self.import_error:
            raise self.import_error
        return self.answer


def local_reader(calls):
    def read(paths, text, use_ai, progress):
        calls.append((list(paths), text, use_ai))
        return ImportResult(recipe=Recipe(nom="Lue ici"), method="règles")
    return read


def test_connected_import_goes_to_the_server(tmp_path):
    client, calls = FakeClient(), []
    pdf = files(tmp_path, "p.pdf")
    result = il.run_import(ImportJob(pdf, "notes", client=client), read_local=local_reader(calls))
    assert client.sent == (pdf, "notes") and calls == []
    assert result.recipe.nom == "Tarte aux pommes"


def test_unreachable_server_falls_back_to_this_computer(tmp_path):
    calls = []
    pdf = files(tmp_path, "p.pdf")
    job = ImportJob(pdf, client=FakeClient(import_error=OfflineError()), use_ai=False)
    result = il.run_import(job, read_local=local_reader(calls))
    assert calls == [(pdf, None, False)] and result.recipe.nom == "Lue ici"
    assert result.warnings[0] == il.FALLBACK_WARNING
    assert len(job.network_errors) == 1  # the sidebar learns the server is offline


def test_status_failure_reads_locally_without_sending(tmp_path):
    for error in (OfflineError(), AuthError(401, "Reconnectez-vous."), UpgradeRequired("Trop ancienne")):
        client, calls = FakeClient(status_error=error), []
        job = ImportJob(files(tmp_path, "r.txt"), client=client)
        il.run_import(job, read_local=local_reader(calls))
        assert client.sent is None and len(calls) == 1 and job.network_errors == [error]


def test_old_server_reads_locally(tmp_path):
    client, calls = FakeClient(status={"available": False}), []
    il.run_import(ImportJob(files(tmp_path, "p.pdf"), client=client), read_local=local_reader(calls))
    assert client.sent is None and len(calls) == 1


def test_photos_without_server_or_local_ocr_are_refused(tmp_path):
    photo = files(tmp_path, "1.jpg")
    with pytest.raises(ImportRefused, match="connectez-vous"):
        il.run_import(ImportJob(photo), read_local=local_reader([]))
    with pytest.raises(ImportRefused, match="Réessayez plus tard"):
        il.run_import(ImportJob(photo, client=FakeClient(import_error=OfflineError())), read_local=local_reader([]))
    busy = OfflineError("Le serveur est occupé, réessayez dans un instant.", 503)
    with pytest.raises(ImportRefused, match="occupé"):
        il.run_import(ImportJob(photo, client=FakeClient(import_error=busy)), read_local=local_reader([]))


def test_server_refusal_is_shown_as_is(tmp_path):
    refused = ApiError(422, "Aucune recette trouvée sur ces photos.")
    calls = []
    with pytest.raises(ImportRefused, match="Aucune recette trouvée"):
        il.run_import(ImportJob(files(tmp_path, "1.jpg"), client=FakeClient(import_error=refused)),
                      read_local=local_reader(calls))
    assert calls == []  # the server read it: no second reading here


# --------------------------------------------------- local path (no server)
SAMPLE = """Gâteau au yaourt
Pour 6 personnes
Préparation : 10 min
Cuisson : 35 min
Ingrédients :
1 pot de yaourt
3 oeufs
2 pots de sucre
Préparation :
1. Mélanger le yaourt et les oeufs.
2. Cuire au four à 180°C.
"""


def docx_bytes(paragraphs):
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs) + "</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_pasted_text_uses_the_rules_without_ai(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: False)
    result = pipeline.import_text(SAMPLE, use_llm=False)
    assert result.recipe.nom == "Gâteau au yaourt" and result.recipe.pour == 6
    assert result.method == "règles" and result.images == [] and result.warnings == []
    with pytest.raises(ValueError):
        pipeline.import_text("   ")


def test_text_goes_through_the_same_ai_and_merge_as_pdfs(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "parse_recipe_with_llm", lambda text: {
        "nom": "Gâteau", "temps": 99, "pour": 1, "ingredients": [{"nom": "yaourt", "quantité": "7 pots"}],
        "etapes": []})
    result = pipeline.import_text(SAMPLE)
    r = result.recipe
    assert result.method == "IA"
    assert r.temps == 10 and r.pour == 6  # the text says so: the invented numbers are replaced
    assert r.etapes and r.etapes[0].startswith("Mélanger")  # filled from the rules


def test_documents_and_text_files_are_read_together(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: False)
    doc = tmp_path / "recette.docx"
    doc.write_bytes(docx_bytes(SAMPLE.splitlines()[:8]))
    txt = tmp_path / "suite.txt"
    txt.write_bytes("\n".join(SAMPLE.splitlines()[8:]).encode("cp1252"))
    result = pipeline.import_files([doc, txt], use_llm=False)
    r = result.recipe
    assert r.nom == "Gâteau au yaourt" and len(r.ingredients) == 3 and len(r.etapes) == 2
    assert r.source == "recette.docx" and result.images == []


def test_photos_are_read_with_tesseract(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: False)
    monkeypatch.setattr(pipeline.pdf_text, "ocr_image", lambda path: SAMPLE)
    result = pipeline.import_files(files(tmp_path, "page.jpg"), use_llm=False)
    assert result.recipe.nom == "Gâteau au yaourt"


def test_files_without_any_text_say_so(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.pdf_text, "ocr_image", lambda path: "")
    with pytest.raises(ValueError, match="Aucun texte"):
        pipeline.import_files(files(tmp_path, "page.jpg"), use_llm=False)
    empty = tmp_path / "vide.txt"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="Aucun texte"):
        pipeline.import_files([empty], use_llm=False)


def test_read_locally_routes_text_and_files(monkeypatch):
    seen = []
    monkeypatch.setattr(il, "import_text", lambda text, use_llm, progress: seen.append(("text", text)) or "t")
    monkeypatch.setattr(il, "import_files", lambda paths, use_llm, progress, text: seen.append(("files", text)) or "f")
    assert il.read_locally([], "coucou", True, print) == "t"
    assert il.read_locally([Path("a.pdf")], None, True, print) == "f"
    assert seen == [("text", "coucou"), ("files", "")]
