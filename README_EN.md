# Photo Bracket Sorter 1.1.0

Portable Windows app for sorting HEIC/JPEG, RAW/DNG, and 3/5-frame bracketed photo folders.

## Highlights

- Bundled ExifTool is found automatically after ZIP extraction.
- RAW + HEIC/JPEG matching uses time, name and metadata signals.
- `BRACKETING_3` and `BRACKETING_5` are independent destinations.
- Mixed timezone-aware/timezone-naive datetimes do not crash processing.
- Copy keeps originals unchanged.
- Move copies, verifies size and SHA-256, then deletes the source.
- Delete failures are reported as `Copied, but not deleted`.
- GUI supports Russian and English, themes, settings, preview and manual review.

## Logs

Logs are written to `%LOCALAPPDATA%\PhotoBracketSorter\logs`.

## Verification Boundary

Automated tests use synthetic files and metadata. HEIC/DNG/RAW code paths are tested, but this run did not use the user's real photos.
