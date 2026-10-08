"""RAW + rendered companion matching.

Candidate selection
-------------------
Scoring every RAW against every rendered file is O(N*M); at 5000 + 5000 that is 25
million scoring calls, each running a datetime comparison and a ``SequenceMatcher``
ratio. Instead, rendered files are indexed by capture time and each RAW only scores
the rendered files inside its time window.

This is not an approximation. A pair earns at most :data:`MAX_SCORE_WITHOUT_TIME`
from camera, exposure, geometry, and name evidence combined, which is below every
built-in acceptance threshold. A pair outside the time window therefore could never
have been accepted. When a user lowers the threshold below that ceiling, the
exhaustive fallback is re-enabled automatically.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from datetime import UTC, datetime
from difflib import SequenceMatcher

from app.format_detection import is_raw_type, is_rendered_type
from app.models import AppSettings, PhotoFile, RawHeicPair
from app.perceptual_hash import PerceptualHashCache
from app.time_compare import (
    MIXED_TIMEZONE_ASSUMED_OFFSET,
    add_time_warning,
    is_aware,
    safe_file_time_difference_seconds,
)

#: Best total reachable with no time evidence: camera 20 + exposure 15 + geometry 10 + name 10.
MAX_SCORE_WITHOUT_TIME = 55.0

#: Time evidence stops contributing beyond this multiple of max_pair_delta_seconds.
TIME_WINDOW_FACTOR = 3.0

#: Trailing tokens that distinguish a rendered companion from its RAW sibling.
_COMPANION_SUFFIX = re.compile(r"(?:[_\-\s]?(?:raw|dng|heic|heif|hif|jpe?g|png|tiff?|proshot|hdr|edit|copy))+$")


class PairMatcher:
    def __init__(self, hash_cache: PerceptualHashCache | None = None):
        self.hash_cache = hash_cache or PerceptualHashCache()

    def match(
        self,
        raw_files: list[PhotoFile],
        rendered_files: list[PhotoFile],
        settings: AppSettings,
    ) -> list[RawHeicPair]:
        threshold = settings.pair_threshold()
        index = _RenderedIndex(rendered_files, settings, threshold)
        candidates: dict[tuple[int, int], tuple[float, list[str], list[str], float, float | None, float | None, list[tuple[str, float]]]] = {}
        for raw_index, raw_file in enumerate(raw_files):
            if not is_raw_type(raw_file.metadata.detected_type):
                continue
            for rendered_index in index.candidates_for(raw_file):
                rendered_file = rendered_files[rendered_index]
                scored = self._score_pair(raw_file, rendered_file, settings)
                if scored[0] > 0:
                    candidates[(raw_index, rendered_index)] = scored

        best_by_raw = _best_by_first(candidates)
        best_by_rendered = _best_by_second(candidates)
        runner_up_by_raw = _runner_up_by_first(candidates)
        runner_up_by_rendered = _runner_up_by_second(candidates)

        pairs: list[RawHeicPair] = []
        used_rendered: set[int] = set()
        pair_counter = 1
        for raw_index, (rendered_index, best_score) in sorted(best_by_raw.items()):
            reverse = best_by_rendered.get(rendered_index)
            if reverse is None or reverse[0] != raw_index:
                continue
            if rendered_index in used_rendered:
                continue
            score, reasons, warnings, name_score, time_delta, preview_score, breakdown = candidates[(raw_index, rendered_index)]
            # Ambiguity is checked from both sides: a rendered file with two equally
            # plausible RAW parents is just as unsafe as the reverse.
            raw_margin = best_score - runner_up_by_raw.get(raw_index, 0.0)
            rendered_margin = best_score - runner_up_by_rendered.get(rendered_index, 0.0)
            margin = min(raw_margin, rendered_margin)
            ambiguous = margin < settings.pair_second_best_margin
            if score < threshold or ambiguous:
                # Flagged whenever the margin is the reason, not only when the score
                # also happened to clear the threshold — a review queue needs to find
                # every ambiguous near-pair, not just the ones that were otherwise good.
                if ambiguous:
                    note = f"Компаньон RAW + HEIC неоднозначен (разрыв {margin:.1f} балла)."
                    raw_files[raw_index].warnings.append(note)
                    rendered_files[rendered_index].warnings.append(note)
                continue
            pair_id = f"PAIR_{pair_counter:04d}"
            pair_counter += 1
            raw = raw_files[raw_index]
            rendered = rendered_files[rendered_index]
            raw.pair_id = pair_id
            rendered.pair_id = pair_id
            pairs.append(
                RawHeicPair(
                    pair_id=pair_id,
                    raw_file=raw,
                    rendered_file=rendered,
                    score=round(score, 2),
                    time_delta=time_delta,
                    name_similarity=round(name_score, 3),
                    metadata_similarity=round(min(1.0, score / 85.0), 3),
                    preview_similarity=None if preview_score is None else round(preview_score, 3),
                    reasons=reasons,
                    warnings=warnings,
                    # An accepted pair can still be ambiguous (margin below the
                    # second-best threshold); the field must reflect that, not a
                    # constant, or nothing downstream can ever see it.
                    ambiguous=ambiguous,
                    score_breakdown=breakdown,
                )
            )
            used_rendered.add(rendered_index)
        return pairs

    def _score_pair(
        self,
        raw_file: PhotoFile,
        rendered_file: PhotoFile,
        settings: AppSettings,
    ) -> tuple[float, list[str], list[str], float, float | None, float | None, list[tuple[str, float]]]:
        raw = raw_file.metadata
        rendered = rendered_file.metadata
        if not is_raw_type(raw.detected_type) or not is_rendered_type(rendered.detected_type):
            return 0.0, [], [], 0.0, None, None, []
        reasons: list[str] = []
        warnings: list[str] = []
        breakdown: list[tuple[str, float]] = []
        score = 0.0

        time_result = safe_file_time_difference_seconds(raw_file, rendered_file)
        time_delta = time_result.absolute_seconds
        if time_result.warning_code:
            warnings.append(time_result.warning_code)
            add_time_warning(raw_file, time_result.warning_code)
            add_time_warning(rendered_file, time_result.warning_code)
            if time_result.warning_code == MIXED_TIMEZONE_ASSUMED_OFFSET:
                reasons.append("PAIR_TIME_MIXED_TIMEZONE_NORMALIZED")
        if time_delta is not None:
            if time_delta <= settings.max_pair_delta_seconds:
                time_points = 30.0 * (1.0 - min(time_delta / settings.max_pair_delta_seconds, 1.0))
                score += time_points
                reasons.append("PAIR_TIME_CLOSE")
                breakdown.append(("PAIR_TIME_CLOSE", time_points))
            elif time_delta <= settings.max_pair_delta_seconds * TIME_WINDOW_FACTOR:
                score += 5.0
                warnings.append("Время похоже, но выходит за основной порог.")
                breakdown.append(("PAIR_TIME_FAR", 5.0))
        else:
            warnings.append("Нет точного времени для пары.")

        if settings.use_exif_for_pairing:
            camera_score = 0.0
            if raw.make and rendered.make and raw.make == rendered.make:
                camera_score += 8.0
            if raw.model and rendered.model and raw.model == rendered.model:
                camera_score += 7.0
            if raw.lens_model and rendered.lens_model and raw.lens_model == rendered.lens_model:
                camera_score += 5.0
            if camera_score:
                score += camera_score
                reasons.append("PAIR_CAMERA_MATCH")
                breakdown.append(("PAIR_CAMERA_MATCH", camera_score))
            exposure_score = _exposure_similarity(raw_file, rendered_file)
            if exposure_score:
                score += exposure_score
                reasons.append("PAIR_EXPOSURE_MATCH")
                breakdown.append(("PAIR_EXPOSURE_MATCH", exposure_score))
            geometry_score = _geometry_similarity(raw_file, rendered_file)
            if geometry_score:
                score += geometry_score
                reasons.append("PAIR_GEOMETRY_MATCH")
                breakdown.append(("PAIR_GEOMETRY_MATCH", geometry_score))

        name_score = 0.0
        if settings.use_name_for_pairing:
            name_score = stem_similarity(raw.file_name, rendered.file_name)
            if name_score >= 0.65:
                name_points = 10.0 * name_score
                score += name_points
                reasons.append("PAIR_NAME_MATCH")
                breakdown.append(("PAIR_NAME_MATCH", name_points))

        preview_score = None
        if settings.use_preview_for_pairing or settings.preview_similarity_enabled:
            preview_score = self.hash_cache.similarity(raw.source_path, rendered.source_path)
            if preview_score is None:
                warnings.append("Превью недоступно для визуальной проверки пары.")
            elif preview_score >= settings.visual_similarity_threshold:
                score += 10.0
                reasons.append("PAIR_PREVIEW_MATCH")
                breakdown.append(("PAIR_PREVIEW_MATCH", 10.0))
            else:
                # Visually different frames are actively disqualified, not merely unrewarded.
                score -= 15.0
                warnings.append(f"Превью различаются (сходство {preview_score:.2f}).")
                breakdown.append(("PAIR_PREVIEW_MISMATCH", -15.0))

        if not reasons:
            warnings.append("Недостаточно признаков для уверенного компаньона.")
        return max(0.0, score), reasons, warnings, name_score, time_delta, preview_score, breakdown


class _RenderedIndex:
    """Time-ordered index over rendered files, for windowed candidate lookup.

    The index mirrors exactly what ``safe_datetime_difference_seconds`` compares,
    so that it never depends on the machine timezone:

    * naive vs naive compares wall-clock values;
    * aware vs aware compares true instants (UTC);
    * aware vs naive borrows the aware side's offset for the naive side, which is
      again a wall-clock comparison.

    Naive rendered files are therefore keyed by wall-clock seconds, aware ones by
    both their UTC and their wall-clock seconds, and each RAW is looked up against
    the key kind that applies to it. Reading naive values through a naive
    ``timestamp()`` (the PC's timezone) made a naive DNG at 12:00 and an aware HEIC
    at 12:00+09:00 meet only when the PC itself was on +09:00.
    """

    def __init__(self, rendered_files: list[PhotoFile], settings: AppSettings, threshold: float):
        self.window = max(settings.max_pair_delta_seconds * TIME_WINDOW_FACTOR, 0.0)
        # Only a threshold at or below the no-time ceiling can accept a pair that the
        # time window would exclude, so only then is the exhaustive scan needed.
        self.exhaustive = threshold <= MAX_SCORE_WITHOUT_TIME
        naive: list[tuple[float, int]] = []
        aware_utc: list[tuple[float, int]] = []
        aware_wall: list[tuple[float, int]] = []
        self._untimed: list[int] = []
        for index, file in enumerate(rendered_files):
            if not is_rendered_type(file.metadata.detected_type):
                continue
            value = file.metadata.capture_time
            wall = _epoch_seconds(value)
            if wall is None or value is None:
                self._untimed.append(index)
                continue
            if not is_aware(value):
                naive.append((wall, index))
                continue
            utc = _utc_seconds(value)
            if utc is None:
                self._untimed.append(index)
                continue
            aware_utc.append((utc, index))
            aware_wall.append((wall, index))
        self._naive = _SortedKeys(naive)
        self._aware_utc = _SortedKeys(aware_utc)
        self._aware_wall = _SortedKeys(aware_wall)
        self._all = [index for _stamp, index in sorted(naive + aware_wall)] + self._untimed

    def candidates_for(self, raw_file: PhotoFile) -> list[int]:
        if self.exhaustive:
            return self._all
        value = raw_file.metadata.capture_time
        wall = _epoch_seconds(value)
        if wall is None or value is None:
            # Without a timestamp the RAW can only match on non-time evidence, which
            # cannot clear the threshold on its own.
            return self._untimed
        if is_aware(value):
            utc = _utc_seconds(value)
            if utc is None:
                return self._untimed
            found = self._aware_utc.within(utc, self.window) + self._naive.within(wall, self.window)
        else:
            found = self._naive.within(wall, self.window) + self._aware_wall.within(wall, self.window)
        return found + self._untimed


class _SortedKeys:
    """Sorted ``(seconds, index)`` pairs answering "which indices lie within a window"."""

    __slots__ = ("_entries", "_keys")

    def __init__(self, entries: list[tuple[float, int]]):
        self._entries = sorted(entries)
        self._keys = [stamp for stamp, _index in self._entries]

    def within(self, stamp: float, window: float) -> list[int]:
        low = bisect_left(self._keys, stamp - window)
        high = bisect_right(self._keys, stamp + window)
        return [index for _stamp, index in self._entries[low:high]]


def _epoch_seconds(value: datetime | None) -> float | None:
    """Wall-clock seconds of ``value``; its UTC offset, if any, is ignored.

    Computed arithmetically against the UTC epoch, never through the naive
    ``timestamp()`` path: that one applies the machine timezone and, on Windows,
    raises ``OSError`` for anything before 1970. Only used to pick candidates; the
    actual delta always goes through ``safe_file_time_difference_seconds``.
    """
    if value is None:
        return None
    try:
        return value.replace(tzinfo=UTC).timestamp()
    except (OSError, OverflowError, ValueError):
        return None


def _utc_seconds(value: datetime) -> float | None:
    """True UTC seconds of an aware value, or ``None`` when they cannot be computed."""
    try:
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        return None


def split_raw_and_rendered(files: list[PhotoFile]) -> tuple[list[PhotoFile], list[PhotoFile]]:
    raw_files = [file for file in files if is_raw_type(file.metadata.detected_type)]
    rendered_files = [file for file in files if is_rendered_type(file.metadata.detected_type)]
    return raw_files, rendered_files


def stem_similarity(left_name: str, right_name: str) -> float:
    """Name similarity in ``[0.0, 1.0]``.

    Two names that normalise to nothing are treated as carrying no evidence.
    ``SequenceMatcher`` scores a pair of empty strings as a perfect 1.0, which made
    every non-Latin filename look like a perfect match for every other one.
    """
    left = normalize_stem(left_name)
    right = normalize_stem(right_name)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_stem(file_name: str) -> str:
    """Reduce a filename to a comparable stem, preserving non-Latin scripts.

    Applies NFKC normalisation first so the same name written in NFC (Windows) and
    NFD (macOS) compares equal, then keeps every alphanumeric character in any
    script rather than only ``[a-z0-9]``.
    """
    normalized = unicodedata.normalize("NFKC", file_name).casefold()
    stem = re.sub(r"\.[^.]+$", "", normalized)
    stem = _COMPANION_SUFFIX.sub("", stem)
    return "".join(char for char in stem if char.isalnum())


def _exposure_similarity(raw_file: PhotoFile, rendered_file: PhotoFile) -> float:
    raw = raw_file.metadata
    rendered = rendered_file.metadata
    score = 0.0
    if raw.iso and rendered.iso and abs(raw.iso - rendered.iso) <= max(20.0, raw.iso * 0.1):
        score += 4.0
    if raw.exposure_time and rendered.exposure_time:
        ratio = max(raw.exposure_time, rendered.exposure_time) / max(min(raw.exposure_time, rendered.exposure_time), 1e-9)
        if ratio <= 1.2:
            score += 4.0
    if raw.exposure_compensation is not None and rendered.exposure_compensation is not None:
        if abs(raw.exposure_compensation - rendered.exposure_compensation) <= 0.15:
            score += 4.0
    if raw.f_number and rendered.f_number and abs(raw.f_number - rendered.f_number) <= 0.2:
        score += 3.0
    return min(15.0, score)


def _geometry_similarity(raw_file: PhotoFile, rendered_file: PhotoFile) -> float:
    raw = raw_file.metadata
    rendered = rendered_file.metadata
    score = 0.0
    if raw.orientation and rendered.orientation and raw.orientation == rendered.orientation:
        score += 3.0
    if raw.width and raw.height and rendered.width and rendered.height:
        raw_ratio = raw.width / raw.height
        rendered_ratio = rendered.width / rendered.height
        if abs(raw_ratio - rendered_ratio) <= 0.03:
            score += 7.0
    return score


def _best_by_first(candidates: dict) -> dict[int, tuple[int, float]]:
    result: dict[int, tuple[int, float]] = {}
    for (raw_index, rendered_index), values in candidates.items():
        score = values[0]
        current = result.get(raw_index)
        if current is None or score > current[1]:
            result[raw_index] = (rendered_index, score)
    return result


def _best_by_second(candidates: dict) -> dict[int, tuple[int, float]]:
    result: dict[int, tuple[int, float]] = {}
    for (raw_index, rendered_index), values in candidates.items():
        score = values[0]
        current = result.get(rendered_index)
        if current is None or score > current[1]:
            result[rendered_index] = (raw_index, score)
    return result


def _runner_up_by_first(candidates: dict) -> dict[int, float]:
    """Second-best score per RAW, computed in one pass instead of a scan per pair."""
    best: dict[int, float] = {}
    second: dict[int, float] = {}
    for (raw_index, _rendered_index), values in candidates.items():
        score = values[0]
        if score > best.get(raw_index, float("-inf")):
            second[raw_index] = best.get(raw_index, 0.0)
            best[raw_index] = score
        elif score > second.get(raw_index, float("-inf")):
            second[raw_index] = score
    return {key: max(0.0, value) for key, value in second.items()}


def _runner_up_by_second(candidates: dict) -> dict[int, float]:
    best: dict[int, float] = {}
    second: dict[int, float] = {}
    for (_raw_index, rendered_index), values in candidates.items():
        score = values[0]
        if score > best.get(rendered_index, float("-inf")):
            second[rendered_index] = best.get(rendered_index, 0.0)
            best[rendered_index] = score
        elif score > second.get(rendered_index, float("-inf")):
            second[rendered_index] = score
    return {key: max(0.0, value) for key, value in second.items()}
