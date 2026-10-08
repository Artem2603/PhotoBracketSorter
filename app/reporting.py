"""Atomic report generation."""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from app.constants import REPORT_CSV_NAME, REPORT_JSON_NAME, REPORT_TEXT_NAME
from app.errors import ReportWriteError
from app.i18n import I18n
from app.models import AnalysisResult, AppSettings, BracketSeries, CopyResult


def write_reports(analysis: AnalysisResult, copy_result: CopyResult, settings: AppSettings | None = None) -> None:
    """Write the report formats the user asked for, in the language they asked for."""
    settings = settings or AppSettings()
    formats = {str(name).lower() for name in settings.report_formats}
    try:
        if "json" in formats:
            for series in analysis.bracket_series:
                _write_series_json(copy_result.output_root, series)
            _write_summary_json(analysis, copy_result, settings)
        if "csv" in formats:
            _write_csv(analysis, copy_result, settings)
        if "txt" in formats:
            _write_text(analysis, copy_result, settings)
    except OSError as exc:
        raise ReportWriteError(technical_reason=str(exc)) from exc


def _write_series_json(output_root: Path, series: BracketSeries) -> None:
    if series.destination_root is None:
        return
    path = output_root / series.destination_root / "series.json"
    data = {
        "schema_version": 2,
        "series_id": series.series_id,
        "uid": series.uid,
        "series_number": series.series_number,
        "series_size": series.series_size,
        "automatic": series.automatic,
        "manually_confirmed": series.manually_confirmed,
        "confidence": series.confidence,
        "confidence_level": series.confidence_level.value,
        "exposure_source": series.exposure_source,
        "exposure_values": series.exposure_values,
        "estimated_step_ev": series.estimated_step_ev,
        "reasons": series.reasons,
        "warnings": series.warnings,
        "raw_files": [
            {
                "file_name": file.metadata.file_name,
                "relative_source": file.metadata.relative_path.as_posix(),
                "pair_id": file.pair_id,
                "companion": _companion_name(series, file.metadata.file_name),
            }
            for file in series.raw_files
        ],
    }
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _write_summary_json(analysis: AnalysisResult, copy_result: CopyResult, settings: AppSettings) -> None:
    data = {
        "schema_version": 1,
        "app": "Photo Bracket Sorter",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "analysis_created_at": analysis.created_at.isoformat(timespec="seconds"),
        "copy_started_at": copy_result.started_at.isoformat(timespec="seconds"),
        "copy_finished_at": copy_result.finished_at.isoformat(timespec="seconds"),
        "source_root": str(analysis.source_root) if not settings.privacy_redact_absolute_paths else "<source_root>",
        "output_root": _redact_output_root(copy_result.output_root, settings),
        "statistics": analysis.statistics,
        "operation": copy_result.operation,
        "originals_deleted": copy_result.operation == "move" and copy_result.deleted_sources > 0,
        "processed_files": copy_result.processed_files,
        "planned_files": len(copy_result.operations),
        "copied_files": copy_result.copied_files,
        "deleted_sources": copy_result.deleted_sources,
        "skipped_files": copy_result.skipped_files,
        "skipped_duplicates": copy_result.skipped_duplicates,
        "errors": copy_result.errors,
        "cancelled": copy_result.cancelled,
        "status_counts": _status_counts(copy_result),
        "analysis_fingerprint": analysis.fingerprint,
        "settings": redact_settings_snapshot(analysis.settings_snapshot, settings),
        "report_detail": settings.report_detail,
        # Series are always listed: they are the point of the application, and the
        # list stays small even for large runs.
        "series": [_series_to_dict(series) for series in analysis.bracket_series],
    }
    # compact keeps the summary to aggregates; normal and full add the per-item rows.
    if settings.report_detail != "compact":
        data["files"] = [_file_to_dict(file, settings) for file in analysis.files]
        data["pairs"] = [_pair_to_dict(pair, settings) for pair in analysis.pairs]
        data["operations"] = [_operation_to_dict(operation, settings) for operation in copy_result.operations]
    if settings.report_detail == "full":
        data["warnings"] = list(analysis.warnings)
        data["bracket_candidates"] = [_candidate_to_dict(candidate) for candidate in analysis.bracket_candidates]
    atomic_write_text(copy_result.output_root / REPORT_JSON_NAME, json.dumps(data, ensure_ascii=False, indent=2))


def _candidate_to_dict(candidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_size": candidate.candidate_size,
        "duration_seconds": candidate.duration_seconds,
        "exposure_source": candidate.exposure_source,
        "exposure_values": list(candidate.exposure_values),
        "estimated_step_ev": candidate.estimated_step_ev,
        "score": candidate.score,
        "confidence": candidate.confidence,
        "files": [file.metadata.file_name for file in candidate.files],
        "reasons": list(candidate.reasons),
        "warnings": list(candidate.warnings),
    }


def _write_csv(analysis: AnalysisResult, copy_result: CopyResult, settings: AppSettings) -> None:
    path = copy_result.output_root / REPORT_CSV_NAME
    partial = path.with_name(path.name + ".partial")
    # English headers stay machine-friendly for spreadsheets and scripts; the
    # localized variant is for people reading the file directly.
    header_language = "en" if settings.csv_english_headers else settings.report_language
    translate = I18n(header_language).gettext
    with partial.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                translate("csv.source"),
                translate("csv.destination"),
                translate("csv.category"),
                translate("csv.status"),
                translate("csv.source_deleted"),
                translate("csv.series_id"),
                translate("csv.pair_id"),
                translate("csv.sha256"),
            ]
        )
        for operation in copy_result.operations:
            writer.writerow(
                _csv_safe_row(
                    [
                        operation.relative_source.as_posix(),
                        operation.destination_path.relative_to(copy_result.output_root).as_posix(),
                        operation.category.value,
                        operation.status.value,
                        "yes" if operation.source_deleted else "no",
                        operation.series_id or "",
                        operation.pair_id or "",
                        operation.sha256_destination or "",
                    ]
                )
            )
        writer.writerow([])
        writer.writerow([translate("csv.statistic"), translate("csv.value")])
        for key, value in sorted(analysis.statistics.items()):
            writer.writerow([key, value])
    os.replace(partial, path)


def _write_text(analysis: AnalysisResult, copy_result: CopyResult, settings: AppSettings) -> None:
    stats = analysis.statistics
    translate = I18n(settings.report_language).gettext
    is_move = copy_result.operation == "move"
    counts = _status_counts(copy_result)
    mode_label = translate("report.mode.move" if is_move else "report.mode.copy")
    transferred_label = translate("report.transferred.move" if is_move else "report.transferred.copy")
    lines = [
        translate("report.title.cancelled" if copy_result.cancelled else "report.title.done"),
        "",
        f"{translate('report.mode')}: {mode_label}",
        translate("report.processed_of", done=copy_result.processed_files, total=len(copy_result.operations)),
        f"{transferred_label}: {copy_result.copied_files}",
    ]
    if is_move:
        lines.append(f"{translate('report.deleted')}: {copy_result.deleted_sources}")
        failed_delete = counts.get("DELETE_FAILED", 0)
        if failed_delete:
            lines.append(f"{translate('report.delete_failed')}: {failed_delete}")
    lines.extend(
        [
            f"{translate('report.skipped_unprocessed')}: {copy_result.skipped_files}",
            f"{translate('report.skipped_duplicates')}: {copy_result.skipped_duplicates}",
            f"{translate('report.errors')}: {copy_result.errors}",
            f"{translate('report.series_3')}: {stats.get('series_3', 0)}",
            f"{translate('report.series_5')}: {stats.get('series_5', 0)}",
        ]
    )
    if settings.report_detail == "full":
        lines.append("")
        for status, count in sorted(counts.items()):
            lines.append(f"  {status}: {count}")
    lines.append("")
    # Never claim the originals survived a move.
    if is_move:
        if copy_result.deleted_sources:
            lines.append(translate("report.originals_deleted", count=copy_result.deleted_sources))
        else:
            lines.append(translate("report.originals_kept"))
    else:
        lines.append(translate("report.originals_intact"))
    if copy_result.cancelled:
        lines.append(translate("report.cancelled_note"))
    lines.append("")
    atomic_write_text(copy_result.output_root / REPORT_TEXT_NAME, "\n".join(lines))


def _status_counts(copy_result: CopyResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for operation in copy_result.operations:
        counts[operation.status.value] = counts.get(operation.status.value, 0) + 1
    return counts


#: Settings whose values are absolute filesystem paths. The snapshot is written to
#: the report wholesale, so these leak the user's directory layout unless replaced.
PATH_BEARING_SETTINGS = ("last_source_path", "last_output_path", "exiftool_path")

#: Placeholders substituted for each redacted setting.
REDACTED_PLACEHOLDERS = {
    "last_source_path": "<source_root>",
    "last_output_path": "<output_root>",
    "exiftool_path": "<exiftool_path>",
}


def redact_settings_snapshot(snapshot: dict[str, Any], settings: AppSettings) -> dict[str, Any]:
    """Strip absolute paths from a settings snapshot before it reaches a report."""
    if not settings.privacy_redact_absolute_paths:
        return dict(snapshot)
    redacted = dict(snapshot)
    for key in PATH_BEARING_SETTINGS:
        if redacted.get(key):
            redacted[key] = REDACTED_PLACEHOLDERS[key]
    return redacted


def _redact_output_root(output_root: Path, settings: AppSettings) -> str:
    """Keep only the result folder name, which the user chose and already knows."""
    if not settings.privacy_redact_absolute_paths:
        return str(output_root)
    return output_root.name


def _csv_safe_row(cells: list) -> list:
    """Neutralise spreadsheet formula injection from user-controlled names.

    A file called ``=HYPERLINK(...)`` or ``-cmd|...`` is executed by Excel when
    the CSV is opened; prefixing such cells with an apostrophe makes them text.
    """
    safe = []
    for cell in cells:
        if isinstance(cell, str) and cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
            safe.append("'" + cell)
        else:
            safe.append(cell)
    return safe


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def _operation_to_dict(operation, settings: AppSettings) -> dict[str, Any]:
    source = operation.relative_source.as_posix() if settings.privacy_redact_absolute_paths else str(operation.source_path)
    return {
        "source": source,
        "destination": operation.destination_path.name,
        "category": operation.category.value,
        "status": operation.status.value,
        "expected_size": operation.expected_size,
        "source_deleted": operation.source_deleted,
        "series_id": operation.series_id,
        "pair_id": operation.pair_id,
        "sha256_source": operation.sha256_source,
        "sha256_destination": operation.sha256_destination,
        "error": operation.error,
    }


def _file_to_dict(file, settings: AppSettings) -> dict[str, Any]:
    source = file.metadata.relative_path.as_posix() if settings.privacy_redact_absolute_paths else str(file.metadata.source_path)
    return {
        "source": source,
        "file_name": file.metadata.file_name,
        "category": file.destination_category.value,
        "detected_type": file.metadata.detected_type.value,
        "capture_time": file.metadata.capture_time.isoformat() if file.metadata.capture_time else None,
        "capture_time_raw": file.metadata.capture_time_raw,
        "capture_time_source": file.metadata.capture_time_source.value,
        "timezone_status": file.metadata.timezone_status.value,
        "warnings": list(file.warnings),
        "metadata_warnings": list(file.metadata.warnings),
        "confidence": file.confidence,
        "confidence_level": file.confidence_level.value,
        "pair_id": file.pair_id,
        "series_id": file.series_id,
    }


def _pair_to_dict(pair, settings: AppSettings) -> dict[str, Any]:
    return {
        "pair_id": pair.pair_id,
        "raw_file": _safe_source(pair.raw_file, settings),
        "rendered_file": _safe_source(pair.rendered_file, settings),
        "score": pair.score,
        "time_delta": pair.time_delta,
        "reasons": list(pair.reasons),
        "warnings": list(pair.warnings),
        "ambiguous": pair.ambiguous,
    }


def _series_to_dict(series: BracketSeries) -> dict[str, Any]:
    return {
        "series_id": series.series_id,
        "uid": series.uid,
        "series_size": series.series_size,
        "series_number": series.series_number,
        "automatic": series.automatic,
        "manually_confirmed": series.manually_confirmed,
        "start_time": series.start_time.isoformat() if series.start_time else None,
        "end_time": series.end_time.isoformat() if series.end_time else None,
        "duration_seconds": series.duration_seconds,
        "confidence": series.confidence,
        "confidence_level": series.confidence_level.value,
        "exposure_source": series.exposure_source,
        "exposure_values": list(series.exposure_values),
        "estimated_step_ev": series.estimated_step_ev,
        "raw_files": [file.metadata.file_name for file in series.raw_files],
        "warnings": list(series.warnings),
        "reasons": list(series.reasons),
    }


def _safe_source(file, settings: AppSettings) -> str:
    return file.metadata.relative_path.as_posix() if settings.privacy_redact_absolute_paths else str(file.metadata.source_path)


def _companion_name(series: BracketSeries, raw_name: str) -> str | None:
    companion = series.rendered_companions.get(raw_name)
    return companion.metadata.file_name if companion else None


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
