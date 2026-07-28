"""Session save/restore (.pbsession) — feature 9, 1.2.0.

A session is a snapshot of everything needed to reopen a folder exactly as
the user left it, without ever touching the photos themselves: the source
and output paths, the settings snapshot, a per-file EXIF cache (see
:mod:`app.metadata_cache`, so reopening a large folder does not re-run
ExifTool for files that have not changed), and the manual fixes layered on
top of automatic classification — which series exist, which files got a
manual category override, which files were deselected, and which near-miss
candidates were dismissed from the review queue.

Automatic classification (bracket detection, pairing, extended
classification) is fully deterministic from settings + metadata, so it is
never itself persisted — only the *deltas* the user introduced on top of it,
keyed by each file's relative path, the one identifier that stays stable
across a fresh reanalysis (series ids, candidate ids, and uids are all
regenerated every run and cannot be used to recognize the "same" grouping
later).

This is a plain state snapshot, not an action log: restoring replays no
history. :func:`reconcile_session_state` reconciles a freshly (re)detected
``SeriesManager`` against the saved end state in one pass, going through the
same :class:`app.series_manager.SeriesManager` and
:func:`app.manual_overrides.assign_category` every other manual edit in the
app already uses — this module adds no second place series or category
state could live.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.analysis import ProgressCallback, analyze_folder
from app.enums import DestinationCategory
from app.errors import ManualSeriesError, SessionError
from app.manual_overrides import assign_category
from app.metadata import MetadataReader
from app.metadata_cache import build_cache_entries, load_cache_entries
from app.models import AnalysisResult, AppSettings
from app.review_queue import DISMISS_NOT_BRACKET_MARKER, SPLIT_KEEP_SINGLE_MARKER
from app.series_manager import SeriesManager
from app.settings import settings_from_dict

SESSION_SCHEMA_VERSION = 1
SESSION_FILE_EXTENSION = ".pbsession"


@dataclasses.dataclass(slots=True)
class SavedSeries:
    raw_paths: list[str]
    series_size: int
    automatic: bool
    manually_confirmed: bool
    custom_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SavedSeries":
        return cls(
            raw_paths=[str(path) for path in data.get("raw_paths", [])],
            series_size=int(data["series_size"]),
            automatic=bool(data.get("automatic", True)),
            manually_confirmed=bool(data.get("manually_confirmed", False)),
            custom_name=data.get("custom_name"),
        )


@dataclasses.dataclass(slots=True)
class SessionData:
    schema_version: int
    saved_at: str
    source_root: str
    output_parent: str | None
    settings_snapshot: dict[str, Any]
    fingerprint: str
    analysis_created_at: str
    metadata_cache: list[dict[str, Any]]
    series: list[SavedSeries]
    file_overrides: dict[str, str]
    deselected_paths: list[str]
    dismissed_not_bracket_paths: list[str]
    split_keep_single_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "saved_at": self.saved_at,
            "source_root": self.source_root,
            "output_parent": self.output_parent,
            "settings_snapshot": self.settings_snapshot,
            "fingerprint": self.fingerprint,
            "analysis_created_at": self.analysis_created_at,
            "metadata_cache": self.metadata_cache,
            "series": [series.to_dict() for series in self.series],
            "file_overrides": self.file_overrides,
            "deselected_paths": self.deselected_paths,
            "dismissed_not_bracket_paths": self.dismissed_not_bracket_paths,
            "split_keep_single_paths": self.split_keep_single_paths,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionData":
        schema = int(data.get("schema_version") or 1)
        if schema > SESSION_SCHEMA_VERSION:
            raise SessionError(f"Версия файла сессии ({schema}) новее поддерживаемой ({SESSION_SCHEMA_VERSION}).")
        if "source_root" not in data or not data["source_root"]:
            raise SessionError("В файле сессии отсутствует исходная папка.")
        series_raw = data.get("series", [])
        series = [SavedSeries.from_dict(item) for item in series_raw if isinstance(item, dict)] if isinstance(series_raw, list) else []
        metadata_cache = data.get("metadata_cache", [])
        metadata_cache = [item for item in metadata_cache if isinstance(item, dict)] if isinstance(metadata_cache, list) else []
        file_overrides = data.get("file_overrides", {})
        file_overrides = {str(key): str(value) for key, value in file_overrides.items()} if isinstance(file_overrides, dict) else {}
        settings_snapshot = data.get("settings_snapshot", {})
        settings_snapshot = settings_snapshot if isinstance(settings_snapshot, dict) else {}
        return cls(
            schema_version=SESSION_SCHEMA_VERSION,
            saved_at=str(data.get("saved_at", "")),
            source_root=str(data["source_root"]),
            output_parent=str(data["output_parent"]) if data.get("output_parent") else None,
            settings_snapshot=settings_snapshot,
            fingerprint=str(data.get("fingerprint", "")),
            analysis_created_at=str(data.get("analysis_created_at", "")),
            metadata_cache=metadata_cache,
            series=series,
            file_overrides=file_overrides,
            deselected_paths=_string_list(data.get("deselected_paths")),
            dismissed_not_bracket_paths=_string_list(data.get("dismissed_not_bracket_paths")),
            split_keep_single_paths=_string_list(data.get("split_keep_single_paths")),
        )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def build_session(
    analysis: AnalysisResult,
    manager: SeriesManager,
    settings: AppSettings,
    output_parent: Path | None,
    saved_at: datetime | None = None,
) -> SessionData:
    moment = saved_at or datetime.now()
    saved_series = [
        SavedSeries(
            raw_paths=sorted(file.metadata.relative_path.as_posix() for file in bracket_series.raw_files),
            series_size=bracket_series.series_size,
            automatic=bracket_series.automatic,
            manually_confirmed=bracket_series.manually_confirmed,
            custom_name=bracket_series.custom_name,
        )
        for bracket_series in manager.series
    ]
    file_overrides = {
        file.metadata.relative_path.as_posix(): file.manual_override.value
        for file in analysis.files
        if file.manual_override is not None
    }
    deselected = [file.metadata.relative_path.as_posix() for file in analysis.files if not file.selected]
    dismissed = [
        file.metadata.relative_path.as_posix() for file in analysis.files if DISMISS_NOT_BRACKET_MARKER in file.decision_reasons
    ]
    split_keep_single = [
        file.metadata.relative_path.as_posix() for file in analysis.files if SPLIT_KEEP_SINGLE_MARKER in file.decision_reasons
    ]
    return SessionData(
        schema_version=SESSION_SCHEMA_VERSION,
        saved_at=moment.isoformat(timespec="seconds"),
        source_root=str(analysis.source_root),
        output_parent=str(output_parent) if output_parent else None,
        settings_snapshot=settings.snapshot(),
        fingerprint=analysis.fingerprint,
        analysis_created_at=analysis.created_at.isoformat(timespec="seconds"),
        metadata_cache=build_cache_entries([file.metadata for file in analysis.files]),
        series=saved_series,
        file_overrides=file_overrides,
        deselected_paths=deselected,
        dismissed_not_bracket_paths=dismissed,
        split_keep_single_paths=split_keep_single,
    )


def save_session(session: SessionData, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_session(path: Path) -> SessionData:
    """Never raises anything but :class:`SessionError`. A corrupted or
    unreadable session file must not crash the app or touch any photo — it
    simply cannot be restored."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise SessionError("Файл сессии повреждён.")
        return SessionData.from_dict(data)
    except SessionError:
        raise
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise SessionError("Файл сессии повреждён или имеет неверный формат.", str(exc)) from exc


def restore_session(
    session: SessionData,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    metadata_reader: MetadataReader | None = None,
) -> tuple[AnalysisResult, SeriesManager, AppSettings, list[str]]:
    settings = settings_from_dict(session.settings_snapshot)
    cache = load_cache_entries(session.metadata_cache)
    analysis = analyze_folder(
        Path(session.source_root),
        settings,
        cancel_event,
        progress,
        metadata_reader,
        metadata_cache=cache,
    )
    manager = SeriesManager.from_analysis(analysis)
    warnings = reconcile_session_state(analysis, manager, session)
    return analysis, manager, settings, warnings


def reconcile_session_state(analysis: AnalysisResult, manager: SeriesManager, session: SessionData) -> list[str]:
    """Fold the saved manual fixes onto freshly (re)detected automatic state.

    Never raises: a saved fix that can no longer be applied (a file was
    deleted, renamed, or moved out of the source folder since the session was
    saved) is skipped and reported back as a warning string — restoring a
    session must not fail outright just because the folder changed since.
    """
    warnings: list[str] = []
    file_by_path = {file.metadata.relative_path.as_posix(): file for file in analysis.files}

    saved_by_signature = {tuple(sorted(saved.raw_paths)): saved for saved in session.series}
    claimed_paths = {path for saved in session.series for path in saved.raw_paths}

    # Any currently automatic series whose members were claimed differently
    # (or singled out) by the saved session must be dissolved before the saved
    # groupings are (re)created below, or the two would collide.
    for bracket_series in list(manager.series):
        signature = tuple(sorted(file.metadata.relative_path.as_posix() for file in bracket_series.raw_files))
        if signature in saved_by_signature:
            continue
        if any(path in claimed_paths for path in signature):
            manager.dissolve(bracket_series)

    current_by_signature = {
        tuple(sorted(file.metadata.relative_path.as_posix() for file in bracket_series.raw_files)): bracket_series
        for bracket_series in manager.series
    }
    for signature, saved in saved_by_signature.items():
        target = current_by_signature.get(signature)
        if target is None:
            files = [file_by_path[path] for path in signature if path in file_by_path]
            if len(files) != len(signature):
                warnings.append(f"Сохранённая серия не восстановлена (не хватает файлов): {', '.join(signature)}.")
                continue
            try:
                target = manager.create_manual(files, saved.series_size)
            except ManualSeriesError as exc:
                warnings.append(f"Сохранённая серия не восстановлена: {exc.user_message}")
                continue
        target.manually_confirmed = saved.manually_confirmed
        if saved.custom_name:
            try:
                manager.rename_series(target, saved.custom_name)
            except ManualSeriesError as exc:
                warnings.append(exc.user_message)

    # Files explicitly split back to singles (with no reformation the user
    # confirmed) must stay singles even if a freshly (re)detected automatic
    # series would otherwise happily reclaim them — the split itself was
    # never claimed by any saved series above, so nothing in the loop before
    # this one would have dissolved it.
    for path in session.split_keep_single_paths:
        file = file_by_path.get(path)
        if file is None:
            continue
        manager.dissolve_containing([file])
        if SPLIT_KEEP_SINGLE_MARKER not in file.decision_reasons:
            file.decision_reasons.append(SPLIT_KEEP_SINGLE_MARKER)

    for path, category_value in session.file_overrides.items():
        file = file_by_path.get(path)
        if file is None:
            continue
        # Mirrors app.review_queue.exclude_files: a manual category override
        # always wins over series membership, so drop the file out of any
        # series it was just placed back into before assigning the category.
        manager.dissolve_containing([file])
        try:
            assign_category(file, DestinationCategory(category_value))
        except ValueError:
            warnings.append(f"Неизвестная категория в сохранённой сессии: {category_value}.")

    for path in session.deselected_paths:
        file = file_by_path.get(path)
        if file is not None:
            file.selected = False

    for path in session.dismissed_not_bracket_paths:
        file = file_by_path.get(path)
        if file is not None and DISMISS_NOT_BRACKET_MARKER not in file.decision_reasons:
            file.decision_reasons.append(DISMISS_NOT_BRACKET_MARKER)

    manager.apply_to(analysis)
    return warnings
