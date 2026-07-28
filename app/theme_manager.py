"""Theme helpers with ttkbootstrap fallback."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.models import AppSettings

try:
    import ttkbootstrap as tb
except Exception:
    tb = None

BOOTSTRAP_AVAILABLE = tb is not None

THEME_MAP = {
    "system": "flatly",
    "light": "flatly",
    "dark": "darkly",
}

ACCENT_BOOTSTYLE = {
    "blue": "primary",
    "green": "success",
    "purple": "secondary",
    "orange": "warning",
}


def theme_name(settings: AppSettings) -> str:
    return THEME_MAP.get(settings.theme, "flatly")


def accent_to_bootstyle(accent: str) -> str:
    return ACCENT_BOOTSTYLE.get(accent, "primary")


def create_window(title: str, settings: AppSettings | None = None) -> tk.Tk:
    settings = settings or AppSettings()
    if BOOTSTRAP_AVAILABLE:
        return tb.Window(themename=theme_name(settings), title=title)
    window = tk.Tk()
    window.title(title)
    apply_theme(window, settings)
    return window


def apply_theme(window: tk.Misc, settings: AppSettings) -> None:
    if BOOTSTRAP_AVAILABLE:
        try:
            style = tb.Style.instance or tb.Style()
            style.theme_use(theme_name(settings))
        except Exception:
            return
    else:
        style = ttk.Style(window)
        for candidate in ("vista", "clam", "alt", "default"):
            if candidate in style.theme_names():
                style.theme_use(candidate)
                break


def button_style(settings: AppSettings, role: str = "primary") -> str | None:
    if not BOOTSTRAP_AVAILABLE:
        return None
    if role == "move":
        return "warning.TButton"
    if role == "danger":
        return "danger.TButton"
    return f"{accent_to_bootstyle(settings.accent)}.TButton"
