"""Capture time extraction and parsing."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.enums import CaptureTimeSource, TimezoneStatus

EXIF_PRIORITIES: tuple[tuple[str, CaptureTimeSource], ...] = (
    ("SubSecDateTimeOriginal", CaptureTimeSource.SUBSEC_DATE_TIME_ORIGINAL),
    ("DateTimeOriginal", CaptureTimeSource.DATE_TIME_ORIGINAL),
    ("SubSecCreateDate", CaptureTimeSource.SUBSEC_CREATE_DATE),
    ("CreateDate", CaptureTimeSource.CREATE_DATE),
    ("MediaCreateDate", CaptureTimeSource.MEDIA_CREATE_DATE),
    ("TrackCreateDate", CaptureTimeSource.TRACK_CREATE_DATE),
    ("ModifyDate", CaptureTimeSource.MODIFY_DATE),
)

FILENAME_PATTERNS = (
    re.compile(r"(?P<date>20\d{2}[01]\d[0-3]\d)[_\- ]?(?P<time>[0-2]\d[0-5]\d[0-5]\d)(?P<frac>[._-]\d{1,6})?"),
    re.compile(r"(?P<date>20\d{2}[-_.][01]\d[-_.][0-3]\d)[_\- ](?P<time>[0-2]\d[-_.][0-5]\d[-_.][0-5]\d)(?P<frac>[._-]\d{1,6})?"),
)


def extract_capture_time(record: dict[str, Any], path: Path) -> tuple[datetime | None, str | None, CaptureTimeSource, TimezoneStatus, list[str]]:
    warnings: list[str] = []
    for key, source in EXIF_PRIORITIES:
        raw = record.get(key)
        if raw:
            parsed, status = parse_datetime_value(str(raw))
            if parsed is not None:
                return parsed, str(raw), source, status, warnings
            warnings.append(f"Не удалось разобрать время {key}.")
    filename_time = parse_datetime_from_name(path.name)
    if filename_time is not None:
        warnings.append("Точное время съёмки отсутствует; использовано время из имени файла.")
        return filename_time, path.name, CaptureTimeSource.FILE_NAME, TimezoneStatus.LOCAL_UNKNOWN, warnings
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        warnings.append("Точное время съёмки отсутствует; использовано время файла.")
        return mtime, str(path.stat().st_mtime), CaptureTimeSource.FILE_MTIME, TimezoneStatus.LOCAL_UNKNOWN, warnings
    except OSError as exc:
        warnings.append(f"Не удалось получить файловое время: {exc}")
    return None, None, CaptureTimeSource.UNKNOWN, TimezoneStatus.NOT_APPLICABLE, warnings


def parse_datetime_value(value: str) -> tuple[datetime | None, TimezoneStatus]:
    text = value.strip()
    if not text:
        return None, TimezoneStatus.NOT_APPLICABLE
    normalized = text.replace("T", " ")
    normalized = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", normalized)
    normalized = normalized.replace("Z", "+00:00")
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
    formats = (
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(normalized, fmt)
            if parsed.tzinfo is not None:
                status = TimezoneStatus.UTC_MARKED if normalized.endswith("+00:00") else TimezoneStatus.OFFSET_PRESENT
            else:
                status = TimezoneStatus.LOCAL_UNKNOWN
            return parsed, status
        except ValueError:
            continue
    return None, TimezoneStatus.NOT_APPLICABLE


def parse_datetime_from_name(file_name: str) -> datetime | None:
    for pattern in FILENAME_PATTERNS:
        match = pattern.search(file_name)
        if match:
            date_text = re.sub(r"\D", "", match.group("date"))
            time_text = re.sub(r"\D", "", match.group("time"))
            frac_text = match.groupdict().get("frac") or ""
            microsecond = 0
            if frac_text:
                digits = re.sub(r"\D", "", frac_text)[:6]
                microsecond = int(digits.ljust(6, "0"))
            try:
                return datetime(
                    int(date_text[0:4]),
                    int(date_text[4:6]),
                    int(date_text[6:8]),
                    int(time_text[0:2]),
                    int(time_text[2:4]),
                    int(time_text[4:6]),
                    microsecond,
                )
            except ValueError:
                return None
    return None
