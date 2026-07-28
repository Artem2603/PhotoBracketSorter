"""EXIF metadata cache embedded in a saved session (feature 9, 1.2.0).

Re-running ExifTool over every file on every reanalysis is the most
expensive part of reopening a folder. A saved session stores the full
:class:`app.models.PhotoMetadata` already extracted for each file, keyed by
its relative path together with the size and mtime that were true when it
was cached. On restore, a file whose current (size, mtime_ns) still matches
its cache entry reuses that metadata directly; anything new, changed, or
missing from the cache is read fresh through the normal
:class:`app.metadata.MetadataReader` pipeline, exactly as if there were no
cache at all. A single corrupted cache entry only forces that one file back
to a fresh read — it never aborts the whole cache.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Any

from app.enums import CaptureTimeSource, DetectedType, TimezoneStatus
from app.models import PhotoMetadata

#: relative_path -> (cached file_size, cached mtime_ns, cached PhotoMetadata)
MetadataCache = dict[str, tuple[int, int, PhotoMetadata]]


def serialize_metadata(item: PhotoMetadata) -> dict[str, Any]:
    data = dataclasses.asdict(item)
    data["source_path"] = str(item.source_path)
    data["relative_path"] = item.relative_path.as_posix()
    data["detected_type"] = item.detected_type.value
    data["capture_time"] = item.capture_time.isoformat() if item.capture_time else None
    data["capture_time_source"] = item.capture_time_source.value
    data["timezone_status"] = item.timezone_status.value
    return data


def deserialize_metadata(data: dict[str, Any]) -> PhotoMetadata:
    known = {field.name for field in dataclasses.fields(PhotoMetadata)}
    values = {key: value for key, value in data.items() if key in known}
    values["source_path"] = Path(str(values["source_path"]))
    values["relative_path"] = Path(str(values["relative_path"]))
    values["detected_type"] = DetectedType(str(values["detected_type"]))
    capture_time = values.get("capture_time")
    values["capture_time"] = datetime.fromisoformat(capture_time) if capture_time else None
    values["capture_time_source"] = CaptureTimeSource(str(values.get("capture_time_source") or "UNKNOWN"))
    values["timezone_status"] = TimezoneStatus(str(values.get("timezone_status") or "NOT_APPLICABLE"))
    return PhotoMetadata(**values)


def build_cache_entries(items: list[PhotoMetadata]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": item.relative_path.as_posix(),
            "file_size": item.file_size,
            "mtime_ns": item.mtime_ns,
            "metadata": serialize_metadata(item),
        }
        for item in items
    ]


def load_cache_entries(entries: list[dict[str, Any]]) -> MetadataCache:
    cache: MetadataCache = {}
    for entry in entries:
        try:
            relative_path = str(entry["relative_path"])
            file_size = int(entry["file_size"])
            mtime_ns = int(entry["mtime_ns"])
            metadata = deserialize_metadata(entry["metadata"])
        except (KeyError, TypeError, ValueError):
            continue
        cache[relative_path] = (file_size, mtime_ns, metadata)
    return cache


def split_cached_paths(paths: list[Path], source_root: Path, cache: MetadataCache) -> tuple[dict[Path, PhotoMetadata], list[Path]]:
    """Return (metadata reused per absolute path, paths that still need a fresh read)."""
    reused: dict[Path, PhotoMetadata] = {}
    fresh: list[Path] = []
    for path in paths:
        metadata = _cache_hit(path, source_root, cache)
        if metadata is None:
            fresh.append(path)
            continue
        # The cached object may have been serialized under a different absolute
        # root (a session saved from a different machine or a moved drive letter);
        # relative identity is what was actually validated, so re-anchor it here.
        metadata.source_path = path
        metadata.relative_path = _safe_relative(path, source_root)
        reused[path] = metadata
    return reused, fresh


def merge_metadata(paths: list[Path], reused: dict[Path, PhotoMetadata], fresh_items: list[PhotoMetadata]) -> list[PhotoMetadata]:
    """Recombine reused + freshly-read metadata in the same order as ``paths``."""
    fresh_by_path = {item.source_path: item for item in fresh_items}
    merged: list[PhotoMetadata] = []
    for path in paths:
        item = reused.get(path) or fresh_by_path.get(path)
        if item is not None:
            merged.append(item)
    return merged


def _cache_hit(path: Path, source_root: Path, cache: MetadataCache) -> PhotoMetadata | None:
    relative = _safe_relative(path, source_root).as_posix()
    entry = cache.get(relative)
    if entry is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    cached_size, cached_mtime_ns, metadata = entry
    if stat.st_size != cached_size or stat.st_mtime_ns != cached_mtime_ns:
        return None
    return metadata


def _safe_relative(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return Path(path.name)
