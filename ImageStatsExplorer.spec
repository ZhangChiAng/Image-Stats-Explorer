"""PyInstaller recipe for the Windows x64 single-file application."""

# ruff: noqa: F821

from pathlib import Path

project_dir = Path(SPECPATH)

analysis = Analysis(
    [str(project_dir / "src" / "image_stats_explorer" / "app.py")],
    pathex=[str(project_dir / "src")],
    binaries=[],
    datas=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ImageStatsExplorer",
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
