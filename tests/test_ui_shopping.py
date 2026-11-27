"""Shopping list screens: add from a recipe, tick, add an item, copy, clear. Skipped without a display."""

import tkinter

import pytest

from mamarmiton.models import Ingredient, Recipe
from mamarmiton.shopping import ShoppingState
from mamarmiton.storage import RecipeStore


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


def test_shopping_flow(monkeypatch):
    store = RecipeStore()
    r1 = store.add(Recipe(nom="Crêpes", pour=4, ingredients=[Ingredient("farine", "250 g"), Ingredient("oeufs", "3"),
                                                              Ingredient("sel", "")]))
    r2 = store.add(Recipe(nom="Gâteau", pour=2, ingredients=[Ingredient("farine", "200 g"), Ingredient("lait", "20 cl")]))
    store.save()

    from mamarmiton.ui import shopping as shopping_ui
    from mamarmiton.ui.app import App, SHOPPING
    monkeypatch.setattr(shopping_ui.messagebox, "askyesno", lambda *a, **k: True)
    opened = []
    monkeypatch.setattr(shopping_ui, "open_with_default_app", lambda path: opened.append(path) or True)
    try:
        app = App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    try:
        _pump(app)
        assert app.sidebar.items[SHOPPING].count.cget("text") == ""

        app.open_recipe(app.store.get(r1.id))
        _pump(app)
        assert app.detail.btn_shop.cget("text") == "Ajouter à la liste de courses"
        app.detail._change_servings(+4)  # 8 people
        app.detail._shopping_clicked()
        assert app.shopping.servings_of(r1.id) == 8
        assert app.detail.btn_shop.cget("text").startswith("Dans la liste")
        app.add_to_shopping(app.store.get(r2.id), 2)
        assert app.sidebar.items[SHOPPING].count.cget("text") == "4"  # farine, oeufs, sel, lait

        app.sidebar.select(SHOPPING)
        _pump(app)
        view = app.shopping_view
        assert app.current is view
        assert [i.label for i in view.items if "farine" in i.name] == ["700 g de farine"]
        key = next(i.key for i in view.items if i.name == "lait")
        view._rows[key].var.set(True)
        view._rows[key]._paint()
        assert key in app.shopping.checked
        assert app.sidebar.items[SHOPPING].count.cget("text") == "3"
        assert ShoppingState.load().checked == {key}  # saved

        view.entry.insert(0, "papier absorbant")
        view.add_extra()
        _pump(app)
        assert app.shopping.extras[0]["text"] == "papier absorbant"
        view.copy()
        assert "papier absorbant" in app.clipboard_get() and "lait" not in app.clipboard_get()
        view.print_list()
        assert opened and opened[0].read_text(encoding="utf-8").startswith("Liste de courses")

        for width in (900, 700):
            app.geometry(f"{width + 270}x700")
            _pump(app, 8)
        view.set_servings(app.store.get(r1.id), 4)
        assert app.shopping.servings_of(r1.id) == 4

        app.go_home()
        app.sidebar.select(SHOPPING)
        view.clear()
        _pump(app)
        assert not app.shopping.recipes and not app.shopping.extras
        assert app.sidebar.items[SHOPPING].count.cget("text") == ""
    finally:
        app.destroy()
