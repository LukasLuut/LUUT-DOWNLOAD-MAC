"""Persistence of download tasks (queue state + history) and history export."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.core.models.download import DownloadProgress, DownloadStatus, DownloadTask
from app.infrastructure.database import Database
from app.utils.formatters import now_iso

_COLUMNS = (
    "id", "url", "title", "quality_height", "container", "output_dir", "status", "position",
    "created_at", "started_at", "finished_at", "thumbnail_url", "thumbnail_path", "uploader",
    "duration", "extractor", "estimated_size", "file_path", "file_size", "error_kind",
    "error_message", "attempts", "updated_at",
)
_PROGRESS_COLUMNS = ("downloaded_bytes", "total_bytes")
_TERMINAL = tuple(s.value for s in DownloadStatus if s.is_terminal)

HISTORY_FILTERS = {
    "all": ("Todos", _TERMINAL),
    "completed": ("Concluídos", (DownloadStatus.COMPLETED.value,)),
    "cancelled": ("Cancelados", (DownloadStatus.CANCELLED.value,)),
    "failed": ("Falhos", (DownloadStatus.FAILED.value,)),
}

EXPORT_FIELDS = ("title", "url", "status", "quality", "format", "file_path", "file_size",
                 "uploader", "duration", "created_at", "finished_at")


@dataclass(frozen=True)
class DailyStats:
    total: int = 0
    completed: int = 0
    active: int = 0
    failed: int = 0


def _to_row(task: DownloadTask) -> tuple[Any, ...]:
    values = {name: getattr(task, name) for name in _COLUMNS}
    values["status"] = task.status.value
    values["updated_at"] = now_iso()
    progress = (task.progress.downloaded_bytes, task.progress.total_bytes)
    return tuple(values[name] for name in _COLUMNS) + progress


def _from_row(row: sqlite3.Row) -> DownloadTask:
    data = {name: row[name] for name in _COLUMNS}
    try:
        data["status"] = DownloadStatus(data["status"])
    except ValueError:
        data["status"] = DownloadStatus.FAILED
    task = DownloadTask(**data)
    task.progress = DownloadProgress(int(row["downloaded_bytes"] or 0), row["total_bytes"])
    return task


class HistoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, task: DownloadTask) -> None:
        columns = _COLUMNS + _PROGRESS_COLUMNS
        placeholders = ", ".join("?" for _ in columns)
        self._db.execute(f"INSERT OR REPLACE INTO downloads ({', '.join(columns)}) VALUES ({placeholders})",
                         _to_row(task))

    def save_progress(self, task: DownloadTask) -> None:
        """Lightweight periodic checkpoint (no full row rewrite)."""
        self._db.execute("UPDATE downloads SET downloaded_bytes = ?, total_bytes = ?, updated_at = ? WHERE id = ?",
                         (task.progress.downloaded_bytes, task.progress.total_bytes, now_iso(), task.id))

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM downloads")
        return int(row["n"]) if row else 0

    def get(self, task_id: str) -> DownloadTask | None:
        row = self._db.query_one("SELECT * FROM downloads WHERE id = ?", (task_id,))
        return _from_row(row) if row else None

    def delete(self, task_id: str) -> None:
        self._db.execute("DELETE FROM downloads WHERE id = ?", (task_id,))

    def unfinished(self) -> list[DownloadTask]:
        marks = ", ".join("?" for _ in _TERMINAL)
        rows = self._db.query(f"SELECT * FROM downloads WHERE status NOT IN ({marks}) ORDER BY position",
                              _TERMINAL)
        return [_from_row(r) for r in rows]

    def search(self, text: str = "", filter_key: str = "all", limit: int = 100, offset: int = 0) -> list[DownloadTask]:
        statuses = HISTORY_FILTERS.get(filter_key, HISTORY_FILTERS["all"])[1]
        marks = ", ".join("?" for _ in statuses)
        sql = f"SELECT * FROM downloads WHERE status IN ({marks})"
        params: list[Any] = list(statuses)
        if text.strip():
            like = f"%{text.strip().replace('%', '').replace('_', '')}%"
            sql += " AND (title LIKE ? OR url LIKE ? OR file_path LIKE ? OR uploader LIKE ?)"
            params += [like] * 4
        sql += " ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [_from_row(r) for r in self._db.query(sql, params)]

    def recent(self, limit: int = 5) -> list[DownloadTask]:
        return self.search(limit=limit)

    def clear(self) -> list[DownloadTask]:
        """Delete every finished record and return them (so caches can be cleaned)."""
        removed = self.search(limit=1_000_000)
        marks = ", ".join("?" for _ in _TERMINAL)
        self._db.execute(f"DELETE FROM downloads WHERE status IN ({marks})", _TERMINAL)
        return removed

    def prune(self, keep: int) -> list[DownloadTask]:
        """Keep only the `keep` most recent finished records (0 = unlimited)."""
        if keep <= 0:
            return []
        removed = self.search(limit=1_000_000, offset=keep)
        for task in removed:
            self.delete(task.id)
        return removed

    def daily_stats(self, day: date | None = None) -> DailyStats:
        prefix = (day or date.today()).isoformat()
        rows = self._db.query("SELECT status, COUNT(*) AS n FROM downloads WHERE created_at LIKE ? GROUP BY status",
                              (f"{prefix}%",))
        counts = {r["status"]: r["n"] for r in rows}
        active = counts.get(DownloadStatus.DOWNLOADING.value, 0) + counts.get(DownloadStatus.PROCESSING.value, 0)
        return DailyStats(total=sum(counts.values()), completed=counts.get(DownloadStatus.COMPLETED.value, 0),
                          active=active, failed=counts.get(DownloadStatus.FAILED.value, 0))


def _export_record(task: DownloadTask) -> dict[str, Any]:
    return {
        "title": task.title or "",
        "url": task.url,
        "status": task.status.label,
        "quality": task.quality_label,
        "format": task.container_label,
        "file_path": task.file_path or "",
        "file_size": task.file_size or "",
        "uploader": task.uploader or "",
        "duration": task.duration or "",
        "created_at": task.created_at,
        "finished_at": task.finished_at or "",
    }


def export_csv(tasks: list[DownloadTask], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS, delimiter=";")
        writer.writeheader()
        for task in tasks:
            writer.writerow(_export_record(task))


def export_json(tasks: list[DownloadTask], path: Path) -> None:
    path.write_text(json.dumps([_export_record(t) for t in tasks], ensure_ascii=False, indent=2), encoding="utf-8")
