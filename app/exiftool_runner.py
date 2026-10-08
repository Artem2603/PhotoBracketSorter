"""Bundled ExifTool integration with explicit fallback support."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
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


#: Embedded-JPEG tags, largest first.
PREVIEW_TAGS = ("PreviewImage", "JpgFromRaw", "ThumbnailImage")


class ExifToolRunner:
    def __init__(self, executable: Path | None = None, timeout_seconds: int = 60, batch_size: int = 80):
        # An explicit path from Settings wins; discovery is the fallback.
        self.executable = executable or find_exiftool()
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, int(batch_size))
        self._probe_result: bool | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "ExifToolRunner":
        """Honour ``exiftool_path`` (when it points at a real file) and ``exiftool_batch_size``."""
        raw = str(getattr(settings, "exiftool_path", "") or "").strip()
        executable = Path(raw).expanduser() if raw else None
        if executable is not None and not executable.is_file():
            executable = None
        batch_size = getattr(settings, "exiftool_batch_size", 80) or 80
        return cls(executable, batch_size=int(batch_size))

    @property
    def available(self) -> bool:
        if self.executable is None or not self.executable.exists():
            return False
        if exiftool_files_dir(self.executable).exists():
            return True
        # Not the bundled Windows layout (a system exiftool on PATH, or an
        # explicit path from Settings): probe it once instead of assuming it
        # is broken. Previously such executables were silently ignored.
        if self._probe_result is None:
            self._probe_result = self._run_version() is not None
        return self._probe_result

    def version(self) -> str | None:
        if not self.available or self.executable is None:
            return None
        return self._run_version()

    def _run_version(self) -> str | None:
        if self.executable is None:
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

    def _chunk_timeout(self, count: int) -> float:
        """A fixed 60 s used to cover a 1000-file batch on a slow USB disk; scale with the batch."""
        return max(float(self.timeout_seconds), 10.0 + 2.0 * count)

    @staticmethod
    @contextlib.contextmanager
    def _argfile(paths: list[Path]) -> Iterator[str]:
        """Hand file names to ExifTool through a UTF-8 argfile (``-@``).

        On Windows the Perl launcher receives command-line arguments in the
        ANSI code page, so a path like ``C:\\Users\\Артём\\DCIM`` arrives
        mangled and ExifTool answers "File not found" for every file in it,
        silently dropping all metadata. ExifTool's own documentation prescribes
        ``-charset filename=UTF8`` plus a UTF-8 argfile for exactly this case;
        it also sidesteps the 32 767-character command-line limit for big
        batches of long paths.
        """
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".args", prefix="pbs-exiftool-", delete=False)
        try:
            with handle:
                for path in paths:
                    handle.write(str(path) + "\n")
            yield handle.name
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    def _read_chunk(self, paths: list[Path]) -> list[dict[str, Any]]:
        if self.executable is None or not paths:
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
        try:
            with self._argfile(paths) as argfile:
                completed = subprocess.run(
                    [*command, "-@", argfile],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._chunk_timeout(len(paths)),
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ExifToolTimeoutError(technical_reason=str(exc)) from exc
        except OSError as exc:
            # A blocked or quarantined exiftool.exe (antivirus, missing
            # permissions) surfaces here; keep it inside the app's error
            # hierarchy so the caller can fall back instead of crashing.
            raise ExifToolError(technical_reason=f"{type(exc).__name__}: {exc}") from exc
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
        command = [str(self.executable), "-b", "-charset", "filename=UTF8", f"-{tag}"]
        try:
            with self._argfile([path]) as argfile:
                completed = subprocess.run([*command, "-@", argfile], capture_output=True, timeout=self.timeout_seconds, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode not in (0, 1) or not completed.stdout:
            return None
        return completed.stdout

    def extract_embedded_previews(self, paths: list[Path]) -> dict[Path, tuple[bytes, str]]:
        """Largest embedded JPEG for many files in ONE ExifTool call.

        Returns ``{resolved_path: (jpeg_bytes, tag_used)}`` and omits files
        without an embedded preview. ``-json -b`` makes ExifTool emit binary
        tags as ``base64:…`` strings, so one process serves a whole burst
        instead of up to three processes per frame. Never raises.
        """
        if not self.available or self.executable is None or not paths:
            return {}
        command = [str(self.executable), "-json", "-b", "-api", "LargeFileSupport=1", "-charset", "filename=UTF8"]
        command.extend(f"-{tag}" for tag in PREVIEW_TAGS)
        try:
            with self._argfile(paths) as argfile:
                completed = subprocess.run([*command, "-@", argfile], capture_output=True, timeout=self._chunk_timeout(len(paths)), check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if completed.returncode not in (0, 1) or not completed.stdout:
            return {}
        try:
            data = json.loads(completed.stdout.decode("utf-8", errors="replace") or "[]")
        except ValueError:
            return {}
        results: dict[Path, tuple[bytes, str]] = {}
        if not isinstance(data, list):
            return results
        for record in data:
            if not isinstance(record, dict) or not record.get("SourceFile"):
                continue
            for tag in PREVIEW_TAGS:
                value = record.get(tag)
                if isinstance(value, str) and value.startswith("base64:"):
                    try:
                        payload = base64.b64decode(value[7:], validate=False)
                    except (ValueError, TypeError):
                        continue
                    if payload:
                        results[Path(str(record["SourceFile"])).resolve()] = (payload, tag)
                        break
        return results

    def extract_embedded_preview(self, path: Path) -> tuple[bytes | None, str]:
        """Try the common embedded-JPEG tags in size order. Returns (data, tag_used)."""
        for tag in PREVIEW_TAGS:
            data = self.extract_binary_tag(path, tag)
            if data:
                return data, tag
        return None, "unavailable"


def find_exiftool() -> Path | None:
    # The bundled exiftool.exe is a Windows binary; on other platforms the
    # system exiftool (if any) is the only thing that can run.
    if os.name != "nt":
        found = shutil.which("exiftool")
        return Path(found) if found else None
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
