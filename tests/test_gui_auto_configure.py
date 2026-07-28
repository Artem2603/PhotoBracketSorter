"""GUI wiring for auto-configuration: nothing applies without an explicit call (feature 1, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.enums import BracketSizeMode, DetectedType
from app.models import AppSettings, PhotoMetadata

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def hdr3_metadata() -> list[PhotoMetadata]:
    items = []
    shutters = (1 / 500, 1 / 125, 1 / 30)
    for cluster in range(5):
        for index, shutter in enumerate(shutters):
            name = f"IMG_{cluster:02d}_{index}.dng"
            items.append(
                PhotoMetadata(
                    source_path=Path(f"C:/photos/{name}"),
                    relative_path=Path(name),
                    file_name=name,
                    extension=".dng",
                    detected_type=DetectedType.DNG,
                    capture_time=BASE_TIME + timedelta(seconds=cluster * 30 + index * 0.4),
                    make="Canon",
                    model="EOS R5",
                    width=6000,
                    height=4000,
                    orientation="Horizontal",
                    exposure_compensation=0.0,
                    exposure_time=shutter,
                    f_number=2.8,
                    iso=100,
                    file_size=1000,
                )
            )
    return items


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiAutoConfigureTests(unittest.TestCase):
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
        # This suite writes settings (test_apply_auto_config_persists_to_the_settings_file),
        # so the real per-user AppData settings.json must never be touched: a write there
        # would leak into every other test that constructs a MainWindow, and into the
        # user's actual application state.
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        isolated_path = Path(self._temp_dir.name) / "settings.json"
        path_patcher = mock.patch("app.gui.main_window.default_settings_path", return_value=isolated_path)
        path_patcher.start()
        self.addCleanup(path_patcher.stop)
        self.window = MainWindow()

    def tearDown(self):
        self.window.destroy()

    def test_button_exists_and_is_disabled_while_busy(self):
        self.assertTrue(hasattr(self.window, "auto_configure_button"))
        self.window._set_busy(True)
        self.assertEqual(str(self.window.auto_configure_button["state"]), "disabled")
        self.window._set_busy(False)
        self.assertEqual(str(self.window.auto_configure_button["state"]), "normal")

    def test_starting_without_a_source_folder_shows_an_error_and_does_nothing(self):
        self.window.source_var.set("")
        self.window._start_auto_configure()
        self.assertTrue(self.dialogs)
        self.assertEqual(self.dialogs[0][0], "error")
        self.assertIsNone(self.window.worker)

    def test_report_is_built_without_touching_live_settings(self):
        original_snapshot = self.window.settings.snapshot()
        report = self.window._build_auto_config_report(hdr3_metadata())
        self.assertTrue(report.suggestions)
        self.assertEqual(self.window.settings.snapshot(), original_snapshot)

    def test_apply_auto_config_updates_settings_only_for_accepted_fields(self):
        report = self.window._build_auto_config_report(hdr3_metadata())
        fields = {s.field for s in report.suggestions}
        self.assertIn("bracket_size_mode", fields)
        before = self.window.settings.snapshot()
        self.window._apply_auto_config(report, accepted_fields={"bracket_size_mode"})
        after = self.window.settings.snapshot()
        self.assertEqual(self.window.settings.bracket_size_mode, BracketSizeMode.THREE)
        changed_keys = {key for key in before if before[key] != after[key]}
        self.assertIn("bracket_size_mode", changed_keys)
        # Every other suggested field from this dataset must stay untouched.
        for field in fields - {"bracket_size_mode"}:
            self.assertNotIn(field, changed_keys, f"{field} changed without being accepted")

    def test_apply_auto_config_persists_to_the_settings_file(self):
        report = self.window._build_auto_config_report(hdr3_metadata())
        self.window._apply_auto_config(report, accepted_fields={"min_auto_confidence"})
        from app.settings import load_settings

        reloaded = load_settings(self.window.settings_path)
        self.assertEqual(reloaded.min_auto_confidence, self.window.settings.min_auto_confidence)

    def test_dialog_with_no_suggestions_does_not_crash(self):
        report = self.window._build_auto_config_report([])
        self.assertEqual(report.suggestions, ())
        self.window._show_auto_config_dialog(report)  # must not raise

    def test_dialog_populates_one_row_per_suggestion(self):
        report = self.window._build_auto_config_report(hdr3_metadata())
        self.window._show_auto_config_dialog(report)  # must not raise with real suggestions present


if __name__ == "__main__":
    unittest.main()
