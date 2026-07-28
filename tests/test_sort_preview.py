"""Sort-result preview: folder tree, conflicts, exclusions (feature 6, 1.2.0)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from app.analysis import analyze_folder
from app.copy_plan import build_copy_plan
from app.models import AppSettings
from app.sort_preview import build_sort_preview


def make_jpeg(path: Path, color=(128, 128, 128), size=(8, 8)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path, format="JPEG")


class SortPreviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.source = self.tmpdir / "source"
        self.output = self.tmpdir / "output"
        self.source.mkdir()
        self.output.mkdir()

    def _analyze_and_plan(self, settings: AppSettings | None = None):
        settings = settings or AppSettings()
        analysis = analyze_folder(self.source, settings, threading.Event())
        plan = build_copy_plan(analysis, self.output, settings)
        return analysis, plan

    def test_single_file_lands_at_the_expected_relative_path(self):
        make_jpeg(self.source / "single.jpg")
        analysis, plan = self._analyze_and_plan()
        preview = build_sort_preview(analysis, plan)
        self.assertEqual(preview.total_files, 1)
        heic_folder = next(f for f in preview.root.folders if f.name == "HEIC")
        self.assertEqual(len(heic_folder.files), 1)
        self.assertEqual(heic_folder.files[0].destination_relative, "single.jpg")

    def test_name_collision_is_flagged_as_a_conflict(self):
        make_jpeg(self.source / "sub1" / "IMG_0001.jpg", color=(200, 200, 200))
        make_jpeg(self.source / "sub2" / "IMG_0001.jpg", color=(100, 100, 100))
        analysis, plan = self._analyze_and_plan()
        preview = build_sort_preview(analysis, plan)
        self.assertEqual(len(preview.conflicts), 1)
        self.assertTrue(preview.conflicts[0].destination_relative.endswith("_02.jpg"))
        self.assertEqual(preview.conflicts[0].source_name, "IMG_0001.jpg")

    def test_no_collision_means_no_conflicts(self):
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        analysis, plan = self._analyze_and_plan()
        preview = build_sort_preview(analysis, plan)
        self.assertEqual(preview.conflicts, ())

    def test_total_size_matches_the_plan(self):
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        analysis, plan = self._analyze_and_plan()
        preview = build_sort_preview(analysis, plan)
        self.assertEqual(preview.total_size, plan.total_bytes)
        self.assertEqual(preview.required_space, plan.required_space)

    def test_tree_file_counts_and_sizes_are_consistent_bottom_up(self):
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        analysis, plan = self._analyze_and_plan()
        preview = build_sort_preview(analysis, plan)
        self.assertEqual(preview.root.file_count, preview.total_files)
        self.assertEqual(preview.root.total_size, preview.total_size)

    def test_free_space_reflected_in_has_enough_space(self):
        make_jpeg(self.source / "a.jpg")
        analysis, plan = self._analyze_and_plan()
        generous = build_sort_preview(analysis, plan, free_space=plan.required_space + 1)
        stingy = build_sort_preview(analysis, plan, free_space=plan.required_space - 1)
        self.assertTrue(generous.has_enough_space)
        self.assertFalse(stingy.has_enough_space)

    def test_unknown_free_space_defaults_to_enough(self):
        make_jpeg(self.source / "a.jpg")
        analysis, plan = self._analyze_and_plan()
        preview = build_sort_preview(analysis, plan, free_space=None)
        self.assertTrue(preview.has_enough_space)

    def test_deselected_file_is_excluded_with_a_reason(self):
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        analysis, _first_plan = self._analyze_and_plan()
        analysis.files[0].selected = False
        plan = build_copy_plan(analysis, self.output / "again", AppSettings())
        preview = build_sort_preview(analysis, plan)
        self.assertEqual(len(preview.excluded), 1)
        self.assertEqual(preview.excluded[0].reason, "deselected")
        self.assertEqual(preview.total_files, 1)

    def test_skip_category_file_is_excluded_with_skip_reason(self):
        from app.enums import DestinationCategory
        from app.manual_overrides import assign_category

        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        analysis, _first_plan = self._analyze_and_plan()
        assign_category(analysis.files[0], DestinationCategory.SKIP)
        plan = build_copy_plan(analysis, self.output / "again", AppSettings())
        preview = build_sort_preview(analysis, plan)
        excluded_names = {excluded.name for excluded in preview.excluded}
        self.assertIn(analysis.files[0].metadata.file_name, excluded_names)
        reason = next(e.reason for e in preview.excluded if e.name == analysis.files[0].metadata.file_name)
        self.assertEqual(reason, "skip")

    def test_series_members_land_under_the_series_destination_root(self):
        from datetime import datetime, timedelta

        from app.enums import DetectedType
        from app.models import PhotoFile, PhotoMetadata
        from app.series_manager import SeriesManager

        base = datetime(2026, 7, 1, 12, 0, 0)
        raw_files = []
        for index in range(3):
            name = f"IMG_{index:04d}.dng"
            path = self.source / name
            path.write_bytes(b"\x00" * 32)
            raw_files.append(
                PhotoFile(
                    metadata=PhotoMetadata(
                        source_path=path,
                        relative_path=Path(name),
                        file_name=name,
                        extension=".dng",
                        detected_type=DetectedType.DNG,
                        capture_time=base + timedelta(seconds=0.4 * index),
                        file_size=32,
                    )
                )
            )
        manager = SeriesManager()
        series = manager.create_manual(raw_files, 3)
        manager.rename_series(series, "Sunset")
        from app.models import AnalysisResult

        analysis = AnalysisResult(
            source_root=self.source,
            files=raw_files,
            pairs=[],
            bracket_candidates=[],
            bracket_series=manager.series,
            statistics={},
            warnings=[],
            created_at=base,
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="fp",
        )
        plan = build_copy_plan(analysis, self.output, AppSettings())
        preview = build_sort_preview(analysis, plan)
        bracketing_folder = next(f for f in preview.root.folders if f.name == "BRACKETING_3")
        series_folder = next(f for f in bracketing_folder.folders if f.name == "Sunset")
        raw_folder = next(f for f in series_folder.folders if f.name == "RAW")
        self.assertEqual(len(raw_folder.files), 3)
        # The series folder itself must be identifiable back to the series
        # object, so the GUI can offer "rename series" on exactly this node.
        self.assertEqual(series_folder.series_uid, series.uid)
        self.assertIsNone(bracketing_folder.series_uid)
        self.assertIsNone(raw_folder.series_uid)


class PinnedOutputRootTests(unittest.TestCase):
    """Regression: the sort-preview dialog rebuilds the plan after every edit;
    without pinning, a template containing {time} would drift to a fresh
    destination folder on every single edit, before anything had moved."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.source = self.tmpdir / "source"
        self.output = self.tmpdir / "output"
        self.source.mkdir()
        self.output.mkdir()

    def test_pinned_output_root_is_reused_across_rebuilds(self):
        make_jpeg(self.source / "a.jpg")
        analysis = analyze_folder(self.source, AppSettings(), threading.Event())
        first = build_copy_plan(analysis, self.output, AppSettings())
        second = build_copy_plan(analysis, self.output, AppSettings(), pinned_output_root=first.output_root)
        third = build_copy_plan(analysis, self.output, AppSettings(), pinned_output_root=first.output_root)
        self.assertEqual(first.output_root, second.output_root)
        self.assertEqual(second.output_root, third.output_root)

    def test_pinned_output_root_still_reflects_edited_settings(self):
        """Pinning the root must not freeze anything else about the plan."""
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        analysis = analyze_folder(self.source, AppSettings(), threading.Event())
        first = build_copy_plan(analysis, self.output, AppSettings())
        analysis.files[0].selected = False
        settings = AppSettings()
        settings.duplicate_behavior = "overwrite"
        second = build_copy_plan(analysis, self.output, settings, pinned_output_root=first.output_root)
        self.assertEqual(second.output_root, first.output_root)
        self.assertEqual(len(second.operations), 1)


if __name__ == "__main__":
    unittest.main()
