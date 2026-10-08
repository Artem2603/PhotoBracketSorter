"""Auto-configuration suggestions computed from real metadata (feature 1, 1.2.0)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.auto_configure import analyze_for_suggestions, apply_suggestions
from app.enums import BracketSizeMode, DetectedType
from app.models import AppSettings, PhotoMetadata

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def raw_meta(
    cluster: int,
    index: int,
    exposure_time: float,
    exposure_compensation: float = 0.0,
    make: str = "Canon",
    model: str = "EOS R5",
    cluster_gap: float = 30.0,
    spacing: float = 0.4,
) -> PhotoMetadata:
    name = f"IMG_{cluster:02d}_{index}.dng"
    return PhotoMetadata(
        source_path=Path(f"C:/photos/{name}"),
        relative_path=Path(name),
        file_name=name,
        extension=".dng",
        detected_type=DetectedType.DNG,
        capture_time=BASE_TIME + timedelta(seconds=cluster * cluster_gap + index * spacing),
        make=make,
        model=model,
        lens_model="RF24-70",
        width=6000,
        height=4000,
        orientation="Horizontal",
        exposure_compensation=exposure_compensation,
        exposure_time=exposure_time,
        f_number=2.8,
        iso=100,
        file_size=1000,
    )


def hdr3_flat_ec_dataset(clusters: int = 5) -> list[PhotoMetadata]:
    """The classic P0-1 regression shape: EC=0 everywhere, real shutter bracket."""
    shutters = (1 / 500, 1 / 125, 1 / 30)
    return [raw_meta(cluster, index, shutter, exposure_compensation=0.0) for cluster in range(clusters) for index, shutter in enumerate(shutters)]


def hdr5_direct_ec_dataset(clusters: int = 4) -> list[PhotoMetadata]:
    compensations = (-2.0, -1.0, 0.0, 1.0, 2.0)
    return [raw_meta(cluster, index, 1 / 125, exposure_compensation=ec) for cluster in range(clusters) for index, ec in enumerate(compensations)]


def heic_meta(cluster: int, delta_seconds: float, make: str = "Fujifilm", model: str = "X-T5") -> PhotoMetadata:
    name = f"IMG_{cluster:02d}.heic"
    return PhotoMetadata(
        source_path=Path(f"C:/photos/{name}"),
        relative_path=Path(name),
        file_name=name,
        extension=".heic",
        detected_type=DetectedType.HEIC,
        capture_time=BASE_TIME + timedelta(seconds=cluster * 10, milliseconds=int(delta_seconds * 1000)),
        make=make,
        model=model,
        width=6000,
        height=4000,
        orientation="Horizontal",
        exposure_time=1 / 125,
        f_number=2.8,
        iso=200,
        file_size=800,
    )


class RealHdrDetectionTests(unittest.TestCase):
    """Suggestions must come from the real BracketDetector, not a second algorithm."""

    def test_hdr3_series_yields_typical_size_three(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        self.assertEqual(report.typical_series_size, 3)
        self.assertEqual(report.series_size_histogram, {3: 5})

    def test_hdr5_series_yields_typical_size_five(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr5_direct_ec_dataset(), settings, "ru")
        self.assertEqual(report.typical_series_size, 5)

    def test_flat_exposure_compensation_resolves_through_ev100(self):
        """Regression: EC=0 across the group must not blind the auto-configurer,
        exactly like it used to blind the detector before the P0-1 fix."""
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), AppSettings(), "ru")
        self.assertEqual(report.reliable_exposure_source, "EV100")
        self.assertEqual(report.exposure_source_degenerate_groups, 5)
        self.assertEqual(report.exposure_source_total_groups, 5)

    def test_direct_compensation_does_not_need_the_ev100_fallback(self):
        report = analyze_for_suggestions(hdr5_direct_ec_dataset(), AppSettings(), "ru")
        self.assertEqual(report.reliable_exposure_source, "ExposureCompensation")
        self.assertEqual(report.exposure_source_degenerate_groups, 0)

    def test_timing_only_clusters_are_not_mistaken_for_brackets(self):
        """Regression found during development: files that are merely close in time,
        with identical exposure, must not be reported as a typical bracket size."""
        items = [raw_meta(cluster, 0, 1 / 125, exposure_compensation=0.0, cluster_gap=10.0) for cluster in range(10)]
        report = analyze_for_suggestions(items, AppSettings(), "ru")
        self.assertIsNone(report.typical_series_size)
        self.assertEqual(report.suggestions, ())
        self.assertTrue(report.warnings)

    def test_works_for_a_camera_that_is_neither_xiaomi_nor_proshot(self):
        items = hdr3_flat_ec_dataset()  # already Canon EOS R5 in this module's fixture
        report = analyze_for_suggestions(items, AppSettings(), "ru")
        self.assertEqual(len(report.cameras), 1)
        self.assertEqual(report.cameras[0].make, "Canon")
        self.assertGreater(len(report.suggestions), 0)

    def test_works_for_a_camera_with_no_make_or_model_tags_at_all(self):
        items = [raw_meta(cluster, index, shutter, exposure_compensation=0.0, make="", model="") for cluster in range(3) for index, shutter in enumerate((1 / 500, 1 / 125, 1 / 30))]
        report = analyze_for_suggestions(items, AppSettings(), "en")
        self.assertEqual(report.cameras, ())
        self.assertEqual(report.typical_series_size, 3)

    def test_mixed_camera_folder_reports_every_camera_seen(self):
        items = [raw_meta(0, index, shutter, exposure_compensation=0.0, make="Canon", model="EOS R5") for index, shutter in enumerate((1 / 500, 1 / 125, 1 / 30))]
        items += [raw_meta(1, index, shutter, exposure_compensation=0.0, make="Sony", model="A7R V", cluster_gap=30.0) for index, shutter in enumerate((1 / 400, 1 / 100, 1 / 25))]
        report = analyze_for_suggestions(items, AppSettings(), "ru")
        makes = {camera.make for camera in report.cameras}
        self.assertEqual(makes, {"Canon", "Sony"})


class EdgeCaseTests(unittest.TestCase):
    def test_empty_folder_returns_a_warning_and_no_crash(self):
        report = analyze_for_suggestions([], AppSettings(), "ru")
        self.assertEqual(report.sample_size, 0)
        self.assertEqual(report.suggestions, ())
        self.assertTrue(report.warnings)

    def test_no_raw_files_skips_bracket_suggestions_without_crashing(self):
        items = [heic_meta(cluster, 0.0) for cluster in range(5)]
        report = analyze_for_suggestions(items, AppSettings(), "ru")
        self.assertIsNone(report.typical_series_size)
        self.assertTrue(any("RAW" in warning or "raw" in warning.lower() for warning in report.warnings) or report.warnings)

    def test_singles_only_folder_reports_no_series_without_crashing(self):
        items = [raw_meta(cluster, 0, 1 / 125, exposure_compensation=0.0, cluster_gap=120.0) for cluster in range(5)]
        report = analyze_for_suggestions(items, AppSettings(), "ru")
        self.assertIsNone(report.typical_series_size)
        self.assertEqual(report.suggestions, ())


class PairingToleranceTests(unittest.TestCase):
    def test_pair_delta_suggestion_tightens_an_overly_generous_setting(self):
        items = []
        for cluster in range(8):
            items.append(raw_meta(cluster, 0, 1 / 125, exposure_compensation=0.0, cluster_gap=10.0))
            items.append(heic_meta(cluster, delta_seconds=0.3))
        settings = AppSettings()
        settings.max_pair_delta_seconds = 20.0  # deliberately far too generous
        report = analyze_for_suggestions(items, settings, "ru")
        pair_suggestion = next((s for s in report.suggestions if s.field == "max_pair_delta_seconds"), None)
        self.assertIsNotNone(pair_suggestion)
        self.assertLess(pair_suggestion.suggested_value, settings.max_pair_delta_seconds)
        self.assertAlmostEqual(pair_suggestion.suggested_value, 0.45, delta=0.05)

    def test_pair_delta_suggestion_can_widen_an_overly_strict_setting(self):
        items = []
        for cluster in range(8):
            items.append(raw_meta(cluster, 0, 1 / 125, exposure_compensation=0.0, cluster_gap=10.0))
            items.append(heic_meta(cluster, delta_seconds=1.2))
        settings = AppSettings()
        settings.max_pair_delta_seconds = 0.1  # too strict for these files
        report = analyze_for_suggestions(items, settings, "ru")
        pair_suggestion = next((s for s in report.suggestions if s.field == "max_pair_delta_seconds"), None)
        self.assertIsNotNone(pair_suggestion)
        self.assertGreater(pair_suggestion.suggested_value, settings.max_pair_delta_seconds)

    def test_no_rendered_files_skips_pairing_suggestion(self):
        items = hdr3_flat_ec_dataset()
        report = analyze_for_suggestions(items, AppSettings(), "ru")
        self.assertIsNone(report.typical_pair_delta_seconds)
        self.assertFalse(any(s.field == "max_pair_delta_seconds" for s in report.suggestions))


class NothingAppliesSilentlyTests(unittest.TestCase):
    """P0-2's dead-settings lesson applied prospectively: a suggestion is inert
    until an explicit apply call, and the caller controls exactly what changes."""

    def test_analyze_never_mutates_the_input_settings(self):
        settings = AppSettings()
        original_snapshot = settings.snapshot()
        analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        self.assertEqual(settings.snapshot(), original_snapshot)

    def test_analyze_never_mutates_the_input_metadata(self):
        items = hdr3_flat_ec_dataset()
        fingerprints_before = [(item.series_id if hasattr(item, "series_id") else None, item.warnings[:]) for item in items]
        analyze_for_suggestions(items, AppSettings(), "ru")
        fingerprints_after = [(item.series_id if hasattr(item, "series_id") else None, item.warnings[:]) for item in items]
        self.assertEqual(fingerprints_before, fingerprints_after)

    def test_apply_with_no_accepted_fields_changes_nothing(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        self.assertTrue(report.suggestions)
        result = apply_suggestions(settings, report.suggestions, accepted_fields=set())
        self.assertEqual(result.snapshot(), settings.snapshot())

    def test_apply_only_changes_accepted_fields(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        fields = [s.field for s in report.suggestions]
        self.assertGreaterEqual(len(fields), 2)
        only_one = {fields[0]}
        result = apply_suggestions(settings, report.suggestions, accepted_fields=only_one)
        changed = {key for key in settings.snapshot() if settings.snapshot()[key] != result.snapshot()[key]}
        # bracket_size_mode changes drag max_bracket_duration_seconds along with it;
        # every other accepted field is a single scalar with no derived side effect.
        allowed_side_effects = {"max_bracket_duration_seconds"} if fields[0] == "bracket_size_mode" else set()
        self.assertEqual(changed, {fields[0]} | (allowed_side_effects & changed))

    def test_apply_does_not_mutate_the_original_settings_object(self):
        settings = AppSettings()
        original_snapshot = settings.snapshot()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        apply_suggestions(settings, report.suggestions)
        self.assertEqual(settings.snapshot(), original_snapshot)

    def test_apply_returns_a_distinct_object(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        result = apply_suggestions(settings, report.suggestions)
        self.assertIsNot(result, settings)

    def test_overrides_win_over_the_original_suggested_value(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        confidence_suggestion = next(s for s in report.suggestions if s.field == "min_auto_confidence")
        edited_value = confidence_suggestion.suggested_value + 3.0
        result = apply_suggestions(
            settings,
            report.suggestions,
            accepted_fields={"min_auto_confidence"},
            overrides={"min_auto_confidence": edited_value},
        )
        self.assertEqual(result.min_auto_confidence, edited_value)
        self.assertNotEqual(result.min_auto_confidence, confidence_suggestion.suggested_value)

    def test_bracket_size_mode_suggestion_produces_the_enum_not_a_string(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        mode_suggestion = next(s for s in report.suggestions if s.field == "bracket_size_mode")
        self.assertIsInstance(mode_suggestion.suggested_value, BracketSizeMode)
        result = apply_suggestions(settings, report.suggestions, accepted_fields={"bracket_size_mode"})
        self.assertIsInstance(result.bracket_size_mode, BracketSizeMode)


class NoNoiseSuggestionsTests(unittest.TestCase):
    """A suggestion close to the current value must not appear — otherwise every
    dialog would fill with rows the user has no real reason to act on."""

    def test_settings_already_matching_the_data_produce_no_suggestion_for_that_field(self):
        settings = AppSettings()
        settings.min_exposure_span_ev = 1.2  # close to the ~1.2 EV this dataset implies
        settings.exposure_step_tolerance = 0.1
        settings.max_bracket_adjacent_delta_seconds = 0.6
        settings.max_bracket_duration_seconds_3 = 1.2
        settings.min_auto_confidence = 95.0
        settings.bracket_size_mode = BracketSizeMode.THREE
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "ru")
        self.assertEqual(report.suggestions, ())


class AutoExposureDriftTests(unittest.TestCase):
    """Regression: discovery accepted any span >= 0.05 EV, so plain bursts with mild
    auto-exposure drift (1/125 -> 1/140 -> 1/160, 0.36 EV in total) counted as HDR3
    series and the tool suggested min_exposure_span_ev ~ 0.1 and sub-second
    durations for a folder that holds no bracket at all."""

    def _drift_bursts(self, clusters: int = 6) -> list[PhotoMetadata]:
        return [raw_meta(cluster, index, shutter, make="Sony", model="A7") for cluster in range(clusters) for index, shutter in enumerate((1 / 125, 1 / 140, 1 / 160))]

    def test_drift_bursts_are_not_counted_as_series(self):
        report = analyze_for_suggestions(self._drift_bursts(), AppSettings(), "en")
        self.assertIsNone(report.typical_series_size)
        self.assertEqual(report.series_size_histogram, {})
        self.assertFalse(any(suggestion.field in {"min_exposure_span_ev", "max_bracket_duration_seconds_3"} for suggestion in report.suggestions))
        self.assertTrue(report.warnings)

    def test_discovery_span_follows_the_documented_rule(self):
        from app.auto_configure import _discovery_min_span_ev

        settings = AppSettings()
        settings.min_exposure_span_ev = 0.7
        self.assertAlmostEqual(_discovery_min_span_ev(settings), 0.5)  # the 0.5 EV floor
        settings.min_exposure_span_ev = 3.0
        self.assertAlmostEqual(_discovery_min_span_ev(settings), 1.5)  # half of a strict user setting
        settings.min_exposure_span_ev = 0.3
        self.assertAlmostEqual(_discovery_min_span_ev(settings), 0.3)  # never stricter than the user

    def test_a_modest_but_genuine_bracket_still_surfaces(self):
        # +-2/3 EV around the middle frame: log2(200/80) = 1.32 EV in total.
        items = [raw_meta(cluster, index, shutter) for cluster in range(5) for index, shutter in enumerate((1 / 200, 1 / 125, 1 / 80))]
        report = analyze_for_suggestions(items, AppSettings(), "en")
        self.assertEqual(report.typical_series_size, 3)


class TimingConsistencyOnApplyTests(unittest.TestCase):
    """Regression: accepting only the duration suggestion left the adjacent delta
    above it, which validate_settings rejects ("duration 3 < adjacent") — the
    error surfaced later in the settings dialog instead of being impossible."""

    def _fast_dataset(self) -> list[PhotoMetadata]:
        # 0.2 s between frames: duration 0.4 s -> suggested duration 0.6 s, adjacent 0.3 s.
        return [raw_meta(cluster, index, shutter, spacing=0.2) for cluster in range(5) for index, shutter in enumerate((1 / 500, 1 / 125, 1 / 30))]

    def _slow_dataset(self) -> list[PhotoMetadata]:
        # 3 s between frames: the adjacent suggestion (4.5 s) exceeds the default 4.0 s HDR3 duration.
        return [raw_meta(cluster, index, shutter, spacing=3.0, cluster_gap=120.0) for cluster in range(5) for index, shutter in enumerate((1 / 500, 1 / 125, 1 / 30))]

    def test_duration_only_acceptance_still_validates(self):
        from app.settings import validate_settings

        settings = AppSettings()
        report = analyze_for_suggestions(self._fast_dataset(), settings, "en")
        duration = next(suggestion for suggestion in report.suggestions if suggestion.field == "max_bracket_duration_seconds_3")
        self.assertLess(duration.suggested_value, settings.max_bracket_adjacent_delta_seconds, "fixture must produce the conflicting shape")
        result = apply_suggestions(settings, report.suggestions, accepted_fields={"max_bracket_duration_seconds_3"})
        validate_settings(result)  # must not raise
        self.assertEqual(result.max_bracket_duration_seconds_3, duration.suggested_value)
        self.assertLessEqual(result.max_bracket_adjacent_delta_seconds, result.max_bracket_duration_seconds_3)

    def test_adjacent_only_acceptance_that_widens_still_validates(self):
        from app.settings import validate_settings

        settings = AppSettings()
        report = analyze_for_suggestions(self._slow_dataset(), settings, "en")
        adjacent = next(suggestion for suggestion in report.suggestions if suggestion.field == "max_bracket_adjacent_delta_seconds")
        self.assertGreater(adjacent.suggested_value, settings.max_bracket_duration_seconds_3, "fixture must produce the conflicting shape")
        result = apply_suggestions(settings, report.suggestions, accepted_fields={"max_bracket_adjacent_delta_seconds"})
        validate_settings(result)
        self.assertEqual(result.max_bracket_adjacent_delta_seconds, adjacent.suggested_value)
        self.assertGreaterEqual(result.max_bracket_duration_seconds_3, adjacent.suggested_value)
        self.assertGreaterEqual(result.max_bracket_duration_seconds, adjacent.suggested_value)

    def test_accepting_both_timing_suggestions_keeps_both(self):
        from app.settings import validate_settings

        settings = AppSettings()
        report = analyze_for_suggestions(self._fast_dataset(), settings, "en")
        accepted = {"max_bracket_duration_seconds_3", "max_bracket_adjacent_delta_seconds"}
        result = apply_suggestions(settings, report.suggestions, accepted_fields=accepted)
        validate_settings(result)
        for field in accepted:
            self.assertEqual(getattr(result, field), next(suggestion.suggested_value for suggestion in report.suggestions if suggestion.field == field))

    def test_every_subset_of_suggestions_validates(self):
        from itertools import combinations

        from app.settings import validate_settings

        for dataset in (self._fast_dataset(), self._slow_dataset()):
            settings = AppSettings()
            report = analyze_for_suggestions(dataset, settings, "en")
            fields = [suggestion.field for suggestion in report.suggestions]
            self.assertTrue(fields)
            for count in range(len(fields) + 1):
                for subset in combinations(fields, count):
                    validate_settings(apply_suggestions(settings, report.suggestions, accepted_fields=set(subset)))

    def test_untouched_settings_are_not_rewritten(self):
        settings = AppSettings()
        report = analyze_for_suggestions(hdr3_flat_ec_dataset(), settings, "en")
        result = apply_suggestions(settings, report.suggestions, accepted_fields={"min_auto_confidence"})
        changed = {key for key in settings.snapshot() if settings.snapshot()[key] != result.snapshot()[key]}
        self.assertEqual(changed, {"min_auto_confidence"})


if __name__ == "__main__":
    unittest.main()
