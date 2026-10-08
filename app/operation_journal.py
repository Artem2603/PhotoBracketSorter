"""Persistent operation manifest and Move undo (feature 7, 1.2.0).

Every Copy/Move run already produces a :class:`app.models.CopyResult` full of
:class:`app.models.CopyOperation` records — source path, destination path,
size, mtime, fingerprint, resulting hash, status, error. Nothing about that
data previously survived past the in-memory report written by
:mod:`app.reporting`. This module persists it to
``%APPDATA%\\PhotoBracketSorter\\operations_journal.json`` as a bounded history,
and adds the one thing a manifest alone doesn't give you: undoing a completed
Move safely.

Undo is not "Move in reverse" run blind. It is only offered when, right now:

* the destination file still exists,
* its SHA-256 still matches what the original Move actually wrote (not just
  what it meant to write — files can change after being sorted), and
* the original source path is free (nothing occupies it, so restoring cannot
  silently overwrite unrelated data).

Undo goes through the same partial-file-then-atomic-replace discipline as
:class:`app.move_engine.MoveEngine`, and produces its own journal entry and its
own partial-report-on-cancel, exactly like the original Move does.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.enums import OperationStatus
from app.errors import CopyVerificationError, InsufficientSpaceError
from app.integrity import force_unlink, sha256_file
from app.models import CopyOperation, CopyResult

#: How many runs to keep. Old entries are dropped, oldest first, never silently
#: truncating a record's own field list.
MAX_JOURNAL_ENTRIES = 200

ProgressCallback = Callable[[str, int, int], None]


@dataclasses.dataclass(slots=True)
class OperationRecord:
    source_path: str
    destination_path: str
    category: str
    expected_size: int
    expected_mtime_ns: int
    expected_fingerprint: str | None
    sha256_source: str | None
    sha256_destination: str | None
    status: str
    error: str | None
    series_id: str | None
    pair_id: str | None
    source_deleted: bool

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OperationRecord":
        known = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    @classmethod
    def from_operation(cls, operation: CopyOperation) -> "OperationRecord":
        return cls(
            source_path=str(operation.source_path),
            destination_path=str(operation.destination_path),
            category=operation.category.value,
            expected_size=operation.expected_size,
            expected_mtime_ns=operation.expected_mtime_ns,
            expected_fingerprint=operation.expected_fingerprint,
            sha256_source=operation.sha256_source,
            sha256_destination=operation.sha256_destination,
            status=operation.status.value,
            error=operation.error,
            series_id=operation.series_id,
            pair_id=operation.pair_id,
            source_deleted=operation.source_deleted,
        )


@dataclasses.dataclass(slots=True)
class JournalEntry:
    entry_id: str
    operation_type: str  # "copy" | "move" | "undo_move"
    created_at: str
    source_root: str
    output_root: str
    records: list[OperationRecord]
    copied_files: int
    deleted_sources: int
    errors: int
    cancelled: bool
    undone: bool = False
    undo_entry_id: str | None = None

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["records"] = [record.to_dict() for record in self.records]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "JournalEntry":
        known = {field.name for field in dataclasses.fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        values["records"] = [OperationRecord.from_dict(item) for item in values.get("records", [])]
        values.setdefault("undone", False)
        values.setdefault("undo_entry_id", None)
        return cls(**values)


def journal_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "PhotoBracketSorter" / "operations_journal.json"


def load_journal(path: Path | None = None) -> list[JournalEntry]:
    """Read the journal; never raises. A damaged entry is skipped, a damaged file reads as empty."""
    return _read_journal(path or journal_path())[0]


def _read_journal(journal_file: Path) -> tuple[list[JournalEntry], bool]:
    """Return ``(entries, file_is_damaged)``.

    The flag lets writers set a damaged file aside instead of overwriting it:
    the history is the only record of which originals a Move deleted.
    """
    if not journal_file.exists():
        return [], False
    try:
        data = json.loads(journal_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return [], True
    if not isinstance(data, list):
        return [], True
    entries = []
    for item in data:
        if isinstance(item, dict):
            try:
                entries.append(JournalEntry.from_dict(item))
            except Exception:
                continue
    return entries, False


def _set_aside_damaged(journal_file: Path) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        journal_file.replace(journal_file.with_name(f"{journal_file.name}.broken-{stamp}"))
    except OSError:
        pass


def save_journal(entries: list[JournalEntry], path: Path | None = None) -> None:
    journal_file = path or journal_path()
    journal_file.parent.mkdir(parents=True, exist_ok=True)
    trimmed = entries[-MAX_JOURNAL_ENTRIES:]
    text = json.dumps([entry.to_dict() for entry in trimmed], ensure_ascii=False, indent=2)
    # Write-then-rename so a crash mid-write cannot truncate the history.
    partial = journal_file.with_name(journal_file.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, journal_file)


def build_journal_entry(result: CopyResult, source_root: Path) -> JournalEntry:
    return JournalEntry(
        entry_id=uuid.uuid4().hex,
        operation_type=result.operation,
        created_at=result.finished_at.isoformat(timespec="seconds"),
        source_root=str(source_root),
        output_root=str(result.output_root),
        records=[OperationRecord.from_operation(operation) for operation in result.operations],
        copied_files=result.copied_files,
        deleted_sources=result.deleted_sources,
        errors=result.errors,
        cancelled=result.cancelled,
    )


def append_journal_entry(entry: JournalEntry, path: Path | None = None) -> None:
    journal_file = path or journal_path()
    entries, damaged = _read_journal(journal_file)
    if damaged:
        _set_aside_damaged(journal_file)
    entries.append(entry)
    save_journal(entries, journal_file)


def mark_undone(entry_id: str, undo_entry_id: str, path: Path | None = None) -> None:
    journal_file = path or journal_path()
    entries, damaged = _read_journal(journal_file)
    if damaged:
        _set_aside_damaged(journal_file)
    for entry in entries:
        if entry.entry_id == entry_id:
            entry.undone = True
            entry.undo_entry_id = undo_entry_id
            break
    save_journal(entries, journal_file)


# --------------------------------------------------------------------- undo


def _already_restored(record: OperationRecord) -> bool:
    """True when an earlier (cancelled) undo of this entry already put this file back."""
    return not Path(record.destination_path).exists() and Path(record.source_path).exists()


def can_undo_move(entry: JournalEntry, verify_hashes: bool = True) -> tuple[bool, list[str]]:
    """Check every precondition without moving anything. Never raises.

    ``verify_hashes=False`` skips the SHA-256 pass (which reads every moved
    file) so a GUI can run a quick check on its main thread and leave the full
    verification to the worker. Files an earlier, cancelled undo already
    restored are not blockers: the remaining files can still be undone.
    """
    reasons: list[str] = []
    if entry.operation_type != "move":
        return False, ["Эта запись не является перемещением."]
    if entry.undone:
        return False, ["Перемещение уже отменено."]
    completed = [record for record in entry.records if record.status == OperationStatus.COMPLETED.value and record.source_deleted]
    if not completed:
        return False, ["Нет успешно перемещённых файлов для отмены."]
    remaining = 0
    for record in completed:
        destination = Path(record.destination_path)
        source = Path(record.source_path)
        if _already_restored(record):
            continue
        if not destination.exists():
            reasons.append(f"{destination.name}: файл результата отсутствует.")
            continue
        if source.exists():
            reasons.append(f"{source.name}: исходный путь уже занят.")
            continue
        try:
            current_size = destination.stat().st_size
        except OSError as exc:
            reasons.append(f"{destination.name}: не удалось прочитать файл ({exc}).")
            continue
        # Size is checked even without a recorded hash (verify_sha256 off), so
        # undo never copies a foreign file over the original's path blindly.
        if current_size != record.expected_size:
            reasons.append(f"{destination.name}: содержимое изменилось после перемещения (размер не совпадает).")
            continue
        if verify_hashes and record.sha256_destination:
            try:
                current_hash = sha256_file(destination)
            except OSError as exc:
                reasons.append(f"{destination.name}: не удалось прочитать файл ({exc}).")
                continue
            if current_hash != record.sha256_destination:
                reasons.append(f"{destination.name}: содержимое изменилось после перемещения.")
                continue
        remaining += 1
    if not reasons and remaining == 0:
        return False, ["Все файлы этого перемещения уже восстановлены."]
    return not reasons, reasons


def undo_move(
    entry: JournalEntry,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> CopyResult:
    """Reverse a completed Move: copy each file back to its original source path.

    Raises :class:`app.errors.ManualSeriesError`-style errors only through the
    caller's own :func:`can_undo_move` check — this function assumes that check
    already passed and re-verifies the same three conditions per file as it
    goes, since state can change between the check and the restore.
    """
    cancel_event = cancel_event or threading.Event()
    started = datetime.now()
    completed = [record for record in entry.records if record.status == OperationStatus.COMPLETED.value and record.source_deleted]
    total = len(completed)
    # Every record starts PENDING, exactly like MoveEngine.execute: a record the
    # loop never reaches stays PENDING and is swept to SKIPPED afterward, rather
    # than being marked CANCELLED for work that was never attempted.
    operations = [_operation_for(record, OperationStatus.PENDING) for record in completed]
    restored = 0
    errors = 0
    already = 0
    cancelled = False
    for index, (record, operation) in enumerate(zip(completed, operations), start=1):
        if cancel_event.is_set():
            cancelled = True
            break
        if progress:
            progress("Восстановление", index, total)
        if _already_restored(record):
            # Put back by an earlier undo that was cancelled part-way.
            operation.status = OperationStatus.SKIPPED
            already += 1
            continue
        operation.status = OperationStatus.RUNNING
        try:
            _restore_one(record)
            operation.status = OperationStatus.COMPLETED
            restored += 1
        except Exception as exc:
            operation.status = OperationStatus.ERROR
            operation.error = str(exc)
            errors += 1
    skipped = 0
    if cancelled:
        for operation in operations:
            if operation.status is OperationStatus.PENDING:
                operation.status = OperationStatus.SKIPPED
                skipped += 1
    return CopyResult(
        output_root=Path(entry.output_root),
        operations=operations,
        copied_files=restored,
        skipped_duplicates=0,
        errors=errors,
        cancelled=cancelled,
        started_at=started,
        finished_at=datetime.now(),
        operation="undo_move",
        deleted_sources=restored,  # the destination copy is removed once restored
        skipped_files=skipped + already,
    )


def _operation_for(record: OperationRecord, status: OperationStatus) -> CopyOperation:
    from app.enums import DestinationCategory

    return CopyOperation(
        source_path=Path(record.destination_path),
        destination_path=Path(record.source_path),
        category=DestinationCategory(record.category),
        relative_source=Path(record.destination_path).name,
        expected_size=record.expected_size,
        series_id=record.series_id,
        pair_id=record.pair_id,
        status=status,
    )


def _restore_one(record: OperationRecord) -> None:
    destination_now = Path(record.destination_path)  # where the sorted file currently lives
    original_source = Path(record.source_path)  # where it must go back to

    if not destination_now.exists():
        raise FileNotFoundError(f"Файл результата отсутствует: {destination_now}")
    if original_source.exists():
        raise CopyVerificationError("Исходный путь уже занят.", str(original_source))
    current_size = destination_now.stat().st_size
    if current_size != record.expected_size:
        raise CopyVerificationError("Содержимое изменилось после перемещения (размер не совпадает).", str(destination_now))
    if record.sha256_destination:
        current_hash = sha256_file(destination_now)
        if current_hash != record.sha256_destination:
            raise CopyVerificationError("Содержимое изменилось после перемещения.", str(destination_now))

    original_source.parent.mkdir(parents=True, exist_ok=True)
    partial = original_source.with_name(original_source.name + ".partial")
    if partial.exists():
        force_unlink(partial)

    usage = shutil.disk_usage(original_source.parent)
    if usage.free < current_size:
        raise InsufficientSpaceError(technical_reason=f"Нужно {current_size} байт, свободно {usage.free} байт.")

    try:
        shutil.copy2(destination_now, partial)
        if partial.stat().st_size != current_size:
            raise CopyVerificationError("Размер восстановленной копии не совпадает.", str(original_source))
        if record.sha256_destination:
            restored_hash = sha256_file(partial)
            if restored_hash != record.sha256_destination:
                raise CopyVerificationError("SHA-256 восстановленной копии не совпадает.", str(original_source))
        os.replace(partial, original_source)
    except BaseException:
        if partial.exists():
            force_unlink(partial)
        raise
    force_unlink(destination_now)
