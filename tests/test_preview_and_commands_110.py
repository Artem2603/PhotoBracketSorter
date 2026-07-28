from pathlib import Path
import tempfile
import unittest

from PIL import Image

from app.analysis import collect_candidate_paths
from app.models import AppSettings
from app.preview_loader import PreviewCache, load_preview_info
from main import _move_smoke


class PreviewAndCommand110Tests(unittest.TestCase):
    def test_jpeg_preview_info_has_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            Image.new("RGB", (32, 16), color=(10, 20, 30)).save(path)
            info = load_preview_info(path, max_size=20)
            self.assertTrue(info.available)
            self.assertEqual((info.width, info.height), (32, 16))
            self.assertIsNotNone(info.image)

    def test_missing_preview_returns_fallback(self):
        info = load_preview_info(Path("missing.jpg"))
        self.assertFalse(info.available)
        self.assertIn("Preview unavailable", info.message)

    def test_dng_preview_uses_placeholder_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.dng"
            path.write_bytes(b"synthetic dng")
            info = load_preview_info(path)
            self.assertFalse(info.available)
            self.assertIn("RAW/DNG preview placeholder", info.message)

    def test_preview_cache_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = PreviewCache(max_items=2)
            paths = []
            for index in range(3):
                path = Path(directory) / f"{index}.jpg"
                Image.new("RGB", (8, 8), color=(index, index, index)).save(path)
                paths.append(path)
                cache.get(path)
            self.assertEqual(cache.size, 2)
            cached = cache.get(paths[-1])
            self.assertTrue(cached.available)

    def test_collect_candidate_paths_respects_allowed_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "a.jpg").write_bytes(b"x")
            (source / "b.txt").write_bytes(b"x")
            paths = collect_candidate_paths(source, AppSettings(allowed_extensions=(".jpg",)), __import__("threading").Event())
            self.assertEqual([path.name for path in paths], ["a.jpg"])

    def test_collect_candidate_paths_skips_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            hidden = source / ".hidden"
            hidden.mkdir()
            (hidden / "a.jpg").write_bytes(b"x")
            paths = collect_candidate_paths(source, AppSettings(), __import__("threading").Event())
            self.assertEqual(paths, [])

    def test_collect_candidate_paths_can_include_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            hidden = source / ".hidden"
            hidden.mkdir()
            (hidden / "a.jpg").write_bytes(b"x")
            paths = collect_candidate_paths(source, AppSettings(include_hidden_files=True), __import__("threading").Event())
            self.assertEqual([path.name for path in paths], ["a.jpg"])

    def test_move_smoke_command_returns_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(_move_smoke(Path(directory)), 0)


if __name__ == "__main__":
    unittest.main()
