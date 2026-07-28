"""Exposure resolution for pairing and bracket scoring.

Single-source rule
------------------
Exposure metadata comes from physically incompatible tags. ``ExposureCompensation``
is an offset from the meter, ``BaselineExposure`` is a DNG rendering hint,
``LightValue``/``BrightnessValue`` describe scene luminance, and ``EV100`` is
derived from the actual shutter/aperture/ISO triple. Their zero points differ and
``EV100``/``LightValue`` even run in the opposite direction to ``ExposureCompensation``.

Mixing them inside one group produces meaningless spans and step estimates, so a
group resolves to exactly **one** source, and only if that source is present on
**every** file of the group. Otherwise the next source in ``SOURCE_PRIORITY`` is
tried.

Degenerate sources
------------------
Many cameras write ``ExposureCompensation=0`` to every frame of a real bracket and
express the bracket through shutter speed alone. A source whose values are all
identical carries no bracket information, so it is skipped in favour of the next
source that does vary. If no source varies, the degenerate one is still returned
(with ``degenerate=True``) so callers report an honest zero span rather than a
fabricated one.

Sign convention
---------------
All resolved values are normalised to "larger means brighter exposure", matching
``ExposureCompensation``. ``EV100`` and ``LightValue`` are negated because a higher
raw value there means *less* light reaches the sensor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from app.models import PhotoFile, PhotoMetadata


class ExposureSource(str, Enum):
    EXPOSURE_COMPENSATION = "ExposureCompensation"
    EV100 = "EV100"
    BASELINE_EXPOSURE = "BaselineExposure"
    LIGHT_VALUE = "LightValue"
    BRIGHTNESS_VALUE = "BrightnessValue"
    UNKNOWN = "UNKNOWN"


#: Resolution order. ``ExposureCompensation`` states camera intent and wins when it
#: actually varies; ``EV100`` is the physical ground truth and takes over whenever
#: the compensation tag is flat or missing.
SOURCE_PRIORITY: tuple[ExposureSource, ...] = (
    ExposureSource.EXPOSURE_COMPENSATION,
    ExposureSource.EV100,
    ExposureSource.BASELINE_EXPOSURE,
    ExposureSource.LIGHT_VALUE,
    ExposureSource.BRIGHTNESS_VALUE,
)

#: Values closer than this are treated as identical when testing for a flat source.
DEGENERATE_SPAN_EV = 0.05


@dataclass(frozen=True, slots=True)
class GroupExposure:
    """Exposure of one candidate group, resolved from a single source."""

    values: tuple[float, ...]
    source: ExposureSource
    complete: bool
    degenerate: bool
    span: float | None
    step_ev: float | None
    uniform: bool
    skipped_sources: tuple[tuple[ExposureSource, str], ...] = field(default=())

    @property
    def usable(self) -> bool:
        """True when the group carries real, comparable exposure variation."""
        return self.complete and not self.degenerate


def source_value(metadata: PhotoMetadata, source: ExposureSource) -> float | None:
    """Read one specific source, normalised to "larger means brighter"."""
    if source is ExposureSource.EXPOSURE_COMPENSATION:
        return _float_or_none(metadata.exposure_compensation)
    if source is ExposureSource.BASELINE_EXPOSURE:
        return _float_or_none(metadata.baseline_exposure)
    if source is ExposureSource.BRIGHTNESS_VALUE:
        return _float_or_none(metadata.brightness_value)
    if source is ExposureSource.LIGHT_VALUE:
        value = _float_or_none(metadata.light_value)
        return None if value is None else -value
    if source is ExposureSource.EV100:
        value = ev100(metadata)
        return None if value is None else -value
    return None


def ev100(metadata: PhotoMetadata) -> float | None:
    """Exposure value referred to ISO 100, from aperture, shutter, and ISO.

    Returned in the native EV100 convention where a *higher* value means less
    light. :func:`source_value` negates it for group comparison.
    """
    if metadata.f_number is None or metadata.exposure_time is None:
        return None
    if metadata.f_number <= 0 or metadata.exposure_time <= 0:
        return None
    iso = metadata.iso if metadata.iso and metadata.iso > 0 else 100.0
    try:
        aperture_part = math.log2((metadata.f_number * metadata.f_number) / metadata.exposure_time)
        iso_part = math.log2(iso / 100.0)
        return aperture_part - iso_part
    except (ValueError, OverflowError):
        return None


def resolve_group_exposure(
    files: list[PhotoFile],
    tolerance: float = 0.45,
    priority: tuple[ExposureSource, ...] | None = None,
) -> GroupExposure:
    """Resolve one exposure source for the whole group.

    Walks ``priority`` (:data:`SOURCE_PRIORITY` by default) and returns the first
    source that is present on every file and shows real variation. Sources that
    are incomplete or flat are recorded in ``skipped_sources`` for diagnostics.

    ``priority`` lets a camera profile reorder the search — e.g. a camera whose
    ``BaselineExposure`` is reliable and whose ``ExposureCompensation`` is noisy
    can be told to prefer the former. The physical incompatibility across sources
    still holds regardless of order: exactly one source is used per group, never
    a mix.
    """
    if not files:
        return GroupExposure((), ExposureSource.UNKNOWN, False, False, None, None, False)

    search_order = priority or SOURCE_PRIORITY
    skipped: list[tuple[ExposureSource, str]] = []
    degenerate_fallback: GroupExposure | None = None

    for source in search_order:
        values = [source_value(file.metadata, source) for file in files]
        present = [value for value in values if value is not None]
        if len(present) != len(files):
            if present:
                skipped.append((source, f"present on {len(present)}/{len(files)} files"))
            continue
        span = max(present) - min(present)
        if span <= DEGENERATE_SPAN_EV:
            skipped.append((source, f"flat across the group (span {span:.2f} EV)"))
            if degenerate_fallback is None:
                uniform, step = roughly_uniform_steps(present, tolerance)
                degenerate_fallback = GroupExposure(
                    values=tuple(present),
                    source=source,
                    complete=True,
                    degenerate=True,
                    span=span,
                    step_ev=step,
                    uniform=uniform,
                    skipped_sources=(),
                )
            continue
        uniform, step = roughly_uniform_steps(present, tolerance)
        return GroupExposure(
            values=tuple(present),
            source=source,
            complete=True,
            degenerate=False,
            span=span,
            step_ev=step,
            uniform=uniform,
            skipped_sources=tuple(skipped),
        )

    if degenerate_fallback is not None:
        return GroupExposure(
            values=degenerate_fallback.values,
            source=degenerate_fallback.source,
            complete=True,
            degenerate=True,
            span=degenerate_fallback.span,
            step_ev=degenerate_fallback.step_ev,
            uniform=degenerate_fallback.uniform,
            skipped_sources=tuple(skipped),
        )
    return GroupExposure((), ExposureSource.UNKNOWN, False, False, None, None, False, tuple(skipped))


def exposure_value(metadata: PhotoMetadata) -> tuple[float | None, str]:
    """Single-file exposure, using the first available source by priority.

    Only meaningful for display. Never compare results across files — use
    :func:`resolve_group_exposure`, which guarantees one shared source.
    """
    for source in SOURCE_PRIORITY:
        value = source_value(metadata, source)
        if value is not None:
            return value, source.value
    return None, ExposureSource.UNKNOWN.value


def exposure_values(files: list[PhotoFile]) -> tuple[list[float], str]:
    """Backwards-compatible wrapper over :func:`resolve_group_exposure`."""
    group = resolve_group_exposure(files)
    if not group.complete:
        return list(group.values), "PARTIAL"
    return list(group.values), group.source.value


def roughly_uniform_steps(values: list[float] | tuple[float, ...], tolerance: float) -> tuple[bool, float | None]:
    if len(values) < 3:
        return False, None
    ordered = sorted(values)
    steps = [ordered[index + 1] - ordered[index] for index in range(len(ordered) - 1)]
    mean_step = sum(steps) / len(steps)
    if mean_step <= 0:
        return False, None
    max_deviation = max(abs(step - mean_step) for step in steps)
    return max_deviation <= tolerance, mean_step


def priority_from_names(names: tuple[str, ...] | list[str]) -> tuple[ExposureSource, ...]:
    """Parse settings/profile strings into a search order.

    Unknown names are dropped rather than raising, and any source missing from
    ``names`` is appended in its default-priority position, so a partially
    specified or slightly stale list still produces a complete, usable order.
    """
    by_value = {source.value: source for source in ExposureSource if source is not ExposureSource.UNKNOWN}
    ordered: list[ExposureSource] = []
    for name in names:
        source = by_value.get(name)
        if source is not None and source not in ordered:
            ordered.append(source)
    for source in SOURCE_PRIORITY:
        if source not in ordered:
            ordered.append(source)
    return tuple(ordered)


def _float_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result
