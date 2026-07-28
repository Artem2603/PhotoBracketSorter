from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from app.bracket_detection import BracketDetector
from app.classification import Classifier
from app.enums import DetectedType
from app.models import AppSettings, PhotoFile, PhotoMetadata
from app.pairing import PairMatcher
from app.time_compare import MIXED_TIMEZONE_ASSUMED_OFFSET, safe_datetime_difference_seconds


def make_timed_file(
    name: str,
    detected_type: DetectedType,
    capture_time: datetime | None,
    exposure: float | None = None,
) -> PhotoFile:
    metadata = PhotoMetadata(
        source_path=Path("C:/source") / name,
        relative_path=Path(name),
        file_name=name,
        extension=Path(name).suffix.lower(),
        detected_type=detected_type,
        file_size=1024,
        width=4000,
        height=3000,
        make="Xiaomi",
        model="Xiaomi 14",
        lens_model="main",
        capture_time=capture_time,
        capture_time_raw=str(capture_time) if capture_time else None,
        iso=100,
        exposure_time=0.01,
        f_number=1.8,
        exposure_compensation=exposure,
    )
    return PhotoFile(metadata=metadata)


class TimeCompareTests(unittest.TestCase):
    def test_aware_datetime_against_naive_datetime_does_not_crash(self):
        aware = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
        naive = datetime(2026, 7, 1, 12, 0, 1)
        result = safe_datetime_difference_seconds(aware, naive, left_name="raw.dng", right_name="rendered.heic")
        self.assertAlmostEqual(result.absolute_seconds, 1.0)
        self.assertEqual(result.warning_code, MIXED_TIMEZONE_ASSUMED_OFFSET)

    def test_naive_datetime_against_aware_datetime_does_not_crash(self):
        naive = datetime(2026, 7, 1, 12, 0)
        aware = datetime(2026, 7, 1, 12, 0, 2, tzinfo=timezone(timedelta(hours=3)))
        result = safe_datetime_difference_seconds(naive, aware, left_name="raw.dng", right_name="rendered.heic")
        self.assertAlmostEqual(result.absolute_seconds, 2.0)
        self.assertEqual(result.warning_code, MIXED_TIMEZONE_ASSUMED_OFFSET)

    def test_two_naive_datetimes(self):
        left = datetime(2026, 7, 1, 12, 0)
        right = datetime(2026, 7, 1, 12, 0, 3)
        result = safe_datetime_difference_seconds(left, right)
        self.assertAlmostEqual(result.absolute_seconds, 3.0)
        self.assertIsNone(result.warning_code)

    def test_two_aware_datetimes_with_same_offset(self):
        offset = timezone(timedelta(hours=3))
        left = datetime(2026, 7, 1, 12, 0, tzinfo=offset)
        right = datetime(2026, 7, 1, 12, 0, 4, tzinfo=offset)
        result = safe_datetime_difference_seconds(left, right)
        self.assertAlmostEqual(result.absolute_seconds, 4.0)
        self.assertIsNone(result.warning_code)

    def test_two_aware_datetimes_with_different_offsets(self):
        left = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
        right = datetime(2026, 7, 1, 10, 0, 5, tzinfo=timezone(timedelta(hours=1)))
        result = safe_datetime_difference_seconds(left, right)
        self.assertAlmostEqual(result.absolute_seconds, 5.0)
        self.assertEqual(result.method, "both_aware_utc")

    def test_series_crossing_midnight(self):
        files = [
            make_timed_file("MID_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 23, 59, 59, 800000), -1.0),
            make_timed_file("MID_0002.dng", DetectedType.DNG, datetime(2026, 7, 2, 0, 0, 0, 100000), 0.0),
            make_timed_file("MID_0003.dng", DetectedType.DNG, datetime(2026, 7, 2, 0, 0, 0, 400000), 1.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(len(series), 1)
        self.assertAlmostEqual(series[0].duration_seconds, 0.6)

    def test_raw_with_timezone_and_heic_without_timezone_pairing(self):
        raw = make_timed_file("IMG_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3))))
        heic = make_timed_file("IMG_0001.heic", DetectedType.HEIC, datetime(2026, 7, 1, 12, 0, 0, 200000))
        pairs = PairMatcher().match([raw], [heic], AppSettings())
        self.assertEqual(len(pairs), 1)
        self.assertIn(MIXED_TIMEZONE_ASSUMED_OFFSET, pairs[0].warnings)

    def test_heic_with_timezone_and_raw_without_timezone_pairing(self):
        raw = make_timed_file("IMG_0002.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, 100000))
        heic = make_timed_file("IMG_0002.heic", DetectedType.HEIC, datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3))))
        pairs = PairMatcher().match([raw], [heic], AppSettings())
        self.assertEqual(len(pairs), 1)
        self.assertIn(MIXED_TIMEZONE_ASSUMED_OFFSET, pairs[0].warnings)

    def test_mixed_timezone_warning_is_recorded_on_bracket(self):
        files = [
            make_timed_file("MIX_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3))), -1.0),
            make_timed_file("MIX_0002.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, 300000), 0.0),
            make_timed_file("MIX_0003.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, 600000), 1.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(len(series), 1)
        self.assertIn(MIXED_TIMEZONE_ASSUMED_OFFSET, series[0].warnings)

    def test_processing_continues_after_problematic_time(self):
        files = [
            make_timed_file("BAD_TIME.dng", DetectedType.DNG, None, 0.0),
            make_timed_file("OK_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0), -1.0),
            make_timed_file("OK_0002.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, 300000), 0.0),
            make_timed_file("OK_0003.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, 600000), 1.0),
        ]
        classified, _pairs, _candidates, series, stats = Classifier().classify([file.metadata for file in files], AppSettings())
        self.assertEqual(len(classified), 4)
        self.assertEqual(len(series), 1)
        self.assertEqual(stats["series_3"], 1)


if __name__ == "__main__":
    unittest.main()
