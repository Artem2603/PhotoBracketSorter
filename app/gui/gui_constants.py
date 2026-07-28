"""Small shared constants used across main_window.py and its split-out view
modules (feature 10, 1.2.0) — kept in their own module so neither side needs
to import the other just to reach a plain dict."""

from __future__ import annotations

LANGUAGE_LABELS = {"ru": "Русский", "en": "English"}
LANGUAGE_CODES = {value: key for key, value in LANGUAGE_LABELS.items()}

#: Settings-dialog tabs hidden in simple mode. General and Output stay
#: visible in both modes — everything else is advanced-only.
ADVANCED_ONLY_SETTINGS_TABS = {
    "settings.analysis",
    "settings.bracketing",
    "settings.classification",
    "settings.pairing",
    "settings.performance",
    "settings.reports",
    "settings.advanced",
}
