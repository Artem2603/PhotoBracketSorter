from datetime import datetime
from pathlib import Path
import tempfile
import threading
import unittest

from app.copy_engine import CopyEngine
from app.copy_plan import build_copy_plan
from app.enums import ConfidenceLevel, DestinationCategory, DetectedType
from app.errors import CopyCancelled
from app.integrity import sha256_file
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata
from app.paths import validate_output_parent
from app.reporting import write_reports


class CopyAndReportTests(unittest.TestCase):
    def test_copy_plan_engine_and_reports_keep_source(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            output = Path(output_dir)
            photo = source / "IMG_0001.jpg"
            original_bytes = b"\xff\xd8\xff\xe0" + b"photo-data"
            photo.write_bytes(original_bytes)
            metadata = PhotoMetadata(
                source_path=photo,
                relative_path=Path("IMG_0001.jpg"),
                file_name="IMG_0001.jpg",
                extension=".jpg",
                detected_type=DetectedType.JPEG,
                file_size=len(original_bytes),
                capture_time=datetime(2026, 7, 1, 12, 0, 0),
            )
            file = PhotoFile(
                metadata=metadata,
                destination_category=DestinationCategory.HEIC,
                destination_subpath=Path("HEIC") / "IMG_0001.jpg",
                confidence=90.0,
                confidence_level=ConfidenceLevel.HIGH,
                decision_reasons=["RENDERED_FORMAT"],
            )
            analysis = AnalysisResult(
                source_root=source,
                files=[file],
                pairs=[],
                bracket_candidates=[],
                bracket_series=[],
                statistics={
                    "total_discovered": 1,
                    "supported": 1,
                    "heic_jpeg": 1,
                    "raw_single": 0,
                    "series_3": 0,
                    "series_5": 0,
                    "review": 0,
                    "skipped": 0,
                    "errors": 0,
                    "bytes_planned": len(original_bytes),
                },
                warnings=[],
                created_at=datetime.now(),
                settings_snapshot=AppSettings().snapshot(),
                fingerprint="abc",
            )
            plan = build_copy_plan(analysis, output, AppSettings())
            result = CopyEngine(AppSettings()).execute(plan, threading.Event())
            write_reports(analysis, result, AppSettings())
            self.assertEqual(photo.read_bytes(), original_bytes)
            self.assertTrue((result.output_root / "HEIC" / "IMG_0001.jpg").exists())
            self.assertTrue((result.output_root / "RAW").is_dir())
            self.assertTrue((result.output_root / "BRACKETING_3").is_dir())
            self.assertTrue((result.output_root / "BRACKETING_5").is_dir())
            self.assertTrue((result.output_root / "summary.json").exists())
            self.assertTrue((result.output_root / "report.csv").exists())
            self.assertTrue((result.output_root / "report.txt").exists())

    def test_name_collisions_get_suffixes(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            output = Path(output_dir)
            first = source / "a" / "IMG.jpg"
            second = source / "b" / "IMG.jpg"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"\xff\xd8\xffone")
            second.write_bytes(b"\xff\xd8\xfftwo")
            files = [
                _photo_file(first, Path("a/IMG.jpg"), "IMG.jpg", len(first.read_bytes())),
                _photo_file(second, Path("b/IMG.jpg"), "IMG.jpg", len(second.read_bytes())),
            ]
            analysis = _analysis(source, files)
            plan = build_copy_plan(analysis, output, AppSettings())
            destinations = sorted(operation.destination_path.name for operation in plan.operations)
            self.assertEqual(destinations, ["IMG.jpg", "IMG_02.jpg"])

    def test_sha256_helper_matches_known_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.bin"
            path.write_bytes(b"abc")
            self.assertEqual(sha256_file(path), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_copy_cancelled_before_start(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as output_dir:
            source = Path(source_dir)
            photo = source / "IMG.jpg"
            photo.write_bytes(b"\xff\xd8\xffdata")
            analysis = _analysis(source, [_photo_file(photo, Path("IMG.jpg"), "IMG.jpg", photo.stat().st_size)])
            plan = build_copy_plan(analysis, Path(output_dir), AppSettings())
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(CopyCancelled):
                CopyEngine(AppSettings()).execute(plan, cancel)

    def test_output_inside_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            output = source / "child"
            with self.assertRaises(Exception):
                validate_output_parent(output, source)


def _photo_file(path: Path, relative: Path, name: str, size: int) -> PhotoFile:
    metadata = PhotoMetadata(
        source_path=path,
        relative_path=relative,
        file_name=name,
        extension=Path(name).suffix.lower(),
        detected_type=DetectedType.JPEG,
        file_size=size,
        capture_time=datetime(2026, 7, 1, 12, 0, 0),
    )
    return PhotoFile(
        metadata=metadata,
        destination_category=DestinationCategory.HEIC,
        destination_subpath=Path("HEIC") / name,
        confidence=90.0,
        confidence_level=ConfidenceLevel.HIGH,
        decision_reasons=["RENDERED_FORMAT"],
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
        fingerprint="abc",
    )


if __name__ == "__main__":
    unittest.main()
