"""Sidebar on short windows: the categories stay visible (compact layout, then scrolling)."""

import tkinter

import customtkinter as ctk
import pytest

from mamarmiton.models import DISH_TYPES, Ingredient, Recipe
from mamarmiton.storage import RecipeStore
from mamarmiton.ui import remote_logic as rl


def _pump(app, times=5):
    for _ in range(times):
        app.update_idletasks()
        app.update()


@pytest.fixture
def app():
    store = RecipeStore()
    for i in range(3):
        store.add(Recipe(nom=f"Recette {i}", tags=[f"tag{i}", f"autre{i}", f"encore{i}"],
                         ingredients=[Ingredient("farine", "100 g")], etapes=["Cuire."]))
    store.save()
    from mamarmiton.ui.app import App
    try:
        a = App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    _pump(a, 10)
    yield a
    try:
        a.destroy()
    except tkinter.TclError:
        pass


def test_one_import_button(app):
    sidebar = app.sidebar
    assert sidebar.btn_import.cget("text") == "Importer une recette"
    texts = [w.cget("text") for w in sidebar.bottom.winfo_children() if isinstance(w, ctk.CTkButton)]
    assert "Importer un PDF" not in texts and "Importer depuis un site" not in texts


def test_compact_layout_is_tighter_and_moves_help_beside_the_account(app):
    sidebar = app.sidebar
    sidebar._fitting = True  # the window may be tall: keep the automatic choice out of this test
    sidebar.set_compact(True)
    _pump(app)
    assert all(item.cget("height") == sidebar.items[None].COMPACT_HEIGHT for item in sidebar.items.values())
    assert not sidebar._help_row.winfo_ismapped()
    assert sidebar.btn_help_small.winfo_ismapped()  # local mode: beside "Se connecter"
    assert not sidebar.tagline.winfo_ismapped()
    sidebar.set_compact(False)
    _pump(app)
    assert sidebar.items[None].cget("height") == sidebar.items[None].HEIGHT
    assert sidebar._help_row.winfo_ismapped() and not sidebar.btn_help_small.winfo_ismapped()


def test_short_window_switches_to_compact_and_back(app, monkeypatch):
    sidebar = app.sidebar
    sidebar.set_compact(False)
    _pump(app)
    canvas = sidebar.middle._parent_canvas
    last = sidebar.items[DISH_TYPES[-1]]
    # the navigation area is shorter than the last category's bottom edge: "Pains" would be cut
    monkeypatch.setattr(canvas, "winfo_height", lambda: last.winfo_y() + last.winfo_height() - 20)
    sidebar._fit()
    assert sidebar.compact
    monkeypatch.undo()
    sidebar._full_need = 10  # plenty of room again
    sidebar._fit()
    assert not sidebar.compact


def test_scroll_area_shows_its_scrollbar_only_when_needed(app):
    from mamarmiton.ui.widgets import ScrollFrame

    holder = ctk.CTkFrame(app, width=200, height=120)
    holder.pack_propagate(False)
    holder.place(x=0, y=0)
    area = ScrollFrame(holder, autohide=True)
    area.pack(fill="both", expand=True)
    rows = [ctk.CTkLabel(area, text=f"ligne {i}", height=30) for i in range(10)]
    for row in rows:
        row.pack()
    _pump(app, 10)
    assert area._scrollbar.winfo_ismapped()
    for row in rows[1:]:
        row.destroy()
    _pump(app, 10)
    assert not area._scrollbar.winfo_ismapped()
    assert area._parent_canvas.yview()[0] == 0.0
    holder.destroy()


def test_short_offline_status_for_the_compact_sidebar():
    assert rl.status_line("offline", 3, short=True)[1] == "Hors ligne · 3 en attente"
    assert "plus tard" in rl.status_line("offline", 3)[1]
