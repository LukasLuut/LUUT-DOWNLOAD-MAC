# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Luut Video Downloader (one-file, windowed).
# Run `python tools/prepare_build.py` first (build.bat does it) so ffmpeg is available for bundling.
# yt-dlp is NOT bundled: it is the official yt-dlp.exe placed in dist/bin (tools/fetch_ytdlp.py) and updated at
# runtime by YtDlpManager, so a new yt-dlp never requires rebuilding this executable.

from pathlib import Path

ROOT = Path(SPECPATH)
FFMPEG = ROOT / "build" / "bundle" / "ffmpeg" / "ffmpeg.exe"
if not FFMPEG.exists():
    raise SystemExit("ffmpeg.exe não encontrado. Execute: python tools/prepare_build.py")

datas = [
    (str(ROOT / "assets" / "icons"), "assets/icons"),
    (str(ROOT / "assets" / "images"), "assets/images"),
]
hiddenimports: list[str] = []

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG), "ffmpeg")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "imageio_ffmpeg", "yt_dlp", "yt_dlp_ejs", "pytest", "ruff", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Luut Video Downloader",
    icon=str(ROOT / "assets" / "images" / "app.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
    console=False,
    disable_windowed_traceback=True,
    upx=False,
    runtime_tmpdir=None,
)
