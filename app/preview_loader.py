"""Best-effort preview loading for the GUI.

Rendered files (JPEG/PNG, and HEIC/HEIF when the optional ``pillow-heif``
plugin is installed) are decoded with Pillow. RAW/DNG files, and any HEIC
Pillow cannot open, fall back to the embedded JPEG that ExifTool can extract
(``PreviewImage``/``JpgFromRaw``/``ThumbnailImage``) — the same path the
series viewer uses — so the main preview pane shows a real image instead of a
placeholder. Every entry point returns a :class:`PreviewInfo`; nothing here
raises on a bad file.
"""

from __future__ import annotations

import io
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.exiftool_runner import ExifToolRunner

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:  # Optional: HEIC/HEIF decoding for Pillow.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_DECODER_AVAILABLE = True
except Exception:
    HEIF_DECODER_AVAILABLE = False

RAW_PREVIEW_EXTENSIONS = {
    ".3fr",
    ".arw",
    ".cr2",
    ".cr3",
    ".dcr",
    ".dng",
    ".erf",
    ".fff",
    ".iiq",
    ".kdc",
    ".mef",
    ".mos",
    ".mrw",
    ".nef",
    ".orf",
    ".pef",
    ".raf",
    ".raw",
    ".rw2",
    ".rwl",
    ".sr2",
    ".srf",
    ".x3f",
}
HEIF_EXTENSIONS = {".heic", ".heif", ".hif"}

_default_runner: ExifToolRunner | None = None


@dataclass(slots=True)
class PreviewInfo:
    path: Path
    available: bool
    message: str
    width: int | None = None
    height: int | None = None
    image: Any | None = None
    #: Where the pixels came from: ``direct`` (Pillow opened the file), one of
    #: the embedded-preview tag names, or ``unavailable``.
    source: str = "unavailable"


class PreviewCache:
    def __init__(self, max_items: int = 128):
        self.max_items = max(1, int(max_items))
        self._items: OrderedDict[tuple[str, int], PreviewInfo] = OrderedDict()

    @staticmethod
    def _key(path: Path, max_size: int) -> tuple[str, int]:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        return (resolved, max_size)

    def peek(self, path: Path, max_size: int = 360) -> PreviewInfo | None:
        """Return a cached entry without loading anything."""
        key = self._key(path, max_size)
        cached = self._items.get(key)
        if cached is not None:
            self._items.move_to_end(key)
        return cached

    def put(self, info: PreviewInfo, max_size: int = 360) -> None:
        key = self._key(info.path, max_size)
        self._items[key] = info
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def get(self, path: Path, max_size: int = 360, exiftool: ExifToolRunner | None = None) -> PreviewInfo:
        cached = self.peek(path, max_size)
        if cached is not None:
            return cached
        info = load_preview_info(path, max_size=max_size, exiftool=exiftool)
        self.put(info, max_size)
        return info

    def clear(self) -> None:
        self._items.clear()

    @property
    def size(self) -> int:
        return len(self._items)


def load_preview_info(path: Path, max_size: int = 360, exiftool: ExifToolRunner | None = None) -> PreviewInfo:
    """Decode a thumbnail for ``path``; never raises."""
    if Image is None:
        return PreviewInfo(path=path, available=False, message="Pillow is unavailable.")
    suffix = path.suffix.lower()
    if suffix in RAW_PREVIEW_EXTENSIONS:
        return _embedded_preview(path, max_size, exiftool, label="RAW/DNG")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.thumbnail((max_size, max_size))
            return PreviewInfo(path=path, available=True, message="", width=width, height=height, image=image.copy(), source="direct")
    except Exception as exc:
        if suffix in HEIF_EXTENSIONS:
            # No HEIF decoder: an embedded thumbnail is better than nothing.
            embedded = _embedded_preview(path, max_size, exiftool, label="HEIC")
            if embedded.available:
                return embedded
            hint = "" if HEIF_DECODER_AVAILABLE else " Install pillow-heif for full HEIC previews."
            return PreviewInfo(path=path, available=False, message=f"HEIC preview unavailable: {exc}.{hint}")
        return PreviewInfo(path=path, available=False, message=f"Preview unavailable: {exc}")


def _embedded_preview(path: Path, max_size: int, exiftool: ExifToolRunner | None, label: str) -> PreviewInfo:
    runner = exiftool or _shared_runner()
    if not runner.available:
        return PreviewInfo(path=path, available=False, message=f"{label} preview placeholder: ExifTool is unavailable, so the embedded preview cannot be extracted.")
    data, tag = runner.extract_embedded_preview(path)
    if not data:
        return PreviewInfo(path=path, available=False, message=f"{label} preview placeholder: this file has no embedded preview ExifTool can extract.")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        width, height = image.size
        image.thumbnail((max_size, max_size))
        return PreviewInfo(path=path, available=True, message="", width=width, height=height, image=image.convert("RGB"), source=tag)
    except Exception as exc:
        return PreviewInfo(path=path, available=False, message=f"{label} preview placeholder: the embedded preview could not be decoded: {exc}")


def _shared_runner() -> ExifToolRunner:
    global _default_runner
    if _default_runner is None:
        _default_runner = ExifToolRunner()
    return _default_runner


def load_tk_preview(path: Path, max_size: int = 360, exiftool: ExifToolRunner | None = None) -> tuple[Any | None, str | None]:
    if ImageTk is None:
        return None, "Pillow ImageTk is unavailable."
    info = load_preview_info(path, max_size=max_size, exiftool=exiftool)
    if not info.available or info.image is None:
        return None, info.message
    try:
        return ImageTk.PhotoImage(info.image), None
    except Exception as exc:
        return None, f"Preview unavailable: {exc}"
