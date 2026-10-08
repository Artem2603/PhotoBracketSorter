"""Settings dialog and camera profiles dialog (feature 10, 1.2.0).

Second step of the gradual `main_window.py` split — see `app/gui/dialogs.py`
for the pattern this follows: a mixin holding the same method bodies
unchanged, so `MainWindow`'s public interface and every existing GUI test
keep working without modification.
"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.camera_profiles import (
    CameraProfile,
    apply_profile,
    create_profile,
    delete_profile,
    export_profile_json,
    import_profile_json,
    list_all_profiles,
    profile_from_confirmed_series,
    update_profile,
)
from app.errors import PhotoBracketSorterError
from app.gui.gui_constants import ADVANCED_ONLY_SETTINGS_TABS, LANGUAGE_LABELS
from app.models import AppSettings
from app.settings import load_settings, save_settings, settings_from_dict, validate_settings
from app.theme_manager import apply_theme
from app.version import APP_NAME


class SettingsViewMixin:
    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("action.settings"))
        dialog.geometry("860x660")
        dialog.minsize(720, 540)
        dialog.transient(self)
        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        variables: dict[str, tk.Variable] = {}

        def tab(key: str) -> ttk.Frame:
            frame = ttk.Frame(notebook, padding=12)
            # Widgets are always built, so every setting still lands in
            # `variables` and gets saved unchanged — simple mode only hides the
            # tab from the notebook, it never drops a field from being managed.
            if self.settings.ui_mode == "advanced" or key not in ADVANCED_ONLY_SETTINGS_TABS:
                notebook.add(frame, text=self.i18n.gettext(key))
            return frame

        general = tab("settings.general")
        analysis = tab("settings.analysis")
        bracketing = tab("settings.bracketing")
        classification = tab("settings.classification")
        pairing = tab("settings.pairing")
        output = tab("settings.output")
        performance = tab("settings.performance")
        reports = tab("settings.reports")
        advanced = tab("settings.advanced")

        self._settings_combo(general, variables, "language", ("ru", "en"))
        self._settings_combo(general, variables, "theme", ("system", "light", "dark"))
        self._settings_combo(general, variables, "accent", ("blue", "green", "purple", "orange"))
        self._settings_entry(general, variables, "ui_scale")
        self._settings_check(general, variables, "remember_paths")
        self._settings_check(general, variables, "remember_window")

        self._settings_check(analysis, variables, "include_subfolders")
        self._settings_check(analysis, variables, "include_hidden_files")
        self._settings_check(analysis, variables, "skip_existing_sorted_folders")
        self._settings_entry(analysis, variables, "allowed_extensions")
        self._settings_entry(analysis, variables, "excluded_folder_names")
        self._settings_combo(analysis, variables, "detection_mode", ("STRICT", "NORMAL", "SOFT"))

        self._settings_combo(bracketing, variables, "bracket_size_mode", ("AUTO", "THREE", "FIVE", "BOTH"))
        self._settings_entry(bracketing, variables, "max_bracket_adjacent_delta_seconds")
        self._settings_entry(bracketing, variables, "max_bracket_duration_seconds_3")
        self._settings_entry(bracketing, variables, "max_bracket_duration_seconds_5")
        self._settings_entry(bracketing, variables, "min_exposure_span_ev")
        self._settings_entry(bracketing, variables, "exposure_step_tolerance")
        self._settings_check(bracketing, variables, "require_exposure_for_auto_series")
        self._settings_entry(bracketing, variables, "min_auto_confidence")

        self._settings_check(classification, variables, "enable_hdr7")
        self._settings_entry(classification, variables, "max_bracket_duration_seconds_7")
        self._settings_check(classification, variables, "enable_hdr9")
        self._settings_entry(classification, variables, "max_bracket_duration_seconds_9")
        self._settings_check(classification, variables, "enable_incomplete_bracket_detection")
        self._settings_check(classification, variables, "enable_focus_bracket_detection")
        self._settings_entry(classification, variables, "focus_bracket_exposure_tolerance_ev")
        self._settings_entry(classification, variables, "focus_bracket_min_focus_spread_ratio")
        self._settings_entry(classification, variables, "focus_bracket_visual_similarity_min")
        self._settings_check(classification, variables, "enable_burst_detection")
        self._settings_entry(classification, variables, "burst_visual_similarity_threshold")
        self._settings_check(classification, variables, "enable_panorama_detection")
        self._settings_entry(classification, variables, "panorama_min_frames")
        self._settings_entry(classification, variables, "panorama_visual_similarity_min")
        self._settings_entry(classification, variables, "panorama_visual_similarity_max")

        self._settings_entry(pairing, variables, "max_pair_delta_seconds")
        self._settings_check(pairing, variables, "use_name_for_pairing")
        self._settings_check(pairing, variables, "use_exif_for_pairing")
        self._settings_check(pairing, variables, "use_preview_for_pairing")
        self._settings_entry(pairing, variables, "pair_accept_score_normal")
        self._settings_entry(pairing, variables, "pair_accept_score_strict")
        self._settings_entry(pairing, variables, "pair_accept_score_soft")
        self._settings_entry(pairing, variables, "pair_second_best_margin")
        self._settings_check(pairing, variables, "mixed_timezone_safe_mode")

        self._settings_check(output, variables, "store_companions_inside_series")
        self._settings_check(output, variables, "preserve_source_subfolders")
        self._settings_combo(output, variables, "duplicate_behavior", ("rename", "skip", "overwrite"))
        self._settings_entry(output, variables, "output_folder_template")
        self._settings_check(output, variables, "preserve_file_times")
        self._settings_check(output, variables, "preview_only")
        self._settings_check(output, variables, "require_plan_before_operation")
        self._settings_check(output, variables, "verify_sha256")

        self._settings_entry(performance, variables, "worker_count")
        self._settings_entry(performance, variables, "thumbnail_size")
        self._settings_entry(performance, variables, "preview_cache_items")
        self._settings_entry(performance, variables, "exiftool_batch_size")
        self._settings_check(performance, variables, "preview_similarity_enabled")
        self._settings_entry(performance, variables, "visual_similarity_threshold")

        self._settings_entry(reports, variables, "report_formats")
        self._settings_combo(reports, variables, "report_language", ("ru", "en"))
        self._settings_combo(reports, variables, "report_detail", ("compact", "normal", "full"))
        self._settings_check(reports, variables, "csv_english_headers")
        self._settings_check(reports, variables, "privacy_redact_absolute_paths")

        self._settings_entry(advanced, variables, "exiftool_path")
        self._settings_combo(advanced, variables, "log_level", ("DEBUG", "INFO", "WARNING", "ERROR"))
        ttk.Label(advanced, text=self.i18n.gettext("settings.restart_scale"), wraplength=520).pack(anchor="w", pady=(8, 0))

        buttons = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=self.i18n.gettext("settings.import"), command=lambda: self._import_settings(dialog)).pack(side="left")
        ttk.Button(buttons, text=self.i18n.gettext("settings.export"), command=self._export_settings).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("settings.reset"), command=lambda: self._reset_settings(dialog)).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.open"), command=self._open_profiles).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("settings.close"), command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text=self.i18n.gettext("settings.save"), command=lambda: self._apply_settings_dialog(variables, dialog)).pack(side="right", padx=(0, 8))

    def _settings_label(self, name: str) -> str:
        """Human-readable label for a settings field; the raw identifier is the fallback."""
        key = f"settings.field.{name}"
        text = self.i18n.gettext(key)
        return name if text == key else text

    def _settings_check(self, parent, variables: dict[str, tk.Variable], name: str) -> None:
        var = tk.BooleanVar(value=bool(getattr(self.settings, name)))
        variables[name] = var
        ttk.Checkbutton(parent, text=self._settings_label(name), variable=var).pack(anchor="w", pady=3)

    def _settings_entry(self, parent, variables: dict[str, tk.Variable], name: str) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=self._settings_label(name), width=46).pack(side="left")
        value = getattr(self.settings, name)
        if isinstance(value, tuple):
            value = ", ".join(value)
        var = tk.StringVar(value=str(value))
        variables[name] = var
        ttk.Entry(frame, textvariable=var).pack(side="left", fill="x", expand=True)

    def _settings_combo(self, parent, variables: dict[str, tk.Variable], name: str, values: tuple[str, ...]) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=self._settings_label(name), width=46).pack(side="left")
        value = getattr(self.settings, name)
        if hasattr(value, "value"):
            value = value.value
        var = tk.StringVar(value=str(value))
        variables[name] = var
        ttk.Combobox(frame, textvariable=var, values=values, state="readonly").pack(side="left", fill="x", expand=True)

    def _apply_settings_dialog(self, variables: dict[str, tk.Variable], dialog: tk.Toplevel) -> None:
        snapshot = self.settings.snapshot()
        try:
            for name, var in variables.items():
                raw = var.get()
                current = getattr(self.settings, name)
                if isinstance(current, bool):
                    value = bool(raw)
                elif isinstance(current, int) and not isinstance(current, bool):
                    value = int(raw)
                elif isinstance(current, float):
                    value = float(raw)
                elif isinstance(current, tuple):
                    value = tuple(part.strip() for part in str(raw).split(",") if part.strip())
                elif hasattr(current, "__class__") and current.__class__.__name__.endswith("Mode"):
                    value = current.__class__(str(raw))
                else:
                    value = str(raw)
                setattr(self.settings, name, value)
            self.settings.max_bracket_duration_seconds = max(
                self.settings.max_bracket_duration_seconds_3,
                self.settings.max_bracket_duration_seconds_5,
            )
            validate_settings(self.settings)
            save_settings(self.settings, self.settings_path)
        except Exception as exc:
            self.settings = settings_from_dict(snapshot)
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.i18n.set_language(self.settings.language)
        self.language_var.set(LANGUAGE_LABELS.get(self.settings.language, "English"))
        apply_theme(self, self.settings)
        self._refresh_texts()
        self._apply_theme_colors()
        dialog.destroy()

    def _export_settings(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if path:
            save_settings(self.settings, Path(path))

    def _import_settings(self, dialog: tk.Toplevel) -> None:
        path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"),))
        if path:
            self.settings = load_settings(Path(path))
            save_settings(self.settings, self.settings_path)
            dialog.destroy()
            self._open_settings()

    def _reset_settings(self, dialog: tk.Toplevel) -> None:
        self.settings = AppSettings()
        save_settings(self.settings, self.settings_path)
        dialog.destroy()
        self._open_settings()

    def _open_profiles(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self.i18n.gettext("profiles.title"))
        dialog.geometry("820x520")
        dialog.minsize(700, 420)
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True, side="top")
        columns = ("name", "camera")
        tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        tree.heading("name", text=self.i18n.gettext("profiles.column.name"))
        tree.heading("camera", text=self.i18n.gettext("profiles.column.camera"))
        tree.column("name", width=280, anchor="w")
        tree.column("camera", width=260, anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        scroll.pack(fill="y", side="right")
        tree.pack(fill="both", expand=True, side="left")
        tree.configure(yscrollcommand=scroll.set)

        profiles_by_iid: dict[str, CameraProfile] = {}

        def refresh() -> None:
            tree.delete(*tree.get_children())
            profiles_by_iid.clear()
            for profile in list_all_profiles(self.profiles_path):
                label = f"{profile.name}{' *' if profile.is_builtin else ''}"
                camera = " ".join(part for part in (profile.manufacturer, profile.model) if part)
                tree.insert("", "end", iid=profile.profile_id, values=(label, camera))
                profiles_by_iid[profile.profile_id] = profile

        def selected_profile() -> CameraProfile | None:
            selection = tree.selection()
            if not selection:
                return None
            return profiles_by_iid.get(selection[0])

        def do_apply() -> None:
            profile = selected_profile()
            if profile is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.select_item"))
                return
            if profile.is_auto:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.auto_apply_redirect"))
                return
            try:
                self.settings = apply_profile(self.settings, profile)
                save_settings(self.settings, self.settings_path)
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            self._log(self.i18n.gettext("profiles.applied", name=profile.name))

        def do_new() -> None:
            name = self._prompt_text(self.i18n.gettext("profiles.name_prompt"))
            if not name:
                return
            try:
                create_profile(
                    name,
                    path=self.profiles_path,
                    manufacturer="",
                    model="",
                    bracket_size_mode=self.settings.bracket_size_mode.value,
                    min_exposure_span_ev=self.settings.min_exposure_span_ev,
                    exposure_step_tolerance=self.settings.exposure_step_tolerance,
                    max_bracket_duration_seconds_3=self.settings.max_bracket_duration_seconds_3,
                    max_bracket_duration_seconds_5=self.settings.max_bracket_duration_seconds_5,
                    max_bracket_adjacent_delta_seconds=self.settings.max_bracket_adjacent_delta_seconds,
                    exposure_source_priority=self.settings.exposure_source_priority,
                    max_pair_delta_seconds=self.settings.max_pair_delta_seconds,
                    use_name_for_pairing=self.settings.use_name_for_pairing,
                    use_exif_for_pairing=self.settings.use_exif_for_pairing,
                    use_preview_for_pairing=self.settings.use_preview_for_pairing,
                    min_auto_confidence=self.settings.min_auto_confidence,
                )
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            refresh()

        def do_edit() -> None:
            profile = selected_profile()
            if profile is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.select_item"))
                return
            if profile.is_builtin:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.builtin_readonly"))
                return
            name = self._prompt_text(self.i18n.gettext("profiles.name_prompt"), initial=profile.name)
            if not name:
                return
            try:
                update_profile(profile.profile_id, path=self.profiles_path, name=name)
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            refresh()

        def do_delete() -> None:
            profile = selected_profile()
            if profile is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.select_item"))
                return
            if profile.is_builtin:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.builtin_readonly"))
                return
            if not messagebox.askyesno(APP_NAME, self.i18n.gettext("profiles.confirm_delete", name=profile.name)):
                return
            try:
                delete_profile(profile.profile_id, path=self.profiles_path)
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            refresh()

        def do_export() -> None:
            profile = selected_profile()
            if profile is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.select_item"))
                return
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),), initialfile=f"{profile.name}.json")
            if path:
                Path(path).write_text(export_profile_json(profile), encoding="utf-8")

        def do_import() -> None:
            path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"),))
            if not path:
                return
            try:
                imported = import_profile_json(Path(path).read_text(encoding="utf-8"))
                create_profile(
                    imported.name,
                    path=self.profiles_path,
                    **{
                        field.name: getattr(imported, field.name)
                        for field in dataclasses.fields(CameraProfile)
                        if field.name not in {"profile_id", "schema_version", "name", "is_builtin"}
                    },
                )
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            refresh()

        def do_from_series() -> None:
            files = self._selected_files()
            series = None
            if files and self.series_manager is not None:
                series = self.series_manager.find_by_file(files[0])
            if series is None:
                messagebox.showinfo(APP_NAME, self.i18n.gettext("profiles.select_series_file"))
                return
            name = self._prompt_text(self.i18n.gettext("profiles.name_prompt"), initial=f"{series.series_id}")
            if not name:
                return
            profile = profile_from_confirmed_series(series, name)
            try:
                create_profile(
                    name,
                    path=self.profiles_path,
                    **{
                        key: getattr(profile, key)
                        for key in (
                            "manufacturer",
                            "model",
                            "description",
                            "bracket_size_mode",
                            "min_exposure_span_ev",
                            "exposure_step_tolerance",
                            "max_bracket_duration_seconds_3",
                            "max_bracket_duration_seconds_5",
                            "max_bracket_adjacent_delta_seconds",
                            "exposure_source_priority",
                            "max_pair_delta_seconds",
                            "use_name_for_pairing",
                            "use_exif_for_pairing",
                            "use_preview_for_pairing",
                            "min_auto_confidence",
                        )
                    },
                )
            except PhotoBracketSorterError as exc:
                messagebox.showerror(APP_NAME, exc.user_message)
                return
            refresh()

        buttons = ttk.Frame(body, padding=(0, 8, 0, 0))
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text=self.i18n.gettext("profiles.apply"), command=do_apply).pack(side="left")
        ttk.Button(buttons, text=self.i18n.gettext("profiles.new"), command=do_new).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.edit"), command=do_edit).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.delete"), command=do_delete).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.import"), command=do_import).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.export"), command=do_export).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.from_series"), command=do_from_series).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text=self.i18n.gettext("profiles.close"), command=dialog.destroy).pack(side="right")

        refresh()
