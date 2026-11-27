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

        app.open_recipe(app.store.get(recipe.id))
        _pump(app)
        _kitchen_walk(app, recipe.id)
    finally:
        app.destroy()


def _kitchen_walk(app, recipe_id):
    import time

    app.open_kitchen(app.store.get(recipe_id))
    _pump(app)
    k = app.kitchen
    assert k is not None and k.counter.cget("text") == "Étape 1 / 2"
    assert str(k.btn_prev.cget("state")) == "disabled"
    app._kitchen_key("next")
    _pump(app)
    assert k.counter.cget("text") == "Étape 2 / 2"
    assert len(k.timer_row.winfo_children()) == 1
    k.timer_row.winfo_children()[0].invoke()
    _pump(app)
    assert len(app.timers.timers) == 1
    assert "Étape 2 · 30:00" in app.timers.timers[0].text.cget("text")
    k.toggle_ingredients()
    _pump(app)
    app._kitchen_key("previous")
    assert k.counter.cget("text") == "Étape 1 / 2"
    app._kitchen_key("next")
    _pump(app)
    assert k.btn_next.cget("text").startswith("Terminer")
    app._kitchen_key("close")
    _pump(app)
    assert app.kitchen is None

    # the timer survives leaving the recipe, and rings when it ends
    app.go_home()
    _pump(app)
    assert len(app.timers.timers) == 1
    timer = app.timers.timers[0]
    timer.end = time.monotonic() - 1
    app.timers._tick()
    _pump(app)
    assert timer.finished and "Terminé" in timer.text.cget("text")
    app.timers.cancel(timer)
    assert not app.timers.timers
