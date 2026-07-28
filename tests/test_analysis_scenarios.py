from pathlib import Path
import tempfile
import threading
import unittest

from app.analysis import analyze_folder
from app.copy_engine import CopyEngine
from app.copy_plan import build_copy_plan
from app.enums import DestinationCategory
from app.models import AppSettings
from app.reporting import write_reports


JPEG_BYTES = bytes.fromhex("FFD8FFE000104A46494600010101006000600000FFD9")


class AnalysisScenarioTests(unittest.TestCase):
    def test_corrupted_zero_file_goes_to_review(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "empty.dng").write_bytes(b"")
            result = analyze_folder(source, AppSettings(), threading.Event())
            self.assertEqual(result.files[0].metadata.detected_type.value, "CORRUPTED")
            self.assertEqual(result.files[0].destination_category, DestinationCategory.REVIEW)

    def test_missing_exif_uses_fallback_time_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "plain.jpg").write_bytes(JPEG_BYTES)
            result = analyze_folder(source, AppSettings(), threading.Event())
            warnings = " ".join(result.files[0].warnings)
            self.assertIn("Точное время съёмки отсутствует", warnings)

    def test_russian_paths_analyze_copy_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "исходники"
            output = root / "результат"
            source.mkdir()
            output.mkdir()
            (source / "фото_20260701_120000.jpg").write_bytes(JPEG_BYTES)
            result = analyze_folder(source, AppSettings(), threading.Event())
            plan = build_copy_plan(result, output, AppSettings())
            copy_result = CopyEngine(AppSettings()).execute(plan, threading.Event())
            write_reports(result, copy_result, AppSettings())
            self.assertEqual(copy_result.copied_files, 1)
            self.assertTrue((copy_result.output_root / "HEIC" / "фото_20260701_120000.jpg").exists())
            self.assertTrue((copy_result.output_root / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
