"""Queue, concurrency, retries, pause/resume/cancel and persistence of download tasks.

Qt-free: listeners are plain callbacks invoked from worker threads with task snapshots. Every change is also
published on the shared EventBus (download_added, download_started, ..., queue_changed) so any interface - main
window, floating widget, tray - reacts to the same single queue.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from app.core import events as ev
from app.core.errors import AppError, ErrorKind, to_app_error
from app.core.events import EventBus
from app.core.models.download import DownloadProgress, DownloadRequest, DownloadStatus, DownloadTask
from app.core.models.settings import EXISTING_OVERWRITE, AppSettings
from app.core.providers.base import DownloadJob, DownloadProvider, DownloadStopped, Stage, StopReason, StopToken
from app.core.services.history import HistoryRepository
from app.core.services.thumbnails import ThumbnailStore
from app.infrastructure import filesystem as fs
from app.infrastructure.logger import DOWNLOADS_LOGGER, mask_urls
from app.utils.formatters import now_iso

log = logging.getLogger(DOWNLOADS_LOGGER)

MAX_ATTEMPTS = 3
RETRY_DELAYS: tuple[float, ...] = (2, 5)
CHECKPOINT_INTERVAL = 3.0  # seconds between progress checkpoints written to the database
RESTART_NOTICE = "A fonte não permitiu retomar o arquivo parcial; o download recomeçou do início."


class DownloadEvent(str, Enum):
    ADDED = "added"
    UPDATED = "updated"
    PROGRESS = "progress"
    REMOVED = "removed"
    FINISHED = "finished"


Listener = Callable[[DownloadEvent, DownloadTask], None]
_RECOVERABLE = (DownloadStatus.INTERRUPTED, DownloadStatus.QUEUED, DownloadStatus.PAUSED)


def _snapshot(task: DownloadTask) -> DownloadTask:
    return replace(task, progress=replace(task.progress))


class DownloadManager:
    def __init__(self, provider: DownloadProvider, repository: HistoryRepository,
                 settings: Callable[[], AppSettings], thumbnails: ThumbnailStore | None = None,
                 retry_delays: Sequence[float] = RETRY_DELAYS,
                 checkpoint_interval: float = CHECKPOINT_INTERVAL, events: EventBus | None = None) -> None:
        self.events = events or EventBus()
        self._provider = provider
        self._repo = repository
        self._settings = settings
        self._thumbnails = thumbnails
        self._retry_delays = tuple(retry_delays)
        self._checkpoint_interval = checkpoint_interval
        self._last_checkpoint: dict[str, float] = {}
        self._resume_from: dict[str, int] = {}
        self._held = False  # after a restart the queue waits for the user's decision
        self._tasks: dict[str, DownloadTask] = {}
        self._tokens: dict[str, StopToken] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._finalize_lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._accepting = True

    # ----------------------------------------------------------------- listeners
    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, event: DownloadEvent, task: DownloadTask, bus_event: str | None = None) -> None:
        snapshot = _snapshot(task)
        for listener in list(self._listeners):
            try:
                listener(event, snapshot)
            except Exception:  # a broken listener must never kill a download
                log.exception("Download listener failed")
        if event == DownloadEvent.PROGRESS:
            self.events.publish(ev.DOWNLOAD_PROGRESS, snapshot)
            return
        if bus_event:
            self.events.publish(bus_event, snapshot)
        self.events.publish(ev.QUEUE_CHANGED, snapshot)

    # ------------------------------------------------------------------- queries
    def tasks(self) -> list[DownloadTask]:
        with self._lock:
            return [_snapshot(t) for t in sorted(self._tasks.values(), key=lambda t: t.position)]

    def get(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return _snapshot(task) if task else None

    def active_count(self) -> int:
        with self._lock:
            return len(self._threads)

    def pending_count(self) -> int:
        """Running + waiting in queue."""
        with self._lock:
            return len(self._threads) + sum(1 for t in self._tasks.values()
                                            if t.status == DownloadStatus.QUEUED)

    def has_running_work(self) -> bool:
        """Something is downloading, or waiting tasks would start now (a held queue does not count)."""
        with self._lock:
            return bool(self._threads) or (not self._held and any(
                t.status == DownloadStatus.QUEUED for t in self._tasks.values()))

    def queue_positions(self) -> dict[str, int]:
        """Position (1-based) of every waiting task, computed once (cheap for very long queues)."""
        with self._lock:
            return {t.id: index for index, t in enumerate(self._queued(), start=1)}

    def queue_position(self, task_id: str) -> int | None:
        with self._lock:
            queued = self._queued()
            ids = [t.id for t in queued]
            return ids.index(task_id) + 1 if task_id in ids else None

    def _queued(self) -> list[DownloadTask]:
        return sorted((t for t in self._tasks.values() if t.status == DownloadStatus.QUEUED),
                      key=lambda t: t.position)

    def work_dir(self, task_id: str) -> Path:
        return Path(self._settings().temp_dir) / task_id

    # --------------------------------------------------------------- persistence
    def _persist(self, task: DownloadTask) -> None:
        try:
            self._repo.save(task)
        except Exception:
            log.exception("Could not persist task %s", task.id)

    def _persist_terminal(self, task: DownloadTask) -> None:
        settings = self._settings()
        if not settings.history_enabled:
            self._repo.delete(task.id)
            return
        self._persist(task)
        for removed in self._repo.prune(settings.history_limit):
            if self._thumbnails and removed.id not in self._tasks:
                self._thumbnails.delete(removed.thumbnail_path)

    def restore(self) -> list[DownloadTask]:
        """Rebuild the queue left by the previous session (normal exit, crash or power loss).

        Tasks that were running become INTERRUPTED; waiting ones stay QUEUED in their original order; paused ones
        stay PAUSED. The queue is held until the user decides (resume all / review / discard)."""
        restored: list[DownloadTask] = []
        with self._lock:
            for task in self._repo.unfinished():
                if task.status.is_active:
                    task.status = DownloadStatus.INTERRUPTED
                task.has_partial_data = fs.has_partial_files(self.work_dir(task.id))
                if not task.has_partial_data:
                    task.progress = DownloadProgress()
                self._tasks[task.id] = task
                self._persist(task)
                restored.append(_snapshot(task))
            self._held = bool(restored)
        if restored:
            log.info("Restored %d unfinished task(s): %s", len(restored),
                     ", ".join(f"{t.id[:8]}={t.status.value}" for t in restored))
        self._cleanup_orphan_temp_dirs()
        return restored

    @property
    def is_held(self) -> bool:
        return self._held

    def release_queue(self) -> None:
        """Let waiting tasks start again (after the recovery decision)."""
        with self._lock:
            self._held = False
            self._schedule()

    def recoverable(self) -> list[DownloadTask]:
        with self._lock:
            return [_snapshot(t) for t in sorted(self._tasks.values(), key=lambda t: t.position)
                    if t.status in _RECOVERABLE]

    def discard_recoverable(self) -> int:
        """Cancel every recovered task (removes their partial files) and release the queue."""
        with self._lock:
            tasks = [t for t in self._tasks.values() if t.status in _RECOVERABLE]
            for task in tasks:
                self._mark_cancelled(task)
            self._held = False
            return len(tasks)

    def _cleanup_orphan_temp_dirs(self) -> None:
        root = Path(self._settings().temp_dir)
        if not root.is_dir():
            return
        with self._lock:
            keep = set(self._tasks)
        for child in root.iterdir():
            if child.is_dir() and len(child.name) == 32 and child.name not in keep:
                fs.remove_tree(child)

    # ------------------------------------------------------------------ commands
    def add(self, request: DownloadRequest, thumbnail: bytes | None = None) -> DownloadTask:
        task = DownloadTask.from_request(request)
        if self._thumbnails:
            task.thumbnail_path = self._thumbnails.save(task.id, thumbnail)
        with self._lock:
            task.position = self._next_position()
            self._tasks[task.id] = task
            self._persist(task)
            log.info("Queued %s (%s, %s) -> %s", task.id, task.quality_label, task.container, task.output_dir)
            self._emit(DownloadEvent.ADDED, task, ev.DOWNLOAD_ADDED)
            self._schedule()
            return _snapshot(task)

    def _next_position(self) -> int:
        return max((t.position for t in self._tasks.values()), default=0) + 1

    def pause(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status == DownloadStatus.QUEUED:
                task.status = DownloadStatus.PAUSED
                task.has_partial_data = fs.has_partial_files(self.work_dir(task_id))
                self._persist(task)
                log.info("Paused %s (was waiting)", task.id)
                self._emit(DownloadEvent.UPDATED, task, ev.DOWNLOAD_PAUSED)
                return True
            token = self._tokens.get(task_id)
            if token and task.status == DownloadStatus.DOWNLOADING:
                token.request(StopReason.PAUSE)
                task.stopping = True
                self._emit(DownloadEvent.UPDATED, task)
                return True
            return False

    def resume(self, task_id: str) -> bool:
        """Re-queue a paused/interrupted/failed/cancelled task. Partial data is reused when present."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task_id in self._threads:
                return False
            if task.status not in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED,
                                   DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                return False
            if task.status.is_terminal:
                task.position = self._next_position()  # restarting a finished task goes to the end
            previous = task.status
            task.status = DownloadStatus.QUEUED  # paused/interrupted keep their place in the queue
            task.error_kind = task.error_message = task.finished_at = task.notice = None
            task.attempts = 0
            self._persist(task)
            log.info("Resumed %s (was %s)", task.id, previous.value)
            self._emit(DownloadEvent.UPDATED, task, ev.DOWNLOAD_RESUMED)
            self._held = False
            self._schedule()
            return True

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED):
                return False
            token = self._tokens.get(task_id)
            if token:
                token.request(StopReason.CANCEL)
                task.stopping = True
                self._emit(DownloadEvent.UPDATED, task)
                return True
            self._mark_cancelled(task)
            return True

    def _mark_cancelled(self, task: DownloadTask) -> None:
        self._release(task.id)
        fs.remove_tree(self.work_dir(task.id))
        task.status = DownloadStatus.CANCELLED
        task.finished_at = now_iso()
        task.progress = DownloadProgress()
        task.has_partial_data = task.stopping = False
        task.retry_in = None
        self._persist_terminal(task)
        log.info("Cancelled %s", task.id)
        self._emit(DownloadEvent.FINISHED, task, ev.DOWNLOAD_CANCELLED)

    def remove(self, task_id: str) -> bool:
        """Remove a task from the download list (history keeps finished records)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task_id in self._threads:
                return False
            if not task.status.is_terminal:
                self._mark_cancelled(task)
            del self._tasks[task_id]
            self._emit(DownloadEvent.REMOVED, task, ev.DOWNLOAD_REMOVED)
            return True

    def clear_finished(self) -> int:
        with self._lock:
            finished = [t.id for t in self._tasks.values() if t.status.is_terminal]
            for task_id in finished:
                self.remove(task_id)
            return len(finished)

    def move(self, task_id: str, offset: int) -> bool:
        """Move a waiting task up (negative) or down (positive) in the queue."""
        with self._lock:
            queued = self._queued()
            ids = [t.id for t in queued]
            if task_id not in ids:
                return False
            index = ids.index(task_id)
            target = max(0, min(len(queued) - 1, index + offset))
            if target == index:
                return False
            positions = [t.position for t in queued]
            item = queued.pop(index)
            queued.insert(target, item)
            for task, position in zip(queued, positions, strict=True):
                task.position = position
                self._persist(task)
            for task in queued:
                self._emit(DownloadEvent.UPDATED, task)
            return True

    def move_to_top(self, task_id: str) -> bool:
        return self.move(task_id, -len(self._tasks))

    def pause_all(self) -> None:
        with self._lock:
            for task_id in list(self._tasks):
                self.pause(task_id)

    def resume_all(self) -> None:
        with self._lock:
            for task in sorted(self._tasks.values(), key=lambda t: t.position):
                if task.status in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED):
                    self.resume(task.id)
            self._held = False
            self._schedule()

    def cancel_all(self) -> None:
        with self._lock:
            for task_id in list(self._tasks):
                task = self._tasks[task_id]
                if not task.status.is_terminal:
                    self.cancel(task_id)

    def reschedule(self) -> None:
        """Call after the concurrency limit changes."""
        with self._lock:
            self._schedule()

    def shutdown(self, cancel: bool = False, pause: bool = False, timeout: float = 8.0) -> None:
        """Stop workers and record the real state: cancelled, paused (partial data kept) or interrupted."""
        with self._lock:
            self._accepting = False
            reason = StopReason.CANCEL if cancel else StopReason.PAUSE if pause else StopReason.SHUTDOWN
            for token in self._tokens.values():
                token.request(reason)
            threads = list(self._threads.values())
            if cancel:
                for task in list(self._tasks.values()):
                    if task.status in _RECOVERABLE:
                        self._mark_cancelled(task)
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            for task in self._tasks.values():
                if task.status.is_active:  # worker did not stop in time: record honestly
                    task.status = DownloadStatus.INTERRUPTED
                    self._persist(task)

    # ---------------------------------------------------------------- scheduling
    def _schedule(self) -> None:
        if not self._accepting or self._held:
            return
        free_slots = self._settings().max_concurrent - len(self._threads)
        for task in self._queued()[:max(0, free_slots)]:
            token = StopToken()
            thread = threading.Thread(target=self._worker, args=(task.id, token),
                                      name=f"download-{task.id[:8]}", daemon=True)
            self._tokens[task.id] = token
            self._threads[task.id] = thread
            task.status = DownloadStatus.DOWNLOADING
            task.stopping = False
            thread.start()

    def _worker(self, task_id: str, token: StopToken) -> None:
        try:
            self._run(task_id, token)
        except Exception:
            log.exception("Worker crashed for %s", task_id)
        finally:
            with self._lock:
                self._release(task_id)
                self._schedule()

    def _release(self, task_id: str) -> None:
        """Free the worker slot. Called together with the final status change so the UI can
        resume a task immediately; only releases the slot owned by the calling thread."""
        if self._threads.get(task_id) is threading.current_thread():
            self._threads.pop(task_id, None)
            self._tokens.pop(task_id, None)

    def _run(self, task_id: str, token: StopToken) -> None:
        task = self._tasks[task_id]
        work_dir = self.work_dir(task_id)
        attempt = 0
        while True:
            attempt += 1
            self._begin_attempt(task, attempt)
            try:
                result = self._download_once(task, work_dir, token)
                self._complete(task, result, work_dir)
                return
            except DownloadStopped as stop:
                self._stopped(task, stop.reason, work_dir)
                return
            except Exception as exc:  # noqa: BLE001 - every failure is converted to a friendly AppError
                error = to_app_error(exc)
                if token.is_set():
                    self._stopped(task, token.reason or StopReason.CANCEL, work_dir)
                    return
                log.warning("Attempt %d failed for %s: [%s] %s", attempt, task_id, error.kind.value,
                            mask_urls(error.detail))
                if error.is_transient and attempt < MAX_ATTEMPTS:
                    if self._wait_retry(task, error, token, attempt):
                        continue
                    self._stopped(task, token.reason or StopReason.CANCEL, work_dir)
                    return
                self._fail(task, error, work_dir)
                return

    def _begin_attempt(self, task: DownloadTask, attempt: int) -> None:
        with self._lock:
            task.attempts = attempt
            task.status = DownloadStatus.DOWNLOADING
            task.started_at = task.started_at or now_iso()
            task.retry_in = None
            task.error_kind = task.error_message = None
            self._persist(task)
            log.info("Starting %s (attempt %d): %s", task.id, attempt, task.log_title)
            self._emit(DownloadEvent.UPDATED, task, ev.DOWNLOAD_STARTED if attempt == 1 else None)

    def _download_once(self, task: DownloadTask, work_dir: Path, token: StopToken) -> Path:
        destination = Path(task.output_dir)
        fs.ensure_writable_dir(destination)
        remaining = None
        if task.estimated_size:
            partial = sum(p.stat().st_size for p in work_dir.iterdir()) if work_dir.is_dir() else 0
            remaining = max(0, task.estimated_size - partial)
        fs.ensure_free_space(destination, remaining)
        fs.ensure_free_space(work_dir, remaining)
        partial = sum(p.stat().st_size for p in work_dir.iterdir() if p.is_file()) if work_dir.is_dir() else 0
        self._resume_from[task.id] = partial
        job = DownloadJob(task.url, task.quality_height, task.container)
        return self._provider.download(job, work_dir, lambda p: self._on_progress(task, p),
                                       lambda s: self._on_stage(task, s), token)

    def _on_progress(self, task: DownloadTask, progress: DownloadProgress) -> None:
        with self._lock:
            resumed = self._resume_from.pop(task.id, 0)
            if resumed > 1024 * 1024 and progress.downloaded_bytes < resumed * 0.5:
                task.notice = RESTART_NOTICE
                log.info("Resume not honoured for %s (had %d bytes)", task.id, resumed)
                self._emit(DownloadEvent.UPDATED, task)
            task.progress = progress
            now = time.monotonic()
            if now - self._last_checkpoint.get(task.id, 0.0) >= self._checkpoint_interval:
                self._last_checkpoint[task.id] = now
                try:
                    self._repo.save_progress(task)
                except Exception:
                    log.exception("Checkpoint failed for %s", task.id)
            self._emit(DownloadEvent.PROGRESS, task)

    def _on_stage(self, task: DownloadTask, stage: Stage) -> None:
        status = DownloadStatus.PROCESSING if stage == Stage.PROCESSING else DownloadStatus.DOWNLOADING
        with self._lock:
            if task.status != status:
                task.status = status
                self._emit(DownloadEvent.UPDATED, task)

    def _wait_retry(self, task: DownloadTask, error: AppError, token: StopToken, attempt: int) -> bool:
        delay = self._retry_delays[min(attempt - 1, len(self._retry_delays) - 1)]
        with self._lock:
            task.retry_in = int(delay)
            task.error_message = error.user_message
            self._emit(DownloadEvent.UPDATED, task)
        return not token.wait(delay)

    def _complete(self, task: DownloadTask, source: Path, work_dir: Path) -> None:
        with self._lock:
            task.status = DownloadStatus.PROCESSING
            self._emit(DownloadEvent.UPDATED, task)
        settings = self._settings()
        directory = Path(task.output_dir)
        if settings.organize_enabled:
            directory = fs.organized_subdir(directory, settings.organize_pattern, datetime.now(),
                                            task.uploader, task.extractor)
        if not source.is_file() or source.stat().st_size == 0 or source.suffix in fs.PARTIAL_SUFFIXES:
            raise AppError(ErrorKind.UNEXPECTED, f"Engine output is not a complete file: {source}")
        with self._finalize_lock:
            target = fs.move_to_destination(source, directory, task.title,
                                            overwrite=settings.existing_file_policy == EXISTING_OVERWRITE)
        if not target.is_file() or target.stat().st_size == 0:
            raise AppError(ErrorKind.UNEXPECTED, f"Final file missing after move: {target}")
        fs.remove_tree(work_dir)
        with self._lock:
            self._release(task.id)
            task.status = DownloadStatus.COMPLETED
            task.file_path = str(target)
            task.file_size = target.stat().st_size
            task.finished_at = now_iso()
            task.has_partial_data = task.stopping = False
            if task.progress.total_bytes is None:
                task.progress = DownloadProgress(task.file_size, task.file_size)
            self._persist_terminal(task)
            log.info("Completed %s -> %s", task.id, target)
            self._emit(DownloadEvent.FINISHED, task, ev.DOWNLOAD_COMPLETED)

    def _stopped(self, task: DownloadTask, reason: StopReason, work_dir: Path) -> None:
        with self._lock:
            if reason == StopReason.CANCEL:
                self._mark_cancelled(task)
                return
            self._release(task.id)
            task.status = DownloadStatus.PAUSED if reason == StopReason.PAUSE else DownloadStatus.INTERRUPTED
            task.has_partial_data = fs.has_partial_files(work_dir)
            task.stopping = False
            task.retry_in = None
            task.progress.speed = task.progress.eta = None
            self._persist(task)
            log.info("%s %s (partial data kept: %s)", task.status.label, task.id, task.has_partial_data)
            self._emit(DownloadEvent.UPDATED, task,
                       ev.DOWNLOAD_PAUSED if task.status == DownloadStatus.PAUSED else None)

    def _fail(self, task: DownloadTask, error: AppError, work_dir: Path) -> None:
        with self._lock:
            self._release(task.id)
            task.status = DownloadStatus.FAILED
            task.error_kind = error.kind.value
            task.error_message = error.user_message
            task.finished_at = now_iso()
            task.has_partial_data = fs.has_partial_files(work_dir)
            task.retry_in = None
            task.stopping = False
            task.progress.speed = task.progress.eta = None
            self._persist_terminal(task)
            log.error("Failed %s: [%s] %s", task.id, error.kind.value, mask_urls(error.detail))
            self._emit(DownloadEvent.FINISHED, task, ev.DOWNLOAD_FAILED)
