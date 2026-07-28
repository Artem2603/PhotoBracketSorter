"""GUI wiring for the sort-result preview dialog (feature 6, 1.2.0)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk

    from PIL import Image

    from app.gui.main_window import MainWindow

    _root_probe = tk.Tk()
    _root_probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - headless environments
    TK_AVAILABLE = False


def make_jpeg(path: Path, color=(128, 128, 128)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=color).save(path, format="JPEG")


@unittest.skipUnless(TK_AVAILABLE, "Tk/Pillow are not available in this environment")
class GuiSortPreviewTests(unittest.TestCase):
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
        self.source = Path(self._temp_dir.name) / "source"
        self.output = Path(self._temp_dir.name) / "output"
        self.source.mkdir()
        self.output.mkdir()

    def tearDown(self):
        if self.window._last_sort_preview_dialog is not None:
            try:
                self.window._last_sort_preview_dialog.destroy()
            except tk.TclError:
                pass
        self.window.destroy()

    def _analyze(self):
        from app.analysis import analyze_folder
        from app.series_manager import SeriesManager

        self.window.analysis = analyze_folder(self.source, self.window.settings, threading.Event())
        self.window.series_manager = SeriesManager.from_analysis(self.window.analysis)
        self.window.output_var.set(str(self.output))

    def test_opening_without_output_chosen_shows_an_error(self):
        make_jpeg(self.source / "a.jpg")
        self._analyze()
        self.window.output_var.set("")
        self.window._open_sort_preview()
        self.assertTrue(self.dialogs)
        self.assertEqual(self.dialogs[0][0], "error")

    def test_dialog_opens_and_lists_every_file(self):
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        self._analyze()
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        self.assertIsNotNone(dialog)
        self.assertEqual(len(dialog._node_files), 2)

    def test_exclude_action_removes_the_file_from_the_next_plan(self):
        make_jpeg(self.source / "a.jpg")
        make_jpeg(self.source / "b.jpg")
        self._analyze()
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        iid, file = next(iter(dialog._node_files.items()))
        dialog._tree.selection_set(iid)
        dialog._do_exclude()
        self.assertEqual(len(dialog._state["plan"].operations), 1)
        from app.enums import DestinationCategory

        self.assertIs(file.destination_category, DestinationCategory.SKIP)

    def test_change_category_updates_the_file_and_the_plan(self):
        from app.enums import DestinationCategory

        make_jpeg(self.source / "a.jpg")
        self._analyze()
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        iid, file = next(iter(dialog._node_files.items()))
        dialog._tree.selection_set(iid)
        dialog._do_change_category(DestinationCategory.SKIP)
        self.assertIs(file.destination_category, DestinationCategory.SKIP)
        self.assertEqual(len(dialog._state["plan"].operations), 0)

    def test_rename_series_via_the_dialog_updates_the_plan(self):
        from datetime import datetime, timedelta

        from app.enums import DetectedType
        from app.models import PhotoFile, PhotoMetadata

        base = datetime(2026, 7, 1, 12, 0, 0)
        for index in range(3):
            name = f"IMG_{index:04d}.dng"
            (self.source / name).write_bytes(b"\x00" * 32)
        self._analyze()
        raw_files = [file for file in self.window.analysis.files if file.metadata.extension == ".dng"]
        series = self.window.series_manager.create_manual(raw_files, 3)
        self.window.analysis.bracket_series = self.window.series_manager.series
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        self.assertIn(series.uid, dialog._node_series.values())
        folder_iid = next(iid for iid, uid in dialog._node_series.items() if uid == series.uid)
        dialog._tree.selection_set(folder_iid)
        with mock.patch.object(self.window, "_prompt_text", return_value="Sunset Beach"):
            dialog._do_rename_series()
        self.assertEqual(series.custom_name, "Sunset Beach")
        destinations = {str(operation.destination_path) for operation in dialog._state["plan"].operations}
        self.assertTrue(any("Sunset Beach" in d for d in destinations))

    def test_change_folder_updates_a_non_series_file(self):
        make_jpeg(self.source / "a.jpg")
        self._analyze()
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        iid, file = next(iter(dialog._node_files.items()))
        dialog._tree.selection_set(iid)
        with mock.patch.object(self.window, "_prompt_text", return_value="Custom/Folder"):
            dialog._do_change_folder()
        self.assertEqual(file.destination_subpath, Path("Custom/Folder") / file.metadata.file_name)

    def test_preview_only_dialog_has_no_transfer_buttons(self):
        make_jpeg(self.source / "a.jpg")
        self.window.settings.preview_only = True
        self._analyze()
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        all_text = _collect_button_texts(dialog)
        self.assertNotIn(self.window.i18n.gettext("sort_preview.copy"), all_text)
        self.assertNotIn(self.window.i18n.gettext("sort_preview.move"), all_text)

    def test_duplicate_behavior_change_rebuilds_the_plan_at_the_same_root(self):
        make_jpeg(self.source / "sub1" / "IMG_0001.jpg", color=(200, 200, 200))
        make_jpeg(self.source / "sub2" / "IMG_0001.jpg", color=(50, 50, 50))
        self._analyze()
        self.window._open_sort_preview()
        dialog = self.window._last_sort_preview_dialog
        original_root = dialog._state["plan"].output_root
        # Trigger a rebuild through the same path the combobox callback uses.
        from app.copy_plan import build_copy_plan

        self.window.settings.duplicate_behavior = "skip"
        dialog._state["plan"] = build_copy_plan(
            self.window.analysis, original_root.parent, self.window.settings, pinned_output_root=original_root
        )
        self.assertEqual(dialog._state["plan"].output_root, original_root)


def _collect_button_texts(widget) -> list[str]:
    texts = []
    try:
        texts.append(str(widget["text"]))
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        texts.extend(_collect_button_texts(child))
    return texts


if __name__ == "__main__":
    unittest.main()
