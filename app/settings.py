"""Settings persistence, migration, and validation."""

from __future__ import annotations

import json
import os
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any

from app.enums import BracketSizeMode, DetectionMode
from app.errors import SettingsError
from app.i18n import detect_default_language
from app.models import AppSettings

CURRENT_SETTINGS_SCHEMA = 2
LANGUAGES = {"ru", "en"}
THEMES = {"system", "light", "dark"}
UI_MODES = {"simple", "advanced"}
ACCENTS = {"blue", "green", "purple", "orange"}
DUPLICATE_BEHAVIORS = {"rename", "skip", "overwrite"}
REPORT_DETAILS = {"compact", "normal", "full"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
#: Settings that describe this machine or this user's UI, never an analysis.
#: A restored session must not carry them over from the machine it was saved on.
MACHINE_PREFERENCE_KEYS = frozenset(
    {
        "schema_version",
        "language",
        "report_language",
        "theme",
        "accent",
        "ui_scale",
        "ui_mode",
        "remember_paths",
        "last_source_path",
        "last_output_path",
        "remember_window",
        "window_geometry",
        "window_maximized",
        "column_widths",
        "column_order",
        "exiftool_path",
        "log_level",
        "worker_count",
        "thumbnail_size",
        "preview_cache_items",
        "exiftool_batch_size",
    }
)


def default_settings_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "PhotoBracketSorter" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    """Read settings from disk.

    Raises :class:`SettingsError` for every kind of damage (invalid JSON, a
    UTF-8 BOM from Notepad is tolerated, wrong types, unknown enum values,
    out-of-range numbers) so callers have a single exception to handle.
    """
    settings_path = path or default_settings_path()
    if not settings_path.exists():
        return AppSettings()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SettingsError("Файл настроек поврежден.", str(exc)) from exc
    if not isinstance(data, dict):
        raise SettingsError("Файл настроек поврежден.")
    return settings_from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    validate_settings(settings)
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    # Written to a sibling file first and swapped in atomically: the settings are
    # saved on every folder pick and on close, and a crash or power loss mid-write
    # used to leave a truncated file that then blocked the next start.
    partial = settings_path.with_name(settings_path.name + ".partial")
    partial.write_text(json.dumps(settings.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, settings_path)


def settings_from_dict(data: dict[str, Any]) -> AppSettings:
    migrated = migrate_settings_dict(data)
    allowed = {field.name for field in fields(AppSettings)}
    values = {key: value for key, value in migrated.items() if key in allowed}
    defaults = AppSettings()
    try:
        # Coerce scalars first so a hand-edited "worker_count": "4" or
        # "verify_sha256": "false" loads instead of failing later with a TypeError
        # (or, worse, a truthy string passing validation).
        for key in list(values):
            default = getattr(defaults, key)
            if isinstance(default, Enum):
                continue
            if isinstance(default, bool):
                values[key] = _coerce_bool(values[key], key)
            elif isinstance(default, int):
                values[key] = int(values[key])
            elif isinstance(default, float):
                values[key] = float(values[key])
            elif isinstance(default, str):
                values[key] = str(values[key])
        if "detection_mode" in values:
            values["detection_mode"] = DetectionMode(str(values["detection_mode"]).upper())
        if "bracket_size_mode" in values:
            values["bracket_size_mode"] = BracketSizeMode(str(values["bracket_size_mode"]).upper())
        for key in ("allowed_extensions", "excluded_folder_names", "report_formats", "column_order", "exposure_source_priority"):
            if key in values and not isinstance(values[key], tuple):
                raw = values[key]
                if isinstance(raw, str):
                    raw = [part.strip() for part in raw.split(",") if part.strip()]
                values[key] = tuple(str(item) for item in raw)
        if "column_widths" in values:
            raw_widths = values["column_widths"]
            values["column_widths"] = {str(key): int(value) for key, value in raw_widths.items()} if isinstance(raw_widths, dict) else {}
        settings = AppSettings(**values)
    except (TypeError, ValueError, AttributeError) as exc:
        raise SettingsError("Файл настроек содержит некорректное значение.", str(exc)) from exc
    settings.schema_version = CURRENT_SETTINGS_SCHEMA
    validate_settings(settings)
    return settings


def merge_session_settings(current: AppSettings, restored: AppSettings) -> AppSettings:
    """Analysis settings from a session, machine/UI preferences from ``current``."""
    merged = restored.snapshot()
    local = current.snapshot()
    for key in MACHINE_PREFERENCE_KEYS:
        if key in local:
            merged[key] = local[key]
    return settings_from_dict(merged)


def _coerce_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError(f"{key}: ожидалось true/false, получено {value!r}")


def migrate_settings_dict(data: dict[str, Any]) -> dict[str, Any]:
    try:
        schema = int(data.get("schema_version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Файл настроек содержит некорректную версию схемы.", str(exc)) from exc
    if schema > CURRENT_SETTINGS_SCHEMA:
        raise SettingsError(f"Версия настроек {schema} новее поддерживаемой {CURRENT_SETTINGS_SCHEMA}.")
    defaults = AppSettings().snapshot()
    migrated = dict(defaults)
    migrated.update(data)
    if schema <= 1:
        # Derive from ``data`` (what the old file actually had), not from
        # ``migrated``: defaults were merged in above, so setdefault() on the
        # merged dict never fires and the migration used to be a no-op.
        legacy_duration = _as_float(data.get("max_bracket_duration_seconds"), 6.0)
        if "language" not in data:
            migrated["language"] = _detect_language_from_locale()
        if "report_language" not in data:
            migrated["report_language"] = migrated["language"]
        if "max_bracket_duration_seconds_3" not in data:
            migrated["max_bracket_duration_seconds_3"] = min(legacy_duration, 4.0)
        if "max_bracket_duration_seconds_5" not in data:
            migrated["max_bracket_duration_seconds_5"] = legacy_duration
    migrated["schema_version"] = CURRENT_SETTINGS_SCHEMA
    return migrated


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def validate_settings(settings: AppSettings) -> None:
    _choice(settings.language, LANGUAGES, "language")
    _choice(settings.report_language, LANGUAGES, "report_language")
    _choice(settings.theme, THEMES, "theme")
    _choice(settings.ui_mode, UI_MODES, "ui_mode")
    _choice(settings.accent, ACCENTS, "accent")
    _choice(settings.duplicate_behavior, DUPLICATE_BEHAVIORS, "duplicate_behavior")
    _choice(settings.report_detail, REPORT_DETAILS, "report_detail")
    _choice(str(settings.log_level).upper(), LOG_LEVELS, "log_level")
    _range(settings.ui_scale, 0.9, 1.5, "ui_scale")
    _range(settings.max_pair_delta_seconds, 0.05, 30.0, "RAW + HEIC interval")
    _range(settings.pair_accept_score_normal, 1.0, 100.0, "normal pair score")
    _range(settings.pair_accept_score_strict, 1.0, 100.0, "strict pair score")
    _range(settings.pair_accept_score_soft, 1.0, 100.0, "soft pair score")
    _range(settings.pair_second_best_margin, 0.0, 50.0, "pair margin")
    _range(settings.max_bracket_adjacent_delta_seconds, 0.05, 30.0, "adjacent interval")
    _range(settings.max_bracket_duration_seconds, settings.max_bracket_adjacent_delta_seconds, 300.0, "duration")
    _range(settings.max_bracket_duration_seconds_3, settings.max_bracket_adjacent_delta_seconds, 300.0, "duration 3")
    _range(settings.max_bracket_duration_seconds_5, settings.max_bracket_adjacent_delta_seconds, 300.0, "duration 5")
    _range(settings.max_bracket_duration_seconds_7, settings.max_bracket_adjacent_delta_seconds, 300.0, "duration 7")
    _range(settings.max_bracket_duration_seconds_9, settings.max_bracket_adjacent_delta_seconds, 300.0, "duration 9")
    _range(settings.min_exposure_span_ev, 0.05, 4.0, "min EV")
    _range(settings.exposure_step_tolerance, 0.05, 3.0, "step tolerance")
    _range(settings.min_auto_confidence, 1.0, 100.0, "min auto confidence")
    _range(settings.focus_bracket_exposure_tolerance_ev, 0.01, 3.0, "focus bracket exposure tolerance")
    _range(settings.focus_bracket_min_focus_spread_ratio, 1.0, 10.0, "focus bracket min focus spread ratio")
    _range(settings.focus_bracket_visual_similarity_min, 0.0, 1.0, "focus bracket visual similarity")
    _range(settings.burst_visual_similarity_threshold, 0.0, 1.0, "burst visual similarity threshold")
    _range(settings.panorama_min_frames, 3, 20, "panorama min frames")
    _range(settings.panorama_visual_similarity_min, 0.0, 1.0, "panorama visual similarity min")
    _range(settings.panorama_visual_similarity_max, 0.0, 1.0, "panorama visual similarity max")
    if settings.panorama_visual_similarity_min >= settings.panorama_visual_similarity_max:
        raise SettingsError("Диапазон сходства для панорамы задан некорректно (min >= max).")
    _range(settings.visual_similarity_threshold, 0.0, 1.0, "visual similarity threshold")
    _range(settings.thumbnail_size, 96, 1024, "thumbnail size")
    _range(settings.preview_cache_items, 1, 1000, "preview cache items")
    _range(settings.exiftool_batch_size, 1, 1000, "ExifTool batch size")
    if not isinstance(settings.worker_count, int) or settings.worker_count < 1 or settings.worker_count > 16:
        raise SettingsError("Количество потоков должно быть от 1 до 16.")
    if not settings.allowed_extensions:
        raise SettingsError("Список расширений не должен быть пустым.")
    for extension in settings.allowed_extensions:
        if not str(extension).startswith("."):
            raise SettingsError(f"Расширение должно начинаться с точки: {extension}.")
    if not set(settings.report_formats).issubset({"json", "csv", "txt"}):
        raise SettingsError("Форматы отчетов должны быть json, csv или txt.")


def _choice(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise SettingsError(f"Некорректное значение {label}: {value}.")


def _range(value: float, minimum: float, maximum: float, label: str) -> None:
    if not isinstance(value, (int, float)) or value != value or value < minimum or value > maximum:
        raise SettingsError(f"Некорректное значение {label}: {value}.")


def _detect_language_from_locale() -> str:
    return detect_default_language()
