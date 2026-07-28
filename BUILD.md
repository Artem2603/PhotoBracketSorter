# Build

Recommended environment: Python 3.12 or 3.13 on Windows 10/11 x64.
Verified on Python 3.13.5.

All dependency versions are pinned in `requirements.txt` and
`requirements-dev.txt`, and `requirements-dev.txt` includes the runtime file, so
one install covers both.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m unittest discover -s tests -t .
.\.venv\Scripts\pyinstaller --clean --noconfirm PhotoBracketSorter.spec
```

Or simply run `build.bat`, which pins the working directory, requires a virtual
environment, and sets `PYTHONHASHSEED` / `SOURCE_DATE_EPOCH` for reproducibility.

`test.bat` runs the suite (207 tests). `pytest` is optional; the suite runs under
plain `unittest`, but the top-level directory must be the project root
(`-t .`) so `from app...` imports resolve.

The spec resolves data folders from `SPECPATH`, so it can be invoked from any
directory. It aborts the build if `profiles`, `assets`, or `vendor` is missing,
rather than producing an executable without the bundled ExifTool.

The portable folder is `dist\PhotoBracketSorter`. It must contain:

- `PhotoBracketSorter.exe`
- `_internal\`
- `README_FIRST_RU.txt`
- `README_FIRST_EN.txt`
- `LICENSE`
- `THIRD_PARTY_NOTICES.md`
- `CHANGELOG.md`

Post-build smoke commands:

```powershell
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --version
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --exiftool-version
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --self-test
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --mixed-timezone-self-test
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --portable-smoke C:\Temp\pbs-portable-smoke
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --i18n-self-test
.\dist\PhotoBracketSorter\PhotoBracketSorter.exe --move-smoke C:\Temp\pbs-move-smoke
```
