"""Safe copy execution."""

from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.constants import MAIN_OUTPUT_DIRS
from app.enums import OperationStatus
from app.errors import CopyCancelled, CopyVerificationError, InsufficientSpaceError
from app.integrity import sha256_file, verify_unchanged
from app.models import AppSettings, CopyOperation, CopyPlan, CopyResult

CopyEventCallback = Callable[[str, int, int], None]


class CopyEngine:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings()

    def execute(
        self,
        plan: CopyPlan,
        cancel_event: threading.Event | None = None,
        events: CopyEventCallback | None = None,
    ) -> CopyResult:
        cancel_event = cancel_event or threading.Event()
        started = datetime.now()
        operations = [operation for operation in plan.operations]
        # Cancelled before anything happened: nothing to report, so signal it.
        if cancel_event.is_set():
            raise CopyCancelled()
        self._check_space(plan)
        self._create_structure(plan.output_root)
        copied = 0
        errors = 0
        cancelled = False
        _phase(events, "Создание структуры", 1, 1)
        for index, operation in enumerate(operations, start=1):
            # Cancelled mid-run: stop, but return a result so the caller can still
            # write a report describing what really happened.
            if cancel_event.is_set():
                cancelled = True
                break
            _phase(events, f"Копирование {operation.category.value}", index, len(operations))
            try:
                self._copy_one(operation, cancel_event)
                copied += 1
            except CopyCancelled:
                operation.status = OperationStatus.CANCELLED
                cancelled = True
                break
            except Exception as exc:
                operation.status = OperationStatus.ERROR
                operation.error = str(exc)
                errors += 1
        skipped = self._mark_untouched(operations, cancelled)
        _phase(events, "Финальная проверка", len(operations), len(operations))
        return CopyResult(
            output_root=plan.output_root,
            operations=operations,
            copied_files=copied,
            skipped_duplicates=0,
            errors=errors,
            cancelled=cancelled,
            started_at=started,
            finished_at=datetime.now(),
            operation="copy",
            deleted_sources=0,
            skipped_files=skipped,
        )

    def _mark_untouched(self, operations: list[CopyOperation], cancelled: bool) -> int:
        """Give every unattempted operation an explicit status for the report."""
        if not cancelled:
            return 0
        skipped = 0
        for operation in operations:
            if operation.status is OperationStatus.PENDING:
                operation.status = OperationStatus.SKIPPED
                skipped += 1
        return skipped

    def _check_space(self, plan: CopyPlan) -> None:
        parent = plan.output_root.parent
        usage = shutil.disk_usage(parent)
        if usage.free < plan.required_space:
            raise InsufficientSpaceError(technical_reason=f"Нужно {plan.required_space} байт, свободно {usage.free} байт.")

    def _create_structure(self, output_root: Path) -> None:
        # make_unique_output_root already guaranteed this name was free; tolerate the
        # narrow race where it appeared between planning and execution.
        output_root.mkdir(parents=True, exist_ok=True)
        for dirname in MAIN_OUTPUT_DIRS:
            (output_root / dirname).mkdir(parents=True, exist_ok=True)

    def _copy_one(self, operation: CopyOperation, cancel_event: threading.Event) -> None:
        operation.status = OperationStatus.RUNNING
        source = operation.source_path
        destination = operation.destination_path
        if not source.exists():
            raise FileNotFoundError(str(source))
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
        if partial.exists():
            partial.unlink()
        if self.settings.verify_sha256:
            operation.sha256_source = sha256_file(source)
        if cancel_event.is_set():
            raise CopyCancelled()
        # copy2 carries timestamps across; copy deliberately does not.
        if self.settings.preserve_file_times:
            shutil.copy2(source, partial)
        else:
            shutil.copy(source, partial)
        if cancel_event.is_set():
            _remove_partial(partial)
            raise CopyCancelled()
        if partial.stat().st_size != current_size:
            _remove_partial(partial)
            raise CopyVerificationError("Размер копии не совпадает.", str(destination))
        if self.settings.verify_sha256:
            operation.sha256_destination = sha256_file(partial)
            if operation.sha256_source != operation.sha256_destination:
                _remove_partial(partial)
                raise CopyVerificationError("SHA-256 копии не совпадает.", str(destination))
        os.replace(partial, destination)
        operation.status = OperationStatus.COMPLETED


def _remove_partial(path: Path) -> None:
    if path.exists():
        path.unlink()


def _phase(events: CopyEventCallback | None, phase: str, done: int, total: int) -> None:
    if events:
        events(phase, done, total)
