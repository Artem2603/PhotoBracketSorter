"""Perceptual hashing used for optional visual pair confirmation.

Backs the ``use_preview_for_pairing``, ``preview_similarity_enabled``, and
``visual_similarity_threshold`` settings.

Uses dHash: the image is reduced to a 9x8 greyscale grid and each pixel is compared
with its right-hand neighbour, producing 64 bits that describe gradient direction
rather than absolute brightness. That makes it stable across the exposure and
white-balance differences between a RAW file and its rendered companion, which is
exactly the comparison being made here.

Formats Pillow cannot decode (most camera RAW) yield ``None``. Callers must treat
``None`` as "unknown", never as "different" — refusing to pair a RAW with its own
HEIC just because the RAW could not be decoded would be worse than not checking.
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only without Pillow
    Image = None

#: Grid width; one bit is emitted per adjacent horizontal pair, so bits = HASH_SIZE * (WIDTH-1).
HASH_HEIGHT = 8
HASH_WIDTH = 9
HASH_BITS = HASH_HEIGHT * (HASH_WIDTH - 1)

#: Greyscale modes deeper than 8 bits (16-bit TIFF/PNG open this way). Pillow's
#: ``convert("L")`` clips them to white instead of rescaling, so an 8-bit and a
#: 16-bit copy of the same picture hashed differently.
_DEEP_GREYSCALE_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N"})


def dhash(path: Path) -> int | None:
    """Compute a 64-bit difference hash, or ``None`` if the file cannot be decoded."""
    if Image is None:
        return None
    try:
        with Image.open(path) as image:
            return dhash_from_image(image)
    except Exception:
        return None


def dhash_from_image(image) -> int | None:
    """Same hash as :func:`dhash`, from an already-decoded image.

    Used for RAW frames, whose embedded preview (extracted by
    :mod:`app.series_preview` through ExifTool) is decoded once and reused —
    Pillow cannot open most RAW files directly, so :func:`dhash` alone would
    always return ``None`` for them.
    """
    if Image is None or image is None:
        return None
    try:
        reduced = _to_greyscale(image).resize((HASH_WIDTH, HASH_HEIGHT), Image.Resampling.LANCZOS)
        pixels = list(reduced.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(HASH_HEIGHT):
        offset = row * HASH_WIDTH
        for column in range(HASH_WIDTH - 1):
            bits <<= 1
            if pixels[offset + column] > pixels[offset + column + 1]:
                bits |= 1
    return bits


def _to_greyscale(image):
    """``image.convert("L")`` that scales deep greyscale data instead of clipping it."""
    if image.mode not in _DEEP_GREYSCALE_MODES:
        return image.convert("L")
    deep = image if image.mode == "I" else image.convert("I")
    _low, high = deep.getextrema()
    if high <= 255:
        # 8-bit data that merely sits in a wide container.
        return deep.convert("L")
    scale = 1.0 / 256.0 if high <= 65535 else 255.0 / high
    # On mode "I" Pillow accepts only an affine function here and runs it in C.
    return deep.point(lambda value: value * scale).convert("L")


def hash_similarity(left: int | None, right: int | None) -> float | None:
    """Fraction of matching bits in ``[0.0, 1.0]``, or ``None`` if either hash is missing."""
    if left is None or right is None:
        return None
    distance = bin(left ^ right).count("1")
    return 1.0 - (distance / HASH_BITS)


class PerceptualHashCache:
    """Memoises hashes per path so a file is decoded at most once per run."""

    def __init__(self) -> None:
        self._hashes: dict[Path, int | None] = {}

    def get(self, path: Path) -> int | None:
        if path not in self._hashes:
            self._hashes[path] = dhash(path)
        return self._hashes[path]

    def similarity(self, left: Path, right: Path) -> float | None:
        return hash_similarity(self.get(left), self.get(right))

    @property
    def size(self) -> int:
        return len(self._hashes)
