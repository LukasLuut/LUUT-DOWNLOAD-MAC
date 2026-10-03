# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Luut Video Downloader on macOS (one-folder .app bundle, windowed).
# Run build_mac.sh (or the GitHub Actions workflow); it prepares ffmpeg, the .icns icon and the yt-dlp seed first:
#   python tools/prepare_build.py
#   python tools/fetch_ytdlp.py build/bundle/bin
# yt-dlp is NOT a Python dependency: the official yt-dlp_macos travels inside the bundle (Contents/Resources/bin) only as
# a seed. On first run the app copies it to ~/Library/Application Support/LuutVideoDownloader/bin, where it is updated
# without rebuilding this app.

import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)
from app import APP_NAME, APP_VERSION  # noqa: E402

ROOT = Path(SPECPATH)
BUNDLE_DIR = ROOT / "build" / "bundle"
FFMPEG = BUNDLE_DIR / "ffmpeg" / "ffmpeg"
YTDLP = BUNDLE_DIR / "bin" / "yt-dlp_macos"
ICNS = BUNDLE_DIR / "app.icns"
for required, step in ((FFMPEG, "python tools/prepare_build.py"), (ICNS, "python tools/prepare_build.py"),
                       (YTDLP, "python tools/fetch_ytdlp.py build/bundle/bin")):
    if not required.exists():
        raise SystemExit(f"{required.name} não encontrado. Execute: {step}")

datas = [
    (str(ROOT / "assets" / "icons"), "assets/icons"),
    (str(ROOT / "assets" / "images"), "assets/images"),
    (str(YTDLP), "bin"),
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG), "ffmpeg")],
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "imageio_ffmpeg", "yt_dlp", "yt_dlp_ejs", "pytest", "ruff", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    upx=False,
    codesign_identity=None,  # ad-hoc signature (no Apple Developer account)
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)

mac_app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ICNS),
    bundle_identifier="com.luut.LuutVideoDownloader",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,  # follow the system's dark mode
        "NSHumanReadableCopyright": "(c) 2026 Luut",
    },
)
