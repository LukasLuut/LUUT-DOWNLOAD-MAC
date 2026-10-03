"""Download engine abstraction. The UI never talks to an engine directly."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.models.download import DownloadProgress
from app.core.models.video import VideoInfo


class StopReason(str, Enum):
    CANCEL = "cancel"
    PAUSE = "pause"
    SHUTDOWN = "shutdown"


class StopToken:
    """Cooperative stop signal shared between the manager and a running engine job."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason: StopReason | None = None

    def request(self, reason: StopReason) -> None:
        if self.reason is None:
            self.reason = reason
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class DownloadStopped(Exception):
    """Raised by a provider when a job stopped because the StopToken was triggered."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class Stage(str, Enum):
    DOWNLOADING = "downloading"
    PROCESSING = "processing"


@dataclass(frozen=True)
class DownloadJob:
    url: str
    quality_height: int
    container: str


@dataclass(frozen=True)
class EngineInfo:
    name: str
    version: str


ProgressCallback = Callable[[DownloadProgress], None]
StageCallback = Callable[[Stage], None]


class DownloadProvider(ABC):
    """Contract for download engines.

    `download()` must honour the StopToken: pause/cancel/shutdown are requested through it and
    the provider raises DownloadStopped. Pausing keeps partial data in `work_dir`, so resuming is a
    new `download()` call over the same directory.
    """

    @property
    @abstractmethod
    def engine(self) -> EngineInfo: ...

    @property
    def supports_resume(self) -> bool:
        return False

    @abstractmethod
    def analyze(self, url: str) -> VideoInfo:
        """Return metadata and the quality/format options really available. Raises AppError."""

    @abstractmethod
    def download(self, job: DownloadJob, work_dir: Path, on_progress: ProgressCallback,
                 on_stage: StageCallback, token: StopToken) -> Path:
        """Download into `work_dir` and return the finished file. Raises AppError/DownloadStopped."""
