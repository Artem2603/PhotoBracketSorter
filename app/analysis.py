"""Top-level analysis workflow."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

from app.classification import build_analysis_result
from app.constants import SORTED_ROOT_PREFIX
from app.errors import AnalysisCancelled, ConfigurationError
from app.logging_setup import configure_logging_level
from app.metadata import MetadataReader
from app.metadata_cache import MetadataCache, merge_metadata, split_cached_paths
from app.models import AnalysisResult, AppSettings
from app.paths import validate_source
from app.time_compare import set_mixed_timezone_safe_mode

ProgressCallback = Callable[[str, int, int], None]


def analyze_folder(
    source_root: Path,
    settings: AppSettings | None = None,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    metadata_reader: MetadataReader | None = None,
    metadata_cache: MetadataCache | None = None,
) -> AnalysisResult:
    settings = settings or AppSettings()
    cancel_event = cancel_event or threading.Event()
    configure_logging_level(settings.log_level)
    set_mixed_timezone_safe_mode(settings.mixed_timezone_safe_mode)
    source = validate_source(source_root)
    _phase(progress, "Проверка папок", 1, 1)
    paths = collect_candidate_paths(source, settings, cancel_event, progress)
    if cancel_event.is_set():
        raise AnalysisCancelled()
    if not paths:
        raise ConfigurationError("В исходной папке нет файлов для анализа.")
    _phase(progress, "Определение форматов", 0, len(paths))
    reader = metadata_reader or MetadataReader.from_settings(settings)

    def metadata_progress(done: int, total: int) -> None:
        _phase(progress, "Чтение метаданных", done, total)

    def exif_progress(done: int, total: int) -> None:
        _phase(progress, "Чтение EXIF", done, total)

    if metadata_cache:
        reused, fresh_paths = split_cached_paths(paths, source, metadata_cache)
    else:
        reused, fresh_paths = {}, paths
    fresh_items = (
        reader.read_many(fresh_paths, source, cancel_event, metadata_progress, settings, exif_progress=exif_progress) if fresh_paths else []
    )
    metadata_items = merge_metadata(paths, reused, fresh_items)
    if cancel_event.is_set():
        raise AnalysisCancelled()
    _phase(progress, "Поиск RAW + HEIC", 1, 1)
    fingerprint = analysis_fingerprint(source, settings, paths)
    result = build_analysis_result(source, metadata_items, settings, fingerprint, datetime.now())
    _phase(progress, "Готово к проверке", len(result.files), len(result.files))
    return result


def collect_candidate_paths(
    source: Path,
    settings: AppSettings,
    cancel_event: threading.Event,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    files: list[Path] = []
    iterator = source.rglob("*") if settings.include_subfolders else source.glob("*")
    allowed_extensions = {extension.lower() for extension in settings.allowed_extensions}
    excluded_patterns = tuple(pattern.lower() for pattern in settings.excluded_folder_names if pattern)
    seen = 0
    for path in iterator:
        if cancel_event.is_set():
            break
        if path.is_dir():
            continue
        relative_parts = path.relative_to(source).parts
        if not settings.include_hidden_files and any(part.startswith(".") for part in relative_parts):
            continue
        if settings.skip_existing_sorted_folders and any(part.startswith(SORTED_ROOT_PREFIX) for part in relative_parts[:-1]):
            continue
        if is_excluded_folder(relative_parts[:-1], excluded_patterns):
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        files.append(path)
        seen += 1
        if seen % 100 == 0:
            _phase(progress, "Сканирование", seen, seen)
    _phase(progress, "Сканирование", len(files), len(files))
    return sorted(files, key=lambda item: str(item.relative_to(source)).lower())


def is_excluded_folder(folder_parts: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    """Match any parent folder name against the user's exclusion patterns.

    Patterns are shell globs (``Sorted_Photos_*``, ``*.lrdata``) matched
    case-insensitively, since Windows folder names are case-insensitive.
    """
    if not patterns:
        return False
    return any(fnmatch(part.lower(), pattern) for part in folder_parts for pattern in patterns)


def analysis_fingerprint(source_root: Path, settings: AppSettings, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(str(source_root.resolve()).encode("utf-8", errors="replace"))
    digest.update(json.dumps(settings.snapshot(), sort_keys=True).encode("utf-8"))
    for path in sorted(paths, key=lambda item: str(item).lower()):
        try:
            stat = path.stat()
            relative = path.relative_to(source_root)
            line = f"{relative.as_posix()}|{stat.st_size}|{stat.st_mtime_ns}\n"
        except OSError:
            line = f"{path.name}|missing|0\n"
        digest.update(line.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _phase(progress: ProgressCallback | None, phase: str, done: int, total: int) -> None:
    if progress:
        progress(phase, done, total)
