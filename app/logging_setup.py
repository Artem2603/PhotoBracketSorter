"""Logging configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path


def configure_logging(log_path: Path | None = None, level: str = "INFO") -> None:
    if logging.getLogger().handlers:
        configure_logging_level(level)
        return
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=_resolve_level(level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def configure_logging_level(level: str) -> None:
    """Apply the configured log level to the root logger.

    Called whenever an analysis starts so a level changed in Settings takes effect
    without restarting the application.
    """
    logging.getLogger().setLevel(_resolve_level(level))


def _resolve_level(level: str) -> int:
    resolved = logging.getLevelNamesMapping().get(str(level).upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def default_log_path() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "PhotoBracketSorter" / "PhotoBracketSorter.log"
    return Path.home() / "AppData" / "Local" / "PhotoBracketSorter" / "PhotoBracketSorter.log"
