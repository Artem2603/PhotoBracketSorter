"""Manual override helpers used by the GUI."""

from __future__ import annotations

from pathlib import Path

from app.enums import ConfidenceLevel, DestinationCategory
from app.models import PhotoFile


def assign_category(file: PhotoFile, category: DestinationCategory) -> None:
    file.manual_override = category
    file.destination_category = category
    file.confidence_level = ConfidenceLevel.HIGH
    file.decision_reasons.append("MANUAL_OVERRIDE")
    file.selected = category not in {DestinationCategory.SKIP, DestinationCategory.REVIEW}
    if category is DestinationCategory.HEIC:
        file.destination_subpath = Path("HEIC") / file.metadata.file_name
    elif category is DestinationCategory.RAW:
        file.destination_subpath = Path("RAW") / file.metadata.file_name
    elif category in {DestinationCategory.SKIP, DestinationCategory.REVIEW}:
        file.destination_subpath = None


def restore_auto(file: PhotoFile) -> None:
    file.manual_override = None
    file.decision_reasons.append("MANUAL_RESTORE_REQUESTED")
