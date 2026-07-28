"""Camera profiles: named presets over the same fields ``AppSettings`` already has.

A profile is deliberately not a parallel vocabulary that needs translating into
real settings — its fields are named and scaled exactly like the ``AppSettings``
fields they map to, so :func:`apply_profile` is a straight
``dataclasses.replace`` plus the existing :func:`app.settings.validate_settings`.
No camera model is special-cased in the detection or pairing code; a profile is
only ever a bundle of starting values for those same generic knobs.

Two profiles are built in, not stored as JSON:

* **Universal** — the conservative ``AppSettings()`` defaults.
* **Auto-detect** — not a static value bundle at all. Applying it means running
  feature 1's auto-configuration instead of copying fixed numbers; there is
  nothing to copy blind, since the whole point is to look at the folder first.

``profiles/generic.json`` and ``profiles/xiaomi_proshot.json`` predate this
module and were never loaded by anything — dead configuration, the same defect
class as the P0-2 settings. They are now read here, through
:func:`migrate_profile_dict`, which is a real migration (schema 1 field names
differ from schema 2), not a version number sitting unused.
"""

from __future__ import annotations

import dataclasses
import json
import os
import uuid
from pathlib import Path
from typing import Any

from app.constants import PROFILES_DIR
from app.enums import BracketSizeMode
from app.errors import ProfileError, SettingsError
from app.exposure import SOURCE_PRIORITY
from app.models import AppSettings, BracketSeries
from app.settings import validate_settings

CURRENT_PROFILE_SCHEMA = 2

UNIVERSAL_PROFILE_ID = "universal"
AUTO_PROFILE_ID = "auto"

_DEFAULT_EXPOSURE_PRIORITY: tuple[str, ...] = tuple(source.value for source in SOURCE_PRIORITY)

#: Profile fields that map onto an identically-named, identically-scaled
#: AppSettings field. Kept as one list so apply/validate/from-series can't drift.
_MAPPED_FIELDS = (
    "min_exposure_span_ev",
    "exposure_step_tolerance",
    "max_bracket_duration_seconds_3",
    "max_bracket_duration_seconds_5",
    "max_bracket_adjacent_delta_seconds",
    "exposure_source_priority",
    "max_pair_delta_seconds",
    "use_name_for_pairing",
    "use_exif_for_pairing",
    "use_preview_for_pairing",
    "min_auto_confidence",
)


@dataclasses.dataclass(slots=True)
class CameraProfile:
    profile_id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: int = CURRENT_PROFILE_SCHEMA
    name: str = "Custom"
    manufacturer: str = ""
    model: str = ""
    description: str = ""
    is_builtin: bool = False
    #: True only for the "Auto-detect" entry: applying it runs auto-configure
    #: instead of copying fixed values, so every numeric field below is unused.
    is_auto: bool = False
    raw_formats: tuple[str, ...] = ()
    rendered_formats: tuple[str, ...] = ()
    bracket_size_mode: str = "BOTH"
    min_exposure_span_ev: float = 0.7
    exposure_step_tolerance: float = 0.45
    max_bracket_duration_seconds_3: float = 4.0
    max_bracket_duration_seconds_5: float = 6.0
    max_bracket_adjacent_delta_seconds: float = 1.2
    exposure_source_priority: tuple[str, ...] = _DEFAULT_EXPOSURE_PRIORITY
    max_pair_delta_seconds: float = 0.8
    use_name_for_pairing: bool = True
    use_exif_for_pairing: bool = True
    use_preview_for_pairing: bool = False
    min_auto_confidence: float = 72.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "schema_version": CURRENT_PROFILE_SCHEMA,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "description": self.description,
            "is_auto": self.is_auto,
            "raw_formats": list(self.raw_formats),
            "rendered_formats": list(self.rendered_formats),
            "bracket_size_mode": self.bracket_size_mode,
            "min_exposure_span_ev": self.min_exposure_span_ev,
            "exposure_step_tolerance": self.exposure_step_tolerance,
            "max_bracket_duration_seconds_3": self.max_bracket_duration_seconds_3,
            "max_bracket_duration_seconds_5": self.max_bracket_duration_seconds_5,
            "max_bracket_adjacent_delta_seconds": self.max_bracket_adjacent_delta_seconds,
            "exposure_source_priority": list(self.exposure_source_priority),
            "max_pair_delta_seconds": self.max_pair_delta_seconds,
            "use_name_for_pairing": self.use_name_for_pairing,
            "use_exif_for_pairing": self.use_exif_for_pairing,
            "use_preview_for_pairing": self.use_preview_for_pairing,
            "min_auto_confidence": self.min_auto_confidence,
        }


# ------------------------------------------------------------------ built-ins


def universal_profile() -> CameraProfile:
    defaults = AppSettings()
    return CameraProfile(
        profile_id=UNIVERSAL_PROFILE_ID,
        name="Universal",
        description="Conservative defaults; a safe starting point for any camera.",
        is_builtin=True,
        bracket_size_mode=defaults.bracket_size_mode.value,
        min_exposure_span_ev=defaults.min_exposure_span_ev,
        exposure_step_tolerance=defaults.exposure_step_tolerance,
        max_bracket_duration_seconds_3=defaults.max_bracket_duration_seconds_3,
        max_bracket_duration_seconds_5=defaults.max_bracket_duration_seconds_5,
        max_bracket_adjacent_delta_seconds=defaults.max_bracket_adjacent_delta_seconds,
        exposure_source_priority=defaults.exposure_source_priority,
        max_pair_delta_seconds=defaults.max_pair_delta_seconds,
        use_name_for_pairing=defaults.use_name_for_pairing,
        use_exif_for_pairing=defaults.use_exif_for_pairing,
        use_preview_for_pairing=defaults.use_preview_for_pairing,
        min_auto_confidence=defaults.min_auto_confidence,
    )


def auto_profile() -> CameraProfile:
    return CameraProfile(
        profile_id=AUTO_PROFILE_ID,
        name="Auto-detect",
        description="Analyzes the selected folder and suggests settings instead of using fixed values.",
        is_builtin=True,
        is_auto=True,
    )


def _load_bundled_profile(path: Path) -> CameraProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    migrated = migrate_profile_dict(data)
    profile = _profile_from_dict(migrated)
    profile.is_builtin = True
    return profile


def bundled_profiles() -> list[CameraProfile]:
    """The example profiles shipped in ``profiles/``, finally read by something."""
    if not PROFILES_DIR.exists():
        return []
    profiles: list[CameraProfile] = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        profile = _load_bundled_profile(path)
        if profile is not None:
            profiles.append(profile)
    return profiles


def builtin_profiles() -> list[CameraProfile]:
    return [universal_profile(), auto_profile(), *bundled_profiles()]


# --------------------------------------------------------------------- schema


def migrate_profile_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Schema 1 (the original bundled profiles) -> schema 2 field names.

    Schema 1 had no per-size duration split, so ``bracket_duration_seconds``
    seeds both ``max_bracket_duration_seconds_3`` and ``_5``.
    """
    schema = int(data.get("schema_version", 1) or 1)
    if schema > CURRENT_PROFILE_SCHEMA:
        raise ProfileError(f"Версия профиля {schema} новее поддерживаемой {CURRENT_PROFILE_SCHEMA}.")
    migrated = dict(data)
    if schema <= 1:
        if "pair_time_threshold_seconds" in migrated:
            migrated.setdefault("max_pair_delta_seconds", migrated.pop("pair_time_threshold_seconds"))
        if "bracket_adjacent_threshold_seconds" in migrated:
            migrated.setdefault("max_bracket_adjacent_delta_seconds", migrated.pop("bracket_adjacent_threshold_seconds"))
        if "bracket_duration_seconds" in migrated:
            duration = migrated.pop("bracket_duration_seconds")
            migrated.setdefault("max_bracket_duration_seconds_3", duration)
            migrated.setdefault("max_bracket_duration_seconds_5", duration)
        sizes = migrated.pop("expected_bracket_sizes", None)
        if sizes:
            unique = sorted(set(int(size) for size in sizes))
            if unique == [3]:
                migrated.setdefault("bracket_size_mode", "THREE")
            elif unique == [5]:
                migrated.setdefault("bracket_size_mode", "FIVE")
            elif unique:
                migrated.setdefault("bracket_size_mode", "BOTH")
    migrated["schema_version"] = CURRENT_PROFILE_SCHEMA
    return migrated


def _profile_from_dict(data: dict[str, Any]) -> CameraProfile:
    migrated = migrate_profile_dict(data)
    allowed = {field.name for field in dataclasses.fields(CameraProfile)}
    values = {key: value for key, value in migrated.items() if key in allowed and key != "is_builtin"}
    for key in ("raw_formats", "rendered_formats", "exposure_source_priority"):
        if key in values and not isinstance(values[key], tuple):
            values[key] = tuple(str(item) for item in values[key])
    values.setdefault("profile_id", uuid.uuid4().hex)
    profile = CameraProfile(**values)
    profile.schema_version = CURRENT_PROFILE_SCHEMA
    return profile


# ------------------------------------------------------------ user profiles


def user_profiles_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "PhotoBracketSorter" / "camera_profiles.json"


def load_user_profiles(path: Path | None = None) -> list[CameraProfile]:
    profiles_path = path or user_profiles_path()
    if not profiles_path.exists():
        return []
    try:
        data = json.loads(profiles_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Файл профилей повреждён: {exc}") from exc
    if not isinstance(data, list):
        raise ProfileError("Файл профилей повреждён: ожидался список.")
    profiles = []
    for entry in data:
        if isinstance(entry, dict):
            profile = _profile_from_dict(entry)
            profile.is_builtin = False
            profiles.append(profile)
    return profiles


def save_user_profiles(profiles: list[CameraProfile], path: Path | None = None) -> None:
    profiles_path = path or user_profiles_path()
    profiles_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [profile.to_dict() for profile in profiles if not profile.is_builtin]
    profiles_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_all_profiles(path: Path | None = None) -> list[CameraProfile]:
    return [*builtin_profiles(), *load_user_profiles(path)]


def find_profile(profile_id: str, path: Path | None = None) -> CameraProfile | None:
    return next((profile for profile in list_all_profiles(path) if profile.profile_id == profile_id), None)


# --------------------------------------------------------------------- CRUD


def create_profile(name: str, path: Path | None = None, **fields: Any) -> CameraProfile:
    if not name.strip():
        raise ProfileError("Имя профиля не должно быть пустым.")
    unknown = set(fields) - {field.name for field in dataclasses.fields(CameraProfile)}
    if unknown:
        raise ProfileError(f"Неизвестные поля профиля: {', '.join(sorted(unknown))}.")
    if fields.get("is_auto"):
        raise ProfileError("«Автоопределение» встроено и не может быть создано повторно.")
    profile = CameraProfile(name=name, **fields)
    profile.is_builtin = False
    _validate_profile(profile)
    users = load_user_profiles(path)
    users.append(profile)
    save_user_profiles(users, path)
    return profile


def update_profile(profile_id: str, path: Path | None = None, **changes: Any) -> CameraProfile:
    if changes.get("is_auto"):
        raise ProfileError("«Автоопределение» встроено и не может быть создано повторно.")
    users = load_user_profiles(path)
    for index, profile in enumerate(users):
        if profile.profile_id == profile_id:
            updated = dataclasses.replace(profile, **changes)
            updated.is_builtin = False
            _validate_profile(updated)
            users[index] = updated
            save_user_profiles(users, path)
            return updated
    raise ProfileError("Встроенные профили нельзя редактировать; создайте копию." if _is_builtin_id(profile_id) else "Профиль не найден.")


def delete_profile(profile_id: str, path: Path | None = None) -> None:
    if _is_builtin_id(profile_id):
        raise ProfileError("Встроенные профили нельзя удалить.")
    users = load_user_profiles(path)
    remaining = [profile for profile in users if profile.profile_id != profile_id]
    if len(remaining) == len(users):
        raise ProfileError("Профиль не найден.")
    save_user_profiles(remaining, path)


def _is_builtin_id(profile_id: str) -> bool:
    return profile_id in {UNIVERSAL_PROFILE_ID, AUTO_PROFILE_ID} or any(
        profile.profile_id == profile_id for profile in builtin_profiles()
    )


# --------------------------------------------------------------- import/export


def export_profile_json(profile: CameraProfile) -> str:
    return json.dumps(profile.to_dict(), ensure_ascii=False, indent=2)


def import_profile_json(text: str) -> CameraProfile:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Некорректный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("Профиль должен быть JSON-объектом.")
    profile = _profile_from_dict(data)
    # Imported content is never trusted as builtin or as the one auto-detect
    # entry, whatever the source claimed.
    profile.is_builtin = False
    profile.is_auto = False
    profile.profile_id = uuid.uuid4().hex
    _validate_profile(profile)
    return profile


# ---------------------------------------------------------- from a real series


def profile_from_confirmed_series(series: BracketSeries, name: str, manufacturer: str = "", model: str = "") -> CameraProfile:
    """Build a profile from what a real, user-confirmed series actually measured.

    Every numeric value comes from the series itself, not a guess: the same
    "derive from real algorithm output" rule the auto-configurer and the
    decision explainer both follow.
    """
    step = series.estimated_step_ev
    span = max(series.exposure_values) - min(series.exposure_values) if len(series.exposure_values) >= 2 else None
    duration = series.duration_seconds or AppSettings().max_bracket_duration_seconds_3
    size_mode = {3: "THREE", 5: "FIVE"}.get(series.series_size, "BOTH")
    priority = _DEFAULT_EXPOSURE_PRIORITY
    if series.exposure_source and series.exposure_source not in {"UNKNOWN", "PARTIAL"}:
        priority = (series.exposure_source, *(value for value in _DEFAULT_EXPOSURE_PRIORITY if value != series.exposure_source))
    profile = CameraProfile(
        name=name,
        manufacturer=manufacturer,
        model=model,
        description=f"Created from confirmed series {series.series_id}.",
        bracket_size_mode=size_mode,
        min_exposure_span_ev=round(max(0.1, (span or 0.7) * 0.6), 2),
        exposure_step_tolerance=round(max(0.1, (step or 0.45) * 0.3), 2) if step else 0.45,
        max_bracket_duration_seconds_3=round(max(0.5, duration * 1.5), 2) if series.series_size == 3 else AppSettings().max_bracket_duration_seconds_3,
        max_bracket_duration_seconds_5=round(max(0.5, duration * 1.5), 2) if series.series_size == 5 else AppSettings().max_bracket_duration_seconds_5,
        exposure_source_priority=priority,
        min_auto_confidence=round(min(99.0, max(50.0, series.confidence - 5.0)), 1),
    )
    _validate_profile(profile)
    return profile


# --------------------------------------------------------------------- apply


def apply_profile(settings: AppSettings, profile: CameraProfile) -> AppSettings:
    """Return a NEW AppSettings with the profile's values applied. Never mutates ``settings``.

    Raises :class:`ProfileError` (not the lower-level ``SettingsError``) so
    every caller of this module — GUI, CRUD validation, tests — catches one
    consistent error type.
    """
    if profile.is_auto:
        raise ProfileError("«Автоопределение» не задаёт значения напрямую — запустите автонастройку анализа.")
    try:
        BracketSizeMode(profile.bracket_size_mode)
    except ValueError as exc:
        raise ProfileError(f"Некорректный bracket_size_mode: {profile.bracket_size_mode}.") from exc
    changes: dict[str, Any] = {"bracket_size_mode": BracketSizeMode(profile.bracket_size_mode)}
    for field_name in _MAPPED_FIELDS:
        changes[field_name] = getattr(profile, field_name)
    updated = dataclasses.replace(settings, **changes)
    updated = dataclasses.replace(
        updated, max_bracket_duration_seconds=max(updated.max_bracket_duration_seconds_3, updated.max_bracket_duration_seconds_5)
    )
    try:
        validate_settings(updated)
    except SettingsError as exc:
        raise ProfileError(exc.user_message, exc.technical_reason) from exc
    return updated


def _validate_profile(profile: CameraProfile) -> None:
    if not profile.is_auto:
        apply_profile(AppSettings(), profile)
