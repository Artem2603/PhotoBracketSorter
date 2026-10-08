"""Persistent operation journal and Move undo (feature 7, 1.2.0)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.analysis import analyze_folder
from app.copy_engine import CopyEngine
from app.copy_plan import build_copy_plan
from app.enums import OperationStatus
from app.models import AppSettings, CopyResult
from app.move_engine import MoveEngine
from app.operation_journal import (
    MAX_JOURNAL_ENTRIES,
    JournalEntry,
    OperationRecord,
    append_journal_entry,
    build_journal_entry,
    can_undo_move,
    journal_path,
    load_journal,
    mark_undone,
    save_journal,
    undo_move,
)

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False


def make_source_with_files(tmpdir: Path, count: int = 1) -> Path:
    source = tmpdir / "source"
    source.mkdir(exist_ok=True)
    for index in range(count):
        path = source / f"IMG_{index}.jpg"
        if PIL_AVAILABLE:
            Image.new("RGB", (10, 10), color=(index * 20, index * 20, index * 20)).save(path, format="JPEG")
        else:
            path.write_bytes(b"\xff\xd8\xff" + bytes([index]) * 32)
    return source


def run_move(tmpdir: Path, count: int = 1) -> tuple[Path, CopyResult]:
    source = make_source_with_files(tmpdir, count)
    output = tmpdir / "output"
    output.mkdir(exist_ok=True)
    settings = AppSettings()
    analysis = analyze_folder(source, settings, threading.Event())
    plan = build_copy_plan(analysis, output, settings)
    result = MoveEngine(settings).execute(plan, threading.Event())
    return source, result


def run_copy(tmpdir: Path, count: int = 1):
    source = make_source_with_files(tmpdir, count)
    output = tmpdir / "output"
    output.mkdir(exist_ok=True)
    settings = AppSettings()
    analysis = analyze_folder(source, settings, threading.Event())
    plan = build_copy_plan(analysis, output, settings)
    result = CopyEngine(settings).execute(plan, threading.Event())
    return source, result


class JournalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.journal_file = self.tmpdir / "journal.json"

    def test_build_journal_entry_from_a_real_move_result(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        self.assertEqual(entry.operation_type, "move")
        self.assertEqual(entry.copied_files, 1)
        self.assertEqual(entry.deleted_sources, 1)
        self.assertEqual(len(entry.records), 1)
        self.assertEqual(entry.records[0].status, OperationStatus.COMPLETED.value)
        self.assertTrue(entry.records[0].source_deleted)
        self.assertIsNotNone(entry.records[0].sha256_destination)

    def test_build_journal_entry_from_a_real_copy_result(self):
        source, result = run_copy(self.tmpdir)
        entry = build_journal_entry(result, source)
        self.assertEqual(entry.operation_type, "copy")
        self.assertFalse(entry.records[0].source_deleted)

    def test_append_and_load_round_trips(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        append_journal_entry(entry, self.journal_file)
        reloaded = load_journal(self.journal_file)
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0].entry_id, entry.entry_id)
        self.assertEqual(reloaded[0].records[0].destination_path, entry.records[0].destination_path)

    def test_multiple_appends_accumulate(self):
        for _ in range(3):
            source, result = run_move(self.tmpdir)
            append_journal_entry(build_journal_entry(result, source), self.journal_file)
        self.assertEqual(len(load_journal(self.journal_file)), 3)

    def test_journal_is_trimmed_to_max_entries(self):
        entries = [
            JournalEntry(
                entry_id=str(index),
                operation_type="move",
                created_at="2026-01-01T00:00:00",
                source_root="C:/x",
                output_root="C:/y",
                records=[],
                copied_files=0,
                deleted_sources=0,
                errors=0,
                cancelled=False,
            )
            for index in range(MAX_JOURNAL_ENTRIES + 10)
        ]
        save_journal(entries, self.journal_file)
        reloaded = load_journal(self.journal_file)
        self.assertEqual(len(reloaded), MAX_JOURNAL_ENTRIES)
        # The oldest entries are the ones dropped, not the newest.
        self.assertEqual(reloaded[-1].entry_id, str(MAX_JOURNAL_ENTRIES + 9))

    def test_missing_journal_file_returns_empty_list(self):
        self.assertEqual(load_journal(self.tmpdir / "does_not_exist.json"), [])

    def test_corrupted_journal_file_returns_empty_list_not_a_crash(self):
        self.journal_file.write_text("{not valid json", encoding="utf-8")
        self.assertEqual(load_journal(self.journal_file), [])

    def test_non_list_json_returns_empty_list(self):
        self.journal_file.write_text('{"not": "a list"}', encoding="utf-8")
        self.assertEqual(load_journal(self.journal_file), [])

    def test_mark_undone_persists(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        append_journal_entry(entry, self.journal_file)
        mark_undone(entry.entry_id, "undo-123", self.journal_file)
        reloaded = load_journal(self.journal_file)
        self.assertTrue(reloaded[0].undone)
        self.assertEqual(reloaded[0].undo_entry_id, "undo-123")

    def test_default_journal_path_is_under_appdata(self):
        self.assertTrue(str(journal_path()).endswith("operations_journal.json"))


class CanUndoMoveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def _entry(self):
        source, result = run_move(self.tmpdir)
        return source, build_journal_entry(result, source)

    def test_a_fresh_completed_move_can_be_undone(self):
        _source, entry = self._entry()
        ok, reasons = can_undo_move(entry)
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_copy_entries_cannot_be_undone(self):
        source, result = run_copy(self.tmpdir)
        entry = build_journal_entry(result, source)
        ok, reasons = can_undo_move(entry)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_already_undone_entry_cannot_be_undone_again(self):
        _source, entry = self._entry()
        entry.undone = True
        ok, _reasons = can_undo_move(entry)
        self.assertFalse(ok)

    def test_missing_destination_blocks_undo(self):
        _source, entry = self._entry()
        Path(entry.records[0].destination_path).unlink()
        ok, reasons = can_undo_move(entry)
        self.assertFalse(ok)
        self.assertIn("отсутствует", reasons[0])

    def test_occupied_source_path_blocks_undo(self):
        source, entry = self._entry()
        Path(entry.records[0].source_path).write_bytes(b"something new lives here")
        ok, reasons = can_undo_move(entry)
        self.assertFalse(ok)
        self.assertIn("занят", reasons[0])

    def test_tampered_destination_blocks_undo(self):
        _source, entry = self._entry()
        destination = Path(entry.records[0].destination_path)
        destination.write_bytes(b"tampered, different size and hash entirely")
        ok, reasons = can_undo_move(entry)
        self.assertFalse(ok)
        self.assertIn("изменилось", reasons[0])

    def test_no_completed_records_blocks_undo(self):
        entry = JournalEntry(
            entry_id="x",
            operation_type="move",
            created_at="2026-01-01T00:00:00",
            source_root="C:/x",
            output_root="C:/y",
            records=[],
            copied_files=0,
            deleted_sources=0,
            errors=0,
            cancelled=False,
        )
        ok, reasons = can_undo_move(entry)
        self.assertFalse(ok)


class UndoMoveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def test_undo_restores_the_source_and_removes_the_destination(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        original_path = Path(entry.records[0].source_path)
        destination = Path(entry.records[0].destination_path)
        self.assertFalse(original_path.exists())
        self.assertTrue(destination.exists())

        undo_result = undo_move(entry)
        self.assertEqual(undo_result.copied_files, 1)
        self.assertEqual(undo_result.errors, 0)
        self.assertEqual(undo_result.operation, "undo_move")
        self.assertTrue(original_path.exists())
        self.assertFalse(destination.exists())

    def test_undo_restores_the_original_bytes(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        destination_hash = entry.records[0].sha256_destination
        undo_move(entry)
        from app.integrity import sha256_file

        restored_path = Path(entry.records[0].source_path)
        self.assertEqual(sha256_file(restored_path), destination_hash)

    def test_multi_file_undo_restores_every_file(self):
        source, result = run_move(self.tmpdir, count=3)
        entry = build_journal_entry(result, source)
        undo_result = undo_move(entry)
        self.assertEqual(undo_result.copied_files, 3)
        for record in entry.records:
            self.assertTrue(Path(record.source_path).exists())
            self.assertFalse(Path(record.destination_path).exists())

    def test_cancellation_mid_undo_produces_a_correct_partial_report(self):
        source, result = run_move(self.tmpdir, count=3)
        entry = build_journal_entry(result, source)
        cancel_event = threading.Event()

        def progress(_phase, done, _total):
            if done >= 1:
                cancel_event.set()

        undo_result = undo_move(entry, cancel_event, progress)
        self.assertTrue(undo_result.cancelled)
        self.assertEqual(undo_result.copied_files, 1)
        self.assertEqual(undo_result.skipped_files, 2)
        self.assertEqual(len(undo_result.operations), 3)
        statuses = [operation.status for operation in undo_result.operations]
        self.assertEqual(statuses.count(OperationStatus.COMPLETED), 1)
        self.assertEqual(statuses.count(OperationStatus.SKIPPED), 2)
        # Skipped, not cancelled: nothing was attempted on those two, matching
        # the exact convention MoveEngine._mark_untouched already established.
        self.assertNotIn(OperationStatus.CANCELLED, statuses)

    def test_cancelled_undo_leaves_untouched_files_untouched(self):
        source, result = run_move(self.tmpdir, count=2)
        entry = build_journal_entry(result, source)
        cancel_event = threading.Event()
        cancel_event.set()  # cancel before even the first file
        undo_result = undo_move(entry, cancel_event)
        self.assertEqual(undo_result.copied_files, 0)
        self.assertEqual(undo_result.skipped_files, 2)
        for record in entry.records:
            self.assertFalse(Path(record.source_path).exists())
            self.assertTrue(Path(record.destination_path).exists())

    def test_undo_of_an_already_gone_destination_reports_an_error_not_a_crash(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        Path(entry.records[0].destination_path).unlink()
        undo_result = undo_move(entry)
        self.assertEqual(undo_result.errors, 1)
        self.assertEqual(undo_result.copied_files, 0)

    def test_undo_never_touches_a_re_occupied_source_path(self):
        source, result = run_move(self.tmpdir)
        entry = build_journal_entry(result, source)
        occupied = Path(entry.records[0].source_path)
        occupied.write_bytes(b"do not overwrite me")
        undo_result = undo_move(entry)
        self.assertEqual(undo_result.errors, 1)
        self.assertEqual(occupied.read_bytes(), b"do not overwrite me")


if __name__ == "__main__":
    unittest.main()
