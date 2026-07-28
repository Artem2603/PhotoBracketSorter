"""Bracket candidate and series detection."""

from __future__ import annotations

from itertools import count

from app.enums import BracketSizeMode, ConfidenceLevel
from app.exposure import priority_from_names, resolve_group_exposure
from app.models import AppSettings, BracketCandidate, BracketSeries, PhotoFile
from app.time_compare import (
    MIXED_TIMEZONE_ASSUMED_OFFSET,
    add_time_warning,
    safe_file_time_difference_seconds,
    sort_photo_files_by_capture_time,
    time_sort_key,
)


class BracketDetector:
    def detect(self, raw_files: list[PhotoFile], settings: AppSettings) -> tuple[list[BracketCandidate], list[BracketSeries]]:
        ordered = sort_photo_files_by_capture_time(raw_files)
        sizes = _enabled_sizes(settings)
        candidates: list[BracketCandidate] = []
        candidate_counter = count(1)
        for size in sizes:
            for start in range(0, max(0, len(ordered) - size + 1)):
                window = ordered[start : start + size]
                candidate = self._score_window(window, size, next(candidate_counter), settings)
                if candidate is not None:
                    candidates.append(candidate)
        selected = self._select_non_overlapping(candidates, settings)
        series_list: list[BracketSeries] = []
        counters = {3: 1, 5: 1, 7: 1, 9: 1}
        for candidate in selected:
            series_number = counters[candidate.candidate_size]
            counters[candidate.candidate_size] += 1
            series_id = f"HDR{candidate.candidate_size}_{series_number:04d}"
            confidence_level = ConfidenceLevel.from_score(candidate.confidence)
            series = BracketSeries(
                series_id=series_id,
                series_number=series_number,
                series_size=candidate.candidate_size,
                raw_files=candidate.files,
                # Keyed by file name, matching how the classifier and reports look
                # companions up. Keying by pair_id here silently broke that lookup.
                rendered_companions={file.metadata.file_name: None for file in candidate.files},
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                duration_seconds=candidate.duration_seconds,
                exposure_values=candidate.exposure_values,
                estimated_step_ev=candidate.estimated_step_ev,
                confidence=candidate.confidence,
                confidence_level=confidence_level,
                automatic=True,
                manually_confirmed=False,
                reasons=list(candidate.reasons),
                warnings=list(candidate.warnings),
                exposure_source=candidate.exposure_source,
                score_breakdown=list(candidate.score_breakdown),
            )
            for file in series.raw_files:
                file.series_id = series_id
            series_list.append(series)
        return candidates, series_list

    def _score_window(self, files: list[PhotoFile], size: int, number: int, settings: AppSettings) -> BracketCandidate | None:
        if len(files) != size:
            return None
        if not _same_camera_group(files):
            return None
        start_time = files[0].metadata.capture_time
        end_time = files[-1].metadata.capture_time
        duration = None
        reasons: list[str] = []
        warnings: list[str] = []
        breakdown: list[tuple[str, float]] = []
        score = 0.0
        if start_time is not None and end_time is not None:
            duration_result = safe_file_time_difference_seconds(files[0], files[-1])
            if duration_result.warning_code:
                warnings.append(duration_result.warning_code)
                add_time_warning(files[0], duration_result.warning_code)
                add_time_warning(files[-1], duration_result.warning_code)
                if duration_result.warning_code == MIXED_TIMEZONE_ASSUMED_OFFSET:
                    reasons.append("BRACKET_DURATION_MIXED_TIMEZONE_NORMALIZED")
            duration = duration_result.absolute_seconds
            if duration is None:
                warnings.append("Не удалось безопасно вычислить длительность серии.")
                return BracketCandidate(
                    candidate_id=f"CAND_{number:04d}",
                    files=files,
                    candidate_size=size,
                    start_time=start_time,
                    end_time=end_time,
                    duration_seconds=None,
                    exposure_values=[],
                    exposure_source="UNKNOWN",
                    estimated_step_ev=None,
                    score=0.0,
                    confidence=0.0,
                    reasons=reasons,
                    warnings=warnings,
                    score_breakdown=breakdown,
                )
            if duration <= _max_duration_for_size(settings, size):
                score += 22.0
                reasons.append("BRACKET_DURATION_OK")
                breakdown.append(("BRACKET_DURATION_OK", 22.0))
            else:
                return None
            adjacent_ok = True
            for index in range(len(files) - 1):
                gap_result = safe_file_time_difference_seconds(files[index], files[index + 1])
                if gap_result.warning_code:
                    warnings.append(gap_result.warning_code)
                    add_time_warning(files[index], gap_result.warning_code)
                    add_time_warning(files[index + 1], gap_result.warning_code)
                gap_seconds = gap_result.absolute_seconds
                if gap_seconds is None:
                    adjacent_ok = False
                    break
                if gap_seconds > settings.max_bracket_adjacent_delta_seconds:
                    adjacent_ok = False
                    break
            if adjacent_ok:
                score += 18.0
                reasons.append("BRACKET_ADJACENT_TIME_OK")
                breakdown.append(("BRACKET_ADJACENT_TIME_OK", 18.0))
            else:
                warnings.append("Соседние кадры сняты не плотной серией.")
                return BracketCandidate(
                    candidate_id=f"CAND_{number:04d}",
                    files=files,
                    candidate_size=size,
                    start_time=start_time,
                    end_time=end_time,
                    duration_seconds=duration,
                    exposure_values=[],
                    exposure_source="UNKNOWN",
                    estimated_step_ev=None,
                    score=score,
                    confidence=min(score, 44.0),
                    reasons=reasons,
                    warnings=warnings,
                    score_breakdown=breakdown,
                )
        else:
            warnings.append("Нет точного времени для всей серии.")
        group = resolve_group_exposure(files, settings.exposure_step_tolerance, priority_from_names(settings.exposure_source_priority))
        values = list(group.values)
        source = group.source.value
        estimated_step = group.step_ev
        if group.usable:
            reasons.append(f"BRACKET_EXPOSURE_SOURCE_{group.source.value.upper()}")
            for skipped_source, skip_reason in group.skipped_sources:
                reasons.append(f"BRACKET_EXPOSURE_SKIPPED_{skipped_source.value.upper()}: {skip_reason}")
            if group.span is not None and group.span >= settings.min_exposure_span_ev:
                score += 25.0
                reasons.append("BRACKET_EXPOSURE_SPAN_OK")
                breakdown.append(("BRACKET_EXPOSURE_SPAN_OK", 25.0))
            else:
                warnings.append("Разброс экспозиции меньше порога вилки.")
            if group.uniform:
                score += 20.0
                reasons.append("BRACKET_EXPOSURE_STEP_OK")
                breakdown.append(("BRACKET_EXPOSURE_STEP_OK", 20.0))
            else:
                warnings.append("Шаг экспозиции неравномерен.")
        elif settings.require_exposure_for_auto_series:
            if group.degenerate:
                warnings.append(f"Экспозиция одинакова у всех кадров по {group.source.value}; вилки нет.")
            warnings.append("Недостаточно экспозиционных данных для автоматической серии.")
            return BracketCandidate(
                candidate_id=f"CAND_{number:04d}",
                files=files,
                candidate_size=size,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration,
                exposure_values=values,
                exposure_source=source,
                estimated_step_ev=None,
                score=score,
                confidence=min(score, 44.0),
                reasons=reasons,
                warnings=warnings,
                score_breakdown=breakdown,
            )
        if _same_dimensions(files):
            score += 10.0
            reasons.append("BRACKET_DIMENSIONS_OK")
            breakdown.append(("BRACKET_DIMENSIONS_OK", 10.0))
        if _same_lens(files):
            score += 5.0
            reasons.append("BRACKET_LENS_OK")
            breakdown.append(("BRACKET_LENS_OK", 5.0))
        confidence = min(100.0, score)
        if confidence < _series_threshold(settings):
            warnings.append("Группа похожа на обычную серийную съёмку.")
        return BracketCandidate(
            candidate_id=f"CAND_{number:04d}",
            files=files,
            candidate_size=size,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            exposure_values=values,
            exposure_source=source,
            estimated_step_ev=estimated_step,
            score=round(score, 2),
            confidence=round(confidence, 2),
            reasons=reasons,
            warnings=warnings,
            score_breakdown=breakdown,
        )

    def _select_non_overlapping(self, candidates: list[BracketCandidate], settings: AppSettings) -> list[BracketCandidate]:
        threshold = _series_threshold(settings)
        accepted = [candidate for candidate in candidates if candidate.confidence >= threshold]
        accepted.sort(
            key=lambda candidate: (
                -candidate.confidence,
                -candidate.candidate_size,
                time_sort_key(candidate.start_time),
                candidate.files[0].metadata.file_name,
            )
        )
        # Identity, not resolve(): candidates slice the same PhotoFile list, and a
        # filesystem call per candidate made selection O(candidates x size) syscalls.
        used: set[int] = set()
        selected: list[BracketCandidate] = []
        for candidate in accepted:
            ids = {id(file) for file in candidate.files}
            if ids & used:
                continue
            used.update(ids)
            selected.append(candidate)
        selected.sort(key=lambda candidate: (time_sort_key(candidate.start_time), candidate.files[0].metadata.file_name))
        return selected


def _enabled_sizes(settings: AppSettings) -> tuple[int, ...]:
    """HDR7/HDR9 are additive opt-ins layered on top of bracket_size_mode's 3/5
    choice, not folded into it — enabling them must never change what 3-vs-5
    already means to an existing settings.json."""
    if settings.bracket_size_mode is BracketSizeMode.THREE:
        base: tuple[int, ...] = (3,)
    elif settings.bracket_size_mode is BracketSizeMode.FIVE:
        base = (5,)
    else:
        base = (3, 5)
    extra = tuple(size for size, enabled in ((7, settings.enable_hdr7), (9, settings.enable_hdr9)) if enabled)
    return base + extra


def _max_duration_for_size(settings: AppSettings, size: int) -> float:
    """Per-size duration limit. A larger bracket naturally takes longer to shoot."""
    if size <= 3:
        return settings.max_bracket_duration_seconds_3
    if size <= 5:
        return settings.max_bracket_duration_seconds_5
    if size <= 7:
        return settings.max_bracket_duration_seconds_7
    return settings.max_bracket_duration_seconds_9


#: Detection mode shifts the acceptance threshold around ``min_auto_confidence``.
DETECTION_MODE_THRESHOLD_SHIFT = {"STRICT": 10.0, "NORMAL": 0.0, "SOFT": -10.0}


def _series_threshold(settings: AppSettings) -> float:
    shift = DETECTION_MODE_THRESHOLD_SHIFT.get(settings.detection_mode.value, 0.0)
    return max(1.0, min(100.0, settings.min_auto_confidence + shift))


def _same_camera_group(files: list[PhotoFile]) -> bool:
    keys = {((file.metadata.make or "").lower(), (file.metadata.model or "").lower()) for file in files}
    non_empty = {key for key in keys if any(key)}
    return len(non_empty) <= 1


def _same_dimensions(files: list[PhotoFile]) -> bool:
    pairs = {(file.metadata.width, file.metadata.height, file.metadata.orientation) for file in files if file.metadata.width and file.metadata.height}
    return len(pairs) <= 1 and bool(pairs)


def _same_lens(files: list[PhotoFile]) -> bool:
    values = {(file.metadata.lens_model or file.metadata.lens_id or "").lower() for file in files}
    values = {value for value in values if value}
    return len(values) <= 1 and bool(values)
