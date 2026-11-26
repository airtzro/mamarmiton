"""Reusable widgets: modal window base, image cache, recipe card."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageOps

from mamarmiton import config
from mamarmiton.models import Recipe, format_minutes
from mamarmiton.ui import theme

log = logging.getLogger(__name__)

_image_cache: dict[tuple[str, int, int, float], ctk.CTkImage] = {}


def load_image(path: Path, size: tuple[int, int], cover: bool = False) -> ctk.CTkImage | None:
    """Load ``path`` as a CTkImage fitting (or, with ``cover``, filling) ``size``."""
    try:
        key = (str(path), size[0], size[1], path.stat().st_mtime + (0.5 if cover else 0))
    except OSError:
        return None
    if key in _image_cache:
        return _image_cache[key]
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            if cover:
                img = ImageOps.fit(img, size, Image.LANCZOS)
            else:
                img.thumbnail(size, Image.LANCZOS)
            photo = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception:
        log.exception("Cannot load image %s", path)
        return None
    _image_cache[key] = photo
    return photo


def set_window_icon(window) -> None:
    icon = config.resource_path(config.ICON)
    if not icon.exists():
        return
    try:
        if sys.platform.startswith("win"):
            window.iconbitmap(str(icon))
        else:
            from PIL import ImageTk

            with Image.open(icon) as img:
                window._icon_photo = ImageTk.PhotoImage(img.convert("RGBA"))
            window.iconphoto(True, window._icon_photo)
    except Exception:
        log.debug("Could not set window icon", exc_info=True)


class Modal(ctk.CTkToplevel):
    """A dialog window that stays on top of the main window and blocks it."""

    def __init__(self, master, title: str, width: int, height: int):
        super().__init__(master)
        self.title(title)
        self.configure(fg_color=theme.BG)
        self.transient(master.winfo_toplevel())
        # centre over the parent
        master.update_idletasks()
        parent = master.winfo_toplevel()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(width, 420), min(height, 300))
        self.bind("<Escape>", lambda _e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        # CTkToplevel resets the icon shortly after creation on Windows
        self.after(250, lambda: set_window_icon(self))
        self.after(100, self._grab)

    def _grab(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:  # window not viewable yet
            self.after(100, self._grab)

    def close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class RecipeCard(ctk.CTkFrame):
    WIDTH = 270
    PHOTO_H = 140

    def __init__(self, master, recipe: Recipe, photo: Path | None,
                 on_open: Callable[[Recipe], None], on_favorite: Callable[[Recipe], None]):
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=16, border_width=1,
                         border_color=theme.BORDER, width=self.WIDTH)
        self.recipe = recipe
        self._on_open = on_open
        self.grid_columnconfigure(0, weight=1)

        banner_color = theme.DISH_COLORS.get(recipe.type_plat, theme.SURFACE_ALT)
        image = load_image(photo, (self.WIDTH - 2, self.PHOTO_H), cover=True) if photo else None
        if image:
            banner = ctk.CTkLabel(self, text="", image=image, corner_radius=14)
        else:
            banner = ctk.CTkLabel(self, text=recipe.type_plat, fg_color=banner_color, corner_radius=14,
                                  height=70, font=theme.font(15, "bold"), text_color=theme.MUTED)
        banner.grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))

        star = ctk.CTkButton(
            self, text="★" if recipe.favori else "☆", width=34, height=34, corner_radius=17,
            fg_color=theme.SURFACE, hover_color=theme.ACCENT_SOFT,
            text_color=theme.STAR if recipe.favori else theme.MUTED, font=theme.font(18),
            command=lambda: on_favorite(recipe),
        )
        star.place(relx=1.0, x=-10, y=10, anchor="ne")

        title = ctk.CTkLabel(self, text=recipe.nom, font=theme.font(16, "bold"), text_color=theme.TEXT,
                             wraplength=self.WIDTH - 30, justify="left", anchor="w")
        title.grid(row=1, column=0, sticky="ew", padx=14, pady=(10, 2))

        meta_parts = [format_minutes(recipe.temps_total) if recipe.temps_total else None,
                      recipe.type_cuisson, f"{recipe.pour} pers."]
        meta = ctk.CTkLabel(self, text="  ·  ".join(p for p in meta_parts if p), font=theme.font(12),
                            text_color=theme.MUTED, anchor="w")
        meta.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 12))
        theme.chip(chips, recipe.type_plat, fg=banner_color).pack(side="left", padx=(0, 6))
        if recipe.ingredients:
            theme.chip(chips, f"{len(recipe.ingredients)} ingrédients").pack(side="left")

        for widget in (self, banner, title, meta, chips, *chips.winfo_children()):
            widget.bind("<Button-1>", self._clicked)
            widget.bind("<Enter>", self._hover_on)
            widget.bind("<Leave>", self._hover_off)
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass

    def _clicked(self, _event=None) -> None:
        self._on_open(self.recipe)

    def _hover_on(self, _event=None) -> None:
        self.configure(border_color=theme.ACCENT)

    def _hover_off(self, _event=None) -> None:
        # Leaving the frame for one of its own labels is not leaving the card.
        def check():
            if not self.winfo_exists():
                return
            x, y = self.winfo_pointerxy()
            inside = (self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width()
                      and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height())
            if not inside:
                self.configure(border_color=theme.BORDER)
        self.after(20, check)
