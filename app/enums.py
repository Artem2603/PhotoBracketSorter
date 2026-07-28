"""Typed enums used by the core and GUI."""

from enum import Enum


class DetectedType(str, Enum):
    JPEG = "JPEG"
    PNG = "PNG"
    HEIC = "HEIC"
    HEIF = "HEIF"
    HIF = "HIF"
    DNG = "DNG"
    RAW = "RAW"
    #: A TIFF holding undemosaiced sensor data (CFA photometric, DNG-ish structure).
    TIFF_RAW = "TIFF_RAW"
    #: An ordinary TIFF exported from an editor. A rendered image, not a RAW.
    TIFF = "TIFF"
    UNSUPPORTED = "UNSUPPORTED"
    CORRUPTED = "CORRUPTED"
    UNKNOWN = "UNKNOWN"


class DestinationCategory(str, Enum):
    HEIC = "HEIC"
    RAW = "RAW"
    BRACKETING_3 = "BRACKETING_3"
    BRACKETING_5 = "BRACKETING_5"
    #: Opt-in (settings.enable_hdr7/enable_hdr9): real, validated series, same
    #: rigor as BRACKETING_3/5 — never assigned unless the detector accepts them.
    BRACKETING_7 = "BRACKETING_7"
    BRACKETING_9 = "BRACKETING_9"
    SKIP = "SKIP"
    REVIEW = "REVIEW"


class GroupClassification(str, Enum):
    """Informational labels layered on top of ``DestinationCategory``.

    These never move a file anywhere by themselves — ``destination_category``
    still governs physical placement. A label here only explains *what kind of
    group* the classifier thinks a file belongs to, surfaced through the review
    queue and the decision explainer, so the user can act on it (confirm as a
    real series, exclude, or ignore) through the existing manual controls
    instead of the app silently deciding for them.
    """

    HDR3 = "HDR3"
    HDR5 = "HDR5"
    HDR7 = "HDR7"
    HDR9 = "HDR9"
    INCOMPLETE_BRACKET = "INCOMPLETE_BRACKET"
    FOCUS_BRACKET = "FOCUS_BRACKET"
    BURST = "BURST"
    PROBABLE_PANORAMA = "PROBABLE_PANORAMA"
    RAW_JPEG_PAIR = "RAW_JPEG_PAIR"
    RAW_HEIC_PAIR = "RAW_HEIC_PAIR"
    SINGLE_RAW = "SINGLE_RAW"
    SINGLE_RENDERED = "SINGLE_RENDERED"
    UNKNOWN_GROUP = "UNKNOWN_GROUP"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNCLASSIFIED = "UNCLASSIFIED"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    REVIEW = "REVIEW"

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceLevel":
        if score >= 82:
            return cls.HIGH
        if score >= 65:
            return cls.MEDIUM
        if score >= 45:
            return cls.LOW
        return cls.REVIEW


class CaptureTimeSource(str, Enum):
    SUBSEC_DATE_TIME_ORIGINAL = "SUBSEC_DATE_TIME_ORIGINAL"
    DATE_TIME_ORIGINAL = "DATE_TIME_ORIGINAL"
    SUBSEC_CREATE_DATE = "SUBSEC_CREATE_DATE"
    CREATE_DATE = "CREATE_DATE"
    MEDIA_CREATE_DATE = "MEDIA_CREATE_DATE"
    TRACK_CREATE_DATE = "TRACK_CREATE_DATE"
    MODIFY_DATE = "MODIFY_DATE"
    FILE_NAME = "FILE_NAME"
    FILE_MTIME = "FILE_MTIME"
    UNKNOWN = "UNKNOWN"


class TimezoneStatus(str, Enum):
    OFFSET_PRESENT = "OFFSET_PRESENT"
    UTC_MARKED = "UTC_MARKED"
    LOCAL_UNKNOWN = "LOCAL_UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OperationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    DELETE_FAILED = "DELETE_FAILED"


class DetectionMode(str, Enum):
    STRICT = "STRICT"
    NORMAL = "NORMAL"
    SOFT = "SOFT"


class BracketSizeMode(str, Enum):
    AUTO = "AUTO"
    THREE = "THREE"
    FIVE = "FIVE"
    BOTH = "BOTH"
