"""Run blocking work on the thread pool and deliver results on the UI thread."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.infrastructure.logger import get_logger

log = get_logger("ui.workers")
_alive: set[_Signals] = set()


class _Signals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - delivered to the UI as a friendly error
            log.debug("Background job failed: %r", exc)
            self._signals.failed.emit(exc)
        else:
            self._signals.succeeded.emit(result)


def run_in_background(fn: Callable[[], Any], on_success: Callable[[Any], None] | None = None,
                      on_error: Callable[[BaseException], None] | None = None) -> None:
    signals = _Signals()
    _alive.add(signals)

    def finish(callback: Callable[[Any], None] | None, value: Any) -> None:
        _alive.discard(signals)
        if callback:
            callback(value)

    signals.succeeded.connect(lambda value: finish(on_success, value))
    signals.failed.connect(lambda exc: finish(on_error, exc))
    QThreadPool.globalInstance().start(_Job(fn, signals))
