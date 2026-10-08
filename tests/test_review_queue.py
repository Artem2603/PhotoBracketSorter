"""Manual review queue: population and actions (feature 2, 1.2.0)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.enums import CaptureTimeSource, ConfidenceLevel, DestinationCategory, DetectedType
from app.errors import ManualSeriesError
from app.models import AnalysisResult, AppSettings, BracketCandidate, BracketSeries, PhotoFile, PhotoMetadata, RawHeicPair
from app.review_queue import (
    DISMISS_NOT_BRACKET_MARKER,
    NEAR_MISS_MIN_CONFIDENCE,
    SPLIT_KEEP_SINGLE_MARKER,
    ReviewReason,
    build_review_queue,
    confirm_candidate,
    dismiss_candidate,
    exclude_files,
    merge_series,
    restore_automatic,
    split_series,
)
from app.series_manager import SeriesManager

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_raw(index: int, detected_type: DetectedType = DetectedType.DNG, **overrides) -> PhotoFile:
    name = f"IMG_{index:04d}.dng"
    fields = dict(
        source_path=Path(f"C:/photos/{name}"),
        relative_path=Path(name),
        file_name=name,
        extension=".dng",
        detected_type=detected_type,
        capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
        exposure_compensation=float(index % 3) - 1.0,
        make="Canon",
        model="EOS R5",
        lens_model="RF24-70",
        width=6000,
        height=4000,
        orientation="Horizontal",
        file_size=1000,
    )
    fields.update(overrides)
    return PhotoFile(metadata=PhotoMetadata(**fields), destination_category=DestinationCategory.RAW, destination_subpath=Path("RAW") / name)


def make_rendered(index: int, **overrides) -> PhotoFile:
    name = f"IMG_{index:04d}.heic"
    fields = dict(
        source_path=Path(f"C:/photos/{name}"),
        relative_path=Path(name),
        file_name=name,
        extension=".heic",
        detected_type=DetectedType.HEIC,
        capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
        make="Canon",
        model="EOS R5",
        width=6000,
        height=4000,
        file_size=800,
    )
    fields.update(overrides)
    return PhotoFile(metadata=PhotoMetadata(**fields), destination_category=DestinationCategory.HEIC, destination_subpath=Path("HEIC") / name)


def make_series(
    files: list[PhotoFile],
    series_id: str = "HDR3_0001",
    size: int = 3,
    confidence: float = 90.0,
    reasons: list[str] | None = None,
    warnings: list[str] | None = None,
    automatic: bool = True,
    companions: dict | None = None,
) -> BracketSeries:
    return BracketSeries(
        series_id=series_id,
        series_number=1,
        series_size=size,
        raw_files=files,
        rendered_companions=companions if companions is not None else {file.metadata.file_name: None for file in files},
        start_time=files[0].metadata.capture_time,
        end_time=files[-1].metadata.capture_time,
        duration_seconds=1.0,
        exposure_values=[-1.0, 0.0, 1.0][: len(files)],
        estimated_step_ev=1.0,
        confidence=confidence,
        confidence_level=ConfidenceLevel.from_score(confidence),
        automatic=automatic,
        manually_confirmed=not automatic,
        reasons=reasons if reasons is not None else ["BRACKET_DURATION_OK", "BRACKET_EXPOSURE_SPAN_OK", "BRACKET_DIMENSIONS_OK"],
        warnings=warnings or [],
    )


def make_analysis(files: list[PhotoFile], series: list[BracketSeries] | None = None, pairs: list[RawHeicPair] | None = None, candidates: list | None = None) -> AnalysisResult:
    for file in files:
        for s in series or []:
            if file in s.raw_files:
                file.series_id = s.series_id
    return AnalysisResult(
        source_root=Path("C:/photos"),
        files=files,
        pairs=pairs or [],
        bracket_candidates=candidates or [],
        bracket_series=series or [],
        statistics={},
        warnings=[],
        created_at=BASE_TIME,
        settings_snapshot={},
        fingerprint="fp",
    )


class SeriesReviewItemTests(unittest.TestCase):
    def test_low_confidence_series_is_flagged(self):
        files = [make_raw(index) for index in range(3)]
        series = make_series(files, confidence=50.0)
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items}
        self.assertIn(ReviewReason.LOW_CONFIDENCE, reasons)

    def test_high_confidence_series_is_not_flagged_for_confidence(self):
        files = [make_raw(index) for index in range(3)]
        series = make_series(files, confidence=95.0)
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertNotIn(ReviewReason.LOW_CONFIDENCE, reasons)

    def test_uneven_exposure_step_is_flagged(self):
        files = [
            make_raw(0, exposure_compensation=-2.0),
            make_raw(1, exposure_compensation=-1.5),
            make_raw(2, exposure_compensation=2.0),
        ]
        series = make_series(files, confidence=90.0)
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertIn(ReviewReason.UNEVEN_STEP, reasons)

    def test_even_exposure_step_is_not_flagged(self):
        files = [
            make_raw(0, exposure_compensation=-1.0),
            make_raw(1, exposure_compensation=0.0),
            make_raw(2, exposure_compensation=1.0),
        ]
        series = make_series(files, confidence=90.0)
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertNotIn(ReviewReason.UNEVEN_STEP, reasons)

    def test_partial_companions_flag_an_incomplete_series(self):
        raw_files = [make_raw(index) for index in range(3)]
        heic = make_rendered(0)
        companions = {raw_files[0].metadata.file_name: heic, raw_files[1].metadata.file_name: None, raw_files[2].metadata.file_name: None}
        series = make_series(raw_files, confidence=90.0, companions=companions)
        manager = SeriesManager([series])
        analysis = make_analysis(raw_files + [heic], [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertIn(ReviewReason.INCOMPLETE_SERIES, reasons)

    def test_no_companions_at_all_is_not_incomplete(self):
        """All-or-nothing is the common case, not a review-worthy anomaly."""
        files = [make_raw(index) for index in range(3)]
        series = make_series(files, confidence=90.0)  # companions default to all-None
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertNotIn(ReviewReason.INCOMPLETE_SERIES, reasons)

    def test_missing_dimension_agreement_is_flagged(self):
        files = [make_raw(index) for index in range(3)]
        series = make_series(files, confidence=90.0, reasons=["BRACKET_DURATION_OK", "BRACKET_EXPOSURE_SPAN_OK"])
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertIn(ReviewReason.MIXED_EVIDENCE, reasons)

    def test_mixed_time_warning_code_is_flagged(self):
        files = [make_raw(index) for index in range(3)]
        series = make_series(files, confidence=90.0, warnings=["MIXED_TIMEZONE_ASSUMED_OFFSET"])
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertIn(ReviewReason.MIXED_TIME_SOURCE, reasons)

    def test_weak_time_source_mixed_with_strong_is_flagged(self):
        files = [
            make_raw(0, capture_time_source=CaptureTimeSource.DATE_TIME_ORIGINAL),
            make_raw(1, capture_time_source=CaptureTimeSource.FILE_MTIME),
            make_raw(2, capture_time_source=CaptureTimeSource.DATE_TIME_ORIGINAL),
        ]
        series = make_series(files, confidence=90.0)
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        reasons = {item.reason for item in items if item.series_uid == series.uid}
        self.assertIn(ReviewReason.MIXED_TIME_SOURCE, reasons)

    def test_manual_series_is_flagged_as_manual_edit(self):
        manager = SeriesManager()
        files = [make_raw(index) for index in range(3)]
        series = manager.create_manual(files, 3)
        analysis = make_analysis(files, manager.series)
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        manual_items = [item for item in items if item.reason is ReviewReason.MANUAL_EDIT and item.series_uid == series.uid]
        self.assertEqual(len(manual_items), 1)


class NearMissCandidateTests(unittest.TestCase):
    def _candidate(self, files):
        from app.models import BracketCandidate

        return BracketCandidate(
            candidate_id="CAND_0001",
            files=files,
            candidate_size=3,
            start_time=files[0].metadata.capture_time,
            end_time=files[-1].metadata.capture_time,
            duration_seconds=2.0,
            exposure_values=[],
            exposure_source="UNKNOWN",
            estimated_step_ev=None,
            score=22.0,
            confidence=22.0,
            reasons=["BRACKET_DURATION_OK"],
            warnings=["Соседние кадры сняты не плотной серией."],
        )

    def test_unresolved_candidate_is_a_near_miss_item(self):
        files = [make_raw(index) for index in range(3)]
        candidate = self._candidate(files)
        analysis = make_analysis(files, [], candidates=[candidate])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        near_miss = [item for item in items if item.reason is ReviewReason.NEAR_MISS_CANDIDATE]
        self.assertEqual(len(near_miss), 1)
        self.assertEqual(near_miss[0].candidate_id, "CAND_0001")

    def test_candidate_whose_files_already_belong_to_a_series_is_not_a_near_miss(self):
        files = [make_raw(index) for index in range(3)]
        series = make_series(files, confidence=90.0)
        candidate = self._candidate(files)
        analysis = make_analysis(files, [series], candidates=[candidate])
        manager = SeriesManager([series])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        near_miss = [item for item in items if item.reason is ReviewReason.NEAR_MISS_CANDIDATE]
        self.assertEqual(near_miss, [])

    def test_dismiss_candidate_removes_it_from_the_queue(self):
        files = [make_raw(index) for index in range(3)]
        candidate = self._candidate(files)
        analysis = make_analysis(files, [], candidates=[candidate])
        manager = SeriesManager()
        dismiss_candidate(candidate)
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        near_miss = [item for item in items if item.reason is ReviewReason.NEAR_MISS_CANDIDATE]
        self.assertEqual(near_miss, [])
        self.assertTrue(all(DISMISS_NOT_BRACKET_MARKER in file.decision_reasons for file in files))

    def test_confirm_candidate_creates_a_real_series_via_series_manager(self):
        files = [make_raw(index) for index in range(3)]
        candidate = self._candidate(files)
        manager = SeriesManager()
        series = confirm_candidate(manager, candidate)
        self.assertEqual(len(manager.series), 1)
        self.assertIs(manager.series[0], series)
        for file in files:
            self.assertEqual(file.series_id, series.series_id)
            self.assertIs(file.destination_category, DestinationCategory.BRACKETING_3)

    def test_confirm_candidate_with_explicit_size(self):
        files = [make_raw(index) for index in range(5)]
        from app.models import BracketCandidate

        candidate = BracketCandidate(
            candidate_id="CAND_0002",
            files=files,
            candidate_size=5,
            start_time=files[0].metadata.capture_time,
            end_time=files[-1].metadata.capture_time,
            duration_seconds=2.0,
            exposure_values=[],
            exposure_source="UNKNOWN",
            estimated_step_ev=None,
            score=22.0,
            confidence=22.0,
        )
        manager = SeriesManager()
        series = confirm_candidate(manager, candidate, size=5)
        self.assertEqual(series.series_size, 5)

    def test_confirmed_candidate_no_longer_appears_as_a_near_miss(self):
        files = [make_raw(index) for index in range(3)]
        candidate = self._candidate(files)
        manager = SeriesManager()
        confirm_candidate(manager, candidate)
        analysis = make_analysis(files, manager.series, candidates=[candidate])
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        near_miss = [item for item in items if item.reason is ReviewReason.NEAR_MISS_CANDIDATE]
        self.assertEqual(near_miss, [])


class AmbiguousPairTests(unittest.TestCase):
    def test_ambiguous_accepted_pair_is_flagged(self):
        raw = make_raw(0)
        rendered = make_rendered(0)
        pair = RawHeicPair(
            pair_id="PAIR_0001",
            raw_file=raw,
            rendered_file=rendered,
            score=70.0,
            time_delta=0.2,
            name_similarity=0.9,
            metadata_similarity=0.8,
            preview_similarity=None,
            ambiguous=True,
        )
        analysis = make_analysis([raw, rendered], [], pairs=[pair])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        ambiguous = [item for item in items if item.reason is ReviewReason.AMBIGUOUS_PAIR]
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous[0].pair_id, "PAIR_0001")

    def test_non_ambiguous_pair_is_not_flagged(self):
        raw = make_raw(0)
        rendered = make_rendered(0)
        pair = RawHeicPair(
            pair_id="PAIR_0001",
            raw_file=raw,
            rendered_file=rendered,
            score=90.0,
            time_delta=0.05,
            name_similarity=0.95,
            metadata_similarity=0.95,
            preview_similarity=None,
            ambiguous=False,
        )
        analysis = make_analysis([raw, rendered], [], pairs=[pair])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        self.assertFalse([item for item in items if item.reason is ReviewReason.AMBIGUOUS_PAIR])

    def test_rejected_ambiguous_files_are_flagged_even_without_a_pair_object(self):
        raw = make_raw(0)
        raw.warnings.append("Компаньон RAW + HEIC неоднозначен (разрыв 1.0 балла).")
        analysis = make_analysis([raw], [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        ambiguous = [item for item in items if item.reason is ReviewReason.AMBIGUOUS_PAIR]
        self.assertEqual(len(ambiguous), 1)


class NameConflictTests(unittest.TestCase):
    def test_two_selected_files_sharing_a_destination_are_flagged(self):
        a = make_raw(0)
        b = make_raw(1)
        b.destination_subpath = a.destination_subpath  # forced collision
        analysis = make_analysis([a, b], [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        conflicts = [item for item in items if item.reason is ReviewReason.NAME_CONFLICT]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(len(conflicts[0].files), 2)

    def test_unselected_files_do_not_count_toward_a_conflict(self):
        a = make_raw(0)
        b = make_raw(1)
        b.destination_subpath = a.destination_subpath
        b.selected = False
        analysis = make_analysis([a, b], [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        self.assertFalse([item for item in items if item.reason is ReviewReason.NAME_CONFLICT])


class ManualOverrideAndFileReviewTests(unittest.TestCase):
    def test_manual_category_override_outside_a_series_is_flagged(self):
        file = make_raw(0)
        file.manual_override = DestinationCategory.SKIP
        analysis = make_analysis([file], [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        manual = [item for item in items if item.reason is ReviewReason.MANUAL_EDIT]
        self.assertEqual(len(manual), 1)

    def test_review_category_file_is_flagged(self):
        file = make_raw(0, detected_type=DetectedType.CORRUPTED)
        file.destination_category = DestinationCategory.REVIEW
        analysis = make_analysis([file], [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        review = [item for item in items if item.reason is ReviewReason.FILE_NEEDS_REVIEW]
        self.assertEqual(len(review), 1)


class ExtendedClassificationItemTests(unittest.TestCase):
    """Feature 8's soft labels (focus-bracket, probable panorama, incomplete
    bracket) must actually surface in the review queue, not just sit in a
    table column nobody opens the review screen to see."""

    def _labelled_files(self, label: str, evidence: str, count: int = 3) -> list[PhotoFile]:
        files = [make_raw(index) for index in range(count)]
        for file in files:
            file.group_classification = label
            file.group_classification_evidence = [evidence]
        return files

    def test_focus_bracket_label_becomes_one_review_item(self):
        files = self._labelled_files("FOCUS_BRACKET", "focus 0.5 -> 2.0m")
        analysis = make_analysis(files, [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        extended = [item for item in items if item.reason is ReviewReason.EXTENDED_CLASSIFICATION]
        self.assertEqual(len(extended), 1)
        self.assertEqual(len(extended[0].files), 3)

    def test_probable_panorama_label_is_surfaced(self):
        files = self._labelled_files("PROBABLE_PANORAMA", "moderate similarity 0.55")
        analysis = make_analysis(files, [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        extended = [item for item in items if item.reason is ReviewReason.EXTENDED_CLASSIFICATION]
        self.assertEqual(len(extended), 1)

    def test_incomplete_bracket_label_is_surfaced(self):
        files = self._labelled_files("INCOMPLETE_BRACKET", "2 frames, real EV span", count=2)
        analysis = make_analysis(files, [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        extended = [item for item in items if item.reason is ReviewReason.EXTENDED_CLASSIFICATION]
        self.assertEqual(len(extended), 1)

    def test_confident_labels_are_not_surfaced(self):
        """HDR3/5/7/9, pairs, and plain singles are confident enough already;
        flagging them would just be noise."""
        for label in ("HDR3", "SINGLE_RAW", "RAW_HEIC_PAIR", "BURST", "UNKNOWN_GROUP", "UNCLASSIFIED"):
            files = self._labelled_files(label, "n/a")
            analysis = make_analysis(files, [])
            manager = SeriesManager()
            items = build_review_queue(analysis, manager, AppSettings(), "ru")
            extended = [item for item in items if item.reason is ReviewReason.EXTENDED_CLASSIFICATION]
            self.assertEqual(extended, [], f"{label} should not be flagged")

    def test_two_distinct_clusters_with_different_evidence_become_two_items(self):
        cluster_a = self._labelled_files("FOCUS_BRACKET", "focus 0.5 -> 2.0m", count=3)
        cluster_b = self._labelled_files("FOCUS_BRACKET", "focus 1.0 -> 5.0m", count=4)
        analysis = make_analysis(cluster_a + cluster_b, [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        extended = [item for item in items if item.reason is ReviewReason.EXTENDED_CLASSIFICATION]
        self.assertEqual(len(extended), 2)
        sizes = sorted(len(item.files) for item in extended)
        self.assertEqual(sizes, [3, 4])

    def test_summary_includes_the_evidence_text(self):
        files = self._labelled_files("FOCUS_BRACKET", "focus 0.5 -> 2.0m on 3 frames")
        analysis = make_analysis(files, [])
        manager = SeriesManager()
        items = build_review_queue(analysis, manager, AppSettings(), "ru")
        extended = [item for item in items if item.reason is ReviewReason.EXTENDED_CLASSIFICATION][0]
        self.assertIn("focus 0.5 -> 2.0m on 3 frames", extended.summary)


class ActionTests(unittest.TestCase):
    def test_exclude_dissolves_the_series_and_skips_the_files(self):
        manager = SeriesManager()
        files = [make_raw(index) for index in range(3)]
        manager.create_manual(files, 3)
        exclude_files(files, manager)
        self.assertEqual(manager.series, [])
        for file in files:
            self.assertIs(file.destination_category, DestinationCategory.SKIP)
            self.assertFalse(file.selected)

    def test_split_returns_files_to_plain_singles(self):
        manager = SeriesManager()
        files = [make_raw(index) for index in range(3)]
        series = manager.create_manual(files, 3)
        released = split_series(manager, series)
        self.assertEqual(len(released), 3)
        self.assertEqual(manager.series, [])
        for file in released:
            self.assertIsNone(file.series_id)
            self.assertIs(file.destination_category, DestinationCategory.RAW)
            self.assertIn(SPLIT_KEEP_SINGLE_MARKER, file.decision_reasons)

    def test_merge_completes_a_series_with_neighboring_files(self):
        manager = SeriesManager()
        core = [make_raw(index) for index in range(3)]
        series = manager.create_manual(core, 3)
        extra = [make_raw(10), make_raw(11)]
        merged = merge_series(manager, series, extra, target_size=5)
        self.assertEqual(merged.series_size, 5)
        self.assertEqual(len(manager.series), 1)
        for file in core + extra:
            self.assertEqual(file.series_id, merged.series_id)

    def test_merge_with_wrong_combined_size_raises_and_leaves_manager_consistent(self):
        manager = SeriesManager()
        core = [make_raw(index) for index in range(3)]
        series = manager.create_manual(core, 3)
        extra = [make_raw(10)]  # 3 + 1 = 4, not a supported size
        with self.assertRaises(ManualSeriesError):
            merge_series(manager, series, extra, target_size=5)
        # The series was dissolved before validation failed inside create_manual;
        # nothing should be left half-registered in the manager.
        self.assertEqual(manager.series, [])

    def test_restore_automatic_reinstates_a_series_that_still_qualifies(self):
        manager = SeriesManager()
        shutters = (1 / 500, 1 / 125, 1 / 30)
        files = [
            make_raw(index, exposure_time=shutter, exposure_compensation=0.0, f_number=2.8, iso=100)
            for index, shutter in enumerate(shutters)
        ]
        manager.create_manual(files, 3)
        # Overwrite provenance to simulate "this was manual"; restore should hand
        # judgement back to the real detector using files that do form a real bracket.
        result = restore_automatic(manager, manager.series[0], AppSettings())
        self.assertIsNotNone(result)
        self.assertTrue(result.automatic)
        self.assertEqual(len(manager.series), 1)
        for file in result.raw_files:
            self.assertNotIn(SPLIT_KEEP_SINGLE_MARKER, file.decision_reasons)

    def test_restore_automatic_leaves_files_as_singles_when_no_longer_qualifying(self):
        manager = SeriesManager()
        files = [make_raw(index, exposure_compensation=0.0, exposure_time=1 / 125, f_number=2.8, iso=100) for index in range(3)]
        manager.create_manual(files, 3)
        result = restore_automatic(manager, manager.series[0], AppSettings())
        self.assertIsNone(result, "flat exposure across all three frames cannot pass real detection")
        self.assertEqual(manager.series, [])
        for file in files:
            self.assertIsNone(file.series_id)
            self.assertIn(SPLIT_KEEP_SINGLE_MARKER, file.decision_reasons)


class RestoreAutomaticNumberingTests(unittest.TestCase):
    """Regression: restore_automatic appended the detector's freshly numbered
    HDR3_0001 next to the HDR3_0001 that already existed — same series_id, same
    BRACKETING_3/Series_0001 folder, six RAW files in one directory."""

    def _bracket(self, offset: int, minute: int) -> list[PhotoFile]:
        shutters = (1 / 500, 1 / 125, 1 / 30)
        return [
            make_raw(
                offset + index,
                exposure_time=shutter,
                exposure_compensation=0.0,
                f_number=2.8,
                iso=100,
                capture_time=BASE_TIME + timedelta(minutes=minute, seconds=0.4 * index),
            )
            for index, shutter in enumerate(shutters)
        ]

    def test_restored_series_gets_a_unique_id_and_destination_folder(self):
        from app.bracket_detection import BracketDetector

        first = self._bracket(0, 0)
        second = self._bracket(10, 60)
        _candidates, automatic = BracketDetector().detect(first + second, AppSettings())
        self.assertEqual(len(automatic), 2, "fixture must yield two automatic series")
        manager = SeriesManager(automatic)
        manager.sync_destinations()
        manual = manager.create_manual(second, 3)  # the user re-groups the second one by hand
        restored = restore_automatic(manager, manual, AppSettings())
        self.assertIsNotNone(restored)
        self.assertEqual(len(manager.series), 2)
        ids = [series.series_id for series in manager.series]
        roots = [series.destination_root for series in manager.series]
        numbers = [series.series_number for series in manager.series]
        self.assertEqual(len(set(ids)), 2, ids)
        self.assertEqual(len(set(roots)), 2, roots)
        self.assertEqual(len(set(numbers)), 2, numbers)
        destinations = [file.destination_subpath for series in manager.series for file in series.raw_files]
        self.assertEqual(len(set(destinations)), 6, destinations)
        for file in restored.raw_files:
            self.assertEqual(file.series_id, restored.series_id)


class NearMissFloodTests(unittest.TestCase):
    """Regression: a 20-frame flat burst produced 34 near-miss items (every sliding
    3- and 5-frame window at confidence 40) that never cleared, even after the
    user excluded the whole burst."""

    def _flat_burst(self, count: int = 20) -> list[PhotoFile]:
        return [
            make_raw(index, exposure_compensation=0.0, exposure_time=1 / 125, f_number=2.8, iso=100, capture_time=BASE_TIME + timedelta(seconds=0.2 * index))
            for index in range(count)
        ]

    def _bracketed_burst(self, count: int = 20) -> list[PhotoFile]:
        # make_raw's default exposure_compensation cycles -1/0/+1, so every
        # 3-frame window carries real exposure variation.
        return [make_raw(index, capture_time=BASE_TIME + timedelta(seconds=0.2 * index)) for index in range(count)]

    def _windows(self, files: list[PhotoFile], size: int, confidence: float, reasons: list[str] | None = None, first_id: int = 1) -> list[BracketCandidate]:
        candidates = []
        for start in range(len(files) - size + 1):
            window = files[start : start + size]
            candidates.append(
                BracketCandidate(
                    candidate_id=f"CAND_{first_id + start:04d}",
                    files=window,
                    candidate_size=size,
                    start_time=window[0].metadata.capture_time,
                    end_time=window[-1].metadata.capture_time,
                    duration_seconds=0.2 * (size - 1),
                    exposure_values=[],
                    exposure_source="UNKNOWN",
                    estimated_step_ev=None,
                    score=confidence,
                    confidence=confidence,
                    reasons=list(reasons) if reasons is not None else ["BRACKET_DURATION_OK", "BRACKET_ADJACENT_TIME_OK"],
                )
            )
        return candidates

    def _near_miss(self, analysis: AnalysisResult, manager: SeriesManager) -> list:
        return [item for item in build_review_queue(analysis, manager, AppSettings(), "ru") if item.reason is ReviewReason.NEAR_MISS_CANDIDATE]

    def test_floor_is_the_low_confidence_boundary(self):
        self.assertIs(ConfidenceLevel.from_score(NEAR_MISS_MIN_CONFIDENCE), ConfidenceLevel.LOW)
        self.assertIs(ConfidenceLevel.from_score(NEAR_MISS_MIN_CONFIDENCE - 0.01), ConfidenceLevel.REVIEW)

    def test_flat_burst_windows_below_the_floor_are_not_listed(self):
        files = self._flat_burst()
        candidates = self._windows(files, 3, 40.0) + self._windows(files, 5, 40.0, first_id=100)
        self.assertEqual(len(candidates), 34)
        self.assertEqual(self._near_miss(make_analysis(files, [], candidates=candidates), SeriesManager()), [])

    def test_real_exposure_variation_keeps_a_low_confidence_candidate(self):
        files = [make_raw(index) for index in range(3)]  # EC -1/0/+1: a bracket the detector rejected on timing
        candidate = self._windows(files, 3, 22.0, reasons=["BRACKET_DURATION_OK"])[0]
        items = self._near_miss(make_analysis(files, [], candidates=[candidate]), SeriesManager())
        self.assertEqual([item.candidate_id for item in items], ["CAND_0001"])

    def test_overlapping_windows_collapse_to_the_strongest_non_overlapping_ones(self):
        files = self._bracketed_burst()
        candidates = self._windows(files, 3, 50.0)
        candidates[4].confidence = 60.0  # the strongest window must be among the chosen
        items = self._near_miss(make_analysis(files, [], candidates=candidates), SeriesManager())
        self.assertIn("CAND_0005", [item.candidate_id for item in items])
        claimed: set[int] = set()
        for item in items:
            ids = {id(file) for file in item.files}
            self.assertFalse(ids & claimed, "two near-miss items share a frame")
            claimed |= ids
        self.assertGreater(len(items), 1)
        self.assertLessEqual(len(items), len(files) // 3)

    def test_excluding_the_burst_clears_its_near_miss_items(self):
        files = self._bracketed_burst()
        analysis = make_analysis(files, [], candidates=self._windows(files, 3, 50.0))
        manager = SeriesManager()
        self.assertTrue(self._near_miss(analysis, manager))
        exclude_files(files, manager)
        self.assertEqual(self._near_miss(analysis, manager), [])

    def test_a_manual_category_on_one_frame_retires_its_windows(self):
        from app.manual_overrides import assign_category

        files = [make_raw(index) for index in range(3)]
        analysis = make_analysis(files, [], candidates=self._windows(files, 3, 60.0))
        assign_category(files[1], DestinationCategory.RAW)
        self.assertEqual(self._near_miss(analysis, SeriesManager()), [])

    def test_real_classifier_flat_burst_does_not_flood_the_queue(self):
        """The reviewer's trigger end to end: 20 flat frames 0.2 s apart through the real detector."""
        from app.classification import build_analysis_result

        metadata = [file.metadata for file in self._flat_burst()]
        analysis = build_analysis_result(Path("C:/photos"), metadata, AppSettings(), "fp", BASE_TIME)
        manager = SeriesManager.from_analysis(analysis)
        self.assertEqual(self._near_miss(analysis, manager), [])
        exclude_files(list(analysis.files), manager)
        self.assertEqual(self._near_miss(analysis, manager), [])


class AmbiguousPairClearingTests(unittest.TestCase):
    """Regression: ambiguous items were collected from a warning substring alone, so
    'Исключить' (or any manual category) never made them go away."""

    NOTE = "Компаньон RAW + HEIC неоднозначен (разрыв 0.0 балла)."

    def _ambiguous(self, analysis: AnalysisResult, manager: SeriesManager) -> list:
        return [item for item in build_review_queue(analysis, manager, AppSettings(), "ru") if item.reason is ReviewReason.AMBIGUOUS_PAIR]

    def test_excluding_ambiguous_unpaired_files_clears_their_items(self):
        raw = make_raw(0)
        heic = make_rendered(0)
        raw.warnings.append(self.NOTE)
        heic.warnings.append(self.NOTE)
        analysis = make_analysis([raw, heic], [])
        manager = SeriesManager()
        self.assertEqual(len(self._ambiguous(analysis, manager)), 2)
        exclude_files([raw, heic], manager)
        self.assertEqual(self._ambiguous(analysis, manager), [])

    def test_manually_categorised_ambiguous_file_is_not_asked_again(self):
        from app.manual_overrides import assign_category

        raw = make_raw(0)
        raw.warnings.append(self.NOTE)
        assign_category(raw, DestinationCategory.RAW)
        self.assertEqual(self._ambiguous(make_analysis([raw], []), SeriesManager()), [])

    def test_ambiguous_accepted_pair_with_an_excluded_member_is_not_listed(self):
        raw = make_raw(0)
        rendered = make_rendered(0)
        pair = RawHeicPair(
            pair_id="PAIR_0001",
            raw_file=raw,
            rendered_file=rendered,
            score=70.0,
            time_delta=0.2,
            name_similarity=0.9,
            metadata_similarity=0.8,
            preview_similarity=None,
            ambiguous=True,
        )
        analysis = make_analysis([raw, rendered], [], pairs=[pair])
        manager = SeriesManager()
        self.assertEqual(len(self._ambiguous(analysis, manager)), 1)
        exclude_files([rendered], manager)
        self.assertEqual(self._ambiguous(analysis, manager), [])


if __name__ == "__main__":
    unittest.main()
