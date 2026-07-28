import unittest

from app.models import AppSettings
from app.settings import settings_from_dict, validate_settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        validate_settings(AppSettings())

    def test_dict_migration_ignores_unknown_keys(self):
        settings = settings_from_dict({"detection_mode": "SOFT", "unknown": "value", "worker_count": 2})
        self.assertEqual(settings.detection_mode.value, "SOFT")
        self.assertEqual(settings.worker_count, 2)


if __name__ == "__main__":
    unittest.main()
