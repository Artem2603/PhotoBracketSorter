"""File signature detection helpers."""

from __future__ import annotations

from pathlib import Path

from app.enums import DetectedType

HEIF_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
}


def read_header(path: Path, length: int = 256) -> bytes:
    with path.open("rb") as handle:
        return handle.read(length)


def detect_by_signature(path: Path) -> tuple[DetectedType, str | None, list[str]]:
    warnings: list[str] = []
    try:
        size = path.stat().st_size
    except OSError as exc:
        return DetectedType.CORRUPTED, None, [f"Не удалось прочитать файл: {exc}"]
    if size == 0:
        return DetectedType.CORRUPTED, None, ["Нулевой файл."]
    try:
        header = read_header(path)
    except OSError as exc:
        return DetectedType.CORRUPTED, None, [f"Не удалось прочитать сигнатуру: {exc}"]

    if header.startswith(b"\xff\xd8\xff"):
        return DetectedType.JPEG, "image/jpeg", warnings
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedType.PNG, "image/png", warnings
    if header.startswith(b"II*\x00") or header.startswith(b"MM\x00*"):
        extension = path.suffix.lower()
        if extension == ".dng":
            return DetectedType.DNG, "image/x-adobe-dng", warnings
        return DetectedType.TIFF, "image/tiff", warnings
    if header.startswith(b"II+\x00") or header.startswith(b"MM\x00+"):
        return DetectedType.TIFF, "image/tiff", warnings
    bmff_type = _detect_bmff(header)
    if bmff_type is not None:
        return bmff_type, "image/heif", warnings
    return DetectedType.UNKNOWN, None, warnings


def _detect_bmff(header: bytes) -> DetectedType | None:
    if len(header) < 16:
        return None
    if header[4:8] != b"ftyp":
        return None
    major_brand = header[8:12]
    compatible = {header[i : i + 4] for i in range(16, min(len(header), 80), 4)}
    brands = {major_brand} | compatible
    if brands & HEIF_BRANDS:
        if b"heic" in brands or b"heix" in brands or b"hevc" in brands or b"hevx" in brands:
            return DetectedType.HEIC
        return DetectedType.HEIF
    return None
