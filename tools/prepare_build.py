"""Prepare resources for PyInstaller: app icon (.ico, plus .icns on macOS) and the ffmpeg binary to bundle."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_MAC = sys.platform == "darwin"
BUNDLE_FFMPEG = ROOT / "build" / "bundle" / "ffmpeg" / ("ffmpeg" if IS_MAC else "ffmpeg.exe")
ICON = ROOT / "assets" / "images" / "app.ico"
ICNS = ROOT / "build" / "bundle" / "app.icns"
LOGO = ROOT / "assets" / "images" / "logo.svg"
# iconutil expects exactly these names: icon_<size>x<size>[@2x].png
ICONSET_SIZES = ((16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2))


def prepare_icon() -> None:
    if ICON.exists():
        print(f"Ícone: {ICON}")
    else:
        subprocess.run([sys.executable, str(ROOT / "tools" / "generate_icons.py")], check=True)
    if IS_MAC:
        prepare_icns()


def prepare_icns() -> None:
    """Render logo.svg at every Retina size and pack it with Apple's iconutil."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtSvg import QSvgRenderer

    sys.path.insert(0, str(ROOT / "tools"))
    from generate_icons import render_png

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    renderer = QSvgRenderer(str(LOGO))
    if not renderer.isValid():
        raise SystemExit("logo.svg inválido")
    ICNS.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "app.iconset"
        iconset.mkdir()
        for size, scale in ICONSET_SIZES:
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            (iconset / name).write_bytes(render_png(renderer, size * scale))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True)
    print(f"Ícone macOS: {ICNS}")


def prepare_ffmpeg() -> None:
    import imageio_ffmpeg

    source = Path(imageio_ffmpeg.get_ffmpeg_exe())
    BUNDLE_FFMPEG.parent.mkdir(parents=True, exist_ok=True)
    if not BUNDLE_FFMPEG.exists() or BUNDLE_FFMPEG.stat().st_size != source.stat().st_size:
        shutil.copy2(source, BUNDLE_FFMPEG)
    if IS_MAC:
        BUNDLE_FFMPEG.chmod(0o755)
    print(f"FFmpeg: {BUNDLE_FFMPEG} ({BUNDLE_FFMPEG.stat().st_size // (1024 * 1024)} MB)")


if __name__ == "__main__":
    prepare_icon()
    prepare_ffmpeg()
