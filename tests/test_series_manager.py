"""Manual series creation, dissolution, and state ownership (P0-3)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.enums import ConfidenceLevel, DestinationCategory, DetectedType
from app.errors import ManualSeriesError
from app.models import AnalysisResult, BracketSeries, PhotoFile, PhotoMetadata
from app.series_manager import SeriesManager, compute_statistics

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_file(index: int, detected_type: DetectedType = DetectedType.DNG, extension: str = ".dng") -> PhotoFile:
    name = f"IMG_{index:04d}{extension}"
    return PhotoFile(
        metadata=PhotoMetadata(
            source_path=Path(f"C:/photos/{name}"),
            relative_path=Path(name),
            file_name=name,
            extension=extension,
            detected_type=detected_type,
            capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
            exposure_compensation=float(index % 3) - 1.0,
            file_size=1000,
        )
    )


def make_analysis(files: list[PhotoFile]) -> AnalysisResult:
    return AnalysisResult(
        source_root=Path("C:/photos"),
        files=files,
        pairs=[],
        bracket_candidates=[],
        bracket_series=[],
        statistics={},
        warnings=[],
        created_at=BASE_TIME,
        settings_snapshot={},
        fingerprint="test",
    )


class ManualSeriesIdentityTests(unittest.TestCase):
    """Regression: every manual HDR3 used to receive the identical id."""

    def test_repeated_manual_series_get_distinct_ids(self):
        manager = SeriesManager()
        ids = []
        for batch in range(3):
            files = [make_file(batch * 3 + offset) for offset in range(3)]
            ids.append(manager.create_manual(files, 3).series_id)
        self.assertEqual(len(set(ids)), 3, f"ids collided: {ids}")

    def test_repeated_manual_series_get_distinct_uids(self):
        manager = SeriesManager()
        uids = {manager.create_manual([make_file(batch * 3 + o) for o in range(3)], 3).uid for batch in range(3)}
        self.assertEqual(len(uids), 3)

    def test_distinct_series_get_distinct_destination_folders(self):
        manager = SeriesManager()
        roots = []
        for batch in range(3):
            series = manager.create_manual([make_file(batch * 3 + o) for o in range(3)], 3)
            roots.append(series.destination_root)
        self.assertEqual(len(set(roots)), 3, f"series would merge into one folder: {roots}")

    def test_hdr3_and_hdr5_ids_do_not_collide(self):
        manager = SeriesManager()
        three = manager.create_manual([make_file(index) for index in range(3)], 3)
        five = manager.create_manual([make_file(index) for index in range(10, 15)], 5)
        self.assertNotEqual(three.series_id, five.series_id)
        self.assertNotEqual(three.destination_root, five.destination_root)

    def test_manual_folders_do_not_collide_with_automatic_ones(self):
        manager = SeriesManager()
        series = manager.create_manual([make_file(index) for index in range(3)], 3)
        self.assertIn("Manual_", str(series.destination_root))


class ManualSeriesValidationTests(unittest.TestCase):
    def test_wrong_file_count_is_rejected(self):
        manager = SeriesManager()
        with self.assertRaises(ManualSeriesError):
            manager.create_manual([make_file(index) for index in range(2)], 3)

    def test_rendered_files_are_rejected(self):
        manager = SeriesManager()
        files = [make_file(index, DetectedType.HEIC, ".heic") for index in range(3)]
        with self.assertRaises(ManualSeriesError) as caught:
            manager.create_manual(files, 3)
        self.assertIn("RAW", caught.exception.user_message)

    def test_mixed_raw_and_rendered_is_rejected(self):
        manager = SeriesManager()
        files = [make_file(0), make_file(1), make_file(2, DetectedType.JPEG, ".jpg")]
        with self.assertRaises(ManualSeriesError):
            manager.create_manual(files, 3)

    def test_corrupted_files_are_rejected(self):
        manager = SeriesManager()
        files = [make_file(0), make_file(1), make_file(2, DetectedType.CORRUPTED)]
        with self.assertRaises(ManualSeriesError):
            manager.create_manual(files, 3)

    def test_duplicate_selection_is_rejected(self):
        manager = SeriesManager()
        duplicated = make_file(1)
        with self.assertRaises(ManualSeriesError):
            manager.create_manual([duplicated, duplicated, make_file(2)], 3)

    def test_unsupported_size_is_rejected(self):
        manager = SeriesManager()
        with self.assertRaises(ManualSeriesError):
            manager.create_manual([make_file(index) for index in range(4)], 4)

    def test_rejection_leaves_state_untouched(self):
        manager = SeriesManager()
        files = [make_file(index, DetectedType.HEIC, ".heic") for index in range(3)]
        with self.assertRaises(ManualSeriesError):
            manager.create_manual(files, 3)
        self.assertEqual(manager.series, [])
        self.assertTrue(all(file.series_id is None for file in files))


class SeriesStateOwnershipTests(unittest.TestCase):
    def test_created_series_owns_file_placement(self):
        manager = SeriesManager()
        files = [make_file(index) for index in range(3)]
        series = manager.create_manual(files, 3)
        for file in files:
            self.assertEqual(file.series_id, series.series_id)
            self.assertIs(file.destination_category, DestinationCategory.BRACKETING_3)
            self.assertEqual(file.destination_subpath.parent, series.destination_root / "RAW")

    def test_five_shot_series_uses_the_five_shot_category(self):
        manager = SeriesManager()
        files = [make_file(index) for index in range(5)]
        manager.create_manual(files, 5)
        for file in files:
            self.assertIs(file.destination_category, DestinationCategory.BRACKETING_5)

    def test_dissolving_releases_files_back_to_raw(self):
        manager = SeriesManager()
        files = [make_file(index) for index in range(3)]
        series = manager.create_manual(files, 3)
        manager.dissolve(series)
        self.assertEqual(manager.series, [])
        for file in files:
            self.assertIsNone(file.series_id)
            self.assertIs(file.destination_category, DestinationCategory.RAW)

    def test_dissolving_leaves_no_orphan_series(self):
        """Regression: the file lost its series_id but the series stayed in reports."""
        manager = SeriesManager()
        files = [make_file(index) for index in range(3)]
        manager.create_manual(files, 3)
        manager.dissolve_containing([files[0]])
        self.assertEqual(manager.series, [])

    def test_reusing_a_file_moves_it_out_of_the_old_series(self):
        manager = SeriesManager()
        first = [make_file(index) for index in range(3)]
        manager.create_manual(first, 3)
        second = [first[0], make_file(10), make_file(11)]
        series = manager.create_manual(second, 3)
        self.assertEqual(len(manager.series), 1)
        self.assertEqual(manager.find_by_file(first[0]), series)
        # The two files left behind returned to singles rather than a broken series.
        self.assertIsNone(first[1].series_id)
        self.assertIsNone(first[2].series_id)

    def test_find_by_file_locates_membership(self):
        manager = SeriesManager()
        files = [make_file(index) for index in range(3)]
        series = manager.create_manual(files, 3)
        self.assertEqual(manager.find_by_file(files[1]), series)
        self.assertIsNone(manager.find_by_file(make_file(99)))

    def test_manual_series_records_its_exposure_source(self):
        manager = SeriesManager()
        series = manager.create_manual([make_file(index) for index in range(3)], 3)
        self.assertNotEqual(series.exposure_source, "")
        self.assertEqual(len(series.exposure_values), 3)


class StatisticsAgreementTests(unittest.TestCase):
    """Regression: GUI and report statistics used different keys and rules."""

    def test_manual_series_reaches_the_analysis_result(self):
        files = [make_file(index) for index in range(3)]
        analysis = make_analysis(files)
        manager = SeriesManager.from_analysis(analysis)
        manager.create_manual(files, 3)
        manager.apply_to(analysis)
        self.assertEqual(len(analysis.bracket_series), 1)
        self.assertEqual(analysis.statistics["series_3"], 1)
        self.assertEqual(analysis.statistics["series_manual"], 1)

    def test_statistics_carry_every_documented_key(self):
        files = [make_file(index) for index in range(3)]
        stats = compute_statistics(files, [])
        for key in (
            "total_discovered",
            "supported",
            "heic_jpeg",
            "raw_single",
            "series_3",
            "series_5",
            "series_manual",
            "raw_in_series_3",
            "raw_in_series_5",
            "companions_in_series",
            "review",
            "skipped",
            "errors",
            "bytes_planned",
        ):
            self.assertIn(key, stats)

    def test_statistics_follow_manual_edits(self):
        files = [make_file(index) for index in range(6)]
        analysis = make_analysis(files)
        manager = SeriesManager.from_analysis(analysis)
        manager.create_manual(files[:3], 3)
        manager.apply_to(analysis)
        self.assertEqual(analysis.statistics["raw_in_series_3"], 3)
        manager.dissolve(manager.series[0])
        manager.apply_to(analysis)
        self.assertEqual(analysis.statistics["series_3"], 0)
        self.assertEqual(analysis.statistics["raw_in_series_3"], 0)


class RenameSeriesTests(unittest.TestCase):
    """Feature 6 (sort preview) needs a real folder-name override, not cosmetic."""

    def test_rename_changes_the_destination_root(self):
        manager = SeriesManager()
        series = manager.create_manual([make_file(index) for index in range(3)], 3)
        manager.rename_series(series, "Sunset Beach")
        self.assertEqual(series.destination_root, Path("BRACKETING_3/Sunset Beach"))

    def test_rename_updates_every_member_destination_subpath(self):
        manager = SeriesManager()
        files = [make_file(index) for index in range(3)]
        series = manager.create_manual(files, 3)
        manager.rename_series(series, "Sunset")
        for file in files:
            self.assertEqual(file.destination_subpath.parent.parent, Path("BRACKETING_3/Sunset"))

    def test_clearing_the_name_restores_the_default(self):
        manager = SeriesManager()
        series = manager.create_manual([make_file(index) for index in range(3)], 3)
        manager.rename_series(series, "Sunset")
        manager.rename_series(series, None)
        self.assertEqual(series.destination_root, Path("BRACKETING_3/Manual_0001"))

    def test_blank_name_clears_rather_than_raising(self):
        manager = SeriesManager()
        series = manager.create_manual([make_file(index) for index in range(3)], 3)
        manager.rename_series(series, "Sunset")
        manager.rename_series(series, "   ")
        self.assertIsNone(series.custom_name)

    def test_duplicate_name_within_the_same_size_is_rejected(self):
        manager = SeriesManager()
        first = manager.create_manual([make_file(index) for index in range(3)], 3)
        second = manager.create_manual([make_file(index) for index in range(10, 13)], 3)
        manager.rename_series(first, "Sunset")
        with self.assertRaises(ManualSeriesError):
            manager.rename_series(second, "Sunset")

    def test_same_name_is_allowed_across_different_series_sizes(self):
        manager = SeriesManager()
        three = manager.create_manual([make_file(index) for index in range(3)], 3)
        five = manager.create_manual([make_file(index) for index in range(10, 15)], 5)
        manager.rename_series(three, "Sunset")
        manager.rename_series(five, "Sunset")  # must not raise: different category
        self.assertEqual(three.custom_name, "Sunset")
        self.assertEqual(five.custom_name, "Sunset")

    def test_renaming_a_series_not_owned_by_the_manager_is_a_no_op(self):
        manager = SeriesManager()
        foreign = manager.create_manual([make_file(index) for index in range(3)], 3)
        manager.dissolve(foreign)
        manager.rename_series(foreign, "Ghost")  # must not raise
        self.assertIsNone(foreign.custom_name)

    def test_sanitizes_characters_windows_forbids_in_a_path_component(self):
        manager = SeriesManager()
        series = manager.create_manual([make_file(index) for index in range(3)], 3)
        manager.rename_series(series, "Trip: Day/1?")
        self.assertNotIn(":", series.custom_name)
        self.assertNotIn("/", series.custom_name)
        self.assertNotIn("?", series.custom_name)


def make_auto_series(files: list[PhotoFile], number: int = 1, companions: dict[str, PhotoFile | None] | None = None) -> BracketSeries:
    """A detector-shaped automatic series (the detector numbers from 1 on every call)."""
    return BracketSeries(
        series_id=f"HDR3_{number:04d}",
        series_number=number,
        series_size=3,
        raw_files=files,
        rendered_companions=companions if companions is not None else {file.metadata.file_name: None for file in files},
        start_time=files[0].metadata.capture_time,
        end_time=files[-1].metadata.capture_time,
        duration_seconds=0.8,
        exposure_values=[-1.0, 0.0, 1.0],
        estimated_step_ev=1.0,
        confidence=90.0,
        confidence_level=ConfidenceLevel.HIGH,
        automatic=True,
        manually_confirmed=False,
    )


class AutomaticSeriesRegistrationTests(unittest.TestCase):
    """Regression: a series detected after the initial analysis ("restore
    automatic" in the review queue) was appended with the detector's own
    numbering, so two automatic series shared ``HDR3_0001`` and the
    ``BRACKETING_3/Series_0001`` destination folder."""

    def test_add_automatic_assigns_the_next_free_number_and_folder(self):
        first = [make_file(index) for index in range(3)]
        second = [make_file(index) for index in range(10, 13)]
        manager = SeriesManager([make_auto_series(first)])
        manager.sync_destinations()
        added = manager.add_automatic(make_auto_series(second))  # detector-numbered HDR3_0001 again
        ids = [series.series_id for series in manager.series]
        roots = [series.destination_root for series in manager.series]
        self.assertEqual(len(set(ids)), 2, ids)
        self.assertEqual(len(set(roots)), 2, roots)
        self.assertEqual(added.series_id, "HDR3_0002")
        self.assertEqual(added.destination_root, Path("BRACKETING_3/Series_0002"))
        self.assertTrue(added.automatic)
        for file in second:
            self.assertEqual(file.series_id, "HDR3_0002")
            self.assertEqual(file.destination_subpath, Path("BRACKETING_3/Series_0002/RAW") / file.metadata.file_name)

    def test_add_automatic_never_reuses_a_number_freed_by_dissolve(self):
        manager = SeriesManager(
            [
                make_auto_series([make_file(index) for index in range(3)], 1),
                make_auto_series([make_file(index) for index in range(10, 13)], 2),
            ]
        )
        manager.dissolve(manager.series[1])
        added = manager.add_automatic(make_auto_series([make_file(index) for index in range(20, 23)]))
        self.assertEqual(added.series_id, "HDR3_0003")

    def test_add_automatic_is_idempotent_for_a_series_already_registered(self):
        manager = SeriesManager()
        series = manager.add_automatic(make_auto_series([make_file(index) for index in range(3)]))
        again = manager.add_automatic(series)
        self.assertIs(again, series)
        self.assertEqual(len(manager.series), 1)
        self.assertEqual(series.series_id, "HDR3_0001")

    def test_add_automatic_selects_its_own_members(self):
        files = [make_file(index) for index in range(3)]
        files[1].selected = False
        SeriesManager().add_automatic(make_auto_series(files))
        self.assertTrue(all(file.selected for file in files))


class SelectionSurvivesSyncTests(unittest.TestCase):
    """Regression: ``sync_destinations`` set ``selected=True`` on every member, so a
    frame the user unticked was silently re-selected by any later series operation."""

    def test_deselected_frame_survives_an_unrelated_series_creation(self):
        manager = SeriesManager()
        frames = [make_file(index) for index in range(3)]
        manager.create_manual(frames, 3)
        frames[0].selected = False
        manager.create_manual([make_file(index) for index in range(10, 13)], 3)
        self.assertFalse(frames[0].selected)

    def test_deselected_frame_survives_rename_dissolve_and_explicit_sync(self):
        manager = SeriesManager()
        frames = [make_file(index) for index in range(3)]
        manager.create_manual(frames, 3)
        other = manager.create_manual([make_file(index) for index in range(10, 13)], 3)
        frames[1].selected = False
        manager.rename_series(other, "Sunset")
        manager.dissolve(other)
        manager.sync_destinations()
        self.assertFalse(frames[1].selected)

    def test_deselected_companion_survives_a_sync(self):
        raws = [make_file(index) for index in range(3)]
        heic = make_file(0, DetectedType.HEIC, ".heic")
        heic.selected = False
        manager = SeriesManager([make_auto_series(raws, companions={raws[0].metadata.file_name: heic})])
        manager.sync_destinations()
        self.assertFalse(heic.selected)

    def test_creating_a_manual_series_still_selects_its_own_members(self):
        manager = SeriesManager()
        frames = [make_file(index) for index in range(3)]
        frames[2].selected = False
        manager.create_manual(frames, 3)
        self.assertTrue(all(file.selected for file in frames))


class CompanionPlacementFlagTests(unittest.TestCase):
    """``store_companions_inside_series`` is honoured in exactly one place: here."""

    def _series_with_companion(self) -> tuple[list[PhotoFile], PhotoFile, BracketSeries]:
        raws = [make_file(index) for index in range(3)]
        heic = make_file(0, DetectedType.HEIC, ".heic")
        heic.destination_category = DestinationCategory.HEIC
        heic.destination_subpath = Path("HEIC") / heic.metadata.file_name
        raws[0].pair_id = heic.pair_id = "PAIR_0001"
        series = make_auto_series(raws, companions={raws[0].metadata.file_name: heic, raws[1].metadata.file_name: None, raws[2].metadata.file_name: None})
        return raws, heic, series

    def test_companions_follow_the_series_by_default(self):
        _raws, heic, series = self._series_with_companion()
        SeriesManager([series]).sync_destinations()
        self.assertEqual(heic.series_id, series.series_id)
        self.assertIs(heic.destination_category, DestinationCategory.BRACKETING_3)
        self.assertEqual(heic.destination_subpath, series.destination_root / "HEIC" / heic.metadata.file_name)

    def test_companions_stay_in_heic_when_the_flag_is_off(self):
        raws, heic, series = self._series_with_companion()
        SeriesManager([series], store_companions_inside_series=False).sync_destinations()
        self.assertIsNone(heic.series_id)
        self.assertIs(heic.destination_category, DestinationCategory.HEIC)
        self.assertEqual(heic.destination_subpath, Path("HEIC") / heic.metadata.file_name)
        # The relation is kept: the RAW is still placed, still paired, and the
        # series still knows which rendered file belongs to which frame.
        self.assertEqual(raws[0].series_id, "HDR3_0001")
        self.assertEqual(heic.pair_id, "PAIR_0001")
        self.assertIs(series.rendered_companions[raws[0].metadata.file_name], heic)

    def test_from_analysis_reads_the_flag_from_the_settings_snapshot(self):
        analysis = make_analysis([make_file(index) for index in range(3)])
        analysis.settings_snapshot = {"store_companions_inside_series": False}
        self.assertFalse(SeriesManager.from_analysis(analysis).store_companions_inside_series)
        analysis.settings_snapshot = {}
        self.assertTrue(SeriesManager.from_analysis(analysis).store_companions_inside_series)


if __name__ == "__main__":
    unittest.main()
