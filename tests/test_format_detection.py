from pathlib import Path
import tempfile
import unittest

from app.enums import DetectedType
from app.signatures import detect_by_signature


class FormatDetectionTests(unittest.TestCase):
    def test_jpeg_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.bin"
            path.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
            detected, mime, warnings = detect_by_signature(path)
        self.assertEqual(detected, DetectedType.JPEG)
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(warnings, [])

    def test_heic_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic")
            detected, mime, warnings = detect_by_signature(path)
        self.assertEqual(detected, DetectedType.HEIC)
        self.assertEqual(mime, "image/heif")
        self.assertEqual(warnings, [])

    def test_zero_file_is_corrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.dng"
            path.write_bytes(b"")
            detected, mime, warnings = detect_by_signature(path)
        self.assertEqual(detected, DetectedType.CORRUPTED)
        self.assertIsNone(mime)
        self.assertTrue(warnings)


if __name__ == "__main__":
    unittest.main()
