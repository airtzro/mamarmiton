"""Reusable widgets: modal window base, recipe card, sidebar navigation item."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image

from mamarmiton import config
from mamarmiton.models import Recipe, format_minutes, servings_text
from mamarmiton.ui import art, theme

log = logging.getLogger(__name__)


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


def use_portable_corners(root) -> None:
    """On a Linux Tk without Xft, draw rounded corners with canvas circles.

    customtkinter's default there uses a special shapes font, which a Tk
    built without Xft (python.org / uv builds) renders as jagged blocks.
    With Xft (e.g. Arch's ``tk`` package) the default looks better, so keep it.
    Call before creating any rounded widget.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        xft = root.tk.call("::tk::pkgconfig", "get", "fontsystem") == "xft"
    except Exception:
        xft = False
    if not xft:
        from customtkinter.windows.widgets.core_rendering import DrawEngine

        DrawEngine.preferred_drawing_method = "circle_shapes"


def fix_scaled_sizes() -> None:
    """Stop customtkinter from drawing widgets a few pixels too small when scaled.

    On a resize, customtkinter converts the real pixel size back to "unscaled"
    units with ``int(pixels / scaling)``. At the "Grande" / "Très grande" text
    sizes (1.15x / 1.3x) that truncation loses up to a pixel, and the drawing
    is then floored to an even size, so the rounded border ends 2-3 px above
    the real bottom (or right) edge, where the text field paints over it: every
    input looked cut off at the bottom. Keep the exact (float) size instead.
    Call once, before creating widgets.
    """
    from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass

    def _update_dimensions_event(self, event):
        width = self._reverse_widget_scaling(float(event.width))
        height = self._reverse_widget_scaling(float(event.height))
        if abs(self._current_width - width) > 0.01 or abs(self._current_height - height) > 0.01:
            self._current_width, self._current_height = width, height
            self._draw(no_color_updates=True)

    CTkBaseClass._update_dimensions_event = _update_dimensions_event


class ScrollFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame that also scrolls with the wheel on Linux + Tk 9.

    Tk 9 sends <MouseWheel> on X11, but customtkinter only listens to the
    old Button-4/5 events there, so the wheel did nothing.

    ``autohide=True`` (vertical only) shows the scrollbar only while the
    content is taller than the frame, and keeps the content at the top otherwise.
    """

    def __init__(self, master, autohide: bool = False, **kwargs):
        opts = dict(fg_color="transparent", corner_radius=0, scrollbar_button_color=theme.BORDER,
                    scrollbar_button_hover_color=theme.BORDER_STRONG)
        opts.update(kwargs)
        super().__init__(master, **opts)
        if sys.platform.startswith("linux"):
            self._wheel_id = self.bind_all("<MouseWheel>", self._linux_wheel, add=True)
        self._autohide_pending = None
        if autohide:
            self._scrollbar.grid_remove()
            self.bind("<Configure>", self._queue_autohide)  # forced to add: the scroll region keeps updating
            self._parent_canvas.bind("<Configure>", self._queue_autohide, add="+")

    def _queue_autohide(self, _event=None) -> None:
        if self._autohide_pending is None:
            self._autohide_pending = self.after_idle(self._autohide)

    def _autohide(self) -> None:
        self._autohide_pending = None
        try:
            if not self.winfo_exists():
                return
            canvas = self._parent_canvas
            overflow = self.winfo_reqheight() > canvas.winfo_height() + 1
            if overflow and not self._scrollbar.winfo_ismapped():
                self._scrollbar.grid()
            elif not overflow:
                if self._scrollbar.winfo_ismapped():
                    self._scrollbar.grid_remove()
                canvas.yview_moveto(0)
        except Exception:  # destroyed meanwhile
            pass

    def destroy(self):
        if self._autohide_pending is not None:
            try:
                self.after_cancel(self._autohide_pending)
            except Exception:
                pass
        super().destroy()

    def bind(self, sequence=None, func=None, add=None):
        # customtkinter updates the scroll region from this frame's own
        # <Configure> binding; a plain bind() would silently replace it and
        # the page could no longer be scrolled. Always add instead.
        if sequence == "<Configure>" and func is not None:
            add = "+"
        return super().bind(sequence, func, add)

    def _linux_wheel(self, event) -> None:
        try:
            if not self.winfo_exists() or not self.winfo_ismapped():
                return
            if not self._check_if_valid_scroll(event.widget):
                return
        except Exception:  # destroyed widget, or the pointer is over a non-Tk window
            return
        step = -1 if event.delta > 0 else 1
        canvas = self._parent_canvas
        if self._shift_pressed:
            if canvas.xview() != (0.0, 1.0):
                canvas.xview_scroll(step, "units")
        elif canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(step, "units")


def bind_tree(widget, sequence: str, func, skip: tuple = ()) -> None:
    """Bind ``func`` on ``widget`` and all its descendants (except ``skip``)."""
    if widget in skip:
        return
    widget.bind(sequence, func, add="+")
    for child in widget.winfo_children():
        bind_tree(child, sequence, func, skip)


def set_cursor_tree(widget, cursor: str, skip: tuple = ()) -> None:
    if widget in skip:
        return
    try:
        widget.configure(cursor=cursor)
    except Exception:
        pass
    for child in widget.winfo_children():
        set_cursor_tree(child, cursor, skip)


def pointer_inside(widget) -> bool:
    if not widget.winfo_exists():
        return False
    x, y = widget.winfo_pointerxy()
    return (widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width()
            and widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height())


class Modal(ctk.CTkToplevel):
    """A dialog window that stays on top of the main window and blocks it."""

    def __init__(self, master, title: str, width: int, height: int):
        super().__init__(master)
        self.title(title)
        self.configure(fg_color=theme.BG)
        self.transient(master.winfo_toplevel())
        # centre over the parent, but never taller than the screen
        master.update_idletasks()
        parent = master.winfo_toplevel()
        height = min(height, self.winfo_screenheight() - 80)
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{max(0, y)}")
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

    def header(self, title: str, subtitle: str = "", row: int = 0) -> ctk.CTkFrame:
        """A standard dialog title block (serif title, muted subtitle)."""
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=row, column=0, sticky="ew", padx=32, pady=(26, 10))
        theme.heading(head, title, size=26).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                         justify="left").pack(anchor="w", pady=(2, 0))
        return head

    def close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        parent = self.master
        self.destroy()
        if isinstance(parent, Modal):  # give the grab back to the dialog we were opened from
            try:
                parent.after(50, parent._grab)
            except Exception:
                pass


class Toast(ctk.CTkFrame):
    """A short message with one action, floating at the bottom of the window (e.g. "Annuler")."""

    def __init__(self, master, message: str, action_text: str, on_action: Callable[[], None],
                 on_expire: Callable[[], None], seconds: int = 10):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER_STRONG,
                         corner_radius=theme.RADIUS)
        self._on_expire = on_expire
        self._done = False
        ctk.CTkLabel(self, text=message, font=theme.font(14), text_color=theme.TEXT, anchor="w",
                     justify="left", wraplength=480).pack(side="left", padx=(18, 8), pady=12)
        theme.link_button(self, text=action_text, command=lambda: self._finish(on_action)).pack(
            side="left", padx=(0, 10), pady=8)
        self.place(relx=0.5, rely=1.0, anchor="s", y=-24)
        self.lift()
        self._timer = self.after(seconds * 1000, lambda: self._finish(on_expire))

    def _finish(self, callback: Callable[[], None]) -> None:
        if self._done:
            return
        self._done = True
        try:
            self.after_cancel(self._timer)
        except Exception:
            pass
        self.destroy()
        callback()

    def dismiss(self) -> None:
        """Close the toast now, running its expiry callback."""
        self._finish(self._on_expire)


class ReportButton(ctk.CTkButton):
    """"Signaler un problème", floating in the bottom-right corner like a website's help button.

    Shrinks to a round icon when the content area is narrow, so it never hides half the page.
    """

    LABEL = "Signaler un problème"
    SIZE = 52
    MARGIN = 20
    RIGHT = 34  # clear of the page's scrollbar
    COMPACT_BELOW = 620  # content width, in unscaled pixels

    def __init__(self, master, command: Callable[[], None], content_left: int = 0):
        super().__init__(master, text=self.LABEL, image=art.icon("message", theme.SIDEBAR_ACCENT, 20),
                         compound="left", height=self.SIZE, corner_radius=self.SIZE // 2, border_spacing=10,
                         fg_color=theme.SIDEBAR, hover_color=theme.SIDEBAR_HOVER, text_color=theme.SIDEBAR_TEXT,
                         border_width=2, border_color=theme.SIDEBAR_ACCENT, bg_color=theme.BG,
                         font=theme.font(14, "bold"), command=command)
        self._content_left = content_left
        self._compact: bool | None = None
        self._shown = False
        self._under = None
        master.bind("<Configure>", self._on_resize, add="+")
        self._bg_job = self.after(400, self._match_background)

    def destroy(self) -> None:
        try:
            self.after_cancel(self._bg_job)
        except Exception:
            pass
        super().destroy()

    def set_content_left(self, left: int) -> None:
        self._content_left = left

    def _match_background(self) -> None:
        """Tk cannot draw a see-through corner: paint the round button's corners in the colour of
        what is behind it (page or card), checked a few times a second as the page scrolls."""
        if not self.winfo_exists():
            return
        try:
            if self._shown and self.winfo_ismapped():
                widget = self.winfo_containing(self.winfo_rootx() - 3, self.winfo_rooty() + self.winfo_height() // 2)
                color = None
                while widget is not None and color is None:
                    try:
                        fg = widget.cget("fg_color")
                    except Exception:
                        fg = None
                    if fg and fg != "transparent":
                        color = fg
                    widget = getattr(widget, "master", None)
                if color is not None and color != self._under:
                    self._under = color
                    self.configure(bg_color=color)
        except Exception:
            log.debug("report button background", exc_info=True)
        self._bg_job = self.after(300, self._match_background)

    def show(self) -> None:
        self._shown = True
        self.place(relx=1.0, rely=1.0, anchor="se", x=-self.RIGHT, y=-self.MARGIN)
        self.lift()

    def hide(self) -> None:
        self._shown = False
        self.place_forget()

    def _on_resize(self, event) -> None:
        if event.widget is not self.master:  # a root binding also fires for every child
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        self.set_compact(event.width / scaling - self._content_left < self.COMPACT_BELOW)

    def _create_grid(self) -> None:
        super()._create_grid()
        if getattr(self, "_compact", False):  # customtkinter reserves corner_radius on both sides: a circle needs 0
            self.grid_columnconfigure(0, minsize=0)
            self.grid_columnconfigure(4, minsize=0)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.configure(text="", compound="center", width=self.SIZE, border_spacing=0)
        else:
            self.configure(text=self.LABEL, compound="left", width=0, border_spacing=10)
        if self._shown:
            self.lift()


class UpdateBanner(ctk.CTkFrame):
    """A discreet "new version" notice at the bottom of the window.

    It shows a message with buttons (``set_buttons``), or a progress bar with a cancel link
    (``show_progress`` / ``set_progress``), or a plain status line (``show_status``), in place.
    """

    def __init__(self, master, message: str, on_download: Callable[[], None], on_later: Callable[[], None],
                 download_text: str = "Télécharger", primary: bool = False, close_on_download: bool = True):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER_STRONG,
                         corner_radius=theme.RADIUS)
        self.label = ctk.CTkLabel(self, text=message, font=theme.font(14), text_color=theme.TEXT, anchor="w",
                                  justify="left", wraplength=420)
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_bar: ctk.CTkProgressBar | None = None
        self.cancel_button: ctk.CTkButton | None = None
        self.download_button, self.later_button = self.set_buttons(message, [
            (download_text, on_download, "primary" if primary else "link", close_on_download),
            ("Plus tard", on_later, "link", True),
        ])
        self.place(relx=0.5, rely=1.0, anchor="s", y=-24)
        self.lift()

    def _clear(self, layout: str = "row") -> None:
        """``layout``: "row" (message then actions), "stacked" (actions under a long message), "text"."""
        for child in self.actions.winfo_children():
            child.destroy()
        self.progress_bar = None
        self.cancel_button = None
        self.label.pack_forget()
        self.actions.pack_forget()  # an empty frame would fall back to its default 200 px height
        if layout == "stacked":
            self.label.pack(side="top", anchor="w", padx=18, pady=(12, 2))
            self.actions.pack(side="top", anchor="w", padx=(10, 10), pady=(0, 8))
        else:
            self.label.pack(side="left", padx=(18, 8), pady=12)
            if layout == "row":
                self.actions.pack(side="left", padx=(0, 10), pady=8)

    def set_buttons(self, message: str, buttons, stacked: bool = False) -> list[ctk.CTkButton]:
        """``buttons``: (text, callback, "primary" | "link", closes_the_banner) tuples."""
        self._clear("stacked" if stacked else "row")
        self.label.configure(text=message)
        made = []
        for text, callback, style, closes in buttons:
            if style == "primary":
                btn = theme.primary_button(self.actions, text=text, width=0, height=34)
            else:
                btn = theme.link_button(self.actions, text=text)
            btn.configure(command=lambda c=callback, x=closes: self._run(c, x))
            btn.pack(side="left", padx=(0, 4))
            made.append(btn)
        return made

    def show_progress(self, message: str, fraction: float, on_cancel: Callable[[], None]) -> None:
        self._clear()
        self.label.configure(text=message)
        self.progress_bar = ctk.CTkProgressBar(self.actions, width=160, height=8, corner_radius=4,
                                               progress_color=theme.ACCENT, fg_color=theme.SURFACE_ALT)
        self.progress_bar.set(fraction)
        self.progress_bar.pack(side="left", padx=(0, 10))
        self.cancel_button = theme.link_button(self.actions, text="Annuler", command=on_cancel)
        self.cancel_button.pack(side="left")

    def set_progress(self, message: str, fraction: float) -> None:
        self.label.configure(text=message)
        if self.progress_bar is not None:
            self.progress_bar.set(fraction)

    def show_status(self, message: str) -> None:
        self._clear("text")
        self.label.configure(text=message)

    def _run(self, callback: Callable[[], None], close: bool = True) -> None:
        if close:
            self.destroy()
        callback()


class IconLine(ctk.CTkFrame):
    """An icon with a text that wraps beside it (a label with ``compound="left"`` wraps under the icon)."""

    def __init__(self, master, font, size: int = 15, wraplength: int = 200, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.size = size
        self.grid_columnconfigure(1, weight=1)
        self.icon = ctk.CTkLabel(self, text="", width=size + 4)
        self.icon.grid(row=0, column=0, sticky="n", padx=(0, 6), pady=(2, 0))
        self.text = ctk.CTkLabel(self, text="", font=font, anchor="w", justify="left", wraplength=wraplength)
        self.text.grid(row=0, column=1, sticky="ew")

    def set(self, icon: str, text: str, color) -> None:
        self.icon.configure(image=art.icon(icon, color, self.size))
        self.text.configure(text=text, text_color=color)

    def set_wrap(self, wraplength: int) -> None:
        self.text.configure(wraplength=wraplength)


class Choice(ctk.CTkFrame):
    """A row of rounded pill buttons where exactly one is selected.

    Same ``get`` / ``set`` API as CTkSegmentedButton, which renders poorly.
    """

    def __init__(self, master, values: list[str], command: Callable[[str], None] | None = None,
                 height: int = 38, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.command = command
        self.value: str | None = None
        self.buttons: dict[str, ctk.CTkButton] = {}
        for i, value in enumerate(values):
            btn = ctk.CTkButton(self, text=value, height=height, width=0, corner_radius=height // 2,
                                border_width=1, border_spacing=10, font=theme.font(14, "bold"),
                                command=lambda v=value: self._clicked(v))
            btn.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0))
            self.buttons[value] = btn
        self._per_row = len(values)
        self._paint()

    def reflow(self, width: int) -> None:
        """Wrap the pills onto several lines to fit ``width`` (unscaled px)."""
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        limit = width * scaling
        gap = 8 * scaling
        used, per_row = 0.0, 0
        for btn in self.buttons.values():
            need = btn.winfo_reqwidth() + (gap if per_row else 0)
            if per_row and used + need > limit:
                break
            used += need
            per_row += 1
        per_row = max(1, per_row)
        if per_row == self._per_row:
            return
        self._per_row = per_row
        for i, btn in enumerate(self.buttons.values()):
            row, col = divmod(i, per_row)
            btn.grid_configure(row=row, column=col, sticky="w", padx=(0 if col == 0 else 8, 0),
                               pady=(0 if row == 0 else 8, 0))

    def _clicked(self, value: str) -> None:
        changed = value != self.value
        self.set(value)
        if changed and self.command:
            self.command(value)

    def get(self) -> str:
        return self.value or ""

    def set(self, value: str) -> None:
        if value in self.buttons:
            self.value = value
            self._paint()

    def _paint(self) -> None:
        for value, btn in self.buttons.items():
            if value == self.value:
                btn.configure(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT,
                              border_color=theme.ACCENT)
            else:
                btn.configure(fg_color=theme.SURFACE, hover_color=theme.SURFACE_ALT, text_color=theme.TEXT,
                              border_color=theme.BORDER_STRONG)


class FlowFrame(ctk.CTkFrame):
    """Children laid out left to right, wrapping onto new lines to fit the width."""

    def __init__(self, master, gap: int = 6, **kwargs):
        super().__init__(master, fg_color="transparent", height=1, **kwargs)
        self.gap = gap
        self.items: list = []
        self._strips: list = []
        self._key = None
        self.bind("<Configure>", lambda _e: self.reflow(), add="+")

    def set_items(self, widgets: list) -> None:
        old_items = self.items
        for old in old_items:
            old.grid_forget()
            old.pack_forget()
        # destroy the replaced widgets (a button may be calling us from its own command: wait a moment)
        self.after(200, lambda: [w.destroy() for w in old_items if w.winfo_exists()])
        self.items = list(widgets)
        self._key = None
        self.reflow()

    def chips(self) -> list:
        """The children that are items (not the per-line strips)."""
        return [w for w in self.winfo_children() if w not in self._strips]

    def reflow(self) -> None:
        if not self.items:
            for strip in self._strips:
                strip.destroy()
            self._strips = []
            self._key = None
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        limit = self.winfo_width()
        if limit <= 1:  # not laid out yet: one line, the <Configure> event fixes it
            limit = 10 ** 6
        gap = self.gap * scaling
        rows: list[list] = [[]]
        used = 0.0
        for item in self.items:
            need = item.winfo_reqwidth() + (gap if rows[-1] else 0)
            if rows[-1] and used + need > limit:
                rows.append([])
                used, need = 0.0, item.winfo_reqwidth()
            rows[-1].append(item)
            used += need
        key = tuple(len(r) for r in rows)
        if key == self._key:
            return
        self._key = key
        # one strip per line, so a long chip on one line does not widen a "column" on the others
        for item in self.items:
            item.grid_forget()
            item.pack_forget()
        for strip in self._strips:
            strip.destroy()
        self._strips = []
        for r, row in enumerate(rows):
            strip = ctk.CTkFrame(self, fg_color="transparent", height=1)
            strip.grid(row=r, column=0, sticky="w", pady=(0 if r == 0 else self.gap, 0))
            self._strips.append(strip)
            for c, item in enumerate(row):
                item.pack(in_=strip, side="left", padx=(0 if c == 0 else self.gap, 0))
                item.lift()  # the strip was created after the item: keep the item on top of it


def ingredient_preview(recipe: Recipe, max_names: int = 6) -> str:
    """The first ingredient names, enough to tell two cakes apart."""
    names = [i.nom.strip() for i in recipe.ingredients if i.nom.strip()]
    text = ", ".join(names[:max_names])
    return text + "…" if len(names) > max_names else text


def clip_lines(text: str, width: int, font_size: int, lines: int = 2) -> str:
    """Cut ``text`` to about ``lines`` lines of ``width`` px (Tk labels cannot clamp lines themselves)."""
    per_line = max(10, int(width / (font_size * 0.56)))
    limit = per_line * lines
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;: ") + "…"


class RecipeCard(ctk.CTkFrame):
    """One recipe in the list: an index card with a dish-coloured ribbon, the name and
    what goes in it. The first photo sits on the right when there is one."""

    MIN_WIDTH = 300
    THUMB = (104, 96)
    RADIUS = 16

    def __init__(self, master, recipe: Recipe, photo: Path | None, width: int,
                 on_open: Callable[[Recipe], None], on_favorite: Callable[[Recipe], None], shared_by: str = ""):
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=self.RADIUS, border_width=1,
                         border_color=theme.BORDER, width=width)
        self.recipe = recipe
        self.photo = photo
        self.card_width = 0
        self._on_open = on_open
        self.grid_columnconfigure(1, weight=1)
        pal = theme.dish(recipe.type_plat)

        # the ribbon: the dish type at a glance, like a coloured tab in a recipe box
        ctk.CTkFrame(self, width=6, height=10, fg_color=pal["to"], corner_radius=3).grid(
            row=0, column=0, rowspan=3, sticky="ns", padx=(10, 0), pady=14)

        # name and ingredients beside the photo; the facts below both, so they never widen the card
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=1, sticky="nsew", padx=(12, 6), pady=(12, 0))
        body.grid_columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(body, text=recipe.nom, font=theme.serif(18), text_color=theme.TEXT,
                                  justify="left", anchor="w")
        self.title.grid(row=0, column=0, sticky="ew")
        self.star = ctk.CTkButton(
            body, text="", width=36, height=36, corner_radius=18, fg_color="transparent",
            hover_color=theme.SURFACE_ALT,
            image=art.icon("star" if recipe.favori else "star_outline",
                           theme.STAR if recipe.favori else theme.FAINT, 20),
            command=lambda: on_favorite(recipe),
        )
        self.star.grid(row=0, column=1, sticky="ne")
        self._preview = ingredient_preview(recipe)
        self.ings = ctk.CTkLabel(body, text="", font=theme.font(13, slant="roman" if self._preview else "italic"),
                                 text_color=theme.MUTED, justify="left", anchor="w")
        self.ings.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(12, 12), pady=(8, 12 if not shared_by else 0))
        ctk.CTkLabel(meta, text=f"  {recipe.type_plat}", image=art.dot(pal["dot"], 10), compound="left",
                     font=theme.font(13, "bold"), text_color=theme.TEXT, height=20).pack(side="left", padx=(0, 14))
        if recipe.temps_total:
            self._meta(meta, "clock", format_minutes(recipe.temps_total))
        self._meta(meta, "users", servings_text(recipe.pour, recipe.pour_unite, short=True))
        self.shared = None
        if shared_by:
            self.shared = ctk.CTkLabel(self, text=f" Partagée par {shared_by}", image=art.icon("share", theme.ACCENT_TEXT, 14),
                                       compound="left", font=theme.font(12, "bold"), text_color=theme.ACCENT_TEXT,
                                       anchor="w", justify="left")
            self.shared.grid(row=2, column=1, columnspan=2, sticky="ew", padx=12, pady=(6, 12))

        self.thumb = None
        if photo is not None:
            img = art.photo_thumb(photo, self.THUMB, radius=12, bg=theme.SURFACE)
            if img is not None:
                self.thumb = ctk.CTkLabel(self, text="", image=img)
                self.thumb.grid(row=0, column=2, sticky="ne", padx=(0, 10), pady=(10, 0))

        self.set_width(width)
        skip = (self.star,)
        for seq, func in (("<Button-1>", self._clicked), ("<Enter>", self._hover_on), ("<Leave>", self._hover_off)):
            bind_tree(self, seq, func, skip)
        set_cursor_tree(self, "hand2", skip)

    @staticmethod
    def _meta(parent, icon: str, text: str) -> None:
        ctk.CTkLabel(parent, text=f" {text}", image=art.icon(icon, theme.MUTED, 15), compound="left",
                     font=theme.font(13), text_color=theme.MUTED, height=20).pack(side="left", padx=(0, 14))

    def set_width(self, width: int) -> None:
        width = max(self.MIN_WIDTH, int(width))
        if width == self.card_width:
            return
        self.card_width = width
        self.configure(width=width)
        text_w = width - 40 - (self.THUMB[0] + 14 if self.thumb is not None else 0)
        self.title.configure(wraplength=text_w - 44)
        self.ings.configure(text=clip_lines(self._preview, text_w, 13) if self._preview else "Pas encore d'ingrédients",
                            wraplength=text_w)
        if self.shared is not None:
            self.shared.configure(wraplength=text_w)

    def _clicked(self, _event=None) -> None:
        self._on_open(self.recipe)

    def _hover_on(self, _event=None) -> None:
        self.configure(border_color=theme.BORDER_STRONG)

    def _hover_off(self, _event=None) -> None:
        # Leaving the frame for one of its own labels is not leaving the card.
        self.after(20, lambda: self.winfo_exists() and not pointer_inside(self)
                   and self.configure(border_color=theme.BORDER))


class NavItem(ctk.CTkFrame):
    """A sidebar entry: optional icon or colour dot, label, and a count on the right."""

    HEIGHT = 42
    COMPACT_HEIGHT = 34  # short windows (still a 44 px target at the "Très grande" text size)

    def __init__(self, master, text: str, command: Callable[[], None], icon: str | None = None,
                 dot: str | None = None, height: int = HEIGHT):
        super().__init__(master, fg_color="transparent", corner_radius=10, height=height)
        self.command = command
        self.active = False
        self.grid_columnconfigure(1, weight=1)
        self.grid_propagate(False)
        self.icon_name = icon
        if icon:
            self.icon = ctk.CTkLabel(self, text="", width=22, image=art.icon(icon, theme.SIDEBAR_MUTED, 18))
        else:
            self.icon = ctk.CTkLabel(self, text="", width=22, image=art.dot(dot or "#999999", 10))
        self.icon.grid(row=0, column=0, padx=(14, 10), pady=10 if height >= 40 else 6)
        self.label = ctk.CTkLabel(self, text=text, font=theme.font(14), text_color=theme.SIDEBAR_TEXT, anchor="w")
        self.label.grid(row=0, column=1, sticky="w")
        self.count = ctk.CTkLabel(self, text="", font=theme.font(12, "bold"), text_color=theme.SIDEBAR_MUTED,
                                  width=28, anchor="e")
        self.count.grid(row=0, column=2, padx=(4, 14))

        bind_tree(self, "<Button-1>", lambda _e: self.command())
        bind_tree(self, "<Enter>", lambda _e: self._paint(hover=True))
        bind_tree(self, "<Leave>", lambda _e: self.after(20, lambda: self._paint(hover=pointer_inside(self))))
        set_cursor_tree(self, "hand2")

    def set_height(self, height: int) -> None:
        self.configure(height=height)
        self.icon.grid_configure(pady=10 if height >= 40 else 6)

    def set_count(self, n: int | None) -> None:
        self.count.configure(text="" if n is None else str(n))

    def set_active(self, active: bool) -> None:
        self.active = active
        self._paint(hover=False)

    def _paint(self, hover: bool) -> None:
        if not self.winfo_exists():
            return
        if self.active:
            bg = theme.SIDEBAR_ACTIVE
        elif hover:
            bg = theme.SIDEBAR_HOVER
        else:
            bg = "transparent"
        self.configure(fg_color=bg)
        self.label.configure(font=theme.font(14, "bold" if self.active else "normal"),
                             text_color=theme.SIDEBAR_ACCENT if self.active else theme.SIDEBAR_TEXT)
        if self.icon_name:
            color = theme.SIDEBAR_ACCENT if self.active else theme.SIDEBAR_MUTED
            self.icon.configure(image=art.icon(self.icon_name, color, 18))
