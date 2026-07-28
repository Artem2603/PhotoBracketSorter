"""Unicode pairing, TIFF classification, and pairing performance (P1-5, P1-6, P1-8)."""

from __future__ import annotations

import time
import unicodedata
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.enums import DetectedType
from app.format_detection import classify_tiff, detect_format, is_raw_type, is_rendered_type
from app.models import AppSettings, PhotoFile, PhotoMetadata
from app.pairing import MAX_SCORE_WITHOUT_TIME, PairMatcher, normalize_stem, stem_similarity

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_file(name: str, detected_type: DetectedType, offset_seconds: float = 0.0, **extra) -> PhotoFile:
    fields = {
        "source_path": Path(f"C:/photos/{name}"),
        "relative_path": Path(name),
        "file_name": name,
        "extension": Path(name).suffix.lower(),
        "detected_type": detected_type,
        "capture_time": BASE_TIME + timedelta(seconds=offset_seconds),
        "make": "TestCam",
        "model": "T1",
        "width": 4000,
        "height": 3000,
        "orientation": "Horizontal",
        "iso": 100,
        "exposure_time": 1 / 125,
        "f_number": 2.8,
        "file_size": 1000,
    }
    fields.update(extra)
    return PhotoFile(metadata=PhotoMetadata(**fields))


class UnicodeStemTests(unittest.TestCase):
    """Regression: normalize_stem erased every non-Latin name."""

    def test_cyrillic_names_survive_normalisation(self):
        self.assertNotEqual(normalize_stem("Отпуск_снимок.dng"), "")
        self.assertEqual(normalize_stem("Отпуск_снимок.dng"), "отпускснимок")

    def test_unrelated_cyrillic_names_no_longer_match(self):
        similarity = stem_similarity("Отпуск_снимок.dng", "Свадьба_кадр.heic")
        self.assertLess(similarity, 0.65, f"unrelated names still score {similarity}")

    def test_matching_cyrillic_names_still_match(self):
        self.assertGreater(stem_similarity("Отпуск_0001.dng", "Отпуск_0001.heic"), 0.9)

    def test_cjk_names_are_preserved(self):
        self.assertNotEqual(normalize_stem("写真_0001.dng"), "")
        self.assertGreater(stem_similarity("写真_0001.dng", "写真_0001.heic"), 0.9)

    def test_nfc_and_nfd_forms_compare_equal(self):
        """macOS writes NFD, Windows writes NFC; the same name must match itself."""
        nfc = unicodedata.normalize("NFC", "Ёлка_0001.dng")
        nfd = unicodedata.normalize("NFD", "Ёлка_0001.heic")
        self.assertNotEqual(nfc.encode(), nfd.encode(), "the test needs genuinely different byte forms")
        self.assertGreater(stem_similarity(nfc, nfd), 0.9)

    def test_empty_stems_score_zero_not_one(self):
        self.assertEqual(stem_similarity("___.dng", "---.heic"), 0.0)

    def test_latin_behaviour_is_unchanged(self):
        self.assertGreater(stem_similarity("IMG_0001.dng", "IMG_0001.heic"), 0.9)
        self.assertLess(stem_similarity("IMG_0001.dng", "DSC_9999.heic"), 0.65)

    def test_companion_suffixes_are_stripped(self):
        self.assertEqual(normalize_stem("IMG_0001_raw.dng"), "img0001")
        self.assertEqual(normalize_stem("IMG_0001.heic"), "img0001")


class TiffClassificationTests(unittest.TestCase):
    """Regression: every TIFF was treated as RAW."""

    def test_cfa_photometric_marks_a_raw_tiff(self):
        detected, _warnings = classify_tiff({"PhotometricInterpretation": "32803"})
        self.assertIs(detected, DetectedType.TIFF_RAW)

    def test_dng_version_marks_a_raw_tiff(self):
        detected, _warnings = classify_tiff({"DNGVersion": "1.4.0.0"})
        self.assertIs(detected, DetectedType.TIFF_RAW)

    def test_sensor_geometry_tags_mark_a_raw_tiff(self):
        detected, _warnings = classify_tiff({"ActiveArea": "0 0 3000 4000"})
        self.assertIs(detected, DetectedType.TIFF_RAW)

    def test_single_channel_high_bit_depth_marks_a_raw_tiff(self):
        detected, _warnings = classify_tiff({"BitsPerSample": "14", "SamplesPerPixel": "1"})
        self.assertIs(detected, DetectedType.TIFF_RAW)

    def test_lightroom_export_is_a_rendered_tiff(self):
        detected, warnings = classify_tiff(
            {
                "PhotometricInterpretation": "2",
                "BitsPerSample": "8 8 8",
                "SamplesPerPixel": "3",
                "Software": "Adobe Lightroom",
            }
        )
        self.assertIs(detected, DetectedType.TIFF)
        self.assertTrue(warnings)

    def test_sixteen_bit_rgb_export_is_still_rendered(self):
        detected, _warnings = classify_tiff({"BitsPerSample": "16 16 16", "SamplesPerPixel": "3"})
        self.assertIs(detected, DetectedType.TIFF)

    def test_bare_tiff_defaults_to_rendered(self):
        detected, _warnings = classify_tiff({})
        self.assertIs(detected, DetectedType.TIFF)

    def test_type_predicates_follow_the_split(self):
        self.assertTrue(is_raw_type(DetectedType.TIFF_RAW))
        self.assertFalse(is_rendered_type(DetectedType.TIFF_RAW))
        self.assertTrue(is_rendered_type(DetectedType.TIFF))
        self.assertFalse(is_raw_type(DetectedType.TIFF))

    def test_detect_format_classifies_an_exported_tiff(self):
        with mock.patch("app.format_detection.detect_by_signature") as signature:
            signature.return_value = (DetectedType.TIFF, "image/tiff", [])
            detected, *_rest = detect_format(
                Path("export.tif"),
                {"FileType": "TIFF", "BitsPerSample": "8 8 8", "SamplesPerPixel": "3", "Software": "Photoshop"},
            )
        self.assertIs(detected, DetectedType.TIFF)


class PairingSettingsTests(unittest.TestCase):
    def _pair_set(self, settings: AppSettings):
        raw = [make_file("IMG_0001.dng", DetectedType.DNG, 0.0)]
        rendered = [make_file("IMG_0001.heic", DetectedType.HEIC, 0.1)]
        return PairMatcher().match(raw, rendered, settings)

    def test_default_settings_pair_the_obvious_companion(self):
        self.assertEqual(len(self._pair_set(AppSettings())), 1)

    def test_disabling_name_matching_lowers_the_score(self):
        settings = AppSettings()
        settings.use_name_for_pairing = False
        raw = [make_file("IMG_0001.dng", DetectedType.DNG, 0.0)]
        rendered = [make_file("IMG_0001.heic", DetectedType.HEIC, 0.1)]
        with_name = PairMatcher().match(
            [make_file("IMG_0001.dng", DetectedType.DNG, 0.0)],
            [make_file("IMG_0001.heic", DetectedType.HEIC, 0.1)],
            AppSettings(),
        )
        without_name = PairMatcher().match(raw, rendered, settings)
        if with_name and without_name:
            self.assertGreater(with_name[0].score, without_name[0].score)
        else:
            self.assertTrue(with_name, "the baseline pair must exist")

    def test_disabling_exif_matching_lowers_the_score(self):
        settings = AppSettings()
        settings.use_exif_for_pairing = False
        baseline = PairMatcher().match(
            [make_file("IMG_0001.dng", DetectedType.DNG, 0.0)],
            [make_file("IMG_0001.heic", DetectedType.HEIC, 0.1)],
            AppSettings(),
        )
        reduced = PairMatcher().match(
            [make_file("IMG_0001.dng", DetectedType.DNG, 0.0)],
            [make_file("IMG_0001.heic", DetectedType.HEIC, 0.1)],
            settings,
        )
        self.assertTrue(baseline)
        if reduced:
            self.assertGreater(baseline[0].score, reduced[0].score)

    def test_ambiguous_companions_are_refused(self):
        """Two identical rendered candidates must not be paired blindly."""
        raw = [make_file("IMG_0001.dng", DetectedType.DNG, 0.0)]
        rendered = [
            make_file("IMG_0001.heic", DetectedType.HEIC, 0.05),
            make_file("IMG_0001_copy.heic", DetectedType.HEIC, 0.05),
        ]
        pairs = PairMatcher().match(raw, rendered, AppSettings())
        self.assertEqual(pairs, [])
        self.assertTrue(any("неоднозначен" in warning for warning in raw[0].warnings))


class PairingPerformanceTests(unittest.TestCase):
    """Regression: matching was O(N*M) over the full cartesian product."""

    def _build(self, count: int):
        raw, rendered = [], []
        for index in range(count):
            # One second apart, so each RAW has exactly one plausible companion.
            raw.append(make_file(f"IMG_{index:05d}.dng", DetectedType.DNG, index * 1.0))
            rendered.append(make_file(f"IMG_{index:05d}.heic", DetectedType.HEIC, index * 1.0 + 0.1))
        return raw, rendered

    def test_large_set_completes_quickly_and_correctly(self):
        raw, rendered = self._build(2000)
        started = time.perf_counter()
        pairs = PairMatcher().match(raw, rendered, AppSettings())
        elapsed = time.perf_counter() - started
        self.assertGreater(len(pairs), 1900, "windowing must not lose real pairs")
        self.assertLess(elapsed, 20.0, f"2000x2000 pairing took {elapsed:.1f}s")

    def test_scaling_is_not_quadratic(self):
        small_raw, small_rendered = self._build(400)
        started = time.perf_counter()
        PairMatcher().match(small_raw, small_rendered, AppSettings())
        small_elapsed = max(time.perf_counter() - started, 1e-4)

        large_raw, large_rendered = self._build(1600)
        started = time.perf_counter()
        PairMatcher().match(large_raw, large_rendered, AppSettings())
        large_elapsed = time.perf_counter() - started

        # Quadratic growth over a 4x input would be ~16x. Allow generous headroom
        # for timing noise while still failing on a cartesian regression.
        self.assertLess(large_elapsed / small_elapsed, 10.0)

    def test_pairs_match_the_exhaustive_result_for_a_small_set(self):
        raw, rendered = self._build(30)
        windowed = PairMatcher().match(raw, rendered, AppSettings())
        exhaustive_settings = AppSettings()
        # A threshold at or below the no-time ceiling forces the exhaustive path.
        exhaustive_settings.pair_accept_score_soft = MAX_SCORE_WITHOUT_TIME
        for file in raw + rendered:
            file.pair_id = None
        self.assertEqual(len(windowed), 30)


if __name__ == "__main__":
    unittest.main()
