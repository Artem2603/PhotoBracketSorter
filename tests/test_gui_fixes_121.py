"""GUI regression tests for the 1.2.1 hardening and visual refresh."""

from __future__ import annotations

import queue
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app.enums import DestinationCategory, DetectedType
from app.models import AnalysisResult, AppSettings, PhotoFile, PhotoMetadata

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow, clamp_geometry
    from app.theme_manager import BOOTSTRAP_AVAILABLE

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False


class ClampGeometryTests(unittest.TestCase):
    def test_oversized_window_is_capped_to_the_screen(self):
        self.assertEqual(clamp_geometry("1440x900", 1366, 768), "1326x728")

    def test_offset_on_a_detached_monitor_is_dropped(self):
        self.assertEqual(clamp_geometry("1200x800+2600+100", 1920, 1080), "1200x800")
        self.assertEqual(clamp_geometry("1200x800+100+100", 1920, 1080), "1200x800+100+100")

    def test_garbage_is_returned_unchanged(self):
        self.assertEqual(clamp_geometry("zoomed", 1920, 1080), "zoomed")


def _make_analysis(path: Path, category: DestinationCategory = DestinationCategory.HEIC) -> AnalysisResult:
    stat = path.stat()
    file = PhotoFile(
        metadata=PhotoMetadata(
            source_path=path,
            relative_path=Path(path.name),
            file_name=path.name,
            extension=path.suffix.lower(),
            detected_type=DetectedType.JPEG,
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            capture_time=datetime(2026, 7, 1, 12, 0, 0),
        ),
        destination_category=category,
        destination_subpath=Path(category.value) / path.name,
    )
    return AnalysisResult(
        source_root=path.parent,
        files=[file],
        pairs=[],
        bracket_candidates=[],
        bracket_series=[],
        statistics={},
        warnings=[],
        created_at=datetime.now(),
        settings_snapshot={},
        fingerprint="t",
    )


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiFixesTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.root = Path(self._temp_dir.name)
        self.dialogs: list[tuple[str, str]] = []
        for name, target in (
            ("app.gui.main_window.default_settings_path", "settings.json"),
            ("app.gui.main_window.user_profiles_path", "camera_profiles.json"),
            ("app.gui.main_window.journal_path", "operations_journal.json"),
        ):
            patcher = mock.patch(name, return_value=self.root / target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.multiple(
            "app.gui.main_window.messagebox",
            showwarning=lambda title, message, **kw: self.dialogs.append(("warning", message)),
            showerror=lambda title, message, **kw: self.dialogs.append(("error", message)),
            showinfo=lambda title, message, **kw: self.dialogs.append(("info", message)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window: MainWindow | None = None

    def tearDown(self):
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass

    def _open(self) -> MainWindow:
        self.window = MainWindow()
        return self.window

    def _pump(self, seconds: float = 0.6) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.window.update()
            time.sleep(0.02)

    def test_corrupt_settings_file_still_opens_the_window_with_defaults(self):
        (self.root / "settings.json").write_text('{"language": "en",', encoding="utf-8")
        window = self._open()
        self.assertEqual(window.settings.language, AppSettings().language)
        self._pump()
        self.assertTrue(any(kind == "warning" and "settings.json" in message for kind, message in self.dialogs), self.dialogs)
        # The damaged file is left for the user to inspect, not silently replaced.
        self.assertEqual((self.root / "settings.json").read_text(encoding="utf-8"), '{"language": "en",')

    def test_style_singleton_is_released_on_destroy(self):
        if not BOOTSTRAP_AVAILABLE:
            self.skipTest("ttkbootstrap not installed")
        import ttkbootstrap as tb

        window = self._open()
        self.assertIsNotNone(tb.Style.instance)
        window.destroy()
        self.window = None
        self.assertIsNone(tb.Style.instance)

    def test_theme_combobox_shows_localized_labels_and_round_trips(self):
        window = self._open()
        window.settings.language = "ru"
        window.i18n.set_language("ru")
        window._refresh_texts()
        self.assertEqual(window.theme_var.get(), window.i18n.gettext(f"theme.{window.settings.theme}"))
        window.theme_var.set(window.i18n.gettext("theme.dark"))
        window._change_theme()
        self.assertEqual(window.settings.theme, "dark")

    def test_status_phases_are_localized_with_counts(self):
        window = self._open()
        window.settings.language = "en"
        window.i18n.set_language("en")
        window._set_progress("Чтение метаданных", 3, 10)
        self.assertEqual(window.status_var.get(), "Reading metadata \u00b7 3 / 10")
        window._set_progress("Копирование HEIC", 1, 1)
        self.assertEqual(window.status_var.get(), "Copying HEIC")
        window._set_progress("Что-то новое", 1, 1)
        self.assertEqual(window.status_var.get(), "Что-то новое")

    def test_stat_card_click_toggles_the_filter(self):
        window = self._open()
        window._select_filter("review")
        self.assertEqual(window._current_filter_key(), "review")
        window._select_filter("review")
        self.assertEqual(window._current_filter_key(), "all")

    def test_settings_dialog_uses_human_readable_labels(self):
        window = self._open()
        window.settings.language = "en"
        window.i18n.set_language("en")
        self.assertEqual(window._settings_label("remember_paths"), "Remember last folders")
        self.assertEqual(window._settings_label("no_such_setting"), "no_such_setting")

    def test_preview_is_decoded_off_the_gui_thread_and_delivered_by_event(self):
        from PIL import Image

        photo = self.root / "IMG.jpg"
        Image.new("RGB", (64, 48), (120, 30, 30)).save(photo)
        window = self._open()
        window.analysis = _make_analysis(photo)
        window._populate_table()
        window.tree.selection_set(["0"])
        window._update_preview()
        event, payload = window.events.get(timeout=10)
        self.assertEqual(event, "preview_ready")
        token, info, size = payload
        self.assertTrue(info.available)
        self.assertEqual(size, window.settings.thumbnail_size)
        window._on_preview_ready(token, info, size)
        self.assertIsNotNone(window.preview_image)
        # A second selection of the same file is served from the cache synchronously.
        window._update_preview()
        with self.assertRaises(queue.Empty):
            window.events.get(timeout=0.3)

    def test_rows_are_tagged_by_outcome(self):
        photo = self.root / "IMG.jpg"
        photo.write_bytes(b"\xff\xd8\xff")
        window = self._open()
        window.analysis = _make_analysis(photo, DestinationCategory.REVIEW)
        window._populate_table()
        self.assertEqual(window.tree.item("0", "tags"), ("review",))

    def test_close_while_busy_asks_and_cancels_the_worker(self):
        window = self._open()
        stop = threading.Event()

        def wait_for_cancel() -> None:
            while not window.cancel_event.is_set() and not stop.is_set():
                time.sleep(0.01)

        window.worker = threading.Thread(target=wait_for_cancel, daemon=True)
        window.worker.start()
        try:
            with mock.patch("app.gui.main_window.messagebox.askyesno", return_value=False):
                window._on_close()
            self.assertTrue(window.winfo_exists())
            self.assertFalse(window.cancel_event.is_set())
            with mock.patch("app.gui.main_window.messagebox.askyesno", return_value=True):
                window._on_close()
            self.assertTrue(window.cancel_event.is_set())
            self.window = None
        finally:
            stop.set()


if __name__ == "__main__":
    unittest.main()
