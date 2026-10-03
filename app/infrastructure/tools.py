"""Location and version of external tools (ffmpeg, JavaScript runtime)."""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app.utils.paths import resource_path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@functools.lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    """Bundled ffmpeg first, then PATH, then the imageio-ffmpeg wheel (development)."""
    bundled_dir = resource_path("ffmpeg")
    if bundled_dir.is_dir():
        pattern = "ffmpeg*.exe" if sys.platform == "win32" else "ffmpeg*"
        for candidate in sorted(bundled_dir.glob(pattern)):
            if candidate.is_file():
                return str(candidate)
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        path = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError:
        return None
    return path if Path(path).is_file() else None


@functools.lru_cache(maxsize=1)
def ffmpeg_version() -> str | None:
    exe = find_ffmpeg()
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10,
                                creationflags=_NO_WINDOW, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"ffmpeg version (\S+)", result.stdout)
    return match.group(1) if match else None


def find_js_runtime() -> str | None:
    """yt-dlp uses Deno (if installed) to unlock every YouTube format."""
    return shutil.which("deno")
