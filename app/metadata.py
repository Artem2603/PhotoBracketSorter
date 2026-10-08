"""Metadata loading with ExifTool and local fallbacks."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from app.capture_time import extract_capture_time
from app.enums import DetectedType
from app.errors import ExifToolError, ExifToolTimeoutError
from app.exiftool_runner import ExifToolRunner
from app.format_detection import detect_format
from app.integrity import safe_quick_fingerprint
from app.models import AppSettings, PhotoMetadata

try:
    from PIL import Image
except ImportError:
    Image = None

logger = logging.getLogger(__name__)


class MetadataReader:
    def __init__(self, exiftool: ExifToolRunner | None = None, worker_count: int = 1, batch_size: int = 80):
        self.exiftool = exiftool or ExifToolRunner()
        self.worker_count = max(1, int(worker_count))
        self.batch_size = max(1, int(batch_size))

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "MetadataReader":
        """Build a reader honouring exiftool_path, exiftool_batch_size, and worker_count."""
        return cls(
            exiftool=ExifToolRunner.from_settings(settings),
            worker_count=settings.worker_count,
            batch_size=settings.exiftool_batch_size,
        )

    def read_many(
        self,
        paths: list[Path],
        source_root: Path,
        cancel_event: threading.Event,
        progress: Callable[[int, int], None] | None = None,
        settings: AppSettings | None = None,
        exif_progress: Callable[[int, int], None] | None = None,
    ) -> list[PhotoMetadata]:
        """Read metadata for every path.

        ``exif_progress`` reports the ExifTool stage (files whose EXIF has been
        read so far), which on a large folder is where most of the time goes;
        ``progress`` reports the per-file stage that follows. Cancellation is
        honoured between ExifTool batches, and a batch ExifTool cannot read
        degrades those files to the fallback path (with a warning on each)
        instead of aborting the whole analysis.
        """
        worker_count = max(1, settings.worker_count) if settings else self.worker_count
        batch_size = max(1, settings.exiftool_batch_size) if settings else self.batch_size
        exif_available = self.exiftool.available
        total = len(paths)
        exif_records: dict[Path, dict[str, Any]] = {}
        exif_failures: dict[Path, str] = {}
        if exif_available:
            for start in range(0, total, batch_size):
                if cancel_event.is_set():
                    break
                chunk = paths[start : start + batch_size]
                try:
                    exif_records.update(self.exiftool.read_many(chunk, batch_size))
                except (ExifToolError, ExifToolTimeoutError) as exc:
                    logger.warning("ExifTool failed for a batch of %d files: %s", len(chunk), exc)
                    for path in chunk:
                        exif_failures[path.resolve()] = exc.user_message
                if exif_progress:
                    exif_progress(min(start + len(chunk), total), total)
        results: list[PhotoMetadata | None] = [None] * total

        def read_index(index: int) -> None:
            path = paths[index]
            resolved = path.resolve()
            record = exif_records.get(resolved, {})
            item = self._read_one(path, source_root, record)
            if not exif_available:
                item.warnings.insert(0, "ExifTool недоступен; включён аварийный режим с пониженной точностью.")
            elif resolved in exif_failures:
                item.warnings.insert(0, f"ExifTool не прочитал файл ({exif_failures[resolved]}); использован аварийный режим.")
            results[index] = item

        if worker_count == 1:
            for index in range(total):
                if cancel_event.is_set():
                    break
                read_index(index)
                if progress:
                    progress(index + 1, total)
        else:
            # Per-file work is I/O bound (header sniff, stat, fingerprint, Pillow),
            # so threads genuinely overlap. Results stay in submission order.
            done = 0
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = {pool.submit(read_index, index): index for index in range(total)}
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    if progress:
                        progress(done, total)
                    if cancel_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
        return [item for item in results if item is not None]

    def _read_one(self, path: Path, source_root: Path, record: dict[str, Any]) -> PhotoMetadata:
        warnings: list[str] = []
        errors: list[str] = []
        detected_type, mime_type, file_type, file_type_extension, major_brand, compatible_brands, format_warnings = detect_format(path, record)
        warnings.extend(format_warnings)
        try:
            stat = path.stat()
            file_size = stat.st_size
            mtime_ns = stat.st_mtime_ns
        except OSError as exc:
            file_size = 0
            mtime_ns = 0
            errors.append(str(exc))
        # Captured here, in the analysis worker, so Copy/Move can prove the file did
        # not change since. Doing it at plan time would block the UI thread.
        fingerprint = safe_quick_fingerprint(path) if file_size else None
        capture_time, capture_time_raw, capture_source, timezone_status, time_warnings = extract_capture_time(record, path)
        warnings.extend(time_warnings)
        width = _first_number(record, ("ImageWidth", "ExifImageWidth"))
        height = _first_number(record, ("ImageHeight", "ExifImageHeight"))
        if (width is None or height is None) and detected_type not in {DetectedType.DNG, DetectedType.RAW, DetectedType.CORRUPTED}:
            pillow_width, pillow_height, pillow_error = _read_dimensions_with_pillow(path)
            width = width or pillow_width
            height = height or pillow_height
            if pillow_error:
                warnings.append(pillow_error)
        gps = None
        gps_lat = _number(record.get("GPSLatitude"))
        gps_lon = _number(record.get("GPSLongitude"))
        if gps_lat is not None and gps_lon is not None:
            gps = {"latitude": gps_lat, "longitude": gps_lon}
        relative_path = _safe_relative(path, source_root)
        preview_flags = {
            "PreviewImage": bool(record.get("PreviewImage")),
            "JpgFromRaw": bool(record.get("JpgFromRaw")),
            "ThumbnailImage": bool(record.get("ThumbnailImage")),
        }
        return PhotoMetadata(
            source_path=path,
            relative_path=relative_path,
            file_name=path.name,
            extension=path.suffix.lower(),
            detected_type=detected_type,
            mime_type=mime_type,
            file_type=file_type,
            file_type_extension=file_type_extension,
            major_brand=major_brand,
            compatible_brands=compatible_brands,
            file_size=file_size,
            mtime_ns=mtime_ns,
            quick_fingerprint=fingerprint,
            width=_int_or_none(width),
            height=_int_or_none(height),
            orientation=_text(record.get("Orientation")),
            rotation=_int_or_none(_number(record.get("Rotation"))),
            make=_text(record.get("Make")),
            model=_text(record.get("Model")),
            software=_text(record.get("Software")),
            lens_model=_text(record.get("LensModel")),
            lens_id=_text(record.get("LensID")),
            focal_length=_number(record.get("FocalLength")),
            focal_length_35mm=_number(record.get("FocalLengthIn35mmFormat")),
            focus_distance=_number(record.get("FocusDistance")),
            capture_time=capture_time,
            capture_time_raw=capture_time_raw,
            capture_time_source=capture_source,
            timezone_status=timezone_status,
            iso=_number(record.get("ISO") or record.get("RecommendedExposureIndex")),
            exposure_time=_number(record.get("ExposureTime")),
            shutter_speed_value=_number(record.get("ShutterSpeedValue")),
            f_number=_number(record.get("FNumber")),
            aperture_value=_number(record.get("ApertureValue")),
            exposure_compensation=_number(record.get("ExposureCompensation")),
            exposure_program=_text(record.get("ExposureProgram")),
            brightness_value=_number(record.get("BrightnessValue")),
            light_value=_number(record.get("LightValue")),
            baseline_exposure=_number(record.get("BaselineExposure")),
            white_balance=_text(record.get("WhiteBalance")),
            color_temperature=_number(record.get("ColorTemperature")),
            gps=gps,
            dng_version=_text(record.get("DNGVersion")),
            photometric_interpretation=_text(record.get("PhotometricInterpretation")),
            compression=_text(record.get("Compression")),
            bits_per_sample=_text(record.get("BitsPerSample")),
            samples_per_pixel=_text(record.get("SamplesPerPixel")),
            raw_dimensions=_text(record.get("RawImageFullSize")),
            active_area=_text(record.get("ActiveArea")),
            default_crop_size=_text(record.get("DefaultCropSize")),
            preview_flags=preview_flags,
            warnings=warnings,
            metadata_errors=errors,
            raw_exif_record=dict(record),
        )


def _read_dimensions_with_pillow(path: Path) -> tuple[int | None, int | None, str | None]:
    if Image is None:
        return None, None, "Pillow недоступен; размер изображения не прочитан."
    try:
        with Image.open(path) as image:
            width, height = image.size
            return int(width), int(height), None
    except Exception as exc:
        return None, None, f"Pillow не открыл файл: {exc}"


def _first_number(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(record.get(key))
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if "/" in text:
        parts = text.split("/", 1)
        try:
            denominator = float(parts[1])
            if denominator == 0:
                return None
            return float(parts[0]) / denominator
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(round(number))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_relative(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return Path(path.name)
