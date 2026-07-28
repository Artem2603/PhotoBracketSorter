# Photo Bracket Sorter 1.1.0

Windows portable app for sorting smartphone photo folders with rendered files, RAW files, and 3/5-frame bracketing series.

## What It Does

- Finds HEIC/HEIF/HIF/JPEG/PNG, DNG and common RAW formats.
- Uses bundled ExifTool 13.59 when available; fallback mode is only for emergencies and is less accurate.
- Matches RAW + HEIC/JPEG companions.
- Detects independent `BRACKETING_3` and `BRACKETING_5` series.
- Handles mixed offset-aware and offset-naive datetimes without crashing.
- Copies files safely with `.partial`, size checks and optional SHA-256.
- Moves files safely by copying, verifying, then deleting the source.
- Writes `summary.json`, `report.csv`, `report.txt` and per-series `series.json`.

## Copy vs Move

Copy never changes originals. Move is deliberately not a simple file rename or `shutil.move`: it creates a temporary copy, verifies size and SHA-256 when enabled, atomically finalizes the destination, and deletes the source only after verification.

If a file is copied but the source cannot be deleted, the operation is marked `DELETE_FAILED` and shown as "Copied, but not deleted". In that case check permissions or file locks, then delete the source manually only after confirming the destination copy.

## GUI

Version 1.1.0 adds a modern Tk/ttkbootstrap interface with Russian and English languages, light/dark/system themes, separate Copy and Move buttons, preview tabs, settings migration, filters, sorting and manual review helpers.

## Limits Of Verification

The automated test suite uses synthetic files and synthetic metadata. It verifies the code paths for HEIC/DNG/RAW handling and mixed timezones, but does not prove visual decoding or camera-specific metadata behavior for every real HEIC/DNG file. Use preview/report review for real camera folders.

MTP phone views are not normal filesystem folders and are not supported directly. Copy photos from the phone to a local disk folder first.
