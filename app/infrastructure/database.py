"""Local SQLite storage (thread-safe, single connection)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    title           TEXT,
    quality_height  INTEGER NOT NULL DEFAULT 0,
    container       TEXT NOT NULL,
    output_dir      TEXT NOT NULL,
    status          TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    thumbnail_url   TEXT,
    thumbnail_path  TEXT,
    uploader        TEXT,
    duration        REAL,
    extractor       TEXT,
    estimated_size  INTEGER,
    file_path       TEXT,
    file_size       INTEGER,
    error_kind      TEXT,
    error_message   TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at);
"""

# v2: progress checkpoints and tutorial state. Existing users (first run already done) keep the tutorial off.
_SCHEMA_V2 = """
ALTER TABLE downloads ADD COLUMN downloaded_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE downloads ADD COLUMN total_bytes INTEGER;
ALTER TABLE downloads ADD COLUMN updated_at TEXT;
CREATE TABLE IF NOT EXISTS tutorial_state (
    id            TEXT PRIMARY KEY,
    completed     INTEGER NOT NULL DEFAULT 0,
    dont_show     INTEGER NOT NULL DEFAULT 0,
    last_step     TEXT,
    completed_at  TEXT
);
INSERT OR IGNORE INTO tutorial_state (id, completed)
    SELECT 'main', 1 FROM settings WHERE key = 'first_run_done' AND value = 'true';
DELETE FROM settings WHERE key = 'first_run_done';
"""

# v3: keyboard shortcuts. Stable action ids are stored, never the text shown in the interface.
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS shortcuts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id        TEXT NOT NULL UNIQUE,
    scope            TEXT NOT NULL,
    key_combination  TEXT NOT NULL DEFAULT '',
    enabled          INTEGER NOT NULL DEFAULT 1,
    is_global        INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL
);
"""

_MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            # WAL + NORMAL never corrupts the database on a crash or power loss.
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for target in range(version + 1, SCHEMA_VERSION + 1):
            script = f"BEGIN;{_MIGRATIONS[target]}PRAGMA user_version={target};COMMIT;"
            self._conn.executescript(script)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).rowcount

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def integrity_ok(self) -> bool:
        row = self.query_one("PRAGMA quick_check")
        return bool(row) and row[0] == "ok"

    def close(self) -> None:
        with self._lock:
            self._conn.close()
