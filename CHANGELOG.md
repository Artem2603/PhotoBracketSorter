# Changelog

## 1.2.1 (maintenance)

A hardening pass over 1.2.0 driven by a full code review: data-safety fixes in
the transfer engines and the undo journal, detector and pairing corrections,
robustness against damaged configuration files, and a visual refresh of the
main window. No file-layout or report-format changes; existing sessions,
journals and profiles keep working.

Test suite: 656 tests (527 in 1.2.0), green on Windows paths and on Linux, in
every timezone.

### Fixed — data safety

- **Move with duplicate policy `overwrite` could destroy a photo.** Two sources
  with the same name in different subfolders (`100APPLE/IMG_0001.JPG`,
  `101APPLE/IMG_0001.JPG`) were planned onto one destination; the second copy
  overwrote the first *after* its source had already been deleted. `overwrite`
  now only applies to files that already exist on disk; same-named files within
  one run get `_02`, `_03…` suffixes, and the plan refuses two operations with
  the same destination outright. `skip` names the files it drops in the warning.
- A cancelled Undo-move no longer marks the whole move as undone; the remaining
  files can be restored by running Undo again (already-restored files are
  skipped, not reported as errors).
- Undo now checks the file size even when SHA-256 verification was off, so it
  never copies a foreign file back over the original's path.
- `.partial` files are removed on *every* copy failure (disk full, I/O error,
  locked file), not only on cancel. Read-only sources and partials (camera
  "protected" shots) are deleted correctly on Windows instead of ending as
  `DELETE_FAILED` or `PermissionError`.
- Closing the window during a Copy/Move now asks for confirmation, cancels the
  worker and waits for the current file, so the report and the journal entry are
  written; previously the thread was killed and nothing was recorded.
- Journal: a damaged `operations_journal.json` is set aside (`.broken-<time>`)
  instead of being overwritten; a BOM or a `null` entry no longer loses the
  history; saves are atomic (write-then-rename). Settings saves are atomic too.
- "Processed N of N" in reports and the summary dialog no longer counts files a
  cancelled run never attempted.

### Fixed — detection and pairing

- Auto-exposure bursts with a slight shutter/ISO drift (e.g. 1/125, 1/140,
  1/160 at EC 0) were accepted as HDR3/HDR5 series and routed to
  `BRACKETING_*`. The exposure-span check is now a hard gate; such groups stay
  visible as near misses in the review queue but are never auto-accepted.
- RAW + HEIC/JPEG pairs where one side carries a UTC offset and the other does
  not were only found when the PC's timezone equalled the camera's offset; the
  candidate index is now timezone-independent (and pre-1970 timestamps no
  longer raise on Windows).
- In ExifTool-fallback mode TIFF-container RAWs (NEF, ARW, CR2, ORF, RW2, PEF…)
  were classified as rendered TIFF and filed under `HEIC/`; they are RAW again.
- Perceptual hashes of 16-bit images no longer clip to white.
- `Restore automatic decision` in the review queue gave the restored series an
  id and folder already used by another series.
- `store_companions_inside_series` was ignored: companions were always moved
  into the series folder. The setting is honoured now.
- `Create profile from confirmed series` failed for any bracket shot in under
  0.8 s; the adjacent-frame limit is derived from the real series now.
- Auto-configure no longer treats tiny exposure drift as bracket evidence, and
  applying only a duration suggestion can no longer produce settings that fail
  validation.
- The review queue no longer floods with one near-miss item per sliding window
  of a long burst, and items for excluded or manually overridden files clear.
- Any series operation re-selected every member of every series, undoing a
  user's deselection of a frame.
- Camera profile import reports a clear error for malformed JSON instead of a
  traceback; bundled profiles keep a stable id between loads.

### Fixed — robustness

- A damaged `settings.json` (trailing comma, Notepad BOM, `"worker_count": "4"`,
  unknown enum value) no longer prevents the application from starting: the
  window opens with defaults, says why, and leaves the file untouched for
  inspection. Hand-edited scalar values are coerced; every other problem is a
  `SettingsError`.
- Schema-1 settings migration was a no-op (defaults were merged in first), so an
  old `max_bracket_duration_seconds` never reached the per-size limits.
- ExifTool: file names are passed through a UTF-8 argfile (`-@`), the way
  ExifTool documents for Windows, so non-ASCII paths such as
  `C:\Users\Артём\DCIM` are read instead of silently losing all metadata; the
  per-batch timeout scales with the batch size; a blocked or quarantined
  `exiftool.exe` raises an application error instead of crashing the analysis,
  and a failing batch degrades those files to fallback mode with a warning
  rather than aborting everything. A system `exiftool` on PATH, or an explicit
  `exiftool_path`, is probed and used; previously anything without the bundled
  `exiftool_files` folder was silently ignored.
- Metadata reading reports progress and honours Cancel between ExifTool
  batches (large folders looked frozen at "Определение форматов 0/N").
- Extended classification (burst/panorama labels) extracted up to three
  embedded previews per RAW frame with one ExifTool process each, for every
  frame of every burst, during analysis. It now samples at most six frames per
  group and uses a single ExifTool call per group.
- Folders whose path merely contains the letters "mtp" (`C:\Photos\MTP_backup`)
  were rejected as phone paths; only shell-namespace (`::{…}`) and `mtp://`
  paths are rejected now. Windows reserved names (`CON`, `NUL`, `COM1`…) and
  attribute placeholders (`{date.year}`) in folder templates are handled.
- Opening a `.pbsession` saved on another machine no longer overwrites this
  machine's language, theme, window geometry, `exiftool_path` or log level.
- First-run language follows the Windows display language (the old check read
  `LANG`, which Windows never sets, so Russian users got English).
- `report.csv` cells starting with `=`, `+`, `-` or `@` are prefixed with an
  apostrophe so Excel does not execute them as formulas.
- A ttkbootstrap `Style` outliving its Tk root (second window in one process)
  no longer fails with "application has been destroyed"; the suite is also
  timezone-independent and runs on Linux/macOS.

### Changed — interface

- Action bar in two rows with colour roles: Analyze (accent), Copy (green),
  Move (orange), Cancel (red outline), utilities in grey; thirteen identical
  buttons used to overflow the window in Russian.
- Summary cards have borders and coloured counts and act as filter shortcuts
  (click again to clear). Table rows are coloured by outcome: errors red, review
  amber, series in the accent colour, skipped/deselected muted; the "selected"
  column shows a check mark instead of "yes"/"no".
- The main preview shows embedded JPEG previews for RAW/DNG (and HEIC without a
  decoder) through ExifTool instead of a placeholder, decoded on a background
  thread so selecting rows never freezes the window. `pillow-heif` is detected
  automatically for full HEIC previews.
- `system` theme follows the Windows "choose your app mode" setting; theme names
  in the toolbar are localized.
- Settings dialog shows human-readable Russian/English labels instead of field
  identifiers; progress phases, the context menu, the move confirmation and the
  Help dialog (now with the workflow and shortcuts) are localized; Help, review
  queue, history and profile dialogs got their missing scrollbars.
- A remembered window geometry is clamped to the current screen; folder pickers
  open at the current path.

### Removed

- `app/workers.py` (unused event classes).

## 1.2.0 (in progress)

### Added — automatic analysis configuration (feature 1)

- New "Автонастройка" / "Auto-configure" action: scans a folder's metadata and
  suggests `bracket_size_mode`, `min_exposure_span_ev`, `exposure_step_tolerance`,
  `max_bracket_adjacent_delta_seconds`, `max_bracket_duration_seconds_3/_5`,
  `min_auto_confidence`, and `max_pair_delta_seconds` from what the files
  actually contain, plus an informational report of cameras found, RAW/rendered
  formats present, sub-second EXIF availability, and the reliable exposure
  source. Nothing is applied until the user reviews the dialog and clicks
  Apply; each suggestion can be unchecked or edited first ("Изменить").
- Suggestions are computed by running the real `BracketDetector` and
  `PairMatcher` against a deliberately loosened settings profile, not a second
  approximate algorithm, so they can never disagree with the detector they are
  advising. Clusters that only share timing (no observed exposure variation)
  are excluded before any suggestion is built, so an ordinary burst is never
  mistaken for a bracket. Verified to work for cameras with no `Make`/`Model`
  tags at all, and for cameras other than Xiaomi/ProShot.
- `app/auto_configure.py`. Tests: `tests/test_auto_configure.py` (23),
  `tests/test_gui_auto_configure.py` (7).

### Added — manual review queue (feature 2)

- New "Требуют проверки" / "Needs review" screen listing everything borderline:
  low-confidence series, uneven EV step, series with partially-missing
  companions, near-miss bracket candidates that scored real evidence but
  missed the acceptance threshold, ambiguous RAW+preview pairs, mixed time
  sources, destination name conflicts, series with mismatched frame
  dimensions, and every manual edit (so the user has one place to review or
  undo them).
- Actions, all routed through the same `SeriesManager` and `assign_category`
  used everywhere else — no second state to fall out of sync: Confirm HDR3 /
  HDR5 (promotes a near-miss candidate), Not a bracket (dismisses it — the
  fact is recorded on the files themselves via `decision_reasons`, not a new
  side table), Split, Merge with neighbor (completes a series with frames
  selected in the main table), Exclude, Restore automatic decision (re-runs
  the real `BracketDetector` on exactly those files under the current
  thresholds — not a guess).
- Fixed in passing: `RawHeicPair.ambiguous` was computed but always
  constructed as `False`, so it — and the `ambiguous` field written to
  `summary.json` — never reflected reality. It is now wired through, and the
  "companion is ambiguous" warning is now recorded even when the pair is
  rejected outright, not only when its score also happened to clear the
  threshold.
- `app/review_queue.py`. Tests: `tests/test_review_queue.py` (29),
  `tests/test_gui_review_queue.py` (7).

### Added — universal camera profiles (feature 3)

- A profile bundles the same knobs `AppSettings` already has (bracket sizes,
  EV span/tolerance, per-size duration limits, adjacent-frame gap, exposure
  source priority, pairing thresholds, minimum confidence) under a name —
  deliberately not a parallel vocabulary that needs translating, so applying
  one is a direct field copy plus the existing settings validation. No camera
  model is special-cased anywhere in detection or pairing; a profile only ever
  seeds those same generic settings.
- Two built-in profiles: **Universal** (the conservative defaults) and
  **Auto-detect**, which runs feature 1's auto-configuration instead of
  copying fixed numbers — there is nothing fixed to copy blind.
- `profiles/generic.json` and `profiles/xiaomi_proshot.json` predated this
  feature and were never loaded by anything — the same defect class as the
  P0-2 dead settings. They are now read as built-in example profiles through a
  real schema 1 → 2 migration (`migrate_profile_dict`), not just a version
  number sitting unused.
- Full CRUD for user profiles (create, edit, delete — built-ins are
  read-only), JSON import/export, and "create a profile from a confirmed
  series," which derives every numeric value from what that series actually
  measured (its EV step, duration, confidence, exposure source), the same
  "read real algorithm output, don't guess" rule feature 1 follows.
- New `exposure_source_priority` setting: the search order
  `resolve_group_exposure` uses when picking one exposure source per group was
  a hardcoded module constant; it is now a real, configurable, validated
  `AppSettings` field, threaded through bracket detection, the review queue,
  and manual-series exposure reporting. A profile that reorders it measurably
  changes which source a group resolves to — verified directly, not just
  stored.
- User profiles live under `%APPDATA%\PhotoBracketSorter\camera_profiles.json`,
  the same convention as `settings.json`.
- `app/camera_profiles.py`. Tests: `tests/test_camera_profiles.py` (48),
  `tests/test_gui_camera_profiles.py` (5).

### Added — decision explanations (feature 4)

- New "Почему так определено?" / "Why was this decided?" context-menu entry
  for any file: shows the time source, the exposure source, every frame's EV
  and adjacent gap, duration, camera/lens/dimension match, name and visual
  similarity for a RAW+preview pair, the full points-added/points-removed
  score breakdown, the total, the confidence, and (when analysis context is
  available) the exact review-queue reasons that apply to it.
- Assembled entirely from state the detector and matcher already computed —
  `BracketDetector`/`PairMatcher` now record a `score_breakdown` (label,
  points) on `BracketCandidate`/`BracketSeries`/`RawHeicPair` in the order
  each component was actually scored. `camera_match`/`lens_match`/
  `dimensions_match` are read off the real `BRACKET_DIMENSIONS_OK` /
  `BRACKET_LENS_OK` / `PAIR_CAMERA_MATCH` / `PAIR_GEOMETRY_MATCH` reason codes,
  not recomputed by a second rule that could disagree with the one that made
  the decision. Review reasons come from `app.review_queue.build_review_queue`
  itself, not a duplicate check.
- `app/decision_explainer.py`. Tests: `tests/test_decision_explainer.py` (18),
  `tests/test_gui_decision_explainer.py` (3).

### Added — visual series viewer (feature 5)

- New "Просмотр серии" / "View series" context-menu entry: shows every frame
  of an HDR3/HDR5 series side by side with a real decoded image, EXIF
  (shutter, aperture, ISO, EV) and a per-channel histogram under each
  thumbnail, and highlight/shadow clipping warnings.
- **Real embedded RAW previews, not a placeholder.** Previously every RAW file
  showed a fixed "preview placeholder" string — no image at all. Most RAW
  formats embed a full-size or near-full-size JPEG under `PreviewImage`,
  `JpgFromRaw`, or `ThumbnailImage`; `ExifToolRunner.extract_embedded_preview`
  extracts it through the same bundled ExifTool the rest of the app already
  uses, tried in that size order, falling back gracefully (with a message, not
  a crash) when a RAW has none.
- A focused, zoomable, pannable view (native canvas panning via
  `scan_mark`/`scan_dragto`) that keeps the same zoom level when switching
  frames — Prev/Next, arrow keys, or clicking a thumbnail — so comparing two
  exposures is apples-to-apples. A "Мигание" / "Blink" toggle auto-advances
  between frames on a timer for quick A/B comparison.
- Extraction runs on a background thread (ExifTool per frame, tens to hundreds
  of ms each) through the same worker-queue convention analysis and
  auto-configure already use; the GUI thread never blocks on it.
- `app/series_preview.py`, plus `ExifToolRunner.extract_binary_tag` /
  `extract_embedded_preview`. Tests: `tests/test_series_preview.py` (17),
  `tests/test_gui_series_viewer.py` (6).

### Added — sort-result preview (feature 6)

- New "Предпросмотр сортировки" / "Sort preview" action: shows the full
  destination folder tree before anything is touched — every file's target
  path, series folder names, name conflicts (a file whose destination name
  differs from its source name was renamed to resolve a collision), excluded
  files (review/skip/deselected, with the reason), total size, required
  space, and free space at the target. Built entirely from the existing
  `CopyPlan`/`AnalysisResult` — no second decision engine.
- Interactive, before anything runs: exclude a file, change its category,
  rename a series' destination folder, change a single file's destination
  folder, or change the duplicate-conflict policy — every edit re-plans in
  place and redraws the tree. Copy/Move buttons inside the dialog start the
  real transfer directly; in `preview_only` mode only Close is offered, since
  a dry run must never be one click from becoming a real one.
- New `BracketSeries.custom_name` and `SeriesManager.rename_series` — a real,
  validated (non-empty after sanitizing, unique within its HDR3/HDR5
  namespace) override of the default `Series_NNNN`/`Manual_NNNN` folder name,
  not a cosmetic label.
- Fixed in passing: rebuilding a plan against the same destination with
  `output_folder_template` containing `{time}` used to mint a fresh
  timestamped folder on every rebuild — before a single file had moved.
  `build_copy_plan` now takes an optional `pinned_output_root` so re-planning
  after an edit reflects the edit without drifting the destination.
- `app/sort_preview.py`. Tests: `tests/test_sort_preview.py` (12, including
  the `pinned_output_root` regression), `tests/test_gui_sort_preview.py` (8),
  plus 8 new `SeriesManager.rename_series` tests in `tests/test_series_manager.py`.

### Added — operation journal and Move undo (feature 7)

- Every Copy/Move now writes a persistent manifest entry to
  `%APPDATA%\PhotoBracketSorter\operations_journal.json`: per file, the
  source and destination path, size, mtime, fingerprint, resulting SHA-256,
  operation type, status, error, and timestamp — reusing the verification
  data `CopyEngine`/`MoveEngine` already compute, not a second pass over the
  files. New "История операций" / "Operation history" screen lists every run.
- "Отменить перемещение" / "Undo move" on a completed Move, gated on three
  checks against the *current* disk state, not just what the journal recorded:
  the destination file must still exist, its SHA-256 must still match what the
  Move actually wrote, and the original source path must be free. Any of
  those failing blocks the undo with a specific reason instead of silently
  overwriting something.
- Undo goes through the same partial-file + atomic-replace + hash-verify
  discipline as the original `MoveEngine`, produces its own journal entry
  (`operation_type="undo_move"`, so it cannot itself be undone into a loop),
  and a cancelled undo mid-run leaves a correct partial report — restored
  files stay restored, untouched ones are marked `SKIPPED` (not `CANCELLED`),
  matching the exact status convention `MoveEngine._mark_untouched` already
  established, not a diverging one.
- `app/operation_journal.py`. Tests: `tests/test_operation_journal.py` (24),
  `tests/test_gui_operation_journal.py` (7).

### Added — extended classification (feature 8)

- New informational `PhotoFile.group_classification` label, separate from
  `destination_category`: HDR3/5/7/9, incomplete bracket, focus bracket,
  burst, probable panorama, RAW+JPEG/HEIC pair, single RAW, single rendered,
  unknown group, needs review. It is read-only overlay data computed after
  routing is already final — nothing in this feature moves a file anywhere
  by itself, and probable-panorama clusters are never aggressively
  auto-sorted, per the original requirement.
- HDR7 and HDR9 are now real, independently toggleable bracket sizes
  (`enable_hdr7`, `enable_hdr9`, own duration thresholds), sitting alongside
  the existing 3/5 detection in `bracket_detection.py` and
  `series_manager.py` (`BRACKETING_7`/`BRACKETING_9` destinations, new
  output folders in `MAIN_OUTPUT_DIRS`).
- New `app/extended_classification.py` clusters RAW files left over after
  bracket/pair matching (by camera + time gap) and classifies each cluster
  with a decision tree: exposure variance vs. a near-miss bracket size →
  incomplete bracket; flat exposure + varying focus distance → focus
  bracket; flat exposure + near-identical frames (dHash) → burst; flat
  exposure + moderate frame-to-frame similarity + enough frames → probable
  panorama. Every detector has its own enable toggle and thresholds
  (`enable_incomplete_bracket_detection`, `enable_focus_bracket_detection`,
  `focus_bracket_exposure_tolerance_ev`, `focus_bracket_min_focus_spread_ratio`,
  `focus_bracket_visual_similarity_min`, `enable_burst_detection`,
  `burst_visual_similarity_threshold`, `enable_panorama_detection`,
  `panorama_min_frames`, `panorama_visual_similarity_min`,
  `panorama_visual_similarity_max`) so any of them can be fully disabled.
  The expensive visual-similarity check (RAW preview extraction + dHash,
  reusing `series_preview`/`perceptual_hash` rather than a second decoder)
  only runs on already-small, already-time-clustered candidate groups.
- The three "needs a human look" labels (incomplete bracket, focus bracket,
  probable panorama) are surfaced through the existing review queue as a
  new `ReviewReason.EXTENDED_CLASSIFICATION` item — one item per distinct
  (label, evidence) cluster — instead of a second, parallel notification
  system. Confident labels (HDR3/5/7/9, pairs, singles, burst, unknown) are
  not surfaced; they are informational only.
- New "Group" column in the main results table and a new "Классификация" /
  "Classification" settings tab holding all feature 8 toggles and
  thresholds. 34 new i18n keys, and `validate_catalogs()` now also checks
  `GroupClassification` and `ReviewReason` enum coverage against both
  catalogs.
- `app/extended_classification.py`. Tests: `tests/test_extended_classification.py`
  (29), 6 new cases in `tests/test_review_queue.py`,
  `tests/test_gui_extended_classification.py` (7).

### Added — session save (.pbsession) and EXIF cache (feature 9)

- New "Сохранить сессию" / "Save session" and "Открыть сессию" / "Open
  session" actions write and read a `.pbsession` JSON file: source and
  output paths, the full settings snapshot, a per-file EXIF cache, and the
  manual fixes layered on top of automatic classification. The photos
  themselves are never written to it — only paths and the metadata already
  extracted from them.
- The EXIF cache (`app/metadata_cache.py`) stores every file's already-read
  `PhotoMetadata`, keyed by relative path plus the size and mtime that were
  true when it was cached. Reopening a session skips ExifTool entirely for
  any file whose (size, mtime) still match; anything new, changed, or
  missing from the cache is read fresh through the normal
  `MetadataReader` pipeline, exactly as if there were no cache — proven by
  an end-to-end test that opens a real folder twice and asserts
  `MetadataReader.read_many` is never called on the second, fully-cached
  pass, and is called with only the one changed file when one file changed.
- Automatic classification (bracket detection, pairing, extended
  classification) is fully deterministic from settings + metadata, so
  `app/session.py` never persists it directly — only the deltas the user
  introduced on top of it, keyed by each file's relative path (the one
  identifier stable across a fresh reanalysis): which series exist (and
  their `custom_name`/`manually_confirmed` flags), manual category
  overrides, deselected files, dismissed near-miss candidates, and files
  explicitly split back to singles. Restoring re-runs the real analysis
  pipeline against the (possibly cached) metadata and then reconciles the
  freshly detected `SeriesManager` state against the saved end state in one
  pass — going through the same `SeriesManager` methods and
  `assign_category` every other manual edit in the app already uses, adding
  no second place that state could live.
- Restoring a saved fix that can no longer be applied (a file was deleted,
  renamed, or moved since the session was saved) is skipped and reported
  back as a warning, never a crash — a session is a resumable snapshot, not
  a hard dependency on the folder never changing.
- A corrupted or unreadable `.pbsession` file raises `SessionError` and
  shows an error dialog; it never touches any other file and never starts
  a background worker.
- `split_series`/`restore_automatic` in `app/review_queue.py` now also mark
  released files with `SPLIT_KEEP_SINGLE_MARKER` (the same lightweight
  "note it on the file, don't add a new state store" pattern
  `DISMISS_NOT_BRACKET_MARKER` already used for review-queue dismissals),
  so a session can tell "the user broke this apart on purpose" apart from
  "this file was simply never grouped."
- `app/session.py`, `app/metadata_cache.py`. Tests:
  `tests/test_session.py` (27, including a real-folder end-to-end restore),
  `tests/test_metadata_cache.py` (13), 3 new cases in
  `tests/test_review_queue.py`, `tests/test_gui_session.py` (8).

### Added — Simple/Advanced UI mode and a first modularization pass (feature 10)

- New `ui_mode` setting ("simple" | "advanced", persisted like every other
  setting) and a "Режим" / "Mode" selector in the top bar. **Advanced** is
  the full interface, unchanged. **Simple** hides the auxiliary actions
  (auto-configure, review queue, sort preview, history, save/open session)
  and the advanced-only settings tabs (Analysis, Bracketing, Classification,
  Pairing, Performance, Reports, Advanced), leaving exactly the 5-step flow:
  choose source, choose output, Analyze, review the results table,
  Copy/Move. Nothing is disabled or skipped in simple mode — buttons are
  hidden with `grid_remove()`/`grid()`, and hidden settings-tab widgets are
  still built and still tracked in the Save dialog's `variables` dict, so no
  setting is ever silently dropped from being managed regardless of which
  mode built the dialog.
- Search, filters, sort, themes, UI scale, window geometry, and keyboard
  shortcuts already existed from earlier work (1.1.1 and 1.2.0 features
  1–9); this feature did not need to add them, only the mode toggle that
  hides/shows access to the advanced ones.
- Started the gradual `main_window.py` split called for in the spec, using
  a mixin pattern: each extracted file defines a `*Mixin` class holding a
  cohesive group of methods with their bodies unchanged, and `MainWindow`
  inherits from all of them — so every existing GUI test kept working
  without modification, and the full 527-test suite (all of it, not just
  the two new files) was run after each extraction step to prove zero
  behavior change. Extracted so far:
  - `app/gui/gui_constants.py` — small constants (`LANGUAGE_LABELS`,
    `LANGUAGE_CODES`, `ADVANCED_ONLY_SETTINGS_TABS`) shared between
    `main_window.py` and the split-out view modules, avoiding a circular
    import between them.
  - `app/gui/dialogs.py` (`DialogsMixin`) — session save/open and the
    About/help dialog.
  - `app/gui/settings_view.py` (`SettingsViewMixin`) — the settings dialog
    and the camera profiles dialog.
  - `main_window.py` went from 2557 to 2132 lines (−17%).
  - **Not done in this pass**: `analysis_view.py`, `review_queue_view.py`,
    `preview_view.py`, and `operations_view.py` are not yet split out;
    `main_window.py` still owns analysis/table/preview/copy-move/undo-move
    wiring directly. The pattern above is proven and the remaining split is
    mechanical, but completing all four was judged lower priority than the
    1.2.0 build-and-release verification pass — tracked as a follow-up, not
    silently dropped.
- `tests/test_gui_ui_mode.py` (8). The dialogs/settings extraction itself
  added no new tests — it is a pure refactor verified by the existing
  527-test suite passing unchanged before and after each step.

## 1.1.1 — stabilization release

No new features. This release fixes defects found during a full audit of 1.1.0
(see `docs/AUDIT_1.1.0.md`). Tests grew from 80 to 207.

### Fixed — exposure detection (P0-1)

- **Real HDR brackets were silently missed.** `ExposureCompensation` had absolute
  priority, so cameras that write `0` to every frame and bracket by shutter speed
  produced an exposure span of `0.0` EV. Such a series lost 45 of 100 points and
  could never reach the acceptance threshold. A −2/0/+2 EV bracket shot at
  1/500, 1/125, 1/30 is now detected via `EV100`.
- **Incompatible exposure sources were compared with each other.** Values from
  `ExposureCompensation`, `BaselineExposure`, and `EV100` were collected per file
  and mixed into one list, yielding meaningless spans and step estimates.
  A group now resolves to exactly one source, used only when present on every
  file; otherwise the next source is tried. Sources are never mixed.
- Values are normalised so larger always means brighter. `EV100` and `LightValue`
  were previously inverted relative to `ExposureCompensation`.
- A source that is flat across the whole group is skipped in favour of one that
  varies, and the reason is recorded in the series reasons.

### Fixed — dead settings (P0-2)

Twenty-one settings could be saved and validated but changed nothing. All are now
connected to real logic:

- `preview_only` — was the most dangerous: the "preview only" checkbox did not
  prevent copying or moving. It is now a true dry run.
- `min_auto_confidence`, `max_bracket_duration_seconds_3`, `max_bracket_duration_seconds_5`
  — thresholds were hard-coded; per-size duration limits now apply separately.
- `excluded_folder_names` — now applied as case-insensitive glob patterns.
- `output_folder_template`, `preserve_source_subfolders`, `duplicate_behavior`
  (`rename` / `skip` / `overwrite`), `preserve_file_times`.
- `use_name_for_pairing`, `use_exif_for_pairing`, `use_preview_for_pairing`.
- `preview_similarity_enabled`, `visual_similarity_threshold` — implemented with
  dHash perceptual comparison (`app/perceptual_hash.py`).
- `report_formats`, `report_language`, `csv_english_headers`, `report_detail` —
  reports are now written in the requested formats, language, and verbosity.
- `exiftool_path`, `exiftool_batch_size`, `worker_count` — metadata reading is now
  parallel and honours the configured ExifTool binary and batch size.
- `log_level`, `mixed_timezone_safe_mode`, `require_plan_before_operation`,
  `preview_cache_items`, `column_widths`, `column_order`.

### Fixed — manual series (P0-3)

- **Every manual series received the same id.** `MANUAL_HDR{size}_{len(files):04d}`
  always evaluated to `MANUAL_HDR3_0003` (or `MANUAL_HDR5_0005`), so separate
  series merged into a single output folder. Each series now has a UUID plus a
  unique sequential id and its own folder.
- Manual series validate their input: exactly N files, RAW only, no corrupted
  files, no duplicate selection. Rejections explain themselves and change nothing.
- Added `app/series_manager.py` as the single owner of series state. Manual series
  now take part in the general analysis and appear in reports.
- Dissolving a series no longer leaves an orphan series in the reports, and
  reassigning a member now dissolves the series it came from.
- GUI and reports shared no statistics code and disagreed after any manual edit.
  Both now call `compute_statistics`.

### Fixed — Copy / Move (P0-4)

- **Reports claimed "originals were not changed" after a Move.** The text report
  said so unconditionally. Reports now record the operation kind, how many
  originals were deleted, and per-operation `source_deleted`.
- **Cancelling produced no report at all.** Cancellation raised before
  `write_reports` ran, so a half-finished Move left no trace. Mid-run cancellation
  now returns a partial result and writes a full report: processed, copied,
  deleted, skipped, and errored. Cancelling before the first file still raises.
- Source verification checked only file size. It now also compares modification
  time and a head+tail fingerprint captured during analysis, so an in-place edit
  that preserves the byte count can no longer be deleted by Move.
- After a completed Move the analysis is reset: it described files that no longer
  exist, and Move could be pressed a second time.
- Every operation now ends with an explicit status; none stay `PENDING`.

### Fixed — RAW + HEIC pairing (P1-5)

- **Non-Latin filenames produced false matches.** `normalize_stem` stripped
  everything outside `[a-z0-9]`, so any Cyrillic or CJK name became an empty
  string, and `SequenceMatcher` scores two empty strings as a perfect `1.0`.
  Unrelated files scored a full name-match bonus. Names are now NFKC-normalised
  and keep alphanumerics from every script; empty stems score `0.0`.
- Ambiguity is now checked from both sides of a pair, and the margin is reported.

### Fixed — TIFF classification (P1-6)

- Every TIFF was treated as RAW, so exports from Lightroom or Photoshop landed in
  `RAW/` and polluted bracket detection. True RAW TIFFs are now identified by CFA
  photometric interpretation, DNG tags, sensor geometry, or single-channel high
  bit depth, and are reported as `TIFF_RAW`; everything else is a rendered export.

### Fixed — privacy (P1-7)

- `privacy_redact_absolute_paths` did not cover the settings snapshot, which was
  written to `summary.json` wholesale, including `last_source_path`,
  `last_output_path`, and `exiftool_path`. The output root was never redacted.
  Both are now covered.

### Performance (P1-8)

- Pairing scored the full cartesian product. Rendered files are now indexed by
  capture time and each RAW only scores candidates inside its time window; the
  runner-up score is computed in one pass instead of a scan per pair.
  **5000 RAW + 5000 previews: 0.42 s, all 5000 pairs found**, versus 25 million
  scoring calls before. The exhaustive path is restored automatically if the user
  lowers the acceptance threshold below what non-time evidence can reach.
- Bracket selection no longer calls `Path.resolve()` inside its selection loop.

### Build and dependencies

- Dependencies pinned exactly (`Pillow==12.3.0`, `ttkbootstrap==1.20.4`).
  ttkbootstrap is held at 1.x on purpose: 2.0.0 changes the Style API in use.
- `PhotoBracketSorter.spec` uses `SPECPATH` instead of `Path.cwd()`, so a build no
  longer depends on the current directory, and it fails loudly if a data folder is
  missing rather than shipping an executable without ExifTool.
- `build.bat` requires a virtual environment and sets `PYTHONHASHSEED` and
  `SOURCE_DATE_EPOCH`. `test.bat` runs unittest with the correct top-level dir.

## 1.1.0

- Added modern Tk/ttkbootstrap GUI shell with RU/EN localization.
- Added settings schema 2 and migration from 1.0.1 settings.
- Added separate Copy and Move actions.
- Added safe Move engine: copy to `.partial`, size/SHA-256 verification, atomic finalization, then source delete.
- Added `DELETE_FAILED` reporting for "copied, but not deleted".
- Added preview fallback and LRU cache helpers.
- Added `--i18n-self-test` and `--move-smoke`.
- Expanded tests from 36 to 80.
- Preserved bundled ExifTool, mixed-timezone safety, RAW + HEIC pairing and bracketing logic.

## 1.0.1

- Bundled Windows ExifTool.
- Added mixed offset-naive/offset-aware datetime safety.
- Added portable smoke tests and SHA256SUMS.
