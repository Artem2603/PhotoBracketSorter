"""Visual series preview: embedded RAW extraction, histograms, clipping (feature 5, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.enums import DetectedType
from app.exiftool_runner import ExifToolRunner
from app.models import PhotoFile, PhotoMetadata
from app.series_preview import (
    CLIPPING_FRACTION_THRESHOLD,
    build_frame_preview,
    build_series_previews,
    compute_histogram,
    detect_clipping,
    extract_preview_image,
)

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_jpeg(tmpdir: Path, name: str, color: tuple[int, int, int], size=(80, 60)) -> Path:
    path = tmpdir / name
    Image.new("RGB", size, color=color).save(path, format="JPEG", quality=95)
    return path


def make_file(path: Path, index: int, detected_type: DetectedType = DetectedType.JPEG, **overrides) -> PhotoFile:
    fields = dict(
        source_path=path,
        relative_path=Path(path.name),
        file_name=path.name,
        extension=path.suffix,
        detected_type=detected_type,
        capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
        exposure_time=1 / 125,
        f_number=2.8,
        iso=100,
        exposure_compensation=float(index),
        file_size=1000,
    )
    fields.update(overrides)
    return PhotoFile(metadata=PhotoMetadata(**fields))


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for preview tests")
class HistogramAndClippingTests(unittest.TestCase):
    def test_uniform_bright_image_is_highlight_clipped(self):
        image = Image.new("RGB", (50, 50), color=(255, 255, 255))
        histogram = compute_histogram(image)
        highlight, shadow = detect_clipping(histogram, 50 * 50)
        self.assertTrue(highlight)
        self.assertFalse(shadow)

    def test_uniform_dark_image_is_shadow_clipped(self):
        image = Image.new("RGB", (50, 50), color=(0, 0, 0))
        histogram = compute_histogram(image)
        highlight, shadow = detect_clipping(histogram, 50 * 50)
        self.assertFalse(highlight)
        self.assertTrue(shadow)

    def test_mid_gray_image_has_no_clipping(self):
        image = Image.new("RGB", (50, 50), color=(128, 128, 128))
        histogram = compute_histogram(image)
        highlight, shadow = detect_clipping(histogram, 50 * 50)
        self.assertFalse(highlight)
        self.assertFalse(shadow)

    def test_histogram_has_256_bins_per_channel(self):
        image = Image.new("RGB", (10, 10), color=(10, 20, 30))
        r, g, b = compute_histogram(image)
        self.assertEqual(len(r), 256)
        self.assertEqual(len(g), 256)
        self.assertEqual(len(b), 256)

    def test_histogram_bin_counts_sum_to_total_pixels(self):
        image = Image.new("RGB", (12, 7), color=(5, 5, 5))
        r, _g, _b = compute_histogram(image)
        self.assertEqual(sum(r), 12 * 7)

    def test_clipping_respects_the_threshold(self):
        # 1% of a 100x100 image = 100 pixels; put exactly 99 bright pixels in.
        image = Image.new("RGB", (100, 100), color=(0, 0, 0))
        for x in range(99):
            image.putpixel((x, 0), (255, 255, 255))
        histogram = compute_histogram(image)
        highlight, _shadow = detect_clipping(histogram, 100 * 100, threshold=CLIPPING_FRACTION_THRESHOLD)
        self.assertFalse(highlight, "99/10000 = 0.99% must fall below a 1% threshold")

    def test_zero_pixel_image_does_not_crash(self):
        highlight, shadow = detect_clipping(((0,) * 256, (0,) * 256, (0,) * 256), 0)
        self.assertFalse(highlight)
        self.assertFalse(shadow)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for preview tests")
class ExtractPreviewImageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def test_rendered_file_is_decoded_directly(self):
        path = make_jpeg(self.tmpdir, "a.jpg", (128, 128, 128))
        image, source, error = extract_preview_image(path, self.exiftool, is_raw=False)
        self.assertIsNotNone(image)
        self.assertEqual(source, "direct")
        self.assertIsNone(error)

    def test_rendered_file_is_resized_to_max_size(self):
        path = make_jpeg(self.tmpdir, "big.jpg", (100, 100, 100), size=(2000, 1000))
        image, _source, _error = extract_preview_image(path, self.exiftool, is_raw=False, max_size=200)
        self.assertLessEqual(max(image.size), 200)

    def test_raw_file_with_no_embedded_preview_reports_unavailable_not_a_crash(self):
        """A plain JPEG has no PreviewImage/JpgFromRaw/ThumbnailImage tags; forcing
        the RAW extraction path must fail gracefully, not raise."""
        path = make_jpeg(self.tmpdir, "fake_raw.jpg", (50, 50, 50))
        image, source, error = extract_preview_image(path, self.exiftool, is_raw=True)
        self.assertIsNone(image)
        self.assertEqual(source, "unavailable")
        self.assertIsNotNone(error)

    def test_missing_file_reports_unavailable_not_a_crash(self):
        image, source, error = extract_preview_image(self.tmpdir / "does_not_exist.jpg", self.exiftool, is_raw=False)
        self.assertIsNone(image)
        self.assertEqual(source, "unavailable")
        self.assertIsNotNone(error)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for preview tests")
class BuildFramePreviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def test_successful_frame_carries_exif_and_histogram(self):
        path = make_jpeg(self.tmpdir, "a.jpg", (200, 200, 200))
        file = make_file(path, 0, exposure_time=1 / 250, f_number=4.0, iso=200, exposure_compensation=-1.0)
        preview = build_frame_preview(file, self.exiftool)
        self.assertTrue(preview.available)
        self.assertEqual(preview.exif.exposure_time, 1 / 250)
        self.assertEqual(preview.exif.f_number, 4.0)
        self.assertEqual(preview.exif.iso, 200)
        self.assertEqual(preview.exif.ev_display, -1.0)
        self.assertIsNotNone(preview.histogram_rgb)
        self.assertEqual(preview.width, 80)
        self.assertEqual(preview.height, 60)

    def test_failed_frame_carries_an_error_not_a_crash(self):
        file = make_file(self.tmpdir / "missing.jpg", 0)
        preview = build_frame_preview(file, self.exiftool)
        self.assertFalse(preview.available)
        self.assertIsNotNone(preview.error)
        self.assertIsNone(preview.histogram_rgb)

    def test_ev_falls_back_through_the_same_priority_as_the_rest_of_the_app(self):
        """No exposure_compensation, but real shutter/aperture/ISO -> EV100 fallback,
        exactly like app.exposure does everywhere else."""
        path = make_jpeg(self.tmpdir, "a.jpg", (100, 100, 100))
        file = make_file(path, 0, exposure_compensation=None, exposure_time=1 / 125, f_number=2.8, iso=100)
        preview = build_frame_preview(file, self.exiftool)
        self.assertEqual(preview.exif.ev_source, "EV100")
        self.assertIsNotNone(preview.exif.ev_display)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for preview tests")
class BuildSeriesPreviewsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def test_frames_are_ordered_by_capture_time_not_input_order(self):
        colors = [(250, 250, 250), (128, 128, 128), (2, 2, 2)]
        files = []
        # Deliberately build with capture_time in REVERSE of the list order.
        for i, color in enumerate(colors):
            path = make_jpeg(self.tmpdir, f"IMG_{i}.jpg", color)
            files.append(make_file(path, len(colors) - 1 - i))
        previews = build_series_previews(files)
        names = [preview.exif.file_name for preview in previews]
        self.assertEqual(names, ["IMG_2.jpg", "IMG_1.jpg", "IMG_0.jpg"])

    def test_one_bad_frame_does_not_block_the_others(self):
        good_path = make_jpeg(self.tmpdir, "good.jpg", (128, 128, 128))
        files = [make_file(good_path, 0), make_file(self.tmpdir / "missing.jpg", 1)]
        previews = build_series_previews(files)
        self.assertEqual(len(previews), 2)
        available = [preview.available for preview in previews]
        self.assertEqual(sorted(available), [False, True])

    def test_three_and_five_frame_series_both_produce_one_preview_per_frame(self):
        for count in (3, 5):
            files = [make_file(make_jpeg(self.tmpdir, f"s{count}_{i}.jpg", (100, 100, 100)), i) for i in range(count)]
            previews = build_series_previews(files)
            self.assertEqual(len(previews), count)


if __name__ == "__main__":
    unittest.main()
