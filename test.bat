@echo off
REM Runs the suite with unittest, which needs no extra dependency.
REM -t . keeps the project root as the top-level import dir so "from app..." resolves.
setlocal
cd /d "%~dp0"
set PYTHONPATH=%CD%

python -m unittest discover -s tests -t . %*
exit /b %ERRORLEVEL%
