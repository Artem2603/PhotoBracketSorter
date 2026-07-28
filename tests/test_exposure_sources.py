"""Exposure source resolution and real bracket detection (P0-1)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.bracket_detection import BracketDetector
from app.enums import DetectedType
from app.exposure import (
    DEGENERATE_SPAN_EV,
    ExposureSource,
    ev100,
    resolve_group_exposure,
    source_value,
)
from app.models import AppSettings, PhotoFile, PhotoMetadata

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_file(index: int, **metadata_fields) -> PhotoFile:
    """A RAW frame with camera identity and geometry filled in."""
    name = f"IMG_{index:04d}.dng"
    fields = {
        "source_path": Path(f"C:/photos/{name}"),
        "relative_path": Path(name),
        "file_name": name,
        "extension": ".dng",
        "detected_type": DetectedType.DNG,
        "capture_time": BASE_TIME + timedelta(seconds=0.4 * index),
        "make": "TestCam",
        "model": "T1",
        "lens_model": "24mm",
        "width": 4000,
        "height": 3000,
        "orientation": "Horizontal",
        "file_size": 1000,
    }
    fields.update(metadata_fields)
    return PhotoFile(metadata=PhotoMetadata(**fields))


def shutter_bracket(shutters: list[float], **extra) -> list[PhotoFile]:
    """Frames that bracket by shutter speed only, at fixed aperture and ISO."""
    return [
        make_file(index, exposure_time=shutter, f_number=2.8, iso=100, **extra)
        for index, shutter in enumerate(shutters, start=1)
    ]


class ExposureSourceResolutionTests(unittest.TestCase):
    def test_sources_are_never_mixed_within_a_group(self):
        files = [
            make_file(1, exposure_compensation=-2.0),
            make_file(2, baseline_exposure=0.5),
            make_file(3, exposure_time=1 / 60, f_number=4.0, iso=200),
        ]
        group = resolve_group_exposure(files)
        # No single source covers all three files, so nothing is comparable.
        self.assertFalse(group.complete)
        self.assertIs(group.source, ExposureSource.UNKNOWN)
        self.assertEqual(group.values, ())

    def test_partial_source_is_skipped_for_a_complete_one(self):
        # ExposureCompensation covers only 2 of 3; EV100 covers all 3.
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30])
        files[0].metadata.exposure_compensation = -2.0
        files[1].metadata.exposure_compensation = 0.0
        group = resolve_group_exposure(files)
        self.assertTrue(group.complete)
        self.assertIs(group.source, ExposureSource.EV100)

    def test_light_value_is_sign_normalised_to_brightness(self):
        metadata = make_file(1, light_value=8.0).metadata
        self.assertEqual(source_value(metadata, ExposureSource.LIGHT_VALUE), -8.0)

    def test_ev100_matches_the_apex_definition(self):
        # f/2.8, 1/125s, ISO 100 -> log2(7.84 * 125) = log2(980)
        metadata = make_file(1, f_number=2.8, exposure_time=1 / 125, iso=100).metadata
        self.assertAlmostEqual(ev100(metadata), 9.9366, places=3)

    def test_ev100_rejects_impossible_values(self):
        self.assertIsNone(ev100(make_file(1, f_number=0.0, exposure_time=1 / 60).metadata))
        self.assertIsNone(ev100(make_file(1, f_number=2.8, exposure_time=0.0).metadata))
        self.assertIsNone(ev100(make_file(1, f_number=2.8).metadata))

    def test_iso_bracketing_is_detected_through_ev100(self):
        files = [
            make_file(index, exposure_time=1 / 125, f_number=2.8, iso=iso)
            for index, iso in enumerate((400, 100, 25), start=1)
        ]
        group = resolve_group_exposure(files)
        self.assertIs(group.source, ExposureSource.EV100)
        self.assertAlmostEqual(group.span, 4.0, places=6)

    def test_empty_group_resolves_to_unknown(self):
        group = resolve_group_exposure([])
        self.assertFalse(group.complete)
        self.assertIs(group.source, ExposureSource.UNKNOWN)


class DegenerateCompensationTests(unittest.TestCase):
    """The headline P0-1 bug: EC=0 everywhere hiding a real shutter bracket."""

    def test_flat_compensation_falls_through_to_ev100(self):
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30], exposure_compensation=0.0)
        group = resolve_group_exposure(files)
        self.assertIs(group.source, ExposureSource.EV100)
        self.assertTrue(group.usable)
        self.assertFalse(group.degenerate)
        # Nominal shutter speeds are rounded (1/30 is not exactly two stops from
        # 1/125), so a real-world bracket lands near, not on, the whole stop.
        self.assertAlmostEqual(group.span, 4.0, delta=0.1)
        self.assertAlmostEqual(group.step_ev, 2.0, delta=0.1)
        self.assertTrue(group.uniform)

    def test_flat_compensation_is_recorded_as_skipped(self):
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30], exposure_compensation=0.0)
        group = resolve_group_exposure(files)
        skipped = {source for source, _reason in group.skipped_sources}
        self.assertIn(ExposureSource.EXPOSURE_COMPENSATION, skipped)

    def test_varying_compensation_still_wins_over_ev100(self):
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30])
        for file, compensation in zip(files, (-2.0, 0.0, 2.0)):
            file.metadata.exposure_compensation = compensation
        group = resolve_group_exposure(files)
        self.assertIs(group.source, ExposureSource.EXPOSURE_COMPENSATION)
        self.assertEqual(sorted(group.values), [-2.0, 0.0, 2.0])

    def test_truly_flat_exposure_reports_an_honest_zero_span(self):
        files = [
            make_file(index, exposure_compensation=0.0, exposure_time=1 / 125, f_number=2.8, iso=100)
            for index in range(1, 4)
        ]
        group = resolve_group_exposure(files)
        self.assertTrue(group.complete)
        self.assertTrue(group.degenerate)
        self.assertFalse(group.usable)
        self.assertLessEqual(group.span, DEGENERATE_SPAN_EV)


class RealBracketDetectionTests(unittest.TestCase):
    def setUp(self):
        self.settings = AppSettings()
        self.detector = BracketDetector()

    def test_real_hdr3_series_is_detected(self):
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30])
        for file, compensation in zip(files, (-2.0, 0.0, 2.0)):
            file.metadata.exposure_compensation = compensation
        _candidates, series = self.detector.detect(files, self.settings)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 3)
        self.assertEqual(sorted(series[0].exposure_values), [-2.0, 0.0, 2.0])

    def test_real_hdr5_series_is_detected(self):
        shutters = [1 / 800, 1 / 200, 1 / 50, 1 / 13, 1 / 3]
        files = shutter_bracket(shutters)
        for file, compensation in zip(files, (-2.0, -1.0, 0.0, 1.0, 2.0)):
            file.metadata.exposure_compensation = compensation
        _candidates, series = self.detector.detect(files, self.settings)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 5)

    def test_hdr3_with_flat_compensation_but_real_shutters_is_detected(self):
        """Regression: this series was silently invisible before P0-1."""
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30], exposure_compensation=0.0)
        _candidates, series = self.detector.detect(files, self.settings)
        self.assertEqual(len(series), 1, "shutter-only bracket must be detected despite EC=0")
        self.assertEqual(series[0].series_size, 3)
        self.assertAlmostEqual(series[0].estimated_step_ev, 2.0, delta=0.1)

    def test_hdr5_with_flat_compensation_but_real_shutters_is_detected(self):
        files = shutter_bracket([1 / 800, 1 / 200, 1 / 50, 1 / 13, 1 / 3], exposure_compensation=0.0)
        _candidates, series = self.detector.detect(files, self.settings)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].series_size, 5)

    def test_burst_without_exposure_variation_is_not_a_series(self):
        files = shutter_bracket([1 / 125, 1 / 125, 1 / 125], exposure_compensation=0.0)
        _candidates, series = self.detector.detect(files, self.settings)
        self.assertEqual(series, [], "a flat burst must not be promoted to a bracket")

    def test_exposure_source_is_recorded_on_the_series(self):
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30], exposure_compensation=0.0)
        candidates, _series = self.detector.detect(files, self.settings)
        chosen = [candidate for candidate in candidates if candidate.candidate_size == 3][0]
        self.assertEqual(chosen.exposure_source, ExposureSource.EV100.value)


class PerSizeDurationTests(unittest.TestCase):
    """max_bracket_duration_seconds_3 / _5 must actually apply (P0-2)."""

    def test_three_shot_limit_rejects_a_slow_triple(self):
        settings = AppSettings()
        settings.max_bracket_duration_seconds_3 = 1.0
        settings.max_bracket_duration_seconds_5 = 30.0
        settings.max_bracket_adjacent_delta_seconds = 30.0
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30], exposure_compensation=0.0)
        for offset, file in enumerate(files):
            file.metadata.capture_time = BASE_TIME + timedelta(seconds=5 * offset)
        _candidates, series = BracketDetector().detect(files, settings)
        self.assertEqual(series, [], "10s spread must fail a 1s three-shot limit")

    def test_five_shot_limit_is_independent_of_the_three_shot_limit(self):
        settings = AppSettings()
        settings.max_bracket_duration_seconds_3 = 0.1
        settings.max_bracket_duration_seconds_5 = 30.0
        settings.max_bracket_adjacent_delta_seconds = 30.0
        files = shutter_bracket([1 / 800, 1 / 200, 1 / 50, 1 / 13, 1 / 3], exposure_compensation=0.0)
        for offset, file in enumerate(files):
            file.metadata.capture_time = BASE_TIME + timedelta(seconds=2 * offset)
        _candidates, series = BracketDetector().detect(files, settings)
        self.assertEqual(len(series), 1, "the generous five-shot limit must still admit the series")


class MinAutoConfidenceTests(unittest.TestCase):
    """min_auto_confidence must drive the acceptance threshold (P0-2)."""

    def _borderline_series(self) -> list[PhotoFile]:
        """Scores 85: duration 22 + adjacency 18 + span 25 + step 20, no lens/geometry bonus."""
        files = shutter_bracket([1 / 500, 1 / 125, 1 / 30], exposure_compensation=0.0)
        for file in files:
            file.metadata.lens_model = None
            file.metadata.width = None
            file.metadata.height = None
        return files

    def test_permissive_threshold_accepts_a_borderline_series(self):
        settings = AppSettings()
        settings.min_auto_confidence = 60.0
        _candidates, series = BracketDetector().detect(self._borderline_series(), settings)
        self.assertEqual(len(series), 1)
        self.assertAlmostEqual(series[0].confidence, 85.0, delta=0.01)

    def test_raising_the_threshold_rejects_the_same_series(self):
        settings = AppSettings()
        settings.min_auto_confidence = 90.0
        _candidates, series = BracketDetector().detect(self._borderline_series(), settings)
        self.assertEqual(series, [], "confidence 85 must fail a 90 threshold")

    def test_detection_mode_shifts_the_threshold(self):
        from app.enums import DetectionMode

        settings = AppSettings()
        settings.min_auto_confidence = 80.0
        settings.detection_mode = DetectionMode.SOFT  # 80 - 10 = 70 -> accepts 85
        self.assertEqual(len(BracketDetector().detect(self._borderline_series(), settings)[1]), 1)
        settings.detection_mode = DetectionMode.STRICT  # 80 + 10 = 90 -> rejects 85
        self.assertEqual(BracketDetector().detect(self._borderline_series(), settings)[1], [])


if __name__ == "__main__":
    unittest.main()
