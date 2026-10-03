"""Download task model and states."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from app.core.models.video import (
    AUDIO_ONLY_LABEL,
    CONTAINER_LABELS,
    KIND_AUDIO,
    VideoInfo,
    container_kind,
    quality_label,
)
from app.utils.formatters import now_iso


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]

    @property
    def is_active(self) -> bool:
        return self in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING)

    @property
    def is_terminal(self) -> bool:
        return self in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED)

    @property
    def is_resumable_state(self) -> bool:
        return self in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED, DownloadStatus.FAILED)


_STATUS_LABELS = {
    DownloadStatus.QUEUED: "Aguardando",
    DownloadStatus.DOWNLOADING: "Baixando",
    DownloadStatus.PROCESSING: "Finalizando",
    DownloadStatus.PAUSED: "Pausado",
    DownloadStatus.COMPLETED: "Concluído",
    DownloadStatus.CANCELLED: "Cancelado",
    DownloadStatus.FAILED: "Falhou",
    DownloadStatus.INTERRUPTED: "Download interrompido",
}


@dataclass
class DownloadProgress:
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None

    @property
    def fraction(self) -> float | None:
        if not self.total_bytes:
            return None
        return max(0.0, min(1.0, self.downloaded_bytes / self.total_bytes))


@dataclass
class DownloadRequest:
    """Everything needed to enqueue a download."""

    url: str
    output_dir: str
    title: str | None = None
    quality_height: int = 0
    container: str = "mp4"
    thumbnail_url: str | None = None
    uploader: str | None = None
    duration: float | None = None
    extractor: str | None = None
    estimated_size: int | None = None


@dataclass
class DownloadTask:
    url: str
    output_dir: str
    title: str | None = None
    quality_height: int = 0
    container: str = "mp4"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: DownloadStatus = DownloadStatus.QUEUED
    position: int = 0
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    thumbnail_url: str | None = None
    thumbnail_path: str | None = None
    uploader: str | None = None
    duration: float | None = None
    extractor: str | None = None
    estimated_size: int | None = None
    file_path: str | None = None
    file_size: int | None = None
    error_kind: str | None = None
    error_message: str | None = None
    attempts: int = 0
    updated_at: str | None = None
    # Runtime-only state (not persisted).
    progress: DownloadProgress = field(default_factory=DownloadProgress)
    has_partial_data: bool = False
    stopping: bool = False
    retry_in: int | None = None
    notice: str | None = None  # informational message, e.g. "the source did not allow resuming"

    @classmethod
    def from_request(cls, request: DownloadRequest) -> DownloadTask:
        return cls(url=request.url, output_dir=request.output_dir, title=request.title,
                   quality_height=request.quality_height, container=request.container,
                   thumbnail_url=request.thumbnail_url, uploader=request.uploader,
                   duration=request.duration, extractor=request.extractor,
                   estimated_size=request.estimated_size)

    def to_request(self) -> DownloadRequest:
        return DownloadRequest(url=self.url, output_dir=self.output_dir, title=self.title,
                               quality_height=self.quality_height, container=self.container,
                               thumbnail_url=self.thumbnail_url, uploader=self.uploader,
                               duration=self.duration, extractor=self.extractor,
                               estimated_size=self.estimated_size)

    @property
    def display_title(self) -> str:
        return self.title or self.url

    @property
    def log_title(self) -> str:
        """Title for log lines: never the URL (it may have come from the clipboard)."""
        return self.title or "(sem título)"

    @property
    def is_pending(self) -> bool:
        """Still has work to do: downloading, waiting, paused or interrupted."""
        return not self.status.is_terminal

    @property
    def quality_label(self) -> str:
        if container_kind(self.container) == KIND_AUDIO:
            return AUDIO_ONLY_LABEL
        return quality_label(self.quality_height)

    @property
    def container_label(self) -> str:
        if self.file_path:
            return self.file_path.rsplit(".", 1)[-1].upper()
        return CONTAINER_LABELS.get(self.container, self.container.upper())


def request_from_info(info: VideoInfo, output_dir: str, container: str, quality_height: int) -> DownloadRequest:
    """The single place where an analysed URL + the user's choices become a DownloadRequest (desktop and widget)."""
    height = 0 if container_kind(container) == KIND_AUDIO else quality_height
    return DownloadRequest(
        url=info.url, output_dir=output_dir, title=info.title, quality_height=height, container=container,
        thumbnail_url=info.thumbnail_url, uploader=info.uploader, duration=info.duration, extractor=info.extractor,
        estimated_size=info.estimated_size_for(height, container),
    )
