"""GUI wiring for the visual series viewer (feature 5, 1.2.0)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.enums import ConfidenceLevel, DetectedType
from app.models import BracketSeries, PhotoFile, PhotoMetadata

try:
    import tkinter as tk

    from PIL import Image

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)


def make_series_with_real_jpegs(tmpdir: Path) -> BracketSeries:
    files = []
    for index, color in enumerate([(250, 250, 250), (128, 128, 128), (2, 2, 2)]):
        name = f"IMG_{index}.jpg"
        path = tmpdir / name
        Image.new("RGB", (80, 60), color=color).save(path, format="JPEG")
        files.append(
            PhotoFile(
                metadata=PhotoMetadata(
                    source_path=path,
                    relative_path=Path(name),
                    file_name=name,
                    extension=".jpg",
                    detected_type=DetectedType.JPEG,
                    capture_time=BASE_TIME + timedelta(seconds=0.4 * index),
                    exposure_time=1 / 125,
                    f_number=2.8,
                    iso=100,
                    exposure_compensation=float(index - 1),
                    file_size=1000,
                )
            )
        )
    return BracketSeries(
        series_id="HDR3_0001",
        series_number=1,
        series_size=3,
        raw_files=files,
        rendered_companions={f.metadata.file_name: None for f in files},
        start_time=files[0].metadata.capture_time,
        end_time=files[-1].metadata.capture_time,
        duration_seconds=0.8,
        exposure_values=[-1.0, 0.0, 1.0],
        estimated_step_ev=1.0,
        confidence=95.0,
        confidence_level=ConfidenceLevel.HIGH,
        automatic=True,
        manually_confirmed=False,
    )


@unittest.skipUnless(TK_AVAILABLE, "Tk/Pillow are not available in this environment")
class GuiSeriesViewerTests(unittest.TestCase):
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
        if self.window._last_series_dialog is not None:
            try:
                self.window._last_series_dialog.destroy()
            except tk.TclError:
                pass
        self.window.destroy()

    def test_opening_without_a_series_selected_shows_a_message(self):
        self.window.analysis = None
        self.window._open_series_viewer()
        self.assertTrue(self.dialogs)
        self.assertEqual(self.dialogs[0][0], "info")

    def test_dialog_builds_from_real_previews_without_crashing(self):
        from app.series_manager import SeriesManager
        from app.series_preview import build_series_previews

        series = make_series_with_real_jpegs(Path(self._temp_dir.name))
        self.window.series_manager = SeriesManager([series])
        previews = build_series_previews(list(series.raw_files))
        self.window._show_series_viewer_dialog(series, previews)
        dialog = self.window._last_series_dialog
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog._state["focus"], 0)

    def test_next_and_prev_wrap_around_the_frame_count(self):
        from app.series_manager import SeriesManager
        from app.series_preview import build_series_previews

        series = make_series_with_real_jpegs(Path(self._temp_dir.name))
        self.window.series_manager = SeriesManager([series])
        previews = build_series_previews(list(series.raw_files))
        self.window._show_series_viewer_dialog(series, previews)
        dialog = self.window._last_series_dialog
        dialog._set_focus(dialog._state["focus"] - 1)  # 0 - 1 -> wraps to last
        self.assertEqual(dialog._state["focus"], len(previews) - 1)
        dialog._set_focus(dialog._state["focus"] + 1)
        self.assertEqual(dialog._state["focus"], 0)

    def test_zoom_changes_are_applied_without_crashing(self):
        from app.series_manager import SeriesManager
        from app.series_preview import build_series_previews

        series = make_series_with_real_jpegs(Path(self._temp_dir.name))
        self.window.series_manager = SeriesManager([series])
        previews = build_series_previews(list(series.raw_files))
        self.window._show_series_viewer_dialog(series, previews)
        dialog = self.window._last_series_dialog
        dialog._on_zoom("2.0")
        self.assertEqual(dialog._state["zoom"], 2.0)
        dialog._on_zoom("0.5")
        self.assertEqual(dialog._state["zoom"], 0.5)

    def test_blink_toggle_starts_and_stops_a_timer_without_crashing(self):
        from app.series_manager import SeriesManager
        from app.series_preview import build_series_previews

        series = make_series_with_real_jpegs(Path(self._temp_dir.name))
        self.window.series_manager = SeriesManager([series])
        previews = build_series_previews(list(series.raw_files))
        self.window._show_series_viewer_dialog(series, previews)
        dialog = self.window._last_series_dialog
        dialog._blink_var.set(True)
        dialog._toggle_blink()
        self.assertTrue(dialog._state["blink"])
        self.assertIsNotNone(dialog._state["blink_job"])
        dialog._blink_var.set(False)
        dialog._toggle_blink()
        self.assertFalse(dialog._state["blink"])
        self.assertIsNone(dialog._state["blink_job"])

    def test_worker_reports_ready_through_the_event_queue(self):
        """Extraction must never run on the GUI thread; drive the real worker
        and confirm the completion event lands in the queue."""
        series = make_series_with_real_jpegs(Path(self._temp_dir.name))
        loading = tk.Toplevel(self.window)
        self.window._series_viewer_worker(series, loading)  # synchronous call is fine for this check
        event, payload = self.window.events.get_nowait()
        self.assertEqual(event, "series_preview_ready")
        _series, previews, loading_dialog = payload
        self.assertEqual(len(previews), 3)
        loading_dialog.destroy()


if __name__ == "__main__":
    unittest.main()
