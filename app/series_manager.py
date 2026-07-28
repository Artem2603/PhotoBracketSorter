"""Single owner of bracket series state, automatic and manual alike.

Before this module, series lived in three places at once — ``PhotoFile.series_id``,
``AnalysisResult.bracket_series``, and each series' ``destination_root`` — with no
synchronisation. Dissolving a series cleared the file field but left an orphan
series in the report; manual series were never added to ``bracket_series`` at all,
so reports never saw them; and the GUI recomputed statistics with a different set
of keys than the report did.

``SeriesManager`` owns every series and is the only thing allowed to mutate
``PhotoFile.series_id``, ``destination_category``, and ``destination_subpath`` for
series membership. Statistics come from :func:`compute_statistics`, which both the
analysis pipeline and the GUI call, so the two can no longer disagree.
"""

from __future__ import annotations

from pathlib import Path

from app.enums import ConfidenceLevel, DestinationCategory, DetectedType
from app.errors import ManualSeriesError
from app.format_detection import is_raw_type, is_rendered_type
from app.models import AnalysisResult, BracketSeries, PhotoFile
from app.time_compare import safe_file_time_difference_seconds, sort_photo_files_by_capture_time

#: Series sizes the application understands. HDR7/HDR9 are always available for
#: *manual* creation — the settings.enable_hdr7/9 flags only gate whether the
#: automatic detector goes looking for them, matching how HDR3/HDR5 already work.
SUPPORTED_SERIES_SIZES = (3, 5, 7, 9)

#: Manual series are trusted by construction; the user asserted the grouping.
MANUAL_SERIES_CONFIDENCE = 100.0

_CATEGORY_BY_SIZE = {
    3: DestinationCategory.BRACKETING_3,
    5: DestinationCategory.BRACKETING_5,
    7: DestinationCategory.BRACKETING_7,
    9: DestinationCategory.BRACKETING_9,
}


def category_for_size(size: int) -> DestinationCategory:
    return _CATEGORY_BY_SIZE.get(size, DestinationCategory.REVIEW)


class SeriesManager:
    """Owns all bracket series for one analysis and keeps files consistent."""

    def __init__(self, series: list[BracketSeries] | None = None):
        self._series: list[BracketSeries] = list(series or [])
        self._counters: dict[tuple[str, int], int] = {}
        self._seed_counters()

    # ------------------------------------------------------------------ state

    @property
    def series(self) -> list[BracketSeries]:
        return self._series

    @classmethod
    def from_analysis(cls, analysis: AnalysisResult) -> "SeriesManager":
        return cls(analysis.bracket_series)

    def apply_to(self, analysis: AnalysisResult) -> None:
        """Publish current series and statistics back onto the analysis result."""
        analysis.bracket_series = self._series
        analysis.statistics = compute_statistics(analysis.files, self._series)

    def find_by_uid(self, uid: str) -> BracketSeries | None:
        return next((series for series in self._series if series.uid == uid), None)

    def find_by_file(self, file: PhotoFile) -> BracketSeries | None:
        for series in self._series:
            if any(member is file for member in series.raw_files):
                return series
        return None

    # ----------------------------------------------------------- manual edits

    def create_manual(self, files: list[PhotoFile], size: int) -> BracketSeries:
        """Group ``files`` into a new manual series after validating them.

        Raises :class:`ManualSeriesError` with a user-facing message when the
        selection cannot form a series.
        """
        self._validate_manual(files, size)
        for file in list(files):
            self.remove_file(file)

        ordered = sort_photo_files_by_capture_time(list(files))
        series_id, number = self._next_id("MANUAL", size)
        duration = self._duration(ordered)
        series = BracketSeries(
            series_id=series_id,
            series_number=number,
            series_size=size,
            raw_files=ordered,
            rendered_companions={file.metadata.file_name: None for file in ordered},
            start_time=ordered[0].metadata.capture_time,
            end_time=ordered[-1].metadata.capture_time,
            duration_seconds=duration,
            exposure_values=[],
            estimated_step_ev=None,
            confidence=MANUAL_SERIES_CONFIDENCE,
            confidence_level=ConfidenceLevel.HIGH,
            automatic=False,
            manually_confirmed=True,
            reasons=["MANUAL_SERIES"],
        )
        self._resolve_exposure(series)
        self._series.append(series)
        self.sync_destinations()
        return series

    def dissolve(self, series: BracketSeries) -> list[PhotoFile]:
        """Break a series apart and return its former RAW members to singles."""
        if series not in self._series:
            return []
        self._series.remove(series)
        released = list(series.raw_files)
        for file in released:
            self._detach_file(file)
        for companion in series.rendered_companions.values():
            if companion is not None:
                self._detach_file(companion)
        self.sync_destinations()
        return released

    def dissolve_containing(self, files: list[PhotoFile]) -> list[BracketSeries]:
        """Dissolve every series that any of ``files`` belongs to."""
        affected: list[BracketSeries] = []
        for file in files:
            series = self.find_by_file(file)
            if series is not None and series not in affected:
                affected.append(series)
        for series in affected:
            self.dissolve(series)
        return affected

    def remove_file(self, file: PhotoFile) -> BracketSeries | None:
        """Drop one file from its series, dissolving the series if it gets too small."""
        series = self.find_by_file(file)
        if series is None:
            return None
        self.dissolve(series)
        return series

    def rename_series(self, series: BracketSeries, name: str | None) -> None:
        """Set (or clear, with ``None``) a custom destination folder name.

        Raises :class:`ManualSeriesError` if the sanitized name is empty or
        collides with another series' destination folder in the same category
        (``BRACKETING_3``/``BRACKETING_5`` are separate namespaces, so a HDR3
        and a HDR5 series may reuse a name freely).
        """
        if series not in self._series:
            return
        if name is None or not name.strip():
            series.custom_name = None
            self.sync_destinations()
            return
        from app.paths import sanitize_component

        sanitized = sanitize_component(name.strip())
        if not sanitized:
            raise ManualSeriesError("Имя серии не должно быть пустым после очистки от недопустимых символов.")
        for other in self._series:
            if other is series or other.series_size != series.series_size:
                continue
            other_name = other.custom_name or self._default_folder_name(other)
            if other_name == sanitized:
                raise ManualSeriesError(f"Имя «{sanitized}» уже используется другой серией.")
        series.custom_name = sanitized
        self.sync_destinations()

    # ------------------------------------------------------------ destinations

    def sync_destinations(self) -> None:
        """Rewrite destination paths for every series member from scratch."""
        for series in self._series:
            category = category_for_size(series.series_size)
            folder = self._folder_name(series)
            root = Path(category.value) / folder
            series.destination_root = root
            for raw_file in series.raw_files:
                raw_file.series_id = series.series_id
                raw_file.destination_category = category
                raw_file.destination_subpath = root / "RAW" / raw_file.metadata.file_name
                if not series.automatic:
                    raw_file.confidence = MANUAL_SERIES_CONFIDENCE
                    raw_file.confidence_level = ConfidenceLevel.HIGH
                    if "MANUAL_SERIES" not in raw_file.decision_reasons:
                        raw_file.decision_reasons.append("MANUAL_SERIES")
                raw_file.selected = True
            for companion in series.rendered_companions.values():
                if companion is None:
                    continue
                companion.series_id = series.series_id
                companion.destination_category = category
                companion.destination_subpath = root / "HEIC" / companion.metadata.file_name
                companion.selected = True

    # --------------------------------------------------------------- internals

    def _seed_counters(self) -> None:
        for series in self._series:
            kind = "AUTO" if series.automatic else "MANUAL"
            key = (kind, series.series_size)
            self._counters[key] = max(self._counters.get(key, 0), series.series_number)

    def _next_id(self, kind: str, size: int) -> tuple[str, int]:
        key = (kind, size)
        number = self._counters.get(key, 0) + 1
        self._counters[key] = number
        prefix = "HDR" if kind == "AUTO" else "MANUAL_HDR"
        return f"{prefix}{size}_{number:04d}", number

    def _folder_name(self, series: BracketSeries) -> str:
        if series.custom_name:
            return series.custom_name
        return self._default_folder_name(series)

    def _default_folder_name(self, series: BracketSeries) -> str:
        # Auto and manual folders never collide: different prefixes inside the
        # same category directory.
        prefix = "Series" if series.automatic else "Manual"
        return f"{prefix}_{series.series_number:04d}"

    def _detach_file(self, file: PhotoFile) -> None:
        file.series_id = None
        detected = file.metadata.detected_type
        if is_rendered_type(detected):
            file.destination_category = DestinationCategory.HEIC
            file.destination_subpath = Path("HEIC") / file.metadata.file_name
        elif is_raw_type(detected):
            file.destination_category = DestinationCategory.RAW
            file.destination_subpath = Path("RAW") / file.metadata.file_name
        else:
            file.destination_category = DestinationCategory.REVIEW
            file.destination_subpath = None
        file.confidence_level = ConfidenceLevel.from_score(file.confidence)

    def _validate_manual(self, files: list[PhotoFile], size: int) -> None:
        if size not in SUPPORTED_SERIES_SIZES:
            raise ManualSeriesError(f"Серия из {size} кадров не поддерживается.")
        if len(files) != size:
            raise ManualSeriesError(f"Нужно выбрать ровно {size} файлов, выбрано {len(files)}.")
        if len({id(file) for file in files}) != len(files):
            raise ManualSeriesError("Один и тот же файл выбран несколько раз.")

        corrupted = [file for file in files if file.metadata.detected_type is DetectedType.CORRUPTED]
        if corrupted:
            raise ManualSeriesError(f"Повреждённый файл нельзя включить в серию: {corrupted[0].metadata.file_name}.")
        not_raw = [file for file in files if not is_raw_type(file.metadata.detected_type)]
        if not_raw:
            names = ", ".join(file.metadata.file_name for file in not_raw[:3])
            raise ManualSeriesError(f"Серия строится только из RAW-кадров. Не RAW: {names}.")

    def _duration(self, ordered: list[PhotoFile]) -> float | None:
        if len(ordered) < 2:
            return None
        return safe_file_time_difference_seconds(ordered[0], ordered[-1]).absolute_seconds

    def _resolve_exposure(self, series: BracketSeries) -> None:
        """Record exposure for a manual series without gating on it."""
        from app.exposure import resolve_group_exposure

        group = resolve_group_exposure(series.raw_files)
        series.exposure_source = group.source.value
        if group.complete:
            series.exposure_values = list(group.values)
            series.estimated_step_ev = group.step_ev
            if group.degenerate:
                series.warnings.append("Экспозиция кадров одинакова; серия принята по решению пользователя.")
        else:
            series.warnings.append("Недостаточно экспозиционных данных; серия принята по решению пользователя.")


def compute_statistics(files: list[PhotoFile], series_list: list[BracketSeries]) -> dict[str, int | float]:
    """The one statistics function. Used by the analysis pipeline and the GUI.

    Two separate implementations previously disagreed on both keys and rules, so
    the table and the report showed different numbers after any manual edit.
    """
    unsupported = {DetectedType.UNSUPPORTED, DetectedType.CORRUPTED, DetectedType.UNKNOWN}
    stats: dict[str, int | float] = {
        "total_discovered": len(files),
        "supported": sum(1 for file in files if file.metadata.detected_type not in unsupported),
        "heic_jpeg": sum(1 for file in files if file.destination_category is DestinationCategory.HEIC),
        "raw_single": sum(1 for file in files if file.destination_category is DestinationCategory.RAW),
        "series_manual": sum(1 for series in series_list if not series.automatic),
        "companions_in_series": sum(
            1 for file in files if file.series_id and is_rendered_type(file.metadata.detected_type)
        ),
        "review": sum(1 for file in files if file.destination_category is DestinationCategory.REVIEW),
        "skipped": sum(1 for file in files if file.destination_category is DestinationCategory.SKIP),
        "errors": sum(1 for file in files if file.metadata.metadata_errors),
        "bytes_planned": sum(
            file.metadata.file_size for file in files if file.selected and file.destination_subpath is not None
        ),
    }
    for size in SUPPORTED_SERIES_SIZES:
        stats[f"series_{size}"] = sum(1 for series in series_list if series.series_size == size)
        stats[f"raw_in_series_{size}"] = sum(len(series.raw_files) for series in series_list if series.series_size == size)
    return stats
