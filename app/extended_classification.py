"""Extended classification: HDR7/9 labels, incomplete brackets, focus-bracketing,
burst, probable panorama (feature 8, 1.2.0).

This module never decides where a file is physically routed — that remains
``PhotoFile.destination_category``, owned by ``SeriesManager`` and the existing
``BracketDetector``. It only fills in ``PhotoFile.group_classification``, an
informational label surfaced through the review queue and the decision
explainer. A "probable panorama" is exactly that: probable. Nothing here moves
a file anywhere on the strength of a panorama or burst guess — the caution the
1.2.0 spec asks for is structural, not a threshold tuned to be extra strict.

Each detector below is independently disable-able
(``enable_focus_bracket_detection`` etc.); a disabled detector's files simply
keep the ``UNCLASSIFIED`` label rather than being forced into a wrong bucket.

Detection pipeline for one still-unassigned run of same-camera, time-adjacent
RAW frames (already time-clustered by :func:`cluster_unassigned_raw_files`):

1. Real exposure variance, wrong size for a supported bracket -> INCOMPLETE_BRACKET.
2. Exposure ~flat, focus distance clearly changing -> FOCUS_BRACKET (confirmed
   by requiring the frames to still look related, not just close in time).
3. Exposure ~flat, focus ~flat, frames near-duplicate -> BURST.
4. Exposure ~flat, focus ~flat, frames moderately similar (not duplicates, not
   unrelated), enough frames -> PROBABLE_PANORAMA.
5. Anything else that does not fit -> UNKNOWN_GROUP.

Visual similarity (steps 2-4) is the only expensive check (it decodes an
embedded RAW preview per frame via ExifTool, reusing
:func:`app.series_preview.extract_preview_image` from feature 5) and only runs
on these already-small, already-time-clustered candidate groups — never over
the whole file population.
"""

from __future__ import annotations

import dataclasses

from app.enums import GroupClassification
from app.exiftool_runner import ExifToolRunner
from app.exposure import priority_from_names, resolve_group_exposure
from app.format_detection import is_raw_type, is_rendered_type
from app.models import AppSettings, PhotoFile, RawHeicPair
from app.perceptual_hash import dhash_from_image, hash_similarity
from app.time_compare import safe_file_time_difference_seconds, sort_photo_files_by_capture_time

#: A cluster whose size is exactly one short of a supported bracket size is the
#: clearest "incomplete" signal — the user was very likely one frame away from
#: a real bracket (missed shutter release, one frame failed to write, etc.).
_NEAR_MISS_OFFSETS = (-1,)


@dataclasses.dataclass(frozen=True, slots=True)
class GroupResult:
    files: tuple[PhotoFile, ...]
    classification: GroupClassification
    evidence: tuple[str, ...]


def classify_extended_groups(
    raw_files: list[PhotoFile],
    settings: AppSettings,
    exiftool: ExifToolRunner | None = None,
) -> list[GroupResult]:
    """Label every RAW file's ``group_classification``. Mutates ``PhotoFile.
    group_classification``/``group_classification_evidence`` in place (the
    same "core model is the state" convention the rest of the app follows);
    returns the per-cluster results for callers that want the detail (tests,
    the review queue).
    """
    results: list[GroupResult] = []
    for file in raw_files:
        if file.series_id:
            size = _series_size_from_classification_hint(file)
            if size is not None:
                _apply(file, _HDR_LABELS.get(size, GroupClassification.UNCLASSIFIED), ())
            continue

    clusters = cluster_unassigned_raw_files(raw_files, settings)
    exiftool = exiftool or ExifToolRunner()
    for cluster in clusters:
        result = _classify_cluster(cluster, settings, exiftool)
        results.append(result)
        for file in cluster:
            _apply(file, result.classification, result.evidence)

    for file in raw_files:
        if file.series_id is None and file.group_classification == GroupClassification.UNCLASSIFIED.value:
            _apply(file, GroupClassification.SINGLE_RAW, ())
    return results


def apply_pair_and_review_labels(files: list[PhotoFile], pairs: list[RawHeicPair]) -> None:
    """Overlay pairing and review labels. Called after :func:`classify_extended_groups`
    so an HDR/focus-bracket/incomplete label (evidence about the RAW's OWN group)
    is never silently replaced by a pairing label — pairing is recorded as
    additional evidence, not a takeover, except when the file has no group
    label at all yet (a single RAW/rendered file)."""
    from app.enums import DestinationCategory

    paired_raw_ids = set()
    for pair in pairs:
        raw, rendered = pair.raw_file, pair.rendered_file
        paired_raw_ids.add(id(raw))
        label = GroupClassification.RAW_HEIC_PAIR if rendered.metadata.detected_type.value in {"HEIC", "HEIF", "HIF"} else GroupClassification.RAW_JPEG_PAIR
        note = f"paired with {rendered.metadata.file_name}"
        if raw.group_classification in {GroupClassification.SINGLE_RAW.value, GroupClassification.UNCLASSIFIED.value}:
            _apply(raw, label, (note,))
        if rendered.group_classification == GroupClassification.UNCLASSIFIED.value:
            _apply(rendered, label, (f"paired with {raw.metadata.file_name}",))

    for file in files:
        if file.destination_category is DestinationCategory.REVIEW:
            _apply(file, GroupClassification.NEEDS_REVIEW, tuple(file.decision_reasons))
        elif file.group_classification == GroupClassification.UNCLASSIFIED.value and is_rendered_type(file.metadata.detected_type) and id(file) not in paired_raw_ids:
            _apply(file, GroupClassification.SINGLE_RENDERED, ())
        elif file.group_classification == GroupClassification.UNCLASSIFIED.value and not is_raw_type(file.metadata.detected_type) and not is_rendered_type(file.metadata.detected_type):
            _apply(file, GroupClassification.UNKNOWN_GROUP, ())


_HDR_LABELS = {
    3: GroupClassification.HDR3,
    5: GroupClassification.HDR5,
    7: GroupClassification.HDR7,
    9: GroupClassification.HDR9,
}


def _series_size_from_classification_hint(file: PhotoFile) -> int | None:
    from app.enums import DestinationCategory

    by_category = {
        DestinationCategory.BRACKETING_3: 3,
        DestinationCategory.BRACKETING_5: 5,
        DestinationCategory.BRACKETING_7: 7,
        DestinationCategory.BRACKETING_9: 9,
    }
    return by_category.get(file.destination_category)


def _apply(file: PhotoFile, classification: GroupClassification, evidence: tuple[str, ...]) -> None:
    file.group_classification = classification.value
    file.group_classification_evidence = list(evidence)


# ------------------------------------------------------------------ clustering


def cluster_unassigned_raw_files(raw_files: list[PhotoFile], settings: AppSettings) -> list[list[PhotoFile]]:
    """Group RAW files not already in a bracket series into time-adjacent,
    same-camera runs of 2+ frames. Singles (no neighbor within the adjacent-gap
    threshold) are left out — they get SINGLE_RAW directly."""
    unassigned = [file for file in raw_files if not file.series_id]
    ordered = sort_photo_files_by_capture_time(unassigned)
    clusters: list[list[PhotoFile]] = []
    current: list[PhotoFile] = []
    for file in ordered:
        if not current:
            current = [file]
            continue
        if _same_camera(current[-1], file) and _within_gap(current[-1], file, settings.max_bracket_adjacent_delta_seconds):
            current.append(file)
        else:
            clusters.append(current)
            current = [file]
    if current:
        clusters.append(current)
    return [cluster for cluster in clusters if len(cluster) >= 2]


def _same_camera(left: PhotoFile, right: PhotoFile) -> bool:
    left_key = (left.metadata.make or "").lower(), (left.metadata.model or "").lower()
    right_key = (right.metadata.make or "").lower(), (right.metadata.model or "").lower()
    if not any(left_key) or not any(right_key):
        return True  # no camera metadata on either side: don't let this be the reason to split
    return left_key == right_key


def _within_gap(left: PhotoFile, right: PhotoFile, max_gap: float) -> bool:
    gap = safe_file_time_difference_seconds(left, right).absolute_seconds
    return gap is not None and gap <= max_gap


# -------------------------------------------------------------- classification


def _classify_cluster(cluster: list[PhotoFile], settings: AppSettings, exiftool: ExifToolRunner) -> GroupResult:
    files = tuple(cluster)
    group = resolve_group_exposure(list(cluster), settings.exposure_step_tolerance, priority_from_names(settings.exposure_source_priority))

    if group.usable and group.span is not None and group.span >= settings.min_exposure_span_ev:
        near_miss_size = _near_miss_bracket_size(len(cluster), settings)
        if settings.enable_incomplete_bracket_detection and near_miss_size is not None:
            evidence = (f"{len(cluster)} кадров с разбросом экспозиции {group.span:.2f} EV — на 1 кадр меньше валидной серии из {near_miss_size}.",)
            return GroupResult(files, GroupClassification.INCOMPLETE_BRACKET, evidence)
        return GroupResult(files, GroupClassification.UNKNOWN_GROUP, (f"exposure varies ({group.span:.2f} EV) but size {len(cluster)} matches no supported bracket",))

    # Exposure is flat (or unusable) from here on: focus-bracket, burst, or panorama territory.
    focus_spread = _focus_distance_spread_ratio(cluster)
    if settings.enable_focus_bracket_detection and focus_spread is not None and focus_spread >= settings.focus_bracket_min_focus_spread_ratio:
        similarity = _average_adjacent_similarity(cluster, exiftool)
        if similarity is None or similarity >= settings.focus_bracket_visual_similarity_min:
            distances = [file.metadata.focus_distance for file in cluster if file.metadata.focus_distance]
            evidence = (f"дистанция фокусировки {min(distances):.2f} -> {max(distances):.2f} м на {len(cluster)} кадрах, экспозиция почти не меняется.",)
            return GroupResult(files, GroupClassification.FOCUS_BRACKET, evidence)

    if not (settings.enable_burst_detection or settings.enable_panorama_detection):
        return GroupResult(files, GroupClassification.UNKNOWN_GROUP, ())

    similarity = _average_adjacent_similarity(cluster, exiftool)
    if similarity is None:
        return GroupResult(files, GroupClassification.UNKNOWN_GROUP, ("visual similarity unavailable",))

    if settings.enable_burst_detection and similarity >= settings.burst_visual_similarity_threshold:
        return GroupResult(files, GroupClassification.BURST, (f"кадры почти идентичны (сходство {similarity:.2f}).",))

    if (
        settings.enable_panorama_detection
        and len(cluster) >= settings.panorama_min_frames
        and settings.panorama_visual_similarity_min <= similarity <= settings.panorama_visual_similarity_max
    ):
        evidence = (f"{len(cluster)} кадров подряд, умеренное визуальное сходство ({similarity:.2f}) — возможна панорама.",)
        return GroupResult(files, GroupClassification.PROBABLE_PANORAMA, evidence)

    return GroupResult(files, GroupClassification.UNKNOWN_GROUP, (f"no pattern matched (similarity={similarity:.2f})",))


def _near_miss_bracket_size(cluster_size: int, settings: AppSettings) -> int | None:
    supported = [3, 5]
    if settings.enable_hdr7:
        supported.append(7)
    if settings.enable_hdr9:
        supported.append(9)
    for size in supported:
        if cluster_size in (size + offset for offset in _NEAR_MISS_OFFSETS):
            return size
    return None


def _focus_distance_spread_ratio(cluster: list[PhotoFile]) -> float | None:
    distances = [file.metadata.focus_distance for file in cluster if file.metadata.focus_distance and file.metadata.focus_distance > 0]
    if len(distances) < 2:
        return None
    return max(distances) / min(distances)


#: Frames sampled per cluster for the visual check. Adjacent-frame similarity
#: over an evenly spaced sample is enough to tell a burst from a panorama;
#: hashing all 60 frames of a long burst only costs time.
VISUAL_SAMPLE_LIMIT = 6
#: Longest edge used for perceptual hashing; dhash downsamples to 9x8 anyway.
_HASH_PREVIEW_SIZE = 256


def _sample_evenly(cluster: list[PhotoFile], limit: int = VISUAL_SAMPLE_LIMIT) -> list[PhotoFile]:
    if len(cluster) <= limit:
        return list(cluster)
    step = (len(cluster) - 1) / (limit - 1)
    return [cluster[round(index * step)] for index in range(limit)]


def _average_adjacent_similarity(cluster: list[PhotoFile], exiftool: ExifToolRunner) -> float | None:
    """Mean dhash similarity of neighbouring frames in an evenly spaced sample.

    All RAW previews of the sample are extracted with a single ExifTool call
    (previously up to three processes per frame, for every frame of every
    burst, during analysis).
    """
    import io

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a pinned runtime dependency
        return None

    sample = _sample_evenly(cluster)
    raw_paths = [file.metadata.source_path for file in sample if is_raw_type(file.metadata.detected_type)]
    embedded = exiftool.extract_embedded_previews(raw_paths) if raw_paths else {}
    hashes: list[int | None] = []
    for file in sample:
        path = file.metadata.source_path
        image = None
        try:
            if is_raw_type(file.metadata.detected_type):
                payload = embedded.get(path.resolve())
                if payload is not None:
                    image = Image.open(io.BytesIO(payload[0]))
                    image.load()
            else:
                with Image.open(path) as opened:
                    opened.load()
                    image = opened.copy()
            if image is not None:
                image.thumbnail((_HASH_PREVIEW_SIZE, _HASH_PREVIEW_SIZE))
        except Exception:
            image = None
        hashes.append(dhash_from_image(image) if image is not None else None)
    similarities = [hash_similarity(hashes[index], hashes[index + 1]) for index in range(len(hashes) - 1)]
    valid = [value for value in similarities if value is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)
