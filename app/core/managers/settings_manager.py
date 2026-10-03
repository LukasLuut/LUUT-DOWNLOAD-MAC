"""Loads/saves AppSettings in the local database and notifies listeners of changes."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.core.models.settings import AppSettings
from app.infrastructure.database import Database

SettingsListener = Callable[[AppSettings, set[str]], None]


class SettingsManager:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._listeners: list[SettingsListener] = []
        self._settings = self._load()

    def _load(self) -> AppSettings:
        data: dict[str, Any] = {}
        for row in self._db.query("SELECT key, value FROM settings"):
            try:
                data[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
        return AppSettings.from_dict(data)

    @property
    def settings(self) -> AppSettings:
        with self._lock:
            return replace(self._settings)

    def update(self, **changes: Any) -> AppSettings:
        with self._lock:
            merged = self._settings.to_dict() | changes
            new = AppSettings.from_dict(merged)
            changed = {k for k, v in new.to_dict().items() if getattr(self._settings, k) != v}
            if changed:
                with self._db.transaction() as conn:
                    for key in changed:
                        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                                     (key, json.dumps(getattr(new, key))))
            self._settings = new
            snapshot = replace(new)
        if changed:
            for listener in list(self._listeners):
                listener(snapshot, changed)
        return snapshot

    def add_listener(self, listener: SettingsListener) -> None:
        self._listeners.append(listener)
