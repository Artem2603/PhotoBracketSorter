"""GUI wiring for extended classification (feature 8, 1.2.0): the "group" table
column and the "Классификация" settings tab."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.enums import DestinationCategory, DetectedType
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_file(index: int, group_classification: str = "UNCLASSIFIED") -> PhotoFile:
    name = f"IMG_{index:04d}.dng"
    file = PhotoFile(
        metadata=PhotoMetadata(
            source_path=Path(f"C:/photos/{name}"),
            relative_path=Path(name),
            file_name=name,
            extension=".dng",
            detected_type=DetectedType.DNG,
            capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
            file_size=1000,
        ),
        destination_category=DestinationCategory.RAW,
        destination_subpath=Path("RAW") / name,
    )
    file.group_classification = group_classification
    return file


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiExtendedClassificationTests(unittest.TestCase):
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

    def _load(self, files: list[PhotoFile]) -> None:
        self.window.analysis = AnalysisResult(
            source_root=Path("C:/photos"),
            files=files,
            pairs=[],
            bracket_candidates=[],
            bracket_series=[],
            statistics={},
            warnings=[],
            created_at=BASE_TIME,
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="fp",
        )
        self.window._populate_table()

    def test_group_column_exists_in_the_main_table(self):
        self.assertIn("group", self.window.tree["columns"])

    def test_group_column_shows_the_localized_label_for_each_file(self):
        files = [make_file(0, "FOCUS_BRACKET"), make_file(1, "BURST")]
        self._load(files)
        row0 = self.window.tree.item("0", "values")
        row1 = self.window.tree.item("1", "values")
        columns = self.window.tree["columns"]
        group_index = columns.index("group")
        self.assertEqual(row0[group_index], self.window.i18n.gettext("group.FOCUS_BRACKET"))
        self.assertEqual(row1[group_index], self.window.i18n.gettext("group.BURST"))
        self.assertNotEqual(row0[group_index], row1[group_index])

    def test_sorting_by_group_uses_the_raw_classification_value(self):
        files = [make_file(0, "UNKNOWN_GROUP"), make_file(1, "BURST")]
        self._load(files)
        self.window.sort_column = "group"
        self.assertEqual(self.window._sort_key(files[0]), "UNKNOWN_GROUP")
        self.assertEqual(self.window._sort_key(files[1]), "BURST")

    def test_settings_dialog_opens_without_error(self):
        self.window._open_settings()  # must not raise

    def test_classification_tab_exposes_all_feature_8_toggles_and_thresholds(self):
        captured: dict[str, object] = {}
        original_check = self.window._settings_check
        original_entry = self.window._settings_entry

        def spy_check(parent, variables, name):
            original_check(parent, variables, name)
            captured[name] = variables[name]

        def spy_entry(parent, variables, name):
            original_entry(parent, variables, name)
            captured[name] = variables[name]

        with mock.patch.object(self.window, "_settings_check", side_effect=spy_check), mock.patch.object(
            self.window, "_settings_entry", side_effect=spy_entry
        ):
            self.window._open_settings()

        expected_fields = (
            "enable_hdr7",
            "max_bracket_duration_seconds_7",
            "enable_hdr9",
            "max_bracket_duration_seconds_9",
            "enable_incomplete_bracket_detection",
            "enable_focus_bracket_detection",
            "focus_bracket_exposure_tolerance_ev",
            "focus_bracket_min_focus_spread_ratio",
            "focus_bracket_visual_similarity_min",
            "enable_burst_detection",
            "burst_visual_similarity_threshold",
            "enable_panorama_detection",
            "panorama_min_frames",
            "panorama_visual_similarity_min",
            "panorama_visual_similarity_max",
        )
        for field in expected_fields:
            self.assertIn(field, captured, f"{field} was not wired into the settings dialog")

    def test_toggling_a_classification_checkbox_and_saving_updates_real_settings(self):
        self.assertTrue(self.window.settings.enable_focus_bracket_detection)
        variables: dict[str, tk.Variable] = {}
        dialog = tk.Toplevel(self.window)
        self.window._settings_check(dialog, variables, "enable_focus_bracket_detection")
        variables["enable_focus_bracket_detection"].set(False)

        self.window._apply_settings_dialog(variables, dialog)

        self.assertFalse(self.window.settings.enable_focus_bracket_detection)

    def test_settings_path_is_isolated_from_real_appdata(self):
        self.assertEqual(self.window.settings_path, Path(self._temp_dir.name) / "settings.json")


if __name__ == "__main__":
    unittest.main()
