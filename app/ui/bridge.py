"""Thread-safe bridge from the Qt-free core (DownloadManager, EventBus) to Qt signals (queued to the UI thread)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from app.core.events import WILDCARD, EventBus
from app.core.managers.download_manager import DownloadEvent, DownloadManager
from app.core.models.download import DownloadTask


class DownloadBridge(QObject):
    task_added = Signal(object)
    task_updated = Signal(object)
    task_progress = Signal(object)
    task_removed = Signal(object)
    task_finished = Signal(object)
    # Named application events (download_started, download_paused, queue_changed, analysis_completed...).
    event = Signal(str, object)

    def __init__(self, manager: DownloadManager, events: EventBus | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._signals = {
            DownloadEvent.ADDED: self.task_added,
            DownloadEvent.UPDATED: self.task_updated,
            DownloadEvent.PROGRESS: self.task_progress,
            DownloadEvent.REMOVED: self.task_removed,
            DownloadEvent.FINISHED: self.task_finished,
        }
        manager.add_listener(self._on_event)
        (events or manager.events).subscribe(WILDCARD, self._on_bus)

    def _on_event(self, event: DownloadEvent, task: DownloadTask) -> None:
        # Emitting from a worker thread is safe: receivers live in the UI thread (queued connection).
        self._signals[event].emit(task)

    def _on_bus(self, name: str, payload: Any) -> None:
        self.event.emit(name, payload)
