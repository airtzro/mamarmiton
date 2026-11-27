"""The counters are hooked to what people do, and the Aide dialog has the toggle and the report form."""

import tkinter
from datetime import date

import pytest

from mamarmiton import report, usage
from mamarmiton.models import Ingredient, Recipe
from mamarmiton.storage import RecipeStore


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


@pytest.fixture
def app():
    store = RecipeStore()
    store.add(Recipe(nom="Gâteau", ingredients=[Ingredient("farine", "100 g")], etapes=["Cuire 10 minutes."]))
    store.save()
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


def today():
    return usage.counter().snapshot().get(date.today().isoformat(), {})


def test_actions_are_counted(app):
    recipe = app.store.all()[0]
    app.open_recipe(recipe)
    app.toggle_favorite(recipe)
    app.add_to_shopping(recipe, 4)
    app.show_shopping()
    app.start_timer("x", 60)
    app.duplicate_recipe(recipe)
    counts = today()
    for event in ("recipe_open", "favorite_toggle", "shopping_add", "shopping_open", "timer_start", "variant_create"):
        assert counts.get(event) == 1, event
    assert counts["active"] == 1


def test_opt_out_in_the_app(app):
    app.set_share_usage(False)
    app.open_recipe(app.store.all()[0])
    assert today() == {}
    assert app.settings.share_usage is False
    app.set_share_usage(True)
    app.open_recipe(app.store.all()[0])
    assert today()["recipe_open"] == 1


def test_help_dialog_and_report_form(app, monkeypatch):
    from tkinter import messagebox

    from mamarmiton.ui.dialogs import HelpDialog, ReportDialog

    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    help_dialog = HelpDialog(app, app)
    _pump(app)
    help_dialog.destroy()
    dialog = ReportDialog(app, app)
    _pump(app)
    dialog._send()  # empty message: refused
    assert "quelques mots" in dialog.msg.cget("text")
    dialog.text.insert("1.0", "ça plante")
    dialog._send()
    for _ in range(40):  # the work runs in a thread; local mode queues the report
        _pump(app)
        if report.pending():
            break
        import time
        time.sleep(0.05)
    assert report.pending() == 1
