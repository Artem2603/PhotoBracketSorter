from pathlib import Path
import tempfile
import unittest

from app.enums import DestinationCategory, OperationStatus
from app.gui_state import GuiState, UndoAction, UndoRedoStack
from app.i18n import I18n, available_languages, format_status, translate_category, validate_catalogs
from app.gui.main_window import FILTERS
from main import _i18n_self_test


class I18nAndGuiStateTests(unittest.TestCase):
    def test_catalogs_are_complete(self):
        ok, missing = validate_catalogs()
        self.assertTrue(ok, missing)

    def test_i18n_self_test_command_returns_zero(self):
        self.assertEqual(_i18n_self_test(), 0)

    def test_ru_and_en_action_labels_differ(self):
        self.assertNotEqual(I18n("ru").gettext("action.move"), I18n("en").gettext("action.move"))

    def test_category_translation_does_not_show_raw_enum_for_user_label(self):
        self.assertEqual(translate_category(DestinationCategory.BRACKETING_3, "en"), "HDR 3")
        self.assertEqual(translate_category(DestinationCategory.HEIC, "ru"), "Готовые фото")

    def test_delete_failed_status_is_localized(self):
        self.assertIn("не удалено", format_status(OperationStatus.DELETE_FAILED, "ru"))
        self.assertIn("not deleted", format_status(OperationStatus.DELETE_FAILED, "en"))

    def test_unsupported_language_falls_back_to_english(self):
        self.assertEqual(I18n("de").gettext("action.copy"), "Copy")

    def test_available_languages_are_stable(self):
        self.assertEqual(available_languages(), ("ru", "en"))

    def test_filter_keys_cover_prompt_filters(self):
        self.assertEqual(FILTERS, ("all", "ready", "raw", "hdr3", "hdr5", "review", "errors", "duplicates"))

    def test_gui_state_preview_token_increments(self):
        state = GuiState()
        self.assertEqual(state.next_preview_token(), 1)
        self.assertEqual(state.next_preview_token(), 2)

    def test_undo_redo_executes_actions(self):
        values: list[str] = []
        stack = UndoRedoStack(limit=30)
        stack.push(UndoAction("x", lambda: values.append("undo"), lambda: values.append("redo")))
        self.assertTrue(stack.can_undo())
        self.assertTrue(stack.undo())
        self.assertEqual(values, ["undo"])
        self.assertTrue(stack.can_redo())
        self.assertTrue(stack.redo())
        self.assertEqual(values, ["undo", "redo"])

    def test_undo_redo_limit_is_30(self):
        stack = UndoRedoStack(limit=30)
        for index in range(40):
            stack.push(UndoAction(str(index), lambda: None, lambda: None))
        self.assertEqual(stack.undo_count, 30)

    def test_new_action_clears_redo(self):
        stack = UndoRedoStack()
        stack.push(UndoAction("a", lambda: None, lambda: None))
        stack.undo()
        self.assertTrue(stack.can_redo())
        stack.push(UndoAction("b", lambda: None, lambda: None))
        self.assertFalse(stack.can_redo())

    def test_main_window_module_imports_without_display(self):
        from app.gui.main_window import MainWindow

        self.assertEqual(MainWindow.__name__, "MainWindow")

    def test_no_artifact_needed_for_i18n_temp_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "русский.txt"
            path.write_text(I18n("ru").gettext("action.copy"), encoding="utf-8")
            self.assertEqual(path.read_text(encoding="utf-8"), "Копировать")


if __name__ == "__main__":
    unittest.main()
