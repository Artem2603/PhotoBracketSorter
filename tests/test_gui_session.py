"""GUI wiring for session save/open (feature 9, 1.2.0)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.analysis import analyze_folder
from app.enums import DestinationCategory
from app.models import AppSettings
from app.series_manager import SeriesManager
from app.session import build_session, save_session
from app.manual_overrides import assign_category

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False

JPEG_BYTES = bytes.fromhex("FFD8FFE000104A46494600010101006000600000FFD9")


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiSessionTests(unittest.TestCase):
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
            ("app.gui.main_window.journal_path", "operations_journal.json"),
        ):
            p = mock.patch(name, return_value=Path(self._temp_dir.name) / target)
            p.start()
            self.addCleanup(p.stop)
        self.window = MainWindow()
        self.source = Path(self._temp_dir.name) / "source"
        self.source.mkdir()
        (self.source / "a.jpg").write_bytes(JPEG_BYTES)
        (self.source / "b.jpg").write_bytes(JPEG_BYTES)
        self.session_path = Path(self._temp_dir.name) / "test.pbsession"

    def tearDown(self):
        self.window.destroy()

    def _analyze_real_folder(self):
        analysis = analyze_folder(self.source, AppSettings(), threading.Event())
        self.window.analysis = analysis
        self.window.series_manager = SeriesManager.from_analysis(analysis)
        return analysis

    # ------------------------------------------------------------- save

    def test_save_session_button_starts_disabled(self):
        self.assertEqual(str(self.window.save_session_button["state"]), "disabled")

    def test_save_session_button_enables_once_analysis_exists(self):
        self._analyze_real_folder()
        self.window.events.put(("analysis_done", self.window.analysis))
        self.window._poll_events()
        self.assertEqual(str(self.window.save_session_button["state"]), "normal")

    def test_save_session_without_an_analysis_shows_a_warning_and_writes_nothing(self):
        self.window._save_session()
        self.assertTrue(any(kind == "warning" for kind, _ in self.dialogs))
        self.assertFalse(self.session_path.exists())

    def test_save_session_writes_a_loadable_pbsession_file(self):
        self._analyze_real_folder()
        with mock.patch("app.gui.main_window.filedialog.asksaveasfilename", return_value=str(self.session_path)):
            self.window._save_session()
        self.assertTrue(self.session_path.exists())
        from app.session import load_session

        loaded = load_session(self.session_path)
        self.assertEqual(Path(loaded.source_root), self.source.resolve())

    def test_save_session_cancelled_dialog_writes_nothing(self):
        self._analyze_real_folder()
        with mock.patch("app.gui.main_window.filedialog.asksaveasfilename", return_value=""):
            self.window._save_session()
        self.assertFalse(self.session_path.exists())

    # ------------------------------------------------------------- open

    def test_open_session_loads_and_populates_the_table(self):
        analysis = self._analyze_real_folder()
        excluded = analysis.files[0]
        assign_category(excluded, DestinationCategory.SKIP)
        session = build_session(analysis, self.window.series_manager, self.window.settings, None)
        save_session(session, self.session_path)

        # Reset so the test observes a real open, not leftover state from setUp.
        self.window.analysis = None
        self.window.series_manager = None
        self.window.tree.delete(*self.window.tree.get_children())

        with mock.patch("app.gui.main_window.filedialog.askopenfilename", return_value=str(self.session_path)):
            self.window._start_open_session()
        self.window.worker.join(timeout=10)
        self.assertFalse(self.window.worker.is_alive(), "open-session worker did not finish in time")
        self.window._poll_events()

        self.assertIsNotNone(self.window.analysis)
        self.assertEqual(len(self.window.analysis.files), 2)
        self.assertEqual(self.window.source_var.get(), str(self.source.resolve()))
        self.assertEqual(len(self.window.tree.get_children()), 2)
        restored_excluded = next(f for f in self.window.analysis.files if f.metadata.relative_path == excluded.metadata.relative_path)
        self.assertIs(restored_excluded.destination_category, DestinationCategory.SKIP)
        self.assertEqual(str(self.window.save_session_button["state"]), "normal")

    def test_open_session_cancelled_dialog_starts_no_worker(self):
        with mock.patch("app.gui.main_window.filedialog.askopenfilename", return_value=""):
            self.window._start_open_session()
        self.assertIsNone(self.window.worker)

    def test_open_a_corrupted_session_file_shows_an_error_and_starts_no_worker(self):
        self.session_path.write_text("not valid json", encoding="utf-8")
        with mock.patch("app.gui.main_window.filedialog.askopenfilename", return_value=str(self.session_path)):
            self.window._start_open_session()
        self.assertTrue(any(kind == "error" for kind, _ in self.dialogs))
        self.assertIsNone(self.window.worker)


if __name__ == "__main__":
    unittest.main()
