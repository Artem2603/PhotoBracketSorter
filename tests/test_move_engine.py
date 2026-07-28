from datetime import datetime
from pathlib import Path
import inspect
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.copy_plan import build_copy_plan
from app.enums import ConfidenceLevel, DestinationCategory, DetectedType, OperationStatus
from app.errors import InsufficientSpaceError, MoveCancelled
from app.integrity import sha256_file
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata
from app.move_engine import MoveEngine


class MoveEngineTests(unittest.TestCase):
    def test_move_success_deletes_source_after_verified_copy(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffmove")
            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            result = MoveEngine(AppSettings()).execute(plan, threading.Event())
            destination = result.operations[0].destination_path
            self.assertFalse(photo.exists())
            self.assertTrue(destination.exists())
            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.errors, 0)
            self.assertEqual(result.operations[0].status, OperationStatus.COMPLETED)

    def test_move_uses_sha256_when_enabled(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffsha")
            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            result = MoveEngine(AppSettings(verify_sha256=True)).execute(plan, threading.Event())
            operation = result.operations[0]
            self.assertEqual(operation.sha256_source, operation.sha256_destination)
            self.assertEqual(operation.sha256_destination, sha256_file(operation.destination_path))

    def test_delete_failure_keeps_source_and_reports_delete_failed(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffdelete")

            def fail_delete(_path: Path) -> None:
                raise PermissionError("locked")

            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            result = MoveEngine(AppSettings(), delete_func=fail_delete).execute(plan, threading.Event())
            self.assertTrue(photo.exists())
            self.assertTrue(result.operations[0].destination_path.exists())
            self.assertEqual(result.errors, 1)
            self.assertEqual(result.operations[0].status, OperationStatus.DELETE_FAILED)
            self.assertIn("Скопировано, но не удалено", result.operations[0].error)

    def test_cancelled_before_start_raises_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffcancel")
            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(MoveCancelled):
                MoveEngine(AppSettings()).execute(plan, cancel)
            self.assertTrue(photo.exists())

    def test_hash_failure_does_not_delete_source_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffhash")
            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            with patch("app.move_engine.sha256_file", side_effect=["source-hash", "dest-hash"]):
                result = MoveEngine(AppSettings()).execute(plan, threading.Event())
            operation = result.operations[0]
            self.assertTrue(photo.exists())
            self.assertFalse(operation.destination_path.exists())
            self.assertFalse(operation.destination_path.with_name(operation.destination_path.name + ".partial").exists())
            self.assertEqual(result.errors, 1)
            self.assertEqual(operation.status, OperationStatus.ERROR)

    def test_name_collision_gets_unique_move_destinations(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            first = source / "a" / "IMG.jpg"
            second = source / "b" / "IMG.jpg"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"\xff\xd8\xffone")
            second.write_bytes(b"\xff\xd8\xfftwo")
            plan = build_copy_plan(_analysis(source, [_photo(first, Path("a/IMG.jpg")), _photo(second, Path("b/IMG.jpg"))]), Path(output_dir), AppSettings())
            names = sorted(operation.destination_path.name for operation in plan.operations)
            self.assertEqual(names, ["IMG.jpg", "IMG_02.jpg"])

    def test_russian_paths_move_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "исходники"
            output = root / "результат"
            source.mkdir()
            output.mkdir()
            photo = source / "фото.jpg"
            photo.write_bytes(b"\xff\xd8\xffru")
            plan = build_copy_plan(_analysis(source, [_photo(photo, Path("фото.jpg"))]), output, AppSettings())
            result = MoveEngine(AppSettings()).execute(plan, threading.Event())
            self.assertFalse(photo.exists())
            self.assertTrue(result.operations[0].destination_path.exists())

    def test_repeated_run_creates_unique_output_roots(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo1 = source / "one.jpg"
            photo2 = source / "two.jpg"
            photo1.write_bytes(b"\xff\xd8\xffone")
            photo2.write_bytes(b"\xff\xd8\xfftwo")
            plan1 = build_copy_plan(_analysis(source, [_photo(photo1)]), Path(output_dir), AppSettings())
            result1 = MoveEngine(AppSettings()).execute(plan1, threading.Event())
            plan2 = build_copy_plan(_analysis(source, [_photo(photo2)]), Path(output_dir), AppSettings())
            result2 = MoveEngine(AppSettings()).execute(plan2, threading.Event())
            self.assertNotEqual(result1.output_root, result2.output_root)

    def test_insufficient_space_is_reported_before_delete(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffspace")
            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            with patch("app.move_engine.shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()):
                with self.assertRaises(InsufficientSpaceError):
                    MoveEngine(AppSettings()).execute(plan, threading.Event())
            self.assertTrue(photo.exists())

    def test_move_engine_does_not_call_shutil_move(self):
        import app.move_engine as move_engine

        self.assertNotIn("shutil.move", inspect.getsource(move_engine))

    def test_move_progress_reports_copy_and_delete_phases(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffprogress")
            phases: list[str] = []
            plan = build_copy_plan(_analysis(source, [_photo(photo)]), Path(output_dir), AppSettings())
            MoveEngine(AppSettings()).execute(plan, threading.Event(), lambda phase, _done, _total: phases.append(phase))
            joined = " ".join(phases)
            self.assertIn("Копирование", joined)
            self.assertIn("Удаление", joined)


def _photo(path: Path, relative: Path | None = None) -> PhotoFile:
    relative = relative or Path(path.name)
    metadata = PhotoMetadata(
        source_path=path,
        relative_path=relative,
        file_name=path.name,
        extension=path.suffix.lower(),
        detected_type=DetectedType.JPEG,
        file_size=path.stat().st_size,
        capture_time=datetime(2026, 7, 1, 12, 0, 0),
    )
    return PhotoFile(
        metadata=metadata,
        destination_category=DestinationCategory.HEIC,
        destination_subpath=Path("HEIC") / path.name,
        confidence=90.0,
        confidence_level=ConfidenceLevel.HIGH,
        decision_reasons=["TEST"],
    )


def _analysis(source: Path, files: list[PhotoFile]) -> AnalysisResult:
    return AnalysisResult(
        source_root=source,
        files=files,
        pairs=[],
        bracket_candidates=[],
        bracket_series=[],
        statistics={
            "total_discovered": len(files),
            "supported": len(files),
            "heic_jpeg": len(files),
            "raw_single": 0,
            "series_3": 0,
            "series_5": 0,
            "review": 0,
            "skipped": 0,
            "errors": 0,
            "bytes_planned": sum(file.metadata.file_size for file in files),
        },
        warnings=[],
        created_at=datetime.now(),
        settings_snapshot=AppSettings().snapshot(),
        fingerprint="move",
    )


if __name__ == "__main__":
    unittest.main()
