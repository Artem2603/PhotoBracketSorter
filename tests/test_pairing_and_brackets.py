from datetime import datetime, timedelta
from pathlib import Path
import unittest

from app.bracket_detection import BracketDetector
from app.classification import Classifier
from app.enums import DestinationCategory, DetectedType
from app.models import AppSettings, PhotoFile, PhotoMetadata
from app.pairing import PairMatcher


def make_file(name: str, detected_type: DetectedType, seconds: float, exposure: float | None = None) -> PhotoFile:
    capture = datetime(2026, 7, 1, 12, 0, 0) + timedelta(seconds=seconds)
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
        capture_time=capture,
        exposure_compensation=exposure,
        iso=100,
        exposure_time=0.01,
        f_number=1.8,
    )
    return PhotoFile(metadata=metadata)


class PairingAndBracketTests(unittest.TestCase):
    def test_pair_matcher_accepts_mutual_best_match(self):
        raw = make_file("IMG_0001.dng", DetectedType.DNG, 0)
        rendered = make_file("IMG_0001.heic", DetectedType.HEIC, 0.2)
        pairs = PairMatcher().match([raw], [rendered], AppSettings())
        self.assertEqual(len(pairs), 1)
        self.assertEqual(raw.pair_id, "PAIR_0001")
        self.assertEqual(rendered.pair_id, "PAIR_0001")

    def test_bracket_detector_accepts_three_exposure_levels(self):
        files = [
            make_file("IMG_0001.dng", DetectedType.DNG, 0.0, -1.0),
            make_file("IMG_0002.dng", DetectedType.DNG, 0.4, 0.0),
            make_file("IMG_0003.dng", DetectedType.DNG, 0.8, 1.0),
        ]
        candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertTrue(candidates)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 3)

    def test_bracket_detector_accepts_five_exposure_levels(self):
        files = [
            make_file("IMG_1001.dng", DetectedType.DNG, 0.0, -2.0),
            make_file("IMG_1002.dng", DetectedType.DNG, 0.3, -1.0),
            make_file("IMG_1003.dng", DetectedType.DNG, 0.6, 0.0),
            make_file("IMG_1004.dng", DetectedType.DNG, 0.9, 1.0),
            make_file("IMG_1005.dng", DetectedType.DNG, 1.2, 2.0),
        ]
        candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertTrue(candidates)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 5)

    def test_same_exposure_burst_is_not_bracket(self):
        files = [
            make_file("BURST_0001.dng", DetectedType.DNG, 0.0, 0.0),
            make_file("BURST_0002.dng", DetectedType.DNG, 0.2, 0.0),
            make_file("BURST_0003.dng", DetectedType.DNG, 0.4, 0.0),
            make_file("BURST_0004.dng", DetectedType.DNG, 0.6, 0.0),
            make_file("BURST_0005.dng", DetectedType.DNG, 0.8, 0.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(series, [])

    def test_exposure_order_zero_minus_plus_is_accepted(self):
        files = [
            make_file("IMG_2001.dng", DetectedType.DNG, 0.0, 0.0),
            make_file("IMG_2002.dng", DetectedType.DNG, 0.3, -1.0),
            make_file("IMG_2003.dng", DetectedType.DNG, 0.6, 1.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(len(series), 1)
        self.assertEqual(sorted(series[0].exposure_values), [-1.0, 0.0, 1.0])

    def test_two_nearby_three_frame_series_are_independent(self):
        files = [
            make_file("A_0001.dng", DetectedType.DNG, 0.0, -1.0),
            make_file("A_0002.dng", DetectedType.DNG, 0.3, 0.0),
            make_file("A_0003.dng", DetectedType.DNG, 0.6, 1.0),
            make_file("B_0001.dng", DetectedType.DNG, 2.2, -1.0),
            make_file("B_0002.dng", DetectedType.DNG, 2.5, 0.0),
            make_file("B_0003.dng", DetectedType.DNG, 2.8, 1.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(len(series), 2)
        self.assertEqual([item.series_number for item in series], [1, 2])

    def test_series_three_single_raw_and_series_five(self):
        files = [
            make_file("S3_0001.dng", DetectedType.DNG, 0.0, -1.0),
            make_file("S3_0002.dng", DetectedType.DNG, 0.3, 0.0),
            make_file("S3_0003.dng", DetectedType.DNG, 0.6, 1.0),
            make_file("SINGLE.dng", DetectedType.DNG, 5.0, 0.0),
            make_file("S5_0001.dng", DetectedType.DNG, 10.0, -2.0),
            make_file("S5_0002.dng", DetectedType.DNG, 10.3, -1.0),
            make_file("S5_0003.dng", DetectedType.DNG, 10.6, 0.0),
            make_file("S5_0004.dng", DetectedType.DNG, 10.9, 1.0),
            make_file("S5_0005.dng", DetectedType.DNG, 11.2, 2.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual([item.series_size for item in series], [3, 5])
        self.assertIsNone(files[3].series_id)

    def test_bracketing_three_and_five_categories_are_independent(self):
        files = [
            make_file("S3_0001.dng", DetectedType.DNG, 0.0, -1.0),
            make_file("S3_0002.dng", DetectedType.DNG, 0.3, 0.0),
            make_file("S3_0003.dng", DetectedType.DNG, 0.6, 1.0),
            make_file("S5_0001.dng", DetectedType.DNG, 10.0, -2.0),
            make_file("S5_0002.dng", DetectedType.DNG, 10.3, -1.0),
            make_file("S5_0003.dng", DetectedType.DNG, 10.6, 0.0),
            make_file("S5_0004.dng", DetectedType.DNG, 10.9, 1.0),
            make_file("S5_0005.dng", DetectedType.DNG, 11.2, 2.0),
        ]
        classified, _pairs, _candidates, series, stats = Classifier().classify([file.metadata for file in files], AppSettings())
        self.assertEqual(stats["series_3"], 1)
        self.assertEqual(stats["series_5"], 1)
        self.assertEqual(classified[0].destination_category, DestinationCategory.BRACKETING_3)
        self.assertEqual(classified[3].destination_category, DestinationCategory.BRACKETING_5)
        self.assertEqual(series[0].series_number, 1)
        self.assertEqual(series[1].series_number, 1)


if __name__ == "__main__":
    unittest.main()
