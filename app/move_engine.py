"""Safe move execution built on the same copy plan as Copy."""

from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.constants import MAIN_OUTPUT_DIRS
from app.enums import OperationStatus
from app.errors import CopyVerificationError, InsufficientSpaceError, MoveCancelled
from app.integrity import force_unlink, sha256_file, verify_unchanged
from app.models import AppSettings, CopyOperation, CopyPlan, CopyResult

MoveEventCallback = Callable[[str, int, int], None]
DeleteFunc = Callable[[Path], None]


class MoveEngine:
    def __init__(self, settings: AppSettings | None = None, delete_func: DeleteFunc | None = None):
        self.settings = settings or AppSettings()
        # force_unlink clears a read-only attribute first: a camera-"protected"
        # source would otherwise end every move as DELETE_FAILED on Windows.
        self._delete = delete_func or force_unlink

    def execute(
        self,
        plan: CopyPlan,
        cancel_event: threading.Event | None = None,
        events: MoveEventCallback | None = None,
    ) -> CopyResult:
        cancel_event = cancel_event or threading.Event()
        started = datetime.now()
        operations = [operation for operation in plan.operations]
        if cancel_event.is_set():
            raise MoveCancelled()
        self._check_space(plan)
        self._create_structure(plan.output_root)
        moved = 0
        errors = 0
        deleted = 0
        cancelled = False
        _phase(events, "Создание структуры", 1, 1)
        for index, operation in enumerate(operations, start=1):
            # Stop cleanly rather than raising: a half-finished move is exactly the
            # case where the user most needs a report of what was already deleted.
            if cancel_event.is_set():
                cancelled = True
                break
            try:
                _phase(events, f"Копирование {operation.category.value}", index, len(operations))
                self._copy_verify_finalize(operation, cancel_event)
                if cancel_event.is_set():
                    # The copy landed but the source is still intact; leave it that way.
                    operation.status = OperationStatus.CANCELLED
                    cancelled = True
                    break
                _phase(events, f"Удаление исходника {operation.category.value}", index, len(operations))
                self._delete_source(operation)
                moved += 1
                if operation.status is OperationStatus.DELETE_FAILED:
                    errors += 1
                else:
                    deleted += 1
            except MoveCancelled:
                operation.status = OperationStatus.CANCELLED
                cancelled = True
                break
            except Exception as exc:
                if operation.status is not OperationStatus.DELETE_FAILED:
                    operation.status = OperationStatus.ERROR
                    operation.error = str(exc)
                errors += 1
        skipped = self._mark_untouched(operations, cancelled)
        _phase(events, "Финальная проверка", len(operations), len(operations))
        return CopyResult(
            output_root=plan.output_root,
            operations=operations,
            copied_files=moved,
            skipped_duplicates=0,
            errors=errors,
            cancelled=cancelled,
            started_at=started,
            finished_at=datetime.now(),
            operation="move",
            deleted_sources=deleted,
            skipped_files=skipped,
        )

    def _mark_untouched(self, operations: list[CopyOperation], cancelled: bool) -> int:
        if not cancelled:
            return 0
        skipped = 0
        for operation in operations:
            if operation.status is OperationStatus.PENDING:
                operation.status = OperationStatus.SKIPPED
                skipped += 1
        return skipped

    def _check_space(self, plan: CopyPlan) -> None:
        usage = shutil.disk_usage(plan.output_root.parent)
        if usage.free < plan.required_space:
            raise InsufficientSpaceError(technical_reason=f"Нужно {plan.required_space} байт, свободно {usage.free} байт.")

    def _create_structure(self, output_root: Path) -> None:
        output_root.mkdir(parents=True, exist_ok=True)
        for dirname in MAIN_OUTPUT_DIRS:
            (output_root / dirname).mkdir(parents=True, exist_ok=True)

    def _copy_verify_finalize(self, operation: CopyOperation, cancel_event: threading.Event) -> None:
        operation.status = OperationStatus.RUNNING
        source = operation.source_path
        destination = operation.destination_path
        if not source.exists():
            raise FileNotFoundError(str(source))
        # Move deletes the original, so verify identity by size, modification time,
        # and a head+tail fingerprint before touching anything.
        changed = verify_unchanged(
            source,
            operation.expected_size,
            operation.expected_mtime_ns,
            operation.expected_fingerprint,
        )
        if changed:
            raise CopyVerificationError(f"Файл изменился после анализа. {changed}", str(source))
        current_size = source.stat().st_size
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        _remove_partial(partial)
        if self.settings.verify_sha256:
            operation.sha256_source = sha256_file(source)
        if cancel_event.is_set():
            raise MoveCancelled()
        try:
            if self.settings.preserve_file_times:
                shutil.copy2(source, partial)
            else:
                shutil.copy(source, partial)
            if cancel_event.is_set():
                raise MoveCancelled()
            if partial.stat().st_size != current_size:
                raise CopyVerificationError("Размер копии не совпадает.", str(destination))
            if self.settings.verify_sha256:
                operation.sha256_destination = sha256_file(partial)
                if operation.sha256_source != operation.sha256_destination:
                    raise CopyVerificationError("SHA-256 копии не совпадает.", str(destination))
            os.replace(partial, destination)
        except BaseException:
            # Never leave a .partial behind, whatever interrupted the copy.
            _remove_partial(partial)
            raise
        operation.status = OperationStatus.COMPLETED

    def _delete_source(self, operation: CopyOperation) -> None:
        try:
            self._delete(operation.source_path)
            operation.source_deleted = True
        except Exception as exc:
            operation.status = OperationStatus.DELETE_FAILED
            operation.error = f"Скопировано, но не удалено: {exc}"


def _remove_partial(path: Path) -> None:
    if path.exists():
        force_unlink(path)


def _phase(events: MoveEventCallback | None, phase: str, done: int, total: int) -> None:
    if events:
        events(phase, done, total)
