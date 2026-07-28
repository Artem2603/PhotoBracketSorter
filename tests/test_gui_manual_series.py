"""GUI-level manual series editing, undo, and statistics agreement (P0-3)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.enums import DestinationCategory, DetectedType
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_file(index: int, detected_type: DetectedType = DetectedType.DNG) -> PhotoFile:
    name = f"IMG_{index:04d}.dng"
    return PhotoFile(
        metadata=PhotoMetadata(
            source_path=Path(f"C:/photos/{name}"),
            relative_path=Path(name),
            file_name=name,
            extension=".dng",
            detected_type=detected_type,
            capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
            exposure_compensation=float(index % 3) - 1.0,
            file_size=1000,
        ),
        destination_category=DestinationCategory.RAW,
        destination_subpath=Path("RAW") / name,
    )


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiManualSeriesTests(unittest.TestCase):
    def setUp(self):
        # Modal dialogs block forever without a user, so every messagebox entry point
        # is stubbed. Rejections are recorded instead, and asserted on directly.
        self.dialogs: list[tuple[str, str]] = []
        patcher = mock.patch.multiple(
            "app.gui.main_window.messagebox",
            showwarning=lambda title, message, **kw: self.dialogs.append(("warning", message)),
            showerror=lambda title, message, **kw: self.dialogs.append(("error", message)),
            showinfo=lambda title, message, **kw: self.dialogs.append(("info", message)),
            askyesno=lambda title, message, **kw: True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow()
        self.files = [make_file(index) for index in range(6)]
        self.window.analysis = AnalysisResult(
            source_root=Path("C:/photos"),
            files=self.files,
            pairs=[],
            bracket_candidates=[],
            bracket_series=[],
            statistics={},
            warnings=[],
            created_at=BASE_TIME,
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="fp",
        )
        from app.series_manager import SeriesManager

        self.window.series_manager = SeriesManager.from_analysis(self.window.analysis)
        self.window._populate_table()

    def tearDown(self):
        self.window.destroy()

    def _select(self, indices: list[int]) -> None:
        self.window.tree.selection_set([str(index) for index in indices])

    def test_two_manual_series_land_in_separate_folders(self):
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self._select([3, 4, 5])
        self.window._manual_series(3)
        series = self.window.series_manager.series
        self.assertEqual(len(series), 2)
        self.assertNotEqual(series[0].series_id, series[1].series_id)
        self.assertNotEqual(series[0].destination_root, series[1].destination_root)

    def test_manual_series_reaches_the_analysis_and_statistics(self):
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self.assertEqual(len(self.window.analysis.bracket_series), 1)
        self.assertEqual(self.window.analysis.statistics["series_3"], 1)
        self.assertEqual(self.window.analysis.statistics["raw_in_series_3"], 3)

    def test_undo_restores_the_previous_state(self):
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self.assertEqual(len(self.window.series_manager.series), 1)
        self.window._undo()
        self.assertEqual(len(self.window.series_manager.series), 0)
        self.assertEqual(self.window.analysis.statistics["series_3"], 0)
        for file in self.files[:3]:
            self.assertIsNone(file.series_id)

    def test_redo_reapplies_the_series(self):
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self.window._undo()
        self.window._redo()
        self.assertEqual(len(self.window.series_manager.series), 1)

    def test_dissolve_removes_the_series_everywhere(self):
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self._select([0])
        self.window._dissolve_series()
        self.assertEqual(self.window.series_manager.series, [])
        self.assertEqual(self.window.analysis.bracket_series, [])
        self.assertEqual(self.window.analysis.statistics["series_3"], 0)

    def test_reassigning_a_member_dissolves_its_series(self):
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self._select([0])
        self.window._assign_selected(DestinationCategory.SKIP)
        self.assertEqual(self.window.series_manager.series, [], "the series must not keep a reassigned member")
        self.assertEqual(self.window.analysis.bracket_series, [])

    def test_wrong_selection_size_creates_nothing(self):
        self._select([0, 1])
        self.window._manual_series(3)
        self.assertEqual(self.window.series_manager.series, [])
        self.assertTrue(self.dialogs, "the user must be told why the series was refused")
        self.assertEqual(self.dialogs[0][0], "warning")

    def test_manual_edit_invalidates_a_stale_plan(self):
        self.window.copy_plan = object()
        self._select([0, 1, 2])
        self.window._manual_series(3)
        self.assertIsNone(self.window.copy_plan)


if __name__ == "__main__":
    unittest.main()
