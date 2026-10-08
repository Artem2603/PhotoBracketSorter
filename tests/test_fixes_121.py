"""Regression tests for the 1.2.1 hardening pass (core modules, no Tk)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app.copy_engine import CopyEngine
from app.copy_plan import build_copy_plan
from app.enums import DestinationCategory, DetectedType
from app.errors import ExifToolError, SettingsError
from app.exiftool_runner import ExifToolRunner
from app.i18n import detect_default_language
from app.integrity import force_unlink
from app.metadata import MetadataReader
from app.metadata_cache import load_cache_entries
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata
from app.move_engine import MoveEngine
from app.operation_journal import (
    JournalEntry,
    append_journal_entry,
    build_journal_entry,
    can_undo_move,
    load_journal,
    undo_move,
)
from app.paths import _reject_mtp, render_output_folder_name, sanitize_component
from app.preview_loader import load_preview_info
from app.reporting import _csv_safe_row
from app.settings import load_settings, merge_session_settings, save_settings, settings_from_dict


def _photo(path: Path, relative: str, category: DestinationCategory = DestinationCategory.HEIC) -> PhotoFile:
    stat = path.stat()
    metadata = PhotoMetadata(
        source_path=path,
        relative_path=Path(relative),
        file_name=path.name,
        extension=path.suffix.lower(),
        detected_type=DetectedType.JPEG,
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        capture_time=datetime(2026, 7, 1, 12, 0, 0),
    )
    return PhotoFile(metadata=metadata, destination_category=category, destination_subpath=Path(category.value) / path.name)


def _analysis(source: Path, files: list[PhotoFile]) -> AnalysisResult:
    return AnalysisResult(
        source_root=source,
        files=files,
        pairs=[],
        bracket_candidates=[],
        bracket_series=[],
        statistics={},
        warnings=[],
        created_at=datetime.now(),
        settings_snapshot={},
        fingerprint="test",
    )


class PathsTests(unittest.TestCase):
    def test_folder_containing_mtp_letters_is_an_ordinary_folder(self):
        # Relative paths bypass the drive check, which is what we want to isolate here.
        _reject_mtp(Path("Photos/MTP_backup"))  # must not raise
        _reject_mtp(Path("Photos/smtp-logs"))

    def test_shell_namespace_and_mtp_uris_are_rejected(self):
        from app.errors import MTPPathError

        with self.assertRaises(MTPPathError):
            _reject_mtp(Path("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}/phone"))
        with self.assertRaises(MTPPathError):
            _reject_mtp(Path("mtp://device/DCIM"))

    def test_windows_reserved_names_get_a_suffix(self):
        self.assertEqual(sanitize_component("CON"), "CON_")
        self.assertEqual(sanitize_component("nul.txt"), "nul_.txt")
        self.assertEqual(sanitize_component("Sunset"), "Sunset")
        self.assertEqual(sanitize_component("COM10"), "COM10")

    def test_attribute_access_in_template_falls_back_instead_of_raising(self):
        name = render_output_folder_name("{date.year}", datetime(2026, 7, 1, 12, 0, 0))
        self.assertTrue(name.startswith("Sorted_Photos_2026-07-01"))


class SettingsRobustnessTests(unittest.TestCase):
    def test_invalid_json_raises_settings_error_not_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"language": "ru",}', encoding="utf-8")
            with self.assertRaises(SettingsError):
                load_settings(path)

    def test_utf8_bom_from_notepad_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"language": "en"}), encoding="utf-8-sig")
            self.assertEqual(load_settings(path).language, "en")

    def test_hand_edited_scalars_are_coerced(self):
        settings = settings_from_dict({"worker_count": "4", "verify_sha256": "false", "ui_scale": "1.1"})
        self.assertEqual(settings.worker_count, 4)
        self.assertIs(settings.verify_sha256, False)
        self.assertAlmostEqual(settings.ui_scale, 1.1)

    def test_bad_values_raise_settings_error(self):
        for data in ({"detection_mode": "weird"}, {"worker_count": "many"}, {"allowed_extensions": 5}, {"schema_version": "abc"}):
            with self.subTest(data=data), self.assertRaises(SettingsError):
                settings_from_dict(data)

    def test_schema_1_duration_migrates_into_per_size_limits(self):
        settings = settings_from_dict({"schema_version": 1, "max_bracket_duration_seconds": 3.0})
        self.assertEqual(settings.max_bracket_duration_seconds_3, 3.0)
        self.assertEqual(settings.max_bracket_duration_seconds_5, 3.0)

    def test_save_is_atomic_and_leaves_no_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AppSettings(), path)
            self.assertTrue(path.exists())
            self.assertFalse(path.with_name("settings.json.partial").exists())

    def test_session_merge_keeps_machine_preferences(self):
        local = AppSettings(language="en", theme="dark", exiftool_path="C:/tools/exiftool.exe", window_geometry="800x600")
        restored = AppSettings(language="ru", theme="light", exiftool_path="D:/elsewhere.exe", min_exposure_span_ev=1.5, duplicate_behavior="skip")
        merged = merge_session_settings(local, restored)
        self.assertEqual((merged.language, merged.theme, merged.exiftool_path, merged.window_geometry), ("en", "dark", "C:/tools/exiftool.exe", "800x600"))
        self.assertEqual((merged.min_exposure_span_ev, merged.duplicate_behavior), (1.5, "skip"))


class LocaleDetectionTests(unittest.TestCase):
    def test_windows_russian_ui_language_wins(self):
        with mock.patch("app.i18n._windows_ui_language_id", return_value=0x0419):
            self.assertEqual(detect_default_language(), "ru")
        with mock.patch("app.i18n._windows_ui_language_id", return_value=0x0409):
            self.assertEqual(detect_default_language(), "en")

    def test_environment_fallback(self):
        with mock.patch("app.i18n._windows_ui_language_id", return_value=None), mock.patch("app.i18n._locale_candidates", return_value=["ru_RU.UTF-8"]):
            self.assertEqual(detect_default_language(), "ru")
        with mock.patch("app.i18n._windows_ui_language_id", return_value=None), mock.patch("app.i18n._locale_candidates", return_value=["de_DE"]):
            self.assertEqual(detect_default_language(), "en")


class ExifToolRunnerRobustnessTests(unittest.TestCase):
    def test_permission_error_becomes_exiftool_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "exiftool.exe"
            fake.write_bytes(b"")
            (Path(tmp) / "exiftool_files").mkdir()
            runner = ExifToolRunner(fake)
            with mock.patch("app.exiftool_runner.subprocess.run", side_effect=PermissionError(13, "blocked")):
                with self.assertRaises(ExifToolError):
                    runner.read_many([Path(tmp) / "a.jpg"])

    def test_executable_without_bundled_layout_is_probed_not_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "exiftool"
            fake.write_bytes(b"")
            runner = ExifToolRunner(fake)
            with mock.patch.object(ExifToolRunner, "_run_version", return_value="13.0"):
                self.assertTrue(runner.available)
            broken = ExifToolRunner(fake)
            with mock.patch.object(ExifToolRunner, "_run_version", return_value=None):
                self.assertFalse(broken.available)

    def test_from_settings_ignores_a_missing_explicit_path(self):
        settings = AppSettings(exiftool_path="C:/does/not/exist/exiftool.exe", exiftool_batch_size=50)
        runner = ExifToolRunner.from_settings(settings)
        self.assertEqual(runner.batch_size, 50)
        self.assertNotEqual(str(runner.executable), settings.exiftool_path)

    def test_paths_go_through_a_utf8_argfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "exiftool.exe"
            fake.write_bytes(b"")
            (Path(tmp) / "exiftool_files").mkdir()
            runner = ExifToolRunner(fake)
            seen: dict[str, object] = {}

            def fake_run(command, **kwargs):
                argfile = Path(command[command.index("-@") + 1])
                seen["lines"] = argfile.read_text(encoding="utf-8").splitlines()
                seen["charset"] = command[command.index("-charset") + 1]
                return mock.Mock(returncode=0, stdout="[]", stderr="")

            with mock.patch("app.exiftool_runner.subprocess.run", side_effect=fake_run):
                runner.read_many([Path(tmp) / "Фото" / "IMG_тест.jpg"])
            self.assertEqual(seen["charset"], "filename=UTF8")
            self.assertTrue(seen["lines"][0].endswith("IMG_тест.jpg"))


class MetadataReaderDegradationTests(unittest.TestCase):
    def test_a_failing_exiftool_batch_degrades_to_fallback_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            files = []
            for index in range(3):
                path = source / f"IMG_{index}.jpg"
                path.write_bytes(b"\xff\xd8\xff" + bytes([index]))
                files.append(path)
            fake_runner = mock.Mock()
            fake_runner.available = True
            fake_runner.read_many.side_effect = ExifToolError(technical_reason="boom")
            reader = MetadataReader(exiftool=fake_runner, worker_count=1, batch_size=2)
            exif_calls: list[tuple[int, int]] = []
            items = reader.read_many(files, source, threading.Event(), exif_progress=lambda done, total: exif_calls.append((done, total)))
            self.assertEqual(len(items), 3)
            self.assertTrue(all("ExifTool не прочитал файл" in item.warnings[0] for item in items))
            self.assertEqual(exif_calls, [(2, 3), (3, 3)])

    def test_cancel_stops_between_exiftool_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            files = [source / f"IMG_{index}.jpg" for index in range(4)]
            for path in files:
                path.write_bytes(b"\xff\xd8\xff")
            cancel = threading.Event()
            fake_runner = mock.Mock()
            fake_runner.available = True

            def read_many(chunk, batch_size):
                cancel.set()
                return {}

            fake_runner.read_many.side_effect = read_many
            reader = MetadataReader(exiftool=fake_runner, worker_count=1, batch_size=2)
            reader.read_many(files, source, cancel)
            self.assertEqual(fake_runner.read_many.call_count, 1)


class CopyPlanCollisionTests(unittest.TestCase):
    def test_overwrite_policy_move_keeps_both_same_named_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            (source / "100APPLE").mkdir(parents=True)
            (source / "101APPLE").mkdir(parents=True)
            first = source / "100APPLE" / "IMG_0001.JPG"
            second = source / "101APPLE" / "IMG_0001.JPG"
            first.write_bytes(b"\xff\xd8\xff first photo")
            second.write_bytes(b"\xff\xd8\xff second photo, longer")
            settings = AppSettings(duplicate_behavior="overwrite")
            analysis = _analysis(source, [_photo(first, "100APPLE/IMG_0001.JPG"), _photo(second, "101APPLE/IMG_0001.JPG")])
            plan = build_copy_plan(analysis, Path(tmp) / "out", settings)
            result = MoveEngine(settings).execute(plan, threading.Event())
            self.assertEqual((result.copied_files, result.errors, result.deleted_sources), (2, 0, 2))
            contents = sorted(op.destination_path.read_bytes() for op in result.operations)
            self.assertEqual(contents, sorted([b"\xff\xd8\xff first photo", b"\xff\xd8\xff second photo, longer"]))

    def test_skip_policy_names_the_dropped_files_in_the_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            (source / "a").mkdir(parents=True)
            (source / "b").mkdir(parents=True)
            first = source / "a" / "IMG.JPG"
            second = source / "b" / "IMG.JPG"
            first.write_bytes(b"1")
            second.write_bytes(b"22")
            plan = build_copy_plan(_analysis(source, [_photo(first, "a/IMG.JPG"), _photo(second, "b/IMG.JPG")]), Path(tmp) / "out", AppSettings(duplicate_behavior="skip"))
            self.assertEqual(len(plan.operations), 1)
            self.assertTrue(any("b/IMG.JPG" in warning for warning in plan.warnings))


class EngineCleanupTests(unittest.TestCase):
    def test_copy_error_leaves_no_partial_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffdata")
            plan = build_copy_plan(_analysis(source, [_photo(photo, "IMG.jpg")]), Path(tmp) / "out", AppSettings())

            def failing_copy2(src, dst):
                Path(dst).write_bytes(b"half")
                raise OSError(28, "No space left on device")

            with mock.patch("app.copy_engine.shutil.copy2", side_effect=failing_copy2):
                result = CopyEngine(AppSettings()).execute(plan, threading.Event())
            self.assertEqual(result.errors, 1)
            self.assertEqual(list(plan.output_root.rglob("*.partial")), [])

    def test_force_unlink_clears_a_read_only_attribute(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protected.jpg"
            path.write_bytes(b"x")
            os.chmod(path, 0o444)
            real_unlink = Path.unlink
            calls = {"count": 0}

            def unlink_once_denied(self, *args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise PermissionError(13, "Access is denied")
                return real_unlink(self, *args, **kwargs)

            with mock.patch.object(Path, "unlink", unlink_once_denied):
                force_unlink(path)
            self.assertFalse(path.exists())

    def test_processed_files_excludes_never_attempted_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            files = []
            for index in range(3):
                path = source / f"IMG_{index}.jpg"
                path.write_bytes(b"\xff\xd8\xff")
                files.append(_photo(path, path.name))
            plan = build_copy_plan(_analysis(source, files), Path(tmp) / "out", AppSettings())
            cancel = threading.Event()
            original = CopyEngine._copy_one

            def copy_then_cancel(self, operation, cancel_event):
                original(self, operation, cancel_event)
                cancel_event.set()

            with mock.patch.object(CopyEngine, "_copy_one", copy_then_cancel):
                result = CopyEngine(AppSettings()).execute(plan, cancel)
            self.assertTrue(result.cancelled)
            self.assertEqual(result.processed_files, 1)
            self.assertEqual(result.skipped_files, 2)


def _move_two(tmp: Path, verify_sha256: bool = True):
    source = tmp / "src"
    source.mkdir()
    files = []
    for index in range(2):
        path = source / f"IMG_{index}.jpg"
        path.write_bytes(b"\xff\xd8\xff" + bytes([index]) * (index + 4))
        files.append(_photo(path, path.name))
    settings = AppSettings(verify_sha256=verify_sha256)
    plan = build_copy_plan(_analysis(source, files), tmp / "out", settings)
    result = MoveEngine(settings).execute(plan, threading.Event())
    return source, result


class JournalAndUndoTests(unittest.TestCase):
    def test_damaged_journal_is_set_aside_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "journal.json"
            journal.write_text("{not json", encoding="utf-8")
            _source, result = _move_two(Path(tmp))
            append_journal_entry(build_journal_entry(result, Path(tmp) / "src"), journal)
            self.assertEqual(len(load_journal(journal)), 1)
            self.assertTrue(list(Path(tmp).glob("journal.json.broken-*")))

    def test_bom_and_null_entries_are_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "journal.json"
            entry = JournalEntry("id1", "copy", "2026-01-01T00:00:00", "C:/s", "C:/o", [], 0, 0, 0, False)
            journal.write_text(json.dumps([None, "junk", entry.to_dict()]), encoding="utf-8-sig")
            loaded = load_journal(journal)
            self.assertEqual([item.entry_id for item in loaded], ["id1"])

    def test_undo_without_sha_still_checks_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, result = _move_two(Path(tmp), verify_sha256=False)
            entry = build_journal_entry(result, source)
            tampered = Path(entry.records[0].destination_path)
            tampered.write_bytes(b"something else entirely, different length")
            ok, reasons = can_undo_move(entry)
            self.assertFalse(ok)
            self.assertIn("изменилось", reasons[0])

    def test_partially_undone_move_can_be_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, result = _move_two(Path(tmp))
            entry = build_journal_entry(result, source)
            # Simulate an undo cancelled after the first file was put back.
            first = entry.records[0]
            shutil.copy2(first.destination_path, first.source_path)
            Path(first.destination_path).unlink()
            ok, reasons = can_undo_move(entry)
            self.assertTrue(ok, reasons)
            undo_result = undo_move(entry)
            self.assertEqual((undo_result.copied_files, undo_result.errors, undo_result.skipped_files), (1, 0, 1))
            self.assertTrue(all(Path(record.source_path).exists() for record in entry.records))
            ok, reasons = can_undo_move(entry)
            self.assertFalse(ok)
            self.assertIn("уже восстановлены", reasons[0])

    def test_quick_check_skips_hashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, result = _move_two(Path(tmp))
            entry = build_journal_entry(result, source)
            with mock.patch("app.operation_journal.sha256_file", side_effect=AssertionError("must not hash")):
                ok, _reasons = can_undo_move(entry, verify_hashes=False)
            self.assertTrue(ok)


class MiscRobustnessTests(unittest.TestCase):
    def test_csv_cells_that_look_like_formulas_are_neutralised(self):
        self.assertEqual(_csv_safe_row(["=HYPERLINK(1)", "+1", "-cmd", "@x", "IMG_0001.jpg", 5]), ["'=HYPERLINK(1)", "'+1", "'-cmd", "'@x", "IMG_0001.jpg", 5])

    def test_non_dict_metadata_cache_entry_is_skipped(self):
        cache = load_cache_entries([{"relative_path": "a.jpg", "file_size": 1, "mtime_ns": 1, "metadata": "oops"}])
        self.assertEqual(cache, {})

    def test_raw_preview_comes_from_the_embedded_jpeg(self):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (40, 30), (1, 2, 3)).save(buffer, format="JPEG")
        runner = mock.Mock()
        runner.available = True
        runner.extract_embedded_preview.return_value = (buffer.getvalue(), "PreviewImage")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IMG.dng"
            path.write_bytes(b"not really a dng")
            info = load_preview_info(path, max_size=20, exiftool=runner)
            self.assertTrue(info.available)
            self.assertEqual((info.width, info.height, info.source), (40, 30, "PreviewImage"))
            runner.extract_embedded_preview.return_value = (None, "unavailable")
            missing = load_preview_info(path, exiftool=runner)
            self.assertFalse(missing.available)
            self.assertIn("RAW/DNG preview placeholder", missing.message)

    def test_heic_without_decoder_falls_back_to_embedded_thumbnail(self):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (16, 8), (9, 9, 9)).save(buffer, format="JPEG")
        runner = mock.Mock()
        runner.available = True
        runner.extract_embedded_preview.return_value = (buffer.getvalue(), "ThumbnailImage")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IMG.heic"
            path.write_bytes(b"\x00\x00\x00\x18ftypheic")
            info = load_preview_info(path, exiftool=runner)
            self.assertTrue(info.available)
            self.assertEqual(info.source, "ThumbnailImage")


if __name__ == "__main__":
    unittest.main()
