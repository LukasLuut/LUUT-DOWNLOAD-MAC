"""Application-wide event bus (Qt-free, thread-safe).

Core services publish named events; every interface (main window, floating widget, tray, notifications) subscribes to
the same bus instead of polling. Handlers run on the publishing thread — Qt code re-emits them as queued signals (see
`app.ui.bridge`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("luut.events")

DOWNLOAD_ADDED = "download_added"
DOWNLOAD_STARTED = "download_started"
DOWNLOAD_PROGRESS = "download_progress"
DOWNLOAD_PAUSED = "download_paused"
DOWNLOAD_RESUMED = "download_resumed"
DOWNLOAD_COMPLETED = "download_completed"
DOWNLOAD_FAILED = "download_failed"
DOWNLOAD_CANCELLED = "download_cancelled"
DOWNLOAD_REMOVED = "download_removed"
QUEUE_CHANGED = "queue_changed"
ANALYSIS_STARTED = "analysis_started"
ANALYSIS_COMPLETED = "analysis_completed"
ANALYSIS_FAILED = "analysis_failed"

ALL_EVENTS = frozenset({
    DOWNLOAD_ADDED, DOWNLOAD_STARTED, DOWNLOAD_PROGRESS, DOWNLOAD_PAUSED, DOWNLOAD_RESUMED, DOWNLOAD_COMPLETED,
    DOWNLOAD_FAILED, DOWNLOAD_CANCELLED, DOWNLOAD_REMOVED, QUEUE_CHANGED, ANALYSIS_STARTED, ANALYSIS_COMPLETED,
    ANALYSIS_FAILED,
})

Handler = Callable[[str, Any], None]
WILDCARD = "*"


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to one event name (or `*` for all). Returns a function that unsubscribes."""
        if event != WILDCARD and event not in ALL_EVENTS:
            raise ValueError(f"Unknown event: {event}")
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event: str, payload: Any = None) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event, ())) + list(self._handlers.get(WILDCARD, ()))
        for handler in handlers:
            try:
                handler(event, payload)
            except Exception:  # a broken subscriber must never break the publisher (e.g. a download)
                log.exception("Event handler failed for %s", event)
