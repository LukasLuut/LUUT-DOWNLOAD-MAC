"""Filesystem locations used by the application."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import APP_ID

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> Path:
    """Path of a bundled read-only resource (works in dev and inside the PyInstaller bundle)."""
    base = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
    return base.joinpath(*parts)


def app_install_dir() -> Path:
    """Folder of the program itself: next to "Luut Video Downloader.exe" / inside the .app's Contents/MacOS (frozen)
    or the project root (dev)."""
    return Path(sys.executable).resolve().parent if is_frozen() else _PROJECT_ROOT


APP_DATA_ENV = "LUUT_DATA_DIR"
FRESH_SUFFIX = "-FreshStart"


def base_data_dir() -> Path:
    """%LOCALAPPDATA%/LuutVideoDownloader on Windows, ~/Library/Application Support/LuutVideoDownloader on macOS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_ID


def app_data_dir() -> Path:
    """Per-user writable directory. `LUUT_DATA_DIR` overrides it (tests and --fresh-start)."""
    override = os.environ.get(APP_DATA_ENV)
    base = Path(override) if override else base_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_fresh_profile() -> bool:
    return app_data_dir().name.endswith(FRESH_SUFFIX)


def _subdir(name: str) -> Path:
    path = app_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    return _subdir("logs")


def thumbnails_dir() -> Path:
    return _subdir("thumbnails")


def default_temp_dir() -> Path:
    return _subdir("temp")


def tools_dir() -> Path:
    """Writable home of external tools that update independently of the .exe (yt-dlp)."""
    return _subdir("bin")


def database_path() -> Path:
    return app_data_dir() / "luut.db"


def _known_folder(folder_guid: str) -> Path | None:
    """Resolve a Windows Known Folder (handles OneDrive/relocated folders)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        raw = uuid.UUID(folder_guid).bytes_le
        guid = GUID.from_buffer_copy(raw)
        out = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        if result != 0:
            return None
        path = Path(out.value)
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    except (OSError, AttributeError, ValueError):
        return None


_FOLDERID_VIDEOS = "18989B1D-99B5-455B-841C-AB7C74E4DDFC"
_FOLDERID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"


def default_download_dir() -> Path:
    for candidate in (_known_folder(_FOLDERID_VIDEOS), Path.home() / "Videos", Path.home() / "Movies",
                      _known_folder(_FOLDERID_DOWNLOADS), Path.home() / "Downloads"):
        if candidate and candidate.is_dir():
            return candidate
    return Path.home()
