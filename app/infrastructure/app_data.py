"""Reset of the application's internal data and the isolated "fresh start" profile.

Only entries created by the app are removed (explicit whitelist). Downloaded videos live in the user's folders
and are never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.utils.paths import APP_DATA_ENV, FRESH_SUFFIX, base_data_dir

APP_ENTRIES = ("luut.db", "luut.db-wal", "luut.db-shm", "logs", "thumbnails", "temp", "cache")
_TASK_DIR = re.compile(r"^[0-9a-f]{32}$")


def fresh_profile_dir() -> Path:
    """Separate data folder used by --fresh-start; the real profile is never read or modified."""
    base = base_data_dir()
    return base.with_name(base.name + FRESH_SUFFIX)


def activate_fresh_profile() -> Path:
    """Point the app to a brand-new empty profile for this run."""
    folder = fresh_profile_dir()
    wipe_app_data(folder, attempts=1)  # locked = a fresh instance is already open (it will be focused)
    folder.mkdir(parents=True, exist_ok=True)
    os.environ[APP_DATA_ENV] = str(folder)
    return folder


def _custom_temp_dir(data_dir: Path) -> Path | None:
    database = data_dir / "luut.db"
    if not database.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'temp_dir'").fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        return Path(json.loads(row[0]))
    except (ValueError, TypeError):
        return None


def _remove(path: Path, attempts: int, delay: float) -> bool:
    for _ in range(attempts):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return True
        except OSError:
            time.sleep(delay)  # a previous instance may still be releasing its files
    return not path.exists()


def wipe_app_data(data_dir: Path, attempts: int = 40, delay: float = 0.25) -> list[Path]:
    """Delete the app's internal data in `data_dir`. Returns entries that could not be removed."""
    failed: list[Path] = []
    if not data_dir.is_dir():
        return failed
    custom_temp = _custom_temp_dir(data_dir)
    if custom_temp and custom_temp.is_dir():
        for child in custom_temp.iterdir():
            if child.is_dir() and _TASK_DIR.match(child.name) and not _remove(child, attempts, delay):
                failed.append(child)
    for name in APP_ENTRIES:
        entry = data_dir / name
        if entry.exists() and not _remove(entry, attempts, delay):
            failed.append(entry)
    return failed
