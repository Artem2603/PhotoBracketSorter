"""Integrity helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def quick_fingerprint(path: Path, chunk_size: int = 64 * 1024) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(chunk_size))
        if stat.st_size > chunk_size:
            handle.seek(max(0, stat.st_size - chunk_size))
            digest.update(handle.read(chunk_size))
    return digest.hexdigest()


def safe_quick_fingerprint(path: Path, chunk_size: int = 64 * 1024) -> str | None:
    """Fingerprint that returns ``None`` instead of raising on unreadable files."""
    try:
        return quick_fingerprint(path, chunk_size)
    except OSError:
        return None


def verify_unchanged(
    path: Path,
    expected_size: int,
    expected_mtime_ns: int = 0,
    expected_fingerprint: str | None = None,
) -> str | None:
    """Check a source still matches what analysis recorded.

    Returns a human-readable reason when the file changed, or ``None`` when it is
    safe to proceed. Size alone is not enough: editing metadata in place commonly
    preserves the byte count, and Move deletes the original afterwards.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        return f"Файл недоступен: {exc}"
    if stat.st_size != expected_size:
        return f"Размер изменился после анализа: было {expected_size}, стало {stat.st_size}."
    if expected_mtime_ns and stat.st_mtime_ns != expected_mtime_ns:
        return "Время изменения файла отличается от зафиксированного при анализе."
    if expected_fingerprint:
        current = safe_quick_fingerprint(path)
        if current is None:
            return "Не удалось перечитать файл для проверки контрольной суммы."
        if current != expected_fingerprint:
            return "Содержимое файла изменилось после анализа."
    return None
