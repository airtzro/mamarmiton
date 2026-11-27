"""Regression tests for bugs found by the desktop bug hunt (real window)."""

import tkinter
from tkinter import messagebox

import pytest

from mamarmiton.models import Ingredient, Recipe
from mamarmiton.storage import RecipeStore


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


@pytest.fixture
def app():
    store = RecipeStore()
    for i in range(3):
        store.add(Recipe(nom=f"Recette {i}", tags=["Noël"] if i == 0 else [],
                         ingredients=[Ingredient("farine", "100 g")], etapes=["Cuire 10 minutes."]))
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


def test_sidebar_follows_save_from_editor(app):
    """After saving a new recipe the detail page is shown: the sidebar counts and tags must already be current."""
    app.new_recipe()
    _pump(app)
    app.editor.e_nom.insert(0, "Nouvelle")
    app.editor.e_tag.insert(0, "Fête")
    app.editor.submit()
    _pump(app)
    assert app.current is app.detail
    assert app.sidebar.items[None].count.cget("text") == "4"
    assert "Fête" in [t for t, _n in app.sidebar._tags_signature[0]]


def test_sidebar_follows_favorite_toggle_on_detail(app):
    recipe = app.store.all()[0]
    app.open_recipe(recipe)
    _pump(app)
    app.toggle_favorite(recipe)
    _pump(app)
    assert app.sidebar.items["__favoris__"].count.cget("text") == "1"


def test_undo_delete_does_not_leave_the_editor(app, monkeypatch):
    victim = app.store.all()[0]
    app.open_recipe(victim)
    app.delete_recipe(victim)
    _pump(app)
    app.new_recipe()
    _pump(app)
    app.editor.e_nom.insert(0, "Brouillon")
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(1) or False)
    button = next(w for w in app._toast.winfo_children() if hasattr(w, "invoke"))
    button.invoke()
    _pump(app)
    assert app.current is app.editor and not asked
    assert app.store.get(victim.id) is not None
    assert app.editor.e_nom.get() == "Brouillon"


def test_shortcuts_ignored_in_kitchen_mode(app):
    recipe = app.store.all()[0]
    app.open_recipe(recipe)
    app.open_kitchen(recipe)
    _pump(app)
    app.new_recipe()
    _pump(app)
    assert app.current is not app.editor
    app.close_kitchen()


def test_search_debounce_does_not_pull_user_off_the_recipe(app):
    app.home.search.entry.insert(0, "Rec")
    app.home.search._typed()  # schedules the debounced refresh
    app.open_recipe(app.store.all()[0])
    _pump(app)
    app.after(400, lambda: None)
    for _ in range(40):
        app.update()
        app.after(20)
    import time
    time.sleep(0.4)
    _pump(app)
    assert app.current is app.detail


def test_timer_cancel_stops_ringing(app, monkeypatch):
    from mamarmiton.ui import timerbar
    rings = []
    monkeypatch.setattr(timerbar, "beep", lambda w: rings.append(1))
    monkeypatch.setattr(timerbar, "RING_GAP_MS", 20)
    timer = app.timers.start("Étape 1", 1)
    timer.end = 0
    app.timers._tick()
    assert len(rings) == 1
    app.timers.cancel(timer)
    import time
    time.sleep(0.2)
    _pump(app)
    assert len(rings) == 1


def test_favorite_star_on_home_keeps_scroll_position(app):
    for i in range(40):
        app.store.add(Recipe(nom=f"Z{i:02d}"))
    app.geometry("1100x700")
    app.home.m_sort.set(next(k for k in __import__("mamarmiton.ui.home", fromlist=["x"]).SORT_LABELS if "nom" in k))
    app.go_home()
    _pump(app, 10)
    canvas = app.home.grid_area._parent_canvas
    canvas.yview_moveto(0.6)
    _pump(app)
    before = canvas.yview()[0]
    assert before > 0.3
    target = app.home._cards[30].recipe if hasattr(app.home._cards[30], "recipe") else app.store.all()[30]
    app.toggle_favorite(target)
    _pump(app, 10)
    assert canvas.yview()[0] > 0.2


def test_flowframe_does_not_leak_replaced_chips(app):
    import time
    for _ in range(5):
        app.toggle_tag("Noël")
        _pump(app)
    flow = app.home.tag_flow
    time.sleep(0.4)
    _pump(app, 10)
    assert len(flow.winfo_children()) == len(flow.items) <= 1


def test_editor_suggestions_do_not_leak(app):
    app.new_recipe()
    _pump(app)
    import time
    for ch in "No":
        app.editor.e_tag.insert("end", ch)
        app.editor._tag_typed()
    time.sleep(0.4)
    _pump(app, 10)
    flow = app.editor.tag_suggestions
    assert len(flow.winfo_children()) == len(flow.items)
