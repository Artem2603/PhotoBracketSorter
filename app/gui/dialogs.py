"""Session save/open and the About/help dialog (feature 10, 1.2.0).

First step of the gradual `main_window.py` split: a mixin holding
self-contained dialogs that do not depend on the analysis/copy/move worker
plumbing. `MainWindow` inherits from `DialogsMixin` unchanged — every method
here still expects the same `self.` attributes (settings, i18n, events
queue, worker thread) `main_window.py` already sets up, so no behavior
changes, only where the code lives.
"""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

from app.errors import SessionError
from app.gui.gui_constants import LANGUAGE_LABELS
from app.models import AnalysisResult, AppSettings
from app.series_manager import SeriesManager
from app.session import SESSION_FILE_EXTENSION, SessionData, build_session, load_session, restore_session, save_session
from app.theme_manager import apply_theme
from app.version import APP_NAME, __version__


class DialogsMixin:
    def _save_session(self) -> None:
        if self.analysis is None or self.series_manager is None:
            messagebox.showwarning(APP_NAME, self.i18n.gettext("session.nothing_to_save"))
            return
        path = filedialog.asksaveasfilename(
            defaultextension=SESSION_FILE_EXTENSION,
            filetypes=((self.i18n.gettext("session.file_type"), f"*{SESSION_FILE_EXTENSION}"),),
        )
        if not path:
            return
        output = self.output_var.get().strip()
        session = build_session(self.analysis, self.series_manager, self.settings, Path(output) if output else None)
        try:
            save_session(session, Path(path))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"{self.i18n.gettext('session.save_failed')}: {exc}")
            return
        self._log(f"{self.i18n.gettext('session.saved')}: {path}")

    def _start_open_session(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        path = filedialog.askopenfilename(filetypes=((self.i18n.gettext("session.file_type"), f"*{SESSION_FILE_EXTENSION}"),))
        if not path:
            return
        try:
            session = load_session(Path(path))
        except SessionError as exc:
            messagebox.showerror(APP_NAME, exc.user_message)
            return
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self.analysis = None
        self.copy_plan = None
        self.tree.delete(*self.tree.get_children())
        self._log(self.i18n.gettext("session.opening"))
        self.worker = threading.Thread(target=self._open_session_worker, args=(session,), daemon=True)
        self.worker.start()

    def _open_session_worker(self, session: SessionData) -> None:
        try:
            analysis, manager, settings, warnings = restore_session(session, self.cancel_event, self._worker_progress)
            self.events.put(("session_ready", (analysis, manager, settings, session, warnings)))
        except Exception as exc:
            self.events.put(("error", exc))

    def _on_session_ready(
        self, analysis: AnalysisResult, manager: SeriesManager, settings: AppSettings, session: SessionData, warnings: list[str]
    ) -> None:
        # The restored settings drive this analysis's classification; they are
        # applied in memory only, exactly like every other in-app settings
        # change, and are not written to settings.json until the user does
        # that explicitly through the Settings dialog.
        self.settings = settings
        self.i18n.set_language(self.settings.language)
        self.language_var.set(LANGUAGE_LABELS.get(self.settings.language, "English"))
        self.theme_var.set(self.settings.theme)
        apply_theme(self, self.settings)
        self.analysis = analysis
        self.series_manager = manager
        self.copy_plan = None
        self.source_var.set(session.source_root)
        if session.output_parent:
            self.output_var.set(session.output_parent)
        self._refresh_texts()
        self._populate_table()
        self._update_summary()
        self._set_busy(False)
        self.copy_button.configure(state="normal")
        self.move_button.configure(state="normal")
        self.review_queue_button.configure(state="normal")
        self.sort_preview_button.configure(state="normal")
        self.save_session_button.configure(state="normal")
        self._log(self.i18n.gettext("session.opened"))
        if warnings:
            messagebox.showwarning(APP_NAME, "\n".join(warnings))

    def _show_help(self) -> None:
        messagebox.showinfo(
            APP_NAME, f"{APP_NAME} {__version__}\n\nCopy keeps originals. Move deletes originals only after size and SHA-256 verification."
        )
