"""Classification orchestration after metadata loading."""

from __future__ import annotations

from pathlib import Path

from app.bracket_detection import BracketDetector
from app.enums import ConfidenceLevel, DestinationCategory, DetectedType
from app.extended_classification import apply_pair_and_review_labels, classify_extended_groups
from app.format_detection import is_raw_type, is_rendered_type
from app.models import AnalysisResult, AppSettings, BracketSeries, PhotoFile, PhotoMetadata, RawHeicPair
from app.pairing import PairMatcher, split_raw_and_rendered
from app.series_manager import SeriesManager, compute_statistics


class Classifier:
    def __init__(self, pair_matcher: PairMatcher | None = None, bracket_detector: BracketDetector | None = None):
        self.pair_matcher = pair_matcher or PairMatcher()
        self.bracket_detector = bracket_detector or BracketDetector()

    def classify(self, metadata_items: list[PhotoMetadata], settings: AppSettings) -> tuple[list[PhotoFile], list[RawHeicPair], list[object], list[BracketSeries], dict[str, int]]:
        files = [PhotoFile(metadata=item, warnings=list(item.warnings)) for item in metadata_items]
        for file in files:
            self._initial_category(file)
        raw_files, rendered_files = split_raw_and_rendered(files)
        pairs = self.pair_matcher.match(raw_files, rendered_files, settings)
        candidates, series_list = self.bracket_detector.detect(raw_files, settings)
        self._connect_series_companions(series_list, pairs)
        # SeriesManager owns destination layout for series members, so automatic and
        # manual series land in exactly the same place by exactly the same rules.
        # It also owns the one place where ``store_companions_inside_series`` is
        # honoured, so the setting cannot silently stop working again.
        self.series_manager = SeriesManager(series_list, store_companions_inside_series=settings.store_companions_inside_series)
        self.series_manager.sync_destinations()
        self._apply_series_confidence(series_list, settings)
        self._apply_default_destinations(files)
        # Informational only (feature 8): labels PhotoFile.group_classification
        # without touching destination_category, which the lines above already
        # finalized — HDR3/5/7/9, focus-bracket, burst, and "probable panorama"
        # never move a file anywhere by themselves.
        classify_extended_groups(raw_files, settings)
        apply_pair_and_review_labels(files, pairs)
        statistics = compute_statistics(files, series_list)
        return files, pairs, candidates, series_list, statistics

    def _initial_category(self, file: PhotoFile) -> None:
        detected = file.metadata.detected_type
        if detected is DetectedType.CORRUPTED:
            file.destination_category = DestinationCategory.REVIEW
            file.selected = False
            file.confidence = 0.0
            file.confidence_level = ConfidenceLevel.REVIEW
            file.decision_reasons.append("FILE_CORRUPTED")
            return
        if detected is DetectedType.UNSUPPORTED or detected is DetectedType.UNKNOWN:
            file.destination_category = DestinationCategory.SKIP
            file.selected = False
            file.confidence = 0.0
            file.confidence_level = ConfidenceLevel.REVIEW
            file.decision_reasons.append("FILE_UNSUPPORTED")
            return
        if is_rendered_type(detected):
            file.destination_category = DestinationCategory.HEIC
            file.confidence = 75.0
            file.confidence_level = ConfidenceLevel.MEDIUM
            file.decision_reasons.append("RENDERED_FORMAT")
        elif is_raw_type(detected):
            file.destination_category = DestinationCategory.RAW
            file.confidence = 72.0
            file.confidence_level = ConfidenceLevel.MEDIUM
            file.decision_reasons.append("RAW_FORMAT")

    def _connect_series_companions(self, series_list: list[BracketSeries], pairs: list[RawHeicPair]) -> None:
        """Record which rendered file pairs with each series RAW.

        Only the relation is recorded here; whether a companion is *placed*
        inside the series folder is decided by SeriesManager.sync_destinations
        from ``store_companions_inside_series``.
        """
        pair_by_raw = {pair.raw_file.metadata.source_path.resolve(): pair for pair in pairs}
        for series in series_list:
            companions: dict[str, PhotoFile | None] = {}
            for raw_file in series.raw_files:
                pair = pair_by_raw.get(raw_file.metadata.source_path.resolve())
                companions[raw_file.metadata.file_name] = pair.rendered_file if pair else None
            series.rendered_companions = companions

    def _apply_series_confidence(self, series_list: list[BracketSeries], settings: AppSettings) -> None:
        """Score members of automatic series. Paths are already set by SeriesManager."""
        for series in series_list:
            for raw_file in series.raw_files:
                raw_file.confidence = series.confidence
                raw_file.confidence_level = series.confidence_level
                raw_file.decision_reasons.extend(series.reasons)
            if not settings.store_companions_inside_series:
                continue  # companions stay plain HEIC/ files; nothing about them changed
            for companion in series.rendered_companions.values():
                if companion is not None:
                    companion.confidence = min(companion.confidence + 10.0, 95.0)
                    companion.confidence_level = ConfidenceLevel.from_score(companion.confidence)
                    companion.decision_reasons.append("COMPANION_INSIDE_SERIES")

    def _apply_default_destinations(self, files: list[PhotoFile]) -> None:
        for file in files:
            if file.destination_category in {DestinationCategory.REVIEW, DestinationCategory.SKIP}:
                file.destination_subpath = None
                continue
            if file.destination_subpath is None:
                if file.destination_category is DestinationCategory.HEIC:
                    file.destination_subpath = Path("HEIC") / file.metadata.file_name
                elif file.destination_category is DestinationCategory.RAW:
                    file.destination_subpath = Path("RAW") / file.metadata.file_name
            file.confidence_level = ConfidenceLevel.from_score(file.confidence)


def build_analysis_result(
    source_root: Path,
    metadata_items: list[PhotoMetadata],
    settings: AppSettings,
    fingerprint: str,
    created_at,
) -> AnalysisResult:
    classifier = Classifier()
    files, pairs, candidates, series, statistics = classifier.classify(metadata_items, settings)
    warnings = [warning for file in files for warning in file.warnings]
    return AnalysisResult(
        source_root=source_root,
        files=files,
        pairs=pairs,
        bracket_candidates=candidates,
        bracket_series=series,
        statistics=statistics,
        warnings=warnings,
        created_at=created_at,
        settings_snapshot=settings.snapshot(),
        fingerprint=fingerprint,
    )
