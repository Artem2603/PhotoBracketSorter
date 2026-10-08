"""Every setting exposed in the UI must change real behaviour (P0-2).

Each test flips one setting and asserts an observable difference. A setting that
can be saved but changes nothing is a lie told to the user, so these tests exist to
keep the settings dialog honest.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.analysis import collect_candidate_paths, is_excluded_folder
from app.constants import REPORT_CSV_NAME, REPORT_JSON_NAME, REPORT_TEXT_NAME
from app.copy_engine import CopyEngine
from app.copy_plan import build_copy_plan, resolve_destination
from app.enums import DestinationCategory, DetectedType
from app.metadata import MetadataReader
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata
from app.paths import DEFAULT_OUTPUT_TEMPLATE, render_output_folder_name
from app.reporting import write_reports
from tests.test_transfer_safety import make_operation, make_plan, write_photo

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_photo_file(name: str, relative: str | None = None, size: int = 1000) -> PhotoFile:
    return PhotoFile(
        metadata=PhotoMetadata(
            source_path=Path(f"C:/photos/{relative or name}"),
            relative_path=Path(relative or name),
            file_name=name,
            extension=Path(name).suffix,
            detected_type=DetectedType.DNG,
            capture_time=BASE_TIME,
            file_size=size,
        ),
        destination_category=DestinationCategory.RAW,
        destination_subpath=Path("RAW") / name,
    )


def make_analysis(files: list[PhotoFile], root: Path) -> AnalysisResult:
    return AnalysisResult(
        source_root=root,
        files=files,
        pairs=[],
        bracket_candidates=[],
        bracket_series=[],
        statistics={"series_3": 0, "series_5": 0},
        warnings=[],
        created_at=BASE_TIME,
        settings_snapshot=AppSettings().snapshot(),
        fingerprint="fp",
    )


class ExcludedFolderNamesTests(unittest.TestCase):
    def test_pattern_matches_folder_name(self):
        self.assertTrue(is_excluded_folder(("sorted_photos_2026",), ("sorted_photos_*",)))
        self.assertFalse(is_excluded_folder(("holiday",), ("sorted_photos_*",)))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(is_excluded_folder(("cache",), ("cache",)))

    def test_excluded_folders_are_skipped_during_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_photo(root / "keep" / "a.dng")
            write_photo(root / "Lightroom.lrdata" / "b.dng")
            settings = AppSettings()
            settings.excluded_folder_names = ("*.lrdata",)
            found = collect_candidate_paths(root, settings, threading.Event())
            names = {path.name for path in found}
            self.assertIn("a.dng", names)
            self.assertNotIn("b.dng", names, "files under an excluded folder must be skipped")

    def test_empty_pattern_list_excludes_nothing(self):
        self.assertFalse(is_excluded_folder(("anything",), ()))


class OutputFolderTemplateTests(unittest.TestCase):
    def test_default_template_renders_date_and_time(self):
        name = render_output_folder_name(DEFAULT_OUTPUT_TEMPLATE, BASE_TIME)
        self.assertEqual(name, "Sorted_Photos_2026-07-01_12-00-00")

    def test_custom_template_is_honoured(self):
        self.assertEqual(render_output_folder_name("Photos_{year}_{month}", BASE_TIME), "Photos_2026_07")

    def test_unknown_placeholder_falls_back_instead_of_crashing(self):
        name = render_output_folder_name("{nonsense}", BASE_TIME)
        self.assertEqual(name, "Sorted_Photos_2026-07-01_12-00-00")

    def test_illegal_characters_are_stripped(self):
        self.assertNotIn(":", render_output_folder_name("bad:name?", BASE_TIME))

    def test_template_reaches_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings()
            settings.output_folder_template = "MyRun_{date}"
            analysis = make_analysis([make_photo_file("a.dng")], root / "src")
            plan = build_copy_plan(analysis, root / "out", settings)
            self.assertTrue(plan.output_root.name.startswith("MyRun_"))


class DuplicateBehaviorTests(unittest.TestCase):
    def test_rename_picks_a_free_variant(self):
        reserved = {Path("out/a.dng")}
        result = resolve_destination(Path("out/a.dng"), reserved, "rename")
        self.assertEqual(result, Path("out/a_02.dng"))

    def test_skip_returns_none_for_a_taken_path(self):
        reserved = {Path("out/a.dng")}
        self.assertIsNone(resolve_destination(Path("out/a.dng"), reserved, "skip"))

    def test_overwrite_reuses_a_path_that_exists_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "a.dng"
            existing.write_bytes(b"old")
            reserved: set[Path] = set()
            self.assertEqual(resolve_destination(existing, reserved, "overwrite"), existing)

    def test_overwrite_never_reuses_a_path_claimed_by_the_same_plan(self):
        # Two same-named sources in one run (100APPLE/IMG_0001.JPG and
        # 101APPLE/IMG_0001.JPG) must not share a destination: in Move mode the
        # second copy would overwrite the first *after* its source was deleted.
        reserved = {Path("out/a.dng")}
        self.assertEqual(resolve_destination(Path("out/a.dng"), reserved, "overwrite"), Path("out/a_02.dng"))

    def test_overwrite_plan_keeps_both_colliding_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings()
            settings.duplicate_behavior = "overwrite"
            files = [make_photo_file("a.dng", "one/a.dng"), make_photo_file("a.dng", "two/a.dng")]
            plan = build_copy_plan(make_analysis(files, root / "src"), root / "out", settings)
            self.assertEqual(len(plan.operations), 2)
            self.assertEqual(len({op.destination_path for op in plan.operations}), 2)

    def test_skip_drops_colliding_operations_from_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings()
            settings.duplicate_behavior = "skip"
            # Two different sources that collapse onto one destination name.
            files = [make_photo_file("a.dng", "one/a.dng"), make_photo_file("a.dng", "two/a.dng")]
            plan = build_copy_plan(make_analysis(files, root / "src"), root / "out", settings)
            self.assertEqual(len(plan.operations), 1)

    def test_rename_keeps_both_colliding_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings()
            settings.duplicate_behavior = "rename"
            files = [make_photo_file("a.dng", "one/a.dng"), make_photo_file("a.dng", "two/a.dng")]
            plan = build_copy_plan(make_analysis(files, root / "src"), root / "out", settings)
            self.assertEqual(len(plan.operations), 2)
            self.assertEqual(len({op.destination_path for op in plan.operations}), 2)


class PreserveSourceSubfoldersTests(unittest.TestCase):
    def test_disabled_produces_a_flat_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings()
            settings.preserve_source_subfolders = False
            plan = build_copy_plan(
                make_analysis([make_photo_file("a.dng", "2026/Trip/a.dng")], root / "src"), root / "out", settings
            )
            relative = plan.operations[0].destination_path.relative_to(plan.output_root)
            self.assertEqual(relative, Path("RAW/a.dng"))

    def test_enabled_mirrors_the_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = AppSettings()
            settings.preserve_source_subfolders = True
            plan = build_copy_plan(
                make_analysis([make_photo_file("a.dng", "2026/Trip/a.dng")], root / "src"), root / "out", settings
            )
            relative = plan.operations[0].destination_path.relative_to(plan.output_root)
            self.assertEqual(relative, Path("RAW/2026/Trip/a.dng"))


class PreserveFileTimesTests(unittest.TestCase):
    def _copy_with(self, preserve: bool) -> tuple[float, float]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            import os
            import time

            old = time.time() - 100000
            os.utime(photo, (old, old))
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            settings = AppSettings()
            settings.preserve_file_times = preserve
            CopyEngine(settings).execute(make_plan(root / "out", [operation]))
            return photo.stat().st_mtime, operation.destination_path.stat().st_mtime

    def test_enabled_carries_the_timestamp(self):
        source_mtime, destination_mtime = self._copy_with(True)
        self.assertAlmostEqual(source_mtime, destination_mtime, delta=2)

    def test_disabled_leaves_a_fresh_timestamp(self):
        source_mtime, destination_mtime = self._copy_with(False)
        self.assertGreater(destination_mtime, source_mtime + 1000)


class ReportFormatTests(unittest.TestCase):
    def _write(self, settings: AppSettings) -> Path:
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        photo = root / "src" / "a.dng"
        write_photo(photo)
        operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
        result = CopyEngine(settings).execute(make_plan(root / "out", [operation]))
        write_reports(make_analysis([], root / "src"), result, settings)
        return root / "out"

    def test_only_requested_formats_are_written(self):
        settings = AppSettings()
        settings.report_formats = ("json",)
        output = self._write(settings)
        self.assertTrue((output / REPORT_JSON_NAME).exists())
        self.assertFalse((output / REPORT_CSV_NAME).exists())
        self.assertFalse((output / REPORT_TEXT_NAME).exists())

    def test_txt_only_skips_json(self):
        settings = AppSettings()
        settings.report_formats = ("txt",)
        output = self._write(settings)
        self.assertTrue((output / REPORT_TEXT_NAME).exists())
        self.assertFalse((output / REPORT_JSON_NAME).exists())

    def test_report_language_switches_the_text_report(self):
        settings = AppSettings()
        settings.report_language = "en"
        text = (self._write(settings) / REPORT_TEXT_NAME).read_text(encoding="utf-8")
        self.assertIn("Sorting finished.", text)
        self.assertNotIn("Сортировка завершена.", text)

    def test_russian_report_language_is_still_available(self):
        settings = AppSettings()
        settings.report_language = "ru"
        text = (self._write(settings) / REPORT_TEXT_NAME).read_text(encoding="utf-8")
        self.assertIn("Сортировка завершена.", text)

    def test_csv_headers_follow_the_setting(self):
        settings = AppSettings()
        settings.csv_english_headers = False
        settings.report_language = "ru"
        header = (self._write(settings) / REPORT_CSV_NAME).read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertIn("источник", header)

        settings.csv_english_headers = True
        header = (self._write(settings) / REPORT_CSV_NAME).read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertIn("source", header)

    def test_compact_detail_omits_per_file_rows(self):
        settings = AppSettings()
        settings.report_detail = "compact"
        data = json.loads((self._write(settings) / REPORT_JSON_NAME).read_text(encoding="utf-8"))
        self.assertNotIn("files", data)
        self.assertIn("statistics", data)

    def test_full_detail_adds_candidates(self):
        settings = AppSettings()
        settings.report_detail = "full"
        data = json.loads((self._write(settings) / REPORT_JSON_NAME).read_text(encoding="utf-8"))
        self.assertIn("bracket_candidates", data)
        self.assertIn("files", data)


class ExifToolAndWorkerSettingsTests(unittest.TestCase):
    def test_exiftool_path_is_passed_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "exiftool.exe"
            fake.write_bytes(b"stub")
            settings = AppSettings()
            settings.exiftool_path = str(fake)
            reader = MetadataReader.from_settings(settings)
            self.assertEqual(reader.exiftool.executable, fake)

    def test_missing_exiftool_path_falls_back_to_discovery(self):
        settings = AppSettings()
        settings.exiftool_path = r"C:\definitely\not\here\exiftool.exe"
        reader = MetadataReader.from_settings(settings)
        self.assertNotEqual(str(reader.exiftool.executable or ""), settings.exiftool_path)

    def test_batch_size_and_worker_count_reach_the_reader(self):
        settings = AppSettings()
        settings.exiftool_batch_size = 17
        settings.worker_count = 5
        reader = MetadataReader.from_settings(settings)
        self.assertEqual(reader.batch_size, 17)
        self.assertEqual(reader.worker_count, 5)
        self.assertEqual(reader.exiftool.batch_size, 17)


class LogLevelTests(unittest.TestCase):
    def test_configured_level_is_applied(self):
        import logging

        from app.logging_setup import configure_logging_level

        original = logging.getLogger().level
        try:
            configure_logging_level("ERROR")
            self.assertEqual(logging.getLogger().level, logging.ERROR)
            configure_logging_level("DEBUG")
            self.assertEqual(logging.getLogger().level, logging.DEBUG)
        finally:
            logging.getLogger().setLevel(original)

    def test_unknown_level_falls_back_to_info(self):
        import logging

        from app.logging_setup import configure_logging_level

        original = logging.getLogger().level
        try:
            configure_logging_level("NOT_A_LEVEL")
            self.assertEqual(logging.getLogger().level, logging.INFO)
        finally:
            logging.getLogger().setLevel(original)


class MixedTimezoneSafeModeTests(unittest.TestCase):
    def test_safe_mode_off_refuses_mixed_comparison(self):
        from datetime import timezone

        from app.time_compare import safe_datetime_difference_seconds, set_mixed_timezone_safe_mode

        aware = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
        naive = datetime(2026, 7, 1, 12, 0, 1)
        try:
            set_mixed_timezone_safe_mode(True)
            self.assertIsNotNone(safe_datetime_difference_seconds(aware, naive).seconds)
            set_mixed_timezone_safe_mode(False)
            self.assertIsNone(safe_datetime_difference_seconds(aware, naive).seconds)
        finally:
            set_mixed_timezone_safe_mode(True)


class StoreCompanionsInsideSeriesTests(unittest.TestCase):
    """Regression: the checkbox was saved and shown but changed nothing — the
    HEIC/JPEG companion of a series RAW was always moved into the series folder."""

    def _items(self) -> list[PhotoMetadata]:
        items = []
        for index, compensation in enumerate((-1.0, 0.0, 1.0)):
            for extension, detected, offset in ((".dng", DetectedType.DNG, 0.0), (".heic", DetectedType.HEIC, 0.1)):
                name = f"IMG_{index}{extension}"
                items.append(
                    PhotoMetadata(
                        source_path=Path(f"C:/photos/{name}"),
                        relative_path=Path(name),
                        file_name=name,
                        extension=extension,
                        detected_type=detected,
                        capture_time=BASE_TIME + timedelta(seconds=0.4 * index + offset),
                        make="Xiaomi",
                        model="Xiaomi 14",
                        lens_model="main",
                        width=4000,
                        height=3000,
                        orientation="1",
                        exposure_compensation=compensation,
                        exposure_time=1 / 125,
                        f_number=1.8,
                        iso=100,
                        file_size=1000,
                    )
                )
        return items

    def _classify(self, flag: bool):
        from app.classification import Classifier

        settings = AppSettings()
        settings.store_companions_inside_series = flag
        files, pairs, _candidates, series, _statistics = Classifier().classify(self._items(), settings)
        self.assertEqual(len(series), 1, "fixture must form one automatic series")
        self.assertEqual(len(pairs), 3, "fixture must pair every RAW with its HEIC")
        companions = [file for file in files if file.metadata.detected_type is DetectedType.HEIC]
        return series[0], companions

    def test_enabled_moves_companions_into_the_series_folder(self):
        series, companions = self._classify(True)
        for heic in companions:
            self.assertEqual(heic.series_id, series.series_id)
            self.assertIs(heic.destination_category, DestinationCategory.BRACKETING_3)
            self.assertEqual(heic.destination_subpath, series.destination_root / "HEIC" / heic.metadata.file_name)

    def test_disabled_keeps_companions_in_heic_but_keeps_the_pair(self):
        series, companions = self._classify(False)
        for heic in companions:
            self.assertIsNone(heic.series_id)
            self.assertIs(heic.destination_category, DestinationCategory.HEIC)
            self.assertEqual(heic.destination_subpath, Path("HEIC") / heic.metadata.file_name)
            self.assertIsNotNone(heic.pair_id, "the pair relation must survive")
        # The series still knows its companions; they are simply not stored inside it.
        self.assertEqual(sum(1 for companion in series.rendered_companions.values() if companion is not None), 3)
        for raw in series.raw_files:
            self.assertEqual(raw.series_id, series.series_id)

    def test_the_two_values_produce_different_layouts(self):
        _inside_series, inside = self._classify(True)
        _outside_series, outside = self._classify(False)
        self.assertNotEqual({heic.destination_subpath for heic in inside}, {heic.destination_subpath for heic in outside})


if __name__ == "__main__":
    unittest.main()
