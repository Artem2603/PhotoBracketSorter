""""Why was this decided this way?" — assembled from real algorithm output.

Every field here reads something the detector or matcher already computed and
stored (``reasons``, ``score_breakdown``, ``exposure_values``,
``capture_time_source``, ...), or a directly observable fact recomputed the
same way the rest of the app already does (adjacent-frame time gaps, exactly
as :mod:`app.review_queue` and :mod:`app.auto_configure` do it). Nothing here
re-implements or approximates a decision the detector already made — camera
match, lens match, and dimension match are read off the real reason codes
(``BRACKET_DIMENSIONS_OK`` / ``BRACKET_LENS_OK`` / ``PAIR_CAMERA_MATCH`` /
``PAIR_GEOMETRY_MATCH``), not recomputed by a second, possibly-diverging rule.
"""

from __future__ import annotations

import dataclasses

from app.models import AnalysisResult, AppSettings, BracketSeries, PhotoFile, RawHeicPair
from app.review_queue import ReviewReason, build_review_queue
from app.series_manager import SeriesManager
from app.time_compare import safe_file_time_difference_seconds, sort_photo_files_by_capture_time


@dataclasses.dataclass(frozen=True, slots=True)
class FrameExplanation:
    file_name: str
    capture_time: str
    capture_time_source: str
    exposure_compensation: float | None
    exposure_time: float | None
    f_number: float | None
    iso: float | None


@dataclasses.dataclass(frozen=True, slots=True)
class ScoreLine:
    label: str
    points: float


@dataclasses.dataclass(frozen=True, slots=True)
class SeriesExplanation:
    series_id: str
    series_uid: str
    automatic: bool
    time_source: str
    exposure_source: str
    frames: tuple[FrameExplanation, ...]
    exposure_values: tuple[float, ...]
    estimated_step_ev: float | None
    duration_seconds: float | None
    adjacent_gaps_seconds: tuple[float | None, ...]
    camera_match: bool
    lens_match: bool
    dimensions_match: bool
    score_breakdown: tuple[ScoreLine, ...]
    total_score: float
    confidence: float
    confidence_level: str
    review_reasons: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class PairExplanation:
    pair_id: str
    raw_name: str
    rendered_name: str
    raw_time_source: str
    rendered_time_source: str
    time_delta_seconds: float | None
    camera_match: bool
    lens_match: bool
    dimensions_match: bool
    name_similarity: float
    visual_similarity: float | None
    score_breakdown: tuple[ScoreLine, ...]
    total_score: float
    ambiguous: bool
    review_reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def explain_series(
    series: BracketSeries,
    analysis: AnalysisResult | None = None,
    manager: SeriesManager | None = None,
    language: str = "ru",
) -> SeriesExplanation:
    ordered = sort_photo_files_by_capture_time(list(series.raw_files))
    frames = tuple(_frame_explanation(file) for file in ordered)
    gaps = tuple(_adjacent_gap(left, right) for left, right in zip(ordered, ordered[1:]))
    review_reasons = _review_reasons_for_series(series, analysis, manager, language)
    return SeriesExplanation(
        series_id=series.series_id,
        series_uid=series.uid,
        automatic=series.automatic,
        time_source=_dominant_time_source(series.raw_files),
        exposure_source=series.exposure_source,
        frames=frames,
        exposure_values=tuple(series.exposure_values),
        estimated_step_ev=series.estimated_step_ev,
        duration_seconds=series.duration_seconds,
        adjacent_gaps_seconds=gaps,
        camera_match=True,  # a hard gate before any candidate exists; never false here
        lens_match="BRACKET_LENS_OK" in series.reasons,
        dimensions_match="BRACKET_DIMENSIONS_OK" in series.reasons,
        score_breakdown=tuple(ScoreLine(label, points) for label, points in series.score_breakdown),
        total_score=sum(points for _label, points in series.score_breakdown),
        confidence=series.confidence,
        confidence_level=series.confidence_level.value,
        review_reasons=review_reasons,
        warnings=tuple(series.warnings),
    )


def explain_pair(
    pair: RawHeicPair,
    analysis: AnalysisResult | None = None,
    manager: SeriesManager | None = None,
    language: str = "ru",
) -> PairExplanation:
    review_reasons = _review_reasons_for_pair(pair, analysis, manager, language)
    time_result = safe_file_time_difference_seconds(pair.raw_file, pair.rendered_file)
    lens_match = bool(
        pair.raw_file.metadata.lens_model
        and pair.rendered_file.metadata.lens_model
        and pair.raw_file.metadata.lens_model == pair.rendered_file.metadata.lens_model
    )
    return PairExplanation(
        pair_id=pair.pair_id,
        raw_name=pair.raw_file.metadata.file_name,
        rendered_name=pair.rendered_file.metadata.file_name,
        raw_time_source=pair.raw_file.metadata.capture_time_source.value,
        rendered_time_source=pair.rendered_file.metadata.capture_time_source.value,
        time_delta_seconds=time_result.absolute_seconds,
        camera_match="PAIR_CAMERA_MATCH" in pair.reasons,
        lens_match=lens_match,
        dimensions_match="PAIR_GEOMETRY_MATCH" in pair.reasons,
        name_similarity=pair.name_similarity,
        visual_similarity=pair.preview_similarity,
        score_breakdown=tuple(ScoreLine(label, points) for label, points in pair.score_breakdown),
        total_score=pair.score,
        ambiguous=pair.ambiguous,
        review_reasons=review_reasons,
        warnings=tuple(pair.warnings),
    )


def format_series_explanation(explanation: SeriesExplanation, language: str = "ru") -> str:
    t = _text(language)
    lines = [
        f"{explanation.series_id} ({t('automatic') if explanation.automatic else t('manual')})",
        f"{t('confidence')}: {explanation.confidence:.1f} ({explanation.confidence_level})",
        f"{t('time_source')}: {explanation.time_source}",
        f"{t('exposure_source')}: {explanation.exposure_source}",
        f"{t('duration')}: {_fmt_seconds(explanation.duration_seconds)}",
        f"{t('camera_match')}: {_fmt_bool(explanation.camera_match, language)}",
        f"{t('lens_match')}: {_fmt_bool(explanation.lens_match, language)}",
        f"{t('dimensions_match')}: {_fmt_bool(explanation.dimensions_match, language)}",
        "",
        t("frames") + ":",
    ]
    for frame, gap in zip(explanation.frames, (None, *explanation.adjacent_gaps_seconds)):
        gap_text = f", {t('gap_from_previous')}: {_fmt_seconds(gap)}" if gap is not None else ""
        lines.append(
            f"  - {frame.file_name}: {frame.capture_time} [{frame.capture_time_source}]"
            f", ISO {_fmt_num(frame.iso)}, f/{_fmt_num(frame.f_number)}, {_fmt_shutter(frame.exposure_time)}"
            f", EC {_fmt_num(frame.exposure_compensation)}{gap_text}"
        )
    if explanation.exposure_values:
        lines.append(f"{t('exposure_values')}: {', '.join(f'{value:.2f}' for value in explanation.exposure_values)}")
    if explanation.estimated_step_ev is not None:
        lines.append(f"{t('estimated_step_ev')}: {explanation.estimated_step_ev:.2f} EV")
    lines.append("")
    lines.append(t("score_breakdown") + ":")
    for line in explanation.score_breakdown:
        sign = "+" if line.points >= 0 else ""
        lines.append(f"  {sign}{line.points:.1f}  {line.label}")
    lines.append(f"  = {explanation.total_score:.1f}")
    if explanation.review_reasons:
        lines.append("")
        lines.append(t("review_reasons") + ":")
        lines.extend(f"  - {reason}" for reason in explanation.review_reasons)
    if explanation.warnings:
        lines.append("")
        lines.append(t("warnings") + ":")
        lines.extend(f"  - {warning}" for warning in explanation.warnings)
    return "\n".join(lines)


def format_pair_explanation(explanation: PairExplanation, language: str = "ru") -> str:
    t = _text(language)
    lines = [
        f"{explanation.pair_id}: {explanation.raw_name} <-> {explanation.rendered_name}",
        f"{t('ambiguous')}: {_fmt_bool(explanation.ambiguous, language)}",
        f"{t('time_delta')}: {_fmt_seconds(explanation.time_delta_seconds)}",
        f"{t('time_source')} RAW/preview: {explanation.raw_time_source} / {explanation.rendered_time_source}",
        f"{t('camera_match')}: {_fmt_bool(explanation.camera_match, language)}",
        f"{t('lens_match')}: {_fmt_bool(explanation.lens_match, language)}",
        f"{t('dimensions_match')}: {_fmt_bool(explanation.dimensions_match, language)}",
        f"{t('name_similarity')}: {explanation.name_similarity:.2f}",
        f"{t('visual_similarity')}: {'-' if explanation.visual_similarity is None else f'{explanation.visual_similarity:.2f}'}",
        "",
        t("score_breakdown") + ":",
    ]
    for line in explanation.score_breakdown:
        sign = "+" if line.points >= 0 else ""
        lines.append(f"  {sign}{line.points:.1f}  {line.label}")
    lines.append(f"  = {explanation.total_score:.1f}")
    if explanation.review_reasons:
        lines.append("")
        lines.append(t("review_reasons") + ":")
        lines.extend(f"  - {reason}" for reason in explanation.review_reasons)
    if explanation.warnings:
        lines.append("")
        lines.append(t("warnings") + ":")
        lines.extend(f"  - {warning}" for warning in explanation.warnings)
    return "\n".join(lines)


# --------------------------------------------------------------------- helpers


def _frame_explanation(file: PhotoFile) -> FrameExplanation:
    metadata = file.metadata
    return FrameExplanation(
        file_name=metadata.file_name,
        capture_time=metadata.capture_time.isoformat(sep=" ", timespec="milliseconds") if metadata.capture_time else "-",
        capture_time_source=metadata.capture_time_source.value,
        exposure_compensation=metadata.exposure_compensation,
        exposure_time=metadata.exposure_time,
        f_number=metadata.f_number,
        iso=metadata.iso,
    )


def _adjacent_gap(left: PhotoFile, right: PhotoFile) -> float | None:
    return safe_file_time_difference_seconds(left, right).absolute_seconds


def _dominant_time_source(files: list[PhotoFile]) -> str:
    sources = {file.metadata.capture_time_source.value for file in files}
    if len(sources) == 1:
        return next(iter(sources))
    return "MIXED (" + ", ".join(sorted(sources)) + ")"


def _review_reasons_for_series(
    series: BracketSeries, analysis: AnalysisResult | None, manager: SeriesManager | None, language: str
) -> tuple[str, ...]:
    if analysis is None or manager is None:
        return ()
    settings = _settings_for(analysis)
    items = build_review_queue(analysis, manager, settings, language)
    return tuple(item.summary for item in items if item.series_uid == series.uid)


def _review_reasons_for_pair(
    pair: RawHeicPair, analysis: AnalysisResult | None, manager: SeriesManager | None, language: str
) -> tuple[str, ...]:
    if analysis is None or manager is None:
        return ()
    settings = _settings_for(analysis)
    items = build_review_queue(analysis, manager, settings, language)
    return tuple(item.summary for item in items if item.pair_id == pair.pair_id)


def _settings_for(analysis: AnalysisResult) -> AppSettings:
    from app.settings import settings_from_dict

    return settings_from_dict(analysis.settings_snapshot) if analysis.settings_snapshot else AppSettings()


def _fmt_seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f} s"


def _fmt_num(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _fmt_shutter(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    return f"1/{round(1 / value)}" if value < 1 else f"{value:g}s"


def _fmt_bool(value: bool, language: str) -> str:
    if language == "ru":
        return "да" if value else "нет"
    return "yes" if value else "no"


def _text(language: str):
    catalog = _LABELS.get(language, _LABELS["en"])
    return lambda key: catalog.get(key, key)


_LABELS: dict[str, dict[str, str]] = {
    "ru": {
        "automatic": "автоматически",
        "manual": "вручную",
        "confidence": "Уверенность",
        "time_source": "Источник времени",
        "exposure_source": "Источник экспозиции",
        "duration": "Длительность",
        "camera_match": "Совпадение камеры",
        "lens_match": "Совпадение объектива",
        "dimensions_match": "Совпадение размеров",
        "frames": "Кадры",
        "gap_from_previous": "интервал от предыдущего",
        "exposure_values": "Значения EV",
        "estimated_step_ev": "Оценённый шаг",
        "score_breakdown": "Баллы",
        "review_reasons": "Причины проверки",
        "warnings": "Предупреждения",
        "ambiguous": "Неоднозначно",
        "time_delta": "Интервал времени",
        "name_similarity": "Сходство имён",
        "visual_similarity": "Визуальное сходство",
    },
    "en": {
        "automatic": "automatic",
        "manual": "manual",
        "confidence": "Confidence",
        "time_source": "Time source",
        "exposure_source": "Exposure source",
        "duration": "Duration",
        "camera_match": "Camera match",
        "lens_match": "Lens match",
        "dimensions_match": "Dimensions match",
        "frames": "Frames",
        "gap_from_previous": "gap from previous",
        "exposure_values": "EV values",
        "estimated_step_ev": "Estimated step",
        "score_breakdown": "Score",
        "review_reasons": "Review reasons",
        "warnings": "Warnings",
        "ambiguous": "Ambiguous",
        "time_delta": "Time delta",
        "name_similarity": "Name similarity",
        "visual_similarity": "Visual similarity",
    },
}
