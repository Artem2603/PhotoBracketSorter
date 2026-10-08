"""Suggests analysis settings from real folder metadata.

Nothing here mutates ``AppSettings``. :func:`analyze_for_suggestions` only reads
metadata already extracted by :class:`app.metadata.MetadataReader` and returns an
immutable report; only an explicit call to :func:`apply_suggestions` — wired to
the GUI's "Apply" button, never fired automatically — may produce a new settings
object.

Every number in the report comes from running the real detection code
(:class:`app.bracket_detection.BracketDetector`, :class:`app.pairing.PairMatcher`)
against a deliberately loosened settings profile, not from a second, approximate
implementation. Loosening only widens acceptance (bigger time windows, no
minimum exposure span, no confidence floor) so real clusters surface regardless
of the user's current thresholds; the suggestions then tighten the real settings
just enough to keep what was actually observed. This keeps the two algorithms
identical, so this module can never disagree with the detector it is advising.
"""

from __future__ import annotations

import dataclasses
import statistics
from collections import Counter
from typing import Any, Iterable

from app.bracket_detection import BracketDetector
from app.enums import BracketSizeMode, DetectionMode
from app.format_detection import is_raw_type, is_rendered_type
from app.models import AppSettings, PhotoFile, PhotoMetadata
from app.pairing import PairMatcher
from app.time_compare import safe_file_time_difference_seconds, sort_photo_files_by_capture_time

#: Tags that carry sub-second precision when ExifTool exposes them.
_SUBSECOND_TAGS = ("SubSecDateTimeOriginal", "SubSecCreateDate", "SubSecTimeOriginal", "SubSecTimeDigitized")

#: A suggestion is only worth showing when it differs from the current value by
#: more than this fraction — otherwise the dialog fills with no-op rows.
_RELATIVE_CHANGE_THRESHOLD = 0.08

#: Discovery only counts a cluster as bracket evidence when its frames span at
#: least this much exposure. The smallest bracket increment cameras offer is
#: 1/3 EV, so even a three-frame bracket spans about 0.67 EV, while
#: auto-exposure drift inside a plain burst rarely exceeds about 0.4 EV; 0.5 EV
#: sits between the two. See :func:`_discovery_min_span_ev` for how it combines
#: with the user's own threshold.
_DISCOVERY_MIN_SPAN_EV = 0.5

#: Per-size duration limits that ``validate_settings`` requires to be at least
#: ``max_bracket_adjacent_delta_seconds``.
_DURATION_FIELDS = (
    "max_bracket_duration_seconds_3",
    "max_bracket_duration_seconds_5",
    "max_bracket_duration_seconds_7",
    "max_bracket_duration_seconds_9",
)
_ADJACENT_FIELD = "max_bracket_adjacent_delta_seconds"


@dataclasses.dataclass(frozen=True, slots=True)
class CameraSignature:
    make: str
    model: str
    count: int


@dataclasses.dataclass(frozen=True, slots=True)
class SettingSuggestion:
    field: str
    current_value: Any
    suggested_value: Any
    rationale: str


@dataclasses.dataclass(frozen=True, slots=True)
class AutoConfigReport:
    sample_size: int
    raw_count: int
    rendered_count: int
    cameras: tuple[CameraSignature, ...]
    raw_formats: tuple[str, ...]
    rendered_formats: tuple[str, ...]
    subsecond_available: bool
    subsecond_fraction: float
    reliable_exposure_source: str | None
    exposure_source_votes: dict[str, int]
    exposure_source_degenerate_groups: int
    exposure_source_total_groups: int
    typical_series_size: int | None
    series_size_histogram: dict[int, int]
    typical_step_ev: dict[int, float]
    typical_duration_seconds: dict[int, float]
    typical_adjacent_gap_seconds: dict[int, float]
    typical_pair_delta_seconds: float | None
    suggestions: tuple[SettingSuggestion, ...]
    warnings: tuple[str, ...]

    @property
    def has_suggestions(self) -> bool:
        return bool(self.suggestions)


def analyze_for_suggestions(
    metadata_items: list[PhotoMetadata],
    settings: AppSettings,
    language: str = "ru",
) -> AutoConfigReport:
    """Compute a settings suggestion report. Never mutates ``settings`` or ``metadata_items``."""
    warnings: list[str] = []
    if not metadata_items:
        warnings.append(_text(language, "no_files"))
        return _empty_report(warnings)

    raw_files = [PhotoFile(metadata=item) for item in metadata_items if is_raw_type(item.detected_type)]
    rendered_files = [PhotoFile(metadata=item) for item in metadata_items if is_rendered_type(item.detected_type)]

    cameras = _camera_signatures(metadata_items)
    raw_formats = tuple(sorted({item.detected_type.value for item in metadata_items if is_raw_type(item.detected_type)}))
    rendered_formats = tuple(
        sorted({item.detected_type.value for item in metadata_items if is_rendered_type(item.detected_type)})
    )
    subsecond_fraction = _subsecond_fraction(metadata_items)

    suggestions: list[SettingSuggestion] = []

    if not raw_files:
        warnings.append(_text(language, "no_raw"))
        series_list: list = []
    else:
        discovery_settings = _bracket_discovery_settings(settings)
        _candidates, detected = BracketDetector().detect(raw_files, discovery_settings)
        # The discovery profile widens time/duration windows so real brackets are
        # never rejected on timing alone, but that also lets bursts through with
        # a timing-only score. Only a cluster whose exposure spans a meaningful
        # range (the discovery min_exposure_span_ev, see _discovery_min_span_ev)
        # is evidence of a bracket; flat bursts and auto-exposure drift are
        # dropped before they can shape a suggestion.
        series_list = [series for series in detected if "BRACKET_EXPOSURE_SPAN_OK" in series.reasons]
        if not series_list:
            warnings.append(_text(language, "no_series"))

    size_histogram = dict(Counter(series.series_size for series in series_list))
    typical_size = max(size_histogram, key=lambda size: size_histogram[size]) if size_histogram else None

    step_by_size = _median_by_size(series_list, lambda series: series.estimated_step_ev)
    duration_by_size = _median_by_size(series_list, lambda series: series.duration_seconds)
    adjacent_by_size = _adjacent_gap_by_size(series_list)

    source_votes = Counter(
        series.exposure_source
        for series in series_list
        if series.exposure_source and series.exposure_source not in {"UNKNOWN", "PARTIAL"}
    )
    reliable_source = source_votes.most_common(1)[0][0] if source_votes else None
    degenerate_groups = sum(
        1 for series in series_list if any("BRACKET_EXPOSURE_SKIPPED_EXPOSURECOMPENSATION" in reason for reason in series.reasons)
    )

    if typical_size is not None:
        _suggest_bracket_size_mode(suggestions, settings, size_histogram, typical_size, language)
        _suggest_step_and_tolerance(suggestions, settings, series_list, typical_size, step_by_size, language)
        _suggest_duration_limits(suggestions, settings, typical_size, duration_by_size, adjacent_by_size, language)
        _suggest_confidence_threshold(suggestions, settings, series_list, typical_size, language)

    pair_delta = _suggest_pair_delta(suggestions, settings, raw_files, rendered_files, language)

    return AutoConfigReport(
        sample_size=len(metadata_items),
        raw_count=len(raw_files),
        rendered_count=len(rendered_files),
        cameras=cameras,
        raw_formats=raw_formats,
        rendered_formats=rendered_formats,
        subsecond_available=subsecond_fraction >= 0.5,
        subsecond_fraction=subsecond_fraction,
        reliable_exposure_source=reliable_source,
        exposure_source_votes=dict(source_votes),
        exposure_source_degenerate_groups=degenerate_groups,
        exposure_source_total_groups=len(series_list),
        typical_series_size=typical_size,
        series_size_histogram=size_histogram,
        typical_step_ev=step_by_size,
        typical_duration_seconds=duration_by_size,
        typical_adjacent_gap_seconds=adjacent_by_size,
        typical_pair_delta_seconds=pair_delta,
        suggestions=tuple(suggestions),
        warnings=tuple(warnings),
    )


def apply_suggestions(
    settings: AppSettings,
    suggestions: Iterable[SettingSuggestion],
    accepted_fields: set[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppSettings:
    """Return a NEW ``AppSettings`` with accepted suggestions applied.

    ``settings`` is never mutated. ``accepted_fields`` defaults to "all suggested
    fields" — the GUI must pass the user's actual checkbox selection. ``overrides``
    lets the user edit a value before applying ("Изменить") without changing what
    was suggested elsewhere.
    """
    overrides = overrides or {}
    changes: dict[str, Any] = {}
    for suggestion in suggestions:
        if accepted_fields is not None and suggestion.field not in accepted_fields:
            continue
        changes[suggestion.field] = overrides.get(suggestion.field, suggestion.suggested_value)
    if not changes:
        return settings
    updated = dataclasses.replace(settings, **changes)
    if _ADJACENT_FIELD in changes or any(field in changes for field in _DURATION_FIELDS):
        updated = _reconcile_timing(updated, changes)
    return updated


def _reconcile_timing(updated: AppSettings, changes: dict[str, Any]) -> AppSettings:
    """Keep ``duration >= adjacent gap`` true whatever subset the user accepted.

    ``validate_settings`` rejects any per-size duration below the adjacent
    delta. The suggestions are consistent *as a set*, but the user may accept
    only one side: a 0.6 s duration next to the old 1.2 s gap, or a widened
    gap next to the old durations. An accepted duration wins — a gap between
    two frames can never exceed the whole series — so the gap is lowered to
    it; otherwise the untouched durations are raised to make room for the
    accepted gap. Either way the result validates by construction instead of
    failing later in the settings dialog.
    """
    fixes: dict[str, Any] = {}
    adjacent = updated.max_bracket_adjacent_delta_seconds
    accepted_durations = [getattr(updated, field) for field in _DURATION_FIELDS if field in changes]
    if accepted_durations and adjacent > min(accepted_durations):
        adjacent = min(accepted_durations)
        fixes[_ADJACENT_FIELD] = adjacent
    for field in _DURATION_FIELDS:
        if getattr(updated, field) < adjacent:
            fixes[field] = adjacent
    if fixes:
        updated = dataclasses.replace(updated, **fixes)
    return dataclasses.replace(
        updated,
        max_bracket_duration_seconds=max(updated.max_bracket_duration_seconds_3, updated.max_bracket_duration_seconds_5),
    )


# --------------------------------------------------------------------- helpers


def _empty_report(warnings: list[str]) -> AutoConfigReport:
    return AutoConfigReport(
        sample_size=0,
        raw_count=0,
        rendered_count=0,
        cameras=(),
        raw_formats=(),
        rendered_formats=(),
        subsecond_available=False,
        subsecond_fraction=0.0,
        reliable_exposure_source=None,
        exposure_source_votes={},
        exposure_source_degenerate_groups=0,
        exposure_source_total_groups=0,
        typical_series_size=None,
        series_size_histogram={},
        typical_step_ev={},
        typical_duration_seconds={},
        typical_adjacent_gap_seconds={},
        typical_pair_delta_seconds=None,
        suggestions=(),
        warnings=tuple(warnings),
    )


def _camera_signatures(metadata_items: list[PhotoMetadata]) -> tuple[CameraSignature, ...]:
    counter = Counter((item.make or "", item.model or "") for item in metadata_items if item.make or item.model)
    return tuple(
        CameraSignature(make=make, model=model, count=count) for (make, model), count in counter.most_common()
    )


def _subsecond_fraction(metadata_items: list[PhotoMetadata]) -> float:
    if not metadata_items:
        return 0.0
    hits = sum(1 for item in metadata_items if any(item.raw_exif_record.get(tag) for tag in _SUBSECOND_TAGS))
    return hits / len(metadata_items)


def _bracket_discovery_settings(settings: AppSettings) -> AppSettings:
    """A copy of ``settings`` loosened so real clusters surface regardless of
    the user's current thresholds. Never persisted, never shown to the user."""
    return dataclasses.replace(
        settings,
        bracket_size_mode=BracketSizeMode.BOTH,
        detection_mode=DetectionMode.NORMAL,
        max_bracket_adjacent_delta_seconds=max(settings.max_bracket_adjacent_delta_seconds, 5.0),
        max_bracket_duration_seconds_3=max(settings.max_bracket_duration_seconds_3, 20.0),
        max_bracket_duration_seconds_5=max(settings.max_bracket_duration_seconds_5, 30.0),
        max_bracket_duration_seconds=max(settings.max_bracket_duration_seconds, 30.0),
        min_exposure_span_ev=_discovery_min_span_ev(settings),
        exposure_step_tolerance=2.0,
        require_exposure_for_auto_series=False,
        min_auto_confidence=1.0,
    )


def _discovery_min_span_ev(settings: AppSettings) -> float:
    """The exposure span a discovered cluster must show to count as a bracket.

    ``max(0.5 EV, half the user's min_exposure_span_ev)``, capped at the user's
    own value: half, so a camera the current setting is too strict for still
    surfaces and can be suggested; the 0.5 EV floor (see
    ``_DISCOVERY_MIN_SPAN_EV``) so auto-exposure drift in a burst is never
    mistaken for a bracket; and never above what the user already accepts.
    """
    user_min = settings.min_exposure_span_ev
    return max(min(_DISCOVERY_MIN_SPAN_EV, user_min), user_min / 2)


def _pairing_discovery_settings(settings: AppSettings) -> AppSettings:
    return dataclasses.replace(
        settings,
        detection_mode=DetectionMode.NORMAL,
        max_pair_delta_seconds=max(settings.max_pair_delta_seconds, 15.0),
        pair_accept_score_normal=max(1.0, settings.pair_accept_score_normal - 30.0),
        pair_second_best_margin=0.0,
    )


def _median_by_size(series_list, extract) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for series in series_list:
        value = extract(series)
        if value is not None:
            grouped.setdefault(series.series_size, []).append(value)
    return {size: statistics.median(values) for size, values in grouped.items() if values}


def _adjacent_gap_by_size(series_list) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for series in series_list:
        ordered = sort_photo_files_by_capture_time(list(series.raw_files))
        max_gap = 0.0
        found = False
        for left, right in zip(ordered, ordered[1:]):
            result = safe_file_time_difference_seconds(left, right)
            if result.absolute_seconds is not None:
                max_gap = max(max_gap, result.absolute_seconds)
                found = True
        if found:
            grouped.setdefault(series.series_size, []).append(max_gap)
    return {size: statistics.median(values) for size, values in grouped.items() if values}


def _maybe_suggest(
    suggestions: list[SettingSuggestion],
    field: str,
    current: Any,
    suggested: Any,
    rationale: str,
) -> None:
    if isinstance(current, (int, float)) and isinstance(suggested, (int, float)) and not isinstance(current, bool):
        if current == 0:
            changed = abs(suggested - current) > 1e-9
        else:
            changed = abs(suggested - current) / abs(current) > _RELATIVE_CHANGE_THRESHOLD
    else:
        changed = current != suggested
    if changed:
        suggestions.append(SettingSuggestion(field=field, current_value=current, suggested_value=suggested, rationale=rationale))


def _suggest_bracket_size_mode(suggestions, settings: AppSettings, histogram: dict[int, int], typical_size: int, language: str) -> None:
    present = {size for size in histogram if size in (3, 5)}
    if len(present) >= 2:
        suggested_mode = BracketSizeMode.BOTH
    elif typical_size == 3:
        suggested_mode = BracketSizeMode.THREE
    elif typical_size == 5:
        suggested_mode = BracketSizeMode.FIVE
    else:
        return
    if suggested_mode is not settings.bracket_size_mode:
        total = sum(histogram.values())
        rationale = _text(
            language, "bracket_size_mode", size=typical_size, total=total, histogram=_format_histogram(histogram)
        )
        suggestions.append(
            SettingSuggestion(
                field="bracket_size_mode",
                current_value=settings.bracket_size_mode.value,
                suggested_value=suggested_mode,
                rationale=rationale,
            )
        )


def _suggest_step_and_tolerance(suggestions, settings: AppSettings, series_list, typical_size: int, step_by_size: dict[int, float], language: str) -> None:
    step = step_by_size.get(typical_size)
    if step is None:
        return
    steps = [series.estimated_step_ev for series in series_list if series.series_size == typical_size and series.estimated_step_ev is not None]
    spread = statistics.pstdev(steps) if len(steps) > 1 else 0.0
    suggested_span = round(max(0.1, step * 0.6), 2)
    suggested_tolerance = round(max(0.1, spread * 2.5 + 0.1), 2)
    _maybe_suggest(
        suggestions,
        "min_exposure_span_ev",
        settings.min_exposure_span_ev,
        suggested_span,
        _text(language, "min_exposure_span_ev", step=step, size=typical_size),
    )
    _maybe_suggest(
        suggestions,
        "exposure_step_tolerance",
        settings.exposure_step_tolerance,
        suggested_tolerance,
        _text(language, "exposure_step_tolerance", spread=spread),
    )


def _suggest_duration_limits(suggestions, settings: AppSettings, typical_size: int, duration_by_size: dict[int, float], adjacent_by_size: dict[int, float], language: str) -> None:
    duration = duration_by_size.get(typical_size)
    adjacent = adjacent_by_size.get(typical_size)
    if adjacent is not None:
        suggested_adjacent = round(max(0.2, adjacent * 1.5), 2)
        _maybe_suggest(
            suggestions,
            "max_bracket_adjacent_delta_seconds",
            settings.max_bracket_adjacent_delta_seconds,
            suggested_adjacent,
            _text(language, "max_bracket_adjacent_delta_seconds", observed=adjacent),
        )
    if duration is None:
        return
    field = "max_bracket_duration_seconds_3" if typical_size == 3 else "max_bracket_duration_seconds_5"
    current = getattr(settings, field)
    suggested_duration = round(max(0.5, duration * 1.5), 2)
    _maybe_suggest(suggestions, field, current, suggested_duration, _text(language, "max_bracket_duration", size=typical_size, observed=duration))


def _suggest_confidence_threshold(suggestions, settings: AppSettings, series_list, typical_size: int, language: str) -> None:
    confidences = [series.confidence for series in series_list if series.series_size == typical_size]
    if not confidences:
        return
    suggested = round(min(99.0, max(50.0, min(confidences) - 5.0)), 1)
    _maybe_suggest(
        suggestions,
        "min_auto_confidence",
        settings.min_auto_confidence,
        suggested,
        _text(language, "min_auto_confidence", lowest=min(confidences), count=len(confidences)),
    )


def _suggest_pair_delta(suggestions, settings: AppSettings, raw_files: list[PhotoFile], rendered_files: list[PhotoFile], language: str) -> float | None:
    if not raw_files or not rendered_files:
        return None
    discovery = _pairing_discovery_settings(settings)
    pairs = PairMatcher().match(list(raw_files), list(rendered_files), discovery)
    deltas = sorted(pair.time_delta for pair in pairs if pair.time_delta is not None)
    if not deltas:
        return None
    index = max(0, min(len(deltas) - 1, int(round(0.9 * (len(deltas) - 1)))))
    p90 = deltas[index]
    suggested = round(min(10.0, max(0.2, p90 * 1.5)), 2)
    _maybe_suggest(
        suggestions,
        "max_pair_delta_seconds",
        settings.max_pair_delta_seconds,
        suggested,
        _text(language, "max_pair_delta_seconds", observed=p90, pairs=len(deltas)),
    )
    return p90


def _format_histogram(histogram: dict[int, int]) -> str:
    return ", ".join(f"{size}×{count}" for size, count in sorted(histogram.items()))


def _text(language: str, key: str, **kwargs: Any) -> str:
    template = _RATIONALE_TEMPLATES.get(language, _RATIONALE_TEMPLATES["en"]).get(key, key)
    if language == "ru" and "size" in kwargs:
        kwargs = {**kwargs, "size_word": _russian_frame_count(kwargs["size"])}
    return template.format(**kwargs)


def _russian_frame_count(size: int) -> str:
    """'{size} кадра' vs '{size} кадров', standard Russian genitive plural rules."""
    if 11 <= size % 100 <= 14:
        word = "кадров"
    elif size % 10 in (2, 3, 4):
        word = "кадра"
    else:
        word = "кадров"
    return f"{size} {word}"


_RATIONALE_TEMPLATES: dict[str, dict[str, str]] = {
    "ru": {
        "no_files": "В папке не найдено файлов для анализа.",
        "no_raw": "RAW-файлы не найдены; настройки брекетинга предложить нельзя.",
        "no_series": "Не найдено достаточно кадров, снятых сериями, чтобы предложить настройки брекетинга.",
        "bracket_size_mode": "Среди {total} обнаруженных серий преобладает размер {size} (распределение: {histogram}).",
        "min_exposure_span_ev": "Типичный шаг экспозиции в сериях по {size_word} — {step:.2f} EV.",
        "exposure_step_tolerance": "Разброс шага экспозиции между кадрами серии — {spread:.2f} EV.",
        "max_bracket_adjacent_delta_seconds": "Наблюдаемый интервал между соседними кадрами серии — {observed:.2f} с.",
        "max_bracket_duration": "Наблюдаемая длительность серий по {size_word} — {observed:.2f} с.",
        "min_auto_confidence": "Минимальная уверенность среди {count} найденных серий — {lowest:.1f}.",
        "max_pair_delta_seconds": "90-й перцентиль интервала между RAW и превью — {observed:.2f} с ({pairs} пар).",
    },
    "en": {
        "no_files": "No files were found to analyze.",
        "no_raw": "No RAW files found; bracketing settings cannot be suggested.",
        "no_series": "Not enough frames shot in series were found to suggest bracketing settings.",
        "bracket_size_mode": "Size {size} dominates the {total} series found (distribution: {histogram}).",
        "min_exposure_span_ev": "The typical exposure step in {size}-shot series is {step:.2f} EV.",
        "exposure_step_tolerance": "The exposure step spread within a series is {spread:.2f} EV.",
        "max_bracket_adjacent_delta_seconds": "The observed gap between adjacent series frames is {observed:.2f} s.",
        "max_bracket_duration": "The observed duration of {size}-shot series is {observed:.2f} s.",
        "min_auto_confidence": "The lowest confidence among {count} series found is {lowest:.1f}.",
        "max_pair_delta_seconds": "The 90th percentile RAW-to-preview interval is {observed:.2f} s ({pairs} pairs).",
    },
}
