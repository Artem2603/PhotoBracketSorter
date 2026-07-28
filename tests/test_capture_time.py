from datetime import datetime, timezone
import unittest

from app.capture_time import parse_datetime_from_name, parse_datetime_value
from app.enums import TimezoneStatus


class CaptureTimeTests(unittest.TestCase):
    def test_exif_datetime_with_offset(self):
        parsed, status = parse_datetime_value("2026:07:01 12:34:56.123+03:00")
        self.assertEqual(status, TimezoneStatus.OFFSET_PRESENT)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.microsecond, 123000)

    def test_exif_datetime_utc_mark(self):
        parsed, status = parse_datetime_value("2026-07-01T12:34:56Z")
        self.assertEqual(status, TimezoneStatus.UTC_MARKED)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_filename_time(self):
        parsed = parse_datetime_from_name("IMG_20260701_213045_123.dng")
        self.assertEqual(parsed, datetime(2026, 7, 1, 21, 30, 45, 123000))

    def test_variable_fraction_digits(self):
        parsed, status = parse_datetime_value("2026:07:01 12:34:56.7")
        self.assertEqual(status, TimezoneStatus.LOCAL_UNKNOWN)
        self.assertEqual(parsed.microsecond, 700000)


if __name__ == "__main__":
    unittest.main()
