"""Design tokens: colours, fonts and widget factories.

Every colour is a ``(light, dark)`` pair understood by customtkinter, so the
whole app follows the Clair / Sombre setting live.

The look is a warm cookbook: cream "paper", a dark cocoa sidebar,
terracotta accents and a serif display face for titles.
"""

from __future__ import annotations

import sys
from functools import lru_cache

import customtkinter as ctk

# --------------------------------------------------------------- surfaces
BG = ("#E9DFD2", "#16110E")            # the page
SURFACE = ("#F5EFE7", "#211A16")       # cards
SURFACE_ALT = ("#E2D5C5", "#2B221D")   # inset areas inside cards
SURFACE_HOVER = ("#EDE4D8", "#28201B")
BORDER = ("#D3C2AE", "#34291F")
BORDER_STRONG = ("#B9A28A", "#4A3B30")

# ---------------------------------------------------------------- sidebar
SIDEBAR = ("#2B201C", "#0F0B09")
SIDEBAR_TEXT = ("#F5ECE3", "#EDE3D9")
SIDEBAR_MUTED = ("#A8968A", "#8C7D72")
SIDEBAR_HOVER = ("#3A2C26", "#1C1512")
SIDEBAR_ACTIVE = ("#4A3329", "#2A1D17")
SIDEBAR_LINE = ("#43342D", "#251C17")
SIDEBAR_ACCENT = ("#F7B79C", "#F2A283")

# ----------------------------------------------------------------- accent
ACCENT = ("#C8553D", "#E57F5E")        # terracotta
ACCENT_HOVER = ("#AD4430", "#F0936F")
ACCENT_SOFT = ("#EFCDBD", "#3D2820")
ACCENT_TEXT = ("#9A3B26", "#F2A283")   # accent used as text on light bg
ON_ACCENT = ("#FFFFFF", "#1A120E")

# ------------------------------------------------------------------- text
TEXT = ("#1F1612", "#F3EAE1")
MUTED = ("#5E4D42", "#A8998C")
FAINT = ("#85725F", "#6E6056")

DANGER = ("#B3261E", "#F2B8B5")
DANGER_HOVER = ("#F0D0CC", "#4A2522")
STAR = ("#C98A06", "#F2C14E")
NOTE_BG = ("#F0E1BC", "#2E2617")
NOTE_BAR = ("#E0B24A", "#C9A04A")

# Each dish type gets its own colour family: (light tint, deep, chip bg pair)
DISH_PALETTE = {
    "Entrée": {"from": "#C9D9B8", "to": "#7FA273", "dot": "#7FA273", "chip": ("#D3E2C6", "#26331F")},
    "Plat": {"from": "#F1C29F", "to": "#C8553D", "dot": "#D9704F", "chip": ("#F0CDB9", "#3D2820")},
    "Accompagnement": {"from": "#C2D8D6", "to": "#4F8A8B", "dot": "#5E9C9C", "chip": ("#CDE1DF", "#1E3132")},
    "Dessert": {"from": "#F0C6CF", "to": "#B05C74", "dot": "#C4708A", "chip": ("#EDCBD4", "#3A2229")},
    "Pain": {"from": "#EED9AE", "to": "#C29350", "dot": "#C9A25E", "chip": ("#EAD8B3", "#382C1A")},
}
DEFAULT_DISH_PALETTE = {"from": "#E4D5C3", "to": "#A88E76", "dot": "#A88E76", "chip": ("#EFE5D8", "#2E251F")}


def dish(name: str) -> dict:
    return DISH_PALETTE.get(name, DEFAULT_DISH_PALETTE)


# ------------------------------------------------------------------ fonts
# Bundled (SIL Open Font License, see assets/fonts):
#   Atkinson Hyperlegible: body text, drawn for low-vision readers
#   DM Serif Display: titles, a warm cookbook serif
BODY_FAMILY, TITLE_FAMILY = "Atkinson Hyperlegible", "DM Serif Display"
FONT_FILES = [
    "AtkinsonHyperlegible-Regular.ttf", "AtkinsonHyperlegible-Bold.ttf",
    "AtkinsonHyperlegible-Italic.ttf", "AtkinsonHyperlegible-BoldItalic.ttf",
    "DMSerifDisplay-Regular.ttf", "DMSerifDisplay-Italic.ttf",
]
if sys.platform.startswith("win"):
    SANS, SERIF = "Segoe UI", "Georgia"
elif sys.platform == "darwin":
    SANS, SERIF = "Helvetica Neue", "Georgia"
else:
    SANS, SERIF = "Noto Sans", "Noto Serif"


def font_path(name: str):
    from mamarmiton import config

    return config.resource_path(f"assets/fonts/{name}")


def register_fonts() -> None:
    """Make the bundled fonts available to Tk. Call before creating the window."""
    for name in FONT_FILES:
        path = font_path(name)
        if path.exists():
            try:
                ctk.FontManager.load_font(str(path))
            except Exception:
                pass


def resolve_fonts(root) -> None:
    """Use the bundled families if Tk can see them, else keep the system ones."""
    global SANS, SERIF
    from tkinter import font as tkfont

    families = set(tkfont.families(root))
    if BODY_FAMILY in families:
        SANS = BODY_FAMILY
    if TITLE_FAMILY in families:
        SERIF = TITLE_FAMILY
    font.cache_clear()
    serif.cache_clear()


@lru_cache(maxsize=None)
def font(size: int = 14, weight: str = "normal", slant: str = "roman", overstrike: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=SANS, size=size, weight=weight, slant=slant, overstrike=overstrike)


@lru_cache(maxsize=None)
def serif(size: int = 24, weight: str | None = None, slant: str = "roman") -> ctk.CTkFont:
    # DM Serif Display has a single (already heavy) weight; fake bold would smear it
    if weight is None:
        weight = "normal" if SERIF == TITLE_FAMILY else "bold"
    return ctk.CTkFont(family=SERIF, size=size, weight=weight, slant=slant)


# --------------------------------------------------------------- factories
RADIUS = 12
PILL = 999  # customtkinter clamps this to "fully round"


def primary_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                corner_radius=RADIUS, height=42, font=font(14, "bold"), border_spacing=6)
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def secondary_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color=SURFACE, hover_color=SURFACE_ALT, text_color=TEXT,
                border_width=1, border_color=BORDER_STRONG, corner_radius=RADIUS, height=42,
                font=font(14, "bold"), border_spacing=6)
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def ghost_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color="transparent", hover_color=SURFACE_ALT, text_color=TEXT,
                corner_radius=RADIUS, height=38, font=font(14, "bold"))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def danger_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color="transparent", hover_color=DANGER_HOVER, text_color=DANGER,
                border_width=1, border_color=BORDER_STRONG, corner_radius=RADIUS, height=42,
                font=font(14, "bold"))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def link_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color="transparent", hover_color=ACCENT_SOFT, text_color=ACCENT_TEXT,
                corner_radius=8, height=30, width=0, font=font(13, "bold"))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def option_menu(master, values: list[str], **kwargs) -> ctk.CTkOptionMenu:
    opts = dict(values=values, fg_color=SURFACE_ALT, button_color=SURFACE_ALT, button_hover_color=BORDER,
                text_color=TEXT, dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
                dropdown_hover_color=ACCENT_SOFT, corner_radius=RADIUS, height=40, font=font(14),
                dropdown_font=font(14), dynamic_resizing=False)
    opts.update(kwargs)
    return ctk.CTkOptionMenu(master, **opts)


def pill_menu(master, values: list[str], **kwargs) -> ctk.CTkOptionMenu:
    """A compact rounded dropdown used in the filter bar."""
    opts = dict(corner_radius=20, height=36, width=170, font=font(13, "bold"), fg_color=SURFACE,
                button_color=SURFACE, button_hover_color=SURFACE_ALT)
    opts.update(kwargs)
    return option_menu(master, values, **opts)


def combo(master, values: list[str], **kwargs) -> ctk.CTkComboBox:
    """A dropdown you can also type into (units, …)."""
    opts = dict(values=values, fg_color=SURFACE, border_color=BORDER_STRONG, border_width=1,
                button_color=SURFACE_ALT, button_hover_color=BORDER, text_color=TEXT,
                dropdown_fg_color=SURFACE, dropdown_text_color=TEXT, dropdown_hover_color=ACCENT_SOFT,
                corner_radius=RADIUS, height=42, font=font(14), dropdown_font=font(14))
    opts.update(kwargs)
    return ctk.CTkComboBox(master, **opts)


def switch(master, **kwargs) -> ctk.CTkSwitch:
    opts = dict(fg_color=BORDER_STRONG, progress_color=ACCENT, button_color=("#FFFFFF", "#F3EAE1"),
                button_hover_color=("#FFFFFF", "#FFFFFF"), switch_width=46, switch_height=24,
                font=font(14, "bold"), text_color=TEXT)
    opts.update(kwargs)
    return ctk.CTkSwitch(master, **opts)


def entry(master, **kwargs) -> ctk.CTkEntry:
    opts = dict(fg_color=SURFACE, border_color=BORDER_STRONG, border_width=1, text_color=TEXT,
                corner_radius=RADIUS, height=42, font=font(14), placeholder_text_color=FAINT)
    opts.update(kwargs)
    return ctk.CTkEntry(master, **opts)


def textbox(master, **kwargs) -> ctk.CTkTextbox:
    opts = dict(fg_color=SURFACE, border_color=BORDER_STRONG, border_width=1, text_color=TEXT,
                corner_radius=RADIUS, font=font(14), wrap="word", border_spacing=8)
    opts.update(kwargs)
    return ctk.CTkTextbox(master, **opts)


def chip(master, text: str, fg=None, text_color=None, **kwargs) -> ctk.CTkLabel:
    opts = dict(text=text, fg_color=fg or SURFACE_ALT, text_color=text_color or TEXT, corner_radius=14,
                font=font(12, "bold"), padx=12, height=28)
    opts.update(kwargs)
    return ctk.CTkLabel(master, **opts)


def card(master, **kwargs) -> ctk.CTkFrame:
    opts = dict(fg_color=SURFACE, corner_radius=18, border_width=1, border_color=BORDER)
    opts.update(kwargs)
    return ctk.CTkFrame(master, **opts)


def eyebrow(master, text: str, **kwargs) -> ctk.CTkLabel:
    """Small label above a field or a group (sentence case: easier to read than capitals)."""
    opts = dict(text=text, font=font(13, "bold"), text_color=MUTED, anchor="w")
    opts.update(kwargs)
    return ctk.CTkLabel(master, **opts)


def heading(master, text: str, size: int = 22, **kwargs) -> ctk.CTkLabel:
    opts = dict(text=text, font=serif(size), text_color=TEXT, anchor="w", justify="left")
    opts.update(kwargs)
    return ctk.CTkLabel(master, **opts)


def divider(master, **kwargs) -> ctk.CTkFrame:
    opts = dict(height=1, fg_color=BORDER, corner_radius=0)
    opts.update(kwargs)
    return ctk.CTkFrame(master, **opts)
