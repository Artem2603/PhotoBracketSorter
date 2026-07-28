"""Shared constants for Photo Bracket Sorter."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
PROFILES_DIR = PROJECT_ROOT / "profiles"
VENDOR_EXIFTOOL_DIR = PROJECT_ROOT / "vendor" / "exiftool"

SORTED_ROOT_PREFIX = "Sorted_Photos_"
SUPPORTED_RAW_EXTENSIONS = {
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
RENDERED_EXTENSIONS = {".heic", ".heif", ".hif", ".jpg", ".jpeg", ".jpe", ".png"}
POSSIBLE_IMAGE_EXTENSIONS = SUPPORTED_RAW_EXTENSIONS | RENDERED_EXTENSIONS | {
    ".tif",
    ".tiff",
}

MAIN_OUTPUT_DIRS = ("HEIC", "RAW", "BRACKETING_3", "BRACKETING_5", "BRACKETING_7", "BRACKETING_9")
REPORT_JSON_NAME = "summary.json"
REPORT_CSV_NAME = "report.csv"
REPORT_TEXT_NAME = "report.txt"
