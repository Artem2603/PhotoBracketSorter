"""Best-effort preview loading for the GUI."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

RAW_PREVIEW_EXTENSIONS = {".3fr", ".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".raw", ".rw2"}


@dataclass(slots=True)
class PreviewInfo:
    path: Path
    available: bool
    message: str
    width: int | None = None
    height: int | None = None
    image: Any | None = None


class PreviewCache:
    def __init__(self, max_items: int = 128):
        self.max_items = max(1, int(max_items))
        self._items: OrderedDict[tuple[str, int], PreviewInfo] = OrderedDict()

    def get(self, path: Path, max_size: int = 360) -> PreviewInfo:
        key = (str(path.resolve()), max_size)
        cached = self._items.get(key)
        if cached is not None:
            self._items.move_to_end(key)
            return cached
        info = load_preview_info(path, max_size=max_size)
        self._items[key] = info
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return info

    def clear(self) -> None:
        self._items.clear()

    @property
    def size(self) -> int:
        return len(self._items)


def load_preview_info(path: Path, max_size: int = 360) -> PreviewInfo:
    suffix = path.suffix.lower()
    if suffix in RAW_PREVIEW_EXTENSIONS:
        return PreviewInfo(path=path, available=False, message="RAW/DNG preview placeholder; embedded preview extraction is not available in this synthetic check.")
    if Image is None:
        return PreviewInfo(path=path, available=False, message="Pillow is unavailable.")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.thumbnail((max_size, max_size))
            return PreviewInfo(path=path, available=True, message="", width=width, height=height, image=image.copy())
    except Exception as exc:
        return PreviewInfo(path=path, available=False, message=f"Preview unavailable: {exc}")


def load_tk_preview(path: Path, max_size: int = 360) -> tuple[Any | None, str | None]:
    if ImageTk is None:
        return None, "Pillow ImageTk is unavailable."
    info = load_preview_info(path, max_size=max_size)
    if not info.available or info.image is None:
        return None, info.message
    try:
        return ImageTk.PhotoImage(info.image), None
    except Exception as exc:
        return None, f"Preview unavailable: {exc}"
