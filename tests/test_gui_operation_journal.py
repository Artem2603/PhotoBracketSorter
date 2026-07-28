"""GUI wiring for the operation history / Move undo screen (feature 7, 1.2.0)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.analysis import analyze_folder
from app.models import AppSettings
from app.move_engine import MoveEngine
from app.copy_plan import build_copy_plan
from app.operation_journal import build_journal_entry, load_journal

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False

try:
    import tkinter as tk

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False


def make_move(tmpdir: Path):
    source = tmpdir / "source"
    output = tmpdir / "output"
    source.mkdir()
    output.mkdir()
    sample = source / "IMG_0.jpg"
    if PIL_AVAILABLE:
        Image.new("RGB", (10, 10), color=(50, 50, 50)).save(sample, format="JPEG")
    else:
        sample.write_bytes(b"\xff\xd8\xff" + b"0" * 32)
    settings = AppSettings()
    analysis = analyze_folder(source, settings, threading.Event())
    plan = build_copy_plan(analysis, output, settings)
    result = MoveEngine(settings).execute(plan, threading.Event())
    return source, result


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available in this environment")
class GuiOperationJournalTests(unittest.TestCase):
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

    def tearDown(self):
        self.window.destroy()

    def test_journal_path_is_isolated_from_real_appdata(self):
        self.assertEqual(self.window.journal_path, Path(self._temp_dir.name) / "operations_journal.json")

    def test_history_button_exists(self):
        self.assertTrue(hasattr(self.window, "history_button"))

    def test_history_dialog_opens_with_an_empty_journal(self):
        self.window._open_history()  # must not raise

    def test_move_via_the_gui_writes_a_journal_entry(self):
        move_tmp = Path(self._temp_dir.name) / "move_case"
        move_tmp.mkdir()
        source, result = make_move(move_tmp)
        entry = build_journal_entry(result, source)
        from app.operation_journal import append_journal_entry

        append_journal_entry(entry, self.window.journal_path)
        entries = load_journal(self.window.journal_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].operation_type, "move")

    def test_transfer_worker_writes_to_the_isolated_journal_path_not_the_real_one(self):
        """The GUI's own _transfer_worker must use self.journal_path, not the
        module-level default — this is the same isolation bug class fixed for
        settings.json and camera_profiles.json earlier in 1.2.0."""
        move_tmp = Path(self._temp_dir.name) / "worker_case"
        move_tmp.mkdir()
        source, result = make_move(move_tmp)
        self.window.analysis = type("A", (), {"source_root": source})()
        self.window.copy_plan = object()
        with mock.patch("app.gui.main_window.MoveEngine") as mock_engine_cls, mock.patch(
            "app.gui.main_window.write_reports"
        ):
            mock_engine_cls.return_value.execute.return_value = result
            self.window._transfer_worker("move")
        entries = load_journal(self.window.journal_path)
        self.assertEqual(len(entries), 1)

    def test_confirm_undo_move_defaults_to_not_accepted_without_interaction(self):
        """Fail-safe: if the modal never gets an explicit accept, undo must not
        proceed. Stubbing wait_window (no real event loop in a unit test) means
        accept() never runs, so the function must still return False, not True."""
        with mock.patch.object(self.window, "wait_window", lambda dialog: None):
            result = self.window._confirm_undo_move()
        self.assertFalse(result)

    def test_start_and_finish_undo_move_end_to_end(self):
        """Real move, real undo, driven through the actual GUI worker/event path
        (minus the Tk .after timer): _start_undo_move -> worker -> queued event
        -> _on_undo_move_done must restore the file and record a second journal
        entry for the undo itself."""
        move_tmp = Path(self._temp_dir.name) / "undo_case"
        move_tmp.mkdir()
        source, result = make_move(move_tmp)
        entry = build_journal_entry(result, source)
        from app.operation_journal import append_journal_entry

        append_journal_entry(entry, self.window.journal_path)

        done_calls = []
        self.window._start_undo_move(entry, lambda: done_calls.append(True))
        self.window.worker.join(timeout=10)
        self.assertFalse(self.window.worker.is_alive(), "undo worker did not finish in time")

        # Drain the event queue exactly like _poll_events would, without the
        # .after(100, ...) rescheduling that never fires outside mainloop().
        drained = []
        while True:
            try:
                drained.append(self.window.events.get_nowait())
            except Exception:
                break
        self.assertTrue(any(name == "undo_move_ready" for name, _payload in drained))
        for name, payload in drained:
            if name == "undo_move_ready":
                undo_entry, undo_result, on_done = payload
                self.window._on_undo_move_done(undo_entry, undo_result, on_done)

        self.assertTrue(done_calls, "the on_done callback (dialog refresh) was never invoked")
        restored_path = Path(entry.records[0].source_path)
        self.assertTrue(restored_path.exists(), "undo must restore the original file")

        entries = load_journal(self.window.journal_path)
        self.assertEqual(len(entries), 2, "the undo itself must be journaled as a second entry")
        self.assertEqual(entries[1].operation_type, "undo_move")
        self.assertTrue(entries[0].undone, "the original move entry must be flagged undone")


if __name__ == "__main__":
    unittest.main()
