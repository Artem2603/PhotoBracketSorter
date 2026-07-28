"""Copy/Move verification, honest reporting, and privacy redaction (P0-4, P1-7)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from app.constants import REPORT_CSV_NAME, REPORT_JSON_NAME, REPORT_TEXT_NAME
from app.copy_engine import CopyEngine
from app.enums import DestinationCategory, DetectedType, OperationStatus
from app.errors import CopyVerificationError
from app.integrity import quick_fingerprint, verify_unchanged
from app.models import (
    AnalysisResult,
    AppSettings,
    CopyOperation,
    CopyPlan,
    CopyResult,
    PhotoFile,
    PhotoMetadata,
)
from app.move_engine import MoveEngine
from app.reporting import redact_settings_snapshot, write_reports


def write_photo(path: Path, payload: bytes = b"\xff\xd8\xff" + b"photo-body" * 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def make_operation(source: Path, destination: Path) -> CopyOperation:
    stat = source.stat()
    return CopyOperation(
        source_path=source,
        destination_path=destination,
        category=DestinationCategory.RAW,
        relative_source=Path(source.name),
        expected_size=stat.st_size,
        expected_mtime_ns=stat.st_mtime_ns,
        expected_fingerprint=quick_fingerprint(source),
    )


def make_plan(output_root: Path, operations: list[CopyOperation]) -> CopyPlan:
    return CopyPlan(
        output_root=output_root,
        operations=tuple(operations),
        total_bytes=sum(operation.expected_size for operation in operations),
        expected_counts={},
        required_space=1024,
        warnings=(),
        unresolved_review=0,
        created_at=datetime.now(),
        analysis_fingerprint="test",
    )


class SourceVerificationTests(unittest.TestCase):
    """Size alone never proved a file was unchanged."""

    def test_unchanged_file_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "a.dng"
            write_photo(photo)
            stat = photo.stat()
            self.assertIsNone(
                verify_unchanged(photo, stat.st_size, stat.st_mtime_ns, quick_fingerprint(photo))
            )

    def test_size_change_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "a.dng"
            write_photo(photo)
            stat = photo.stat()
            photo.write_bytes(b"short")
            self.assertIsNotNone(verify_unchanged(photo, stat.st_size, stat.st_mtime_ns))

    def test_same_size_edit_is_detected_by_content(self):
        """The dangerous case: an in-place edit that preserves the byte count."""
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "a.dng"
            write_photo(photo)
            stat = photo.stat()
            fingerprint = quick_fingerprint(photo)
            body = bytearray(photo.read_bytes())
            body[0:3] = b"\x00\x00\x00"
            photo.write_bytes(bytes(body))
            os.utime(photo, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # forge the mtime too
            self.assertEqual(photo.stat().st_size, stat.st_size)
            reason = verify_unchanged(photo, stat.st_size, stat.st_mtime_ns, fingerprint)
            self.assertIsNotNone(reason, "same-size, same-mtime edit must still be caught")
            self.assertIn("одержимое", reason)

    def test_mtime_change_alone_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "a.dng"
            write_photo(photo)
            stat = photo.stat()
            time.sleep(0.01)
            os.utime(photo)
            self.assertIsNotNone(verify_unchanged(photo, stat.st_size, stat.st_mtime_ns))

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(verify_unchanged(Path(tmp) / "gone.dng", 10, 0))

    def test_move_refuses_to_delete_a_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            write_photo(photo, b"\xff\xd8\xffdifferent-content-entirely" * 40)
            result = MoveEngine(AppSettings()).execute(make_plan(root / "out", [operation]))
            self.assertIs(operation.status, OperationStatus.ERROR)
            self.assertTrue(photo.exists(), "the original must survive a failed verification")
            self.assertEqual(result.deleted_sources, 0)

    def test_copy_refuses_a_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            write_photo(photo, b"\xff\xd8\xffchanged" * 90)
            result = CopyEngine(AppSettings()).execute(make_plan(root / "out", [operation]))
            self.assertEqual(result.errors, 1)
            self.assertIs(operation.status, OperationStatus.ERROR)


class MidRunCancelTests(unittest.TestCase):
    """A cancelled run must still describe what it did."""

    def _plan_with(self, root: Path, count: int) -> tuple[CopyPlan, list[CopyOperation]]:
        operations = []
        for index in range(count):
            photo = root / "src" / f"a{index}.dng"
            write_photo(photo)
            operations.append(make_operation(photo, root / "out" / "RAW" / f"a{index}.dng"))
        return make_plan(root / "out", operations), operations

    def test_cancel_midway_returns_a_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operations = self._plan_with(root, 4)
            cancel = threading.Event()

            def events(phase, done, total):
                if done >= 2:
                    cancel.set()

            result = CopyEngine(AppSettings()).execute(plan, cancel, events)
            self.assertTrue(result.cancelled)
            self.assertGreater(result.copied_files, 0)
            self.assertLess(result.copied_files, 4)
            self.assertGreater(result.skipped_files, 0)

    def test_every_operation_ends_with_an_explicit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operations = self._plan_with(root, 4)
            cancel = threading.Event()
            result = CopyEngine(AppSettings()).execute(plan, cancel, lambda p, d, t: cancel.set())
            self.assertTrue(result.cancelled)
            for operation in operations:
                self.assertIsNot(operation.status, OperationStatus.PENDING)

    def test_cancelled_move_does_not_delete_unprocessed_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operations = self._plan_with(root, 4)
            cancel = threading.Event()

            def events(phase, done, total):
                if done >= 2:
                    cancel.set()

            result = MoveEngine(AppSettings()).execute(plan, cancel, events)
            self.assertTrue(result.cancelled)
            survivors = [op for op in operations if op.status is OperationStatus.SKIPPED]
            self.assertTrue(survivors)
            for operation in survivors:
                self.assertTrue(operation.source_path.exists())
                self.assertFalse(operation.source_deleted)

    def test_cancel_before_start_still_raises(self):
        from app.errors import CopyCancelled

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, _operations = self._plan_with(root, 2)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(CopyCancelled):
                CopyEngine(AppSettings()).execute(plan, cancel)


class ReportHonestyTests(unittest.TestCase):
    def _analysis(self, root: Path) -> AnalysisResult:
        return AnalysisResult(
            source_root=root / "src",
            files=[],
            pairs=[],
            bracket_candidates=[],
            bracket_series=[],
            statistics={"series_3": 0, "series_5": 0},
            warnings=[],
            created_at=datetime.now(),
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="fp",
        )

    def test_move_report_never_claims_originals_are_intact(self):
        """Regression: the text report said "Оригиналы не изменены" after a move."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            result = MoveEngine(AppSettings()).execute(make_plan(root / "out", [operation]))
            write_reports(self._analysis(root), result, AppSettings())
            text = (root / "out" / REPORT_TEXT_NAME).read_text(encoding="utf-8")
            self.assertNotIn("Оригиналы не изменены", text)
            self.assertIn("оригиналы удалены", text.lower())
            self.assertIn("Перемещение", text)

    def test_copy_report_still_states_originals_are_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            result = CopyEngine(AppSettings()).execute(make_plan(root / "out", [operation]))
            write_reports(self._analysis(root), result, AppSettings())
            text = (root / "out" / REPORT_TEXT_NAME).read_text(encoding="utf-8")
            self.assertIn("Оригиналы не изменены", text)

    def test_summary_json_records_the_operation_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            result = MoveEngine(AppSettings()).execute(make_plan(root / "out", [operation]))
            write_reports(self._analysis(root), result, AppSettings())
            data = json.loads((root / "out" / REPORT_JSON_NAME).read_text(encoding="utf-8"))
            self.assertEqual(data["operation"], "move")
            self.assertTrue(data["originals_deleted"])
            self.assertEqual(data["deleted_sources"], 1)
            self.assertTrue(data["operations"][0]["source_deleted"])

    def test_partial_report_breaks_results_down_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            operations = []
            for index in range(4):
                photo = root / "src" / f"a{index}.dng"
                write_photo(photo)
                operations.append(make_operation(photo, root / "out" / "RAW" / f"a{index}.dng"))
            cancel = threading.Event()

            def events(phase, done, total):
                if done >= 2:
                    cancel.set()

            result = MoveEngine(AppSettings()).execute(make_plan(root / "out", operations), cancel, events)
            write_reports(self._analysis(root), result, AppSettings())
            data = json.loads((root / "out" / REPORT_JSON_NAME).read_text(encoding="utf-8"))
            self.assertTrue(data["cancelled"])
            self.assertIn("status_counts", data)
            self.assertEqual(data["planned_files"], 4)
            self.assertGreater(data["skipped_files"], 0)
            text = (root / "out" / REPORT_TEXT_NAME).read_text(encoding="utf-8")
            self.assertIn("отменена", text.lower())

    def test_csv_records_whether_the_source_was_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            result = MoveEngine(AppSettings()).execute(make_plan(root / "out", [operation]))
            write_reports(self._analysis(root), result, AppSettings())
            csv_text = (root / "out" / REPORT_CSV_NAME).read_text(encoding="utf-8-sig")
            self.assertIn("source_deleted", csv_text.splitlines()[0])
            self.assertIn("yes", csv_text.splitlines()[1])


class PrivacyRedactionTests(unittest.TestCase):
    """Regression: absolute paths leaked through the settings snapshot."""

    def test_path_settings_are_redacted(self):
        settings = AppSettings()
        settings.last_source_path = r"C:\Users\Ivan\Photos\Vacation"
        settings.last_output_path = r"D:\Sorted"
        settings.exiftool_path = r"C:\Users\Ivan\tools\exiftool.exe"
        redacted = redact_settings_snapshot(settings.snapshot(), settings)
        blob = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn("Ivan", blob)
        self.assertNotIn("D:\\\\Sorted", blob)
        self.assertEqual(redacted["last_source_path"], "<source_root>")
        self.assertEqual(redacted["last_output_path"], "<output_root>")
        self.assertEqual(redacted["exiftool_path"], "<exiftool_path>")

    def test_redaction_can_be_disabled(self):
        settings = AppSettings()
        settings.privacy_redact_absolute_paths = False
        settings.last_source_path = r"C:\Users\Ivan\Photos"
        redacted = redact_settings_snapshot(settings.snapshot(), settings)
        self.assertEqual(redacted["last_source_path"], r"C:\Users\Ivan\Photos")

    def test_empty_path_settings_are_left_alone(self):
        settings = AppSettings()
        redacted = redact_settings_snapshot(settings.snapshot(), settings)
        self.assertEqual(redacted["last_source_path"], "")

    def test_summary_json_leaks_no_user_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "src" / "a.dng"
            write_photo(photo)
            operation = make_operation(photo, root / "out" / "RAW" / "a.dng")
            settings = AppSettings()
            settings.last_source_path = r"C:\Users\SecretPerson\Pictures"
            settings.exiftool_path = r"C:\Users\SecretPerson\exiftool.exe"
            analysis = AnalysisResult(
                source_root=root / "src",
                files=[],
                pairs=[],
                bracket_candidates=[],
                bracket_series=[],
                statistics={},
                warnings=[],
                created_at=datetime.now(),
                settings_snapshot=settings.snapshot(),
                fingerprint="fp",
            )
            result = CopyEngine(settings).execute(make_plan(root / "out", [operation]))
            write_reports(analysis, result, settings)
            blob = (root / "out" / REPORT_JSON_NAME).read_text(encoding="utf-8")
            self.assertNotIn("SecretPerson", blob)
            self.assertNotIn(str(root), blob, "the absolute output root must not be written either")


if __name__ == "__main__":
    unittest.main()
