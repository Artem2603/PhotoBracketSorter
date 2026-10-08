"""Path validation and output-root helpers."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from app.constants import SORTED_ROOT_PREFIX
from app.errors import ConfigurationError, MTPPathError, UnsupportedPathError


def validate_source(source_root: Path) -> Path:
    source = source_root.expanduser().resolve()
    if not source.exists():
        raise ConfigurationError("Исходная папка не существует.", str(source))
    if not source.is_dir():
        raise ConfigurationError("Исходный путь должен быть папкой.", str(source))
    _reject_mtp(source)
    return source


def validate_output_parent(output_parent: Path, source_root: Path) -> Path:
    output = output_parent.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise ConfigurationError("Папка результата должна быть папкой.", str(output))
    if output == source_root:
        raise ConfigurationError("Папка результата совпадает с исходной.")
    if _is_relative_to(output, source_root):
        raise ConfigurationError("Папка результата находится внутри исходной.")
    _reject_mtp(output)
    output.mkdir(parents=True, exist_ok=True)
    return output


#: Characters Windows forbids in a path component.
_INVALID_NAME_CHARS = '<>:"/\\|?*'

DEFAULT_OUTPUT_TEMPLATE = f"{SORTED_ROOT_PREFIX}{{date}}_{{time}}"


def render_output_folder_name(template: str, now: datetime | None = None) -> str:
    """Expand an output_folder_template into a safe single folder name.

    Supported placeholders: ``{date}``, ``{time}``, ``{datetime}``, ``{year}``,
    ``{month}``, ``{day}``. An unknown placeholder or an empty result falls back to
    the default template rather than producing an unusable path.
    """
    moment = now or datetime.now()
    values = {
        "date": moment.strftime("%Y-%m-%d"),
        "time": moment.strftime("%H-%M-%S"),
        "datetime": moment.strftime("%Y-%m-%d_%H-%M-%S"),
        "year": moment.strftime("%Y"),
        "month": moment.strftime("%m"),
        "day": moment.strftime("%d"),
    }
    try:
        rendered = (template or DEFAULT_OUTPUT_TEMPLATE).format(**values)
    except Exception:
        # KeyError/IndexError/ValueError for unknown or malformed placeholders,
        # AttributeError for things like ``{date.year}``: all fall back.
        rendered = DEFAULT_OUTPUT_TEMPLATE.format(**values)
    rendered = sanitize_component(rendered)
    return rendered or DEFAULT_OUTPUT_TEMPLATE.format(**values)


#: Device names Windows refuses as file or folder names, with or without an extension.
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(name: str) -> str:
    """Strip characters that cannot appear in a Windows path component."""
    cleaned = "".join("_" if char in _INVALID_NAME_CHARS else char for char in name)
    cleaned = "".join(char for char in cleaned if ord(char) >= 32)
    cleaned = cleaned.strip(" .")
    stem = cleaned.split(".", 1)[0]
    if stem.upper() in _RESERVED_NAMES:
        cleaned = f"{stem}_" + cleaned[len(stem):]
    return cleaned


def make_unique_output_root(
    output_parent: Path,
    now: datetime | None = None,
    template: str = DEFAULT_OUTPUT_TEMPLATE,
) -> Path:
    name = render_output_folder_name(template, now)
    base = output_parent / name
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = output_parent / f"{name}_{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


def _reject_mtp(path: Path) -> None:
    text = str(path)
    # Shell namespace paths ("::{GUID}\\...") and mtp:// URIs are virtual device
    # views. A folder that merely contains the letters "mtp" (C:\\Photos\\MTP_backup)
    # is an ordinary folder and used to be rejected by a substring check.
    if text.startswith("::") or text.lower().startswith(("mtp:", "mtp//")):
        raise MTPPathError("Виртуальный MTP-путь не поддерживается.", text)
    # A drive letter is a Windows concept; on POSIX every absolute path lacks
    # one, so the check only applies where it means something. This lets the
    # test suite and the core run on Linux/macOS development machines.
    if os.name == "nt" and not path.drive and path.is_absolute():
        raise UnsupportedPathError("Путь без Windows-диска не поддерживается.", text)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
