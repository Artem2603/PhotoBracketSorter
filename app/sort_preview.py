"""Sort-result preview: the destination folder tree before Copy/Move touches anything.

Built entirely from :class:`app.models.CopyPlan` and the analysis it came
from — no new decision logic. A conflict-renamed file is detected by comparing
the operation's final destination name against the source file's own name
(the only thing besides a real name collision that changes a filename); an
excluded file is anything ``build_copy_plan`` already declined to include,
with the same reason it would have logged. This module only decides how to
*show* state the copy plan already computed, plus the mechanics of the
interactive edits (exclude, recategorize, rename a series, change the
duplicate policy) that all go through the same ``SeriesManager`` /
``assign_category`` used everywhere else — never a second copy of that state.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from app.models import AnalysisResult, CopyPlan, PhotoFile

#: destination_category values that mean "never part of a copy plan".
_EXCLUDED_CATEGORIES = {"REVIEW", "SKIP"}


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewFile:
    file: PhotoFile
    source_name: str
    source_relative: str
    destination_relative: str
    category: str
    series_id: str | None
    size: int
    renamed_for_conflict: bool


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewFolder:
    name: str
    relative_path: str
    folders: tuple["PreviewFolder", ...]
    files: tuple[PreviewFile, ...]
    file_count: int
    total_size: int
    #: Set when this folder is exactly one series' destination root, so the GUI
    #: can offer "rename series" on it without guessing from the folder name.
    series_uid: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ExcludedFile:
    file: PhotoFile
    name: str
    relative_source: str
    category: str
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class SortPreview:
    output_root: str
    root: PreviewFolder
    total_files: int
    total_size: int
    required_space: int
    free_space: int | None
    excluded: tuple[ExcludedFile, ...]
    conflicts: tuple[PreviewFile, ...]
    warnings: tuple[str, ...]

    @property
    def has_enough_space(self) -> bool:
        return self.free_space is None or self.free_space >= self.required_space


def build_sort_preview(analysis: AnalysisResult, plan: CopyPlan, free_space: int | None = None) -> SortPreview:
    preview_files = [_preview_file(analysis, operation, plan.output_root) for operation in plan.operations]
    series_by_root = {
        series.destination_root.as_posix(): series.uid for series in analysis.bracket_series if series.destination_root is not None
    }
    root = _build_tree("", "", preview_files, series_by_root)
    excluded = tuple(_excluded_file(file) for file in analysis.files if _is_excluded(file))
    conflicts = tuple(preview_file for preview_file in preview_files if preview_file.renamed_for_conflict)
    return SortPreview(
        output_root=str(plan.output_root),
        root=root,
        total_files=len(preview_files),
        total_size=plan.total_bytes,
        required_space=plan.required_space,
        free_space=free_space,
        excluded=excluded,
        conflicts=conflicts,
        warnings=plan.warnings,
    )


def _preview_file(analysis: AnalysisResult, operation, output_root: Path) -> PreviewFile:
    try:
        relative_destination = operation.destination_path.relative_to(output_root)
    except ValueError:
        relative_destination = operation.destination_path
    return PreviewFile(
        file=_find_file(analysis, operation),
        source_name=operation.relative_source.name,
        source_relative=operation.relative_source.as_posix(),
        destination_relative=relative_destination.as_posix(),
        category=operation.category.value,
        series_id=operation.series_id,
        size=operation.expected_size,
        renamed_for_conflict=operation.destination_path.name != operation.relative_source.name,
    )


def _find_file(analysis: AnalysisResult, operation) -> PhotoFile | None:
    return next((file for file in analysis.files if file.metadata.relative_path == operation.relative_source), None)


def _is_excluded(file: PhotoFile) -> bool:
    return file.destination_category.value in _EXCLUDED_CATEGORIES or not file.selected or file.destination_subpath is None


def _excluded_file(file: PhotoFile) -> ExcludedFile:
    if file.destination_category.value == "REVIEW":
        reason = "review"
    elif file.destination_category.value == "SKIP":
        reason = "skip"
    elif not file.selected:
        reason = "deselected"
    else:
        reason = "no_destination"
    return ExcludedFile(
        file=file,
        name=file.metadata.file_name,
        relative_source=file.metadata.relative_path.as_posix(),
        category=file.destination_category.value,
        reason=reason,
    )


def _build_tree(
    name: str,
    cumulative_path: str,
    preview_files: list[PreviewFile],
    series_by_root: dict[str, str],
) -> PreviewFolder:
    """Group by destination folder path, one level of recursion per path segment."""
    direct_files: list[PreviewFile] = []
    grouped: dict[str, list[PreviewFile]] = {}
    for preview_file in preview_files:
        parts = Path(preview_file.destination_relative).parts
        if len(parts) <= 1:
            direct_files.append(preview_file)
            continue
        head, rest = parts[0], parts[1:]
        rest_relative = str(Path(*rest)) if rest else ""
        grouped.setdefault(head, []).append(dataclasses.replace(preview_file, destination_relative=rest_relative))
    folders = tuple(
        _build_tree(
            child_name,
            f"{cumulative_path}/{child_name}" if cumulative_path else child_name,
            files,
            series_by_root,
        )
        for child_name, files in sorted(grouped.items())
    )
    file_count = len(direct_files) + sum(folder.file_count for folder in folders)
    total_size = sum(f.size for f in direct_files) + sum(folder.total_size for folder in folders)
    return PreviewFolder(
        name=name,
        relative_path=cumulative_path,
        folders=folders,
        files=tuple(direct_files),
        file_count=file_count,
        total_size=total_size,
        series_uid=series_by_root.get(cumulative_path),
    )
