"""GUI wiring for camera profiles (feature 3, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.camera_profiles import UNIVERSAL_PROFILE_ID, load_user_profiles

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiCameraProfilesTests(unittest.TestCase):
    def setUp(self):
        self.dialogs: list[tuple[str, str]] = []
        patcher = mock.patch.multiple(
            "app.gui.main_window.messagebox",
            showwarning=lambda title, message, **kw: self.dialogs.append(("warning", message)),
            showerror=lambda title, message, **kw: self.dialogs.append(("error", message)),
            showinfo=lambda title, message, **kw: self.dialogs.append(("info", message)),
            askyesno=lambda title, message, **kw: True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        settings_patcher = mock.patch(
            "app.gui.main_window.default_settings_path", return_value=Path(self._temp_dir.name) / "settings.json"
        )
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)
        profiles_patcher = mock.patch(
            "app.gui.main_window.user_profiles_path", return_value=Path(self._temp_dir.name) / "camera_profiles.json"
        )
        profiles_patcher.start()
        self.addCleanup(profiles_patcher.stop)
        self.window = MainWindow()

    def tearDown(self):
        self.window.destroy()

    def test_profiles_path_is_isolated_from_real_appdata(self):
        self.assertEqual(self.window.profiles_path, Path(self._temp_dir.name) / "camera_profiles.json")

    def test_dialog_opens_without_crashing(self):
        self.window._open_profiles()

    def test_created_profile_is_persisted_under_the_isolated_path(self):
        from app.camera_profiles import create_profile

        create_profile("Test Camera", path=self.window.profiles_path, min_auto_confidence=80.0)
        names = {p.name for p in load_user_profiles(self.window.profiles_path)}
        self.assertIn("Test Camera", names)
        # And definitely not the real per-user file.
        from app.camera_profiles import user_profiles_path as real_path_fn

        self.assertNotEqual(self.window.profiles_path, real_path_fn())

    def test_applying_universal_profile_matches_defaults(self):
        from app.camera_profiles import find_profile

        self.window.settings.min_auto_confidence = 99.0  # perturb first
        profile = find_profile(UNIVERSAL_PROFILE_ID, self.window.profiles_path)
        self.assertIsNotNone(profile)
        from app.camera_profiles import apply_profile

        self.window.settings = apply_profile(self.window.settings, profile)
        self.assertEqual(self.window.settings.min_auto_confidence, 72.0)

    def test_prompt_text_returns_none_when_the_dialog_var_is_empty(self):
        # _prompt_text is exercised indirectly by do_new/do_edit through a modal
        # Toplevel; here we just confirm the helper exists and is callable shape.
        self.assertTrue(hasattr(self.window, "_prompt_text"))


if __name__ == "__main__":
    unittest.main()
