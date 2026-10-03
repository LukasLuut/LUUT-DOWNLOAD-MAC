"""Filesystem operations: space checks, destination handling, opening files safely."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.errors import AppError, ErrorKind, classify_os_error
from app.utils.filenames import safe_join, sanitize_filename, unique_path
from app.utils.formatters import MONTHS_PT

MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".3gp", ".ts",
    ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac",
})
PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def nearest_existing(path: Path) -> Path | None:
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current


def free_space(path: Path) -> int | None:
    existing = nearest_existing(path)
    if existing is None:
        return None
    try:
        return shutil.disk_usage(existing).free
    except OSError:
        return None


def ensure_writable_dir(path: Path) -> None:
    """Create the directory if needed and prove we can write to it. Raises AppError."""
    if nearest_existing(path) is None:
        raise AppError(ErrorKind.DESTINATION, f"No existing ancestor for {path}")
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".luut-write-test-", delete=True):
            pass
    except OSError as exc:
        raise classify_os_error(exc) from exc


def ensure_free_space(path: Path, required_bytes: int | None) -> None:
    if not required_bytes:
        return
    available = free_space(path)
    if available is not None and available < int(required_bytes * 1.05):
        raise AppError(ErrorKind.DISK_FULL, f"{available} bytes free, {required_bytes} required at {path}")


def organized_subdir(base: Path, pattern: str, when: datetime, uploader: str | None,
                     extractor: str | None) -> Path:
    if pattern == "year":
        return safe_join(base, str(when.year))
    if pattern == "uploader":
        return safe_join(base, uploader or "Outros")
    if pattern == "site":
        return safe_join(base, (extractor or "Outros").split(":")[0].capitalize())
    return safe_join(base, str(when.year), MONTHS_PT[when.month - 1])


def move_to_destination(source: Path, directory: Path, title: str | None, overwrite: bool) -> Path:
    """Move a finished temp file to `directory` using the sanitized title. Never overwrites silently.

    The data is first copied/moved to `<final name>.part` next to the target and then renamed atomically, so a
    crash in the middle never leaves a truncated file under the final name."""
    stem = sanitize_filename(title)
    extension = source.suffix
    staging: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{stem}{extension}" if overwrite else unique_path(directory, stem, extension)
        staging = target.with_name(target.name + ".part")
        shutil.move(str(source), str(staging))
        os.replace(staging, target)
    except OSError as exc:
        _rollback(staging, source)
        raise classify_os_error(exc) from exc
    return target


def _rollback(staging: Path | None, source: Path) -> None:
    """Undo a half-finished move so the finished temp file is kept for a retry and no partial file is left."""
    if staging is None or not staging.exists():
        return
    try:
        if source.exists():
            staging.unlink()
        else:
            shutil.move(str(staging), str(source))
    except OSError:
        pass


def has_partial_files(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    return any(p.is_file() and p.stat().st_size > 0 for p in directory.iterdir())


def remove_tree(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def open_file(path: Path) -> bool:
    """Open a media file with its default app. Only known media types are opened."""
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
        return False
    _open_with_default_app(path)  # explicit user action
    return True


def _open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607 - macOS `open`, arguments as a list


def explorer_select_command(path: Path) -> str:
    """Explorer needs the argument verbatim as /select,"<path>". Passing it as a list makes Python quote the whole
    argument, which Explorer ignores (it then opens Documents). Windows paths cannot contain double quotes."""
    return f'explorer /select,"{path.resolve()}"'


def reveal_in_folder(path: Path) -> bool:
    """Open the folder that really contains the file (organized subfolders included) and select it."""
    if path.is_file():
        if sys.platform == "win32":
            subprocess.Popen(explorer_select_command(path), creationflags=_NO_WINDOW)  # noqa: S603 - no shell
        else:
            subprocess.Popen(["open", "-R", str(path)])  # noqa: S603, S607 - Finder, file selected
        return True
    folder = path if path.is_dir() else nearest_existing(path.parent)
    if folder is None:
        return False
    _open_with_default_app(folder)
    return True
