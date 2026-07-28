# Test Report — Photo Bracket Sorter 1.2.0

Date: 2026-07-28

## Automated test suite

- **527 tests, 0 failures, 0 errors.** Baseline at the start of 1.2.0 work
  (stable 1.1.1) was 207 tests; all 207 remain, unmodified, and still pass.
- Run twice as part of this release: from the working tree (`py -m unittest
  discover -s tests -t .`) and from a freshly extracted copy of
  `PhotoBracketSorter_Project_1.2.0.zip` in a clean temp folder — both gave
  the identical 527/0/0 result.
- Full suite run time: ~27–34s depending on machine load.
- `app.i18n.validate_catalogs()` (called by the suite's own self-test pass):
  RU/EN catalogs complete, including full `DestinationCategory`,
  `OperationStatus`, `GroupClassification`, and `ReviewReason` enum
  coverage.
- AppData isolation held for the entire 1.2.0 work: every GUI test file
  patches `default_settings_path`/`user_profiles_path`/`journal_path` to a
  temp directory, and the real `%APPDATA%\PhotoBracketSorter\settings.json`
  mtime was re-checked after every full-suite run and never changed from
  its pre-existing timestamp.

### Test count by area (new/changed in 1.2.0)

| Area | Tests |
|---|---|
| Feature 1 — automatic analysis configuration | `test_auto_configure.py`, `test_gui_auto_configure.py` |
| Feature 2 — manual review queue | `test_review_queue.py` (extended), `test_gui_review_queue.py` |
| Feature 3 — camera profiles | `test_camera_profiles.py`, `test_gui_camera_profiles.py` |
| Feature 4 — decision explanations | `test_decision_explainer.py`, `test_gui_decision_explainer.py` |
| Feature 5 — visual series viewer | `test_series_preview.py`, `test_gui_series_viewer.py` |
| Feature 6 — sort-result preview | `test_sort_preview.py`, `test_gui_sort_preview.py` |
| Feature 7 — operation journal / Move undo | `test_operation_journal.py` (24), `test_gui_operation_journal.py` (7) |
| Feature 8 — extended classification | `test_extended_classification.py` (29), `test_gui_extended_classification.py` (7), 6 cases in `test_review_queue.py` |
| Feature 9 — session save / EXIF cache | `test_session.py` (27), `test_metadata_cache.py` (13), `test_gui_session.py` (8), 3 cases in `test_review_queue.py` |
| Feature 10 — Simple/Advanced UI mode | `test_gui_ui_mode.py` (8) |

Feature 10's `main_window.py` modularization step (`dialogs.py`,
`settings_view.py`) added no new tests of its own by design — it is a pure
refactor (method bodies moved unchanged into mixins) whose correctness is
verified by the full 527-test suite passing identically before and after
each extraction step.

## Portable build verification (real exe, not just "PyInstaller exited 0")

Build: `python -m PyInstaller PhotoBracketSorter.spec --noconfirm --clean`
from the pinned venv (`Pillow==12.3.0`, `ttkbootstrap==1.20.4`,
`pyinstaller==6.21.0`), `PYTHONHASHSEED=0`, `SOURCE_DATE_EPOCH=1751328000`.

All of the following were run against the **actual portable zip**, extracted
to a clean temp folder with nothing else in it — not just the raw `dist/`
build output:

| Check | Result |
|---|---|
| `PhotoBracketSorter.exe --self-test` | exit 0, `exiftool 13.59` detected, `self-test ok` |
| `PhotoBracketSorter.exe --i18n-self-test` | exit 0, `i18n self-test ok` |
| `PhotoBracketSorter.exe --portable-smoke <folder>` | exit 0, `portable smoke ok`, exercises the real `analyze_folder` → `CopyEngine` → `write_reports` path |
| `PhotoBracketSorter.exe --move-smoke <folder>` | exit 0, `move smoke ok`, exercises the real `analyze_folder` → `MoveEngine` → `write_reports` path |
| GUI launch, no arguments | Process stays alive, main window title reads `Сортировщик фото 1.2.0` (correct app name, correct version) |
| ExifTool bundled | `_internal/vendor/exiftool/exiftool.exe` present and detected by the app at runtime |
| No leftover source in the bundle | No `.py`, `test_*`, or `__pycache__` anywhere under the extracted `dist/PhotoBracketSorter/` tree |
| Portable zip size | ~30.8 MiB |

### Known environment limitation (not a defect in the app)

Interactive, click-level GUI verification (opening the folder picker,
clicking Analyze, watching the table populate) could not be performed in
this environment: the computer-use tool's `request_access` only resolves
against a static Start-Menu snapshot taken at session start, and a freshly
built, non-installed `.exe` is never in that snapshot — this was also true
for the 1.1.1 build and is an environment constraint, not something the
app itself can control or that indicates a problem with it. Compensating
evidence: the self-test/smoke CLI flags above exercise the exact same
production code paths (`analyze_folder`, `CopyEngine`, `MoveEngine`,
`write_reports`) the GUI's Analyze/Copy/Move buttons call, and the GUI
launch check confirms the window itself constructs, titles, and stays
alive without crashing. The 527-test suite additionally drives every GUI
dialog and worker-thread code path (including feature 10's new Simple/
Advanced mode toggle and settings-dialog tab visibility) directly through
`tkinter`, unit-test style, without a window manager.

## What was not (re-)verified this pass

- RU/EN visual proofreading inside the actual running window (blocked by
  the same environment limitation above) — `validate_catalogs()` proves
  every key exists in both languages, but not that long strings don't
  clip or wrap badly in a real window.
- Light/dark theme visual appearance in the real window — `apply_theme()`
  is exercised by the test suite (it runs without raising), but not
  visually inspected pixel-by-pixel.
- Any of the 10 features against a folder of **real camera RAW files**
  (all automated tests use synthetic fixtures or minimal JPEG bytes — real
  RAW files from an actual camera were never available in this
  environment). This applies to every feature built in 1.2.0, not just the
  newest ones.
