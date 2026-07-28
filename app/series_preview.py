"""Visual series comparison: embedded RAW previews, histograms, clipping flags.

Previous versions showed a fixed "RAW preview placeholder" text for every RAW
file — no image, ever. Most RAW formats (DNG, CR2/CR3, NEF, ARW, ORF, RAF,
RW2, ...) embed a full-size or near-full-size JPEG under ``PreviewImage``,
``JpgFromRaw``, or ``ThumbnailImage``; this module extracts and decodes that
embedded JPEG through the same bundled ExifTool the rest of the app already
uses (:mod:`app.exiftool_runner`), so the series viewer (feature 5) has a real
image to show, zoom, and compare — not a placeholder.

Every function here is pure with respect to its inputs (an ExifTool subprocess
call is the only I/O) and never raises: a frame that cannot be decoded gets an
``error`` message instead of an image, so one bad file cannot take down the
whole comparison view.
"""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

from app.exiftool_runner import ExifToolRunner
from app.exposure import exposure_value
from app.format_detection import is_raw_type
from app.models import PhotoFile
from app.time_compare import sort_photo_files_by_capture_time

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is a pinned runtime dependency
    Image = None

#: Fraction of pixels at the extreme end of the histogram that counts as clipping.
CLIPPING_FRACTION_THRESHOLD = 0.01
#: Bins near the extremes; a few bins wide so mild JPEG banding near 0/255
#: does not read as a false positive from a single saturated bin.
_HIGHLIGHT_BINS = range(250, 256)
_SHADOW_BINS = range(0, 6)

#: Default longest-edge size for a comparison-view preview. Large enough to
#: inspect detail after a synchronized zoom, small enough to decode and hold
#: five of them in memory without delay.
DEFAULT_PREVIEW_SIZE = 640


@dataclasses.dataclass(frozen=True, slots=True)
class FrameExif:
    file_name: str
    exposure_time: float | None
    f_number: float | None
    iso: float | None
    exposure_compensation: float | None
    ev_display: float | None
    ev_source: str


@dataclasses.dataclass(frozen=True, slots=True)
class FramePreview:
    file: PhotoFile
    exif: FrameExif
    image: "Image.Image | None"
    source: str
    width: int | None
    height: int | None
    histogram_rgb: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]] | None
    highlight_clipped: bool
    shadow_clipped: bool
    error: str | None

    @property
    def available(self) -> bool:
        return self.image is not None


def extract_preview_image(
    path: Path,
    exiftool: ExifToolRunner,
    is_raw: bool,
    max_size: int = DEFAULT_PREVIEW_SIZE,
) -> tuple["Image.Image | None", str, str | None]:
    """Return ``(image, source_label, error)``. Never raises.

    ``source_label`` is one of ``PreviewImage``/``JpgFromRaw``/``ThumbnailImage``
    (which embedded tag supplied the image), ``direct`` (a rendered file opened
    directly), or ``unavailable``.
    """
    if Image is None:
        return None, "unavailable", "Pillow is unavailable."
    if is_raw:
        if not exiftool.available:
            return None, "unavailable", "ExifTool is unavailable; cannot extract an embedded preview."
        data, tag = exiftool.extract_embedded_preview(path)
        if data is None:
            return None, "unavailable", "This RAW file has no embedded preview ExifTool can extract."
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
            image.thumbnail((max_size, max_size))
            return image.convert("RGB"), tag, None
        except Exception as exc:
            return None, "unavailable", f"The embedded preview could not be decoded: {exc}"
    try:
        with Image.open(path) as opened:
            opened.load()
            image = opened.copy()
        image.thumbnail((max_size, max_size))
        return image.convert("RGB"), "direct", None
    except Exception as exc:
        return None, "unavailable", f"Preview unavailable: {exc}"


def compute_histogram(image: "Image.Image") -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """256-bin count per RGB channel. ``image`` must already be RGB."""
    raw = image.histogram()
    return tuple(raw[0:256]), tuple(raw[256:512]), tuple(raw[512:768])


def detect_clipping(
    histogram: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]],
    total_pixels: int,
    threshold: float = CLIPPING_FRACTION_THRESHOLD,
) -> tuple[bool, bool]:
    """(highlight_clipped, shadow_clipped). Any channel saturating counts."""
    if total_pixels <= 0:
        return False, False
    highlight_count = max(sum(channel[bin_] for bin_ in _HIGHLIGHT_BINS) for channel in histogram)
    shadow_count = max(sum(channel[bin_] for bin_ in _SHADOW_BINS) for channel in histogram)
    return (highlight_count / total_pixels) >= threshold, (shadow_count / total_pixels) >= threshold


def build_frame_preview(file: PhotoFile, exiftool: ExifToolRunner, max_size: int = DEFAULT_PREVIEW_SIZE) -> FramePreview:
    metadata = file.metadata
    ev, ev_source = exposure_value(metadata)
    exif = FrameExif(
        file_name=metadata.file_name,
        exposure_time=metadata.exposure_time,
        f_number=metadata.f_number,
        iso=metadata.iso,
        exposure_compensation=metadata.exposure_compensation,
        ev_display=ev,
        ev_source=ev_source,
    )
    image, source, error = extract_preview_image(metadata.source_path, exiftool, is_raw_type(metadata.detected_type), max_size)
    if image is None:
        return FramePreview(
            file=file,
            exif=exif,
            image=None,
            source=source,
            width=None,
            height=None,
            histogram_rgb=None,
            highlight_clipped=False,
            shadow_clipped=False,
            error=error,
        )
    histogram = compute_histogram(image)
    total_pixels = image.width * image.height
    highlight_clipped, shadow_clipped = detect_clipping(histogram, total_pixels)
    return FramePreview(
        file=file,
        exif=exif,
        image=image,
        source=source,
        width=image.width,
        height=image.height,
        histogram_rgb=histogram,
        highlight_clipped=highlight_clipped,
        shadow_clipped=shadow_clipped,
        error=None,
    )


def build_series_previews(
    files: list[PhotoFile],
    exiftool: ExifToolRunner | None = None,
    max_size: int = DEFAULT_PREVIEW_SIZE,
) -> list[FramePreview]:
    """One :class:`FramePreview` per frame, ordered by capture time.

    Never raises: a frame whose preview cannot be extracted or decoded gets an
    ``error`` message instead of an image, so one bad file in a series cannot
    block the comparison view for the rest.
    """
    exiftool = exiftool or ExifToolRunner()
    ordered = sort_photo_files_by_capture_time(list(files))
    return [build_frame_preview(file, exiftool, max_size) for file in ordered]
