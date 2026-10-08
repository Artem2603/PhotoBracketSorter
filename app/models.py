"""Dataclass models shared across the application."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.enums import (
    BracketSizeMode,
    CaptureTimeSource,
    ConfidenceLevel,
    DestinationCategory,
    DetectedType,
    DetectionMode,
    OperationStatus,
    TimezoneStatus,
)


@dataclass(slots=True)
class PhotoMetadata:
    source_path: Path
    relative_path: Path
    file_name: str
    extension: str
    detected_type: DetectedType
    mime_type: str | None = None
    file_type: str | None = None
    file_type_extension: str | None = None
    major_brand: str | None = None
    compatible_brands: str | None = None
    file_size: int = 0
    #: Modification time captured at analysis. Compared before Copy/Move so an edit
    #: that preserved the file size cannot slip through unnoticed.
    mtime_ns: int = 0
    #: Head+tail digest captured at analysis, re-checked before the source is deleted.
    quick_fingerprint: str | None = None
    width: int | None = None
    height: int | None = None
    orientation: str | None = None
    rotation: int | None = None
    make: str | None = None
    model: str | None = None
    software: str | None = None
    lens_model: str | None = None
    lens_id: str | None = None
    focal_length: float | None = None
    focal_length_35mm: float | None = None
    focus_distance: float | None = None
    capture_time: datetime | None = None
    capture_time_raw: str | None = None
    capture_time_source: CaptureTimeSource = CaptureTimeSource.UNKNOWN
    timezone_status: TimezoneStatus = TimezoneStatus.NOT_APPLICABLE
    iso: float | None = None
    exposure_time: float | None = None
    shutter_speed_value: float | None = None
    f_number: float | None = None
    aperture_value: float | None = None
    exposure_compensation: float | None = None
    exposure_program: str | None = None
    brightness_value: float | None = None
    light_value: float | None = None
    baseline_exposure: float | None = None
    white_balance: str | None = None
    color_temperature: float | None = None
    gps: dict[str, float] | None = None
    dng_version: str | None = None
    photometric_interpretation: str | None = None
    compression: str | None = None
    bits_per_sample: str | None = None
    samples_per_pixel: str | None = None
    raw_dimensions: str | None = None
    active_area: str | None = None
    default_crop_size: str | None = None
    preview_flags: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata_errors: list[str] = field(default_factory=list)
    raw_exif_record: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PhotoFile:
    metadata: PhotoMetadata
    pair_id: str | None = None
    series_id: str | None = None
    destination_category: DestinationCategory = DestinationCategory.REVIEW
    destination_subpath: Path | None = None
    confidence: float = 0.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.REVIEW
    decision_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual_override: DestinationCategory | None = None
    selected: bool = True
    copy_status: OperationStatus = OperationStatus.PENDING
    copy_error: str | None = None
    #: Informational only — never drives placement. See app.enums.GroupClassification.
    group_classification: str = "UNCLASSIFIED"
    #: Human-readable evidence for group_classification (for the review queue /
    #: decision explainer), e.g. "focus distance 0.5m -> 2.1m across 4 frames".
    group_classification_evidence: list[str] = field(default_factory=list)

    @property
    def source_path(self) -> Path:
        return self.metadata.source_path

    @property
    def relative_path(self) -> Path:
        return self.metadata.relative_path


@dataclass(slots=True)
class RawHeicPair:
    pair_id: str
    raw_file: PhotoFile
    rendered_file: PhotoFile
    score: float
    time_delta: float | None
    name_similarity: float
    metadata_similarity: float
    preview_similarity: float | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ambiguous: bool = False
    #: (label, points) for every component the scorer actually computed, in the
    #: order it computed them — the source of truth for "why" explanations, not
    #: a re-derivation from ``reasons`` after the fact.
    score_breakdown: list[tuple[str, float]] = field(default_factory=list)


@dataclass(slots=True)
class BracketCandidate:
    candidate_id: str
    files: list[PhotoFile]
    candidate_size: int
    start_time: datetime | None
    end_time: datetime | None
    duration_seconds: float | None
    exposure_values: list[float]
    exposure_source: str
    estimated_step_ev: float | None
    score: float
    confidence: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    preview_similarity: float | None = None
    #: (label, points) for every component the scorer actually computed, in the
    #: order it computed them.
    score_breakdown: list[tuple[str, float]] = field(default_factory=list)


@dataclass(slots=True)
class BracketSeries:
    series_id: str
    series_number: int
    series_size: int
    raw_files: list[PhotoFile]
    rendered_companions: dict[str, PhotoFile | None]
    start_time: datetime | None
    end_time: datetime | None
    duration_seconds: float | None
    exposure_values: list[float]
    estimated_step_ev: float | None
    confidence: float
    confidence_level: ConfidenceLevel
    automatic: bool
    manually_confirmed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    destination_root: Path | None = None
    exposure_source: str = "UNKNOWN"
    #: Stable identity that survives renumbering. ``series_id`` is a display label
    #: and gets rewritten when series are added or dissolved; ``uid`` never does.
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    #: (label, points) from the candidate that became this series, for "why" explanations.
    score_breakdown: list[tuple[str, float]] = field(default_factory=list)
    #: User-chosen folder name override (sort preview, feature 6). ``None`` means
    #: the default ``Series_NNNN``/``Manual_NNNN`` pattern.
    custom_name: str | None = None


@dataclass(slots=True)
class ClassificationDecision:
    file: PhotoFile
    category: DestinationCategory
    confidence: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CopyOperation:
    source_path: Path
    destination_path: Path
    category: DestinationCategory
    relative_source: Path
    expected_size: int
    series_id: str | None = None
    pair_id: str | None = None
    status: OperationStatus = OperationStatus.PENDING
    expected_mtime_ns: int = 0
    expected_fingerprint: str | None = None
    sha256_source: str | None = None
    sha256_destination: str | None = None
    source_deleted: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CopyPlan:
    output_root: Path
    operations: tuple[CopyOperation, ...]
    total_bytes: int
    expected_counts: dict[str, int]
    required_space: int
    warnings: tuple[str, ...]
    unresolved_review: int
    created_at: datetime
    analysis_fingerprint: str


@dataclass(slots=True)
class CopyResult:
    output_root: Path
    operations: list[CopyOperation]
    copied_files: int
    skipped_duplicates: int
    errors: int
    cancelled: bool
    started_at: datetime
    finished_at: datetime
    #: ``"copy"`` or ``"move"``. Reports must never claim originals are untouched
    #: after a move, so they read this rather than assuming.
    operation: str = "copy"
    #: Sources actually unlinked. Non-zero only for a move.
    deleted_sources: int = 0
    #: Operations never attempted, e.g. everything after a mid-run cancel.
    skipped_files: int = 0

    @property
    def processed_files(self) -> int:
        """Operations that were actually attempted.

        SKIPPED marks work that was never started (everything after a cancel),
        so it must not count as processed — a run cancelled after one file
        used to report "4 of 4 processed".
        """
        attempted = {OperationStatus.COMPLETED, OperationStatus.ERROR, OperationStatus.DELETE_FAILED, OperationStatus.CANCELLED}
        return sum(1 for operation in self.operations if operation.status in attempted)


@dataclass(slots=True)
class AnalysisResult:
    source_root: Path
    files: list[PhotoFile]
    pairs: list[RawHeicPair]
    bracket_candidates: list[BracketCandidate]
    bracket_series: list[BracketSeries]
    statistics: dict[str, int | float]
    warnings: list[str]
    created_at: datetime
    settings_snapshot: dict[str, Any]
    fingerprint: str


@dataclass(slots=True)
class AppSettings:
    schema_version: int = 2
    language: str = "ru"
    report_language: str = "ru"
    csv_english_headers: bool = True
    theme: str = "system"
    accent: str = "blue"
    ui_scale: float = 1.0
    #: "simple" shows a 5-step guided workflow (source, output, analyze,
    #: review, copy/move) and hides advanced-only actions and settings tabs.
    #: "advanced" is the full 1.1.1-and-later interface, unchanged.
    ui_mode: str = "advanced"
    remember_paths: bool = True
    last_source_path: str = ""
    last_output_path: str = ""
    remember_window: bool = True
    window_geometry: str = "1440x900"
    window_maximized: bool = False
    detection_mode: DetectionMode = DetectionMode.NORMAL
    bracket_size_mode: BracketSizeMode = BracketSizeMode.BOTH
    include_subfolders: bool = True
    include_hidden_files: bool = False
    skip_existing_sorted_folders: bool = True
    allowed_extensions: tuple[str, ...] = (
        ".3fr",
        ".arw",
        ".cr2",
        ".cr3",
        ".dcr",
        ".dng",
        ".erf",
        ".fff",
        ".heic",
        ".heif",
        ".hif",
        ".iiq",
        ".jpeg",
        ".jpe",
        ".jpg",
        ".kdc",
        ".mef",
        ".mos",
        ".mrw",
        ".nef",
        ".orf",
        ".pef",
        ".png",
        ".raf",
        ".raw",
        ".rw2",
        ".rwl",
        ".sr2",
        ".srf",
        ".tif",
        ".tiff",
        ".x3f",
    )
    excluded_folder_names: tuple[str, ...] = ("Sorted_Photos_*",)
    max_pair_delta_seconds: float = 0.8
    use_name_for_pairing: bool = True
    use_exif_for_pairing: bool = True
    use_preview_for_pairing: bool = False
    pair_accept_score_normal: float = 68.0
    pair_accept_score_strict: float = 78.0
    pair_accept_score_soft: float = 58.0
    pair_second_best_margin: float = 8.0
    max_bracket_adjacent_delta_seconds: float = 1.2
    max_bracket_duration_seconds: float = 6.0
    max_bracket_duration_seconds_3: float = 4.0
    max_bracket_duration_seconds_5: float = 6.0
    #: HDR7/HDR9 are opt-in: rare, and a wider window risks matching unrelated
    #: bursts, so they stay off until the user asks for them.
    enable_hdr7: bool = False
    enable_hdr9: bool = False
    max_bracket_duration_seconds_7: float = 8.0
    max_bracket_duration_seconds_9: float = 10.0
    min_exposure_span_ev: float = 0.7
    exposure_step_tolerance: float = 0.45
    #: Search order for resolving one exposure source per bracket group (see
    #: app/exposure.py). A camera profile can reorder this; the app never mixes
    #: sources regardless of order. Unknown/missing entries fall back safely.
    exposure_source_priority: tuple[str, ...] = (
        "ExposureCompensation",
        "EV100",
        "BaselineExposure",
        "LightValue",
        "BrightnessValue",
    )
    require_exposure_for_auto_series: bool = True
    min_auto_confidence: float = 72.0
    #: Extended classification (feature 8): informational labels only — see
    #: app.enums.GroupClassification and app.extended_classification. Each
    #: detector is independently disable-able and never changes where a file
    #: is physically routed; it only explains what the classifier noticed.
    enable_incomplete_bracket_detection: bool = True
    enable_focus_bracket_detection: bool = True
    enable_burst_detection: bool = True
    enable_panorama_detection: bool = True
    focus_bracket_exposure_tolerance_ev: float = 0.3
    focus_bracket_min_focus_spread_ratio: float = 1.05
    focus_bracket_visual_similarity_min: float = 0.55
    burst_visual_similarity_threshold: float = 0.85
    panorama_min_frames: int = 3
    panorama_visual_similarity_min: float = 0.30
    panorama_visual_similarity_max: float = 0.80
    store_companions_inside_series: bool = True
    preserve_source_subfolders: bool = False
    duplicate_behavior: str = "rename"
    output_folder_template: str = "Sorted_Photos_{date}_{time}"
    preserve_file_times: bool = True
    preview_only: bool = False
    require_plan_before_operation: bool = True
    preview_similarity_enabled: bool = False
    visual_similarity_threshold: float = 0.85
    mixed_timezone_safe_mode: bool = True
    thumbnail_size: int = 360
    preview_cache_items: int = 128
    exiftool_batch_size: int = 200
    report_formats: tuple[str, ...] = ("json", "csv", "txt")
    report_detail: str = "normal"
    exiftool_path: str = ""
    log_level: str = "INFO"
    verify_sha256: bool = True
    privacy_redact_absolute_paths: bool = True
    worker_count: int = 4
    column_widths: dict[str, int] = field(default_factory=dict)
    column_order: tuple[str, ...] = (
        "selected",
        "icon",
        "file",
        "category",
        "confidence",
        "type",
        "time",
        "size",
        "series",
        "warnings",
    )

    def pair_threshold(self) -> float:
        if self.detection_mode is DetectionMode.STRICT:
            return self.pair_accept_score_strict
        if self.detection_mode is DetectionMode.SOFT:
            return self.pair_accept_score_soft
        return self.pair_accept_score_normal

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "report_language": self.report_language,
            "csv_english_headers": self.csv_english_headers,
            "theme": self.theme,
            "accent": self.accent,
            "ui_scale": self.ui_scale,
            "ui_mode": self.ui_mode,
            "remember_paths": self.remember_paths,
            "last_source_path": self.last_source_path,
            "last_output_path": self.last_output_path,
            "remember_window": self.remember_window,
            "window_geometry": self.window_geometry,
            "window_maximized": self.window_maximized,
            "detection_mode": self.detection_mode.value,
            "bracket_size_mode": self.bracket_size_mode.value,
            "include_subfolders": self.include_subfolders,
            "include_hidden_files": self.include_hidden_files,
            "skip_existing_sorted_folders": self.skip_existing_sorted_folders,
            "allowed_extensions": list(self.allowed_extensions),
            "excluded_folder_names": list(self.excluded_folder_names),
            "max_pair_delta_seconds": self.max_pair_delta_seconds,
            "use_name_for_pairing": self.use_name_for_pairing,
            "use_exif_for_pairing": self.use_exif_for_pairing,
            "use_preview_for_pairing": self.use_preview_for_pairing,
            "pair_accept_score_normal": self.pair_accept_score_normal,
            "pair_accept_score_strict": self.pair_accept_score_strict,
            "pair_accept_score_soft": self.pair_accept_score_soft,
            "pair_second_best_margin": self.pair_second_best_margin,
            "max_bracket_adjacent_delta_seconds": self.max_bracket_adjacent_delta_seconds,
            "max_bracket_duration_seconds": self.max_bracket_duration_seconds,
            "max_bracket_duration_seconds_3": self.max_bracket_duration_seconds_3,
            "max_bracket_duration_seconds_5": self.max_bracket_duration_seconds_5,
            "enable_hdr7": self.enable_hdr7,
            "enable_hdr9": self.enable_hdr9,
            "max_bracket_duration_seconds_7": self.max_bracket_duration_seconds_7,
            "max_bracket_duration_seconds_9": self.max_bracket_duration_seconds_9,
            "min_exposure_span_ev": self.min_exposure_span_ev,
            "exposure_step_tolerance": self.exposure_step_tolerance,
            "exposure_source_priority": list(self.exposure_source_priority),
            "require_exposure_for_auto_series": self.require_exposure_for_auto_series,
            "min_auto_confidence": self.min_auto_confidence,
            "enable_incomplete_bracket_detection": self.enable_incomplete_bracket_detection,
            "enable_focus_bracket_detection": self.enable_focus_bracket_detection,
            "enable_burst_detection": self.enable_burst_detection,
            "enable_panorama_detection": self.enable_panorama_detection,
            "focus_bracket_exposure_tolerance_ev": self.focus_bracket_exposure_tolerance_ev,
            "focus_bracket_min_focus_spread_ratio": self.focus_bracket_min_focus_spread_ratio,
            "focus_bracket_visual_similarity_min": self.focus_bracket_visual_similarity_min,
            "burst_visual_similarity_threshold": self.burst_visual_similarity_threshold,
            "panorama_min_frames": self.panorama_min_frames,
            "panorama_visual_similarity_min": self.panorama_visual_similarity_min,
            "panorama_visual_similarity_max": self.panorama_visual_similarity_max,
            "store_companions_inside_series": self.store_companions_inside_series,
            "preserve_source_subfolders": self.preserve_source_subfolders,
            "duplicate_behavior": self.duplicate_behavior,
            "output_folder_template": self.output_folder_template,
            "preserve_file_times": self.preserve_file_times,
            "preview_only": self.preview_only,
            "require_plan_before_operation": self.require_plan_before_operation,
            "preview_similarity_enabled": self.preview_similarity_enabled,
            "visual_similarity_threshold": self.visual_similarity_threshold,
            "mixed_timezone_safe_mode": self.mixed_timezone_safe_mode,
            "thumbnail_size": self.thumbnail_size,
            "preview_cache_items": self.preview_cache_items,
            "exiftool_batch_size": self.exiftool_batch_size,
            "report_formats": list(self.report_formats),
            "report_detail": self.report_detail,
            "exiftool_path": self.exiftool_path,
            "log_level": self.log_level,
            "verify_sha256": self.verify_sha256,
            "privacy_redact_absolute_paths": self.privacy_redact_absolute_paths,
            "worker_count": self.worker_count,
            "column_widths": dict(self.column_widths),
            "column_order": list(self.column_order),
        }
