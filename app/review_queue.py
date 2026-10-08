"""Manual review queue: surfaces borderline analysis results and lets the user
resolve them without leaving the GUI.

Every action here goes through :class:`app.series_manager.SeriesManager` or
:func:`app.manual_overrides.assign_category` — the same single owners of series
and category state used everywhere else in the app. This module adds no second
place where that state could live; it only decides *what to show* and *which
existing operation to call*.

The queue is rebuilt from scratch on every call. There is one piece of
persistent state introduced here: a "not a bracket" dismissal is recorded as a
marker in the affected files' own ``decision_reasons`` (an existing field on
``PhotoFile``), not in a separate table, so a rejected candidate stops
reappearing without inventing a new place for that fact to live.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Iterable

from app.enums import DestinationCategory
from app.exposure import priority_from_names, resolve_group_exposure
from app.manual_overrides import assign_category
from app.models import AnalysisResult, AppSettings, BracketCandidate, BracketSeries, PhotoFile
from app.series_manager import SeriesManager

#: Recorded on every file of a candidate the user explicitly rejected, so
#: :func:`build_review_queue` never surfaces it again.
DISMISS_NOT_BRACKET_MARKER = "REVIEW_DISMISSED_NOT_BRACKET"

#: Recorded on every file explicitly released back to a plain single via
#: "split" (or a "restore automatic" the real detector declines to confirm),
#: so a saved session (feature 9) can tell "the user broke this apart on
#: purpose" apart from "this file was never grouped at all" — and a fresh
#: reanalysis whose automatic detector would re-offer the exact same window
#: does not silently re-form it as if the split never happened.
SPLIT_KEEP_SINGLE_MARKER = "REVIEW_SPLIT_KEEP_SINGLE"

_MIXED_TIME_WARNING_CODES = ("MIXED_TIMEZONE_ASSUMED_OFFSET", "TIME_COMPARISON_UNAVAILABLE")
_WEAK_TIME_SOURCES = ("FILE_NAME", "FILE_MTIME", "UNKNOWN")

#: A near-miss candidate below this confidence is only shown when its frames
#: carry real exposure variation. 45 is the ``ConfidenceLevel.LOW`` boundary:
#: anything under it is what the detector itself calls REVIEW-grade noise, and
#: a plain burst produces one such window per sliding position (34 of them for
#: a 20-frame burst), which is a flood, not a queue.
NEAR_MISS_MIN_CONFIDENCE = 45.0

#: The note ``app.pairing.PairMatcher`` writes on both files of an ambiguous
#: RAW/rendered match it declined to pair. Matched by substring because the
#: matcher records it as free text, not a code.
_AMBIGUOUS_PAIR_WARNING_FRAGMENT = "неоднозначен"


class ReviewReason(str, Enum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNEVEN_STEP = "UNEVEN_STEP"
    INCOMPLETE_SERIES = "INCOMPLETE_SERIES"
    NEAR_MISS_CANDIDATE = "NEAR_MISS_CANDIDATE"
    AMBIGUOUS_PAIR = "AMBIGUOUS_PAIR"
    MIXED_TIME_SOURCE = "MIXED_TIME_SOURCE"
    NAME_CONFLICT = "NAME_CONFLICT"
    MIXED_EVIDENCE = "MIXED_EVIDENCE"
    MANUAL_EDIT = "MANUAL_EDIT"
    FILE_NEEDS_REVIEW = "FILE_NEEDS_REVIEW"
    #: A feature-8 extended classification (incomplete bracket, focus-bracket,
    #: probable panorama) — informational by construction, so it is only ever
    #: worth a look, never something the app already acted on.
    EXTENDED_CLASSIFICATION = "EXTENDED_CLASSIFICATION"


@dataclasses.dataclass(frozen=True, slots=True)
class ReviewItem:
    item_id: str
    reason: ReviewReason
    files: tuple[PhotoFile, ...]
    summary: str
    series_uid: str | None = None
    candidate_id: str | None = None
    pair_id: str | None = None


def build_review_queue(
    analysis: AnalysisResult,
    manager: SeriesManager,
    settings: AppSettings,
    language: str = "ru",
) -> list[ReviewItem]:
    """Rebuild the queue from current, live state. Never mutates anything."""
    items: list[ReviewItem] = []
    items.extend(_series_items(manager, settings, language))
    items.extend(_near_miss_candidate_items(analysis, settings, language))
    items.extend(_ambiguous_pair_items(analysis, language))
    items.extend(_name_conflict_items(analysis, language))
    items.extend(_manual_file_override_items(analysis, language))
    items.extend(_file_needs_review_items(analysis, language))
    items.extend(_extended_classification_items(analysis, language))
    return items


#: group_classification values (see app.enums.GroupClassification) worth a
#: manual look. HDR3/5/7/9, pairs, and plain singles are confident enough not
#: to need one.
_EXTENDED_REVIEW_LABELS = ("INCOMPLETE_BRACKET", "FOCUS_BRACKET", "PROBABLE_PANORAMA")


def _extended_classification_items(analysis: AnalysisResult, language: str) -> list[ReviewItem]:
    """One item per cluster, not per file. Clusters produced by the same
    ``classify_extended_groups`` call carry identical evidence text (it is
    written once and copied to every member), so grouping by
    (label, evidence) recovers cluster membership without re-deriving it —
    exactly the "read real state, don't re-decide it" rule this whole module
    already follows."""
    groups: dict[tuple[str, tuple[str, ...]], list[PhotoFile]] = {}
    for file in analysis.files:
        if file.group_classification not in _EXTENDED_REVIEW_LABELS:
            continue
        key = (file.group_classification, tuple(file.group_classification_evidence))
        groups.setdefault(key, []).append(file)
    items: list[ReviewItem] = []
    for (label, evidence_tuple), cluster in groups.items():
        evidence = "; ".join(evidence_tuple) or label
        items.append(
            ReviewItem(
                item_id=f"extended:{label}:{id(cluster[0])}",
                reason=ReviewReason.EXTENDED_CLASSIFICATION,
                files=tuple(cluster),
                summary=_text(language, "extended_classification", label=label, evidence=evidence),
            )
        )
    return items


# --------------------------------------------------------------------- series


def _series_items(manager: SeriesManager, settings: AppSettings, language: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for series in manager.series:
        if series.automatic:
            if series.confidence_level.value in {"LOW", "REVIEW"}:
                items.append(
                    ReviewItem(
                        item_id=f"low_confidence:{series.uid}",
                        reason=ReviewReason.LOW_CONFIDENCE,
                        files=tuple(series.raw_files),
                        series_uid=series.uid,
                        summary=_text(language, "low_confidence", series_id=series.series_id, confidence=series.confidence),
                    )
                )
            group = resolve_group_exposure(series.raw_files, settings.exposure_step_tolerance, priority_from_names(settings.exposure_source_priority))
            if group.usable and not group.uniform:
                items.append(
                    ReviewItem(
                        item_id=f"uneven_step:{series.uid}",
                        reason=ReviewReason.UNEVEN_STEP,
                        files=tuple(series.raw_files),
                        series_uid=series.uid,
                        summary=_text(language, "uneven_step", series_id=series.series_id),
                    )
                )
            if "BRACKET_DIMENSIONS_OK" not in series.reasons:
                items.append(
                    ReviewItem(
                        item_id=f"mixed_evidence:{series.uid}",
                        reason=ReviewReason.MIXED_EVIDENCE,
                        files=tuple(series.raw_files),
                        series_uid=series.uid,
                        summary=_text(language, "mixed_evidence", series_id=series.series_id),
                    )
                )
            companions = list(series.rendered_companions.values())
            resolved = sum(1 for companion in companions if companion is not None)
            if 0 < resolved < len(companions):
                items.append(
                    ReviewItem(
                        item_id=f"incomplete:{series.uid}",
                        reason=ReviewReason.INCOMPLETE_SERIES,
                        files=tuple(series.raw_files),
                        series_uid=series.uid,
                        summary=_text(language, "incomplete_series", series_id=series.series_id, resolved=resolved, total=len(companions)),
                    )
                )
            if any(code in series.warnings for code in _MIXED_TIME_WARNING_CODES) or _has_weak_time_source(series):
                items.append(
                    ReviewItem(
                        item_id=f"mixed_time:{series.uid}",
                        reason=ReviewReason.MIXED_TIME_SOURCE,
                        files=tuple(series.raw_files),
                        series_uid=series.uid,
                        summary=_text(language, "mixed_time", series_id=series.series_id),
                    )
                )
        else:
            items.append(
                ReviewItem(
                    item_id=f"manual_edit:{series.uid}",
                    reason=ReviewReason.MANUAL_EDIT,
                    files=tuple(series.raw_files),
                    series_uid=series.uid,
                    summary=_text(language, "manual_series", series_id=series.series_id),
                )
            )
    return items


def _has_weak_time_source(series: BracketSeries) -> bool:
    sources = {file.metadata.capture_time_source.value for file in series.raw_files}
    return len(sources) > 1 and bool(sources & set(_WEAK_TIME_SOURCES))


# ------------------------------------------------------------- near-miss candidates


def _near_miss_candidate_items(analysis: AnalysisResult, settings: AppSettings, language: str) -> list[ReviewItem]:
    """One item per *distinct* group worth a look, not one per sliding window.

    The detector scores every 3- and 5-frame window, so a burst of N frames
    yields roughly 2N overlapping candidates. Windows that are plainly noise
    (REVIEW-grade confidence with no exposure variation) are dropped, files
    the user already dealt with (excluded, manually categorised, confirmed,
    dismissed) are skipped, and of the windows that remain only the strongest
    non-overlapping ones are listed — confirming one of them resolves the
    overlapping ones anyway, so showing them all is pure duplication.
    """
    eligible: list[tuple[int, BracketCandidate]] = []
    for position, candidate in enumerate(analysis.bracket_candidates):
        if any(file.series_id is not None for file in candidate.files):
            continue  # already resolved into a series (this window, or an overlapping one, was accepted)
        if any(DISMISS_NOT_BRACKET_MARKER in file.decision_reasons for file in candidate.files):
            continue  # the user already looked at this exact group and said no
        if any(_user_resolved(file) for file in candidate.files):
            continue  # "Исключить" or a manual category already settled at least one frame
        if candidate.confidence < NEAR_MISS_MIN_CONFIDENCE and not _has_exposure_evidence(candidate, settings):
            continue  # a burst window, not a bracket that narrowly missed
        eligible.append((position, candidate))

    # Strongest first; among equals, the larger window, then detector order.
    eligible.sort(key=lambda entry: (-entry[1].confidence, -entry[1].candidate_size, entry[0]))
    claimed: set[int] = set()
    chosen: list[tuple[int, BracketCandidate]] = []
    for position, candidate in eligible:
        ids = {id(file) for file in candidate.files}
        if ids & claimed:
            continue
        claimed |= ids
        chosen.append((position, candidate))
    chosen.sort(key=lambda entry: entry[0])

    items: list[ReviewItem] = []
    for _position, candidate in chosen:
        items.append(
            ReviewItem(
                item_id=f"near_miss:{candidate.candidate_id}",
                reason=ReviewReason.NEAR_MISS_CANDIDATE,
                files=tuple(candidate.files),
                candidate_id=candidate.candidate_id,
                summary=_text(
                    language, "near_miss", size=candidate.candidate_size, confidence=candidate.confidence, reasons=", ".join(candidate.reasons) or "-"
                ),
            )
        )
    return items


def _has_exposure_evidence(candidate: BracketCandidate, settings: AppSettings) -> bool:
    """True when the window's frames really vary in exposure like a bracket.

    The detector stops scoring early on timing failures, so a genuine bracket
    shot a little too slowly carries no exposure data on the candidate itself;
    the frames are therefore re-read with the same resolver and settings the
    detector uses, not a second rule.
    """
    if "BRACKET_EXPOSURE_SPAN_OK" in candidate.reasons:
        return True
    group = resolve_group_exposure(candidate.files, settings.exposure_step_tolerance, priority_from_names(settings.exposure_source_priority))
    return group.usable and group.span is not None and group.span >= settings.min_exposure_span_ev


def _user_resolved(file: PhotoFile) -> bool:
    """The user already decided this file's fate; the queue must not re-ask."""
    return file.destination_category is DestinationCategory.SKIP or file.manual_override is not None


# --------------------------------------------------------------- ambiguous pairs


def _ambiguous_pair_items(analysis: AnalysisResult, language: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for pair in analysis.pairs:
        if pair.ambiguous and not (_user_resolved(pair.raw_file) or _user_resolved(pair.rendered_file)):
            items.append(
                ReviewItem(
                    item_id=f"ambiguous_pair:{pair.pair_id}",
                    reason=ReviewReason.AMBIGUOUS_PAIR,
                    files=(pair.raw_file, pair.rendered_file),
                    pair_id=pair.pair_id,
                    summary=_text(language, "ambiguous_pair", raw=pair.raw_file.metadata.file_name, rendered=pair.rendered_file.metadata.file_name),
                )
            )
    seen_files: set[int] = set()
    for file in analysis.files:
        if id(file) in seen_files:
            continue
        if _user_resolved(file):
            continue  # excluded or manually categorised: the ambiguity no longer needs an answer
        if any(_AMBIGUOUS_PAIR_WARNING_FRAGMENT in warning for warning in file.warnings) and file.pair_id is None:
            seen_files.add(id(file))
            items.append(
                ReviewItem(
                    item_id=f"ambiguous_unpaired:{id(file)}",
                    reason=ReviewReason.AMBIGUOUS_PAIR,
                    files=(file,),
                    summary=_text(language, "ambiguous_unpaired", name=file.metadata.file_name),
                )
            )
    return items


# ---------------------------------------------------------------- name conflicts


def _name_conflict_items(analysis: AnalysisResult, language: str) -> list[ReviewItem]:
    by_subpath: dict[str, list[PhotoFile]] = {}
    for file in analysis.files:
        if not file.selected or file.destination_subpath is None:
            continue
        key = file.destination_subpath.as_posix().lower()
        by_subpath.setdefault(key, []).append(file)
    items: list[ReviewItem] = []
    for subpath, files in by_subpath.items():
        if len(files) < 2:
            continue
        items.append(
            ReviewItem(
                item_id=f"name_conflict:{subpath}",
                reason=ReviewReason.NAME_CONFLICT,
                files=tuple(files),
                summary=_text(language, "name_conflict", path=subpath, count=len(files)),
            )
        )
    return items


# ------------------------------------------------------------------ manual edits


def _manual_file_override_items(analysis: AnalysisResult, language: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for file in analysis.files:
        if file.manual_override is not None and file.series_id is None:
            items.append(
                ReviewItem(
                    item_id=f"manual_override:{id(file)}",
                    reason=ReviewReason.MANUAL_EDIT,
                    files=(file,),
                    summary=_text(language, "manual_override", name=file.metadata.file_name, category=file.manual_override.value),
                )
            )
    return items


def _file_needs_review_items(analysis: AnalysisResult, language: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for file in analysis.files:
        if file.destination_category is DestinationCategory.REVIEW:
            items.append(
                ReviewItem(
                    item_id=f"file_review:{id(file)}",
                    reason=ReviewReason.FILE_NEEDS_REVIEW,
                    files=(file,),
                    summary=_text(language, "file_review", name=file.metadata.file_name, reasons=", ".join(file.decision_reasons) or "-"),
                )
            )
    return items


# ------------------------------------------------------------------------ actions


def dismiss_candidate(candidate: BracketCandidate) -> None:
    """'Не является брекетингом': the group stops being suggested as a bracket."""
    for file in candidate.files:
        if DISMISS_NOT_BRACKET_MARKER not in file.decision_reasons:
            file.decision_reasons.append(DISMISS_NOT_BRACKET_MARKER)


def confirm_candidate(manager: SeriesManager, candidate: BracketCandidate, size: int | None = None) -> BracketSeries:
    """'Подтвердить HDR3/5' (or a chosen size) for a near-miss candidate."""
    return manager.create_manual(list(candidate.files), size or candidate.candidate_size)


def exclude_files(files: Iterable[PhotoFile], manager: SeriesManager | None = None) -> None:
    """'Исключить': the files are skipped entirely, series membership is dropped first."""
    file_list = list(files)
    if manager is not None:
        manager.dissolve_containing(file_list)
    for file in file_list:
        assign_category(file, DestinationCategory.SKIP)


def split_series(manager: SeriesManager, series: BracketSeries) -> list[PhotoFile]:
    """'Разделить': release every member back to a plain single RAW/rendered file."""
    released = manager.dissolve(series)
    _mark_split_keep_single(released)
    return released


def merge_series(manager: SeriesManager, series: BracketSeries, extra_files: Iterable[PhotoFile], target_size: int) -> BracketSeries:
    """'Объединить с соседней': fold neighboring loose frames into an existing series.

    Every ``BracketCandidate``/``BracketSeries`` is already a full window (exactly
    3 or 5 RAW files), so two complete series can never combine into another valid
    size — 3+3, 3+5, and 5+5 all overshoot both supported sizes. What "merge with
    the neighbor" means in practice is completing a series (or a confirmed
    near-miss) with adjacent single frames the user selects alongside it.

    Raises :class:`ManualSeriesError` if the combined member count is not a
    supported size, or if the files fail the usual manual-series validation.
    """
    combined = list(series.raw_files) + list(extra_files)
    manager.dissolve(series)
    return manager.create_manual(combined, target_size)


def restore_automatic(manager: SeriesManager, series: BracketSeries, settings: AppSettings) -> BracketSeries | None:
    """'Вернуть автоматическое решение': let the real detector re-judge these files.

    Not a special-cased approximation — this dissolves the manual grouping and
    asks :class:`app.bracket_detection.BracketDetector` whether it would accept
    exactly this file set under the current (non-loosened) settings. Returns
    ``None``, leaving the files as plain singles, if it would not.
    """
    from app.bracket_detection import BracketDetector
    from app.enums import BracketSizeMode

    files = manager.dissolve(series)
    size = len(files)
    size_mode = {3: BracketSizeMode.THREE, 5: BracketSizeMode.FIVE}.get(size)
    if size_mode is None:
        _mark_split_keep_single(files)
        return None
    scoped_settings = dataclasses.replace(settings, bracket_size_mode=size_mode)
    _candidates, detected = BracketDetector().detect(files, scoped_settings)
    if not detected:
        _mark_split_keep_single(files)
        return None
    # The detector numbers from 1 on every call; the manager hands out the next
    # free automatic number so the restored series never shares an id or a
    # destination folder with one that already exists.
    return manager.add_automatic(detected[0])


def _mark_split_keep_single(files: Iterable[PhotoFile]) -> None:
    for file in files:
        if SPLIT_KEEP_SINGLE_MARKER not in file.decision_reasons:
            file.decision_reasons.append(SPLIT_KEEP_SINGLE_MARKER)


# ------------------------------------------------------------------------- text


def _text(language: str, key: str, **kwargs: object) -> str:
    template = _SUMMARY_TEMPLATES.get(language, _SUMMARY_TEMPLATES["en"]).get(key, key)
    return template.format(**kwargs)


_SUMMARY_TEMPLATES: dict[str, dict[str, str]] = {
    "ru": {
        "low_confidence": "{series_id}: низкая уверенность ({confidence:.0f}).",
        "uneven_step": "{series_id}: шаг экспозиции неравномерен.",
        "incomplete_series": "{series_id}: найдено {resolved} из {total} компаньонов.",
        "mixed_evidence": "{series_id}: размеры кадров не полностью совпадают.",
        "mixed_time": "{series_id}: смешанные источники времени.",
        "manual_series": "{series_id}: создана вручную.",
        "near_miss": "Похоже на серию из {size} кадров (уверенность {confidence:.0f}), но не прошла порог. Причины: {reasons}.",
        "ambiguous_pair": "Пара {raw} / {rendered} неоднозначна.",
        "ambiguous_unpaired": "{name}: не удалось однозначно найти пару.",
        "name_conflict": "{count} файлов претендуют на один путь: {path}.",
        "manual_override": "{name}: назначено вручную ({category}).",
        "file_review": "{name}: требует проверки. Причины: {reasons}.",
        "extended_classification": "{label}: {evidence}",
    },
    "en": {
        "low_confidence": "{series_id}: low confidence ({confidence:.0f}).",
        "uneven_step": "{series_id}: exposure step is uneven.",
        "incomplete_series": "{series_id}: found {resolved} of {total} companions.",
        "mixed_evidence": "{series_id}: frame dimensions do not fully agree.",
        "mixed_time": "{series_id}: mixed time sources.",
        "manual_series": "{series_id}: created manually.",
        "near_miss": "Looks like a {size}-shot series (confidence {confidence:.0f}) but missed the threshold. Reasons: {reasons}.",
        "ambiguous_pair": "Pair {raw} / {rendered} is ambiguous.",
        "ambiguous_unpaired": "{name}: no unambiguous companion was found.",
        "name_conflict": "{count} files target the same path: {path}.",
        "manual_override": "{name}: manually assigned ({category}).",
        "file_review": "{name}: needs review. Reasons: {reasons}.",
        "extended_classification": "{label}: {evidence}",
    },
}
