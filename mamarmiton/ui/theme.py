"""Colours and fonts. Every colour is a (light, dark) pair understood by customtkinter."""

from __future__ import annotations

import sys
from functools import lru_cache

import customtkinter as ctk

BG = ("#FBF6F1", "#1E1A19")
SURFACE = ("#FFFFFF", "#2A2422")
SURFACE_ALT = ("#FFF1E6", "#332B28")
SIDEBAR = ("#FFE8D6", "#262020")
BORDER = ("#EBDDD2", "#3D3431")

ACCENT = ("#B85C6E", "#D88C9A")
ACCENT_HOVER = ("#9C4A5B", "#C27482")
ACCENT_SOFT = ("#F7DEE3", "#4A3036")
ON_ACCENT = ("#FFFFFF", "#1E1A19")

TEXT = ("#3B2F2F", "#F3EAE6")
MUTED = ("#7D6B66", "#B5A6A1")
DANGER = ("#B3261E", "#F2B8B5")
DANGER_HOVER = ("#8C1D18", "#E8A29E")
STAR = ("#D99A00", "#F5C542")
SUCCESS = ("#2E7D4F", "#7FD1A0")

DISH_COLORS = {
    "Entrée": ("#DDEFD9", "#2F4430"),
    "Plat": ("#FCE3C8", "#4A3825"),
    "Dessert": ("#F7DEE3", "#4A3036"),
    "Pain": ("#EFE3D0", "#463B2C"),
}

FAMILY = "Segoe UI" if sys.platform.startswith("win") else None


@lru_cache(maxsize=None)
def font(size: int = 14, weight: str = "normal", slant: str = "roman") -> ctk.CTkFont:
    if FAMILY:
        return ctk.CTkFont(family=FAMILY, size=size, weight=weight, slant=slant)
    return ctk.CTkFont(size=size, weight=weight, slant=slant)


def primary_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                corner_radius=10, height=38, font=font(14, "bold"))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def secondary_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color="transparent", hover_color=ACCENT_SOFT, text_color=ACCENT,
                border_width=1, border_color=ACCENT, corner_radius=10, height=38, font=font(14))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def ghost_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT,
                corner_radius=10, height=34, font=font(14))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def danger_button(master, **kwargs) -> ctk.CTkButton:
    opts = dict(fg_color="transparent", hover_color=ACCENT_SOFT, text_color=DANGER,
                border_width=1, border_color=DANGER, corner_radius=10, height=38, font=font(14))
    opts.update(kwargs)
    return ctk.CTkButton(master, **opts)


def option_menu(master, values: list[str], **kwargs) -> ctk.CTkOptionMenu:
    opts = dict(values=values, fg_color=SURFACE, button_color=ACCENT_SOFT, button_hover_color=ACCENT_SOFT,
                text_color=TEXT, dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
                dropdown_hover_color=ACCENT_SOFT, corner_radius=8, height=34, font=font(13),
                dropdown_font=font(13), dynamic_resizing=False)
    opts.update(kwargs)
    return ctk.CTkOptionMenu(master, **opts)


def entry(master, **kwargs) -> ctk.CTkEntry:
    opts = dict(fg_color=SURFACE, border_color=BORDER, text_color=TEXT, corner_radius=8,
                height=36, font=font(14), placeholder_text_color=MUTED)
    opts.update(kwargs)
    return ctk.CTkEntry(master, **opts)


def textbox(master, **kwargs) -> ctk.CTkTextbox:
    opts = dict(fg_color=SURFACE, border_color=BORDER, border_width=2, text_color=TEXT,
                corner_radius=8, font=font(14), wrap="word")
    opts.update(kwargs)
    return ctk.CTkTextbox(master, **opts)


def chip(master, text: str, fg=None, **kwargs) -> ctk.CTkLabel:
    opts = dict(text=text, fg_color=fg or SURFACE_ALT, text_color=TEXT, corner_radius=12,
                font=font(12, "bold"), padx=10, height=26)
    opts.update(kwargs)
    return ctk.CTkLabel(master, **opts)


def card(master, **kwargs) -> ctk.CTkFrame:
    opts = dict(fg_color=SURFACE, corner_radius=16, border_width=1, border_color=BORDER)
    opts.update(kwargs)
    return ctk.CTkFrame(master, **opts)
