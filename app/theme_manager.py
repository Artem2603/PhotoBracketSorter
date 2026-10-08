"""Theme helpers with ttkbootstrap fallback."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

from app.models import AppSettings

try:
    import ttkbootstrap as tb
except Exception:
    tb = None

BOOTSTRAP_AVAILABLE = tb is not None

LIGHT_THEME = "flatly"
DARK_THEME = "darkly"

THEME_MAP = {
    "system": LIGHT_THEME,
    "light": LIGHT_THEME,
    "dark": DARK_THEME,
}

ACCENT_BOOTSTYLE = {
    "blue": "primary",
    "green": "success",
    "purple": "secondary",
    "orange": "warning",
}


def system_prefers_dark() -> bool:
    """Whether the OS asks applications for a dark appearance.

    Windows exposes this as ``AppsUseLightTheme`` in the user's registry hive
    (0 means dark). Anything that cannot be read, including every non-Windows
    platform, counts as light so the previous behaviour is preserved.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return False


def theme_name(settings: AppSettings) -> str:
    if settings.theme == "system":
        return DARK_THEME if system_prefers_dark() else LIGHT_THEME
    return THEME_MAP.get(settings.theme, LIGHT_THEME)


def is_dark_theme(settings: AppSettings) -> bool:
    return theme_name(settings) == DARK_THEME


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


def _style_is_stale(style) -> bool:
    """A ttkbootstrap Style outlives the Tk root it was built on.

    ``tb.Window`` resets the singleton in its own ``destroy``; a plain ``tk.Tk``
    subclass (our MainWindow) does not, so a second window in the same process
    (tests, or a future restart-in-place) would keep talking to a dead
    interpreter and fail with "application has been destroyed".
    """
    master = getattr(style, "master", None)
    if master is None:
        return True
    try:
        return not bool(master.winfo_exists())
    except Exception:
        return True


def release_style() -> None:
    """Forget the ttkbootstrap Style singleton; call when its root is destroyed."""
    if BOOTSTRAP_AVAILABLE:
        tb.Style.instance = None


def apply_theme(window: tk.Misc, settings: AppSettings) -> None:
    if BOOTSTRAP_AVAILABLE:
        try:
            style = tb.Style.instance
            if style is not None and _style_is_stale(style):
                release_style()
                style = None
            if style is None:
                style = tb.Style(theme=theme_name(settings))
            else:
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
    """Bootstyle name for a button role, or None when plain ttk is in use.

    Roles: ``primary`` (the accent colour), ``copy`` (success), ``move`` and
    ``warning`` (warning), ``danger``, ``secondary`` and ``outline`` (quiet
    utility buttons).
    """
    if not BOOTSTRAP_AVAILABLE:
        return None
    if role == "copy":
        return "success.TButton"
    if role in {"move", "warning"}:
        return "warning.TButton"
    if role == "danger":
        return "danger.Outline.TButton"
    if role == "secondary":
        return "secondary.TButton"
    if role == "outline":
        return f"{accent_to_bootstyle(settings.accent)}.Outline.TButton"
    return f"{accent_to_bootstyle(settings.accent)}.TButton"
