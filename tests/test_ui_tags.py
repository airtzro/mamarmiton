"""Tags in the real window: sidebar section, filter chips, detail chips, editor input."""

import tkinter

import pytest

from mamarmiton.models import Recipe
from mamarmiton.storage import RecipeStore


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


def _make_app():
    store = RecipeStore()
    for i in range(9):
        store.add(Recipe(nom=f"Recette {i}", tags=["Noël", f"t{i}"] if i < 4 else [f"t{i}"]))
    store.save()
    from mamarmiton.ui.app import App
    try:
        return App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")


def test_tags_ui_flow():
    app = _make_app()
    try:
        app.geometry("840x760")
        _pump(app)
        sb = app.sidebar
        assert sb._tags_signature is None or sb.tag_box.winfo_children()
        app.go_home()
        _pump(app)
        counts = dict(sb._tags_signature[0])
        assert counts["Noël"] == 4 and len(counts) == 10

        app.toggle_tag("Noël")
        _pump(app)
        assert len(app.filters().tags) == 1
        assert app.current is app.home and len(app.home._cards) == 4
        assert len(app.home.tag_flow.items) == 1
        app.toggle_tag("t1")
        _pump(app)
        assert len(app.home._cards) == 1
        app.reset_filters()
        _pump(app)
        assert app.home.tags == [] and len(app.home._cards) == 9

        sb._toggle_tags_expanded()
        _pump(app)
        assert sb.tags_expanded
        sb._toggle_tags_expanded()

        recipe = next(r for r in app.store.all() if "Noël" in r.tags)
        app.open_recipe(recipe)
        _pump(app)
        app.filter_by_tag("Noël")
        _pump(app)
        assert app.current is app.home and len(app.home._cards) == 4
    finally:
        app.destroy()


def test_editor_tags():
    app = _make_app()
    try:
        _pump(app)
        recipe = next(r for r in app.store.all() if r.tags == ["t8"])
        saved = []
        app.editor.open(recipe, saved.append)
        app._switch(app.editor)
        _pump(app)
        ed = app.editor
        assert not ed.dirty()
        ed.e_tag.insert(0, "sans gluten, noel")
        ed._tag_typed()
        _pump(app)
        assert ed.tags == ["t8", "sans gluten"]  # "noel" still being typed
        assert ed.dirty()
        ed._commit_tag_text()
        assert ed.tags == ["t8", "sans gluten", "noel"]
        ed._remove_tag("t8")
        ed.e_tag.insert(0, "Rapide")  # pending text is kept on save
        ed.submit()
        assert saved and saved[0].tags == ["sans gluten", "noel", "Rapide"]
    finally:
        app.destroy()
