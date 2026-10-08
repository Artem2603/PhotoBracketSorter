"""Real format detection based on ExifTool, signatures, and extensions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.constants import RENDERED_EXTENSIONS, SUPPORTED_RAW_EXTENSIONS
from app.enums import DetectedType
from app.signatures import detect_by_signature


def detect_format(path: Path, exif_record: dict[str, Any] | None = None) -> tuple[DetectedType, str | None, str | None, str | None, str | None, str | None, list[str]]:
    record = exif_record or {}
    warnings: list[str] = []
    file_type = _as_string(record.get("FileType"))
    file_type_extension = _as_string(record.get("FileTypeExtension"))
    mime_type = _as_string(record.get("MIMEType"))
    major_brand = _as_string(record.get("MajorBrand"))
    compatible_brands = _as_string(record.get("CompatibleBrands"))

    signature_type, signature_mime, signature_warnings = detect_by_signature(path)
    warnings.extend(signature_warnings)

    exif_type = _type_from_exif(file_type, file_type_extension, mime_type, major_brand, compatible_brands)
    extension = path.suffix.lower()
    extension_type = _type_from_extension(extension)

    if signature_type is DetectedType.CORRUPTED:
        return signature_type, signature_mime, file_type, file_type_extension, major_brand, compatible_brands, warnings

    detected = exif_type or signature_type
    if detected is DetectedType.UNKNOWN and extension_type is not None:
        detected = extension_type
        warnings.append("Формат определён только по расширению.")
    elif exif_type is None and signature_type is DetectedType.TIFF and extension in SUPPORTED_RAW_EXTENSIONS:
        # ExifTool-fallback mode. NEF, ARW, CR2, ORF, RW2, PEF and most other
        # manufacturer RAWs are TIFF containers, so the signature alone reads them
        # as a rendered TIFF and they were filed under HEIC/. Without ExifTool the
        # extension is the only RAW evidence left, and it is far more reliable than
        # "any TIFF header is an export".
        detected = extension_type or DetectedType.RAW
        warnings.append("Формат определён только по расширению.")
    if exif_type and signature_type not in {DetectedType.UNKNOWN, exif_type}:
        if _compatible_family(exif_type, signature_type):
            detected = exif_type
        else:
            warnings.append(f"ExifTool и сигнатура расходятся: {exif_type.value} / {signature_type.value}.")
            detected = exif_type
    if detected is DetectedType.UNKNOWN:
        detected = DetectedType.UNSUPPORTED
    if detected is DetectedType.TIFF:
        detected, tiff_warnings = classify_tiff(record)
        warnings.extend(tiff_warnings)
    if mime_type is None:
        mime_type = signature_mime
    return detected, mime_type, file_type, file_type_extension, major_brand, compatible_brands, warnings


#: PhotometricInterpretation 32803 is Color Filter Array — undemosaiced sensor data.
CFA_PHOTOMETRIC = "32803"
#: 34892 is LinearRaw: demosaiced but still scene-referred RAW.
LINEAR_RAW_PHOTOMETRIC = "34892"


def classify_tiff(record: dict[str, Any]) -> tuple[DetectedType, list[str]]:
    """Separate a true RAW TIFF from an ordinary exported TIFF.

    A TIFF container holds both. Sensor data is identified positively — by CFA
    photometric interpretation, DNG structural tags, or more than 8 bits per
    sample with a single sample. Anything else is treated as a rendered export,
    which is the safer default: a misfiled export lands in HEIC/ rather than
    contaminating bracket detection as a phantom RAW frame.
    """
    warnings: list[str] = []
    photometric = _as_string(record.get("PhotometricInterpretation"))
    if photometric in {CFA_PHOTOMETRIC, LINEAR_RAW_PHOTOMETRIC}:
        return DetectedType.TIFF_RAW, warnings
    if record.get("DNGVersion") or record.get("DNGBackwardVersion"):
        return DetectedType.TIFF_RAW, warnings
    # Structural RAW tags describe a sensor area and never appear in an export.
    if record.get("ActiveArea") or record.get("DefaultCropSize") or record.get("RawImageFullSize"):
        return DetectedType.TIFF_RAW, warnings

    bits = _first_int(_as_string(record.get("BitsPerSample")))
    samples = _first_int(_as_string(record.get("SamplesPerPixel")))
    if bits is not None and bits > 8 and samples == 1:
        # One channel at high bit depth is a mosaic, not an RGB export.
        return DetectedType.TIFF_RAW, warnings

    if record.get("Software"):
        warnings.append("TIFF распознан как экспорт из редактора, а не как RAW.")
    return DetectedType.TIFF, warnings


def _first_int(value: str | None) -> int | None:
    """First integer of a possibly multi-valued tag such as ``"8 8 8"``."""
    if not value:
        return None
    for token in str(value).replace(",", " ").split():
        try:
            return int(float(token))
        except ValueError:
            continue
    return None


#: A plain TIFF belongs here: an export from Lightroom or Photoshop is a finished
#: image, not sensor data, and treating it as RAW sent every export into RAW/.
RENDERED_TYPES = frozenset(
    {
        DetectedType.JPEG,
        DetectedType.PNG,
        DetectedType.HEIC,
        DetectedType.HEIF,
        DetectedType.HIF,
        DetectedType.TIFF,
    }
)

RAW_TYPES = frozenset({DetectedType.DNG, DetectedType.RAW, DetectedType.TIFF_RAW})


def is_rendered_type(detected_type: DetectedType) -> bool:
    return detected_type in RENDERED_TYPES


def is_raw_type(detected_type: DetectedType) -> bool:
    return detected_type in RAW_TYPES


def _type_from_exif(
    file_type: str | None,
    file_type_extension: str | None,
    mime_type: str | None,
    major_brand: str | None,
    compatible_brands: str | None,
) -> DetectedType | None:
    tokens = " ".join(
        value.upper()
        for value in (file_type, file_type_extension, mime_type, major_brand, compatible_brands)
        if value
    )
    if not tokens:
        return None
    if "JPEG" in tokens or "JPG" in tokens:
        return DetectedType.JPEG
    if "PNG" in tokens:
        return DetectedType.PNG
    if "HEIC" in tokens:
        return DetectedType.HEIC
    if "HEIF" in tokens or "MIF1" in tokens:
        return DetectedType.HEIF
    if "DNG" in tokens:
        return DetectedType.DNG
    if "TIFF" in tokens or "TIF" in tokens:
        return DetectedType.TIFF
    for raw_name in ("ARW", "CR2", "CR3", "NEF", "ORF", "RAF", "RW2", "PEF", "RAW"):
        if raw_name in tokens:
            return DetectedType.RAW
    return None


def _type_from_extension(extension: str) -> DetectedType | None:
    if extension in {".jpg", ".jpeg", ".jpe"}:
        return DetectedType.JPEG
    if extension == ".png":
        return DetectedType.PNG
    if extension == ".heic":
        return DetectedType.HEIC
    if extension in {".heif", ".hif"}:
        return DetectedType.HEIF
    if extension == ".dng":
        return DetectedType.DNG
    if extension in {".tif", ".tiff"}:
        return DetectedType.TIFF
    if extension in SUPPORTED_RAW_EXTENSIONS:
        return DetectedType.RAW
    if extension in RENDERED_EXTENSIONS:
        return DetectedType.UNKNOWN
    return None


def _compatible_family(left: DetectedType, right: DetectedType) -> bool:
    # TIFF sits in both families here: the signature cannot tell a RAW TIFF from an
    # exported one, so a TIFF/RAW disagreement is not a real conflict.
    rendered = RENDERED_TYPES | {DetectedType.TIFF_RAW}
    raw = RAW_TYPES | {DetectedType.TIFF}
    return (left in rendered and right in rendered) or (left in raw and right in raw)


def _as_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
