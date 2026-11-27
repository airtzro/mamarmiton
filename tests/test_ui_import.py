""""Importer une recette" in the real window: the three choices, the waiting dialog, cancel, the editor."""

import threading
import time
import tkinter

import pytest

from mamarmiton.importers import ImportResult
from mamarmiton.models import Ingredient, Recipe


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


def _wait(app, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        _pump(app, 2)
        time.sleep(0.05)
    return condition()


@pytest.fixture
def app():
    from mamarmiton.ui.app import App
    try:
        a = App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    _pump(a)
    yield a
    try:
        a.destroy()
    except tkinter.TclError:
        pass


def _dialogs(app, cls):
    return [w for w in app.winfo_children() if isinstance(w, cls) and w.winfo_exists()]


def test_sidebar_button_opens_the_three_choices(app, monkeypatch):
    from mamarmiton.ui import import_dialog

    app.sidebar.btn_import.invoke()
    _pump(app)
    (dlg,) = _dialogs(app, import_dialog.ImportRecipeDialog)
    assert [c.winfo_children()[1].cget("text") for c in dlg.choices] == [
        "Un fichier ou une photo", "Une page internet", "Coller un texte"]
    opened = []
    monkeypatch.setattr(app, "import_url", lambda: opened.append("web"))
    dlg.choose_web()
    _pump(app)
    assert opened == ["web"] and not _dialogs(app, import_dialog.ImportRecipeDialog)
    app.import_pdf()  # the former entry point now opens the same dialog
    _pump(app)
    assert len(_dialogs(app, import_dialog.ImportRecipeDialog)) == 1


def test_pasted_text_opens_the_editor_with_the_check_notice(app, monkeypatch):
    from mamarmiton.ui import import_dialog

    jobs = []

    def fake_run(job, progress):
        jobs.append(job)
        progress("Lecture…")
        return ImportResult(recipe=Recipe(nom="Gâteau au yaourt", ingredients=[Ingredient("yaourt", "1 pot")]),
                            method="IA", warnings=["Vérifiez les quantités."])

    monkeypatch.setattr(import_dialog, "run_import", fake_run)
    dlg = import_dialog.ImportRecipeDialog(app, app)
    _pump(app)
    dlg.show_paste()
    dlg.read_pasted()  # empty: says so, stays open
    assert "Collez d'abord" in dlg.error.cget("text") and dlg.winfo_exists()
    dlg.text.insert("1.0", "Gâteau au yaourt\n1 pot de yaourt")
    dlg.read_pasted()
    assert _wait(app, lambda: app.current is app.editor)
    assert jobs[0].text.startswith("Gâteau") and jobs[0].paths == [] and jobs[0].client is None
    assert app.editor.e_nom.get() == "Gâteau au yaourt"
    assert app.editor.dirty()  # an imported recipe always asks before being dropped
    assert not _dialogs(app, import_dialog.WaitingDialog)


def test_photo_offline_without_local_ocr_is_refused_before_waiting(app, monkeypatch, tmp_path):
    from mamarmiton.ui import import_dialog

    photo = tmp_path / "page.jpg"
    photo.write_bytes(b"x")
    shown = []
    monkeypatch.setattr(import_dialog, "_photos_readable_here", lambda: False)
    monkeypatch.setattr(import_dialog.filedialog, "askopenfilenames", lambda **kw: (str(photo),))
    monkeypatch.setattr(import_dialog.messagebox, "showinfo", lambda title, message, **kw: shown.append(message))
    monkeypatch.setattr(import_dialog, "run_import", lambda job, progress: pytest.fail("must not start"))
    dlg = import_dialog.ImportRecipeDialog(app, app)
    _pump(app)
    dlg.choose_files()
    _pump(app)
    assert shown == ["Pour lire une photo, connectez-vous à votre compte (le serveur sait lire les photos)."]
    assert not _dialogs(app, import_dialog.WaitingDialog)


def test_cancel_ignores_the_result_and_drops_its_photos(app, monkeypatch, tmp_path):
    from mamarmiton.ui import import_dialog

    folder = tmp_path / "tmp_import"
    folder.mkdir()
    photo = folder / "plat.jpg"
    photo.write_bytes(b"x")
    gate = threading.Event()
    finished = []

    def slow(progress):
        progress("Lecture de la recette par le serveur…")
        gate.wait(5)
        return ImportResult(recipe=Recipe(nom="Trop tard"), images=[photo])

    waiting = import_dialog.WaitingDialog(app, "page1.jpg", slow, lambda r: pytest.fail("result must be ignored"),
                                          on_finish=lambda: finished.append(True))
    assert _wait(app, lambda: waiting.status.cget("text").startswith("Lecture de la recette"))
    waiting.close()  # "Annuler"
    _pump(app)
    gate.set()
    assert _wait(app, lambda: not folder.exists(), seconds=5)
    assert finished == [True] and app.current is not app.editor


def test_refusal_from_the_reading_is_shown(app, monkeypatch):
    from mamarmiton.ui import import_dialog
    from mamarmiton.ui.import_logic import ImportRefused

    shown = []
    monkeypatch.setattr(import_dialog.messagebox, "showinfo", lambda title, message, **kw: shown.append(message))

    def refuse(progress):
        raise ImportRefused("Aucune recette trouvée sur ces photos.")

    import_dialog.WaitingDialog(app, "page.jpg", refuse, lambda r: pytest.fail("no result"))
    assert _wait(app, lambda: shown == ["Aucune recette trouvée sur ces photos."])
    assert not _dialogs(app, import_dialog.WaitingDialog)
