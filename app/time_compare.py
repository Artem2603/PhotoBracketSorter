"""Safe datetime comparison helpers.

The app intentionally does not treat the current Windows timezone as truth for
camera files. Mixed aware/naive comparisons use the aware value's fixed UTC
offset only for this comparison and emit a stable warning code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timezone

from app.models import PhotoFile, PhotoMetadata

MIXED_TIMEZONE_ASSUMED_OFFSET = "MIXED_TIMEZONE_ASSUMED_OFFSET"
TIME_COMPARISON_UNAVAILABLE = "TIME_COMPARISON_UNAVAILABLE"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TimeComparisonResult:
    seconds: float | None
    method: str
    warning_code: str | None = None
    left_normalized: datetime | None = None
    right_normalized: datetime | None = None
    detail: str = ""

    @property
    def absolute_seconds(self) -> float | None:
        return abs(self.seconds) if self.seconds is not None else None


def is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def normalize_datetime_for_comparison(
    value: datetime | None,
    assumed_offset_source: datetime | None = None,
) -> tuple[datetime | None, str, str | None]:
    if value is None:
        return None, "missing", TIME_COMPARISON_UNAVAILABLE
    if is_aware(value):
        return value.astimezone(UTC), "aware_to_utc", None
    if assumed_offset_source is not None and is_aware(assumed_offset_source):
        offset = assumed_offset_source.utcoffset()
        if offset is None:
            return value, "naive_local_unspecified", None
        assumed = value.replace(tzinfo=timezone(offset)).astimezone(UTC)
        return assumed, "naive_assumed_peer_offset_to_utc", MIXED_TIMEZONE_ASSUMED_OFFSET
    return value, "naive_local_unspecified", None


def safe_datetime_difference_seconds(
    left: datetime | None,
    right: datetime | None,
    *,
    left_name: str = "<left>",
    right_name: str = "<right>",
    left_raw: str | None = None,
    right_raw: str | None = None,
    left_source: str | None = None,
    right_source: str | None = None,
) -> TimeComparisonResult:
    if left is None or right is None:
        result = TimeComparisonResult(
            seconds=None,
            method="missing_time",
            warning_code=TIME_COMPARISON_UNAVAILABLE,
            detail="One or both datetime values are missing.",
        )
        _log_time_comparison(left, right, result, left_name, right_name, left_raw, right_raw, left_source, right_source)
        return result

    left_aware = is_aware(left)
    right_aware = is_aware(right)
    if left_aware and right_aware:
        left_norm, _left_method, _left_warning = normalize_datetime_for_comparison(left)
        right_norm, _right_method, _right_warning = normalize_datetime_for_comparison(right)
        method = "both_aware_utc"
        warning = None
    elif not left_aware and not right_aware:
        left_norm, _left_method, _left_warning = normalize_datetime_for_comparison(left)
        right_norm, _right_method, _right_warning = normalize_datetime_for_comparison(right)
        method = "both_naive_local_unspecified"
        warning = None
    elif not _safe_mode:
        # Mixed awareness with safe mode off: refuse rather than assume an offset.
        result = TimeComparisonResult(
            seconds=None,
            method="mixed_refused_safe_mode_off",
            warning_code=TIME_COMPARISON_UNAVAILABLE,
            detail="Mixed offset-aware and naive values, and mixed_timezone_safe_mode is disabled.",
        )
        _log_time_comparison(left, right, result, left_name, right_name, left_raw, right_raw, left_source, right_source)
        return result
    elif left_aware and not right_aware:
        left_norm, _left_method, _left_warning = normalize_datetime_for_comparison(left)
        right_norm, _right_method, warning = normalize_datetime_for_comparison(right, left)
        method = "mixed_right_naive_assumed_left_offset"
    else:
        left_norm, _left_method, warning = normalize_datetime_for_comparison(left, right)
        right_norm, _right_method, _right_warning = normalize_datetime_for_comparison(right)
        method = "mixed_left_naive_assumed_right_offset"

    if left_norm is None or right_norm is None:
        result = TimeComparisonResult(
            seconds=None,
            method=method,
            warning_code=warning or TIME_COMPARISON_UNAVAILABLE,
            left_normalized=left_norm,
            right_normalized=right_norm,
            detail="Normalization returned an empty datetime.",
        )
        _log_time_comparison(left, right, result, left_name, right_name, left_raw, right_raw, left_source, right_source)
        return result

    try:
        seconds = (right_norm - left_norm).total_seconds()
    except TypeError as exc:
        result = TimeComparisonResult(
            seconds=None,
            method=method,
            warning_code=warning or TIME_COMPARISON_UNAVAILABLE,
            left_normalized=left_norm,
            right_normalized=right_norm,
            detail=str(exc),
        )
        _log_time_comparison(left, right, result, left_name, right_name, left_raw, right_raw, left_source, right_source)
        return result

    result = TimeComparisonResult(
        seconds=seconds,
        method=method,
        warning_code=warning,
        left_normalized=left_norm,
        right_normalized=right_norm,
    )
    _log_time_comparison(left, right, result, left_name, right_name, left_raw, right_raw, left_source, right_source)
    return result


#: When safe mode is off, a mixed aware/naive comparison is refused outright instead
#: of borrowing the peer's UTC offset. Set from ``mixed_timezone_safe_mode``.
_safe_mode = True


def set_mixed_timezone_safe_mode(enabled: bool) -> None:
    """Toggle the assumed-offset fallback for mixed aware/naive comparisons.

    Safe mode (the default) borrows the peer's offset and records
    ``MIXED_TIMEZONE_ASSUMED_OFFSET``, so grouping still works across cameras that
    disagree about timezones. With it off, such comparisons yield no result at all,
    which suits archives where a wrong grouping is worse than no grouping.
    """
    global _safe_mode
    _safe_mode = bool(enabled)


def mixed_timezone_safe_mode() -> bool:
    return _safe_mode


def safe_file_time_difference_seconds(left: PhotoFile, right: PhotoFile) -> TimeComparisonResult:
    return safe_metadata_time_difference_seconds(left.metadata, right.metadata)


def safe_metadata_time_difference_seconds(left: PhotoMetadata, right: PhotoMetadata) -> TimeComparisonResult:
    return safe_datetime_difference_seconds(
        left.capture_time,
        right.capture_time,
        left_name=left.file_name,
        right_name=right.file_name,
        left_raw=left.capture_time_raw,
        right_raw=right.capture_time_raw,
        left_source=left.capture_time_source.value,
        right_source=right.capture_time_source.value,
    )


def add_time_warning(target: PhotoFile | PhotoMetadata, warning_code: str | None) -> None:
    if not warning_code:
        return
    warnings = target.warnings
    if warning_code not in warnings:
        warnings.append(warning_code)


def time_sort_key(value: datetime | None) -> tuple[int, str, str]:
    if value is None:
        return (1, "", "")
    if is_aware(value):
        return (0, value.astimezone(UTC).isoformat(timespec="microseconds"), "aware_utc")
    return (0, value.isoformat(timespec="microseconds"), "naive_local_unspecified")


def file_time_sort_key(file: PhotoFile) -> tuple[int, str, str, str]:
    return (*time_sort_key(file.metadata.capture_time), file.metadata.file_name.lower())


def sort_photo_files_by_capture_time(files: list[PhotoFile]) -> list[PhotoFile]:
    with_time = [file for file in files if file.metadata.capture_time is not None]
    has_aware = any(is_aware(file.metadata.capture_time) for file in with_time if file.metadata.capture_time is not None)
    has_naive = any(not is_aware(file.metadata.capture_time) for file in with_time if file.metadata.capture_time is not None)
    if has_aware and has_naive:
        for file in with_time:
            add_time_warning(file, MIXED_TIMEZONE_ASSUMED_OFFSET)
        logger.warning(
            "mixed timezone sort using wall-clock order for candidate grouping files=%s",
            [file.metadata.file_name for file in with_time],
        )
        return sorted(files, key=_wall_clock_file_time_sort_key)
    return sorted(files, key=file_time_sort_key)


def _wall_clock_file_time_sort_key(file: PhotoFile) -> tuple[int, str, str]:
    value = file.metadata.capture_time
    if value is None:
        return (1, "", file.metadata.file_name.lower())
    return (0, value.replace(tzinfo=None).isoformat(timespec="microseconds"), file.metadata.file_name.lower())


def _log_time_comparison(
    left: datetime | None,
    right: datetime | None,
    result: TimeComparisonResult,
    left_name: str,
    right_name: str,
    left_raw: str | None,
    right_raw: str | None,
    left_source: str | None,
    right_source: str | None,
) -> None:
    level = logging.WARNING if result.warning_code else logging.DEBUG
    logger.log(
        level,
        "time comparison left=%s right=%s left_time=%r right_time=%r left_raw=%r right_raw=%r "
        "left_source=%s right_source=%s method=%s seconds=%r warning=%s detail=%s",
        left_name,
        right_name,
        left,
        right,
        left_raw,
        right_raw,
        left_source,
        right_source,
        result.method,
        result.seconds,
        result.warning_code,
        result.detail,
    )
