# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

# SPECPATH, not cwd: the build must resolve the same data folders no matter which
# directory PyInstaller is invoked from. With cwd, building from anywhere other than
# the project root silently produced an executable missing profiles, assets, and the
# bundled ExifTool.
project = Path(SPECPATH).resolve()

datas = []
for folder in ("profiles", "assets", "vendor"):
    source = project / folder
    if source.is_dir():
        datas.append((str(source), folder))
    else:
        raise SystemExit(f"Build aborted: required data folder is missing: {source}")

# Optional dependency; skip cleanly rather than failing the build when absent.
try:
    datas += collect_data_files("ttkbootstrap")
except Exception:
    pass

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL", "PIL.Image", "PIL.ImageTk", "ttkbootstrap", "ttkbootstrap.dialogs", "ttkbootstrap.toast"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoBracketSorter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PhotoBracketSorter",
)
