"""Camera profile system: builtin profiles, CRUD, import/export, migration,
and applying a profile onto real settings (feature 3, 1.2.0)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.camera_profiles import (
    AUTO_PROFILE_ID,
    CURRENT_PROFILE_SCHEMA,
    UNIVERSAL_PROFILE_ID,
    CameraProfile,
    apply_profile,
    builtin_profiles,
    bundled_profiles,
    create_profile,
    delete_profile,
    export_profile_json,
    find_profile,
    import_profile_json,
    list_all_profiles,
    load_user_profiles,
    migrate_profile_dict,
    profile_from_confirmed_series,
    save_user_profiles,
    update_profile,
    user_profiles_path,
)
from app.enums import BracketSizeMode, ConfidenceLevel
from app.errors import ProfileError
from app.models import AppSettings, BracketSeries

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_series(size: int = 3, confidence: float = 95.0, exposure_source: str = "EV100") -> BracketSeries:
    files = []  # BracketSeries.raw_files is unused by profile_from_confirmed_series
    return BracketSeries(
        series_id=f"HDR{size}_0001",
        series_number=1,
        series_size=size,
        raw_files=files,
        rendered_companions={},
        start_time=BASE_TIME,
        end_time=BASE_TIME + timedelta(seconds=1.6),
        duration_seconds=1.6,
        exposure_values=[-2.0, 0.0, 2.0][: size if size <= 3 else 3] or [-2.0, -1.0, 0.0, 1.0, 2.0],
        estimated_step_ev=2.0,
        confidence=confidence,
        confidence_level=ConfidenceLevel.from_score(confidence),
        automatic=True,
        manually_confirmed=False,
        exposure_source=exposure_source,
    )


class BuiltinProfileTests(unittest.TestCase):
    def test_universal_profile_matches_app_defaults(self):
        from app.camera_profiles import universal_profile

        defaults = AppSettings()
        applied = apply_profile(defaults, universal_profile())
        self.assertEqual(applied.snapshot(), defaults.snapshot())

    def test_auto_profile_cannot_be_applied_directly(self):
        from app.camera_profiles import auto_profile

        with self.assertRaises(ProfileError):
            apply_profile(AppSettings(), auto_profile())

    def test_builtin_profiles_include_universal_and_auto(self):
        ids = {profile.profile_id for profile in builtin_profiles()}
        self.assertIn(UNIVERSAL_PROFILE_ID, ids)
        self.assertIn(AUTO_PROFILE_ID, ids)

    def test_bundled_json_profiles_are_actually_loaded(self):
        """Regression: profiles/*.json existed but nothing ever read them."""
        loaded = bundled_profiles()
        names = {profile.name for profile in loaded}
        self.assertIn("Generic", names)
        self.assertIn("Xiaomi 14 ProShot", names)
        for profile in loaded:
            self.assertTrue(profile.is_builtin)
            self.assertEqual(profile.schema_version, CURRENT_PROFILE_SCHEMA)

    def test_xiaomi_profile_survives_schema_1_to_2_migration_with_real_values(self):
        loaded = {profile.name: profile for profile in bundled_profiles()}
        xiaomi = loaded["Xiaomi 14 ProShot"]
        self.assertEqual(xiaomi.max_pair_delta_seconds, 0.8)
        self.assertEqual(xiaomi.max_bracket_adjacent_delta_seconds, 1.0)
        self.assertEqual(xiaomi.max_bracket_duration_seconds_3, 5.0)
        self.assertEqual(xiaomi.max_bracket_duration_seconds_5, 5.0)
        self.assertEqual(xiaomi.bracket_size_mode, "BOTH")

    def test_bundled_profiles_apply_cleanly(self):
        for profile in bundled_profiles():
            applied = apply_profile(AppSettings(), profile)
            self.assertIsInstance(applied.bracket_size_mode, BracketSizeMode)


class MigrationTests(unittest.TestCase):
    def test_schema_1_single_size_becomes_three(self):
        migrated = migrate_profile_dict({"schema_version": 1, "expected_bracket_sizes": [3]})
        self.assertEqual(migrated["bracket_size_mode"], "THREE")

    def test_schema_1_both_sizes_becomes_both(self):
        migrated = migrate_profile_dict({"schema_version": 1, "expected_bracket_sizes": [3, 5]})
        self.assertEqual(migrated["bracket_size_mode"], "BOTH")

    def test_schema_1_duration_seeds_both_size_specific_fields(self):
        migrated = migrate_profile_dict({"schema_version": 1, "bracket_duration_seconds": 7.5})
        self.assertEqual(migrated["max_bracket_duration_seconds_3"], 7.5)
        self.assertEqual(migrated["max_bracket_duration_seconds_5"], 7.5)

    def test_schema_newer_than_current_is_rejected(self):
        with self.assertRaises(ProfileError):
            migrate_profile_dict({"schema_version": CURRENT_PROFILE_SCHEMA + 1})

    def test_migration_is_idempotent_on_already_current_schema(self):
        data = {"schema_version": CURRENT_PROFILE_SCHEMA, "max_pair_delta_seconds": 1.2}
        migrated = migrate_profile_dict(data)
        self.assertEqual(migrated["max_pair_delta_seconds"], 1.2)


class CrudTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "camera_profiles.json"

    def test_create_profile_persists_and_is_listed(self):
        profile = create_profile("My Canon", path=self.path, manufacturer="Canon", model="EOS R5")
        self.assertFalse(profile.is_builtin)
        names = {p.name for p in load_user_profiles(self.path)}
        self.assertIn("My Canon", names)

    def test_create_with_empty_name_is_rejected(self):
        with self.assertRaises(ProfileError):
            create_profile("   ", path=self.path)

    def test_create_with_unknown_field_is_rejected(self):
        with self.assertRaises(ProfileError):
            create_profile("X", path=self.path, not_a_real_field=1)

    def test_create_with_out_of_range_value_is_rejected_and_not_persisted(self):
        with self.assertRaises(ProfileError):
            create_profile("X", path=self.path, min_auto_confidence=1000.0)
        self.assertEqual(load_user_profiles(self.path), [])

    def test_create_auto_profile_is_rejected(self):
        with self.assertRaises(ProfileError):
            create_profile("Fake Auto", path=self.path, is_auto=True)

    def test_update_changes_only_requested_fields(self):
        profile = create_profile("X", path=self.path, min_auto_confidence=70.0, max_pair_delta_seconds=0.5)
        updated = update_profile(profile.profile_id, path=self.path, min_auto_confidence=80.0)
        self.assertEqual(updated.min_auto_confidence, 80.0)
        self.assertEqual(updated.max_pair_delta_seconds, 0.5)

    def test_update_of_a_builtin_profile_is_rejected(self):
        with self.assertRaises(ProfileError):
            update_profile(UNIVERSAL_PROFILE_ID, path=self.path, min_auto_confidence=80.0)

    def test_update_of_an_unknown_profile_is_rejected(self):
        with self.assertRaises(ProfileError):
            update_profile("does-not-exist", path=self.path, min_auto_confidence=80.0)

    def test_delete_removes_from_user_profiles(self):
        profile = create_profile("X", path=self.path)
        delete_profile(profile.profile_id, path=self.path)
        self.assertEqual(load_user_profiles(self.path), [])

    def test_delete_of_a_builtin_profile_is_rejected(self):
        with self.assertRaises(ProfileError):
            delete_profile(AUTO_PROFILE_ID, path=self.path)

    def test_delete_of_an_unknown_profile_is_rejected(self):
        with self.assertRaises(ProfileError):
            delete_profile("does-not-exist", path=self.path)

    def test_list_all_profiles_includes_builtin_and_user(self):
        create_profile("X", path=self.path)
        all_names = {p.name for p in list_all_profiles(self.path)}
        self.assertIn("Universal", all_names)
        self.assertIn("Auto-detect", all_names)
        self.assertIn("X", all_names)

    def test_find_profile_locates_by_id(self):
        profile = create_profile("X", path=self.path)
        found = find_profile(profile.profile_id, self.path)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "X")

    def test_corrupted_user_profiles_file_raises_a_clear_error(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ProfileError):
            load_user_profiles(self.path)

    def test_save_user_profiles_never_writes_builtin_entries(self):
        from app.camera_profiles import universal_profile

        create_profile("X", path=self.path)
        users = load_user_profiles(self.path)
        save_user_profiles([*users, universal_profile()], self.path)
        reloaded = load_user_profiles(self.path)
        self.assertEqual({p.name for p in reloaded}, {"X"})

    def test_default_user_profiles_path_is_under_appdata(self):
        # Sanity check on the un-patched default, not a write test.
        self.assertTrue(str(user_profiles_path()).endswith("camera_profiles.json"))


class ImportExportTests(unittest.TestCase):
    def test_round_trip_preserves_values(self):
        profile = CameraProfile(name="Roundtrip", manufacturer="Sony", min_auto_confidence=77.0)
        text = export_profile_json(profile)
        imported = import_profile_json(text)
        self.assertEqual(imported.name, "Roundtrip")
        self.assertEqual(imported.manufacturer, "Sony")
        self.assertEqual(imported.min_auto_confidence, 77.0)

    def test_import_assigns_a_fresh_id(self):
        profile = CameraProfile(name="X")
        imported = import_profile_json(export_profile_json(profile))
        self.assertNotEqual(imported.profile_id, profile.profile_id)

    def test_import_never_trusts_builtin_or_auto_claims(self):
        payload = json.dumps({"name": "Fake", "is_builtin": True, "is_auto": True, "schema_version": CURRENT_PROFILE_SCHEMA})
        imported = import_profile_json(payload)
        self.assertFalse(imported.is_builtin)
        self.assertFalse(imported.is_auto)

    def test_import_invalid_json_raises_profile_error(self):
        with self.assertRaises(ProfileError):
            import_profile_json("{not json")

    def test_import_non_object_json_raises_profile_error(self):
        with self.assertRaises(ProfileError):
            import_profile_json("[1, 2, 3]")

    def test_import_migrates_schema_1(self):
        payload = json.dumps({"schema_version": 1, "name": "Old", "pair_time_threshold_seconds": 1.5})
        imported = import_profile_json(payload)
        self.assertEqual(imported.max_pair_delta_seconds, 1.5)
        self.assertEqual(imported.schema_version, CURRENT_PROFILE_SCHEMA)

    def test_import_out_of_range_value_is_rejected(self):
        payload = json.dumps({"name": "Bad", "min_auto_confidence": -5.0})
        with self.assertRaises(ProfileError):
            import_profile_json(payload)


class FromConfirmedSeriesTests(unittest.TestCase):
    def test_derives_bracket_size_mode_from_series_size(self):
        profile = profile_from_confirmed_series(make_series(size=3), "From HDR3")
        self.assertEqual(profile.bracket_size_mode, "THREE")
        profile5 = profile_from_confirmed_series(make_series(size=5), "From HDR5")
        self.assertEqual(profile5.bracket_size_mode, "FIVE")

    def test_prioritizes_the_series_own_exposure_source(self):
        profile = profile_from_confirmed_series(make_series(exposure_source="BaselineExposure"), "X")
        self.assertEqual(profile.exposure_source_priority[0], "BaselineExposure")

    def test_confidence_threshold_is_below_the_series_confidence(self):
        series = make_series(confidence=95.0)
        profile = profile_from_confirmed_series(series, "X")
        self.assertLess(profile.min_auto_confidence, 95.0)
        self.assertGreaterEqual(profile.min_auto_confidence, 50.0)

    def test_result_applies_cleanly_onto_real_settings(self):
        profile = profile_from_confirmed_series(make_series(), "X")
        applied = apply_profile(AppSettings(), profile)
        self.assertIsInstance(applied, AppSettings)

    def test_does_not_mutate_the_series(self):
        series = make_series()
        snapshot = (series.series_id, series.confidence, tuple(series.exposure_values))
        profile_from_confirmed_series(series, "X")
        self.assertEqual((series.series_id, series.confidence, tuple(series.exposure_values)), snapshot)


class ApplyProfileTests(unittest.TestCase):
    def test_apply_never_mutates_input_settings(self):
        from app.camera_profiles import universal_profile

        settings = AppSettings()
        snapshot = settings.snapshot()
        profile = CameraProfile(name="X", min_auto_confidence=85.0)
        apply_profile(settings, profile)
        self.assertEqual(settings.snapshot(), snapshot)

    def test_apply_returns_a_distinct_object(self):
        settings = AppSettings()
        profile = CameraProfile(name="X")
        result = apply_profile(settings, profile)
        self.assertIsNot(result, settings)

    def test_apply_updates_every_mapped_field(self):
        settings = AppSettings()
        profile = CameraProfile(
            name="X",
            bracket_size_mode="FIVE",
            min_exposure_span_ev=0.55,
            exposure_step_tolerance=0.33,
            max_bracket_duration_seconds_3=3.3,
            max_bracket_duration_seconds_5=7.7,
            max_bracket_adjacent_delta_seconds=0.9,
            exposure_source_priority=("EV100", "ExposureCompensation", "BaselineExposure", "LightValue", "BrightnessValue"),
            max_pair_delta_seconds=0.6,
            use_name_for_pairing=False,
            use_exif_for_pairing=False,
            use_preview_for_pairing=True,
            min_auto_confidence=88.0,
        )
        applied = apply_profile(settings, profile)
        self.assertEqual(applied.bracket_size_mode, BracketSizeMode.FIVE)
        self.assertEqual(applied.min_exposure_span_ev, 0.55)
        self.assertEqual(applied.exposure_step_tolerance, 0.33)
        self.assertEqual(applied.max_bracket_duration_seconds_3, 3.3)
        self.assertEqual(applied.max_bracket_duration_seconds_5, 7.7)
        self.assertEqual(applied.max_bracket_adjacent_delta_seconds, 0.9)
        self.assertEqual(applied.exposure_source_priority[0], "EV100")
        self.assertEqual(applied.max_pair_delta_seconds, 0.6)
        self.assertFalse(applied.use_name_for_pairing)
        self.assertFalse(applied.use_exif_for_pairing)
        self.assertTrue(applied.use_preview_for_pairing)
        self.assertEqual(applied.min_auto_confidence, 88.0)

    def test_apply_recomputes_the_derived_max_duration(self):
        profile = CameraProfile(name="X", max_bracket_duration_seconds_3=2.0, max_bracket_duration_seconds_5=9.0)
        applied = apply_profile(AppSettings(), profile)
        self.assertEqual(applied.max_bracket_duration_seconds, 9.0)

    def test_apply_rejects_out_of_range_values_and_changes_nothing(self):
        settings = AppSettings()
        snapshot = settings.snapshot()
        profile = CameraProfile(name="X", min_auto_confidence=1000.0)
        with self.assertRaises(ProfileError):
            apply_profile(settings, profile)
        self.assertEqual(settings.snapshot(), snapshot)

    def test_exposure_priority_actually_changes_group_resolution(self):
        """The whole point of the field: it must influence real resolution, not
        just sit in a profile record. Two sources both present and both varying;
        whichever is first in priority wins."""
        from datetime import timedelta
        from pathlib import Path as P

        from app.enums import DetectedType
        from app.exposure import priority_from_names, resolve_group_exposure
        from app.models import PhotoFile, PhotoMetadata

        def meta(i, ec, baseline):
            name = f"IMG_{i}.dng"
            return PhotoFile(
                metadata=PhotoMetadata(
                    source_path=P(f"C:/p/{name}"),
                    relative_path=P(name),
                    file_name=name,
                    extension=".dng",
                    detected_type=DetectedType.DNG,
                    capture_time=BASE_TIME + timedelta(seconds=0.4 * i),
                    exposure_compensation=ec,
                    baseline_exposure=baseline,
                    file_size=1000,
                )
            )

        files = [meta(0, -2.0, 10.0), meta(1, 0.0, 12.0), meta(2, 2.0, 14.0)]
        default_order = resolve_group_exposure(files)
        self.assertEqual(default_order.source.value, "ExposureCompensation")

        reordered = priority_from_names(("BaselineExposure", "ExposureCompensation"))
        baseline_first = resolve_group_exposure(files, priority=reordered)
        self.assertEqual(baseline_first.source.value, "BaselineExposure")


class SettingsIntegrationTests(unittest.TestCase):
    """The exposure_source_priority field added for this feature must round-trip
    through the same save/load/migrate path every other setting uses."""

    def test_default_priority_matches_previous_fixed_order(self):
        settings = AppSettings()
        self.assertEqual(
            settings.exposure_source_priority,
            ("ExposureCompensation", "EV100", "BaselineExposure", "LightValue", "BrightnessValue"),
        )

    def test_snapshot_round_trips_through_settings_from_dict(self):
        from app.settings import settings_from_dict

        settings = AppSettings()
        settings.exposure_source_priority = ("EV100", "ExposureCompensation")
        reloaded = settings_from_dict(settings.snapshot())
        self.assertEqual(reloaded.exposure_source_priority, ("EV100", "ExposureCompensation"))

    def test_old_settings_without_the_field_still_load(self):
        """Schema 1/2 settings.json files predating this feature must not break."""
        from app.settings import settings_from_dict

        data = AppSettings().snapshot()
        del data["exposure_source_priority"]
        reloaded = settings_from_dict(data)
        self.assertTrue(reloaded.exposure_source_priority)


class FastSeriesProfileTests(unittest.TestCase):
    """Regression: any series faster than 0.8 s got duration = 1.5x its length while
    the adjacent delta stayed at the default 1.2 s, so validate_settings rejected
    the profile ("duration 3 < adjacent") before it could ever be saved."""

    def _frames(self, times: tuple[float, ...]) -> list:
        from app.enums import DetectedType
        from app.models import PhotoFile, PhotoMetadata

        frames = []
        for index, seconds in enumerate(times):
            name = f"IMG_{index}.dng"
            frames.append(
                PhotoFile(
                    metadata=PhotoMetadata(
                        source_path=Path(f"C:/p/{name}"),
                        relative_path=Path(name),
                        file_name=name,
                        extension=".dng",
                        detected_type=DetectedType.DNG,
                        capture_time=BASE_TIME + timedelta(seconds=seconds),
                        file_size=1000,
                    )
                )
            )
        return frames

    def _series(self, duration: float, frames: list | None = None, size: int = 3) -> BracketSeries:
        return BracketSeries(
            series_id=f"HDR{size}_0001",
            series_number=1,
            series_size=size,
            raw_files=frames or [],
            rendered_companions={},
            start_time=BASE_TIME,
            end_time=BASE_TIME + timedelta(seconds=duration),
            duration_seconds=duration,
            exposure_values=[-2.0, 0.0, 2.0],
            estimated_step_ev=2.0,
            confidence=95.0,
            confidence_level=ConfidenceLevel.HIGH,
            automatic=True,
            manually_confirmed=True,
            exposure_source="EV100",
        )

    def test_a_0_4_second_three_frame_series_yields_a_valid_profile(self):
        profile = profile_from_confirmed_series(self._series(0.4, self._frames((0.0, 0.2, 0.4))), "Phone")
        applied = apply_profile(AppSettings(), profile)  # used to raise ProfileError
        self.assertAlmostEqual(profile.max_bracket_adjacent_delta_seconds, 0.3, places=2)  # 0.2 s gap x1.5, at the 0.3 s floor
        self.assertAlmostEqual(profile.max_bracket_duration_seconds_3, 0.6, places=2)
        self.assertGreaterEqual(profile.max_bracket_duration_seconds_3, profile.max_bracket_adjacent_delta_seconds)
        self.assertEqual(applied.max_bracket_adjacent_delta_seconds, profile.max_bracket_adjacent_delta_seconds)

    def test_adjacent_delta_follows_the_real_maximum_gap_not_the_average(self):
        profile = profile_from_confirmed_series(self._series(1.0, self._frames((0.0, 0.2, 1.0))), "X")
        self.assertAlmostEqual(profile.max_bracket_adjacent_delta_seconds, 1.2, places=2)  # 0.8 s x 1.5
        apply_profile(AppSettings(), profile)

    def test_series_without_per_frame_times_falls_back_to_even_spacing(self):
        profile = profile_from_confirmed_series(self._series(0.4), "X")
        self.assertAlmostEqual(profile.max_bracket_adjacent_delta_seconds, 0.3, places=2)
        apply_profile(AppSettings(), profile)

    def test_every_duration_validates_including_sub_second_ones(self):
        for size in (3, 5):
            for duration in (0.2, 0.3, 0.5, 0.79, 0.8, 1.6, 10.0):
                profile = profile_from_confirmed_series(self._series(duration, size=size), f"X{duration}")
                apply_profile(AppSettings(), profile)
                self.assertGreaterEqual(profile.max_bracket_duration_seconds_3, profile.max_bracket_adjacent_delta_seconds)
                self.assertGreaterEqual(profile.max_bracket_duration_seconds_5, profile.max_bracket_adjacent_delta_seconds)

    def test_the_other_size_keeps_the_default_unless_the_gap_needs_more(self):
        fast = profile_from_confirmed_series(self._series(0.4), "X")
        self.assertEqual(fast.max_bracket_duration_seconds_5, AppSettings().max_bracket_duration_seconds_5)
        slow = profile_from_confirmed_series(self._series(10.0), "Y")  # 5 s gaps -> 7.5 s adjacent delta
        self.assertEqual(slow.max_bracket_duration_seconds_5, 7.5)


class MalformedProfileValueTests(unittest.TestCase):
    """Every malformed JSON value must surface as ProfileError naming the field —
    never a bare TypeError/ValueError escaping from a conversion."""

    def _import(self, **fields):
        return import_profile_json(json.dumps({"name": "X", "schema_version": CURRENT_PROFILE_SCHEMA, **fields}))

    def test_non_list_formats_raise_profile_error(self):
        with self.assertRaises(ProfileError) as caught:
            self._import(raw_formats=5)
        self.assertIn("raw_formats", caught.exception.user_message)

    def test_list_with_non_string_items_raises_profile_error(self):
        with self.assertRaises(ProfileError):
            self._import(rendered_formats=[".heic", 7])

    def test_non_numeric_schema_version_raises_profile_error(self):
        with self.assertRaises(ProfileError):
            import_profile_json(json.dumps({"name": "X", "schema_version": "abc"}))
        with self.assertRaises(ProfileError):
            migrate_profile_dict({"schema_version": [2]})

    def test_non_numeric_expected_bracket_sizes_raise_profile_error(self):
        for sizes in (["3", "x"], 5, [None]):
            with self.assertRaises(ProfileError, msg=repr(sizes)):
                import_profile_json(json.dumps({"name": "X", "schema_version": 1, "expected_bracket_sizes": sizes}))

    def test_booleans_must_be_real_booleans(self):
        for value in ("no", "yes", "false", 0, 1):
            for field in ("use_name_for_pairing", "use_exif_for_pairing", "use_preview_for_pairing", "is_auto"):
                with self.assertRaises(ProfileError, msg=f"{field}={value!r}"):
                    self._import(**{field: value})

    def test_numbers_must_be_finite_numbers(self):
        for value in ("abc", "1e999", [1], {"a": 1}, True, False):
            with self.assertRaises(ProfileError, msg=repr(value)):
                self._import(min_auto_confidence=value)

    def test_numeric_strings_are_still_accepted(self):
        self.assertEqual(self._import(max_pair_delta_seconds="0.5").max_pair_delta_seconds, 0.5)

    def test_text_fields_must_be_strings(self):
        with self.assertRaises(ProfileError):
            self._import(manufacturer=12)
        with self.assertRaises(ProfileError):
            self._import(bracket_size_mode=3)

    def test_null_values_fall_back_to_defaults(self):
        profile = self._import(manufacturer=None, raw_formats=None, min_auto_confidence=None, use_name_for_pairing=None)
        self.assertEqual(profile.manufacturer, "")
        self.assertEqual(profile.raw_formats, ())
        self.assertEqual(profile.min_auto_confidence, 72.0)
        self.assertTrue(profile.use_name_for_pairing)

    def test_user_profiles_file_with_a_bad_value_raises_profile_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "camera_profiles.json"
            path.write_text(json.dumps([{"name": "Bad", "schema_version": CURRENT_PROFILE_SCHEMA, "raw_formats": 5}]), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_user_profiles(path)


class BundledProfileIdentityTests(unittest.TestCase):
    """Regression: bundled profiles got a fresh uuid on every load, so an id the
    GUI had just listed could never be found again."""

    def test_bundled_ids_are_stable_across_loads(self):
        first = {profile.name: profile.profile_id for profile in bundled_profiles()}
        second = {profile.name: profile.profile_id for profile in bundled_profiles()}
        self.assertTrue(first)
        self.assertEqual(first, second)

    def test_distinct_bundled_files_get_distinct_ids(self):
        ids = [profile.profile_id for profile in bundled_profiles()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_find_profile_locates_every_bundled_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "camera_profiles.json"
            for profile in bundled_profiles():
                found = find_profile(profile.profile_id, path)
                self.assertIsNotNone(found, profile.name)
                self.assertEqual(found.name, profile.name)
                self.assertTrue(found.is_builtin)

    def test_bundled_profiles_are_protected_like_other_builtins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "camera_profiles.json"
            bundled_id = bundled_profiles()[0].profile_id
            with self.assertRaises(ProfileError):
                delete_profile(bundled_id, path=path)
            with self.assertRaises(ProfileError):
                update_profile(bundled_id, path=path, name="Hacked")


if __name__ == "__main__":
    unittest.main()
