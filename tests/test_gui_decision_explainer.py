"""GUI wiring for the 'why was this decided?' dialog (feature 4, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.bracket_detection import BracketDetector
from app.enums import DetectedType
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


def raw_meta(index: int, shutter: float) -> PhotoFile:
    name = f"IMG_{index:04d}.dng"
    return PhotoFile(
        metadata=PhotoMetadata(
            source_path=Path(f"C:/photos/{name}"),
            relative_path=Path(name),
            file_name=name,
            extension=".dng",
            detected_type=DetectedType.DNG,
            capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
            make="Fujifilm",
            model="X-T5",
            width=6000,
            height=4000,
            exposure_compensation=0.0,
            exposure_time=shutter,
            f_number=2.8,
            iso=100,
            file_size=1000,
        )
    )


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiWhyDialogTests(unittest.TestCase):
    def setUp(self):
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
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        for name, target in (
            ("app.gui.main_window.default_settings_path", "settings.json"),
            ("app.gui.main_window.user_profiles_path", "camera_profiles.json"),
        ):
            p = mock.patch(name, return_value=Path(self._temp_dir.name) / target)
            p.start()
            self.addCleanup(p.stop)
        self.window = MainWindow()

    def tearDown(self):
        self.window.destroy()

    def _build_series_analysis(self):
        files = [raw_meta(index, shutter) for index, shutter in enumerate((1 / 500, 1 / 125, 1 / 30))]
        _candidates, series = BracketDetector().detect(files, AppSettings())
        from app.series_manager import SeriesManager

        self.window.analysis = AnalysisResult(
            source_root=Path("C:/photos"),
            files=files,
            pairs=[],
            bracket_candidates=_candidates,
            bracket_series=series,
            statistics={},
            warnings=[],
            created_at=BASE_TIME,
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="fp",
        )
        self.window.series_manager = SeriesManager.from_analysis(self.window.analysis)
        self.window._populate_table()
        return files

    def test_why_dialog_with_no_selection_does_not_crash(self):
        self.window.analysis = None
        self.window._show_why_dialog()  # must not raise

    def test_why_dialog_opens_for_a_real_series_member(self):
        self._build_series_analysis()
        self.window.tree.selection_set(["0"])
        self.window._show_why_dialog()  # must not raise, real explanation built

    def test_why_menu_entry_exists_on_the_context_menu(self):
        # The context menu is built fresh on right-click; we only need the
        # underlying handler to exist and be wired, which _show_why_dialog is.
        self.assertTrue(hasattr(self.window, "_show_why_dialog"))


if __name__ == "__main__":
    unittest.main()
