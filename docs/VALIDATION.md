# Validation Notes

## What Was Tested

The automated test suite uses synthetic metadata and synthetic files to validate the decision logic without touching real photo collections.

Synthetic metadata covers:

- RAW files with capture timestamps;
- exposure compensation sequences for 3-frame brackets;
- exposure compensation sequences for 5-frame brackets;
- order `0, -1, +1`;
- same-exposure bursts that must not become brackets;
- two nearby independent series;
- one 3-frame series, one single RAW, and one 5-frame series;
- RAW + HEIC companion matching;
- Russian file and directory names;
- files without EXIF capture time;
- zero-byte corrupted files;
- destination name collisions;
- SHA-256 verification and cancellation.

Portable smoke validation creates a synthetic JPEG named with Russian characters, analyzes it through the packaged executable, copies it into a fresh `Sorted_Photos_*` result, and verifies that reports are written.

## ExifTool Validation

The bundled Windows ExifTool is version 13.59. The downloaded archive SHA-256 was checked against the official `checksums-13.59.txt` file:

```text
SHA2-256(exiftool-13.59_64.zip)= 44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec
```

The application self-test checks that the packaged executable can find ExifTool after extraction.

## Boundaries

No real Xiaomi 14, ProShot, HEIC, HIF, DNG, UltraRAW, Linear DNG, or RAW camera files were used in this validation pass.

Therefore the project verifies:

- packaging and discovery of ExifTool;
- synthetic metadata decision logic;
- fallback behavior;
- copy safety;
- report creation;
- portable execution after unpacking.

It does not prove full real-world recognition quality for all HEIC/DNG/RAW variants. That needs a separate acceptance pass with real sample photos from the target cameras.
