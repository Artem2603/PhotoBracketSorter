"""Immutable copy plan creation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from app.collision_handling import unique_destination
from app.constants import MAIN_OUTPUT_DIRS
from app.enums import DestinationCategory
from app.models import AnalysisResult, AppSettings, CopyOperation, CopyPlan
from app.paths import make_unique_output_root, validate_output_parent


def _apply_subfolders(file, settings: AppSettings) -> Path:
    """Optionally mirror the source folder tree inside the destination category.

    ``HEIC/photo.jpg`` becomes ``HEIC/2026/Trip/photo.jpg`` when the source lived in
    ``2026/Trip``. Series members keep their series layout untouched.
    """
    subpath = file.destination_subpath
    if not settings.preserve_source_subfolders:
        return subpath
    if file.series_id:
        return subpath
    source_parent = file.metadata.relative_path.parent
    if source_parent in (Path("."), Path("")):
        return subpath
    return subpath.parent / source_parent / subpath.name


def resolve_destination(base_path: Path, reserved: set[Path], behavior: str) -> Path | None:
    """Apply the duplicate_behavior policy to a destination collision.

    ``rename`` picks a free ``name_02`` variant, ``overwrite`` reuses the path, and
    ``skip`` returns ``None`` so the caller drops the operation.
    """
    if behavior == "overwrite":
        reserved.add(base_path)
        return base_path
    if behavior == "skip":
        if base_path in reserved or base_path.exists():
            return None
        reserved.add(base_path)
        return base_path
    return unique_destination(base_path, reserved)


def build_copy_plan(
    analysis: AnalysisResult,
    output_parent: Path,
    settings: AppSettings | None = None,
    pinned_output_root: Path | None = None,
) -> CopyPlan:
    """Build a plan. ``pinned_output_root`` re-plans against an *existing* root
    instead of minting a new timestamped one — needed when the sort-preview
    dialog (feature 6) rebuilds the plan after every edit: without it, a
    template containing ``{time}`` would drift to a new destination folder on
    every single edit, before a single file had actually moved.
    """
    settings = settings or AppSettings()
    output_dir = validate_output_parent(output_parent, analysis.source_root)
    output_root = pinned_output_root or make_unique_output_root(output_dir, template=settings.output_folder_template)
    reserved: set[Path] = set()
    operations: list[CopyOperation] = []
    unresolved_review = 0
    skipped_duplicates = 0
    warnings: list[str] = []
    for file in analysis.files:
        if file.destination_category in {DestinationCategory.REVIEW, DestinationCategory.SKIP}:
            if file.destination_category is DestinationCategory.REVIEW:
                unresolved_review += 1
            continue
        if not file.selected or file.destination_subpath is None:
            continue
        subpath = _apply_subfolders(file, settings)
        destination = resolve_destination(output_root / subpath, reserved, settings.duplicate_behavior)
        if destination is None:
            skipped_duplicates += 1
            continue
        operations.append(
            CopyOperation(
                source_path=file.metadata.source_path,
                destination_path=destination,
                category=file.destination_category,
                relative_source=file.metadata.relative_path,
                expected_size=file.metadata.file_size,
                expected_mtime_ns=file.metadata.mtime_ns,
                expected_fingerprint=file.metadata.quick_fingerprint,
                series_id=file.series_id,
                pair_id=file.pair_id,
            )
        )
    counts = Counter(operation.category.value for operation in operations)
    for main_dir in MAIN_OUTPUT_DIRS:
        counts.setdefault(main_dir, 0)
    total_bytes = sum(operation.expected_size for operation in operations)
    if unresolved_review:
        warnings.append(f"Файлов для проверки: {unresolved_review}. Они не будут скопированы автоматически.")
    if skipped_duplicates:
        warnings.append(f"Пропущено существующих файлов: {skipped_duplicates} (режим дубликатов «skip»).")
    if settings.duplicate_behavior == "overwrite":
        warnings.append("Режим дубликатов «overwrite»: существующие файлы в папке результата будут перезаписаны.")
    return CopyPlan(
        output_root=output_root,
        operations=tuple(operations),
        total_bytes=total_bytes,
        expected_counts=dict(sorted(counts.items())),
        required_space=int(total_bytes * 1.02) + 10 * 1024 * 1024,
        warnings=tuple(warnings),
        unresolved_review=unresolved_review,
        created_at=datetime.now(),
        analysis_fingerprint=analysis.fingerprint,
    )
