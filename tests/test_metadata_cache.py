"""EXIF metadata cache used by saved sessions (feature 9, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.enums import CaptureTimeSource, DetectedType, TimezoneStatus
from app.metadata_cache import (
    build_cache_entries,
    deserialize_metadata,
    load_cache_entries,
    merge_metadata,
    serialize_metadata,
    split_cached_paths,
)
from app.models import PhotoMetadata


def make_metadata(name: str, root: Path, **overrides) -> PhotoMetadata:
    fields = dict(
        source_path=root / name,
        relative_path=Path(name),
        file_name=name,
        extension=Path(name).suffix.lower(),
        detected_type=DetectedType.DNG,
        file_size=1234,
        mtime_ns=999,
        make="Canon",
        model="EOS R5",
        capture_time=datetime(2026, 7, 1, 12, 0, 0),
        capture_time_source=CaptureTimeSource.DATE_TIME_ORIGINAL,
        timezone_status=TimezoneStatus.LOCAL_UNKNOWN,
        exposure_compensation=0.0,
        raw_exif_record={"Make": "Canon", "Model": "EOS R5"},
    )
    fields.update(overrides)
    return PhotoMetadata(**fields)


class SerializationRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_every_field(self):
        original = make_metadata("IMG_0001.dng", Path("C:/photos"))
        restored = deserialize_metadata(serialize_metadata(original))
        self.assertEqual(restored.source_path, original.source_path)
        self.assertEqual(restored.relative_path, original.relative_path)
        self.assertEqual(restored.detected_type, original.detected_type)
        self.assertEqual(restored.capture_time, original.capture_time)
        self.assertEqual(restored.capture_time_source, original.capture_time_source)
        self.assertEqual(restored.timezone_status, original.timezone_status)
        self.assertEqual(restored.make, original.make)
        self.assertEqual(restored.model, original.model)
        self.assertEqual(restored.exposure_compensation, original.exposure_compensation)
        self.assertEqual(restored.raw_exif_record, original.raw_exif_record)

    def test_round_trip_handles_missing_capture_time(self):
        original = make_metadata("IMG_0002.dng", Path("C:/photos"), capture_time=None)
        restored = deserialize_metadata(serialize_metadata(original))
        self.assertIsNone(restored.capture_time)

    def test_serialized_form_is_json_safe(self):
        import json

        original = make_metadata("IMG_0003.dng", Path("C:/photos"))
        # Must not raise: every value in the dict has to survive a JSON round trip.
        json.loads(json.dumps(serialize_metadata(original)))


class CacheEntryTests(unittest.TestCase):
    def test_build_and_load_round_trip(self):
        items = [make_metadata(f"IMG_{i:04d}.dng", Path("C:/photos")) for i in range(3)]
        entries = build_cache_entries(items)
        cache = load_cache_entries(entries)
        self.assertEqual(set(cache), {"IMG_0000.dng", "IMG_0001.dng", "IMG_0002.dng"})
        size, mtime_ns, metadata = cache["IMG_0001.dng"]
        self.assertEqual(size, 1234)
        self.assertEqual(mtime_ns, 999)
        self.assertEqual(metadata.file_name, "IMG_0001.dng")

    def test_a_single_corrupted_entry_is_dropped_not_fatal(self):
        items = [make_metadata("IMG_0000.dng", Path("C:/photos"))]
        entries = build_cache_entries(items)
        entries.append({"relative_path": "broken.dng", "file_size": "not-a-number", "mtime_ns": 1, "metadata": {}})
        cache = load_cache_entries(entries)
        self.assertEqual(set(cache), {"IMG_0000.dng"})

    def test_entries_missing_required_keys_are_dropped(self):
        cache = load_cache_entries([{"relative_path": "x.dng"}])
        self.assertEqual(cache, {})


class SplitAndMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, name: str, content: bytes = b"data") -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_unchanged_file_is_reused_from_cache(self):
        path = self._write("a.dng")
        stat = path.stat()
        cached_metadata = make_metadata("a.dng", self.root, file_size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        cache = {"a.dng": (stat.st_size, stat.st_mtime_ns, cached_metadata)}
        reused, fresh = split_cached_paths([path], self.root, cache)
        self.assertEqual(fresh, [])
        self.assertIn(path, reused)
        self.assertIs(reused[path], cached_metadata)

    def test_reused_metadata_source_path_is_reanchored_to_the_current_path(self):
        path = self._write("a.dng")
        stat = path.stat()
        stale_metadata = make_metadata("a.dng", Path("D:/old_location"), file_size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        cache = {"a.dng": (stat.st_size, stat.st_mtime_ns, stale_metadata)}
        reused, _fresh = split_cached_paths([path], self.root, cache)
        self.assertEqual(reused[path].source_path, path)

    def test_changed_mtime_forces_a_fresh_read(self):
        path = self._write("a.dng")
        stat = path.stat()
        cache = {"a.dng": (stat.st_size, stat.st_mtime_ns - 1, make_metadata("a.dng", self.root))}
        reused, fresh = split_cached_paths([path], self.root, cache)
        self.assertEqual(reused, {})
        self.assertEqual(fresh, [path])

    def test_changed_size_forces_a_fresh_read(self):
        path = self._write("a.dng")
        stat = path.stat()
        cache = {"a.dng": (stat.st_size + 1, stat.st_mtime_ns, make_metadata("a.dng", self.root))}
        reused, fresh = split_cached_paths([path], self.root, cache)
        self.assertEqual(reused, {})
        self.assertEqual(fresh, [path])

    def test_path_absent_from_cache_is_fresh(self):
        path = self._write("new.dng")
        reused, fresh = split_cached_paths([path], self.root, {})
        self.assertEqual(reused, {})
        self.assertEqual(fresh, [path])

    def test_merge_preserves_original_path_order(self):
        cached_path = self._write("a.dng")
        fresh_path = self._write("b.dng")
        cached_item = make_metadata("a.dng", self.root)
        fresh_item = make_metadata("b.dng", self.root)
        fresh_item.source_path = fresh_path
        merged = merge_metadata([cached_path, fresh_path], {cached_path: cached_item}, [fresh_item])
        self.assertEqual(merged, [cached_item, fresh_item])

    def test_merge_drops_nothing_when_order_is_reversed(self):
        path_a = self._write("a.dng")
        path_b = self._write("b.dng")
        item_a = make_metadata("a.dng", self.root)
        item_a.source_path = path_a
        item_b = make_metadata("b.dng", self.root)
        item_b.source_path = path_b
        merged = merge_metadata([path_b, path_a], {}, [item_a, item_b])
        self.assertEqual(merged, [item_b, item_a])


if __name__ == "__main__":
    unittest.main()
