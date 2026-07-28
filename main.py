"""Application entry point."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from app.logging_setup import configure_logging, default_log_path
from app.version import __version__


def main(argv: list[str] | None = None) -> int:
    configure_logging(default_log_path())
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print(__version__)
        return 0
    if "--self-test" in args:
        from app.analysis import analyze_folder
        from app.copy_engine import CopyEngine
        from app.exiftool_runner import ExifToolRunner
        from app.gui.main_window import MainWindow

        _ = (analyze_folder, CopyEngine, MainWindow)
        exiftool = ExifToolRunner()
        version = exiftool.version()
        if version is None:
            print("self-test failed: bundled ExifTool not found")
            return 2
        print(f"exiftool {version}")
        print("self-test ok")
        return 0
    if "--exiftool-version" in args:
        from app.exiftool_runner import ExifToolRunner

        version = ExifToolRunner().version()
        if version is None:
            print("ExifTool not found")
            return 2
        print(version)
        return 0
    if "--portable-smoke" in args:
        index = args.index("--portable-smoke")
        try:
            smoke_root = Path(args[index + 1])
        except IndexError:
            print("Usage: PhotoBracketSorter.exe --portable-smoke <empty-test-folder>")
            return 2
        return _portable_smoke(smoke_root)
    if "--i18n-self-test" in args:
        return _i18n_self_test()
    if "--move-smoke" in args:
        index = args.index("--move-smoke")
        try:
            smoke_root = Path(args[index + 1])
        except IndexError:
            print("Usage: PhotoBracketSorter.exe --move-smoke <empty-test-folder>")
            return 2
        return _move_smoke(smoke_root)
    if "--mixed-timezone-self-test" in args:
        return _mixed_timezone_self_test()
    from app.gui.main_window import run

    run()
    return 0


def _portable_smoke(root: Path) -> int:
    from app.analysis import analyze_folder
    from app.copy_engine import CopyEngine
    from app.copy_plan import build_copy_plan
    from app.exiftool_runner import ExifToolRunner
    from app.models import AppSettings
    from app.reporting import write_reports

    root.mkdir(parents=True, exist_ok=True)
    source = root / "source_русский"
    output = root / "output"
    source.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    sample = source / "IMG_20260701_120000_123_тест.jpg"
    _write_smoke_jpeg(sample)
    exiftool = ExifToolRunner()
    version = exiftool.version()
    if version is None:
        print("portable smoke failed: ExifTool not found")
        return 2
    settings = AppSettings()
    analysis = analyze_folder(source, settings, threading.Event())
    plan = build_copy_plan(analysis, output, settings)
    result = CopyEngine(settings).execute(plan, threading.Event())
    write_reports(analysis, result, settings)
    if result.copied_files < 1:
        print("portable smoke failed: no files copied")
        return 3
    print(f"exiftool {version}")
    print(f"portable smoke ok: {result.output_root}")
    return 0


def _i18n_self_test() -> int:
    from app.i18n import I18n, validate_catalogs

    ok, missing = validate_catalogs()
    if not ok:
        print("i18n self-test failed: " + ", ".join(missing[:10]))
        return 3
    if I18n("ru").gettext("action.move") == I18n("en").gettext("action.move"):
        print("i18n self-test failed: ru/en move labels are identical")
        return 4
    print("i18n self-test ok")
    return 0


def _move_smoke(root: Path) -> int:
    from app.analysis import analyze_folder
    from app.copy_plan import build_copy_plan
    from app.models import AppSettings
    from app.move_engine import MoveEngine
    from app.reporting import write_reports

    root.mkdir(parents=True, exist_ok=True)
    source = root / "move_source_русский"
    output = root / "move_output"
    source.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    sample = source / "MOVE_20260701_120000.jpg"
    _write_smoke_jpeg(sample)
    settings = AppSettings()
    analysis = analyze_folder(source, settings, threading.Event())
    plan = build_copy_plan(analysis, output, settings)
    result = MoveEngine(settings).execute(plan, threading.Event())
    write_reports(analysis, result, settings)
    destination = next((operation.destination_path for operation in result.operations), None)
    if result.copied_files != 1 or result.errors != 0:
        print(f"move smoke failed: copied={result.copied_files} errors={result.errors}")
        return 3
    if sample.exists():
        print("move smoke failed: source still exists")
        return 4
    if destination is None or not destination.exists():
        print("move smoke failed: destination missing")
        return 5
    print(f"move smoke ok: {result.output_root}")
    return 0


def _mixed_timezone_self_test() -> int:
    from datetime import datetime, timedelta, timezone

    from app.bracket_detection import BracketDetector
    from app.enums import DetectedType
    from app.models import AppSettings, PhotoFile, PhotoMetadata
    from app.pairing import PairMatcher
    from app.time_compare import MIXED_TIMEZONE_ASSUMED_OFFSET, safe_datetime_difference_seconds

    def make_file(name: str, detected_type: DetectedType, capture_time: datetime, exposure: float | None = None) -> PhotoFile:
        metadata = PhotoMetadata(
            source_path=Path("C:/synthetic") / name,
            relative_path=Path(name),
            file_name=name,
            extension=Path(name).suffix.lower(),
            detected_type=detected_type,
            file_size=1024,
            width=4000,
            height=3000,
            make="Synthetic",
            model="Mixed Timezone",
            lens_model="main",
            capture_time=capture_time,
            capture_time_raw=str(capture_time),
            exposure_compensation=exposure,
            iso=100,
            exposure_time=0.01,
            f_number=1.8,
        )
        return PhotoFile(metadata=metadata)

    aware = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    naive = datetime(2026, 7, 1, 12, 0, 0, 200000)
    diff = safe_datetime_difference_seconds(aware, naive, left_name="raw.dng", right_name="rendered.heic")
    if diff.absolute_seconds is None or diff.warning_code != MIXED_TIMEZONE_ASSUMED_OFFSET:
        print("mixed timezone self-test failed: safe diff")
        return 3
    raw = make_file("IMG_0001.dng", DetectedType.DNG, aware)
    heic = make_file("IMG_0001.heic", DetectedType.HEIC, naive)
    pairs = PairMatcher().match([raw], [heic], AppSettings())
    if len(pairs) != 1 or MIXED_TIMEZONE_ASSUMED_OFFSET not in pairs[0].warnings:
        print("mixed timezone self-test failed: pairing")
        return 4
    bracket_files = [
        make_file("MIX_0001.dng", DetectedType.DNG, aware, -1.0),
        make_file("MIX_0002.dng", DetectedType.DNG, naive, 0.0),
        make_file("MIX_0003.dng", DetectedType.DNG, datetime(2026, 7, 1, 12, 0, 0, 600000), 1.0),
    ]
    _candidates, series = BracketDetector().detect(bracket_files, AppSettings())
    if len(series) != 1 or MIXED_TIMEZONE_ASSUMED_OFFSET not in series[0].warnings:
        print("mixed timezone self-test failed: bracket")
        return 5
    print("mixed timezone self-test ok")
    return 0


def _write_smoke_jpeg(path: Path) -> None:
    try:
        from PIL import Image

        image = Image.new("RGB", (8, 8), color=(32, 96, 160))
        image.save(path, format="JPEG")
    except Exception:
        path.write_bytes(
            bytes.fromhex(
                "FFD8FFE000104A46494600010101006000600000"
                "FFDB004300" + "08" * 64 + "FFC00011080008000803012200021101031101"
                "FFC4001400010000000000000000000000000000000000000000"
                "FFDA000C03010002110311003F00D2CF20FFD9"
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
