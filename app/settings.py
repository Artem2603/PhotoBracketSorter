"""Settings persistence, migration, and validation."""

from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

from app.enums import BracketSizeMode, DetectionMode
from app.errors import SettingsError
from app.models import AppSettings

CURRENT_SETTINGS_SCHEMA = 2
LANGUAGES = {"ru", "en"}
THEMES = {"system", "light", "dark"}
UI_MODES = {"simple", "advanced"}
ACCENTS = {"blue", "green", "purple", "orange"}
DUPLICATE_BEHAVIORS = {"rename", "skip", "overwrite"}
REPORT_DETAILS = {"compact", "normal", "full"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def default_settings_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "PhotoBracketSorter" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    if not settings_path.exists():
        return AppSettings()
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SettingsError("Файл настроек поврежден.")
    return settings_from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    validate_settings(settings)
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")


def settings_from_dict(data: dict[str, Any]) -> AppSettings:
    migrated = migrate_settings_dict(data)
    allowed = {field.name for field in fields(AppSettings)}
    values = {key: value for key, value in migrated.items() if key in allowed}
    if "detection_mode" in values:
        values["detection_mode"] = DetectionMode(str(values["detection_mode"]))
    if "bracket_size_mode" in values:
        values["bracket_size_mode"] = BracketSizeMode(str(values["bracket_size_mode"]))
    for key in ("allowed_extensions", "excluded_folder_names", "report_formats", "column_order", "exposure_source_priority"):
        if key in values and not isinstance(values[key], tuple):
            values[key] = tuple(str(item) for item in values[key])
    if "column_widths" in values:
        raw_widths = values["column_widths"]
        values["column_widths"] = {str(key): int(value) for key, value in raw_widths.items()} if isinstance(raw_widths, dict) else {}
    settings = AppSettings(**values)
    settings.schema_version = CURRENT_SETTINGS_SCHEMA
    validate_settings(settings)
    return settings


def migrate_settings_dict(data: dict[str, Any]) -> dict[str, Any]:
    schema = int(data.get("schema_version", 1) or 1)
    if schema > CURRENT_SETTINGS_SCHEMA:
        raise SettingsError(f"Версия настроек {schema} новее поддерживаемой {CURRENT_SETTINGS_SCHEMA}.")
    defaults = AppSettings().snapshot()
    migrated = dict(defaults)
    migrated.update(data)
    if schema <= 1:
        migrated["schema_version"] = CURRENT_SETTINGS_SCHEMA
        migrated.setdefault("language", _detect_language_from_locale())
        migrated.setdefault("report_language", migrated.get("language", "ru"))
        migrated.setdefault("theme", "system")
        migrated.setdefault("accent", "blue")
        migrated.setdefault("ui_scale", 1.0)
        migrated.setdefault("max_bracket_duration_seconds_3", min(float(migrated.get("max_bracket_duration_seconds", 6.0)), 4.0))
        migrated.setdefault("max_bracket_duration_seconds_5", float(migrated.get("max_bracket_duration_seconds", 6.0)))
    migrated["schema_version"] = CURRENT_SETTINGS_SCHEMA
    return migrated


def validate_settings(settings: AppSettings) -> None:
    _choice(settings.language, LANGUAGES, "language")
    _choice(settings.report_language, LANGUAGES, "report_language")
    _choice(settings.theme, THEMES, "theme")
    _choice(settings.ui_mode, UI_MODES, "ui_mode")
    _choice(settings.accent, ACCENTS, "accent")
    _choice(settings.duplicate_behavior, DUPLICATE_BEHAVIORS, "duplicate_behavior")
    _choice(settings.report_detail, REPORT_DETAILS, "report_detail")
    _choice(settings.log_level.upper(), LOG_LEVELS, "log_level")
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
    if settings.worker_count < 1 or settings.worker_count > 16:
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
    locale = ".".join(part for part in (os.environ.get("LANG"), os.environ.get("LANGUAGE")) if part).lower()
    return "ru" if locale.startswith("ru") or "_ru" in locale else "en"
