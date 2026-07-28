"""Destination name collision handling."""

from __future__ import annotations

from pathlib import Path


def unique_destination(base_path: Path, reserved: set[Path]) -> Path:
    normalized = base_path
    if normalized not in reserved and not normalized.exists():
        reserved.add(normalized)
        return normalized
    stem = base_path.stem
    suffix = base_path.suffix
    parent = base_path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index:02d}{suffix}"
        if candidate not in reserved and not candidate.exists():
            reserved.add(candidate)
            return candidate
        index += 1
