"""Builds the real main window and walks home -> detail -> editor -> home.

Skipped when there is no display (CI runs it under xvfb-run).
"""

import tkinter

import pytest

from mamarmiton.models import Ingredient, Recipe
from mamarmiton.storage import RecipeStore


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


def test_main_window_navigation():
    store = RecipeStore()  # uses the temporary MAMARMITON_DATA_DIR
    recipe = store.add(Recipe(nom="Tarte aux pommes", type_plat="Dessert",
                              ingredients=[Ingredient(nom="pommes", quantite="4")],
                              etapes=["Éplucher les pommes.", "Cuire 30 minutes."]))
    store.save()

    from mamarmiton.ui.app import App
    try:
        app = App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    try:
        _pump(app)
        assert app.current is app.home
        assert len(app.store.all()) == 1

        app.open_recipe(app.store.get(recipe.id))
        _pump(app)
        assert app.current is app.detail

        app.edit_recipe(app.store.get(recipe.id))
        _pump(app)
        assert app.current is app.editor

        app.leave_editor()
        _pump(app)
        assert app.current is app.detail

        app.go_home()
        _pump(app)
        assert app.current is app.home
    finally:
        app.destroy()
