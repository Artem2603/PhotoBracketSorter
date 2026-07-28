"""Extended classification: HDR7/9, incomplete brackets, focus-bracketing,
burst, probable panorama (feature 8, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.enums import DestinationCategory, DetectedType, GroupClassification
from app.exiftool_runner import ExifToolRunner
from app.extended_classification import (
    apply_pair_and_review_labels,
    classify_extended_groups,
    cluster_unassigned_raw_files,
)
from app.models import AppSettings, PhotoFile, PhotoMetadata, RawHeicPair

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_raw(tmpdir: Path, index: int, ec: float = 0.0, focus: float | None = None, gap: float = 0.4, **overrides) -> PhotoFile:
    name = f"IMG_{index:04d}.jpg"
    path = tmpdir / name
    if PIL_AVAILABLE and not path.exists():
        Image.new("RGB", (60, 40), color=(120, 120, 120)).save(path, format="JPEG")
    elif not path.exists():
        path.write_bytes(b"\xff\xd8\xff" + bytes([index % 256]) * 32)
    fields = dict(
        source_path=path,
        relative_path=Path(name),
        file_name=name,
        extension=".jpg",
        detected_type=DetectedType.JPEG,  # JPEG so extract_preview_image can decode it directly (is_raw check comes from caller)
        capture_time=BASE_TIME + timedelta(seconds=gap * index),
        make="Canon",
        model="EOS R5",
        width=60,
        height=40,
        orientation="Horizontal",
        exposure_compensation=ec,
        exposure_time=1 / 125,
        f_number=2.8,
        iso=100,
        focus_distance=focus,
        file_size=1000,
    )
    fields.update(overrides)
    return PhotoFile(metadata=PhotoMetadata(**fields))


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for these tests")
class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def test_time_adjacent_same_camera_files_form_one_cluster(self):
        files = [make_raw(self.tmpdir, index) for index in range(4)]
        clusters = cluster_unassigned_raw_files(files, AppSettings())
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 4)

    def test_a_large_time_gap_splits_clusters(self):
        files = [make_raw(self.tmpdir, index) for index in range(2)]
        files.append(make_raw(self.tmpdir, 2, gap=0.0))
        files[-1].metadata.capture_time = BASE_TIME + timedelta(seconds=120)
        files.append(make_raw(self.tmpdir, 3, gap=0.0))
        files[-1].metadata.capture_time = BASE_TIME + timedelta(seconds=120.4)
        clusters = cluster_unassigned_raw_files(files, AppSettings())
        self.assertEqual(len(clusters), 2)

    def test_different_cameras_split_clusters(self):
        files = [make_raw(self.tmpdir, index) for index in range(2)]
        files.append(make_raw(self.tmpdir, 2, model="Different Model"))
        files.append(make_raw(self.tmpdir, 3, model="Different Model"))
        clusters = cluster_unassigned_raw_files(files, AppSettings())
        self.assertEqual(len(clusters), 2)
        cameras = {tuple(sorted({file.metadata.model for file in cluster})) for cluster in clusters}
        self.assertEqual(cameras, {("EOS R5",), ("Different Model",)})

    def test_an_assigned_file_does_not_prevent_its_still_close_neighbors_from_clustering(self):
        """The excluded frame simply does not join the cluster itself; its
        unassigned neighbors, if still within the gap of each other, do."""
        files = [make_raw(self.tmpdir, index) for index in range(3)]  # 0s, 0.4s, 0.8s apart
        files[1].series_id = "HDR3_0001"
        clusters = cluster_unassigned_raw_files(files, AppSettings())
        self.assertEqual(len(clusters), 1)
        cluster_ids = {id(file) for file in clusters[0]}
        self.assertEqual(cluster_ids, {id(files[0]), id(files[2])})

    def test_an_assigned_file_does_split_neighbors_that_are_far_apart_without_it(self):
        settings = AppSettings()
        settings.max_bracket_adjacent_delta_seconds = 0.5
        files = [make_raw(self.tmpdir, index, gap=0.4) for index in range(3)]
        files[1].series_id = "HDR3_0001"
        # file0 at 0s, file2 at 0.8s: 0.8s apart, over the 0.5s threshold.
        clusters = cluster_unassigned_raw_files(files, settings)
        self.assertEqual(clusters, [])

    def test_lone_file_is_not_a_cluster(self):
        files = [make_raw(self.tmpdir, 0)]
        self.assertEqual(cluster_unassigned_raw_files(files, AppSettings()), [])


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for these tests")
class IncompleteBracketTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def test_two_frames_with_real_ev_span_is_incomplete_bracket(self):
        """One frame short of HDR3, with genuine exposure evidence."""
        files = [make_raw(self.tmpdir, 0, ec=-2.0), make_raw(self.tmpdir, 1, ec=2.0)]
        classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.INCOMPLETE_BRACKET.value)
            self.assertTrue(file.group_classification_evidence)

    def test_four_frames_with_real_ev_span_is_incomplete_bracket_for_hdr5(self):
        files = [make_raw(self.tmpdir, i, ec=ec) for i, ec in enumerate((-2.0, -1.0, 1.0, 2.0))]
        classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.INCOMPLETE_BRACKET.value)

    def test_disabled_detection_leaves_files_unclassified_group(self):
        settings = AppSettings()
        settings.enable_incomplete_bracket_detection = False
        files = [make_raw(self.tmpdir, 0, ec=-2.0), make_raw(self.tmpdir, 1, ec=2.0)]
        classify_extended_groups(files, settings, self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.INCOMPLETE_BRACKET.value)

    def test_three_frames_with_ev_span_is_not_incomplete_it_is_a_real_bracket_elsewhere(self):
        """Size 3 is a supported size, not a near-miss; this module must not
        claim it (BracketDetector already handles size 3 upstream)."""
        files = [make_raw(self.tmpdir, i, ec=ec) for i, ec in enumerate((-2.0, 0.0, 2.0))]
        classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.INCOMPLETE_BRACKET.value)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for these tests")
class FocusBracketTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def test_flat_exposure_with_varying_focus_distance_is_focus_bracket(self):
        files = [make_raw(self.tmpdir, i, ec=0.0, focus=focus) for i, focus in enumerate((0.5, 1.0, 2.0, 4.0))]
        classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.FOCUS_BRACKET.value)

    def test_flat_exposure_with_constant_focus_is_not_focus_bracket(self):
        files = [make_raw(self.tmpdir, i, ec=0.0, focus=1.0) for i in range(4)]
        classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.FOCUS_BRACKET.value)

    def test_missing_focus_metadata_does_not_crash_and_is_not_focus_bracket(self):
        files = [make_raw(self.tmpdir, i, ec=0.0, focus=None) for i in range(4)]
        classify_extended_groups(files, AppSettings(), self.exiftool)  # must not raise
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.FOCUS_BRACKET.value)

    def test_disabled_detection_skips_focus_bracket_even_with_varying_focus(self):
        settings = AppSettings()
        settings.enable_focus_bracket_detection = False
        files = [make_raw(self.tmpdir, i, ec=0.0, focus=focus) for i, focus in enumerate((0.5, 1.0, 2.0, 4.0))]
        classify_extended_groups(files, settings, self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.FOCUS_BRACKET.value)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for these tests")
class BurstTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def test_near_identical_frames_with_flat_exposure_are_a_burst(self):
        """Real end-to-end pipeline: real embedded-preview extraction (direct
        decode for JPEG) and real dHash similarity, not a mocked value."""
        files = [make_raw(self.tmpdir, i, ec=0.0) for i in range(4)]
        classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.BURST.value)

    def test_disabled_burst_detection_does_not_label_identical_frames_as_burst(self):
        settings = AppSettings()
        settings.enable_burst_detection = False
        settings.enable_panorama_detection = False
        files = [make_raw(self.tmpdir, i, ec=0.0) for i in range(4)]
        classify_extended_groups(files, settings, self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.BURST.value)


class PanoramaThresholdTests(unittest.TestCase):
    """The similarity band is tested against controlled, mocked similarity
    values — dHash's exact numeric output for a simulated pan is an image-
    processing detail, not something a unit test should pin precisely; the
    classifier's threshold logic is what must be exactly right."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def _files(self, count=4):
        return [make_raw(self.tmpdir, i, ec=0.0) for i in range(count)]

    def test_moderate_similarity_with_enough_frames_is_probable_panorama(self):
        with mock.patch("app.extended_classification._average_adjacent_similarity", return_value=0.55):
            files = self._files(4)
            classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.PROBABLE_PANORAMA.value)

    def test_too_few_frames_is_not_panorama_even_with_moderate_similarity(self):
        settings = AppSettings()
        settings.panorama_min_frames = 5
        with mock.patch("app.extended_classification._average_adjacent_similarity", return_value=0.55):
            files = self._files(4)
            classify_extended_groups(files, settings, self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.PROBABLE_PANORAMA.value)

    def test_similarity_above_the_panorama_band_is_burst_not_panorama(self):
        with mock.patch("app.extended_classification._average_adjacent_similarity", return_value=0.95):
            files = self._files(4)
            classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.BURST.value)

    def test_similarity_below_the_panorama_band_is_unknown_group(self):
        with mock.patch("app.extended_classification._average_adjacent_similarity", return_value=0.05):
            files = self._files(4)
            classify_extended_groups(files, AppSettings(), self.exiftool)
        for file in files:
            self.assertEqual(file.group_classification, GroupClassification.UNKNOWN_GROUP.value)

    def test_disabled_panorama_detection_never_assigns_the_label(self):
        settings = AppSettings()
        settings.enable_panorama_detection = False
        with mock.patch("app.extended_classification._average_adjacent_similarity", return_value=0.55):
            files = self._files(4)
            classify_extended_groups(files, settings, self.exiftool)
        for file in files:
            self.assertNotEqual(file.group_classification, GroupClassification.PROBABLE_PANORAMA.value)

    def test_panorama_never_touches_destination_category(self):
        """The explicit caution requirement: a probable-panorama label must
        never, by itself, move a file anywhere."""
        with mock.patch("app.extended_classification._average_adjacent_similarity", return_value=0.55):
            files = self._files(4)
            categories_before = [file.destination_category for file in files]
            classify_extended_groups(files, AppSettings(), self.exiftool)
            categories_after = [file.destination_category for file in files]
        self.assertEqual(categories_before, categories_after)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for these tests")
class SingleAndHdrLabelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.exiftool = ExifToolRunner()

    def test_isolated_file_is_single_raw(self):
        files = [make_raw(self.tmpdir, 0)]
        files[0].metadata.capture_time = BASE_TIME
        classify_extended_groups(files, AppSettings(), self.exiftool)
        self.assertEqual(files[0].group_classification, GroupClassification.SINGLE_RAW.value)

    def test_series_member_gets_the_matching_hdr_label(self):
        for category, expected in (
            (DestinationCategory.BRACKETING_3, GroupClassification.HDR3),
            (DestinationCategory.BRACKETING_5, GroupClassification.HDR5),
            (DestinationCategory.BRACKETING_7, GroupClassification.HDR7),
            (DestinationCategory.BRACKETING_9, GroupClassification.HDR9),
        ):
            file = make_raw(self.tmpdir, 0)
            file.series_id = "HDR_0001"
            file.destination_category = category
            classify_extended_groups([file], AppSettings(), self.exiftool)
            self.assertEqual(file.group_classification, expected.value, f"{category} should map to {expected}")


class PairAndReviewLabelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def _pair(self, rendered_type: DetectedType):
        raw = make_raw(self.tmpdir, 0)
        raw.group_classification = GroupClassification.SINGLE_RAW.value
        raw.destination_category = DestinationCategory.RAW
        rendered_name = "IMG_0000.heic" if rendered_type is DetectedType.HEIC else "IMG_0000.jpg"
        rendered = PhotoFile(
            metadata=PhotoMetadata(
                source_path=self.tmpdir / rendered_name,
                relative_path=Path(rendered_name),
                file_name=rendered_name,
                extension=Path(rendered_name).suffix,
                detected_type=rendered_type,
                capture_time=BASE_TIME,
                file_size=800,
            ),
            destination_category=DestinationCategory.HEIC,
        )
        pair = RawHeicPair(
            pair_id="PAIR_0001",
            raw_file=raw,
            rendered_file=rendered,
            score=90.0,
            time_delta=0.1,
            name_similarity=0.95,
            metadata_similarity=0.9,
            preview_similarity=None,
        )
        return raw, rendered, pair

    def test_raw_heic_pair_gets_the_heic_label(self):
        raw, rendered, pair = self._pair(DetectedType.HEIC)
        apply_pair_and_review_labels([raw, rendered], [pair])
        self.assertEqual(raw.group_classification, GroupClassification.RAW_HEIC_PAIR.value)
        self.assertEqual(rendered.group_classification, GroupClassification.RAW_HEIC_PAIR.value)

    def test_raw_jpeg_pair_gets_the_jpeg_label(self):
        raw, rendered, pair = self._pair(DetectedType.JPEG)
        apply_pair_and_review_labels([raw, rendered], [pair])
        self.assertEqual(raw.group_classification, GroupClassification.RAW_JPEG_PAIR.value)

    def test_pairing_does_not_override_an_existing_hdr_label(self):
        raw, rendered, pair = self._pair(DetectedType.HEIC)
        raw.group_classification = GroupClassification.HDR3.value
        raw.destination_category = DestinationCategory.BRACKETING_3
        apply_pair_and_review_labels([raw, rendered], [pair])
        self.assertEqual(raw.group_classification, GroupClassification.HDR3.value)

    def test_review_category_gets_needs_review_regardless_of_other_labels(self):
        file = make_raw(self.tmpdir, 0)
        file.destination_category = DestinationCategory.REVIEW
        file.group_classification = GroupClassification.SINGLE_RAW.value
        apply_pair_and_review_labels([file], [])
        self.assertEqual(file.group_classification, GroupClassification.NEEDS_REVIEW.value)

    def test_unpaired_rendered_file_is_single_rendered(self):
        rendered = PhotoFile(
            metadata=PhotoMetadata(
                source_path=self.tmpdir / "solo.heic",
                relative_path=Path("solo.heic"),
                file_name="solo.heic",
                extension=".heic",
                detected_type=DetectedType.HEIC,
                capture_time=BASE_TIME,
                file_size=800,
            ),
            destination_category=DestinationCategory.HEIC,
        )
        apply_pair_and_review_labels([rendered], [])
        self.assertEqual(rendered.group_classification, GroupClassification.SINGLE_RENDERED.value)


if __name__ == "__main__":
    unittest.main()
