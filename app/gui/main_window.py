"""Tkinter GUI for Photo Bracket Sorter."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from app.analysis import analyze_folder, collect_candidate_paths
from app.auto_configure import AutoConfigReport, analyze_for_suggestions, apply_suggestions
from app.camera_profiles import user_profiles_path
from app.copy_engine import CopyEngine
from app.copy_plan import build_copy_plan
from app.enums import DestinationCategory
from app.errors import ManualSeriesError, PhotoBracketSorterError, SettingsError
from app.exiftool_runner import ExifToolRunner
from app.gui.dialogs import DialogsMixin
from app.gui.gui_constants import LANGUAGE_CODES, LANGUAGE_LABELS
from app.gui.settings_view import SettingsViewMixin
from app.gui_state import GuiState, UndoAction, UndoRedoStack
from app.i18n import I18n
from app.manual_overrides import assign_category
from app.metadata import MetadataReader
from app.models import AnalysisResult, AppSettings, CopyPlan, PhotoFile
from app.move_engine import MoveEngine
from app.operation_journal import (
    append_journal_entry,
    build_journal_entry,
    can_undo_move,
    journal_path,
    load_journal,
    mark_undone,
    undo_move,
)
from app.paths import validate_source
from app.preview_loader import PreviewCache, PreviewInfo, load_preview_info
from app.reporting import write_reports
from app.review_queue import ReviewItem, build_review_queue, confirm_candidate, dismiss_candidate, exclude_files, merge_series, restore_automatic, split_series
from app.series_manager import SeriesManager, compute_statistics
from app.settings import default_settings_path, load_settings, save_settings, validate_settings
from app.theme_manager import BOOTSTRAP_AVAILABLE, apply_theme, button_style, is_dark_theme, release_style
from app.version import APP_NAME, __version__


FILTERS = ("all", "ready", "raw", "hdr3", "hdr5", "review", "errors", "duplicates")
UI_MODE_KEYS = ("advanced", "simple")
THEME_KEYS = ("system", "light", "dark")
DEFAULT_GEOMETRY = "1440x900"
#: Stat cards double as filter shortcuts; each key is also a FILTERS entry.
STAT_KEYS = ("ready", "raw", "hdr3", "hdr5", "review", "errors")
#: Colour role per stat card / table row tag, resolved against the active theme.
STAT_ROLES = {"ready": "success", "raw": "info", "hdr3": "primary", "hdr5": "primary", "review": "warning", "errors": "danger"}
#: Fallback palette when ttkbootstrap is absent (plain ttk has no named colours).
FALLBACK_COLORS = {
    "primary": "#2c7be5",
    "success": "#1f9d55",
    "info": "#2a9fd6",
    "warning": "#d98c00",
    "danger": "#c0392b",
    "secondary": "#7f8c8d",
    "border": "#c8ccd0",
    "bg": "#f5f6f7",
}
#: Background-worker phase names (kept in Russian inside the core so reports
#: and logs stay stable) mapped to catalog keys for the status bar.
PHASE_KEYS = {
    "Проверка папок": "phase.check_folders",
    "Сканирование": "phase.scan",
    "Определение форматов": "phase.detect_formats",
    "Чтение EXIF": "phase.read_exif",
    "Чтение метаданных": "phase.read_metadata",
    "Поиск RAW + HEIC": "phase.pairing",
    "Готово к проверке": "phase.ready",
    "Создание структуры": "phase.create_structure",
    "Копирование": "phase.copying",
    "Удаление исходника": "phase.deleting_source",
    "Финальная проверка": "phase.final_check",
    "Восстановление": "phase.restoring",
}
#: Action buttons hidden in simple mode, leaving the 5-step flow: choose
#: source, choose output, analyze, review the table, Copy/Move.
ADVANCED_ONLY_ACTIONS = (
    "auto_configure_button",
    "review_queue_button",
    "sort_preview_button",
    "history_button",
    "open_session_button",
    "save_session_button",
)


class MainWindow(DialogsMixin, SettingsViewMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings_path = default_settings_path()
        self.profiles_path = user_profiles_path()
        self.journal_path = journal_path()
        self.settings, settings_problem = _load_settings_or_defaults(self.settings_path)
        self.i18n = I18n(self.settings.language)
        apply_theme(self, self.settings)
        self.title(f"{self.i18n.gettext('app.title')} {__version__}")
        self.geometry(self._initial_geometry(self.settings.window_geometry or DEFAULT_GEOMETRY))
        self.minsize(1024, 680)
        self.tk.call("tk", "scaling", self.settings.ui_scale)

        self.analysis: AnalysisResult | None = None
        self.series_manager: SeriesManager | None = None
        self.copy_plan: CopyPlan | None = None
        self.last_result_root: Path | None = None
        self._last_series_dialog: tk.Toplevel | None = None
        self._last_sort_preview_dialog: tk.Toplevel | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.preview_image = None
        self.preview_cache = PreviewCache(self.settings.preview_cache_items)
        self.gui_state = GuiState()
        self.undo_stack = UndoRedoStack(30)
        self.localized: list[tuple[Any, str, str]] = []
        self.sort_column = "time"
        self.sort_reverse = False

        self.source_var = tk.StringVar(value=self.settings.last_source_path if self.settings.remember_paths else "")
        self.output_var = tk.StringVar(value=self.settings.last_output_path if self.settings.remember_paths else "")
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value=self._filter_label("all"))
        self.language_var = tk.StringVar(value=LANGUAGE_LABELS.get(self.settings.language, "English"))
        self.theme_var = tk.StringVar(value=self._theme_label(self.settings.theme))
        self.ui_mode_var = tk.StringVar(value=self._ui_mode_label(self.settings.ui_mode))
        self.status_var = tk.StringVar(value=self.i18n.gettext("app.ready"))
        self.free_space_var = tk.StringVar(value="")
        self.exiftool_var = tk.StringVar(value=self._exiftool_status())
        self.stats_vars = {key: tk.StringVar(value="0") for key in STAT_KEYS}
        self.stat_cards: dict[str, tk.Frame] = {}
        self.stat_numbers: dict[str, ttk.Label] = {}

        self._build()
        self._bind_shortcuts()
        self._refresh_texts()
        self._apply_theme_colors()
        self._update_free_space()
        self.after(100, self._poll_events)
        if settings_problem:
            self.after(200, lambda: self._report_settings_problem(settings_problem))

    def destroy(self) -> None:
        # The ttkbootstrap Style singleton is bound to this interpreter; drop it
        # with the window so a later MainWindow in the same process starts clean.
        try:
            super().destroy()
        finally:
            release_style()

    def _initial_geometry(self, geometry: str) -> str:
        """Clamp a remembered geometry to the current screen.

        A window saved on a 1440p monitor must not open larger than a laptop
        screen, and an offset that belonged to a monitor that is no longer
        attached is dropped so the window cannot open off-screen.
        """
        return clamp_geometry(geometry, self.winfo_screenwidth(), self.winfo_screenheight())

    def _report_settings_problem(self, reason: str) -> None:
        message = self.i18n.gettext("message.settings_reset", path=str(self.settings_path), reason=reason)
        self._log(message)
        messagebox.showwarning(APP_NAME, message)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._build_top_bar()
        self._build_paths()
        self._build_actions()
        self._build_stats_and_filters()
        self._build_content()
        self._build_status()
        self._apply_ui_mode()

    def _build_top_bar(self) -> None:
        frame = ttk.Frame(self, padding=(12, 10, 12, 6))
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)
        title = ttk.Label(frame, text=f"{self.i18n.gettext('app.title')} {__version__}", font=("Segoe UI", 14, "bold"))
        title.grid(row=0, column=0, sticky="w")
        exif = ttk.Label(frame, textvariable=self.exiftool_var)
        exif.grid(row=0, column=1, sticky="w", padx=(18, 0))
        self._label(frame, "top.language").grid(row=0, column=2, padx=(12, 4))
        language = ttk.Combobox(frame, textvariable=self.language_var, values=tuple(LANGUAGE_LABELS.values()), width=10, state="readonly")
        language.grid(row=0, column=3)
        language.bind("<<ComboboxSelected>>", lambda _event: self._change_language())
        self._label(frame, "top.theme").grid(row=0, column=4, padx=(12, 4))
        self.theme_combo = ttk.Combobox(
            frame, textvariable=self.theme_var, values=tuple(self._theme_label(key) for key in THEME_KEYS), width=11, state="readonly"
        )
        self.theme_combo.grid(row=0, column=5)
        self.theme_combo.bind("<<ComboboxSelected>>", lambda _event: self._change_theme())
        self._label(frame, "top.ui_mode").grid(row=0, column=7, padx=(12, 4))
        self.ui_mode_combo = ttk.Combobox(
            frame, textvariable=self.ui_mode_var, values=tuple(self._ui_mode_label(key) for key in UI_MODE_KEYS), width=14, state="readonly"
        )
        self.ui_mode_combo.grid(row=0, column=8)
        self.ui_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._change_ui_mode())
        self.help_button = self._button(frame, "top.help", self._show_help)
        self.help_button.grid(row=0, column=9, padx=(10, 0))

    def _build_paths(self) -> None:
        frame = ttk.Frame(self, padding=(12, 4, 12, 6))
        frame.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(6, weight=1)
        self._label(frame, "path.source").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.source_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._button(frame, "path.browse", self._browse_source).grid(row=1, column=2, padx=(6, 0))
        self._button(frame, "path.clear", lambda: self.source_var.set("")).grid(row=1, column=3, padx=(6, 0))
        self._button(frame, "path.open", lambda: self._open_path(self.source_var.get())).grid(row=1, column=4, padx=(6, 16))

        self._label(frame, "path.output").grid(row=0, column=5, sticky="w")
        ttk.Entry(frame, textvariable=self.output_var).grid(row=1, column=5, columnspan=2, sticky="ew", pady=(2, 0))
        self._button(frame, "path.browse", self._browse_output).grid(row=1, column=7, padx=(6, 0))
        self._button(frame, "path.clear", lambda: self.output_var.set("")).grid(row=1, column=8, padx=(6, 0))
        self._button(frame, "path.open", lambda: self._open_path(self.output_var.get())).grid(row=1, column=9, padx=(6, 0))
        ttk.Label(frame, textvariable=self.free_space_var).grid(row=2, column=5, columnspan=5, sticky="w", pady=(4, 0))
        self.output_var.trace_add("write", lambda *_args: self._update_free_space())

    def _build_actions(self) -> None:
        frame = ttk.Frame(self, padding=(12, 4, 12, 8))
        frame.grid(row=2, column=0, sticky="ew")
        # Row 0 is the workflow (analyze, refine, transfer); row 1 holds the
        # utilities. Thirteen equal buttons in one row overflowed a 1440px
        # window in Russian and gave Copy and Move no visual weight.
        self.analyze_button = self._button(frame, "action.analyze", self._start_analysis, role="primary")
        self.auto_configure_button = self._button(frame, "action.auto_configure", self._start_auto_configure, role="outline")
        self.review_queue_button = self._button(frame, "action.review_queue", self._open_review_queue, state="disabled", role="outline")
        self.sort_preview_button = self._button(frame, "action.sort_preview", self._open_sort_preview, state="disabled", role="outline")
        self.copy_button = self._button(frame, "action.copy", lambda: self._start_transfer("copy"), state="disabled", role="copy")
        self.move_button = self._button(frame, "action.move", lambda: self._start_transfer("move"), state="disabled", role="move")
        self.cancel_button = self._button(frame, "action.cancel", self._cancel, state="disabled", role="danger")
        self.reset_button = self._button(frame, "action.reset", self._reset, role="secondary")
        self.open_result_button = self._button(frame, "action.open_result", self._open_last_result, state="disabled", role="secondary")
        self.settings_button = self._button(frame, "action.settings", self._open_settings, role="secondary")
        self.history_button = self._button(frame, "action.history", self._open_history, role="secondary")
        self.save_session_button = self._button(frame, "action.save_session", self._save_session, state="disabled", role="secondary")
        self.open_session_button = self._button(frame, "action.open_session", self._start_open_session, role="secondary")
        rows = (
            (
                self.analyze_button,
                self.auto_configure_button,
                self.review_queue_button,
                self.sort_preview_button,
                self.copy_button,
                self.move_button,
                self.cancel_button,
            ),
            (
                self.reset_button,
                self.open_result_button,
                self.settings_button,
                self.history_button,
                self.save_session_button,
                self.open_session_button,
            ),
        )
        for row, buttons in enumerate(rows):
            for column, button in enumerate(buttons):
                button.grid(row=row, column=column, padx=(0, 8), pady=(0, 6) if row == 0 else 0, sticky="w")

    def _build_stats_and_filters(self) -> None:
        frame = ttk.Frame(self, padding=(12, 0, 12, 8))
        frame.grid(row=3, column=0, sticky="ew")
        for index, key in enumerate(STAT_KEYS):
            # A thin-bordered tk.Frame reads as a card on every theme; clicking
            # it applies the matching table filter.
            card = tk.Frame(frame, highlightthickness=1, bd=0, padx=12, pady=6, cursor="hand2")
            card.grid(row=0, column=index, sticky="ew", padx=(0, 8), ipadx=4)
            number = ttk.Label(card, textvariable=self.stats_vars[key], font=("Segoe UI", 15, "bold"), cursor="hand2")
            number.pack(anchor="w")
            label = ttk.Label(card, cursor="hand2")
            label.pack(anchor="w")
            self.localized.append((label, "text", f"filter.{key}"))
            for widget in (card, number, label):
                widget.bind("<Button-1>", lambda _event, filter_key=key: self._select_filter(filter_key))
            self.stat_cards[key] = card
            self.stat_numbers[key] = number
        filter_frame = ttk.Frame(frame)
        filter_frame.grid(row=0, column=6, sticky="e")
        values = tuple(self._filter_label(key) for key in FILTERS)
        self.filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, values=values, width=18, state="readonly")
        self.filter_combo.grid(row=0, column=0, padx=(12, 6))
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._populate_table())
        self._label(filter_frame, "search.label").grid(row=0, column=1, padx=(0, 4))
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=28)
        search_entry.grid(row=0, column=2)
        search_entry.bind("<KeyRelease>", lambda _event: self._populate_table())

    def _build_content(self) -> None:
        content = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        content.grid(row=4, column=0, sticky="nsew", padx=12)
        table_frame = ttk.Frame(content)
        preview_frame = ttk.Frame(content)
        content.add(table_frame, weight=4)
        content.add(preview_frame, weight=1)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("selected", "icon", "category", "confidence", "type", "time", "size", "series", "group", "warnings")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text=self.i18n.gettext("table.file"), command=lambda: self._sort_by("file"))
        headings = {
            "selected": "table.selected",
            "icon": "table.icon",
            "category": "table.category",
            "confidence": "table.confidence",
            "type": "table.detected_type",
            "time": "table.time",
            "size": "table.size",
            "series": "table.series",
            "group": "table.group",
            "warnings": "table.warnings",
        }
        for column, key in headings.items():
            self.tree.heading(column, text=self.i18n.gettext(key), command=lambda col=column: self._sort_by(col))
        default_widths = {
            "#0": 340,
            "selected": 70,
            "icon": 70,
            "category": 140,
            "confidence": 110,
            "type": 80,
            "time": 175,
            "size": 100,
            "series": 110,
            "group": 140,
            "warnings": 240,
        }
        anchors = {
            "selected": "center",
            "icon": "center",
            "category": "center",
            "confidence": "center",
            "type": "center",
            "time": "center",
            "size": "e",
            "series": "center",
            "group": "w",
            "warnings": "w",
        }
        fixed = {"selected", "icon", "confidence", "type", "size"}
        # Saved widths win over the defaults, so column_widths survives a restart.
        saved = self.settings.column_widths or {}
        self.tree.column("#0", width=int(saved.get("#0", default_widths["#0"])), minwidth=220)
        for column in columns:
            self.tree.column(
                column,
                width=int(saved.get(column, default_widths[column])),
                anchor=anchors[column],
                stretch=column not in fixed,
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_preview())
        self.tree.bind("<Button-3>", self._show_context_menu)

        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.preview_tabs = ttk.Notebook(preview_frame)
        self.preview_tabs.grid(row=0, column=0, sticky="nsew")
        preview_tab = ttk.Frame(self.preview_tabs, padding=8)
        metadata_tab = ttk.Frame(self.preview_tabs, padding=8)
        reasons_tab = ttk.Frame(self.preview_tabs, padding=8)
        self.preview_tabs.add(preview_tab, text=self.i18n.gettext("preview.tab.preview"))
        self.preview_tabs.add(metadata_tab, text=self.i18n.gettext("preview.tab.metadata"))
        self.preview_tabs.add(reasons_tab, text=self.i18n.gettext("preview.tab.reasons"))
        preview_tab.rowconfigure(0, weight=1)
        preview_tab.columnconfigure(0, weight=1)
        self.preview_label = ttk.Label(preview_tab, anchor="center", text=self.i18n.gettext("preview.empty"), wraplength=320)
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_details = ttk.Label(preview_tab, anchor="w", justify="left", wraplength=320)
        self.preview_details.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.metadata_text = tk.Text(metadata_tab, height=12, wrap="word")
        self.metadata_text.pack(fill="both", expand=True)
        self.reasons_text = tk.Text(reasons_tab, height=12, wrap="word")
        self.reasons_text.pack(fill="both", expand=True)

    def _build_status(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.grid(row=5, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(frame, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.log_text = tk.Text(frame, height=5, wrap="word")
        self.log_text.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _event: self._browse_source())
        self.bind("<Control-Shift-O>", lambda _event: self._browse_output())
        self.bind("<F5>", lambda _event: self._start_analysis())
        self.bind("<Control-m>", lambda _event: self._start_transfer("move"))
        self.bind("<Control-z>", lambda _event: self._undo())
        self.bind("<Control-y>", lambda _event: self._redo())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _label(self, parent, key: str) -> ttk.Label:
        widget = ttk.Label(parent)
        self.localized.append((widget, "text", key))
        return widget

    def _button(self, parent, key: str, command, state: str = "normal", role: str | None = None) -> ttk.Button:
        style = button_style(self.settings, role) if role else None
        widget = ttk.Button(parent, command=command, state=state, style=style) if style else ttk.Button(parent, command=command, state=state)
        self.localized.append((widget, "text", key))
        return widget

    def _theme_label(self, key: str) -> str:
        return self.i18n.gettext(f"theme.{key}")

    def _current_theme_key(self) -> str:
        label = self.theme_var.get()
        for key in THEME_KEYS:
            if self._theme_label(key) == label:
                return key
        return label if label in THEME_KEYS else "system"

    def _theme_color(self, role: str) -> str:
        """Resolve a bootstrap colour role against the active theme (or the fallback palette)."""
        if BOOTSTRAP_AVAILABLE:
            try:
                import ttkbootstrap as tb

                colors = tb.Style.instance.colors if tb.Style.instance is not None else None
                value = colors.get(role) if colors is not None else None
                if value:
                    return str(value)
            except Exception:
                pass
        return FALLBACK_COLORS.get(role, FALLBACK_COLORS["primary"])

    def _apply_theme_colors(self) -> None:
        """Re-colour the pieces that do not follow the ttk theme automatically."""
        dark = is_dark_theme(self.settings)
        background = self._theme_color("bg")
        border = self._theme_color("border") if BOOTSTRAP_AVAILABLE else ("#4a4f55" if dark else FALLBACK_COLORS["border"])
        for key, card in self.stat_cards.items():
            try:
                card.configure(background=background, highlightbackground=border, highlightcolor=border)
                self.stat_numbers[key].configure(foreground=self._theme_color(STAT_ROLES[key]))
            except tk.TclError:
                continue
        if hasattr(self, "tree"):
            self._configure_row_tags()
        for widget in (getattr(self, "log_text", None), getattr(self, "metadata_text", None), getattr(self, "reasons_text", None)):
            if widget is None:
                continue
            try:
                widget.configure(
                    background=self._theme_color("inputbg") if BOOTSTRAP_AVAILABLE else ("#1e1e1e" if dark else "white"),
                    foreground=self._theme_color("inputfg") if BOOTSTRAP_AVAILABLE else ("#e6e6e6" if dark else "black"),
                    insertbackground=self._theme_color("inputfg") if BOOTSTRAP_AVAILABLE else ("#e6e6e6" if dark else "black"),
                    relief="flat",
                    highlightthickness=1,
                    highlightbackground=border,
                    highlightcolor=border,
                )
            except tk.TclError:
                continue

    def _configure_row_tags(self) -> None:
        """Row colours by outcome: errors red, review amber, series accent, skipped muted."""
        self.tree.tag_configure("error", foreground=self._theme_color("danger"))
        self.tree.tag_configure("review", foreground=self._theme_color("warning"))
        self.tree.tag_configure("series", foreground=self._theme_color("primary"))
        self.tree.tag_configure("skip", foreground=self._theme_color("secondary"))

    def _select_filter(self, key: str) -> None:
        """Stat-card click: apply that filter, or clear it when it is already active."""
        target = "all" if self._current_filter_key() == key else key
        self.filter_var.set(self._filter_label(target))
        self._populate_table()

    def _refresh_texts(self) -> None:
        self.title(f"{self.i18n.gettext('app.title')} {__version__}")
        for widget, option, key in self.localized:
            try:
                widget.configure(**{option: self.i18n.gettext(key)})
            except tk.TclError:
                continue
        if hasattr(self, "preview_tabs"):
            for index, key in enumerate(("preview.tab.preview", "preview.tab.metadata", "preview.tab.reasons")):
                self.preview_tabs.tab(index, text=self.i18n.gettext(key))
        if hasattr(self, "tree"):
            self.tree.heading("#0", text=self.i18n.gettext("table.file"))
            for column, key in {
                "selected": "table.selected",
                "icon": "table.icon",
                "category": "table.category",
                "confidence": "table.confidence",
                "type": "table.detected_type",
                "time": "table.time",
                "size": "table.size",
                "series": "table.series",
                "group": "table.group",
                "warnings": "table.warnings",
            }.items():
                self.tree.heading(column, text=self.i18n.gettext(key))
        if hasattr(self, "filter_combo"):
            current_key = self._current_filter_key()
            values = tuple(self._filter_label(key) for key in FILTERS)
            self.filter_combo.configure(values=values)
            self.filter_var.set(self._filter_label(current_key))
        if hasattr(self, "ui_mode_combo"):
            # Re-derived from self.settings.ui_mode (the authoritative value),
            # not from the still-old-language label currently on screen.
            self.ui_mode_combo.configure(values=tuple(self._ui_mode_label(key) for key in UI_MODE_KEYS))
            self.ui_mode_var.set(self._ui_mode_label(self.settings.ui_mode))
        if hasattr(self, "theme_combo"):
            self.theme_combo.configure(values=tuple(self._theme_label(key) for key in THEME_KEYS))
            self.theme_var.set(self._theme_label(self.settings.theme))
        self._update_summary()

    def _change_language(self) -> None:
        language = LANGUAGE_CODES.get(self.language_var.get(), "en")
        self.settings.language = language
        self.settings.report_language = language
        self.i18n.set_language(language)
        self._refresh_texts()
        self._save_settings_quietly()

    def _change_theme(self) -> None:
        self.settings.theme = self._current_theme_key()
        apply_theme(self, self.settings)
        self._apply_theme_colors()
        self._save_settings_quietly()

    def _ui_mode_label(self, key: str) -> str:
        return self.i18n.gettext(f"ui_mode.{key}")

    def _current_ui_mode_key(self) -> str:
        label = self.ui_mode_var.get()
        for key in UI_MODE_KEYS:
            if self._ui_mode_label(key) == label:
                return key
        return "advanced"

    def _change_ui_mode(self) -> None:
        self.settings.ui_mode = self._current_ui_mode_key()
        self._apply_ui_mode()
        self._save_settings_quietly()

    def _apply_ui_mode(self) -> None:
        """Simple mode keeps only the 5-step flow (source, output, analyze,
        review the table, Copy/Move) visible; advanced mode is the full
        interface, unchanged. Nothing here disables or skips logic — it only
        shows or hides buttons and settings tabs that were always there."""
        simple = self.settings.ui_mode == "simple"
        for name in ADVANCED_ONLY_ACTIONS:
            button = getattr(self, name)
            if simple:
                button.grid_remove()
            else:
                button.grid()

    def _browse_source(self) -> None:
        folder = filedialog.askdirectory(title=self.i18n.gettext("path.source"), initialdir=_existing_dir(self.source_var.get()))
        if folder:
            self.source_var.set(folder)
            self.settings.last_source_path = folder
            self._save_settings_quietly()

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title=self.i18n.gettext("path.output"), initialdir=_existing_dir(self.output_var.get()))
        if folder:
            self.output_var.set(folder)
            self.settings.last_output_path = folder
            self._update_free_space()
            self._save_settings_quietly()

    def _start_analysis(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        source = self.source_var.get().strip()
        if not source:
            messagebox.showerror(APP_NAME, self.i18n.gettext("message.choose_source"))
            return
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self.analysis = None
        self.copy_plan = None
        self.tree.delete(*self.tree.get_children())
        self._log(self.i18n.gettext("message.analysis_started"))
        self.worker = threading.Thread(target=self._analysis_worker, args=(Path(source),), daemon=True)
        self.worker.start()

    def _analysis_worker(self, source: Path) -> None:
        try:
            result = analyze_folder(source, self.settings, self.cancel_event, self._worker_progress)
            self.events.put(("analysis_done", result))
        except Exception as exc:
            self.events.put(("error", exc))

    def _start_auto_configure(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        source = self.source_var.get().strip()
        if not source:
            messagebox.showerror(APP_NAME, self.i18n.gettext("auto_config.no_source"))
            return
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self._log(self.i18n.gettext("auto_config.scanning"))
        self.worker = threading.Thread(target=self._auto_configure_worker, args=(Path(source),), daemon=True)
        self.worker.start()

    def _auto_configure_worker(self, source: Path) -> None:
        # Only reads metadata; it must never classify or move anything, so a
        # suggestion pass can run before the user trusts the current settings
        # enough to click Analyze.
        try:
            validated = validate_source(source)
            paths = collect_candidate_paths(validated, self.settings, self.cancel_event, self._worker_progress)
            if self.cancel_event.is_set():
                self.events.put(("log", self.i18n.gettext("message.cancel_requested")))
                self.events.put(("auto_config_cancelled", None))
                return
            metadata_items = MetadataReader.from_settings(self.settings).read_many(
                paths,
                validated,
                self.cancel_event,
                lambda done, total: self._worker_progress("Чтение метаданных", done, total),
                self.settings,
                exif_progress=lambda done, total: self._worker_progress("Чтение EXIF", done, total),
            )
            report = self._build_auto_config_report(metadata_items)
            self.events.put(("auto_config_ready", report))
        except Exception as exc:
            self.events.put(("error", exc))

    def _build_auto_config_report(self, metadata_items) -> AutoConfigReport:
        """Pure wrapper so tests can build a report without a folder scan."""
        return analyze_for_suggestions(metadata_items, self.settings, self.settings.language)

    def _start_transfer(self, mode: str) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.analysis is None:
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror(APP_NAME, self.i18n.gettext("message.choose_output"))
            return
        try:
            plan = build_copy_plan(self.analysis, Path(output), self.settings)
        except PhotoBracketSorterError as exc:
            messagebox.showerror(APP_NAME, exc.user_message)
            return
        if not plan.operations:
            messagebox.showwarning(APP_NAME, self.i18n.gettext("message.no_files_to_copy"))
            return
        # preview_only is a dry run: show what would happen and touch nothing.
        if self.settings.preview_only:
            self._show_plan_preview(mode, plan)
            return
        if self.settings.require_plan_before_operation:
            if mode == "move" and not self._confirm_move(plan):
                return
            if mode == "copy" and not self._confirm_copy(plan):
                return
        self._launch_transfer(mode, plan)

    def _launch_transfer(self, mode: str, plan: CopyPlan) -> None:
        """Start the background Copy/Move worker for an already-approved plan.

        Shared by the quick confirm-dialog flow above and the sort-preview
        dialog's own Copy/Move buttons (feature 6), so there is exactly one
        place that flips busy state and starts the worker thread.
        """
        self.copy_plan = plan
        self.cancel_event = threading.Event()
        self._set_busy(True)
        self._log(self.i18n.gettext("message.move_started" if mode == "move" else "message.copy_started"))
        self.worker = threading.Thread(target=self._transfer_worker, args=(mode,), daemon=True)
        self.worker.start()

    def _transfer_worker(self, mode: str) -> None:
        if self.analysis is None or self.copy_plan is None:
            self.events.put(("error", RuntimeError("Operation plan is missing.")))
            return
        try:
            if mode == "move":
                result = MoveEngine(self.settings).execute(self.copy_plan, self.cancel_event, self._worker_progress)
            else:
                result = CopyEngine(self.settings).execute(self.copy_plan, self.cancel_event, self._worker_progress)
            # Written for cancelled runs too: a half-finished move is precisely when
            # the user needs to know what was copied and what was deleted.
            try:
                write_reports(self.analysis, result, self.settings)
            except Exception as report_error:
                self.events.put(("log", f"{self.i18n.gettext('message.report_failed')}: {report_error}"))
            try:
                entry = build_journal_entry(result, self.analysis.source_root)
                append_journal_entry(entry, self.journal_path)
            except Exception as journal_error:
                self.events.put(("log", f"Journal: {journal_error}"))
            self.events.put(("transfer_done", (mode, result)))
        except Exception as exc:
            self.events.put(("error", exc))

    def _show_plan_preview(self, mode: str, plan: CopyPlan) -> None:
        """Dry-run view for preview_only: report the plan, write nothing."""
        counts = "\n".join(f"  {name}: {count}" for name, count in sorted(plan.expected_counts.items()))
        lines = [
            self.i18n.gettext("message.preview_only_active"),
            "",
            f"{self.i18n.gettext('action.move' if mode == 'move' else 'action.copy')}: {len(plan.operations)}",
            f"{self.i18n.gettext('table.size')}: {_format_size(plan.total_bytes)}",
            f"{self.i18n.gettext('path.output')}: {plan.output_root}",
            "",
            counts,
        ]
        if plan.warnings:
            lines.extend(["", *plan.warnings])
        self._log(self.i18n.gettext("message.preview_only_active"))
        messagebox.showinfo(APP_NAME, "\n".join(lines))

    def _confirm_copy(self, plan: CopyPlan) -> bool:
        message = "\n".join(
            [
                f"{self.i18n.gettext('action.copy')}: {len(plan.operations)}",
                f"{self.i18n.gettext('table.size')}: {_format_size(plan.total_bytes)}",
                f"{self.i18n.gettext('path.output')}: {plan.output_root}",
                "",
                self.i18n.gettext("message.copy_done"),
            ]
        )
        if plan.warnings:
            message += "\n\n" + "\n".join(plan.warnings)
        return messagebox.askyesno(APP_NAME, message)

    def _confirm_move(self, plan: CopyPlan) -> bool:
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("move.title"))
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        accepted = tk.BooleanVar(value=False)
        understood = tk.BooleanVar(value=False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.i18n.gettext("move.warning"), wraplength=460, justify="left").pack(anchor="w")
        ttk.Label(
            body,
            text=self.i18n.gettext("move.summary", count=len(plan.operations), size=_format_size(plan.total_bytes)),
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(body, text=str(plan.output_root), wraplength=460, justify="left").pack(anchor="w", pady=(4, 8))
        ttk.Checkbutton(body, text=self.i18n.gettext("move.checkbox"), variable=understood).pack(anchor="w", pady=(4, 8))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))

        def accept() -> None:
            if understood.get():
                accepted.set(True)
                dialog.destroy()
            else:
                messagebox.showwarning(APP_NAME, self.i18n.gettext("move.checkbox"))

        ttk.Button(buttons, text=self.i18n.gettext("move.cancel"), command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text=self.i18n.gettext("move.start"), command=accept).pack(side="right", padx=(0, 8))
        self.wait_window(dialog)
        return accepted.get()

    def _open_sort_preview(self) -> None:
        """Feature 6: the full destination tree before Copy/Move, editable in place."""
        if self.analysis is None:
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror(APP_NAME, self.i18n.gettext("message.choose_output"))
            return
        try:
            plan = build_copy_plan(self.analysis, Path(output), self.settings)
        except PhotoBracketSorterError as exc:
            messagebox.showerror(APP_NAME, exc.user_message)
            return
        if not plan.operations:
            messagebox.showwarning(APP_NAME, self.i18n.gettext("message.no_files_to_copy"))
            return
        self._show_sort_preview_dialog(plan)

    def _free_space_for(self, output_root: Path) -> int | None:
        try:
            return shutil.disk_usage(output_root.parent).free
        except OSError:
            return None

    def _show_sort_preview_dialog(self, plan: CopyPlan) -> None:
        """Build-and-refresh loop: every edit rebuilds the plan and redraws the
        tree, so the preview never shows a stale plan next to live file state."""
        from app.sort_preview import build_sort_preview

        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("sort_preview.title"))
        dialog.geometry("920x640")
        dialog.minsize(760, 480)
        dialog.transient(self)

        state = {"plan": plan}
        node_files: dict[str, object] = {}
        node_series: dict[str, object] = {}

        body = ttk.Frame(dialog, padding=10)
        body.pack(fill="both", expand=True)

        summary_var = tk.StringVar()
        ttk.Label(body, textvariable=summary_var, wraplength=880, justify="left").pack(anchor="w")
        warnings_var = tk.StringVar()
        warnings_label = ttk.Label(body, textvariable=warnings_var, wraplength=880, justify="left", foreground="#c0392b")
        warnings_label.pack(anchor="w", pady=(2, 6))

        top_controls = ttk.Frame(body)
        top_controls.pack(fill="x", pady=(0, 6))
        ttk.Label(top_controls, text=self.i18n.gettext("sort_preview.duplicate_behavior")).pack(side="left")
        behavior_var = tk.StringVar(value=self.settings.duplicate_behavior)
        behavior_combo = ttk.Combobox(top_controls, textvariable=behavior_var, values=("rename", "skip", "overwrite"), state="readonly", width=10)
        behavior_combo.pack(side="left", padx=(6, 0))

        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill="both", expand=True)
        columns = ("category", "size")
        tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        tree.heading("#0", text=self.i18n.gettext("sort_preview.column.name"))
        tree.heading("category", text=self.i18n.gettext("sort_preview.column.category"))
        tree.heading("size", text=self.i18n.gettext("sort_preview.column.size"))
        tree.column("category", width=130, anchor="center")
        tree.column("size", width=100, anchor="e")
        tree.pack(fill="both", expand=True, side="left")
        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        y_scroll.pack(fill="y", side="right")
        tree.configure(yscrollcommand=y_scroll.set)

        def rebuild_plan() -> None:
            # Pinned to the ORIGINAL output_root: re-planning must reflect edits
            # (category, exclusions, series rename, duplicate policy), never mint
            # a fresh timestamped destination folder mid-edit.
            output_root = state["plan"].output_root
            self.settings.duplicate_behavior = behavior_var.get()
            state["plan"] = build_copy_plan(self.analysis, output_root.parent, self.settings, pinned_output_root=output_root)

        def populate() -> None:
            tree.delete(*tree.get_children())
            node_files.clear()
            node_series.clear()
            preview = build_sort_preview(self.analysis, state["plan"], self._free_space_for(state["plan"].output_root))
            summary_var.set(
                self.i18n.gettext(
                    "sort_preview.summary",
                    files=preview.total_files,
                    size=_format_size(preview.total_size),
                    required=_format_size(preview.required_space),
                    free=_format_size(preview.free_space) if preview.free_space is not None else "-",
                )
            )
            extra = []
            if not preview.has_enough_space:
                extra.append(self.i18n.gettext("sort_preview.not_enough_space"))
            if preview.conflicts:
                extra.append(self.i18n.gettext("sort_preview.conflicts", count=len(preview.conflicts)))
            if preview.excluded:
                extra.append(self.i18n.gettext("sort_preview.excluded", count=len(preview.excluded)))
            extra.extend(preview.warnings)
            warnings_var.set(" · ".join(extra))

            def insert_folder(parent_iid: str, folder) -> None:
                for sub in folder.folders:
                    iid = f"folder:{sub.relative_path}"
                    tree.insert(parent_iid, "end", iid=iid, text=sub.name, values=("", _format_size(sub.total_size)), open=True)
                    if sub.series_uid is not None:
                        node_series[iid] = sub.series_uid
                    insert_folder(iid, sub)
                for file in folder.files:
                    iid = f"file:{id(file.file)}"
                    tree.insert(parent_iid, "end", iid=iid, text=file.source_name, values=(file.category, _format_size(file.size)))
                    node_files[iid] = file.file

            insert_folder("", preview.root)

        def on_behavior_change(_event=None) -> None:
            rebuild_plan()
            populate()

        behavior_combo.bind("<<ComboboxSelected>>", on_behavior_change)

        def selected_iid() -> str | None:
            selection = tree.selection()
            return selection[0] if selection else None

        def do_exclude() -> None:
            iid = selected_iid()
            file = node_files.get(iid) if iid else None
            if file is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("sort_preview.select_item"))
                return
            if self.series_manager is not None:
                self.series_manager.dissolve_containing([file])
            assign_category(file, DestinationCategory.SKIP)
            self._after_manual_change()
            rebuild_plan()
            populate()

        def do_change_category(category: DestinationCategory) -> None:
            iid = selected_iid()
            file = node_files.get(iid) if iid else None
            if file is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("sort_preview.select_item"))
                return
            if self.series_manager is not None:
                self.series_manager.dissolve_containing([file])
            assign_category(file, category)
            self._after_manual_change()
            rebuild_plan()
            populate()

        def do_rename_series() -> None:
            iid = selected_iid()
            series_uid = node_series.get(iid) if iid else None
            if series_uid is None or self.series_manager is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("sort_preview.not_a_series_folder"))
                return
            series = self.series_manager.find_by_uid(series_uid)
            if series is None:
                return
            name = self._prompt_text(self.i18n.gettext("sort_preview.rename_prompt"), initial=series.custom_name or "")
            if name is None:
                return
            try:
                self.series_manager.rename_series(series, name)
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            self._after_manual_change()
            rebuild_plan()
            populate()

        def do_change_folder() -> None:
            iid = selected_iid()
            file = node_files.get(iid) if iid else None
            if file is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("sort_preview.select_item"))
                return
            if file.series_id:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("sort_preview.not_a_series_folder"))
                return
            folder = self._prompt_text(self.i18n.gettext("sort_preview.folder_prompt"), initial=str(file.destination_subpath.parent))
            if folder is None:
                return
            file.destination_subpath = Path(folder) / file.metadata.file_name
            rebuild_plan()
            populate()

        def show_context_menu(event) -> None:
            row = tree.identify_row(event.y)
            if row:
                tree.selection_set(row)
            menu = tk.Menu(dialog, tearoff=False)
            if row in node_files:
                menu.add_command(label=self.i18n.gettext("sort_preview.exclude"), command=do_exclude)
                category_menu = tk.Menu(menu, tearoff=False)
                for category in (DestinationCategory.HEIC, DestinationCategory.RAW, DestinationCategory.SKIP, DestinationCategory.REVIEW):
                    category_menu.add_command(label=self.i18n.category(category), command=lambda c=category: do_change_category(c))
                menu.add_cascade(label=self.i18n.gettext("sort_preview.change_category"), menu=category_menu)
                menu.add_command(label=self.i18n.gettext("sort_preview.change_folder"), command=do_change_folder)
            elif row in node_series:
                menu.add_command(label=self.i18n.gettext("sort_preview.rename_series"), command=do_rename_series)
            if menu.index("end") is not None:
                menu.tk_popup(event.x_root, event.y_root)

        tree.bind("<Button-3>", show_context_menu)

        buttons = ttk.Frame(body, padding=(0, 8, 0, 0))
        buttons.pack(fill="x", side="bottom")

        def do_transfer(mode: str) -> None:
            dialog.destroy()
            self._launch_transfer(mode, state["plan"])

        if not self.settings.preview_only:
            ttk.Button(buttons, text=self.i18n.gettext("sort_preview.copy"), command=lambda: do_transfer("copy")).pack(side="left")
            ttk.Button(buttons, text=self.i18n.gettext("sort_preview.move"), command=lambda: do_transfer("move")).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("sort_preview.close"), command=dialog.destroy).pack(side="right")

        # Exposed so tests can drive edits without simulating right-clicks.
        dialog._tree = tree  # type: ignore[attr-defined]
        dialog._state = state  # type: ignore[attr-defined]
        dialog._node_files = node_files  # type: ignore[attr-defined]
        dialog._node_series = node_series  # type: ignore[attr-defined]
        dialog._do_exclude = do_exclude  # type: ignore[attr-defined]
        dialog._do_change_category = do_change_category  # type: ignore[attr-defined]
        dialog._do_rename_series = do_rename_series  # type: ignore[attr-defined]
        dialog._do_change_folder = do_change_folder  # type: ignore[attr-defined]
        dialog._populate = populate  # type: ignore[attr-defined]
        self._last_sort_preview_dialog = dialog

        populate()

    def _worker_progress(self, phase: str, done: int, total: int) -> None:
        self.events.put(("progress", (phase, done, total)))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                phase, done, total = payload
                self._set_progress(str(phase), int(done), int(total))
            elif event == "analysis_done":
                self.analysis = payload
                self.series_manager = SeriesManager.from_analysis(self.analysis)
                self._populate_table()
                self._update_summary()
                self._set_busy(False)
                self.copy_button.configure(state="normal")
                self.move_button.configure(state="normal")
                self.review_queue_button.configure(state="normal")
                self.sort_preview_button.configure(state="normal")
                self.save_session_button.configure(state="normal")
                self._log(self.i18n.gettext("message.analysis_done"))
            elif event == "session_ready":
                self._on_session_ready(*payload)
            elif event == "transfer_done":
                self._on_transfer_done(*payload)
            elif event == "auto_config_ready":
                self._set_busy(False)
                self._show_auto_config_dialog(payload)
            elif event == "auto_config_cancelled":
                self._set_busy(False)
            elif event == "log":
                self._log(str(payload))
            elif event == "series_preview_ready":
                series, previews, loading_dialog = payload
                loading_dialog.destroy()
                self._show_series_viewer_dialog(series, previews)
            elif event == "series_preview_failed":
                exc, loading_dialog = payload
                loading_dialog.destroy()
                self._handle_error(exc)
            elif event == "preview_ready":
                self._on_preview_ready(*payload)
            elif event == "undo_move_ready":
                entry, result, on_done = payload
                self._set_busy(False)
                self._on_undo_move_done(entry, result, on_done)
            elif event == "undo_move_blocked":
                self._set_busy(False)
                messagebox.showwarning(APP_NAME, self.i18n.gettext("history.undo_blocked") + "\n" + "\n".join(payload))
            elif event == "error":
                self._set_busy(False)
                self._handle_error(payload)
        self.after(100, self._poll_events)

    def _on_transfer_done(self, mode: str, result) -> None:
        self.last_result_root = result.output_root
        self.open_result_button.configure(state="normal")
        done_key = "message.move_done" if mode == "move" else "message.copy_done"
        headline = self.i18n.gettext("message.transfer_cancelled") if result.cancelled else self.i18n.gettext(done_key)
        self._log(f"{headline}: {result.output_root}")

        summary = [
            headline,
            "",
            f"{self.i18n.gettext('report.processed')}: {result.processed_files} / {len(result.operations)}",
            f"{self.i18n.gettext('action.copy')}: {result.copied_files}",
        ]
        if mode == "move":
            summary.append(f"{self.i18n.gettext('report.deleted')}: {result.deleted_sources}")
        summary.extend(
            [
                f"{self.i18n.gettext('report.skipped')}: {result.skipped_files}",
                f"{self.i18n.gettext('filter.errors')}: {result.errors}",
                "",
                str(result.output_root),
            ]
        )

        # A completed move invalidates the analysis outright: the sources it
        # describes no longer exist, so re-running against it would fail on every file.
        if mode == "move" and result.deleted_sources:
            self.analysis = None
            self.series_manager = None
            self.copy_plan = None
            self.undo_stack.clear()
            self.tree.delete(*self.tree.get_children())
            for value in self.stats_vars.values():
                value.set("0")
            summary.append("")
            summary.append(self.i18n.gettext("message.analysis_reset_after_move"))
            self._log(self.i18n.gettext("message.analysis_reset_after_move"))
        else:
            self.copy_plan = None

        self._set_busy(False)
        if result.cancelled or result.errors:
            messagebox.showwarning(APP_NAME, "\n".join(summary))
        else:
            messagebox.showinfo(APP_NAME, "\n".join(summary))

    def _set_progress(self, phase: str, done: int, total: int) -> None:
        label = self._phase_label(phase)
        if total > 1:
            label = f"{label} \u00b7 {min(done, total)} / {total}"
        self.status_var.set(label)
        self.progress["maximum"] = max(total, 1)
        self.progress["value"] = min(done, max(total, 1))

    def _phase_label(self, phase: str) -> str:
        """Translate a worker phase name; unknown phases are shown verbatim."""
        key = PHASE_KEYS.get(phase)
        if key:
            return self.i18n.gettext(key)
        for prefix, prefix_key in PHASE_KEYS.items():
            if phase.startswith(prefix + " "):
                return f"{self.i18n.gettext(prefix_key)} {phase[len(prefix) + 1:]}"
        return phase

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.analyze_button, self.auto_configure_button, self.reset_button, self.settings_button, self.open_session_button):
            button.configure(state=state)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        if busy:
            self.copy_button.configure(state="disabled")
            self.move_button.configure(state="disabled")
            self.review_queue_button.configure(state="disabled")
            self.sort_preview_button.configure(state="disabled")
            self.save_session_button.configure(state="disabled")
        elif self.analysis is not None:
            self.copy_button.configure(state="normal")
            self.move_button.configure(state="normal")
            self.review_queue_button.configure(state="normal")
            self.sort_preview_button.configure(state="normal")
            self.save_session_button.configure(state="normal")
        else:
            self.save_session_button.configure(state="disabled")

    def _cancel(self) -> None:
        self.cancel_event.set()
        self._log(self.i18n.gettext("message.cancel_requested"))

    def _reset(self) -> None:
        if self.analysis is not None and not messagebox.askyesno(APP_NAME, self.i18n.gettext("message.reset_confirm")):
            return
        self.analysis = None
        self.series_manager = None
        self.copy_plan = None
        self.undo_stack.clear()
        self.preview_cache.clear()
        self.tree.delete(*self.tree.get_children())
        self.metadata_text.delete("1.0", tk.END)
        self.reasons_text.delete("1.0", tk.END)
        self.preview_label.configure(image="", text=self.i18n.gettext("preview.empty"))
        for value in self.stats_vars.values():
            value.set("0")
        self.status_var.set(self.i18n.gettext("app.ready"))
        self.progress["value"] = 0
        self.copy_button.configure(state="disabled")
        self.move_button.configure(state="disabled")
        self.review_queue_button.configure(state="disabled")
        self.sort_preview_button.configure(state="disabled")
        self.save_session_button.configure(state="disabled")

    def _populate_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.analysis is None:
            return
        files = list(enumerate(self.analysis.files))
        files.sort(key=lambda item: self._sort_key(item[1]), reverse=self.sort_reverse)
        query = self.search_var.get().strip().lower()
        current_filter = self._current_filter_key()
        for index, file in files:
            if not self._matches_filter(file, current_filter):
                continue
            display_name = file.metadata.relative_path.as_posix()
            if query and query not in display_name.lower() and query not in file.metadata.extension.lower():
                continue
            capture_time = file.metadata.capture_time.isoformat(sep=" ", timespec="seconds") if file.metadata.capture_time else ""
            warnings = "; ".join([*file.warnings, *file.metadata.metadata_errors][:3])
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                text=display_name,
                tags=(self._row_tag(file),),
                values=(
                    "\u2713" if file.selected else "",
                    self._type_icon(file),
                    self.i18n.category(file.destination_category),
                    f"{file.confidence:.0f}%",
                    self.i18n.detected_type(file.metadata.detected_type),
                    capture_time,
                    _format_size(file.metadata.file_size),
                    file.series_id or "",
                    self.i18n.gettext(f"group.{file.group_classification}"),
                    warnings,
                ),
            )

    def _sort_key(self, file: PhotoFile):
        column = self.sort_column
        if column == "file":
            return file.metadata.relative_path.as_posix().lower()
        if column == "category":
            return file.destination_category.value
        if column == "confidence":
            return file.confidence
        if column == "type":
            return file.metadata.detected_type.value
        if column == "size":
            return file.metadata.file_size
        if column == "series":
            return file.series_id or ""
        if column == "group":
            return file.group_classification
        if column == "warnings":
            return len(file.warnings) + len(file.metadata.metadata_errors)
        value = file.metadata.capture_time
        return value.replace(tzinfo=None).isoformat(timespec="microseconds") if value else ""

    def _sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self._populate_table()

    def _matches_filter(self, file: PhotoFile, filter_key: str) -> bool:
        category = file.destination_category
        if filter_key == "all":
            return True
        if filter_key == "ready":
            return category is DestinationCategory.HEIC
        if filter_key == "raw":
            return category is DestinationCategory.RAW
        if filter_key == "hdr3":
            return category is DestinationCategory.BRACKETING_3
        if filter_key == "hdr5":
            return category is DestinationCategory.BRACKETING_5
        if filter_key == "review":
            return category is DestinationCategory.REVIEW
        if filter_key == "errors":
            return bool(file.metadata.metadata_errors or file.copy_error)
        if filter_key == "duplicates":
            return any("duplicate" in warning.lower() or "collision" in warning.lower() for warning in file.warnings)
        return True

    def _update_summary(self) -> None:
        if self.analysis is None:
            return
        stats = self.analysis.statistics
        self.stats_vars["ready"].set(str(stats.get("heic_jpeg", 0)))
        self.stats_vars["raw"].set(str(stats.get("raw_single", 0)))
        self.stats_vars["hdr3"].set(str(stats.get("series_3", 0)))
        self.stats_vars["hdr5"].set(str(stats.get("series_5", 0)))
        self.stats_vars["review"].set(str(stats.get("review", 0)))
        self.stats_vars["errors"].set(str(stats.get("errors", 0)))

    def _selected_files(self) -> list[PhotoFile]:
        if self.analysis is None:
            return []
        selected: list[PhotoFile] = []
        for item in self.tree.selection():
            try:
                selected.append(self.analysis.files[int(item)])
            except (ValueError, IndexError):
                continue
        return selected

    def _update_preview(self) -> None:
        files = self._selected_files()
        if not files:
            return
        file = files[0]
        token = self.gui_state.next_preview_token()
        self._render_file_details(file)
        # Goes through PreviewCache so preview_cache_items actually bounds anything;
        # re-selecting a file no longer re-decodes it.
        cache = self._preview_cache()
        path = file.metadata.source_path
        size = self.settings.thumbnail_size
        cached = cache.peek(path, size)
        if cached is not None:
            self._show_preview(cached)
            return
        # Decoding (Pillow, or an ExifTool subprocess for RAW/DNG) runs off the
        # GUI thread; the token drops results for a file no longer selected.
        self.preview_image = None
        self.preview_label.configure(image="", text=self.i18n.gettext("preview.loading"))
        exiftool = self._preview_exiftool()

        def decode() -> None:
            info = load_preview_info(path, max_size=size, exiftool=exiftool)
            self.events.put(("preview_ready", (token, info, size)))

        threading.Thread(target=decode, name="preview-decode", daemon=True).start()

    def _on_preview_ready(self, token: int, info: PreviewInfo, size: int | None = None) -> None:
        self._preview_cache().put(info, size if size is not None else self.settings.thumbnail_size)
        if token != self.gui_state.preview_token:
            return
        self._show_preview(info)

    def _show_preview(self, info: PreviewInfo) -> None:
        image = None
        error = info.message
        if info.available and info.image is not None:
            try:
                from PIL import ImageTk

                image = ImageTk.PhotoImage(info.image)
            except Exception as exc:
                error = f"Preview unavailable: {exc}"
        self.preview_image = image
        if image is not None:
            self.preview_label.configure(image=image, text="")
        else:
            self.preview_label.configure(image="", text=error or self.i18n.gettext("preview.unavailable"))

    def _preview_cache(self) -> PreviewCache:
        """The cache sized by preview_cache_items; rebuilt when that setting changes."""
        if self.preview_cache.max_items != max(1, self.settings.preview_cache_items):
            self.preview_cache = PreviewCache(self.settings.preview_cache_items)
        return self.preview_cache

    def _preview_exiftool(self) -> ExifToolRunner:
        runner = getattr(self, "_preview_runner", None)
        if runner is None or getattr(self, "_preview_runner_path", None) != self.settings.exiftool_path:
            runner = ExifToolRunner.from_settings(self.settings)
            self._preview_runner = runner
            self._preview_runner_path = self.settings.exiftool_path
        return runner

    def _render_file_details(self, file: PhotoFile) -> None:
        self.preview_details.configure(
            text="\n".join(
                [
                    file.metadata.file_name,
                    f"{self.i18n.gettext('table.size')}: {_format_size(file.metadata.file_size)}",
                    f"{self.i18n.gettext('table.detected_type')}: {self.i18n.detected_type(file.metadata.detected_type)}",
                    f"{self.i18n.gettext('table.category')}: {self.i18n.category(file.destination_category)}",
                ]
            )
        )
        metadata_lines = [
            f"file: {file.metadata.file_name}",
            f"type: {file.metadata.detected_type.value}",
            f"category: {file.destination_category.value}",
            f"confidence: {file.confidence:.0f}%",
            f"size: {file.metadata.file_size}",
            f"time: {file.metadata.capture_time or ''}",
            f"time_raw: {file.metadata.capture_time_raw or ''}",
            f"time_source: {file.metadata.capture_time_source.value}",
            f"camera: {' '.join(part for part in (file.metadata.make, file.metadata.model) if part)}",
            f"pair_id: {file.pair_id or ''}",
            f"series_id: {file.series_id or ''}",
        ]
        reason_lines = [
            "reasons:",
            *[f"- {reason}" for reason in file.decision_reasons],
            "",
            "warnings:",
            *[f"- {warning}" for warning in [*file.warnings, *file.metadata.warnings, *file.metadata.metadata_errors]],
        ]
        self.metadata_text.delete("1.0", tk.END)
        self.metadata_text.insert("1.0", "\n".join(metadata_lines))
        self.reasons_text.delete("1.0", tk.END)
        self.reasons_text.insert("1.0", "\n".join(reason_lines))

    def _show_context_menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label=self.i18n.gettext("preview.tab.metadata"), command=self._show_metadata_dialog)
        menu.add_command(label=self.i18n.gettext("why.menu"), command=self._show_why_dialog)
        menu.add_command(label=self.i18n.gettext("series_view.menu"), command=self._open_series_viewer)
        menu.add_separator()
        menu.add_command(label=self.i18n.category(DestinationCategory.HEIC), command=lambda: self._assign_selected(DestinationCategory.HEIC))
        menu.add_command(label=self.i18n.category(DestinationCategory.RAW), command=lambda: self._assign_selected(DestinationCategory.RAW))
        menu.add_command(label=self.i18n.category(DestinationCategory.SKIP), command=lambda: self._assign_selected(DestinationCategory.SKIP))
        menu.add_command(label=self.i18n.category(DestinationCategory.REVIEW), command=lambda: self._assign_selected(DestinationCategory.REVIEW))
        menu.add_separator()
        menu.add_command(label=self.i18n.gettext("menu.create_hdr3"), command=lambda: self._manual_series(3))
        menu.add_command(label=self.i18n.gettext("menu.create_hdr5"), command=lambda: self._manual_series(5))
        menu.add_command(label=self.i18n.gettext("menu.dissolve_series"), command=self._dissolve_series)
        menu.add_command(label=self.i18n.gettext("action.undo"), command=self._undo)
        menu.add_command(label=self.i18n.gettext("action.redo"), command=self._redo)
        menu.tk_popup(event.x_root, event.y_root)

    def _assign_selected(self, category: DestinationCategory) -> None:
        files = self._selected_files()
        if not files:
            return
        snapshot = self._series_snapshot()

        def redo() -> None:
            # Pulling a file out of a series must dissolve that series, or the
            # series would keep listing a file that now lives somewhere else.
            if self.series_manager is not None:
                self.series_manager.dissolve_containing(files)
            for file in files:
                self._set_category(file, category)
            self._after_manual_change()

        def undo() -> None:
            self._restore_series_snapshot(snapshot)
            self._after_manual_change()

        redo()
        self.undo_stack.push(UndoAction(f"assign {category.value}", undo, redo))

    def _manual_series(self, size: int) -> None:
        """Group the selection into a manual series via the single SeriesManager."""
        if self.series_manager is None:
            return
        files = self._selected_files()
        manager = self.series_manager
        snapshot = self._series_snapshot()
        try:
            series = manager.create_manual(files, size)
        except ManualSeriesError as exc:
            messagebox.showwarning(APP_NAME, exc.user_message)
            return

        def redo() -> None:
            if manager.find_by_uid(series.uid) is None:
                manager.series.append(series)
                manager.sync_destinations()
            self._after_manual_change()

        def undo() -> None:
            self._restore_series_snapshot(snapshot)
            self._after_manual_change()

        self._after_manual_change()
        self.undo_stack.push(UndoAction(f"manual hdr {size}", undo, redo))

    def _dissolve_series(self) -> None:
        if self.series_manager is None:
            return
        files = [file for file in self._selected_files() if file.series_id]
        if not files:
            return
        manager = self.series_manager
        snapshot = self._series_snapshot()

        def redo() -> None:
            manager.dissolve_containing(files)
            self._after_manual_change()

        def undo() -> None:
            self._restore_series_snapshot(snapshot)
            self._after_manual_change()

        redo()
        self.undo_stack.push(UndoAction("dissolve series", undo, redo))

    def _series_snapshot(self) -> tuple[list, list[tuple[PhotoFile, Any, Any, str | None, bool]]]:
        """Capture series membership and per-file placement so undo can restore both."""
        if self.analysis is None or self.series_manager is None:
            return [], []
        files_state = [
            (file, file.destination_category, file.destination_subpath, file.series_id, file.selected)
            for file in self.analysis.files
        ]
        return list(self.series_manager.series), files_state

    def _restore_series_snapshot(self, snapshot) -> None:
        series_state, files_state = snapshot
        if self.series_manager is None:
            return
        self.series_manager.series[:] = list(series_state)
        for file, category, subpath, series_id, selected in files_state:
            file.destination_category = category
            file.destination_subpath = subpath
            file.series_id = series_id
            file.selected = selected

    def _set_category(self, file: PhotoFile, category: DestinationCategory) -> None:
        """Assign a single-file category. Series membership goes through SeriesManager."""
        if category in {DestinationCategory.BRACKETING_3, DestinationCategory.BRACKETING_5}:
            raise ValueError("Use SeriesManager.create_manual to place a file into a series.")
        assign_category(file, category)

    def _after_manual_change(self) -> None:
        """Republish manager state onto the analysis so table and reports agree."""
        if self.analysis is not None and self.series_manager is not None:
            self.series_manager.apply_to(self.analysis)
        elif self.analysis is not None:
            self.analysis.statistics = compute_statistics(self.analysis.files, self.analysis.bracket_series)
        # Any edit invalidates a plan built from the previous layout.
        self.copy_plan = None
        self._populate_table()
        self._update_summary()
        self._update_preview()

    def _undo(self) -> None:
        if self.undo_stack.undo():
            self._log(self.i18n.gettext("action.undo"))

    def _redo(self) -> None:
        if self.undo_stack.redo():
            self._log(self.i18n.gettext("action.redo"))

    def _show_metadata_dialog(self) -> None:
        files = self._selected_files()
        if not files:
            return
        file = files[0]
        dialog = tk.Toplevel(self)
        dialog.title(file.metadata.file_name)
        dialog.geometry("760x540")
        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        lines = [f"{key}: {value}" for key, value in sorted(file.metadata.raw_exif_record.items())]
        if not lines:
            lines = [self.i18n.gettext("preview.unavailable")]
        text.insert("1.0", "\n".join(lines))
        ttk.Button(dialog, text="OK", command=dialog.destroy).pack(anchor="e", padx=8, pady=(0, 8))

    def _show_why_dialog(self) -> None:
        """'Почему так определено?' — assembled entirely from app.decision_explainer,
        which itself only reads real algorithm output; nothing here re-derives a
        decision that already exists elsewhere."""
        files = self._selected_files()
        if not files or self.analysis is None:
            return
        file = files[0]
        from app.decision_explainer import explain_pair, explain_series, format_pair_explanation, format_series_explanation

        sections: list[str] = []
        series = self.series_manager.find_by_file(file) if self.series_manager is not None else None
        if series is not None:
            explanation = explain_series(series, self.analysis, self.series_manager, self.settings.language)
            sections.append(format_series_explanation(explanation, self.settings.language))
        pair = next(
            (p for p in self.analysis.pairs if p.raw_file is file or p.rendered_file is file),
            None,
        )
        if pair is not None:
            explanation = explain_pair(pair, self.analysis, self.series_manager, self.settings.language)
            sections.append(format_pair_explanation(explanation, self.settings.language))
        if not sections:
            sections.append(self.i18n.gettext("why.none"))

        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("why.title"))
        dialog.geometry("640x560")
        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", "\n\n".join(sections))
        text.configure(state="disabled")
        ttk.Button(dialog, text=self.i18n.gettext("why.close"), command=dialog.destroy).pack(anchor="e", padx=8, pady=(0, 8))

    def _open_series_viewer(self) -> None:
        """Feature 5: side-by-side series comparison with embedded RAW previews.

        Extraction runs ExifTool per frame (tens to hundreds of ms each), so it
        happens on a background thread — the same single-worker-slot convention
        analysis/auto-configure already use — never on the GUI thread.
        """
        if self.worker and self.worker.is_alive():
            return
        files = self._selected_files()
        series = self.series_manager.find_by_file(files[0]) if files and self.series_manager is not None else None
        if series is None:
            messagebox.showinfo(APP_NAME, self.i18n.gettext("series_view.none"))
            return
        loading = tk.Toplevel(self)
        loading.title(self.i18n.gettext("series_view.loading"))
        loading.geometry("360x80")
        loading.transient(self)
        ttk.Label(loading, text=self.i18n.gettext("series_view.loading"), padding=20).pack(expand=True)
        self.worker = threading.Thread(target=self._series_viewer_worker, args=(series, loading), daemon=True)
        self.worker.start()

    def _series_viewer_worker(self, series, loading_dialog: tk.Toplevel) -> None:
        from app.series_preview import build_series_previews

        try:
            previews = build_series_previews(list(series.raw_files))
            self.events.put(("series_preview_ready", (series, previews, loading_dialog)))
        except Exception as exc:
            self.events.put(("series_preview_failed", (exc, loading_dialog)))

    def _show_series_viewer_dialog(self, series, previews: list) -> None:
        from PIL import ImageTk

        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("series_view.title", series_id=series.series_id))
        dialog.geometry("1000x700")
        dialog.minsize(760, 520)
        dialog.transient(self)

        # Keeps every PhotoImage alive; Tk drops images with no live Python
        # reference even while still displayed.
        image_refs: dict[str, "ImageTk.PhotoImage"] = {}
        state = {"focus": 0, "zoom": 1.0, "blink": False, "blink_job": None}

        body = ttk.Frame(dialog, padding=8)
        body.pack(fill="both", expand=True)

        # --- thumbnail strip (horizontally scrollable for a 5-frame series) ---
        strip_outer = ttk.Frame(body)
        strip_outer.pack(fill="x", side="top")
        strip_canvas = tk.Canvas(strip_outer, height=160, highlightthickness=0)
        strip_scroll = ttk.Scrollbar(strip_outer, orient="horizontal", command=strip_canvas.xview)
        strip_canvas.configure(xscrollcommand=strip_scroll.set)
        strip_canvas.pack(fill="x", side="top")
        strip_scroll.pack(fill="x", side="top")
        strip_inner = ttk.Frame(strip_canvas)
        strip_canvas.create_window((0, 0), window=strip_inner, anchor="nw")

        # --- focused large view: zoomable, pannable via native canvas scan ---
        main_frame = ttk.Frame(body, padding=(0, 8, 0, 0))
        main_frame.pack(fill="both", expand=True)
        main_canvas = tk.Canvas(main_frame, background="#202020", highlightthickness=0)
        main_canvas.pack(fill="both", expand=True)

        info_var = tk.StringVar()
        ttk.Label(main_frame, textvariable=info_var).pack(anchor="w", pady=(4, 0))

        def render_focus() -> None:
            index = state["focus"]
            preview = previews[index]
            info_var.set(self.i18n.gettext("series_view.frame_label", index=index + 1, total=len(previews), name=preview.exif.file_name))
            main_canvas.delete("all")
            if preview.image is None:
                main_canvas.create_text(
                    20, 20, anchor="nw", fill="white",
                    text=self.i18n.gettext("series_view.unavailable", reason=preview.error or "?"),
                )
                return
            zoom = state["zoom"]
            width = max(1, int(preview.image.width * zoom))
            height = max(1, int(preview.image.height * zoom))
            resized = preview.image.resize((width, height))
            photo = ImageTk.PhotoImage(resized)
            image_refs["focus"] = photo
            main_canvas.configure(scrollregion=(0, 0, width, height))
            main_canvas.create_image(0, 0, anchor="nw", image=photo)

        def set_focus(index: int) -> None:
            state["focus"] = index % len(previews)
            render_focus()

        def on_zoom(value: str) -> None:
            state["zoom"] = float(value)
            render_focus()

        def start_pan(event) -> None:
            main_canvas.scan_mark(event.x, event.y)

        def do_pan(event) -> None:
            main_canvas.scan_dragto(event.x, event.y, gain=1)

        main_canvas.bind("<ButtonPress-1>", start_pan)
        main_canvas.bind("<B1-Motion>", do_pan)

        for index, preview in enumerate(previews):
            cell = ttk.Frame(strip_inner, padding=4)
            cell.grid(row=0, column=index, sticky="n")
            if preview.image is not None:
                thumb = preview.image.copy()
                thumb.thumbnail((120, 120))
                photo = ImageTk.PhotoImage(thumb)
                image_refs[f"thumb_{index}"] = photo
                thumb_label = ttk.Label(cell, image=photo, cursor="hand2")
            else:
                thumb_label = ttk.Label(cell, text="?", width=14, anchor="center", cursor="hand2")
            thumb_label.pack()
            thumb_label.bind("<Button-1>", lambda _event, i=index: set_focus(i))
            exif = preview.exif
            exif_text = (
                f"{exif.file_name}\n"
                f"{_fmt_shutter_display(exif.exposure_time)} f/{_fmt_num_display(exif.f_number)} "
                f"ISO {_fmt_num_display(exif.iso)}\n"
                f"EV {_fmt_num_display(exif.ev_display)}"
            )
            ttk.Label(cell, text=exif_text, justify="center", font=("Segoe UI", 8)).pack()
            warnings_text = []
            if preview.highlight_clipped:
                warnings_text.append(self.i18n.gettext("series_view.highlight_clip"))
            if preview.shadow_clipped:
                warnings_text.append(self.i18n.gettext("series_view.shadow_clip"))
            if warnings_text:
                ttk.Label(cell, text=" ".join(warnings_text), foreground="#c0392b", font=("Segoe UI", 8)).pack()
            if preview.histogram_rgb is not None:
                hist_canvas = tk.Canvas(cell, width=120, height=36, highlightthickness=0, background="#101010")
                hist_canvas.pack(pady=(2, 0))
                _draw_histogram(hist_canvas, preview.histogram_rgb)

        strip_inner.update_idletasks()
        strip_canvas.configure(scrollregion=strip_canvas.bbox("all"))

        controls = ttk.Frame(body, padding=(0, 8, 0, 0))
        controls.pack(fill="x", side="bottom")
        ttk.Button(controls, text=self.i18n.gettext("series_view.prev"), command=lambda: set_focus(state["focus"] - 1)).pack(side="left")
        ttk.Button(controls, text=self.i18n.gettext("series_view.next"), command=lambda: set_focus(state["focus"] + 1)).pack(side="left", padx=(6, 0))
        blink_var = tk.BooleanVar(value=False)

        def toggle_blink() -> None:
            state["blink"] = blink_var.get()
            if state["blink"]:
                blink_tick()
            elif state["blink_job"] is not None:
                dialog.after_cancel(state["blink_job"])
                state["blink_job"] = None

        def blink_tick() -> None:
            if not state["blink"]:
                return
            set_focus(state["focus"] + 1)
            state["blink_job"] = dialog.after(500, blink_tick)

        ttk.Checkbutton(controls, text=self.i18n.gettext("series_view.blink"), variable=blink_var, command=toggle_blink).pack(side="left", padx=(12, 0))
        ttk.Label(controls, text=self.i18n.gettext("series_view.zoom")).pack(side="left", padx=(12, 4))
        zoom_scale = ttk.Scale(controls, from_=0.25, to=4.0, value=1.0, command=on_zoom, length=160)
        zoom_scale.pack(side="left")
        ttk.Button(controls, text=self.i18n.gettext("series_view.close"), command=dialog.destroy).pack(side="right")

        def on_key(event) -> None:
            if event.keysym == "Left":
                set_focus(state["focus"] - 1)
            elif event.keysym == "Right":
                set_focus(state["focus"] + 1)

        dialog.bind("<Left>", on_key)
        dialog.bind("<Right>", on_key)

        def on_close() -> None:
            if state["blink_job"] is not None:
                dialog.after_cancel(state["blink_job"])
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)
        # Keep the reference dict alive for the dialog's lifetime, and expose the
        # closures/state tests need to drive the dialog without simulating clicks.
        dialog._image_refs = image_refs  # type: ignore[attr-defined]
        dialog._state = state  # type: ignore[attr-defined]
        dialog._set_focus = set_focus  # type: ignore[attr-defined]
        dialog._on_zoom = on_zoom  # type: ignore[attr-defined]
        dialog._toggle_blink = toggle_blink  # type: ignore[attr-defined]
        dialog._blink_var = blink_var  # type: ignore[attr-defined]
        self._last_series_dialog = dialog
        render_focus()

    def _prompt_text(self, label: str, initial: str = "") -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title(label)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        var = tk.StringVar(value=initial)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=label).pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=var, width=40)
        entry.pack(fill="x", pady=(4, 8))
        entry.focus_set()
        result: dict[str, str | None] = {"value": None}

        def accept() -> None:
            result["value"] = var.get().strip() or None
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x")
        ttk.Button(button_row, text=self.i18n.gettext("move.cancel"), command=cancel).pack(side="right")
        ttk.Button(button_row, text="OK", command=accept).pack(side="right", padx=(0, 6))
        entry.bind("<Return>", lambda _event: accept())
        self.wait_window(dialog)
        return result["value"]

    def _show_auto_config_dialog(self, report: AutoConfigReport) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("auto_config.title"))
        dialog.geometry("760x620")
        dialog.minsize(680, 480)
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=self.i18n.gettext("auto_config.intro"), wraplength=720, justify="left").pack(anchor="w")

        info_lines = []
        if report.cameras:
            cameras_text = ", ".join(f"{c.make} {c.model} ({c.count})".strip() for c in report.cameras)
            info_lines.append(self.i18n.gettext("auto_config.cameras", cameras=cameras_text))
        if report.raw_formats or report.rendered_formats:
            info_lines.append(
                self.i18n.gettext(
                    "auto_config.formats",
                    raw=", ".join(report.raw_formats) or "—",
                    rendered=", ".join(report.rendered_formats) or "—",
                )
            )
        info_lines.append(
            self.i18n.gettext("auto_config.subsecond_yes" if report.subsecond_available else "auto_config.subsecond_no")
        )
        if report.reliable_exposure_source:
            info_lines.append(self.i18n.gettext("auto_config.exposure_source", source=report.reliable_exposure_source))
        for warning in report.warnings:
            info_lines.append(warning)
        ttk.Label(body, text="\n".join(info_lines), wraplength=720, justify="left").pack(anchor="w", pady=(8, 8))

        if not report.suggestions:
            ttk.Label(body, text=self.i18n.gettext("auto_config.no_suggestions"), wraplength=720, justify="left").pack(
                anchor="w", pady=(4, 8)
            )
            ttk.Button(body, text=self.i18n.gettext("auto_config.cancel"), command=dialog.destroy).pack(anchor="e")
            return

        rows_frame = ttk.Frame(body)
        rows_frame.pack(fill="both", expand=True)
        header = ttk.Frame(rows_frame)
        header.pack(fill="x")
        ttk.Label(header, text=self.i18n.gettext("auto_config.column.apply"), width=8).grid(row=0, column=0)
        ttk.Label(header, text=self.i18n.gettext("auto_config.column.setting"), width=28).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text=self.i18n.gettext("auto_config.column.current"), width=14).grid(row=0, column=2, sticky="w")
        ttk.Label(header, text=self.i18n.gettext("auto_config.column.suggested"), width=16).grid(row=0, column=3, sticky="w")
        ttk.Label(header, text=self.i18n.gettext("auto_config.column.reason")).grid(row=0, column=4, sticky="w")

        check_vars: dict[str, tk.BooleanVar] = {}
        value_vars: dict[str, tk.StringVar] = {}
        for index, suggestion in enumerate(report.suggestions, start=1):
            row = ttk.Frame(rows_frame)
            row.pack(fill="x", pady=2)
            check_var = tk.BooleanVar(value=True)
            check_vars[suggestion.field] = check_var
            ttk.Checkbutton(row, variable=check_var).grid(row=0, column=0)
            ttk.Label(row, text=suggestion.field, width=28).grid(row=0, column=1, sticky="w")
            ttk.Label(row, text=_format_suggestion_value(suggestion.current_value), width=14).grid(row=0, column=2, sticky="w")
            value_var = tk.StringVar(value=_format_suggestion_value(suggestion.suggested_value))
            value_vars[suggestion.field] = value_var
            ttk.Entry(row, textvariable=value_var, width=16).grid(row=0, column=3, sticky="w")
            ttk.Label(row, text=suggestion.rationale, wraplength=280, justify="left").grid(row=0, column=4, sticky="w", padx=(6, 0))

        def set_all(checked: bool) -> None:
            for var in check_vars.values():
                var.set(checked)

        buttons = ttk.Frame(body, padding=(0, 8, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=self.i18n.gettext("auto_config.select_all"), command=lambda: set_all(True)).pack(side="left")
        ttk.Button(buttons, text=self.i18n.gettext("auto_config.select_none"), command=lambda: set_all(False)).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text=self.i18n.gettext("auto_config.cancel"), command=dialog.destroy).pack(side="right")
        ttk.Button(
            buttons,
            text=self.i18n.gettext("auto_config.apply"),
            command=lambda: self._on_apply_auto_config(dialog, report, check_vars, value_vars),
        ).pack(side="right", padx=(0, 8))

    def _on_apply_auto_config(
        self,
        dialog: tk.Toplevel,
        report: AutoConfigReport,
        check_vars: dict[str, tk.BooleanVar],
        value_vars: dict[str, tk.StringVar],
    ) -> None:
        accepted = {field for field, var in check_vars.items() if var.get()}
        overrides: dict[str, Any] = {}
        for suggestion in report.suggestions:
            if suggestion.field not in accepted:
                continue
            raw = value_vars[suggestion.field].get()
            try:
                overrides[suggestion.field] = _parse_suggestion_value(suggestion.suggested_value, raw)
            except (TypeError, ValueError) as exc:
                messagebox.showerror(APP_NAME, f"{suggestion.field}: {exc}")
                return
        try:
            self._apply_auto_config(report, accepted, overrides)
        except SettingsError as exc:
            messagebox.showerror(APP_NAME, exc.user_message)
            return
        dialog.destroy()
        self._log(self.i18n.gettext("auto_config.applied", count=len(accepted)))

    def _apply_auto_config(
        self,
        report: AutoConfigReport,
        accepted_fields: set[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        """Apply accepted suggestions. Nothing here runs unless the caller explicitly opts in."""
        updated = apply_suggestions(self.settings, report.suggestions, accepted_fields, overrides)
        validate_settings(updated)
        self.settings = updated
        save_settings(self.settings, self.settings_path)

    def _open_review_queue(self) -> None:
        if self.analysis is None or self.series_manager is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("review_queue.title"))
        dialog.geometry("900x560")
        dialog.minsize(760, 420)
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True, side="top")
        columns = ("reason", "summary")
        tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        tree.heading("reason", text=self.i18n.gettext("review_queue.column.reason"))
        tree.heading("summary", text=self.i18n.gettext("review_queue.column.summary"))
        tree.column("reason", width=180, anchor="w")
        tree.column("summary", width=560, anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        scroll.pack(fill="y", side="right")
        tree.pack(fill="both", expand=True, side="left")
        tree.configure(yscrollcommand=scroll.set)

        items_by_iid: dict[str, ReviewItem] = {}

        def refresh() -> None:
            tree.delete(*tree.get_children())
            items_by_iid.clear()
            if self.analysis is None or self.series_manager is None:
                return
            queue = build_review_queue(self.analysis, self.series_manager, self.settings, self.settings.language)
            for index, item in enumerate(queue):
                iid = str(index)
                items_by_iid[iid] = item
                tree.insert("", "end", iid=iid, values=(self.i18n.gettext(f"reason.{item.reason.value}"), item.summary))
            if not queue:
                tree.insert("", "end", iid="__empty__", values=(self.i18n.gettext("review_queue.empty"), ""))

        def selected_item() -> ReviewItem | None:
            selection = tree.selection()
            if not selection or selection[0] not in items_by_iid:
                return None
            return items_by_iid[selection[0]]

        def run_action(action) -> None:
            item = selected_item()
            if item is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("review_queue.select_item"))
                return
            try:
                action(item)
            except (ManualSeriesError, PhotoBracketSorterError) as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            self._after_manual_change()
            refresh()

        buttons = ttk.Frame(body, padding=(0, 8, 0, 0))
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.confirm_hdr3"), command=lambda: run_action(lambda item: self._review_confirm(item, 3))).pack(side="left")
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.confirm_hdr5"), command=lambda: run_action(lambda item: self._review_confirm(item, 5))).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.not_bracket"), command=lambda: run_action(self._review_not_bracket)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.split"), command=lambda: run_action(self._review_split)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.merge"), command=lambda: run_action(self._review_merge)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.exclude"), command=lambda: run_action(self._review_exclude)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.restore_auto"), command=lambda: run_action(self._review_restore_automatic)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.refresh"), command=refresh).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("review_queue.close"), command=dialog.destroy).pack(side="right")

        refresh()

    def _review_candidate(self, item: ReviewItem):
        if self.analysis is None or item.candidate_id is None:
            return None
        return next((candidate for candidate in self.analysis.bracket_candidates if candidate.candidate_id == item.candidate_id), None)

    def _review_series(self, item: ReviewItem):
        if self.series_manager is None or item.series_uid is None:
            return None
        return self.series_manager.find_by_uid(item.series_uid)

    def _review_confirm(self, item: ReviewItem, size: int) -> None:
        """'Подтвердить HDR3/5': promote a near-miss candidate into a real series."""
        if self.series_manager is None:
            return
        candidate = self._review_candidate(item)
        if candidate is None:
            messagebox.showinfo(APP_NAME, self.i18n.gettext("review_queue.select_item"))
            return
        confirm_candidate(self.series_manager, candidate, size)

    def _review_not_bracket(self, item: ReviewItem) -> None:
        candidate = self._review_candidate(item)
        if candidate is not None:
            dismiss_candidate(candidate)

    def _review_split(self, item: ReviewItem) -> None:
        if self.series_manager is None:
            return
        series = self._review_series(item)
        if series is not None:
            split_series(self.series_manager, series)

    def _review_exclude(self, item: ReviewItem) -> None:
        exclude_files(item.files, self.series_manager)

    def _review_merge(self, item: ReviewItem) -> None:
        """'Объединить с соседней': the series/candidate plus whatever is selected
        in the main table right now becomes one manual series."""
        if self.series_manager is None:
            return
        series = self._review_series(item)
        if series is None:
            messagebox.showinfo(APP_NAME, self.i18n.gettext("review_queue.select_item"))
            return
        extra = [file for file in self._selected_files() if file not in series.raw_files]
        if not extra:
            messagebox.showinfo(APP_NAME, self.i18n.gettext("review_queue.merge_hint"))
            return
        target_size = len(series.raw_files) + len(extra)
        merge_series(self.series_manager, series, extra, target_size)

    def _review_restore_automatic(self, item: ReviewItem) -> None:
        if self.series_manager is None:
            return
        series = self._review_series(item)
        if series is None:
            return
        result = restore_automatic(self.series_manager, series, self.settings)
        self._log(self.i18n.gettext("review_queue.restore_ok" if result is not None else "review_queue.restore_failed"))

    def _open_history(self) -> None:
        """Operation history: every Copy/Move this app has ever run, persisted
        under app.operation_journal, plus 'Undo move' for entries that qualify."""
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("history.title"))
        dialog.geometry("820x480")
        dialog.minsize(700, 380)
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True, side="top")
        columns = ("date", "type", "files", "status")
        tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        tree.heading("date", text=self.i18n.gettext("history.column.date"))
        tree.heading("type", text=self.i18n.gettext("history.column.type"))
        tree.heading("files", text=self.i18n.gettext("history.column.files"))
        tree.heading("status", text=self.i18n.gettext("history.column.status"))
        tree.column("date", width=170, anchor="w")
        tree.column("type", width=150, anchor="w")
        tree.column("files", width=80, anchor="center")
        tree.column("status", width=140, anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        scroll.pack(fill="y", side="right")
        tree.pack(fill="both", expand=True, side="left")
        tree.configure(yscrollcommand=scroll.set)

        entries_by_iid: dict[str, object] = {}

        def refresh() -> None:
            tree.delete(*tree.get_children())
            entries_by_iid.clear()
            for entry in reversed(load_journal(self.journal_path)):
                type_label = self.i18n.gettext(f"history.type.{entry.operation_type}")
                if entry.undone:
                    status = self.i18n.gettext("history.status.undone")
                elif entry.cancelled:
                    status = self.i18n.gettext("history.status.cancelled")
                else:
                    status = self.i18n.gettext("history.status.done")
                tree.insert("", "end", iid=entry.entry_id, values=(entry.created_at, type_label, entry.copied_files, status))
                entries_by_iid[entry.entry_id] = entry

        def selected_entry():
            selection = tree.selection()
            if not selection:
                return None
            return entries_by_iid.get(selection[0])

        def safe_refresh() -> None:
            try:
                refresh()
            except tk.TclError:
                pass  # the dialog was closed while undo was running

        def do_undo() -> None:
            entry = selected_entry()
            if entry is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("history.select_item"))
                return
            # Quick check only (no hashing) on the GUI thread; the worker repeats
            # it with SHA-256 verification before touching anything.
            ok, reasons = can_undo_move(entry, verify_hashes=False)
            if not ok:
                messagebox.showwarning(APP_NAME, self.i18n.gettext("history.undo_blocked") + "\n" + "\n".join(reasons))
                return
            if not self._confirm_undo_move():
                return
            self._start_undo_move(entry, safe_refresh)

        buttons = ttk.Frame(body, padding=(0, 8, 0, 0))
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text=self.i18n.gettext("history.undo_button"), command=do_undo).pack(side="left")
        ttk.Button(buttons, text=self.i18n.gettext("history.close"), command=dialog.destroy).pack(side="right")

        refresh()

    def _confirm_undo_move(self) -> bool:
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("history.confirm_undo_title"))
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        accepted = tk.BooleanVar(value=False)
        understood = tk.BooleanVar(value=False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.i18n.gettext("history.confirm_undo_body"), wraplength=460, justify="left").pack(anchor="w")
        ttk.Checkbutton(body, text=self.i18n.gettext("history.confirm_undo_checkbox"), variable=understood).pack(anchor="w", pady=(8, 8))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))

        def accept() -> None:
            if understood.get():
                accepted.set(True)
                dialog.destroy()
            else:
                messagebox.showwarning(APP_NAME, self.i18n.gettext("history.confirm_undo_checkbox"))

        ttk.Button(buttons, text=self.i18n.gettext("move.cancel"), command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text=self.i18n.gettext("history.undo_button"), command=accept).pack(side="right", padx=(0, 8))
        self.wait_window(dialog)
        return accepted.get()

    def _start_undo_move(self, entry, on_done) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self._log(self.i18n.gettext("history.undo_started"))
        self.worker = threading.Thread(target=self._undo_move_worker, args=(entry, on_done), daemon=True)
        self.worker.start()

    def _undo_move_worker(self, entry, on_done) -> None:
        try:
            self._worker_progress("Восстановление", 0, 1)
            ok, reasons = can_undo_move(entry)
            if not ok:
                self.events.put(("undo_move_blocked", reasons))
                return
            result = undo_move(entry, self.cancel_event, self._worker_progress)
            self.events.put(("undo_move_ready", (entry, result, on_done)))
        except Exception as exc:
            self.events.put(("error", exc))

    def _on_undo_move_done(self, entry, result, on_done) -> None:
        try:
            undo_entry = build_journal_entry(result, Path(entry.source_root))
            append_journal_entry(undo_entry, self.journal_path)
            # A cancelled undo leaves files in the output folder; the entry must
            # stay undoable so the user can resume it later.
            if result.copied_files and not result.errors and not result.cancelled:
                mark_undone(entry.entry_id, undo_entry.entry_id, self.journal_path)
        except Exception as exc:
            self._log(f"Journal: {exc}")
        self._log(self.i18n.gettext("history.undo_done"))
        summary = self.i18n.gettext(
            "history.undo_summary", restored=result.copied_files, skipped=result.skipped_files, errors=result.errors
        )
        self._log(summary)
        messagebox.showinfo(APP_NAME, summary)
        if on_done:
            on_done()

    def _handle_error(self, error: object) -> None:
        if isinstance(error, PhotoBracketSorterError):
            self._log(error.user_message)
            messagebox.showerror(APP_NAME, error.user_message)
        else:
            self._log(str(error))
            messagebox.showerror(APP_NAME, str(error))

    def _update_free_space(self) -> None:
        output = self.output_var.get().strip()
        if not output:
            self.free_space_var.set("")
            return
        try:
            path = Path(output)
            target = path if path.exists() else path.parent
            usage = shutil.disk_usage(target)
            self.free_space_var.set(self.i18n.gettext("path.free_space", space=_format_size(usage.free)))
        except Exception:
            self.free_space_var.set("")

    def _open_path(self, value: str) -> None:
        if not value:
            return
        path = Path(value)
        if path.exists():
            try:
                os.startfile(path)
            except AttributeError:
                subprocess.Popen(["explorer", str(path)])

    def _open_last_result(self) -> None:
        if self.last_result_root:
            self._open_path(str(self.last_result_root))

    def _exiftool_status(self) -> str:
        version = ExifToolRunner.from_settings(self.settings).version()
        if version:
            return f"ExifTool {version}"
        return self.i18n.gettext("top.exiftool_fallback")

    def _current_filter_key(self) -> str:
        label = self.filter_var.get()
        for key in FILTERS:
            if self._filter_label(key) == label:
                return key
        return "all"

    def _filter_label(self, key: str) -> str:
        return self.i18n.gettext(f"filter.{key}")

    def _row_tag(self, file: PhotoFile) -> str:
        if file.metadata.metadata_errors or file.copy_error:
            return "error"
        category = file.destination_category
        if category is DestinationCategory.REVIEW:
            return "review"
        if category is DestinationCategory.SKIP or not file.selected:
            return "skip"
        if category in {
            DestinationCategory.BRACKETING_3,
            DestinationCategory.BRACKETING_5,
            DestinationCategory.BRACKETING_7,
            DestinationCategory.BRACKETING_9,
        }:
            return "series"
        return ""

    def _type_icon(self, file: PhotoFile) -> str:
        if file.destination_category is DestinationCategory.BRACKETING_3:
            return "HDR3"
        if file.destination_category is DestinationCategory.BRACKETING_5:
            return "HDR5"
        return file.metadata.detected_type.value

    def _capture_column_layout(self) -> None:
        """Remember the table layout the user dragged into place."""
        if not hasattr(self, "tree"):
            return
        try:
            widths = {"#0": int(self.tree.column("#0", "width"))}
            for column in self.tree["columns"]:
                widths[str(column)] = int(self.tree.column(column, "width"))
        except tk.TclError:
            return
        self.settings.column_widths = widths
        self.settings.column_order = tuple(str(column) for column in self.tree["columns"])

    def _save_settings_quietly(self) -> None:
        try:
            if self.settings.remember_paths:
                self.settings.last_source_path = self.source_var.get()
                self.settings.last_output_path = self.output_var.get()
            save_settings(self.settings, self.settings_path)
        except (OSError, SettingsError):
            return

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            # Closing mid-Move used to kill the daemon thread outright: no
            # report, no journal entry for the originals already deleted.
            if not messagebox.askyesno(APP_NAME, self.i18n.gettext("message.close_while_busy")):
                return
            self.cancel_event.set()
            self.status_var.set(self.i18n.gettext("message.cancel_requested"))
            self.update_idletasks()
            self.worker.join(timeout=60)
        if self.settings.remember_window:
            self.settings.window_geometry = self.geometry()
        self._capture_column_layout()
        self._save_settings_quietly()
        self.destroy()

    def _log(self, text: str) -> None:
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)


def _load_settings_or_defaults(path: Path) -> tuple[AppSettings, str | None]:
    """Load settings, or fall back to defaults when the file is unreadable.

    A hand-edited or truncated settings.json used to raise straight out of
    MainWindow.__init__, which left the user with no window and (in the
    windowed build) no error either. The problem is returned so the window
    can show it once it exists; the broken file is left in place untouched.
    """
    try:
        return load_settings(path), None
    except (SettingsError, ValueError, TypeError, OSError) as exc:
        reason = exc.user_message if isinstance(exc, PhotoBracketSorterError) else str(exc)
        return AppSettings(), reason


def _existing_dir(value: str) -> str | None:
    """The folder a file dialog should open in: the entry's value when it exists."""
    text = value.strip()
    if not text:
        return None
    path = Path(text)
    if path.is_dir():
        return str(path)
    return str(path.parent) if path.parent.is_dir() else None


def clamp_geometry(geometry: str, screen_width: int, screen_height: int, margin: int = 40) -> str:
    """Fit a Tk geometry string (``WxH`` or ``WxH+X+Y``) onto a screen.

    Width and height are capped at the screen minus ``margin``; an offset
    that would leave less than ``margin`` pixels of the window visible is
    dropped so the window manager places it. Unparseable input is returned
    unchanged so Tk can reject it on its own terms.
    """
    import re

    match = re.fullmatch(r"(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?", geometry.strip())
    if not match or screen_width <= 0 or screen_height <= 0:
        return geometry
    width = min(int(match.group(1)), max(1, screen_width - margin))
    height = min(int(match.group(2)), max(1, screen_height - margin))
    result = f"{width}x{height}"
    if match.group(3) is not None and match.group(4) is not None:
        x_offset = int(match.group(3))
        y_offset = int(match.group(4))
        if -width + margin <= x_offset <= screen_width - margin and 0 <= y_offset <= screen_height - margin:
            result += f"{x_offset:+d}{y_offset:+d}"
    return result


def _format_suggestion_value(value: Any) -> str:
    if hasattr(value, "value") and hasattr(type(value), "__members__"):
        return str(value.value)
    if isinstance(value, tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def _parse_suggestion_value(reference: Any, raw: str) -> Any:
    """Parse an edited dialog value back to the type ``reference`` (the original
    suggestion) has, so a user edit round-trips through AppSettings cleanly."""
    if hasattr(reference, "value") and hasattr(type(reference), "__members__"):
        return type(reference)(raw.strip())
    if isinstance(reference, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(reference, int):
        return int(raw)
    if isinstance(reference, float):
        return float(raw)
    if isinstance(reference, tuple):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return raw


def _format_size(size: int | float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _fmt_num_display(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _fmt_shutter_display(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    return f"1/{round(1 / value)}" if value < 1 else f"{value:g}s"


def _draw_histogram(canvas: "tk.Canvas", histogram_rgb: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]) -> None:
    """Draw a compact per-channel histogram. Downsamples 256 bins to the
    canvas width so this stays cheap to redraw on every frame switch."""
    width = int(canvas["width"])
    height = int(canvas["height"])
    colors = ("#e74c3c", "#2ecc71", "#3498db")
    bucket_count = max(1, min(width, 64))
    bucket_size = 256 // bucket_count
    for channel, color in zip(histogram_rgb, colors):
        buckets = [sum(channel[start : start + bucket_size]) for start in range(0, 256, bucket_size)]
        peak = max(buckets) or 1
        step = width / len(buckets)
        points = []
        for index, count in enumerate(buckets):
            x = index * step
            y = height - (count / peak) * (height - 2)
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=1)


def run() -> None:
    app = MainWindow()
    app.mainloop()
