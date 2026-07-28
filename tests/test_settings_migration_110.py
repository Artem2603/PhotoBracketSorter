from pathlib import Path
import tempfile
import unittest

from app.enums import BracketSizeMode, DetectionMode
from app.errors import SettingsError
from app.models import AppSettings
from app.settings import CURRENT_SETTINGS_SCHEMA, load_settings, save_settings, settings_from_dict, validate_settings


class SettingsMigration110Tests(unittest.TestCase):
    def test_old_101_settings_migrate_to_schema_2(self):
        settings = settings_from_dict(
            {
                "schema_version": 1,
                "detection_mode": "SOFT",
                "bracket_size_mode": "THREE",
                "worker_count": 2,
                "max_bracket_duration_seconds": 5.5,
            }
        )
        self.assertEqual(settings.schema_version, CURRENT_SETTINGS_SCHEMA)
        self.assertEqual(settings.detection_mode, DetectionMode.SOFT)
        self.assertEqual(settings.bracket_size_mode, BracketSizeMode.THREE)
        self.assertEqual(settings.worker_count, 2)
        self.assertEqual(settings.theme, "system")
        self.assertTrue(settings.mixed_timezone_safe_mode)

    def test_list_settings_convert_to_tuples(self):
        settings = settings_from_dict(
            {
                "allowed_extensions": [".jpg", ".dng"],
                "excluded_folder_names": ["Sorted_Photos_*", ".cache"],
                "report_formats": ["json", "txt"],
                "column_order": ["file", "category"],
            }
        )
        self.assertEqual(settings.allowed_extensions, (".jpg", ".dng"))
        self.assertEqual(settings.report_formats, ("json", "txt"))
        self.assertEqual(settings.column_order, ("file", "category"))

    def test_column_widths_are_normalized(self):
        settings = settings_from_dict({"column_widths": {"file": "320", "time": 180}})
        self.assertEqual(settings.column_widths["file"], 320)
        self.assertEqual(settings.column_widths["time"], 180)

    def test_save_load_roundtrip_new_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = AppSettings(language="en", theme="dark", accent="orange", last_source_path="C:/Фото")
            save_settings(settings, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.language, "en")
            self.assertEqual(loaded.theme, "dark")
            self.assertEqual(loaded.accent, "orange")
            self.assertEqual(loaded.last_source_path, "C:/Фото")

    def test_invalid_language_is_rejected(self):
        with self.assertRaises(SettingsError):
            validate_settings(AppSettings(language="de"))

    def test_invalid_theme_is_rejected(self):
        with self.assertRaises(SettingsError):
            validate_settings(AppSettings(theme="neon"))

    def test_invalid_scale_is_rejected(self):
        with self.assertRaises(SettingsError):
            validate_settings(AppSettings(ui_scale=2.0))

    def test_empty_extension_list_is_rejected(self):
        with self.assertRaises(SettingsError):
            validate_settings(AppSettings(allowed_extensions=()))

    def test_extension_without_dot_is_rejected(self):
        with self.assertRaises(SettingsError):
            validate_settings(AppSettings(allowed_extensions=("jpg",)))

    def test_future_schema_is_rejected(self):
        with self.assertRaises(SettingsError):
            settings_from_dict({"schema_version": 99})

    def test_snapshot_contains_110_fields(self):
        snapshot = AppSettings().snapshot()
        self.assertEqual(snapshot["schema_version"], CURRENT_SETTINGS_SCHEMA)
        self.assertIn("thumbnail_size", snapshot)
        self.assertIn("require_plan_before_operation", snapshot)
        self.assertIn("mixed_timezone_safe_mode", snapshot)


if __name__ == "__main__":
    unittest.main()
