"""Simple/Advanced UI mode toggle (feature 10, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.gui.main_window import ADVANCED_ONLY_ACTIONS

try:
    import tkinter as tk
    from tkinter import ttk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiUiModeTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        for name, target in (
            ("app.gui.main_window.default_settings_path", "settings.json"),
            ("app.gui.main_window.user_profiles_path", "camera_profiles.json"),
            ("app.gui.main_window.journal_path", "operations_journal.json"),
        ):
            p = mock.patch(name, return_value=Path(self._temp_dir.name) / target)
            p.start()
            self.addCleanup(p.stop)
        self.window = MainWindow()

    def tearDown(self):
        self.window.destroy()

    @staticmethod
    def _is_gridded(widget) -> bool:
        # winfo_ismapped() depends on the window actually being drawn by a
        # window manager, which a headless test run never triggers; grid_info()
        # reflects grid()/grid_remove() directly and is reliable off-screen.
        return bool(widget.grid_info())

    def test_starts_in_advanced_mode_by_default(self):
        self.assertEqual(self.window.settings.ui_mode, "advanced")
        for name in ADVANCED_ONLY_ACTIONS:
            self.assertTrue(self._is_gridded(getattr(self.window, name)))

    def test_switching_to_simple_mode_hides_advanced_actions(self):
        self.window.ui_mode_var.set(self.window._ui_mode_label("simple"))
        self.window._change_ui_mode()

        self.assertEqual(self.window.settings.ui_mode, "simple")
        for name in ADVANCED_ONLY_ACTIONS:
            self.assertFalse(self._is_gridded(getattr(self.window, name)), f"{name} should be hidden in simple mode")
        # The 5-step flow itself must stay visible.
        for name in ("analyze_button", "copy_button", "move_button", "settings_button", "reset_button"):
            self.assertTrue(self._is_gridded(getattr(self.window, name)))

    def test_switching_back_to_advanced_mode_restores_the_buttons(self):
        self.window.ui_mode_var.set(self.window._ui_mode_label("simple"))
        self.window._change_ui_mode()
        self.window.ui_mode_var.set(self.window._ui_mode_label("advanced"))
        self.window._change_ui_mode()

        for name in ADVANCED_ONLY_ACTIONS:
            self.assertTrue(self._is_gridded(getattr(self.window, name)))

    def test_changing_mode_persists_to_settings_json(self):
        self.window.ui_mode_var.set(self.window._ui_mode_label("simple"))
        self.window._change_ui_mode()

        from app.settings import load_settings

        reloaded = load_settings(self.window.settings_path)
        self.assertEqual(reloaded.ui_mode, "simple")

    def test_settings_dialog_hides_advanced_tabs_in_simple_mode(self):
        self.window.settings.ui_mode = "simple"
        self.window._open_settings()
        dialog = self.window.children[list(self.window.children)[-1]]
        notebook = next(child for child in dialog.winfo_children() if isinstance(child, ttk.Notebook))
        tab_texts = [notebook.tab(tab_id, "text") for tab_id in notebook.tabs()]
        self.assertIn(self.window.i18n.gettext("settings.general"), tab_texts)
        self.assertIn(self.window.i18n.gettext("settings.output"), tab_texts)
        self.assertNotIn(self.window.i18n.gettext("settings.advanced"), tab_texts)
        self.assertNotIn(self.window.i18n.gettext("settings.classification"), tab_texts)
        dialog.destroy()

    def test_settings_dialog_shows_all_tabs_in_advanced_mode(self):
        self.window.settings.ui_mode = "advanced"
        self.window._open_settings()
        dialog = self.window.children[list(self.window.children)[-1]]
        notebook = next(child for child in dialog.winfo_children() if isinstance(child, ttk.Notebook))
        self.assertEqual(len(notebook.tabs()), 9)
        dialog.destroy()

    def test_simple_mode_still_tracks_fields_on_hidden_tabs(self):
        """A hidden tab must not drop its fields from being managed — Save
        still round-trips the full settings snapshot, unchanged."""
        self.window.settings.ui_mode = "simple"
        captured: dict[str, object] = {}
        original_entry = self.window._settings_entry

        def spy_entry(parent, variables, name):
            original_entry(parent, variables, name)
            captured[name] = variables[name]

        with mock.patch.object(self.window, "_settings_entry", side_effect=spy_entry):
            self.window._open_settings()
        dialog = self.window.children[list(self.window.children)[-1]]
        # worker_count lives on the "Performance" tab, hidden in simple mode.
        self.assertIn("worker_count", captured)
        dialog.destroy()

    def test_language_change_keeps_the_ui_mode_selection_and_relabels_it(self):
        self.window.ui_mode_var.set(self.window._ui_mode_label("simple"))
        self.window._change_ui_mode()
        self.window.language_var.set("English")
        self.window._change_language()
        self.assertEqual(self.window.settings.ui_mode, "simple")
        self.assertEqual(self.window.ui_mode_var.get(), "Simple")


if __name__ == "__main__":
    unittest.main()
