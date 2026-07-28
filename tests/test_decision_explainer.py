"""'Why was this decided?' explanations are assembled from real algorithm
output, not a second approximate implementation (feature 4, 1.2.0)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.bracket_detection import BracketDetector
from app.decision_explainer import (
    explain_pair,
    explain_series,
    format_pair_explanation,
    format_series_explanation,
)
from app.enums import DetectedType
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata
from app.pairing import PairMatcher
from app.series_manager import SeriesManager

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def raw_meta(index: int, shutter: float, ec: float = 0.0, **overrides) -> PhotoFile:
    name = f"IMG_{index:04d}.dng"
    fields = dict(
        source_path=Path(f"C:/photos/{name}"),
        relative_path=Path(name),
        file_name=name,
        extension=".dng",
        detected_type=DetectedType.DNG,
        capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
        make="Fujifilm",
        model="X-T5",
        lens_model="XF16-55",
        width=6000,
        height=4000,
        orientation="Horizontal",
        exposure_compensation=ec,
        exposure_time=shutter,
        f_number=2.8,
        iso=100,
        file_size=1000,
    )
    fields.update(overrides)
    return PhotoFile(metadata=PhotoMetadata(**fields))


def rendered_meta(index: int, delta_seconds: float = 0.1, **overrides) -> PhotoFile:
    name = f"IMG_{index:04d}.heic"
    fields = dict(
        source_path=Path(f"C:/photos/{name}"),
        relative_path=Path(name),
        file_name=name,
        extension=".heic",
        detected_type=DetectedType.HEIC,
        capture_time=BASE_TIME + timedelta(seconds=delta_seconds),
        make="Fujifilm",
        model="X-T5",
        lens_model="XF16-55",
        width=6000,
        height=4000,
        orientation="Horizontal",
        exposure_time=1 / 125,
        f_number=2.8,
        iso=100,
        file_size=800,
    )
    fields.update(overrides)
    return PhotoFile(metadata=PhotoMetadata(**fields))


def real_hdr3_series():
    shutters = (1 / 500, 1 / 125, 1 / 30)
    files = [raw_meta(index, shutter, ec=0.0) for index, shutter in enumerate(shutters)]
    _candidates, series = BracketDetector().detect(files, AppSettings())
    return series[0]


def make_analysis(files, series=None, pairs=None):
    return AnalysisResult(
        source_root=Path("C:/photos"),
        files=files,
        pairs=pairs or [],
        bracket_candidates=[],
        bracket_series=series or [],
        statistics={},
        warnings=[],
        created_at=BASE_TIME,
        settings_snapshot=AppSettings().snapshot(),
        fingerprint="fp",
    )


class SeriesExplanationTests(unittest.TestCase):
    def test_score_breakdown_sums_to_the_real_confidence(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertAlmostEqual(explanation.total_score, series.confidence, places=6)
        self.assertAlmostEqual(sum(line.points for line in explanation.score_breakdown), series.confidence, places=6)

    def test_score_breakdown_labels_match_the_series_reasons(self):
        """Every reason the detector recorded must appear as a scored line — the
        explanation is a read of real state, not a re-guess of it."""
        series = real_hdr3_series()
        explanation = explain_series(series)
        breakdown_labels = {line.label for line in explanation.score_breakdown}
        scoring_reasons = {reason for reason in series.reasons if not reason.startswith("BRACKET_EXPOSURE_SOURCE_") and not reason.startswith("BRACKET_EXPOSURE_SKIPPED_")}
        self.assertTrue(scoring_reasons.issubset(breakdown_labels))

    def test_frames_are_in_capture_time_order_with_correct_count(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertEqual(len(explanation.frames), 3)
        times = [frame.capture_time for frame in explanation.frames]
        self.assertEqual(times, sorted(times))

    def test_adjacent_gaps_count_is_one_less_than_frame_count(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertEqual(len(explanation.adjacent_gaps_seconds), len(explanation.frames) - 1)
        for gap in explanation.adjacent_gaps_seconds:
            self.assertAlmostEqual(gap, 0.4, places=6)

    def test_dimensions_match_reflects_the_real_reason_code(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertEqual(explanation.dimensions_match, "BRACKET_DIMENSIONS_OK" in series.reasons)
        self.assertTrue(explanation.dimensions_match)

    def test_lens_match_is_true_when_all_frames_share_a_lens(self):
        series = real_hdr3_series()  # fixture sets the same lens_model on every frame
        explanation = explain_series(series)
        self.assertEqual(explanation.lens_match, "BRACKET_LENS_OK" in series.reasons)

    def test_camera_match_is_always_true_for_a_real_detected_series(self):
        """Different-camera windows are hard-gated before a candidate can even
        exist, so an explained series can never show camera_match=False."""
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertTrue(explanation.camera_match)

    def test_exposure_values_and_step_match_the_series(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertEqual(explanation.exposure_values, tuple(series.exposure_values))
        self.assertEqual(explanation.estimated_step_ev, series.estimated_step_ev)
        self.assertEqual(explanation.exposure_source, "EV100")

    def test_manual_series_is_labelled_as_manual(self):
        files = [raw_meta(index, 1 / 125, ec=float(index)) for index in range(3)]
        manager = SeriesManager()
        series = manager.create_manual(files, 3)
        explanation = explain_series(series)
        self.assertFalse(explanation.automatic)

    def test_review_reasons_are_empty_without_analysis_context(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        self.assertEqual(explanation.review_reasons, ())

    def test_review_reasons_reflect_the_real_review_queue(self):
        files = list(real_hdr3_series().raw_files)
        # Rebuild through a manager so the same series object is tracked live.
        from app.models import BracketSeries
        from app.enums import ConfidenceLevel

        series = BracketSeries(
            series_id="HDR3_0001",
            series_number=1,
            series_size=3,
            raw_files=files,
            rendered_companions={f.metadata.file_name: None for f in files},
            start_time=files[0].metadata.capture_time,
            end_time=files[-1].metadata.capture_time,
            duration_seconds=0.8,
            exposure_values=[-11.9, -9.9, -7.9],
            estimated_step_ev=2.0,
            confidence=50.0,  # LOW, to trigger a real review-queue item
            confidence_level=ConfidenceLevel.LOW,
            automatic=True,
            manually_confirmed=False,
            reasons=["BRACKET_DURATION_OK"],
            score_breakdown=[("BRACKET_DURATION_OK", 22.0)],
        )
        for file in files:
            file.series_id = series.series_id
        manager = SeriesManager([series])
        analysis = make_analysis(files, [series])
        explanation = explain_series(series, analysis, manager, "ru")
        self.assertTrue(explanation.review_reasons)
        self.assertIn("низкая уверенность".split()[0], explanation.review_reasons[0].lower())

    def test_format_series_explanation_produces_readable_text_in_both_languages(self):
        series = real_hdr3_series()
        explanation = explain_series(series)
        ru_text = format_series_explanation(explanation, "ru")
        en_text = format_series_explanation(explanation, "en")
        self.assertIn(series.series_id, ru_text)
        self.assertIn(series.series_id, en_text)
        self.assertIn("BRACKET_DURATION_OK", ru_text)
        self.assertNotEqual(ru_text, en_text)


class PairExplanationTests(unittest.TestCase):
    def _real_pair(self):
        raw = raw_meta(0, 1 / 125, ec=0.0)
        rendered = rendered_meta(0, delta_seconds=0.1)
        pairs = PairMatcher().match([raw], [rendered], AppSettings())
        return pairs[0]

    def test_score_breakdown_sums_to_the_real_pair_score(self):
        pair = self._real_pair()
        explanation = explain_pair(pair)
        self.assertAlmostEqual(sum(line.points for line in explanation.score_breakdown), pair.score, places=6)

    def test_camera_and_dimensions_match_reflect_real_reason_codes(self):
        pair = self._real_pair()
        explanation = explain_pair(pair)
        self.assertEqual(explanation.camera_match, "PAIR_CAMERA_MATCH" in pair.reasons)
        self.assertEqual(explanation.dimensions_match, "PAIR_GEOMETRY_MATCH" in pair.reasons)
        self.assertTrue(explanation.camera_match)

    def test_lens_match_true_when_lens_models_agree(self):
        pair = self._real_pair()
        explanation = explain_pair(pair)
        self.assertTrue(explanation.lens_match)

    def test_ambiguous_flag_is_read_from_the_pair_not_recomputed(self):
        pair = self._real_pair()
        object.__setattr__  # no-op reference to avoid unused import warnings in strict linters
        pair.ambiguous = True
        explanation = explain_pair(pair)
        self.assertTrue(explanation.ambiguous)

    def test_format_pair_explanation_includes_both_file_names(self):
        pair = self._real_pair()
        explanation = explain_pair(pair)
        text = format_pair_explanation(explanation, "ru")
        self.assertIn(pair.raw_file.metadata.file_name, text)
        self.assertIn(pair.rendered_file.metadata.file_name, text)

    def test_review_reasons_reflect_ambiguous_pairs_in_the_queue(self):
        raw = raw_meta(0, 1 / 125, ec=0.0)
        rendered = rendered_meta(0, delta_seconds=0.1)
        from app.models import RawHeicPair

        pair = RawHeicPair(
            pair_id="PAIR_0001",
            raw_file=raw,
            rendered_file=rendered,
            score=70.0,
            time_delta=0.1,
            name_similarity=0.9,
            metadata_similarity=0.8,
            preview_similarity=None,
            ambiguous=True,
            score_breakdown=[("PAIR_TIME_CLOSE", 26.0)],
        )
        analysis = make_analysis([raw, rendered], pairs=[pair])
        manager = SeriesManager()
        explanation = explain_pair(pair, analysis, manager, "ru")
        self.assertTrue(explanation.review_reasons)


if __name__ == "__main__":
    unittest.main()
