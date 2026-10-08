"""Regression tests for the 1.2.1 core fixes.

1. The pairing candidate index must not depend on the machine timezone.
2. An exposure span below ``min_exposure_span_ev`` is a hard gate, not a lost bonus.
3. Without ExifTool, a TIFF-container RAW (NEF/ARW/CR2/...) is RAW, not a rendered TIFF.
4. dHash scales 16-bit greyscale data instead of clipping it to white.
"""

# Naive (offset-less) capture times are exactly what these tests exercise.
# ruff: noqa: DTZ001

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from app.bracket_detection import HARD_GATE_CONFIDENCE_CAP, BracketDetector
from app.classification import Classifier
from app.constants import SUPPORTED_RAW_EXTENSIONS
from app.enums import DestinationCategory, DetectedType
from app.format_detection import detect_format
from app.models import AppSettings, PhotoFile, PhotoMetadata
from app.pairing import PairMatcher, _epoch_seconds, _RenderedIndex
from app.perceptual_hash import dhash, dhash_from_image, hash_similarity
from app.time_compare import MIXED_TIMEZONE_ASSUMED_OFFSET

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_file(name: str, detected_type: DetectedType, capture_time: datetime | None, **extra) -> PhotoFile:
    fields = {
        "source_path": Path("C:/photos") / name,
        "relative_path": Path(name),
        "file_name": name,
        "extension": Path(name).suffix.lower(),
        "detected_type": detected_type,
        "capture_time": capture_time,
        "make": "Xiaomi",
        "model": "Xiaomi 14",
        "lens_model": "main",
        "orientation": "1",
        "width": 4000,
        "height": 3000,
        "iso": 100,
        "exposure_time": 0.01,
        "f_number": 1.8,
        "exposure_compensation": 0.0,
        "file_size": 1,
    }
    fields.update(extra)
    return PhotoFile(metadata=PhotoMetadata(**fields))


# ------------------------------------------------------------------ 1. pairing index


class _ExplodingDatetime(datetime):
    """Stands in for a naive pre-1970 value on Windows, where ``timestamp()`` raises."""

    def timestamp(self) -> float:
        raise OSError(22, "Invalid argument")


class PairingIndexTimezoneTests(unittest.TestCase):
    """Regression: a naive DNG and an aware HEIC paired only when the PC was on the camera's offset."""

    MACHINE_TIMEZONES = ("UTC", "Asia/Tokyo", "America/New_York", "Europe/Moscow")

    def _force_timezone(self, name: str) -> None:
        os.environ["TZ"] = name
        time.tzset()

    def _with_each_machine_timezone(self, check) -> None:
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset() is POSIX-only; the key-based tests below cover Windows")
        original = os.environ.get("TZ")
        try:
            for name in self.MACHINE_TIMEZONES:
                with self.subTest(machine_timezone=name):
                    self._force_timezone(name)
                    check()
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_naive_wall_clock_key_is_the_same_in_every_machine_timezone(self):
        expected = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC).timestamp()
        self._with_each_machine_timezone(lambda: self.assertEqual(_epoch_seconds(datetime(2026, 7, 1, 12, 0, 0)), expected))

    def test_naive_raw_pairs_with_aware_heic_in_every_machine_timezone(self):
        def check():
            raw = make_file("IMG_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0))
            heic = make_file("IMG_0001.heic", DetectedType.HEIC, datetime(2026, 7, 1, 12, 0, 0, 200000, tzinfo=timezone(timedelta(hours=9))))
            pairs = PairMatcher().match([raw], [heic], AppSettings())
            self.assertEqual(len(pairs), 1)
            self.assertAlmostEqual(pairs[0].time_delta, 0.2)
            self.assertIn(MIXED_TIMEZONE_ASSUMED_OFFSET, pairs[0].warnings)

        self._with_each_machine_timezone(check)

    def test_aware_raw_pairs_with_naive_heic_in_every_machine_timezone(self):
        def check():
            raw = make_file("IMG_0002.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5))))
            heic = make_file("IMG_0002.heic", DetectedType.HEIC, datetime(2026, 7, 1, 12, 0, 0, 300000))
            pairs = PairMatcher().match([raw], [heic], AppSettings())
            self.assertEqual(len(pairs), 1)
            self.assertAlmostEqual(pairs[0].time_delta, 0.3)

        self._with_each_machine_timezone(check)

    def test_index_returns_the_aware_companion_of_a_naive_raw(self):
        """Key-level check that holds on every platform, including Windows."""
        settings = AppSettings()
        raw = make_file("IMG_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0))
        heic = make_file("IMG_0001.heic", DetectedType.HEIC, datetime(2026, 7, 1, 12, 0, 0, 200000, tzinfo=timezone(timedelta(hours=9))))
        index = _RenderedIndex([heic], settings, settings.pair_threshold())
        self.assertFalse(index.exhaustive, "the test must exercise the windowed path")
        self.assertEqual(index.candidates_for(raw), [0])

    def test_index_returns_the_naive_companion_of_an_aware_raw(self):
        settings = AppSettings()
        raw = make_file("IMG_0001.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=9))))
        heic = make_file("IMG_0001.heic", DetectedType.HEIC, datetime(2026, 7, 1, 12, 0, 0, 200000))
        index = _RenderedIndex([heic], settings, settings.pair_threshold())
        self.assertEqual(index.candidates_for(raw), [0])

    def test_aware_files_with_different_offsets_are_matched_on_true_utc(self):
        settings = AppSettings()
        raw = make_file("IMG_0003.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=3))))
        heic = make_file("IMG_0003.heic", DetectedType.HEIC, datetime(2026, 7, 1, 10, 0, 0, 200000, tzinfo=timezone(timedelta(hours=1))))
        index = _RenderedIndex([heic], settings, settings.pair_threshold())
        self.assertEqual(index.candidates_for(raw), [0])
        pairs = PairMatcher().match([raw], [heic], settings)
        self.assertEqual(len(pairs), 1)
        self.assertNotIn(MIXED_TIMEZONE_ASSUMED_OFFSET, pairs[0].warnings)

    def test_aware_rendered_files_hours_away_are_not_candidates_for_an_aware_raw(self):
        """Both files carry an offset, so only the true instant counts: a frame
        shot exactly +offset or -offset hours later must not be scored at all."""
        settings = AppSettings()
        plus_nine = timezone(timedelta(hours=9))
        raw = make_file("IMG_0004.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, tzinfo=plus_nine))
        decoys = [
            make_file("IMG_0004.heic", DetectedType.HEIC, datetime(2026, 7, 1, 21, 0, 0, tzinfo=plus_nine)),
            make_file("IMG_0005.heic", DetectedType.HEIC, datetime(2026, 7, 1, 3, 0, 0, tzinfo=plus_nine)),
        ]
        index = _RenderedIndex(decoys, settings, settings.pair_threshold())
        self.assertEqual(index.candidates_for(raw), [])

    def test_naive_rendered_files_stay_in_the_window_of_a_naive_raw(self):
        settings = AppSettings()
        raw = make_file("IMG_0006.dng", DetectedType.DNG, BASE_TIME)
        rendered = [
            make_file("IMG_0006.heic", DetectedType.HEIC, BASE_TIME + timedelta(seconds=0.2)),
            make_file("IMG_0007.heic", DetectedType.HEIC, BASE_TIME + timedelta(seconds=30)),
            make_file("IMG_0008.heic", DetectedType.HEIC, None),
        ]
        index = _RenderedIndex(rendered, settings, settings.pair_threshold())
        self.assertEqual(index.candidates_for(raw), [0, 2])

    def test_pre_1970_naive_time_has_a_key_without_touching_localtime(self):
        value = datetime(1960, 1, 1, 12, 0, 0)
        self.assertEqual(_epoch_seconds(value), datetime(1960, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp())

    def test_unrepresentable_time_is_treated_as_no_time_instead_of_crashing(self):
        broken = _ExplodingDatetime(1960, 1, 1, 12, 0, 0)
        self.assertIsNone(_epoch_seconds(broken))
        settings = AppSettings()
        raw = make_file("OLD_0001.dng", DetectedType.DNG, broken)
        heic = make_file("OLD_0001.heic", DetectedType.HEIC, broken)
        index = _RenderedIndex([heic], settings, settings.pair_threshold())
        self.assertEqual(index.candidates_for(raw), [0], "a file without a usable time stays reachable as untimed")
        PairMatcher().match([raw], [heic], settings)  # must not raise


# ------------------------------------------------------------ 2. exposure span gate


def burst(count: int, shutters, isos=None, compensation=0.0, prefix: str = "IMG") -> list[PhotoFile]:
    isos = isos or [100] * count
    return [
        make_file(
            f"{prefix}_{index:04d}.dng",
            DetectedType.DNG,
            BASE_TIME + timedelta(seconds=0.3 * index),
            exposure_compensation=compensation,
            exposure_time=shutters[index],
            iso=isos[index],
        )
        for index in range(count)
    ]


class ExposureSpanGateTests(unittest.TestCase):
    """Regression: an AE burst with a 0.36 EV drift scored 75 and became HDR3_0001."""

    def _assert_not_automatic(self, files: list[PhotoFile], size: int) -> None:
        settings = AppSettings()
        self.assertGreater(settings.min_exposure_span_ev, 0.05, "the fixture relies on the default span threshold")
        candidates, series = BracketDetector().detect(files, settings)
        self.assertEqual(series, [])
        for file in files:
            self.assertIsNone(file.series_id)
        matching = [candidate for candidate in candidates if candidate.candidate_size == size]
        self.assertTrue(matching, "the burst must stay visible as a candidate for the review queue")
        for candidate in matching:
            self.assertLessEqual(candidate.confidence, HARD_GATE_CONFIDENCE_CAP)
            self.assertLess(candidate.confidence, settings.min_auto_confidence)
            self.assertIn("Разброс экспозиции меньше порога вилки.", candidate.warnings)
            self.assertNotIn("BRACKET_EXPOSURE_SPAN_OK", candidate.reasons)
            self.assertEqual(len(candidate.exposure_values), size, "exposure values are kept for the review queue")

    def test_three_frame_shutter_drift_burst_is_not_an_automatic_series(self):
        self._assert_not_automatic(burst(3, [1 / 125, 1 / 140, 1 / 160]), 3)

    def test_five_frame_iso_drift_burst_is_not_an_automatic_series(self):
        self._assert_not_automatic(burst(5, [1 / 125] * 5, isos=[100, 100, 125, 125, 160]), 5)

    def test_five_frame_shutter_drift_burst_is_not_an_automatic_series(self):
        self._assert_not_automatic(burst(5, [1 / 125, 1 / 130, 1 / 140, 1 / 150, 1 / 160]), 5)

    def test_three_frame_burst_with_small_compensation_drift_is_not_an_automatic_series(self):
        files = [
            make_file("EC_0001.dng", DetectedType.DNG, BASE_TIME, exposure_compensation=-0.2),
            make_file("EC_0002.dng", DetectedType.DNG, BASE_TIME + timedelta(seconds=0.3), exposure_compensation=0.0),
            make_file("EC_0003.dng", DetectedType.DNG, BASE_TIME + timedelta(seconds=0.6), exposure_compensation=0.2),
        ]
        self._assert_not_automatic(files, 3)

    def test_burst_is_routed_to_raw_not_bracketing(self):
        files = burst(3, [1 / 125, 1 / 140, 1 / 160])
        classified, _pairs, _candidates, series, stats = Classifier().classify([file.metadata for file in files], AppSettings())
        self.assertEqual(series, [])
        self.assertEqual(stats["series_3"], 0)
        for file in classified:
            self.assertIs(file.destination_category, DestinationCategory.RAW)

    def test_real_three_frame_compensation_bracket_is_still_detected(self):
        files = [
            make_file("HDR_0001.dng", DetectedType.DNG, BASE_TIME, exposure_compensation=-1.0),
            make_file("HDR_0002.dng", DetectedType.DNG, BASE_TIME + timedelta(seconds=0.4), exposure_compensation=0.0),
            make_file("HDR_0003.dng", DetectedType.DNG, BASE_TIME + timedelta(seconds=0.8), exposure_compensation=1.0),
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 3)
        self.assertIn("BRACKET_EXPOSURE_SPAN_OK", series[0].reasons)

    def test_real_three_frame_shutter_bracket_with_flat_compensation_is_still_detected(self):
        _candidates, series = BracketDetector().detect(burst(3, [1 / 500, 1 / 125, 1 / 30]), AppSettings())
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].exposure_source, "EV100")

    def test_real_five_frame_bracket_is_still_detected(self):
        files = [
            make_file(f"HDR5_{index:04d}.dng", DetectedType.DNG, BASE_TIME + timedelta(seconds=0.3 * index), exposure_compensation=float(index - 2))
            for index in range(5)
        ]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 5)

    def test_span_exactly_at_the_threshold_still_passes(self):
        settings = AppSettings()
        step = settings.min_exposure_span_ev / 2.0
        files = [
            make_file(f"EDGE_{index:04d}.dng", DetectedType.DNG, BASE_TIME + timedelta(seconds=0.3 * index), exposure_compensation=step * index)
            for index in range(3)
        ]
        _candidates, series = BracketDetector().detect(files, settings)
        self.assertEqual(len(series), 1)


# ------------------------------------------------- 3. RAW by extension without ExifTool


TIFF_CONTAINER_RAW_EXTENSIONS = (".nef", ".arw", ".cr2", ".orf", ".rw2", ".pef", ".erf", ".iiq", ".srf", ".sr2", ".dcr", ".kdc", ".mef", ".mos", ".3fr", ".fff", ".x3f")
LITTLE_ENDIAN_TIFF = b"II*\x00\x08\x00\x00\x00" + b"\x00" * 256
BIG_ENDIAN_TIFF = b"MM\x00*\x00\x00\x00\x08" + b"\x00" * 256
EXTENSION_ONLY_WARNING = "Формат определён только по расширению."


class RawExtensionWithoutExifToolTests(unittest.TestCase):
    """Regression: with no ExifTool record every TIFF-container RAW became a rendered TIFF."""

    def _detect(self, name: str, header: bytes, record):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(header)
            return detect_format(path, record)

    def test_fixture_extensions_are_all_supported_raw_extensions(self):
        for extension in TIFF_CONTAINER_RAW_EXTENSIONS:
            self.assertIn(extension, SUPPORTED_RAW_EXTENSIONS)

    def test_tiff_container_raw_without_exiftool_is_raw(self):
        for extension in TIFF_CONTAINER_RAW_EXTENSIONS:
            for header in (LITTLE_ENDIAN_TIFF, BIG_ENDIAN_TIFF):
                for record in ({}, None):
                    with self.subTest(extension=extension, header=header[:2], record=record):
                        detected, *_rest, warnings = self._detect(f"DSC_0001{extension}", header, record)
                        self.assertIs(detected, DetectedType.RAW)
                        self.assertIn(EXTENSION_ONLY_WARNING, warnings)

    def test_dng_without_exiftool_stays_dng(self):
        detected, *_rest, _warnings = self._detect("IMG_0001.dng", LITTLE_ENDIAN_TIFF, {})
        self.assertIs(detected, DetectedType.DNG)

    def test_bare_tif_without_exiftool_is_still_a_rendered_tiff(self):
        for name in ("export.tif", "export.tiff"):
            with self.subTest(name=name):
                detected, *_rest, warnings = self._detect(name, LITTLE_ENDIAN_TIFF, {})
                self.assertIs(detected, DetectedType.TIFF)
                self.assertNotIn(EXTENSION_ONLY_WARNING, warnings)

    def test_exiftool_type_still_wins_over_a_raw_extension(self):
        record = {"FileType": "TIFF", "BitsPerSample": "8 8 8", "SamplesPerPixel": "3", "Software": "Photoshop"}
        detected, *_rest, warnings = self._detect("misnamed.nef", LITTLE_ENDIAN_TIFF, record)
        self.assertIs(detected, DetectedType.TIFF)
        self.assertNotIn(EXTENSION_ONLY_WARNING, warnings)

    def test_exiftool_raw_type_is_unchanged(self):
        detected, *_rest, warnings = self._detect("DSC_0001.nef", LITTLE_ENDIAN_TIFF, {"FileType": "NEF", "MIMEType": "image/x-nikon-nef"})
        self.assertIs(detected, DetectedType.RAW)
        self.assertNotIn(EXTENSION_ONLY_WARNING, warnings)

    def test_non_tiff_raw_without_exiftool_still_falls_back_to_the_extension(self):
        detected, *_rest, warnings = self._detect("DSCF0001.raf", b"FUJIFILMCCD-RAW 0201FF383501" + b"\x00" * 64, {})
        self.assertIs(detected, DetectedType.RAW)
        self.assertIn(EXTENSION_ONLY_WARNING, warnings)


# ------------------------------------------------------------- 4. 16-bit dHash


def gradient_pixels(width: int, height: int) -> list[int]:
    """A diagonal ramp with a dark band, so every hash row carries both bit values."""
    pixels: list[int] = []
    for y in range(height):
        for x in range(width):
            value = (x * 200) // (width - 1) + (y * 55) // (height - 1)
            if width // 3 <= x < width // 2:
                value //= 4
            pixels.append(min(255, value))
    return pixels


class SixteenBitHashTests(unittest.TestCase):
    """Regression: ``convert("L")`` clipped 16-bit data to white, so 8-bit and 16-bit copies hashed differently."""

    WIDTH = 96
    HEIGHT = 48

    def setUp(self) -> None:
        self.eight_bit_pixels = gradient_pixels(self.WIDTH, self.HEIGHT)
        self.eight_bit = Image.new("L", (self.WIDTH, self.HEIGHT))
        self.eight_bit.putdata(self.eight_bit_pixels)
        self.reference = dhash_from_image(self.eight_bit)
        self.assertIsNotNone(self.reference)
        self.assertNotIn(self.reference, (0, (1 << 64) - 1), "the fixture must produce a non-trivial hash")

    def _sixteen_bit(self, mode: str) -> Image.Image:
        image = Image.new(mode, (self.WIDTH, self.HEIGHT))
        image.putdata([value * 257 for value in self.eight_bit_pixels])
        return image

    def test_sixteen_bit_copies_hash_like_the_eight_bit_original(self):
        for mode in ("I;16", "I;16B", "I;16L", "I"):
            with self.subTest(mode=mode):
                hashed = dhash_from_image(self._sixteen_bit(mode))
                self.assertEqual(hashed, self.reference)
                self.assertEqual(hash_similarity(hashed, self.reference), 1.0)

    def test_sixteen_bit_image_is_no_longer_hashed_as_a_flat_frame(self):
        clipped = dhash_from_image(self._sixteen_bit("I;16").convert("L"))
        self.assertNotEqual(clipped, self.reference, "convert('L') must still clip, or this test proves nothing")
        self.assertEqual(dhash_from_image(self._sixteen_bit("I;16")), self.reference)

    def test_mode_i_holding_eight_bit_data_is_unchanged(self):
        image = Image.new("I", (self.WIDTH, self.HEIGHT))
        image.putdata(self.eight_bit_pixels)
        self.assertEqual(dhash_from_image(image), self.reference)

    def test_sixteen_bit_png_on_disk_hashes_like_its_eight_bit_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            eight = Path(directory) / "eight.png"
            sixteen = Path(directory) / "sixteen.png"
            self.eight_bit.save(eight)
            self._sixteen_bit("I;16").save(sixteen)
            with Image.open(sixteen) as reopened:
                self.assertIn(reopened.mode, ("I;16", "I"), "the file must really be 16-bit")
            self.assertEqual(dhash(sixteen), dhash(eight))
            self.assertEqual(dhash(eight), self.reference)


if __name__ == "__main__":
    unittest.main()
