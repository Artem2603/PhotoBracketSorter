"""Bundled ExifTool integration with explicit fallback support."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.constants import VENDOR_EXIFTOOL_DIR
from app.errors import ExifToolError, ExifToolTimeoutError

EXIF_TAGS = (
    "SourceFile",
    "FileName",
    "Directory",
    "FileSize#",
    "FileType",
    "FileTypeExtension",
    "MIMEType",
    "MajorBrand",
    "CompatibleBrands",
    "ImageWidth",
    "ImageHeight",
    "ExifImageWidth",
    "ExifImageHeight",
    "Orientation",
    "Rotation",
    "Make",
    "Model",
    "Software",
    "LensModel",
    "LensID",
    "FocalLength#",
    "FocalLengthIn35mmFormat#",
    "FocusDistance#",
    "DateTimeOriginal",
    "SubSecDateTimeOriginal",
    "SubSecTimeOriginal",
    "OffsetTimeOriginal",
    "CreateDate",
    "SubSecCreateDate",
    "SubSecTimeDigitized",
    "OffsetTimeDigitized",
    "MediaCreateDate",
    "TrackCreateDate",
    "ModifyDate",
    "ISO#",
    "RecommendedExposureIndex#",
    "ExposureTime#",
    "ShutterSpeedValue#",
    "FNumber#",
    "ApertureValue#",
    "ExposureCompensation#",
    "ExposureProgram",
    "BrightnessValue#",
    "LightValue#",
    "BaselineExposure#",
    "WhiteBalance",
    "ColorTemperature#",
    "GPSLatitude#",
    "GPSLongitude#",
    "DNGVersion",
    "DNGBackwardVersion",
    "PhotometricInterpretation",
    "Compression",
    "BitsPerSample",
    "SamplesPerPixel",
    "RawImageFullSize",
    "ActiveArea",
    "DefaultCropSize",
    "PreviewImage",
    "JpgFromRaw",
    "ThumbnailImage",
)


class ExifToolRunner:
    def __init__(self, executable: Path | None = None, timeout_seconds: int = 60, batch_size: int = 80):
        # An explicit path from Settings wins; discovery is the fallback.
        self.executable = executable or find_exiftool()
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, int(batch_size))

    @property
    def available(self) -> bool:
        return self.executable is not None and self.executable.exists() and exiftool_files_dir(self.executable).exists()

    def version(self) -> str | None:
        if not self.available or self.executable is None:
            return None
        try:
            completed = subprocess.run(
                [str(self.executable), "-ver"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def read_many(self, paths: list[Path], chunk_size: int | None = None) -> dict[Path, dict[str, Any]]:
        """Read tags for every path, in batches of ``chunk_size`` (the exiftool_batch_size setting)."""
        if not self.available or self.executable is None:
            return {}
        chunk_size = max(1, int(chunk_size or self.batch_size))
        records: dict[Path, dict[str, Any]] = {}
        for index in range(0, len(paths), chunk_size):
            chunk = paths[index : index + chunk_size]
            for record in self._read_chunk(chunk):
                source = record.get("SourceFile")
                if source:
                    records[Path(str(source)).resolve()] = record
        return records

    def _read_chunk(self, paths: list[Path]) -> list[dict[str, Any]]:
        if self.executable is None:
            return []
        command = [
            str(self.executable),
            "-json",
            "-n",
            "-api",
            "LargeFileSupport=1",
            "-charset",
            "filename=UTF8",
        ]
        command.extend(f"-{tag}" for tag in EXIF_TAGS)
        command.extend(str(path) for path in paths)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExifToolTimeoutError(technical_reason=str(exc)) from exc
        if completed.returncode not in {0, 1}:
            raise ExifToolError(technical_reason=completed.stderr.strip())
        try:
            data = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise ExifToolError(technical_reason=f"Некорректный JSON ExifTool: {exc}") from exc
        if not isinstance(data, list):
            raise ExifToolError(technical_reason="ExifTool вернул не список записей.")
        return [record for record in data if isinstance(record, dict)]

    def extract_binary_tag(self, path: Path, tag: str) -> bytes | None:
        """Extract one binary tag (e.g. ``PreviewImage``) as raw bytes.

        Uses ``-b`` (binary output mode), so the subprocess call must NOT go
        through the ``text=True`` path the JSON reader uses — that would corrupt
        embedded JPEG bytes trying to decode them as text. Returns ``None`` on
        any failure or if the tag is simply absent; this is a best-effort preview
        aid, never something callers should have to handle as an error.
        """
        if not self.available or self.executable is None:
            return None
        command = [str(self.executable), "-b", f"-{tag}", str(path)]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=self.timeout_seconds, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode not in (0, 1) or not completed.stdout:
            return None
        return completed.stdout

    def extract_embedded_preview(self, path: Path) -> tuple[bytes | None, str]:
        """Try the common embedded-JPEG tags in size order. Returns (data, tag_used)."""
        for tag in ("PreviewImage", "JpgFromRaw", "ThumbnailImage"):
            data = self.extract_binary_tag(path, tag)
            if data:
                return data, tag
        return None, "unavailable"


def find_exiftool() -> Path | None:
    candidates: list[Path] = []
    for base in _runtime_bases():
        candidates.extend(
            [
                base / "vendor" / "exiftool" / "exiftool.exe",
                base / "vendor" / "exiftool" / "exiftool(-k).exe",
                base / "_internal" / "vendor" / "exiftool" / "exiftool.exe",
                base / "_internal" / "vendor" / "exiftool" / "exiftool(-k).exe",
            ]
        )
    candidates.extend([VENDOR_EXIFTOOL_DIR / "exiftool.exe", VENDOR_EXIFTOOL_DIR / "exiftool(-k).exe"])
    for candidate in candidates:
        if candidate.exists() and exiftool_files_dir(candidate).exists():
            return candidate
    found = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if found and Path(found).exists():
        return Path(found)
    return None


def exiftool_files_dir(executable: Path) -> Path:
    return executable.parent / "exiftool_files"


def _runtime_bases() -> list[Path]:
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(str(meipass)).resolve())
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    bases.append(Path.cwd().resolve())
    bases.append(Path(__file__).resolve().parents[1])
    unique: list[Path] = []
    for base in bases:
        if base not in unique:
            unique.append(base)
    return unique
