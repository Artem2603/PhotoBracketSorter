@echo off
REM Reproducible build. Run from anywhere: the spec resolves paths from its own
REM location, and this script pins the interpreter's working directory to the project.
setlocal
cd /d "%~dp0"

REM Fixed hash seed so the bundled bytecode is byte-identical between builds.
set PYTHONHASHSEED=0
set SOURCE_DATE_EPOCH=1751328000

REM Pinned versions only. requirements-dev.txt includes requirements.txt.
python -m pip install --require-virtualenv --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 (
  echo.
  echo Not running inside a virtual environment. Create one first:
  echo     python -m venv .venv
  echo     .venv\Scripts\activate
  echo.
  exit /b 1
)

python -m PyInstaller PhotoBracketSorter.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo.
echo Build finished: dist\PhotoBracketSorter\
